"""Filters and sorting applied to mapped features (PLAN.md §7.3, step 5).

These run when the source API can't apply a filter itself (no pushdown in the
spec's ``filters``). They follow pygeoapi's argument shapes:
``properties=[(name, value)]`` and ``sortby=[{'property': name, 'order': '+'|'-'}]``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pygeoapi.provider.base import ProviderInvalidQueryError
from shapely.geometry import box, shape

from .mapping import parse_datetime


def parse_interval(text: str) -> tuple[datetime | None, datetime | None]:
    """An OGC ``datetime`` value: an instant, or ``start/end`` with ``..`` or empty for open."""

    def one(part: str) -> datetime | None:
        part = part.strip()
        if part in ('', '..'):
            return None
        moment = parse_datetime(part.replace('Z', '+00:00'))
        if moment is None:
            raise ProviderInvalidQueryError(user_msg=f'Invalid datetime {part!r}')
        return moment

    if '/' in text:
        start, end = text.split('/', 1)
        return one(start), one(end)
    instant = one(text)
    return instant, instant


@dataclass
class LocalFilters:
    bbox: Sequence[float] | None = None
    interval: tuple[datetime | None, datetime | None] | None = None
    time_field: str | None = None
    properties: list[tuple[str, str]] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    sortby: list[dict[str, str]] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.bbox or self.interval or self.properties or self.terms or self.sortby)

    def filters(self) -> bool:
        return bool(self.bbox or self.interval or self.properties or self.terms)

    def matches(self, feature: dict[str, Any]) -> bool:
        props = feature['properties']
        if self.bbox is not None:
            geometry = feature.get('geometry')
            if not geometry:
                return False
            b = list(self.bbox)
            minx, miny, maxx, maxy = (b[0], b[1], b[3], b[4]) if len(b) == 6 else b[:4]
            try:
                if not shape(geometry).intersects(box(minx, miny, maxx, maxy)):
                    return False
            except (ValueError, TypeError, AttributeError):
                return False
        if self.interval is not None and self.time_field:
            moment = parse_datetime(props.get(self.time_field))
            if moment is None:
                return False
            start, end = self.interval
            if start is not None and moment < start:
                return False
            if end is not None and moment > end:
                return False
        for name, wanted in self.properties:
            if not equal(props.get(name), wanted):
                return False
        if self.terms:
            texts = [str(v).lower() for v in props.values() if isinstance(v, str)]
            if not any(term in text for term in self.terms for text in texts):
                return False
        return True


def equal(value: Any, wanted: str) -> bool:
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


def sort_features(features: list[dict[str, Any]], sortby: Iterable[dict[str, str]]) -> None:
    """Stable multi-key sort; nulls last in either direction."""
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
    feature: dict[str, Any], select_properties: Sequence[str], skip_geometry: bool
) -> dict[str, Any]:
    if select_properties:
        keep = set(select_properties)
        feature['properties'] = {k: v for k, v in feature['properties'].items() if k in keep}
    if skip_geometry:
        feature['geometry'] = None
    return feature
