"""``timeseries-edr``: monitoring-location time series as an OGC API - EDR collection.

Config::

    providers:
      - type: edr
        name: pygeoapi_spec_providers.timeseries.TimeSeriesEDRProvider
        data: /specs/coops-water-levels/1.0.0/spec.yaml

Three sources (PLAN.md §7.2): *locations* come from an HTTP API or a SQL query,
read by the ``http-features`` or ``sql-features`` engine; *parameters* are
declared in the spec, with their units; *observations* come from an HTTP URL
template or a SQL query with ``:location_id``, ``:parameter``, ``:start`` and
``:end`` parameters.

Responses follow pygeoapi's own SensorThings EDR provider (notes §5.2):
``locations`` without an id is a GeoJSON FeatureCollection; everything else is
a CoverageJSON ``CoverageCollection`` of ``PointSeries``, one coverage per
location and parameter, with collection-level ``parameters``. A query with no
observations at all raises ``ProviderNoDataError`` (HTTP 204), since a
CoverageJSON array can't be empty.

The class defines its query methods in its own body, as pygeoapi 0.24 requires,
and narrows ``query_types`` per spec on the instance (ADR-0009).
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from pygeoapi.provider.base import (
    ProviderConnectionError,
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderNoDataError,
    ProviderQueryError,
    ProviderTypeError,
)
from pygeoapi.provider.base_edr import BaseEDRProvider
from shapely.geometry import Point, box, shape

from . import formatter, http, sql, sqlguard
from .client import SourceClient, SourceError, status_error
from .db import DatabaseError, database_for, provider_error, rows, target_for
from .jsonpaths import PathError, findall, first, value_of
from .local import parse_interval
from .mapping import coerce, parse_datetime, to_iso
from .spec import Spec, SpecError, load_spec, normalize_source, parse

QUERY_TYPES = ('locations', 'position', 'area', 'cube')
URL_TOKENS = ('location_id', 'parameter')
TOKENS = ('location_id', 'parameter', 'start', 'end')
DATETIME_TOKENS = ('start', 'end')
SQL_PARAMETERS = frozenset(TOKENS)
DURATION = re.compile(
    r'^P(?:(?P<weeks>\d+)W|(?:(?P<days>\d+)D)?'
    r'(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?)$'
)
EARTH_KM = 6371.0088

GEOGRAPHIC_CRS = {
    'coordinates': ['x', 'y'],
    'system': {'type': 'GeographicCRS', 'id': 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'},
}
TEMPORAL_RS = {'coordinates': ['t'], 'system': {'type': 'TemporalRS', 'calendar': 'Gregorian'}}


def parse_duration(text: str) -> timedelta:
    """An ISO 8601 duration in weeks, days, hours, minutes and seconds (no months or years)."""
    m = DURATION.match(text)
    if not m or text in ('P', 'PT') or text.endswith('T'):
        raise ValueError(f'{text!r} is not a duration such as P7D or PT6H')
    parts = {k: float(v) for k, v in m.groupdict().items() if v}
    return timedelta(**parts)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def point_of(feature: Mapping[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get('geometry')
    if not geometry:
        return None
    try:
        geom = shape(geometry)
    except (ValueError, TypeError, AttributeError):
        return None
    if geom.is_empty:
        return None
    point = geom if geom.geom_type == 'Point' else geom.representative_point()
    return float(point.x), float(point.y)


@contextmanager
def ts_errors() -> Iterator[None]:
    """Engine failures as pygeoapi errors with a ``user_msg`` (notes §4)."""
    try:
        yield
    except SourceError as err:
        cls = ProviderQueryError
        if err.kind in ('egress', 'connect', 'timeout') or err.status in (401, 403):
            cls = ProviderConnectionError
        raise cls(str(err), user_msg=http.user_message(err)) from err
    except DatabaseError as err:
        raise provider_error(err) from err
    except (PathError, formatter.FormatError) as err:
        raise ProviderQueryError(str(err), user_msg='The provider spec has an error.') from err


# --- locations -----------------------------------------------------------------------------


def sub_spec(parent: Spec, template: str, body: Mapping[str, Any]) -> Spec:
    """A features spec built from one block of a time-series spec (its locations)."""
    doc = {
        'spec_version': 1,
        'template': template,
        'id': f'{parent.id[:50]}-locations',
        'title': f'{parent.title} (locations)',
        **body,
    }
    return parse(doc, f'{parent.origin}#locations')


class Locations:
    """Monitoring locations, read with the http-features or sql-features engine."""

    def __init__(self, spec: Spec):
        block = spec.data['locations']
        kind = block['from']
        self.spec = sub_spec(spec, f'{kind}-features', block[kind])
        self.engine: http.Engine | sql.SQLEngine = (
            http.engine_for(self.spec) if kind == 'http' else sql.sql_engine_for(self.spec)
        )
        self.title_field: str | None = self.spec.data.get('title_field')

    def find(self, bbox: Sequence[float] | None, limit: int) -> list[dict[str, Any]]:
        plan = self.engine.plan(bbox or None, None, [], [], None)
        features, _ = self.engine.query(offset=0, limit=limit, hits=False, count=False, plan=plan)  # type: ignore[arg-type]
        return features

    def get(self, location_id: str) -> dict[str, Any]:
        return self.engine.get(location_id)


# --- observations --------------------------------------------------------------------------


Series = list[tuple[datetime, Any]]


class HTTPObservations:
    def __init__(self, cfg: Mapping[str, Any]):
        self.cfg = cfg
        self.url = formatter.parse(cfg['url'], URL_TOKENS)
        self.query = {
            str(k): formatter.parse(formatter.to_text(v), TOKENS, DATETIME_TOKENS)
            for k, v in (cfg.get('query') or {}).items()
        }
        base = {k: v for k, v in cfg.items() if k in SOURCE_KEYS}
        base['url'] = formatter.render(self.url, {'location_id': 'x', 'parameter': 'x'})
        base['query'] = {}
        self.source = normalize_source(base)
        host = self.source['url'].split('://', 1)[1].split('/', 1)[0].split(':', 1)[0]
        self.client = SourceClient(self.source, [host.lower()])
        self.records: str = cfg['records']
        self.time: dict[str, Any] = dict(cfg['time'])
        self.value: dict[str, Any] = dict(cfg['value'])
        self.parameter: dict[str, Any] | None = cfg.get('parameter')
        self.error_path: str | None = cfg.get('error_path')

    def fetch(self, location_id: Any, upstream: Any, start: datetime, end: datetime) -> Series:
        values = {'location_id': location_id, 'parameter': upstream, 'start': start, 'end': end}
        url = formatter.render_url(self.url, values)
        params = {k: formatter.render(t, values) for k, t in self.query.items()}
        response = self.client.get(url, params, static=False)
        if response.status in (404, 204):
            return []
        if not 200 <= response.status < 300:
            raise status_error(response)
        doc = response.json()
        if self.error_path:
            problem = first(self.error_path, doc)
            if problem not in (None, '', [], {}, False):
                return []  # "No data was found" (CO-OPS answers HTTP 200 with an error body)
        matches = findall(self.records, doc)
        if len(matches) == 1 and isinstance(matches[0], list):
            matches = matches[0]
        out: Series = []
        for record in matches:
            if not isinstance(record, Mapping):
                continue
            if self.parameter is not None:
                got = value_of(record, self.parameter['from'])
                if str(got) != str(upstream):
                    continue
            moment = parse_datetime(
                value_of(record, self.time['from']), self.time.get('format', 'iso8601')
            )
            if moment is None:
                continue
            out.append((moment, coerce(value_of(record, self.value['from']), {'type': 'number'})))
        return out


SOURCE_KEYS = (
    'url',
    'headers',
    'auth',
    'timeout_s',
    'retries',
    'rate_limit',
    'cache_ttl_s',
    'max_response_bytes',
)


class SQLObservations:
    def __init__(self, cfg: Mapping[str, Any]):
        source = dict(cfg['source'])
        self.dialect = source['dialect']
        self.checked = sqlguard.check(cfg['query'], self.dialect, SQL_PARAMETERS)
        self.db = database_for(target_for(source))
        self.time: dict[str, Any] = dict(cfg['time'])
        self.value: dict[str, Any] = dict(cfg['value'])
        self.parameter: dict[str, Any] | None = cfg.get('parameter')

    def bind(self, moment: datetime) -> Any:
        # SQLite has no datetime type: compare with ISO 8601 text in UTC.
        return to_iso(moment) if self.dialect == 'sqlite' else moment

    def fetch(self, location_id: Any, upstream: Any, start: datetime, end: datetime) -> Series:
        params = {
            'location_id': location_id,
            'parameter': upstream,
            'start': self.bind(start),
            'end': self.bind(end),
        }
        used = {k: v for k, v in params.items() if k in self.checked.placeholders}
        with self.db.connect() as conn:
            columns, found = rows(conn, self.checked.sql, used)
        index = {name: i for i, name in enumerate(columns)}
        t_col, v_col = index.get(self.time['from']), index.get(self.value['from'])
        if t_col is None or v_col is None:
            missing = [c for c in (self.time['from'], self.value['from']) if c not in index]
            raise DatabaseError(f'The query has no column {missing[0]!r}', kind='query')
        p_col = index.get(self.parameter['from']) if self.parameter else None
        out: Series = []
        for row in found:
            if p_col is not None and str(row[p_col]) != str(upstream):
                continue
            moment = parse_datetime(row[t_col], self.time.get('format', 'iso8601'))
            if moment is not None:
                out.append((moment, coerce(row[v_col], {'type': 'number'})))
        return out


# --- the engine ----------------------------------------------------------------------------


class TimeSeriesEngine:
    """Locations, observations and CoverageJSON for one spec; one per process."""

    def __init__(self, spec: Spec):
        data = spec.data
        self.spec = spec
        self.locations = Locations(spec)
        self.parameters: dict[str, dict[str, Any]] = {p['id']: p for p in data['parameters']}
        obs = data['observations']
        self.observations: HTTPObservations | SQLObservations = (
            HTTPObservations(obs['http']) if obs['from'] == 'http' else SQLObservations(obs['sql'])
        )
        self.default_window = parse_duration(obs['default_window'])
        self.max_window = parse_duration(obs['max_window'])
        self.max_window_text: str = obs['max_window']
        self.max_locations = int(data['limits']['max_locations'])
        self.max_observations = int(data['limits']['max_observations'])
        self.snap_km = float(data['position']['snap_radius_km'])

    # --- requests

    def window(
        self, datetime_: str | None, now: datetime | None = None
    ) -> tuple[datetime, datetime]:
        now = now or datetime.now(UTC)
        start, end = parse_interval(datetime_) if datetime_ else (None, None)
        if start is None and end is None:
            start, end = now - self.default_window, now
        elif end is None:
            assert start is not None
            end = start + self.default_window
        elif start is None:
            start = end - self.default_window
        assert start is not None and end is not None
        if end < start:
            raise ProviderInvalidQueryError(
                user_msg='The datetime interval ends before it starts.'
            )
        if end - start > self.max_window:
            raise ProviderInvalidQueryError(
                user_msg=f'Ask for at most {self.max_window_text} of observations at a time.'
            )
        return start, end

    def parameter_ids(self, select: Sequence[str] | None) -> list[str]:
        if not select:
            return list(self.parameters)
        unknown = [p for p in select if p not in self.parameters]
        if unknown:
            raise ProviderInvalidQueryError(user_msg=f'Unknown parameter-name {unknown[0]}')
        return list(dict.fromkeys(select))

    def cap(self, limit: int | None) -> int:
        return max(1, min(int(limit or self.max_locations), self.max_locations))

    # --- CoverageJSON

    def parameter_json(self, pid: str) -> dict[str, Any]:
        p = self.parameters[pid]
        observed: dict[str, Any] = {'label': {'en': p['label']}}
        if p.get('observed_property'):
            observed['id'] = p['observed_property']
        if p.get('description'):
            observed['description'] = {'en': p['description']}
        unit: dict[str, Any] = {'symbol': p['unit']['symbol']}
        if p['unit'].get('label'):
            unit['label'] = {'en': p['unit']['label']}
        if p.get('unit_uri'):
            unit['id'] = p['unit_uri']
        out: dict[str, Any] = {'type': 'Parameter', 'observedProperty': observed, 'unit': unit}
        if p.get('description'):
            out['description'] = {'en': p['description']}
        return out

    def coverage(
        self, location: Mapping[str, Any], xy: tuple[float, float], pid: str, series: Series
    ) -> dict[str, Any]:
        series = sorted(series, key=lambda s: s[0])[: self.max_observations]
        return {
            'type': 'Coverage',
            'id': f'{location["id"]}-{pid}',
            'domain': {
                'type': 'Domain',
                'domainType': 'PointSeries',
                'axes': {
                    'x': {'values': [xy[0]]},
                    'y': {'values': [xy[1]]},
                    't': {'values': [to_iso(t) for t, _ in series]},
                },
                'referencing': [GEOGRAPHIC_CRS, TEMPORAL_RS],
            },
            'ranges': {
                pid: {
                    'type': 'NdArray',
                    'dataType': 'float',
                    'axisNames': ['t'],
                    'shape': [len(series)],
                    'values': [v for _, v in series],
                }
            },
        }

    def collection(
        self,
        locations: Sequence[dict[str, Any]],
        select: Sequence[str] | None,
        datetime_: str | None,
    ) -> dict[str, Any]:
        pids = self.parameter_ids(select)
        start, end = self.window(datetime_)
        coverages, used = [], set()
        for location in locations:
            xy = point_of(location)
            if xy is None:
                continue
            for pid in pids:
                upstream = self.parameters[pid].get('upstream', pid)
                series = self.observations.fetch(location['id'], upstream, start, end)
                if series:
                    coverages.append(self.coverage(location, xy, pid, series))
                    used.add(pid)
        if not coverages:
            raise ProviderNoDataError(user_msg='No observations match this query.')
        return {
            'type': 'CoverageCollection',
            'domainType': 'PointSeries',
            'parameters': {pid: self.parameter_json(pid) for pid in pids if pid in used},
            'coverages': coverages,
        }

    def location_collection(self, features: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return {
            'type': 'FeatureCollection',
            'features': list(features),
            'numberReturned': len(features),
        }

    def nearest(self, point: Any) -> dict[str, Any]:
        x, y = float(point.x), float(point.y)
        dlat = self.snap_km / 111.0
        dlon = self.snap_km / max(1e-6, 111.32 * math.cos(math.radians(y)))
        candidates = self.locations.find([x - dlon, y - dlat, x + dlon, y + dlat], 10_000)
        best: tuple[float, dict[str, Any]] | None = None
        for feature in candidates:
            xy = point_of(feature)
            if xy is None:
                continue
            km = haversine_km((x, y), xy)
            if km <= self.snap_km and (best is None or km < best[0]):
                best = (km, feature)
        if best is None:
            raise ProviderNoDataError(
                user_msg=f'No monitoring location within {self.snap_km:g} km of that point.'
            )
        return best[1]


@functools.lru_cache(maxsize=32)
def ts_engine_for(spec: Spec) -> TimeSeriesEngine:
    return TimeSeriesEngine(spec)


class TimeSeriesEDRProvider(BaseEDRProvider):  # type: ignore[misc]  # pygeoapi is untyped
    """An EDR provider driven by a ``timeseries-edr`` spec (``data`` points at it)."""

    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        try:
            self.spec = load_spec(provider_def['data'])
        except SpecError as err:
            raise ProviderConnectionError(
                str(err), user_msg='The provider spec is invalid.'
            ) from err
        if self.spec.data['template'] != 'timeseries-edr':
            raise ProviderTypeError(
                user_msg=f'{self.spec.data["template"]} specs need another provider class'
            )
        try:
            self.engine = ts_engine_for(self.spec)
        except (sqlguard.SQLGuardError, DatabaseError, SpecError, ValueError) as err:
            raise ProviderConnectionError(
                str(err), user_msg='The provider spec is invalid.'
            ) from err
        # An instance attribute narrows what the class registered (ADR-0009).
        self.query_types = list(self.spec.data['query_types'])
        self._fields = self.spec.fields()  # with x-ogc-unit, which get_parameters() needs

    def get_fields(self) -> dict[str, dict[str, Any]]:
        return self._fields

    def _require(self, query_type: str) -> None:
        if query_type not in self.query_types:
            raise ProviderInvalidQueryError(user_msg=f'{query_type} queries are not offered here.')

    def locations(
        self,
        location_id: str | None = None,
        bbox: Sequence[float] | None = None,
        datetime_: str | None = None,
        select_properties: Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._require('locations')
        engine = self.engine
        with ts_errors():
            if not location_id:
                return engine.location_collection(engine.locations.find(bbox, engine.cap(limit)))
            try:
                location = engine.locations.get(location_id)
            except ProviderItemNotFoundError as err:
                raise ProviderItemNotFoundError(user_msg='Unknown location id.') from err
            return engine.collection([location], select_properties, datetime_)

    def position(
        self,
        wkt: Any = None,
        datetime_: str | None = None,
        select_properties: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._require('position')
        if wkt is None or getattr(wkt, 'geom_type', None) != 'Point':
            raise ProviderInvalidQueryError(user_msg='position needs coords=POINT(x y).')
        with ts_errors():
            location = self.engine.nearest(wkt)
            return self.engine.collection([location], select_properties, datetime_)

    def area(
        self,
        wkt: Any = None,
        datetime_: str | None = None,
        select_properties: Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._require('area')
        if wkt is None or getattr(wkt, 'geom_type', None) not in ('Polygon', 'MultiPolygon'):
            raise ProviderInvalidQueryError(user_msg='area needs coords=POLYGON((…)).')
        engine = self.engine
        with ts_errors():
            candidates = engine.locations.find(list(wkt.bounds), 10_000)
            inside = [f for f in candidates if (xy := point_of(f)) and wkt.intersects(Point(xy))]
            return engine.collection(inside[: engine.cap(limit)], select_properties, datetime_)

    def cube(
        self,
        bbox: Sequence[float] | None = None,
        datetime_: str | None = None,
        select_properties: Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._require('cube')
        if not bbox:
            raise ProviderInvalidQueryError(user_msg='cube needs a bbox.')
        engine = self.engine
        with ts_errors():
            b = list(bbox)
            area = box(*((b[0], b[1], b[3], b[4]) if len(b) == 6 else b[:4]))
            candidates = engine.locations.find(list(area.bounds), 10_000)
            inside = [f for f in candidates if (xy := point_of(f)) and area.intersects(Point(xy))]
            return engine.collection(inside[: engine.cap(limit)], select_properties, datetime_)

    def __repr__(self) -> str:
        spec = getattr(self, 'spec', None)
        return f'<TimeSeriesEDRProvider> {spec.id if spec else "(no spec)"}'
