"""Suggestions for a new spec, from one sample (PLAN.md §7.4, step 2).

Pure functions over sampled data, which is only data. For a JSON response: the
record array, id candidates, the geometry, date and time fields, property types,
and paging hints from the body, the Link header and the URL. For a SQL query
(``suggest_sql``): the id column, the geometry encoding, and property types from
the profiled columns. Every suggestion is a starting point the wizard asks the
steward to confirm.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

RECORD_NAMES = ('items', 'features', 'data', 'results', 'records', 'rows', 'stations', 'list')
ID_NAMES = ('id', 'uuid', 'objectid', 'fid', 'gid', 'key', 'code')
LON_NAMES = ('lon', 'lng', 'long', 'longitude', 'x')
LAT_NAMES = ('lat', 'latitude', 'y')
TITLE_NAMES = ('name', 'title', 'label', 'station_name', 'site_name', 'display_name')
TOTAL_NAMES = (
    'total',
    'count',
    'totalcount',
    'total_count',
    'numbermatched',
    'totalresults',
    'total_results',
    'hits',
)
CURSOR_NAMES = (
    'next_cursor',
    'nextcursor',
    'cursor',
    'next_page_token',
    'nextpagetoken',
    'continuation',
    'continuation_token',
)
OFFSET_PARAMS = ('offset', 'start', 'skip', 'from', 'startindex', 'resultoffset')
LIMIT_PARAMS = (
    'limit',
    'size',
    'count',
    'pagesize',
    'page_size',
    'per_page',
    'rows',
    'max',
    'resultrecordcount',
)
PAGE_PARAMS = ('page', 'pagenumber', 'page_number', 'p')
WKT = re.compile(r'^\s*(MULTI)?(POINT|LINESTRING|POLYGON)\s*(Z|M|ZM)?\s*\(', re.IGNORECASE)
GEOJSON_TYPES = {
    'Point',
    'MultiPoint',
    'LineString',
    'MultiLineString',
    'Polygon',
    'MultiPolygon',
    'GeometryCollection',
}
EPOCH_S = (0, 4102444800)  # 1970 to 2100
NAME = re.compile(r'[^A-Za-z0-9]+')


def jsonpath_key(key: str) -> str:
    """A normalized RFC 9535 child selector."""
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
        return f'.{key}'
    escaped = key.replace('\\', '\\\\').replace("'", "\\'")
    return f"['{escaped}']"


def snake(key: str) -> str:
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key)
    text = NAME.sub('_', text).strip('_').lower() or 'field'
    return text if text[0].isalpha() or text[0] == '_' else f'f_{text}'


def walk_arrays(node: Any, path: str = '$', depth: int = 0) -> Iterator[tuple[str, list[Any]]]:
    if depth > 6:
        return
    if isinstance(node, list):
        yield path, node
        if node and isinstance(node[0], Mapping):
            return
        for i, item in enumerate(node[:3]):
            yield from walk_arrays(item, f'{path}[{i}]', depth + 1)
    elif isinstance(node, Mapping):
        for key, value in node.items():
            yield from walk_arrays(value, path + jsonpath_key(str(key)), depth + 1)


def find_records(doc: Any) -> tuple[str | None, list[Mapping[str, Any]]]:
    """The largest array of objects, preferring the usual names."""
    best: tuple[float, str, list[Mapping[str, Any]]] | None = None
    for path, array in walk_arrays(doc):
        objects = [x for x in array if isinstance(x, Mapping)]
        if not objects or len(objects) < len(array) * 0.9:
            continue
        name = re.split(r'[.\[]', path.rstrip(']'))[-1].strip('\'"').lower()
        score = len(objects) + (1000 if name in RECORD_NAMES else 0) - path.count('.') * 0.1
        if best is None or score > best[0]:
            best = (score, path, objects)
    if best is None:
        return None, []
    return best[1], best[2]


def flatten(
    record: Mapping[str, Any], prefix: str = '$', depth: int = 0
) -> Iterator[tuple[str, str, Any]]:
    """(name, JSONPath relative to the record, value) for scalars, one level into objects."""
    for key, value in record.items():
        path = prefix + jsonpath_key(str(key))
        if isinstance(value, Mapping) and depth == 0 and not is_geojson(value):
            for inner, inner_path, inner_value in flatten(value, path, depth + 1):
                yield f'{key}_{inner}', inner_path, inner_value
        else:
            yield str(key), path, value


def is_geojson(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get('type') in GEOJSON_TYPES
        and ('coordinates' in value or 'geometries' in value)
    )


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def time_format(name: str, values: Sequence[Any]) -> str | None:
    """iso8601, epoch_s or epoch_ms when every non-null value reads as one."""
    present = [v for v in values if v not in (None, '')]
    if not present:
        return None
    if all(isinstance(v, str) for v in present):
        try:
            for v in present:
                datetime.fromisoformat(v.strip())
        except ValueError:
            return None
        return 'iso8601' if any(c in present[0] for c in ('-', 'T')) else None
    timeish = re.search(r'time|date|epoch|_at$|^at$|stamp', name, re.IGNORECASE)
    numbers = [number(v) for v in present]
    if not timeish or any(n is None for n in numbers):
        return None
    if all(EPOCH_S[0] <= n <= EPOCH_S[1] for n in numbers if n is not None):
        return 'epoch_s'
    if all(EPOCH_S[0] * 1000 <= n <= EPOCH_S[1] * 1000 for n in numbers if n is not None):
        return 'epoch_ms'
    return None


def kind(values: Sequence[Any]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return 'string'
    if all(isinstance(v, bool) for v in present):
        return 'boolean'
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return 'integer'
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return 'number'
    return 'string'


def profile(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per flattened field: path, type, date format, nulls, uniqueness, examples."""
    rows = [{name: (path, value) for name, path, value in flatten(r)} for r in records]
    names = list(dict.fromkeys(name for row in rows for name in row))
    out = []
    for name in names:
        path = next(row[name][0] for row in rows if name in row)
        values = [row[name][1] if name in row else None for row in rows]
        present = [v for v in values if v is not None and v != '']
        scalars = [v for v in present if not isinstance(v, list | Mapping)]
        col_type = 'object' if len(scalars) < len(present) else kind(present)
        distinct = len({repr(v) for v in present})
        out.append(
            {
                'name': name,
                'path': path,
                'type': col_type,
                'format': time_format(name, present) if col_type != 'object' else None,
                'nulls': len(values) - len(present),
                'distinct': distinct,
                'unique': bool(present) and distinct == len(values) == len(present),
                'examples': present[:3],
            }
        )
    return out


