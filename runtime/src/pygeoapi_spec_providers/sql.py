"""``sql-features``: a read-only SQL query as an OGC API - Features collection.

Config::

    providers:
      - type: feature
        name: pygeoapi_spec_providers.sql.SQLFeatureProvider
        data: /specs/waterways/1.0.0/spec.yaml    # or pkg:<module>/<resource> (ADR-0005)

The steward's query runs as a subquery (PLAN.md §7.2)::

    SELECT <columns> FROM (<query>) AS src WHERE <bound filters>
    ORDER BY <columns>, <item id> LIMIT … OFFSET …

The query is the one ``sqlguard`` checked and regenerated (ADR-0007), and every
filter value is a bound parameter. Filters the database can't apply (a bbox on
WKT or GeoJSON text, datetimes stored as text) are applied to the mapped
features, scanning at most ``limits.max_rows`` rows.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pygeoapi.crs import crs_transform, get_crs
from pygeoapi.provider.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderTypeError,
)

from . import sqlguard
from .db import (
    Database,
    DatabaseError,
    Dialect,
    bounds,
    database_for,
    decode_geometry,
    provider_error,
    rows,
    srid_of,
    target_for,
)
from .local import LocalFilters, parse_interval, shape_output, sort_features, split_terms
from .mapping import coerce
from .spec import Spec, SpecError, load_spec

GEOMETRY_COLUMNS = ('native', 'wkb', 'wkt', 'geojson', 'gpkg')


@contextmanager
def sql_errors() -> Iterator[None]:
    """Turn database failures into pygeoapi errors with a ``user_msg`` (notes §4)."""
    try:
        yield
    except DatabaseError as err:
        raise provider_error(err) from err


def json_id(value: Any) -> Any:
    """An item id as JSON can carry it: ints stay ints, everything else becomes text."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return str(value)


def escape_like(term: str) -> str:
    return term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


@dataclass
class Plan:
    where: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    local: LocalFilters = field(default_factory=LocalFilters)

    def bind(self, value: Any) -> str:
        name = f'p{len(self.params)}'
        self.params[name] = value
        return name


