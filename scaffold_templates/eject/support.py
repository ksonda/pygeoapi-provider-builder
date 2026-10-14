"""Values, times, geometries, filters and sorting for an ejected provider.

When this provider was a spec, the Provider Builder's spec engine
(pygeoapi-spec-providers) applied these rules. They're plain code now, and the
package no longer depends on the engine, so change them freely.

pygeoapi passes filters as ``properties=[(name, value)]`` and
``sortby=[{'property': name, 'order': '+'|'-'}]``. A filter that the source
can't apply itself is applied here, to the mapped features (``LocalFilters``).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pygeoapi.provider.base import ProviderConnectionError, ProviderInvalidQueryError
from shapely import wkt
from shapely.errors import ShapelyError
from shapely.geometry import box, mapping, shape

TRUE = frozenset({'true', '1', 'yes', 'y', 't'})
FALSE = frozenset({'false', '0', 'no', 'n', 'f'})


def setting(options: Mapping[str, Any] | None, name: str) -> Any:
    """A required ``options`` entry of the provider config."""
    if not options or options.get(name) is None:
        raise ProviderConnectionError(user_msg=f'The provider config needs options.{name}.')
    return options[name]


def option(options: Mapping[str, Any] | None, name: str) -> str:
    """A required ``options`` entry as text, such as a key from an ``${ENVIRONMENT_VARIABLE}``.

    pygeoapi re-types the values it substitutes, so a numeric key arrives as a
    number: it's turned back into text here.
    """
    return str(setting(options, name))


# --- reading records -------------------------------------------------------------------------


def dig(value: Any, keys: Sequence[str | int]) -> Any:
    """``value[k1][k2]…``, or None when a key is missing (a JSONPath like ``$.a.b[0]``)."""
    for key in keys:
        if isinstance(key, int):
            if not isinstance(value, list) or not -len(value) <= key < len(value):
                return None
            value = value[key]
        elif isinstance(value, Mapping):
            value = value.get(key)
        else:
            return None
    return value


def records_at(value: Any, each: bool = False) -> list[Mapping[str, Any]]:
    """The records at a path: the objects of a list, or one object.

    ``each`` is for a path that ends in ``[*]``, whose matches are the
    elements (or member values) of what it points at.
    """
    if each:
        if isinstance(value, list):
            matches = list(value)
        elif isinstance(value, Mapping):
            matches = list(value.values())
        else:
            matches = []
    else:
        matches = [] if value is None else [value]
    if len(matches) == 1 and isinstance(matches[0], list):
        matches = matches[0]
    return [m for m in matches if isinstance(m, Mapping)]


# --- values ----------------------------------------------------------------------------------


def parse_datetime(value: Any, fmt: str = 'iso8601') -> datetime | None:
    """A timezone-aware datetime (naive input is taken as UTC), or None.

    ``fmt`` is ``iso8601``, ``date``, ``epoch_s``, ``epoch_ms`` or a strftime pattern.
    """
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


def format_moment(moment: datetime, fmt: str) -> str:
    """A datetime as a source API expects it in a request (``fmt`` as for parse_datetime)."""
    moment = moment.astimezone(UTC)
    if fmt == 'iso8601':
        return to_iso(moment)
    if fmt == 'date':
        return moment.date().isoformat()
    if fmt == 'epoch_s':
        return str(int(moment.timestamp()))
    if fmt == 'epoch_ms':
        return str(int(moment.timestamp() * 1000))
    return moment.strftime(fmt)


def text(value: Any, fmt: str | None = None) -> str:
    """One value in a URL or parameter: ISO 8601 for datetimes, shortest form for numbers."""
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo is not None else value
        return moment.strftime(fmt or '%Y-%m-%dT%H:%M:%SZ')
    if isinstance(value, date):
        return value.strftime(fmt) if fmt else value.isoformat()
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def coerce(value: Any, kind: str, fmt: str | None = None) -> Any:
    """A value as a property of type ``kind``; None when it doesn't convert.

    Mapping never fails on bad data: a value that doesn't convert becomes null.
    """
    if value is None:
        return None
    try:
        if kind == 'string':
            if isinstance(value, str):
                return value
            return json.dumps(value) if isinstance(value, list | dict) else str(value)
        if kind == 'boolean':
            if isinstance(value, bool):
                return value
            word = str(value).strip().lower()
            return True if word in TRUE else False if word in FALSE else None
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
            moment = parse_datetime(value, fmt or 'iso8601')
            return to_iso(moment) if moment else None
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def point(x: Any, y: Any) -> dict[str, Any] | None:
    """A GeoJSON point from two coordinate values; None unless both are numbers."""
    fx, fy = coerce(x, 'number'), coerce(y, 'number')
    if fx is None or fy is None:
        return None
    return {'type': 'Point', 'coordinates': [fx, fy]}


def geometry_from(raw: Any, encoding: str) -> dict[str, Any] | None:
    """GeoJSON from WKT text or a GeoJSON value (an object or JSON text); None if unreadable."""
    try:
        if encoding == 'wkt':
            geom = wkt.loads(str(raw))
        else:
            geom = shape(json.loads(raw) if isinstance(raw, str) else raw)
        if geom.is_empty:
            return None
        result: dict[str, Any] = json.loads(json.dumps(mapping(geom)))
        return result
    except (ShapelyError, ValueError, TypeError, KeyError, AttributeError):
        return None


# --- filters the source can't apply ----------------------------------------------------------


def parse_interval(value: str) -> tuple[datetime | None, datetime | None]:
    """An OGC ``datetime`` value: an instant, or ``start/end`` with ``..`` or empty for open."""

    def one(part: str) -> datetime | None:
        part = part.strip()
        if part in ('', '..'):
            return None
        moment = parse_datetime(part.replace('Z', '+00:00'))
        if moment is None:
            raise ProviderInvalidQueryError(user_msg=f'Invalid datetime {part!r}')
        return moment

    if '/' in value:
        start, end = value.split('/', 1)
        return one(start), one(end)
    instant = one(value)
    return instant, instant


def bounds(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    """minx, miny, maxx, maxy of a 4- or 6-number bbox."""
    b = list(bbox)
    return (b[0], b[1], b[3], b[4]) if len(b) == 6 else (b[0], b[1], b[2], b[3])


def equal(value: Any, wanted: str) -> bool:
    """A property filter: the query's text against a typed property value."""
    if value is None:
        return False
    if isinstance(value, bool):
        return str(wanted).strip().lower() == ('true' if value else 'false')
    if isinstance(value, int | float):
        try:
            return float(wanted) == float(value)
        except ValueError:
            return False
    return str(value) == str(wanted)