def suggest_id(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [c for c in columns if c['unique'] and c['type'] in ('string', 'integer')]

    def score(c: dict[str, Any]) -> tuple[int, int]:
        name = c['name'].lower()
        exact = name in ID_NAMES or name.endswith(('_id', 'id'))
        return (0 if exact else 1, len(name))

    candidates.sort(key=score)
    return (
        {'from': ref(candidates[0]), 'candidates': [ref(c) for c in candidates]}
        if candidates
        else None
    )


def ref(column: dict[str, Any]) -> str:
    """A plain key for top-level columns, else the relative JSONPath."""
    path = str(column['path'])
    return path[2:] if path.count('.') == 1 and path.startswith('$.') else path


def suggest_geometry(
    columns: list[dict[str, Any]], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    for record in records[:1]:
        for key, value in record.items():
            if is_geojson(value):
                return {'type': 'geojson', 'field': str(key)}
    for c in columns:
        if (
            c['type'] == 'string'
            and c['examples']
            and all(isinstance(v, str) and WKT.match(v) for v in c['examples'])
        ):
            return {'type': 'wkt', 'field': ref(c)}

    def pick(names: Sequence[str], low: float, high: float) -> dict[str, Any] | None:
        for c in columns:
            leaf = c['name'].lower().split('_')[-1]
            if (
                (c['name'].lower() in names or leaf in names)
                and c['type'] in ('number', 'integer')
                and all(low <= (number(v) or 0) <= high for v in c['examples'])
            ):
                return c
        return None

    x, y = pick(LON_NAMES, -180, 180), pick(LAT_NAMES, -90, 90)
    if x and y:
        return {'type': 'point', 'x': ref(x), 'y': ref(y)}
    return {'type': 'none'}


def suggest_pagination(doc: Any, headers: Mapping[str, str], url: str) -> dict[str, Any]:
    hints: list[str] = []
    out: dict[str, Any] = {'style': 'none'}
    lower = {k.lower(): v for k, v in headers.items()}
    if 'rel="next"' in lower.get('link', '') or 'rel=next' in lower.get('link', ''):
        out = {'style': 'next_link', 'next_link': {'next': 'header:Link'}}
        hints.append('The Link header has a rel="next" link.')
    total = find_total(doc)
    if total:
        out['total'] = total
        hints.append(f'{total} looks like the total number of records.')
    if isinstance(doc, Mapping) and out['style'] == 'none':
        nxt = find_next_link(doc)
        if nxt:
            out = {
                'style': 'next_link',
                'next_link': {'next': nxt},
                **({'total': total} if total else {}),
            }
            hints.append(f'{nxt} looks like a link to the next page.')
        else:
            cursor = find_key(doc, CURSOR_NAMES)
            if cursor:
                out = {
                    'style': 'cursor',
                    'cursor': {'cursor_param': 'cursor', 'cursor_path': cursor},
                    **({'total': total} if total else {}),
                }
                hints.append(
                    f'{cursor} looks like a cursor; check the parameter name the API expects.'
                )
    params = {k.lower(): k for k, _ in parse_qsl(urlsplit(url).query)}
    if out['style'] == 'none':
        offset = next((params[p] for p in OFFSET_PARAMS if p in params), None)
        limit = next((params[p] for p in LIMIT_PARAMS if p in params), None)
        page = next((params[p] for p in PAGE_PARAMS if p in params), None)
        if offset and limit:
            out.update(style='offset', offset_param=offset, limit_param=limit, max_page_size=100)
            hints.append(f'The URL pages with {offset} and {limit}.')
        elif page:
            out.update(
                style='page',
                max_page_size=100,
                page={
                    'page_param': page,
                    'page_start': 1,
                    **({'size_param': limit} if limit else {}),
                },
            )
            hints.append(f'The URL pages with {page}.')
    if out['style'] == 'none' and total:
        hints.append('There is a total but no paging parameters in the URL; check the API docs.')
    out['hints'] = hints
    return out


def find_key(
    doc: Mapping[str, Any], names: Sequence[str], path: str = '$', depth: int = 0
) -> str | None:
    for key, value in doc.items():
        here = path + jsonpath_key(str(key))
        if (
            str(key).lower() in names
            and isinstance(value, str | int)
            and not isinstance(value, bool)
        ):
            return here
        if isinstance(value, Mapping) and depth < 2:
            found = find_key(value, names, here, depth + 1)
            if found:
                return found
    return None


def find_total(doc: Any) -> str | None:
    if not isinstance(doc, Mapping):
        return None
    for key, value in doc.items():
        here = '$' + jsonpath_key(str(key))
        if (
            str(key).lower() in TOTAL_NAMES
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            return here
        if isinstance(value, Mapping):
            for inner, inner_value in value.items():
                if (
                    str(inner).lower() in TOTAL_NAMES
                    and isinstance(inner_value, int)
                    and not isinstance(inner_value, bool)
                ):
                    return here + jsonpath_key(str(inner))
    return None


def find_next_link(doc: Mapping[str, Any]) -> str | None:
    for key, value in doc.items():
        if (
            str(key).lower() in ('next', 'next_url', 'nextlink', 'next_link', 'nextpage')
            and isinstance(value, str)
            and value
        ):
            return '$' + jsonpath_key(str(key))
        if str(key).lower() == 'links' and isinstance(value, list):
            for link in value:
                if isinstance(link, Mapping) and link.get('rel') == 'next' and link.get('href'):
                    return f"$.{key}[?@.rel=='next'].href"
    return None


def suggest_title(
    properties: list[dict[str, Any]], column_of: Mapping[str, dict[str, Any]], size: int
) -> str | None:
    """A text field that names each item: name-like, and distinct across records."""
    best: tuple[int, float, str] | None = None
    for p in properties:
        if p['type'] != 'string':
            continue
        name = p['name'].lower()
        named = (
            2
            if name in TITLE_NAMES
            else 1
            if any(w in name for w in ('name', 'title', 'label'))
            else 0
        )
        if not named:
            continue
        distinct = column_of[p['name']]['distinct'] / max(1, size)
        score = (named, distinct, p['name'])
        if best is None or score[:2] > best[:2]:
            best = score
    return best[2] if best else None


def records_at(doc: Any, path: str) -> list[Mapping[str, Any]]:
    from pygeoapi_spec_providers.jsonpaths import findall

    matches = findall(path, doc)
    if len(matches) == 1 and isinstance(matches[0], list):
        matches = matches[0]
    return [m for m in matches if isinstance(m, Mapping)]


def suggest(
    doc: Any, headers: Mapping[str, str] | None = None, url: str = '', records: str | None = None
) -> dict[str, Any]:
    """Suggestions for the record array ``records`` (a JSONPath), or the likeliest one."""
    if records:
        return _suggest(doc, headers, url, records, records_at(doc, records))
    path, found = find_records(doc)
    return _suggest(doc, headers, url, path, found)


def _suggest(
    doc: Any,
    headers: Mapping[str, str] | None,
    url: str,
    records_path: str | None,
    records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if records_path is None:
        return {
            'records': None,
            'record_count': 0,
            'columns': [],
            'notes': ['No array of objects was found in the response.'],
        }
    sample = list(records[:200])
    columns = profile(sample)
    item_id = suggest_id(columns)
    geometry = suggest_geometry(columns, sample)
    geometry_refs = {geometry.get('x'), geometry.get('y'), geometry.get('field')}
    properties = []
    column_of: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for c in columns:
        if c['type'] == 'object' or ref(c) in geometry_refs:
            continue
        name = snake(c['name'])
        while name in seen:
            name += '_'
        seen.add(name)
        prop: dict[str, Any] = {'name': name, 'type': 'datetime' if c['format'] else c['type']}
        if ref(c) != name:
            prop['from'] = ref(c)
        if c['format'] and c['format'] != 'iso8601':
            prop['format'] = c['format']
        properties.append(prop)
        column_of[name] = c
    time_field = next((p['name'] for p in properties if p['type'] == 'datetime'), None)
    title_field = suggest_title(properties, column_of, len(sample))
    notes = []
    if item_id is None:
        notes.append(
            "No field is unique in this sample: pick an id, or items can't be fetched by id."
        )
    if geometry['type'] == 'none':
        notes.append('No geometry was found: items will be published without one.')
    return {
        'records': records_path,
        'record_count': len(records),
        'columns': columns,
        'item_id': item_id,
        'geometry': geometry,
        'properties': properties,
        'time_field': time_field,
        'title_field': title_field,
        'pagination': suggest_pagination(doc, headers or {}, url),
        'notes': notes,
        'generated': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


# --- SQL -------------------------------------------------------------------------------------

#: Python value types (from the sandbox's profile) to spec property types.
SQL_KINDS = {
    'int': 'integer',
    'float': 'number',
    'Decimal': 'number',
    'bool': 'boolean',
    'datetime': 'datetime',
    'date': 'datetime',
    'str': 'string',
}


def sql_type(column: Mapping[str, Any]) -> tuple[str, str | None]:
    """A property type, plus a datetime format for text or epoch columns."""
    kinds = [k for k in column.get('kinds') or [] if k != 'NoneType']
    mapped = {SQL_KINDS.get(k, 'string') for k in kinds}
    if not mapped:
        return 'string', None
    if mapped == {'integer'}:
        kind_ = 'integer'
    elif mapped <= {'integer', 'number'}:
        kind_ = 'number'
    elif len(mapped) == 1:
        kind_ = next(iter(mapped))
    else:
        kind_ = 'string'
    if kind_ in ('string', 'integer', 'number'):
        fmt = time_format(str(column['name']), column.get('examples') or [])
        if fmt:
            return 'datetime', None if kind_ == 'string' and fmt == 'iso8601' else fmt
    return kind_, None


def suggest_sql(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Suggestions for a SQL spec from the sandbox's ``sql_sample`` profile."""
    columns: list[dict[str, Any]] = list(sample.get('columns') or [])
    typed = []
    for c in columns:
        kind_, fmt = sql_type(c)
        typed.append({**c, 'type': kind_, 'format': fmt})
    notes: list[str] = []

    def id_score(c: Mapping[str, Any]) -> tuple[int, int, int]:
        name = str(c['name']).lower()
        exact = name in ID_NAMES or name.endswith(('_id', 'id'))
        return (0 if exact else 1, 0 if c['type'] == 'integer' else 1, len(name))

    ids = sorted(
        (
            c
            for c in typed
            if c['unique'] and c['type'] in ('string', 'integer') and not c['geometry']
        ),
        key=id_score,
    )
    item_id = None
    if ids:
        item_id = {
            'from': ids[0]['name'],
            'type': 'integer' if ids[0]['type'] == 'integer' else 'string',
            'candidates': [c['name'] for c in ids],
        }
    else:
        notes.append(
            "No column is unique in this sample: pick an id, or items can't be fetched by id."
        )

    geometry: dict[str, Any] = {'encoding': 'none'}
    rank = {'native': 0, 'gpkg': 1, 'wkb': 2, 'geojson': 3, 'wkt': 4}
    found = min(
        (c for c in typed if c['geometry']), key=lambda c: rank.get(c['geometry'], 9), default=None
    )
    if found:
        geometry = {'encoding': found['geometry'], 'column': found['name']}
    else:

        def pick(names: Sequence[str], low: float, high: float) -> Mapping[str, Any] | None:
            for c in typed:
                leaf = str(c['name']).lower().split('_')[-1]
                if (
                    (str(c['name']).lower() in names or leaf in names)
                    and c['type'] in ('number', 'integer')
                    and all(low <= (number(v) or 0) <= high for v in c['examples'])
                ):
                    return c
            return None

        x, y = pick(LON_NAMES, -180, 180), pick(LAT_NAMES, -90, 90)
        if x and y:
            geometry = {'encoding': 'xy', 'x': x['name'], 'y': y['name']}
        else:
            notes.append('No geometry was found: items will be published without one.')
    geometry_columns = {geometry.get('column'), geometry.get('x'), geometry.get('y')}

    properties: list[dict[str, Any]] = []
    column_of: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for c in typed:
        if c['name'] in geometry_columns or {'bytes', 'memoryview'} & set(c.get('kinds') or []):
            continue  # the geometry, and binary columns, which JSON can't carry
        name = snake(str(c['name']))
        while name in seen:
            name += '_'
        seen.add(name)
        prop: dict[str, Any] = {'name': name, 'type': c['type']}
        if name != c['name']:
            prop['from'] = c['name']
        if c['format']:
            prop['format'] = c['format']
        properties.append(prop)
        column_of[name] = c
    time_field = next((p['name'] for p in properties if p['type'] == 'datetime'), None)
    title_field = suggest_title(properties, column_of, max(1, int(sample.get('row_count') or 1)))
    if not sample.get('row_count'):
        notes.append('The query returned no rows, so the suggestions are guesses.')
    return {
        'record_count': int(sample.get('row_count') or 0),
        'columns': typed,
        'item_id': item_id,
        'geometry': geometry,
        'properties': properties,
        'time_field': time_field,
        'title_field': title_field,
        'query': sample.get('query'),
        'notes': notes,
    }