class SQLEngine:
    """Query building and mapping for one spec; one per process, with one pooled engine."""

    def __init__(self, spec: Spec):
        self.spec = spec
        data = spec.data
        source = data['source']
        self.dialect = Dialect(source['dialect'])
        self.checked = sqlguard.check(data['query'], source['dialect'])
        self.db: Database = database_for(target_for(source))
        self.geometry: dict[str, Any] = dict(data['geometry'])
        self.encoding: str = self.geometry['encoding']
        self.props: list[dict[str, Any]] = spec.properties
        self.by_name = {p['name']: p for p in self.props}
        self.id_column: str = data['item_id']['from']
        self.id_type: str | None = data['item_id'].get('type') or self._id_property_type()
        self.max_rows = int(data['limits']['max_rows'])
        self.time_field: str | None = data.get('time_field')
        geometry_columns = []
        if self.encoding in GEOMETRY_COLUMNS:
            geometry_columns = [self.geometry['column']]
        elif self.encoding == 'xy':
            geometry_columns = [self.geometry['x'], self.geometry['y']]
        self.columns = list(
            dict.fromkeys(
                [self.id_column, *geometry_columns, *(self.column(p) for p in self.props)]
            )
        )
        q = self.dialect.quote
        self.select_list = ', '.join(f'src.{q(c)}' for c in self.columns)
        self.from_clause = f'FROM ({self.checked.sql}) AS src'

    def _id_property_type(self) -> str | None:
        for p in self.props:
            if self.column(p) == self.spec.data['item_id']['from']:
                return str(p['type'])
        return None

    @staticmethod
    def column(prop: Mapping[str, Any]) -> str:
        return str(prop.get('from', prop['name']))

    def ref(self, column: str) -> str:
        return f'src.{self.dialect.quote(column)}'

    def param(self, name: str) -> str:
        return self.dialect.param(name)

    # --- planning

    def plan(
        self,
        bbox: Sequence[float] | None,
        datetime_: str | None,
        properties: Sequence[tuple[str, str]],
        sortby: Sequence[dict[str, str]],
        q: str | None,
    ) -> Plan:
        plan = Plan(local=LocalFilters(time_field=self.time_field))
        if bbox:
            self._plan_bbox(plan, bbox)
        if datetime_ and self.time_field:
            self._plan_datetime(plan, datetime_)
        for name, value in properties or []:
            self._plan_property(plan, name, value)
        if q:
            self._plan_q(plan, q)
        self._plan_sort(plan, sortby or [])
        return plan

    def _plan_bbox(self, plan: Plan, bbox: Sequence[float]) -> None:
        minx, miny, maxx, maxy = bounds(bbox)
        if self.encoding == 'xy':
            cast = 'DOUBLE PRECISION' if self.dialect.name == 'postgresql' else 'REAL'
            for column, low, high in (
                (self.geometry['x'], minx, maxx),
                (self.geometry['y'], miny, maxy),
            ):
                expr = f'CAST({self.ref(column)} AS {cast})'
                lo, hi = plan.bind(float(low)), plan.bind(float(high))
                plan.where.append(f'{expr} BETWEEN {self.param(lo)} AND {self.param(hi)}')
        elif self.encoding == 'native' and self.dialect.name == 'postgresql':
            names = [plan.bind(float(v)) for v in (minx, miny, maxx, maxy)]
            srid = plan.bind(srid_of(self.geometry.get('crs')))
            envelope = ', '.join(self.param(n) for n in names)
            plan.where.append(
                f'ST_Intersects(CAST({self.ref(self.geometry["column"])} AS geometry), '
                f'ST_MakeEnvelope({envelope}, {self.param(srid)}))'
            )
        else:
            plan.local.bbox = bbox

    def _plan_datetime(self, plan: Plan, datetime_: str) -> None:
        start, end = parse_interval(datetime_)
        prop = self.by_name.get(self.time_field or '')
        if prop is None or prop.get('format') or self.dialect.name != 'postgresql':
            plan.local.interval = (start, end)  # text or epoch values: compare after mapping
            return
        column = self.ref(self.column(prop))
        if start is not None:
            plan.where.append(f'{column} >= {self.param(plan.bind(start))}')
        if end is not None:
            plan.where.append(f'{column} <= {self.param(plan.bind(end))}')
        if start is None and end is None:
            plan.where.append(f'{column} IS NOT NULL')

    def _plan_property(self, plan: Plan, name: str, value: str) -> None:
        prop = self.by_name.get(name)
        if prop is None or prop['type'] == 'datetime':
            plan.local.properties.append((name, str(value)))
            return
        typed = coerce(value, prop)
        if typed is None:
            plan.where.append('1 = 0')  # e.g. depth=abc: nothing can match
            return
        if prop['type'] == 'boolean' and self.dialect.name == 'sqlite':
            typed = int(typed)
        plan.where.append(f'{self.ref(self.column(prop))} = {self.param(plan.bind(typed))}')

    def _plan_q(self, plan: Plan, q: str) -> None:
        terms = split_terms(q)
        texts = [self.column(p) for p in self.props if p['type'] == 'string']
        if not terms:
            return
        if not texts:
            plan.local.terms = terms
            return
        ors = []
        for term in terms:
            name = plan.bind(f'%{escape_like(term)}%')
            for column in texts:
                expr = self.dialect.text(self.ref(column))
                ors.append(f"{expr} {self.dialect.like} {self.param(name)} ESCAPE '\\'")
        plan.where.append('(' + ' OR '.join(ors) + ')')

    def _plan_sort(self, plan: Plan, sortby: Sequence[dict[str, str]]) -> None:
        local = plan.local.filters() or any(
            self.by_name.get(k['property'], {}).get('format') for k in sortby
        )
        if sortby and local:
            plan.local.sortby = list(sortby)
        else:
            for key in sortby:
                prop = self.by_name.get(key['property'])
                if prop is None:
                    continue
                direction = 'DESC' if key.get('order') == '-' else 'ASC'
                plan.order.append(f'{self.ref(self.column(prop))} {direction} NULLS LAST')
        plan.order.append(f'{self.ref(self.id_column)} ASC')  # stable pages (harness L1.offset)

    # --- running

    def _where(self, plan: Plan) -> str:
        return (' WHERE ' + ' AND '.join(plan.where)) if plan.where else ''

    def query(
        self, *, offset: int, limit: int, hits: bool, count: bool, plan: Plan
    ) -> tuple[list[dict[str, Any]], int | None]:
        limit = min(limit, self.max_rows)
        where = self._where(plan)
        order = ' ORDER BY ' + ', '.join(plan.order)
        with self.db.connect() as conn:
            if not plan.local.any():
                matched = None
                if count or hits:
                    sql = f'SELECT count(*) {self.from_clause}{where}'
                    matched = int(conn.exec_driver_sql(sql, dict(plan.params)).scalar() or 0)
                if hits:
                    return [], matched
                params = {**plan.params, 'pb_limit': limit, 'pb_offset': offset}
                sql = (
                    f'SELECT {self.select_list} {self.from_clause}{where}{order} '
                    f'LIMIT {self.param("pb_limit")} OFFSET {self.param("pb_offset")}'
                )
                _cols, found = rows(conn, sql, params)
                return [f for f in map(self.feature, found) if f is not None], matched
            # Scan within the limit, apply what the database couldn't, then slice.
            params = {**plan.params, 'pb_limit': self.max_rows + 1}
            sql = (
                f'SELECT {self.select_list} {self.from_clause}{where}{order} '
                f'LIMIT {self.param("pb_limit")}'
            )
            _cols, found = rows(conn, sql, params)
        complete = len(found) <= self.max_rows
        matching = [
            f
            for f in map(self.feature, found[: self.max_rows])
            if f is not None and plan.local.matches(f)
        ]
        if plan.local.sortby:
            sort_features(matching, plan.local.sortby)
        matched = len(matching) if complete else None
        return ([] if hits else matching[offset : offset + limit]), matched

    def id_condition(self, plan: Plan, identifier: Any) -> str | None:
        """``src.id = :id`` with the id in the column's type, or None if it can't match."""
        column = self.ref(self.id_column)
        kind = self.id_type
        if kind in ('integer', 'number'):
            typed = coerce(identifier, {'type': kind})
            if typed is None:
                return None
            return f'{column} = {self.param(plan.bind(typed))}'
        if kind == 'string':
            return f'{column} = {self.param(plan.bind(str(identifier)))}'
        # Unknown type: compare as text, which works for any column (without an index).
        return f'{self.dialect.text(column)} = {self.param(plan.bind(str(identifier)))}'

    def get(self, identifier: Any) -> dict[str, Any]:
        not_found = ProviderItemNotFoundError(user_msg='item not found')
        plan = Plan()
        condition = self.id_condition(plan, identifier)
        if condition is None:
            raise not_found
        sql = f'SELECT {self.select_list} {self.from_clause} WHERE {condition} LIMIT 2'
        with self.db.connect() as conn:
            _cols, found = rows(conn, sql, plan.params)
        features = [f for f in map(self.feature, found) if f is not None]
        if not features:
            raise not_found
        return features[0]

    def sample(self, limit: int) -> tuple[list[str], list[tuple[Any, ...]]]:
        """The query's own columns and first rows, before any mapping (the J4 sample)."""
        sql = f'SELECT * {self.from_clause} LIMIT {self.param("pb_limit")}'
        with self.db.connect() as conn:
            return rows(conn, sql, {'pb_limit': limit})

    # --- mapping

    def feature(self, row: Sequence[Any]) -> dict[str, Any] | None:
        record = dict(zip(self.columns, row, strict=True))
        ident = record.get(self.id_column)
        if ident is None or ident == '':
            return None
        return {
            'type': 'Feature',
            'id': json_id(ident),
            'geometry': self.geometry_of(record),
            'properties': {p['name']: coerce(record.get(self.column(p)), p) for p in self.props},
        }

    def geometry_of(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        if self.encoding in GEOMETRY_COLUMNS:
            return decode_geometry(record.get(self.geometry['column']), self.encoding)
        if self.encoding == 'xy':
            x = coerce(record.get(self.geometry['x']), {'type': 'number'})
            y = coerce(record.get(self.geometry['y']), {'type': 'number'})
            if x is None or y is None:
                return None
            return {'type': 'Point', 'coordinates': [x, y]}
        return None


@functools.lru_cache(maxsize=32)
def sql_engine_for(spec: Spec) -> SQLEngine:
    return SQLEngine(spec)


def load_sql_spec(data: Any) -> Spec:
    try:
        spec = load_spec(data)
    except SpecError as err:
        raise ProviderConnectionError(str(err), user_msg='The provider spec is invalid.') from err
    if spec.data['template'] != 'sql-features':
        raise ProviderTypeError(
            user_msg=f'{spec.data["template"]} specs need another provider class'
        )
    return spec


class SQLFeatureProvider(BaseProvider):  # type: ignore[misc]  # pygeoapi is untyped
    """A feature provider driven by a ``sql-features`` spec (``data`` points at it)."""

    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self.spec = load_sql_spec(provider_def['data'])
        try:
            self.engine = sql_engine_for(self.spec)
        except (sqlguard.SQLGuardError, DatabaseError) as err:
            raise ProviderConnectionError(
                str(err), user_msg='The provider spec is invalid.'
            ) from err
        self._fields = self.spec.fields()  # pygeoapi reads p.fields (notes §3.2)
        self.id_field = provider_def.get('id_field') or self.spec.id_property
        self.title_field = provider_def.get('title_field') or self.spec.data.get('title_field')
        self.time_field = provider_def.get('time_field') or self.spec.data.get('time_field')
        crs = self.spec.data['geometry'].get('crs')
        if crs and 'storage_crs' not in provider_def:
            self.storage_crs = get_crs(crs)

    def get_fields(self) -> dict[str, dict[str, str]]:
        return self._fields

    @crs_transform  # type: ignore[untyped-decorator]
    def query(
        self,
        offset: int = 0,
        limit: int = 10,
        resulttype: str = 'results',
        bbox: Sequence[float] | None = None,
        datetime_: str | None = None,
        properties: Sequence[tuple[str, str]] | None = None,
        sortby: Sequence[dict[str, str]] | None = None,
        select_properties: Sequence[str] | None = None,
        skip_geometry: bool = False,
        q: str | None = None,
        filterq: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if filterq is not None:
            raise ProviderInvalidQueryError(
                user_msg='This collection does not support CQL filters.'
            )
        with sql_errors():
            plan = self.engine.plan(bbox, datetime_, properties or [], sortby or [], q)
            features, matched = self.engine.query(
                offset=max(0, int(offset)),
                limit=max(0, int(limit)),
                hits=resulttype == 'hits',
                count=self.count,
                plan=plan,
            )
        features = [shape_output(f, select_properties or [], skip_geometry) for f in features]
        result: dict[str, Any] = {
            'type': 'FeatureCollection',
            'features': features,
            'numberReturned': len(features),
        }
        if matched is not None and (self.count or resulttype == 'hits'):
            result['numberMatched'] = matched
        return result

    @crs_transform  # type: ignore[untyped-decorator]
    def get(self, identifier: Any, **kwargs: Any) -> dict[str, Any]:
        with sql_errors():
            return self.engine.get(identifier)

    def __repr__(self) -> str:
        spec = getattr(self, 'spec', None)
        return f'<SQLFeatureProvider> {spec.id if spec else "(no spec)"}'


def sample_rows(spec_source: Mapping[str, Any], query: str, limit: int) -> dict[str, Any]:
    """Run a query that isn't a spec yet (J4's Sample step): columns, types and rows."""
    checked = sqlguard.check(query, spec_source['dialect'])
    database = database_for(target_for(spec_source))
    dialect = Dialect(spec_source['dialect'])
    # The inner query passed sqlguard and was regenerated by it; the limit is bound.
    sql = f'SELECT * FROM ({checked.sql}) AS src LIMIT {dialect.param("pb_limit")}'  # noqa: S608
    with sql_errors(), database.connect() as conn:
        columns, found = rows(conn, sql, {'pb_limit': limit})
        types: dict[str, str] = {}
        if spec_source['dialect'] == 'postgresql':
            types = postgres_types(conn, sql, limit)
    return {'columns': columns, 'rows': found, 'types': types, 'query': checked.display}


def postgres_types(conn: Any, sql: str, limit: int) -> dict[str, str]:
    """Column type names from the cursor's type OIDs (to spot geometry columns)."""
    cursor = conn.connection.dbapi_connection.cursor()
    try:
        cursor.execute(sql, {'pb_limit': 0})
        oids = {d.name: d.type_code for d in cursor.description or []}
        cursor.execute(
            'SELECT oid, typname FROM pg_type WHERE oid = ANY(%(oids)s)',
            {'oids': list(set(oids.values()))},
        )
        names = {oid: name for oid, name in cursor.fetchall()}
    finally:
        cursor.close()
    return {column: str(names.get(oid, '')) for column, oid in oids.items()}


def to_jsonable(value: Any) -> Any:
    """A sample cell for the browser: geometries as hex, datetimes as ISO text."""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes | bytearray):
        return {'bytes': len(value), 'hex': bytes(value[:4096]).hex()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    if isinstance(value, dict | list):
        return value
    return str(value)