def split_terms(q: str) -> list[str]:
    return [t.lower() for t in re.split(r'[,\s]+', q) if t]


@dataclass
class LocalFilters:
    """The filters and sorting applied after reading, because the source couldn't."""

    bbox: Sequence[float] | None = None
    interval: tuple[datetime | None, datetime | None] | None = None
    time_field: str | None = None
    properties: list[tuple[str, str]] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    sortby: list[dict[str, str]] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.filters() or self.sortby)

    def filters(self) -> bool:
        return bool(self.bbox or self.interval or self.properties or self.terms)

    def matches(self, feature: Mapping[str, Any]) -> bool:
        props = feature['properties']
        if self.bbox is not None:
            geometry = feature.get('geometry')
            if not geometry:
                return False
            try:
                if not shape(geometry).intersects(box(*bounds(self.bbox))):
                    return False
            except (ValueError, TypeError, AttributeError):
                return False
        if self.interval is not None and self.time_field:
            moment = parse_datetime(props.get(self.time_field))
            if moment is None:
                return False
            start, end = self.interval
            if (start is not None and moment < start) or (end is not None and moment > end):
                return False
        for name, wanted in self.properties:
            if not equal(props.get(name), wanted):
                return False
        if self.terms:
            texts = [str(v).lower() for v in props.values() if isinstance(v, str)]
            if not any(term in t for term in self.terms for t in texts):
                return False
        return True


def sort_features(features: list[dict[str, Any]], sortby: Iterable[Mapping[str, str]]) -> None:
    """A stable sort on several keys, in place; nulls last in either direction."""
    for key in reversed(list(sortby)):
        name, descending = key['property'], key.get('order') == '-'
        present = [f for f in features if f['properties'].get(name) is not None]
        missing = [f for f in features if f['properties'].get(name) is None]
        try:
            present.sort(key=lambda f: f['properties'][name], reverse=descending)
        except TypeError:  # mixed types: compare as text
            present.sort(key=lambda f: str(f['properties'][name]), reverse=descending)
        features[:] = present + missing


def shape_output(
    feature: dict[str, Any], select_properties: Sequence[str] | None, skip_geometry: bool
) -> dict[str, Any]:
    """``select_properties`` and ``skip_geometry``, applied to one feature."""
    if select_properties:
        keep = set(select_properties)
        feature['properties'] = {k: v for k, v in feature['properties'].items() if k in keep}
    if skip_geometry:
        feature['geometry'] = None
    return feature
