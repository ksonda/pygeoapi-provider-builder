"""Records to GeoJSON features (PLAN.md §7.3, steps 3 and 4).

Mapping never raises on bad data: a value that doesn't coerce becomes null,
and a record without an id or with an unreadable geometry still maps, minus
the bad part. ``Mapper.stats`` counts what was dropped, for the harness.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from shapely import wkt
from shapely.errors import ShapelyError
from shapely.geometry import mapping, shape

from .client import SourceError
from .jsonpaths import findall, first, value_of
from .spec import Spec

TRUE = frozenset({'true', '1', 'yes', 'y', 't'})
FALSE = frozenset({'false', '0', 'no', 'n', 'f'})


def extract_records(spec: Spec, doc: Any) -> list[Mapping[str, Any]]:
    """The records under the spec's ``records`` path: one array, or each match."""
    matches = findall(spec.data['records'], doc)
    if len(matches) == 1 and isinstance(matches[0], list):
        matches = matches[0]
    return [m for m in matches if isinstance(m, Mapping)]


def check_error(spec: Spec, doc: Any) -> None:
    """Raise when an HTTP 200 body carries an error (ESRI style, ``source.error_path``)."""
    path = spec.source.get('error_path')
    if not path:
        return
    error = first(path, doc)
    if error in (None, '', [], {}, False):
        return
    if isinstance(error, Mapping):
        error = error.get('message') or error.get('details') or json.dumps(error)[:200]
    raise SourceError(f'The source API reported an error: {str(error)[:200]}', kind='api_error')


def parse_datetime(value: Any, fmt: str = 'iso8601') -> datetime | None:
    """A timezone-aware datetime (naive input is taken as UTC), or None."""
    if value is None or value == '':
        return None
    try:
        if fmt in ('epoch_s', 'epoch_ms'):
            seconds = float(value) / (1000 if fmt == 'epoch_ms' else 1)
            return datetime.fromtimestamp(seconds, UTC)
        if isinstance(value, datetime):
            moment = value
        elif fmt == 'date':
            day = date.fromisoformat(str(value)[:10])
            moment = datetime(day.year, day.month, day.day)
        elif fmt == 'iso8601':
            moment = datetime.fromisoformat(str(value).strip())
        else:
            moment = datetime.strptime(str(value).strip(), fmt)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def to_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def coerce(value: Any, prop: Mapping[str, Any]) -> Any:
    if value is None:
        return None
    kind = prop['type']
    try:
        if kind == 'string':
            return (
                value
                if isinstance(value, str)
                else json.dumps(value)
                if isinstance(value, list | dict)
                else str(value)
            )
        if kind == 'boolean':
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            return True if text in TRUE else False if text in FALSE else None
        if kind == 'integer':
            if isinstance(value, bool):
                return int(value)
            number = float(value)
            return int(number) if number.is_integer() else None
        if kind == 'number':
            if isinstance(value, bool):
                return None
            number = float(value)
            return number if math.isfinite(number) else None
        if kind == 'datetime':
            moment = parse_datetime(value, prop.get('format', 'iso8601'))
            return to_iso(moment) if moment else None
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class Mapper:
    def __init__(self, spec: Spec):
        self.id_ref: str = spec.data['item_id']['from']
        self.geometry_spec: dict[str, Any] = dict(spec.data['geometry'])
        self.props: list[dict[str, Any]] = spec.properties
        self.stats: Counter[str] = Counter()

    def feature(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        ident = value_of(record, self.id_ref)
        if ident is None or ident == '' or isinstance(ident, list | dict):
            self.stats['no_id'] += 1
            return None
        properties = {}
        for prop in self.props:
            raw = value_of(record, prop.get('from', prop['name']))
            value = coerce(raw, prop)
            if raw is not None and value is None:
                self.stats[f'uncoerced:{prop["name"]}'] += 1
            properties[prop['name']] = value
        return {
            'type': 'Feature',
            'id': ident,
            'geometry': self.geometry(record),
            'properties': properties,
        }

    def geometry(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        spec = self.geometry_spec
        kind = spec['type']
        if kind == 'none':
            return None
        if kind == 'point':
            x, y = _float(value_of(record, spec['x'])), _float(value_of(record, spec['y']))
            if x is None or y is None:
                self.stats['no_geometry'] += 1
                return None
            return {'type': 'Point', 'coordinates': [x, y]}
        raw = value_of(record, spec['field'])
        try:
            if kind == 'wkt':
                geom = wkt.loads(str(raw))
            else:
                data = json.loads(raw) if isinstance(raw, str) else raw
                geom = shape(data)
            if geom.is_empty:
                raise ValueError('empty')
            result: dict[str, Any] = json.loads(json.dumps(mapping(geom)))
            return result
        except (ShapelyError, ValueError, TypeError, KeyError, AttributeError):
            self.stats['no_geometry'] += 1
            return None
