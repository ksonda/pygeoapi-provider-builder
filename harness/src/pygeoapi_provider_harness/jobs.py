"""Jobs the sandbox worker can run.

Every job that touches a provider starts from YAML text that still contains
``${VAR}`` placeholders and resolves it with pygeoapi's own ``yaml_load``,
so env-var semantics are exactly pygeoapi's (docs/pygeoapi-notes.md §2).
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .util import remap_paths

SAMPLE_ROWS = 20
MAX_TABLES = 500

JobFn = Callable[[dict[str, Any]], dict[str, Any]]


def _load_yaml(text: str) -> Any:
    from pygeoapi.util import yaml_load

    return yaml_load(io.StringIO(text))


FILES_PLACEHOLDER = '@files@'


def _materialize(job: dict[str, Any], key: str = 'resource_yaml') -> str:
    """The job's YAML text, with ``@files@`` pointing at the job's own files.

    ``job['files']`` maps flat file names to text, for example an unsaved spec
    (``data: @files@/spec.yaml``). They're written to a fresh temporary folder.
    ``job['python_path']`` entries are added to ``sys.path``: ``@files@`` paths,
    or a code-built provider's ``src`` folder in the read-only workspace mount
    (remapped like other server paths for the subprocess runner). This runs only
    inside the sandbox.
    """
    text = str(job[key])
    files = job.get('files') or {}
    workdir = None
    if files:
        workdir = job.setdefault('_files_dir', tempfile.mkdtemp(prefix='job-files-'))
        for name, content in files.items():
            if not name or '/' in name or '\\' in name or name.startswith('.'):
                raise ValueError(f'Invalid job file name {name!r}')
            Path(workdir, name).write_text(content, encoding='utf-8')
        text = text.replace(FILES_PLACEHOLDER, workdir)
    for entry in job.get('python_path') or []:
        path = str(entry)
        if FILES_PLACEHOLDER in path:
            if workdir is None:
                continue
            path = path.replace(FILES_PLACEHOLDER, workdir)
        path = str(remap_paths(path, job.get('path_map', [])))
        if path not in sys.path:
            sys.path.insert(0, path)
    return text


def _resource(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The job's one resource, placeholders resolved, paths remapped."""
    resource = remap_paths(_load_yaml(_materialize(job)), job.get('path_map', []))
    if not isinstance(resource, dict) or len(resource) != 1:
        raise ValueError('resource_yaml must hold exactly one resource')
    ((rid, body),) = resource.items()
    return str(rid), body


# --------------------------------------------------------------------------- probe


def probe(job: dict[str, Any]) -> dict[str, Any]:
    """Load a provider with ``load_plugin`` and exercise it like the API does."""
    from pygeoapi.plugin import load_plugin

    rid, body = _resource(job)
    pdef = dict(body['providers'][int(job.get('provider_index', 0))])
    limit = int(job.get('limit', 5))
    timings: dict[str, float] = {}
    if job.get('copy_data') and isinstance(pdef.get('data'), str) and os.path.isfile(pdef['data']):
        # Some providers write to their file on load (SQLiteGPKG): probe a copy.
        workdir = tempfile.mkdtemp(prefix='probe-')
        copy = os.path.join(workdir, os.path.basename(pdef['data']))
        shutil.copyfile(pdef['data'], copy)
        pdef = {**pdef, 'data': copy}

    t0 = time.perf_counter()
    provider = load_plugin('provider', pdef)
    timings['load_ms'] = _ms(t0)

    fields = dict(getattr(provider, 'fields', {}) or {})
    fields_cached = bool(fields)
    if not fields:
        try:
            fields = dict(provider.get_fields() or {})
        except NotImplementedError:
            fields = {}

    result: dict[str, Any] = {
        'provider_type': pdef.get('type'),
        'provider_class': f'{type(provider).__module__}.{type(provider).__qualname__}',
        'fields': _jsonable(fields),
        'fields_cached': fields_cached,
        'timings': timings,
    }

    kind = pdef.get('type')
    if kind == 'edr':
        return _probe_edr(provider, result, limit, timings)
    if kind == 'coverage':
        return _probe_coverage(provider, result, timings)
    if kind == 'map':
        return _probe_map(provider, pdef, body, result, timings)
    if kind == 'stac':
        return _probe_stac(provider, rid, result, timings)
    if kind == 'tile':
        return _probe_tile(
            provider, lambda: load_plugin('provider', pdef), rid, body, result, timings
        )
    if kind not in ('feature', 'record'):
        result['note'] = f'{kind} providers are loaded, but not queried, by the probe.'
        return result

    t0 = time.perf_counter()
    collection = provider.query(**_api_query_kwargs(limit=limit))
    timings['query_ms'] = _ms(t0)
    features = list(collection.get('features', []))[:limit]
    result['sample'] = {
        'type': 'FeatureCollection',
        'features': features,
        'numberMatched': collection.get('numberMatched'),
        'numberReturned': collection.get('numberReturned'),
    }
    result['sample_stats'] = _property_stats(features)

    # Extents: from the whole dataset when it's small, otherwise from the sample.
    extent_limit = int(job.get('extent_limit', 5000))
    matched = collection.get('numberMatched')
    extent_features, extent_from = features, 'sample'
    if isinstance(matched, int) and len(features) < matched <= extent_limit:
        t0 = time.perf_counter()
        extent_features = provider.query(**_api_query_kwargs(limit=matched))['features']
        timings['extent_ms'] = _ms(t0)
        extent_from = 'full'
    elif isinstance(matched, int) and matched <= len(features):
        extent_from = 'full'
    result['extent'] = {
        'from': extent_from,
        'bbox': _bbox(extent_features),
        'temporal': _time_range(extent_features, pdef.get('time_field')),
    }
    result['id_stats'] = _id_stats(extent_features)
    return result


def _probe_edr(
    provider: Any, result: dict[str, Any], limit: int, timings: dict[str, float]
) -> dict[str, Any]:
    """An EDR provider's query types, and its locations as the sample (when it lists them)."""
    from .checks import EDR_KWARGS

    types = list(provider.get_query_types() or [])
    result['query_types'] = types
    result['parameters'] = sorted((provider.get_fields() or {}).keys())
    if 'locations' not in types:
        result['note'] = (
            'This EDR provider has no locations query, so there is no sample of items.'
        )
        result['extent'] = _grid_extent(getattr(provider, '_coverage_properties', None) or {})
        _edr_sample(provider, result, None, timings)
        return result
    t0 = time.perf_counter()
    fc = provider.query(**{**EDR_KWARGS, 'query_type': 'locations', 'limit': max(limit, 50)})
    timings['query_ms'] = _ms(t0)
    features = list(fc.get('features', [])) if isinstance(fc, dict) else []
    result['sample'] = {
        'type': 'FeatureCollection',
        'features': features[:limit],
        'numberMatched': len(features),
        'numberReturned': min(len(features), limit),
    }
    result['sample_stats'] = _property_stats(features[:limit])
    result['extent'] = {'from': 'locations', 'bbox': _bbox(features), 'temporal': None}
    result['id_stats'] = _id_stats(features)
    located = next(
        (f for f in features if f.get('geometry') and f.get('id') not in (None, '')), None
    )
    _edr_sample(provider, result, located, timings)
    return result


#: Values of the sample EDR query's first parameter returned for the preview.
EDR_SERIES = 50


def _edr_sample(
    provider: Any,
    result: dict[str, Any],
    location: dict[str, Any] | None,
    timings: dict[str, float],
) -> None:
    """One EDR query as the API makes it: the first location, else a position at the grid's centre.

    It goes in ``result['edr']``: the query, the CoverageJSON's axes and
    parameters, its problems, and the first values of its first parameter.
    """
    from shapely.geometry import Point

    from . import coveragejson
    from .checks import EDR_KWARGS

    types = result['query_types']
    query: dict[str, Any]
    shown: dict[str, Any]
    if location is not None and 'locations' in types:
        query = {'query_type': 'locations', 'location_id': str(location['id'])}
        shown = dict(query)
    else:
        props = getattr(provider, '_coverage_properties', None) or {}
        bbox = props.get('bbox')
        if 'position' not in types or not isinstance(bbox, list | tuple) or len(bbox) != 4:
            return
        x = (float(bbox[0]) + float(bbox[2])) / 2
        y = (float(bbox[1]) + float(bbox[3])) / 2
        query = {'query_type': 'position', 'wkt': Point(x, y)}
        shown = {'query_type': 'position', 'coords': f'POINT({x:g} {y:g})'}
    from pygeoapi.provider.base import ProviderNoDataError

    t0 = time.perf_counter()
    try:
        doc = provider.query(**{**EDR_KWARGS, **query})
    except ProviderNoDataError:
        result['edr'] = {
            'query': shown,
            'problems': ['No data for this query: pygeoapi answers HTTP 204.'],
        }
        return
    except Exception as err:  # noqa: BLE001 - the provider loaded; report the query's failure
        result['edr'] = {
            'query': shown,
            'problems': [f'The query failed: {type(err).__name__}: {err}'],
        }
        return
    finally:
        timings['sample_query_ms'] = _ms(t0)
    sample: dict[str, Any] = {
        'query': shown,
        'problems': coveragejson.problems(doc),
        'domain': _coverage_summary(doc),
        'series': _series(doc),
    }
    result['edr'] = _jsonable(sample)


def _series(doc: Any) -> dict[str, Any] | None:
    """The first parameter's values along the time axis, when there is one."""
    if not isinstance(doc, dict):
        return None
    coverage = doc
    if doc.get('type') == 'CoverageCollection':
        coverage = (doc.get('coverages') or [{}])[0] or {}
    ranges = coverage.get('ranges') or {}
    key = next(iter(ranges), None)
    if key is None or not isinstance(ranges[key], dict):
        return None
    values = ranges[key].get('values')
    times = ((coverage.get('domain') or {}).get('axes') or {}).get('t', {}).get('values')
    if not isinstance(values, list):
        return None
    pairs = (
        list(zip(times, values, strict=False))
        if isinstance(times, list) and len(times) == len(values)
        else None
    )
    return {
        'parameter': key,
        'count': len(values),
        'values': values[:EDR_SERIES],
        'times': [t for t, _ in pairs[:EDR_SERIES]] if pairs else None,
    }


#: Cells along each axis of the probe's coverage window.
COVERAGE_WINDOW = 200
#: The maps API's default image size (``pygeoapi/api/maps.py:131-132``).
MAP_SIZE = (500, 300)
CRS84 = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
#: The server URL the probe passes to tile metadata, which only uses it in links.
PROBE_SERVER = 'http://localhost:5000'
#: Tiles fetched for the preview: the first three zooms over the extent, within these limits.
TILE_ZOOMS = 3
MAX_PREVIEW_TILES = 24
MAX_PREVIEW_TILE_BYTES = 3_000_000


def _probe_coverage(
    provider: Any, result: dict[str, Any], timings: dict[str, float]
) -> dict[str, Any]:
    """A coverage's grid, and a window of its first band as CoverageJSON and a PNG.

    The window is a ``subset`` on the provider's own x and y axes, in the data's
    own coordinates, plus its first time step, as ``/coverage?subset=`` asks
    (``pygeoapi/api/coverages.py:165-187``).
    """
    from . import coveragejson, previews

    props = dict(getattr(provider, '_coverage_properties', None) or {})
    axes = [str(a) for a in getattr(provider, 'axes', None) or []]
    info: dict[str, Any] = {
        'axes': axes,
        'crs': props.get('bbox_crs'),
        'bbox': props.get('bbox'),
        'width': props.get('width'),
        'height': props.get('height'),
        'resx': props.get('resx'),
        'resy': props.get('resy'),
        'time_range': props.get('time_range'),
        'restime': props.get('restime'),
        # What /collections/{id} reads for the grid (pygeoapi/api/collection.py:313-318).
        'metadata_missing': [k for k in ('width', 'height', 'resx', 'resy') if k not in props],
    }
    fields = list(result['fields'])
    query = {
        'bbox': [],
        'datetime_': None,
        'format_': 'json',
        'properties': fields[:1],
        'subsets': _coverage_window(props, axes),
    }
    t0 = time.perf_counter()
    doc = provider.query(**query)
    timings['query_ms'] = _ms(t0)
    info['query'] = query
    info['problems'] = coveragejson.problems(doc)
    info['domain'] = _coverage_summary(doc)
    result['coverage'] = _jsonable(info)
    result['preview'] = previews.coverage_image(doc, fields[0] if fields else None)
    result['extent'] = _grid_extent(props)
    return result


GEOGRAPHIC = {
    CRS84,
    'http://www.opengis.net/def/crs/EPSG/0/4326',
    'https://www.opengis.net/def/crs/EPSG/0/4326',
}


def _grid_extent(props: dict[str, Any]) -> dict[str, Any] | None:
    """A grid's extent for the collection, when its CRS is longitude/latitude."""
    bbox, crs = props.get('bbox'), props.get('bbox_crs')
    if crs not in GEOGRAPHIC or not isinstance(bbox, list | tuple) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
    if not (box[0] >= -181 and box[2] <= 181 and box[1] >= -91 and box[3] <= 91):
        return None  # 0-360 longitudes, or not degrees: leave the extent to the user
    # Cell edges can reach just past the poles and the antimeridian.
    box = [max(box[0], -180.0), max(box[1], -90.0), min(box[2], 180.0), min(box[3], 90.0)]
    time_range = props.get('time_range')
    temporal = [str(t) for t in time_range] if isinstance(time_range, list | tuple) else None
    return {'from': 'full', 'bbox': box, 'temporal': temporal}


def _coverage_window(props: dict[str, Any], axes: list[str]) -> dict[str, list[Any]]:
    """At most COVERAGE_WINDOW cells around the centre, and the first time step."""
    subsets: dict[str, list[Any]] = {}
    x, y = props.get('x_axis_label'), props.get('y_axis_label')
    bbox, width, height = props.get('bbox'), props.get('width'), props.get('height')
    if x in axes and y in axes and isinstance(bbox, list | tuple) and len(bbox) == 4:
        try:
            minx, miny, maxx, maxy = (float(v) for v in bbox)
            fx = min(1.0, COVERAGE_WINDOW / float(width or COVERAGE_WINDOW))
            fy = min(1.0, COVERAGE_WINDOW / float(height or COVERAGE_WINDOW))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        else:
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            hx, hy = (maxx - minx) * fx / 2, (maxy - miny) * fy / 2
            subsets[str(x)] = [cx - hx, cx + hx]
            subsets[str(y)] = [cy - hy, cy + hy]
    t, time_range = props.get('time_axis_label'), props.get('time_range')
    if t and t in axes and isinstance(time_range, list | tuple) and time_range:
        subsets[str(t)] = [str(time_range[0]), str(time_range[0])]
    return subsets


def _coverage_summary(doc: Any) -> dict[str, Any] | None:
    """A CoverageJSON document's type, axes (first, last, count) and parameters."""
    if not isinstance(doc, dict):
        return None
    coverage = doc
    if doc.get('type') == 'CoverageCollection':
        coverage = (doc.get('coverages') or [{}])[0] or {}
    domain = coverage.get('domain') or {}
    axes: dict[str, Any] = {}
    for name, axis in (domain.get('axes') or {}).items():
        if not isinstance(axis, dict):
            continue
        values = axis.get('values')
        if isinstance(values, list):
            axes[name] = {
                'count': len(values),
                'first': values[0] if values else None,
                'last': values[-1] if values else None,
            }
        else:
            axes[name] = {
                'count': axis.get('num'),
                'first': axis.get('start'),
                'last': axis.get('stop'),
            }
    parameters = {}
    for key, p in (
        {**(doc.get('parameters') or {}), **(coverage.get('parameters') or {})}
    ).items():
        if not isinstance(p, dict):
            continue
        prop = p.get('observedProperty') or {}
        unit = p.get('unit') or {}
        symbol = unit.get('symbol')
        parameters[key] = {
            'label': _label(prop.get('label')) or _label(p.get('description')),
            'unit': (symbol.get('value') if isinstance(symbol, dict) else symbol)
            or _label(unit.get('label')),
        }
    return {
        'type': doc.get('type'),
        'domainType': domain.get('domainType'),
        'coverages': len(doc.get('coverages') or [])
        if doc.get('type') == 'CoverageCollection'
        else 1,
        'axes': axes,
        'parameters': parameters,
        'ranges': sorted((coverage.get('ranges') or {}).keys()),
    }


def _label(value: Any) -> str | None:
    """An i18n string ``{"en": ...}``, or a plain one."""
    if isinstance(value, dict):
        value = value.get('en') or next(iter(value.values()), None)
    return str(value) if value not in (None, '') else None


def _probe_map(
    provider: Any,
    pdef: dict[str, Any],
    body: dict[str, Any],
    result: dict[str, Any],
    timings: dict[str, float],
) -> dict[str, Any]:
    """The map of the collection's extent, asked for as ``/map?f=<format>`` does.

    The keywords are the API's (``pygeoapi/api/maps.py:95-255``): the storage
    CRS, the bbox transformed into it, 500×300 pixels, transparent.
    """
    from pygeoapi.crs import transform_bbox

    from . import previews

    fmt = pdef.get('format') or {}
    crs = pdef.get('storage_crs', CRS84)
    bbox = _extent_bbox(body)
    query_bbox = bbox if crs == CRS84 else transform_bbox(bbox, CRS84, crs, always_xy=True)
    width, height = MAP_SIZE
    query = {
        'format_': fmt.get('name', 'png'),
        'style': None,
        'crs': crs,
        'bbox-crs': CRS84,
        'transparent': True,
        'width': width,
        'height': height,
        'bbox': [float(v) for v in query_bbox],
        'datetime_': None,
    }
    t0 = time.perf_counter()
    data = provider.query(**query)
    timings['query_ms'] = _ms(t0)
    problems: list[str] = []
    info: dict[str, Any] = {'query': query, 'bytes': None, 'problems': problems}
    result['map'] = info
    if not isinstance(data, bytes | bytearray):
        problems.append(f'query() returned {type(data).__name__}, not image bytes.')
        return result
    data = bytes(data)
    info['bytes'] = len(data)
    found = previews.image_mimetype(data)
    if found is None:
        problems.append(f'query() did not return an image: {data[:80]!r}')
        return result
    if fmt.get('mimetype') and found != fmt['mimetype']:
        problems.append(f'The image is {found}, but format.mimetype says {fmt["mimetype"]}.')
    size = previews.image_size(data)
    if size and size != MAP_SIZE:
        problems.append(f'Asked for {width}×{height} pixels, got {size[0]}×{size[1]}.')
    result['preview'] = {
        'mimetype': found,
        'data': previews.b64(data),
        'width': size[0] if size else width,
        'height': size[1] if size else height,
        'bbox': bbox,
    }
    return result


def _extent_bbox(body: dict[str, Any]) -> list[float]:
    """The resource's spatial extent in CRS84 (the first box when there are several)."""
    bbox = ((body.get('extents') or {}).get('spatial') or {}).get('bbox')
    if isinstance(bbox, list) and bbox and isinstance(bbox[0], list):
        bbox = bbox[0]
    if isinstance(bbox, list) and len(bbox) in (4, 6):
        try:
            values = [float(v) for v in bbox]
        except (TypeError, ValueError):
            pass
        else:
            return values if len(values) == 4 else [values[0], values[1], values[3], values[4]]
    return [-180.0, -90.0, 180.0, 90.0]


def _probe_tile(
    provider: Any,
    fresh: Callable[[], Any],
    rid: str,
    body: dict[str, Any],
    result: dict[str, Any],
    timings: dict[str, float],
) -> dict[str, Any]:
    """Tiling schemes, the service and metadata documents, and the first tiles over the extent.

    Calls are the API's (``pygeoapi/api/tiles.py:132-320``): tile indexes are
    strings, the format is the provider's own. Each document comes from a new
    instance, as pygeoapi loads one per request: MVT providers' get_tiles_service()
    replaces the service URL that get_tiles() reads.
    """
    from pygeoapi.provider.tile import ProviderTileNotFoundError

    from . import previews

    schemes = list(provider.get_tiling_schemes() or [])
    names = [str(s.tileMatrixSet) for s in schemes]
    zoom = dict((getattr(provider, 'options', None) or {}).get('zoom') or {})
    layer = provider.get_layer()
    problems: list[str] = []
    info: dict[str, Any] = {
        'tile_type': getattr(provider, 'tile_type', None),
        'format': provider.format_type,
        'mimetype': provider.mimetype,
        'layer': layer,
        'schemes': names,
        'zoom': zoom,
        'problems': problems,
    }
    result['tile'] = info
    if not names:
        problems.append('get_tiling_schemes() is empty, so /tiles lists no tile set.')
        return result

    service = fresh().get_tiles_service(
        baseurl=PROBE_SERVER,
        servicepath=f'{PROBE_SERVER}/collections/{rid}/tiles/{{tileMatrixSetId}}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}?f={provider.format_type}',
    )
    info['service_links'] = len((service or {}).get('links') or [])
    try:
        metadata = fresh().get_metadata(
            dataset=rid,
            server_url=PROBE_SERVER,
            layer=layer,
            tileset=names[0],
            metadata_format='json',
            title=_label(body.get('title')),
            description=_label(body.get('description')),
            language=None,
        )
        info['metadata'] = _jsonable(metadata)
    except Exception as err:  # noqa: BLE001 - reported, the tiles may still work
        problems.append(f'The tile set metadata (/tiles/{names[0]}/metadata) fails: {err}')

    scheme = 'WebMercatorQuad' if 'WebMercatorQuad' in names else names[0]
    info['preview_scheme'] = scheme
    zmin = int(zoom.get('min', 0))
    zmax = int(zoom.get('max', zmin))
    bbox = _extent_bbox(body)
    tiles: dict[str, str | None] = {}
    layers: list[list[dict[str, Any]]] = []
    total = 0
    t0 = time.perf_counter()
    last = zmin
    for z in range(zmin, min(zmax, zmin + TILE_ZOOMS - 1) + 1):
        wanted = (
            list(previews.web_mercator_tiles(bbox, z)) if scheme == 'WebMercatorQuad' else [(0, 0)]
        )
        if len(tiles) + len(wanted) > MAX_PREVIEW_TILES and tiles:
            break
        for x, y in wanted[:MAX_PREVIEW_TILES]:
            try:
                content = provider.get_tiles(
                    layer=layer,
                    tileset=scheme,
                    z=str(z),
                    y=str(y),
                    x=str(x),
                    format_=provider.format_type,
                )
            except ProviderTileNotFoundError:
                content = None
            key = f'{z}/{x}/{y}'
            if not content:  # None (HTTP 204) or no bytes: an empty tile
                tiles[key] = None
                continue
            raw = previews.gunzip(bytes(content))
            total += len(raw)
            if info['tile_type'] != 'raster' and not provider.mimetype.startswith('image'):
                try:
                    layers.append(previews.mvt_layers(raw))
                except (ValueError, IndexError) as err:
                    problems.append(f'Tile {key} is not a valid vector tile: {err}')
                    continue
            tiles[key] = previews.b64(raw)
        last = z
        if total > MAX_PREVIEW_TILE_BYTES:
            break
    timings['query_ms'] = _ms(t0)
    if tiles and all(v is None for v in tiles.values()):
        problems.append(f'Every tile fetched ({", ".join(tiles)}) is empty.')
    result['preview'] = {
        'scheme': scheme,
        'mimetype': provider.mimetype,
        'minzoom': zmin,
        'maxzoom': last,
        'bbox': bbox,
        'tiles': tiles,
        'layers': previews.merge_layers(layers),
    }
    return result


#: Entries of a STAC catalog listed by the probe.
STAC_ENTRIES = 25


def _probe_stac(
    provider: Any, rid: str, result: dict[str, Any], timings: dict[str, float]
) -> dict[str, Any]:
    """The collection's root catalog, as /stac/<id> asks for it (``pygeoapi/api/stac.py:183``)."""
    t0 = time.perf_counter()
    data = provider.get_data_path(f'{PROBE_SERVER}/stac', rid, '')
    timings['query_ms'] = _ms(t0)
    if not isinstance(data, dict):
        result['stac'] = {'type': None, 'links': 0, 'entries': [], 'problems': [
            f'The root of the collection is a {type(data).__name__}, not a catalog.'
        ]}  # fmt: skip
        return result
    links = [link for link in data.get('links') or [] if isinstance(link, dict)]
    result['stac'] = _jsonable(
        {
            'type': data.get('type'),
            'links': len(links),
            'entries': [
                {'rel': link.get('rel'), 'href': link.get('href'), 'title': link.get('title')}
                for link in links[:STAC_ENTRIES]
            ],
            'problems': [] if links else ['The catalog lists nothing: check the file types.'],
        }
    )
    return result


def _jsonable(value: Any) -> Any:
    """Plain JSON: numpy values, tuples, dates and enums become lists, numbers or text."""
    import datetime as dt
    import math
    from enum import Enum

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if hasattr(value, 'tolist'):  # numpy scalars and arrays
        return _jsonable(value.tolist())
    if hasattr(value, 'model_dump'):  # pydantic models (tile metadata)
        return _jsonable(value.model_dump(exclude_none=True))
    return str(value)


def _api_query_kwargs(limit: int) -> dict[str, Any]:
    """The 13 keyword arguments pygeoapi's items API always passes.

    docs/pygeoapi-notes.md §5.1; ``pygeoapi/api/itemtypes.py:557``.
    """
    return {
        'offset': 0,
        'limit': limit,
        'resulttype': 'results',
        'bbox': [],
        'datetime_': None,
        'properties': [],
        'sortby': [],
        'select_properties': [],
        'skip_geometry': False,
        'q': None,
        'language': None,
        'filterq': None,
        'crs_transform_spec': None,
    }


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _property_stats(features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for feature in features:
        for key, value in (feature.get('properties') or {}).items():
            s = stats.setdefault(key, {'nulls': 0, 'values': [], 'types': set()})
            if value in (None, ''):
                s['nulls'] += 1
            else:
                s['types'].add(type(value).__name__)
                if len(s['values']) < 3:
                    s['values'].append(value)
    distinct: dict[str, set[str]] = {}
    for feature in features:
        for key, value in (feature.get('properties') or {}).items():
            if value not in (None, ''):
                distinct.setdefault(key, set()).add(str(value))
    for key, s in stats.items():
        s['types'] = sorted(s['types'])
        s['distinct'] = len(distinct.get(key, ()))
    return stats


def _id_stats(features: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [f.get('id') for f in features]
    present = [str(i) for i in ids if i not in (None, '')]
    return {
        'count': len(ids),
        'nulls': len(ids) - len(present),
        'duplicates': len(present) - len(set(present)),
    }


def _bbox(features: list[dict[str, Any]]) -> list[float] | None:
    from shapely.geometry import shape

    bounds = None
    for feature in features:
        geom = feature.get('geometry')
        if not geom:
            continue
        try:
            minx, miny, maxx, maxy = shape(geom).bounds
        except Exception:  # noqa: BLE001, S112 - malformed geometry: skip it
            continue
        if bounds is None:
            bounds = [minx, miny, maxx, maxy]
        else:
            bounds = [
                min(bounds[0], minx),
                min(bounds[1], miny),
                max(bounds[2], maxx),
                max(bounds[3], maxy),
            ]
    return bounds


def _time_range(features: list[dict[str, Any]], time_field: str | None) -> list[str] | None:
    if not time_field:
        return None
    import datetime as dt

    from dateutil.parser import isoparse

    values = []
    for feature in features:
        raw = (feature.get('properties') or {}).get(time_field)
        if raw in (None, '') or isinstance(raw, bool):
            continue
        try:
            if isinstance(raw, int | float) or (isinstance(raw, str) and raw.isdigit()):
                # Epoch time: ArcGIS services give milliseconds.
                number = float(raw)
                when = dt.datetime.fromtimestamp(
                    number / 1000 if abs(number) > 1e11 else number, dt.UTC
                )
            else:
                when = isoparse(str(raw))
        except (ValueError, OverflowError, OSError):
            continue
        values.append(when if when.tzinfo else when.replace(tzinfo=dt.UTC))
    if not values:
        return None
    return [min(values).isoformat(), max(values).isoformat()]


# ---------------------------------------------------------------------- introspect


def introspect(job: dict[str, Any]) -> dict[str, Any]:
    """Cheap metadata (columns, tables) to fill pickers before a provider loads."""
    what = job['what']
    data = remap_paths(_load_yaml(job['values_yaml']), job.get('path_map', []))
    if what == 'csv':
        return _introspect_csv(data)
    if what == 'geojson':
        return _introspect_geojson(data)
    if what == 'sqlite':
        return _introspect_sqlite(data)
    if what == 'sql':
        return _introspect_sql(data, job.get('dialect', 'postgresql'))
    if what == 'ogr':
        return _introspect_ogr(data)
    if what == 'esri':
        return _introspect_esri(data)
    raise ValueError(f'unknown introspection kind: {what}')


#: OGR field types as pygeoapi's OGR provider names them (pygeoapi/provider/ogr.py:243-261).
OGR_TYPES = {'integer64': 'integer', 'real': 'number'}


def _introspect_ogr(pdef: dict[str, Any]) -> dict[str, Any]:
    """The layers of a GDAL/OGR source, opened as pygeoapi's OGR provider opens it.

    Config and driver options are set first, open options passed to OpenEx
    (pygeoapi/provider/ogr.py:128-137, 177-196). Each layer lists its attribute
    fields, with the FID column marked ``pk`` (first, when it isn't an attribute).
    """
    from osgeo import gdal

    gdal.UseExceptions()
    data = dict(pdef.get('data') or {})
    for key, value in {
        **(data.get('gdal_ogr_options') or {}),
        **(data.get('source_options') or {}),
    }.items():
        gdal.SetConfigOption(str(key), str(value))
    driver = data.get('source_type')
    source = data.get('source')
    if not source:
        raise ValueError('data.source is empty')
    if isinstance(source, str) and source.startswith('/') and not os.path.exists(source):
        raise FileNotFoundError(f"No such file or directory: '{source}'")
    kwargs: dict[str, Any] = {}
    if driver:
        kwargs['allowed_drivers'] = [str(driver)]
    if data.get('open_options'):
        kwargs['open_options'] = [f'{k}={v}' for k, v in data['open_options'].items()]
    ds = gdal.OpenEx(str(source), gdal.OF_VECTOR | gdal.OF_READONLY, **kwargs)
    if ds is None:
        raise ValueError(f'GDAL could not open {source} as {driver or "a vector source"}')
    tables = []
    for i in range(min(ds.GetLayerCount(), MAX_TABLES)):
        layer = ds.GetLayerByIndex(i)
        defn = layer.GetLayerDefn()
        columns: list[dict[str, Any]] = []
        for j in range(defn.GetFieldCount()):
            field = defn.GetFieldDefn(j)
            kind = field.GetFieldTypeName(field.GetType()).lower()
            columns.append({'name': field.GetName(), 'type': OGR_TYPES.get(kind, kind)})
        fid = layer.GetFIDColumn() or 'fid'  # pygeoapi falls back to the FID (ogr.py:514-516)
        same = next((c for c in columns if c['name'] == fid), None)
        if same is not None:
            same['pk'] = True
        else:
            columns.insert(0, {'name': fid, 'type': 'integer', 'pk': True})
        geom = defn.GetGeomFieldDefn(0) if defn.GetGeomFieldCount() else None
        tables.append(
            {
                'name': layer.GetName(),
                'kind': gdal.ogr.GeometryTypeToName(geom.GetType()) if geom else 'no geometry',
                'columns': columns,
                'count': layer.GetFeatureCount(force=0),
            }
        )
    return {'tables': tables, 'driver': ds.GetDriver().ShortName}


#: ArcGIS field types and the JSON types they hold.
ESRI_TYPES = {
    'esriFieldTypeOID': 'integer',
    'esriFieldTypeInteger': 'integer',
    'esriFieldTypeSmallInteger': 'integer',
    'esriFieldTypeBigInteger': 'integer',
    'esriFieldTypeDouble': 'number',
    'esriFieldTypeSingle': 'number',
    'esriFieldTypeString': 'string',
    'esriFieldTypeDate': 'integer',  # epoch milliseconds in GeoJSON
    'esriFieldTypeGUID': 'string',
    'esriFieldTypeGlobalID': 'string',
}


def _introspect_esri(pdef: dict[str, Any]) -> dict[str, Any]:
    """An ArcGIS layer's fields, its object id field and its extent (``<layer>?f=pjson``).

    This is the request pygeoapi's ESRI provider makes to list fields
    (pygeoapi/provider/esri.py:90). A layer behind a login is looked up after the
    provider loads, by the probe.
    """
    import requests

    url = pdef.get('data')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        raise ValueError('data must be the layer URL')
    resp = requests.get(url, params={'f': 'pjson'}, timeout=30)
    resp.raise_for_status()
    doc = resp.json()
    if isinstance(doc.get('error'), dict):
        raise ValueError(f'The service answered: {doc["error"].get("message")}')
    oid = doc.get('objectIdField')
    columns = [
        {
            'name': f.get('name'),
            'type': ESRI_TYPES.get(f.get('type'), 'string'),
            'pk': f.get('name') == oid or f.get('type') == 'esriFieldTypeOID',
            'title': f.get('alias'),
        }
        for f in doc.get('fields') or []
        if f.get('name')
    ]
    advanced = doc.get('advancedQueryCapabilities') or {}
    problems = []
    if not advanced.get('supportsPagination') or not advanced.get('supportsOrderBy'):
        problems.append('The layer must support pagination and orderBy, or pygeoapi refuses it.')
    if 'geojson' not in str(doc.get('supportedQueryFormats', '')).lower():
        problems.append('The layer must answer queries as geoJSON, or pygeoapi refuses it.')
    extent = doc.get('extent') or {}
    wkid = (extent.get('spatialReference') or {}).get('latestWkid') or (
        extent.get('spatialReference') or {}
    ).get('wkid')
    bbox = None
    if wkid == 4326 and all(k in extent for k in ('xmin', 'ymin', 'xmax', 'ymax')):
        bbox = [extent['xmin'], extent['ymin'], extent['xmax'], extent['ymax']]
    return {
        'columns': columns,
        'object_id_field': oid,
        'geometry_type': doc.get('geometryType'),
        'max_record_count': doc.get('maxRecordCount'),
        'bbox': bbox,
        'problems': problems,
    }


def _infer_type(values: list[str]) -> str:
    kinds = set()
    for v in values:
        if v == '':
            continue
        try:
            int(v)
            kinds.add('integer')
            continue
        except ValueError:
            pass
        try:
            float(v)
            kinds.add('number')
            continue
        except ValueError:
            kinds.add('string')
    if not kinds:
        return 'string'
    if kinds <= {'integer'}:
        return 'integer'
    if kinds <= {'integer', 'number'}:
        return 'number'
    return 'string'


def _introspect_csv(data: dict[str, Any]) -> dict[str, Any]:
    path = Path(data['data'])
    with path.open(newline='', encoding='utf-8-sig') as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [row for _, row in zip(range(SAMPLE_ROWS), reader, strict=False)]
    columns = []
    for i, name in enumerate(header):
        values = [row[i] for row in rows if i < len(row)]
        columns.append({'name': name, 'type': _infer_type(values), 'sample': values[:3]})
    return {'columns': columns}


def _introspect_geojson(data: dict[str, Any]) -> dict[str, Any]:
    path = Path(data['data'])
    with path.open(encoding='utf-8') as fh:
        doc = json.load(fh)
    features = doc.get('features', []) if isinstance(doc, dict) else []
    columns: dict[str, dict[str, Any]] = {}
    has_id = False
    for feature in features[:200]:
        has_id = has_id or 'id' in feature
        for key, value in (feature.get('properties') or {}).items():
            col = columns.setdefault(key, {'name': key, 'types': set(), 'sample': []})
            if value is not None:
                col['types'].add(type(value).__name__)
                if len(col['sample']) < 3:
                    col['sample'].append(value)
    out = []
    for col in columns.values():
        types = col.pop('types')
        col['type'] = (
            'integer'
            if types == {'int'}
            else 'number'
            if types <= {'int', 'float'} and types
            else 'string'
        )
        out.append(col)
    return {'columns': out, 'features_have_id': has_id, 'count': len(features)}


def _introspect_sqlite(data: dict[str, Any]) -> dict[str, Any]:
    path = Path(data['data'])
    if not path.exists():
        raise FileNotFoundError(f"No such file or directory: '{path}'")
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        if 'gpkg_contents' in names:
            tables = [
                r[0]
                for r in conn.execute(
                    "select table_name from gpkg_contents where data_type='features'"
                )
            ]
        elif 'geometry_columns' in names:
            tables = [r[0] for r in conn.execute('select f_table_name from geometry_columns')]
        else:
            tables = sorted(names)
        out = []
        for table in tables[:MAX_TABLES]:
            quoted = '"' + table.replace('"', '""') + '"'
            cols = conn.execute(f'PRAGMA table_info({quoted})').fetchall()  # noqa: S608
            out.append(
                {
                    'name': table,
                    'columns': [
                        {'name': c[1], 'type': c[2] or '', 'pk': bool(c[5])} for c in cols
                    ],
                }
            )
    finally:
        conn.close()
    return {'tables': out}


POOL_KEYS = {'pool_size', 'max_overflow', 'pool_recycle', 'pool_timeout', 'pool_pre_ping'}
#: PostGIS's own bookkeeping tables and views, never worth publishing.
POSTGIS_SYSTEM = {
    'geography_columns',
    'geometry_columns',
    'spatial_ref_sys',
    'raster_columns',
    'raster_overviews',
    'topology',
    'layer',
}


def _introspect_sql(pdef: dict[str, Any], dialect: str) -> dict[str, Any]:
    """Tables, views and columns, connecting the way pygeoapi's SQL provider does.

    Mirrors ``pygeoapi/provider/sql.py`` (``store_db_parameters`` and
    ``get_engine``): ``data`` is a DSN string or a dict; ``options`` other than
    the pool keys and ``search_path`` become DBAPI ``connect_args``.
    """
    import geoalchemy2  # noqa: F401  (registers PostGIS column types for reflection)
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import URL

    data = pdef['data']
    options = dict(pdef.get('options') or {})
    search_path = None
    if isinstance(data, dict):
        search_path = data.get('search_path')
    search_path = list(search_path or options.pop('search_path', None) or ['public'])
    connect_args = {
        k: v for k, v in options.items() if k not in POOL_KEYS and not isinstance(v, dict)
    }
    drivers = {'postgresql': ('postgresql+psycopg2', 5432), 'mysql': ('mysql+pymysql', 3306)}
    driver, default_port = drivers[dialect]
    if isinstance(data, str):
        url: str | URL = data
    else:
        url = URL.create(
            drivername=driver,
            username=data.get('user'),
            password=data.get('password'),
            host=data.get('host'),
            port=int(data.get('port', default_port)),
            database=data.get('dbname') or data.get('database'),
        )
    if dialect == 'postgresql':
        connect_args.setdefault('connect_timeout', 5)
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    try:
        insp = inspect(engine)
        tables = []
        for schema in search_path:
            names = [(n, 'table') for n in insp.get_table_names(schema=schema)]
            names += [(n, 'view') for n in insp.get_view_names(schema=schema)]
            if hasattr(insp, 'get_materialized_view_names') and dialect == 'postgresql':
                names += [
                    (n, 'materialized view')
                    for n in insp.get_materialized_view_names(schema=schema)
                ]
            names = [(n, k) for n, k in names if n not in POSTGIS_SYSTEM]
            for name, kind in sorted(names)[:MAX_TABLES]:
                cols = insp.get_columns(name, schema=schema)
                pk = (insp.get_pk_constraint(name, schema=schema) or {}).get(
                    'constrained_columns'
                ) or []
                tables.append(
                    {
                        'schema': schema,
                        'name': name,
                        'kind': kind,
                        'columns': [
                            {
                                'name': c['name'],
                                'type': str(c['type']),
                                'geometry': str(c['type'])
                                .upper()
                                .startswith(('GEOMETRY', 'GEOGRAPHY')),
                                'pk': c['name'] in pk,
                            }
                            for c in cols
                        ],
                    }
                )
        return {'tables': tables, 'search_path': search_path}
    finally:
        engine.dispose()


# ------------------------------------------------------------------ openapi, env


def openapi(job: dict[str, Any]) -> dict[str, Any]:
    """``get_oas`` on a whole config with ``fail_on_invalid_collection=True``.

    This is what ``pygeoapi openapi generate`` does (docs/pygeoapi-notes.md
    §6.2); it instantiates feature/record providers to read their fields.
    """
    from pygeoapi.openapi import get_oas

    cfg = remap_paths(_load_yaml(job['config_yaml']), job.get('path_map', []))
    t0 = time.perf_counter()
    oas = get_oas(cfg, fail_on_invalid_collection=True)
    out: dict[str, Any] = {'paths': sorted(oas.get('paths', {})), 'ms': _ms(t0)}
    if job.get('document'):
        import yaml

        # As ``pygeoapi openapi generate`` writes it (pygeoapi/openapi.py:1052).
        out['document'] = yaml.safe_dump(oas, default_flow_style=False)
    return out


def availability(job: dict[str, Any]) -> dict[str, Any]:
    """Which modules can be imported, without importing them.

    A dotted name (azure.storage.blob) is looked up whole once its top-level
    package exists, since a namespace package such as azure can be there alone.
    """
    found = {}
    for name in job.get('modules', []):
        top = name.split('.')[0]
        try:
            found[name] = importlib.util.find_spec(top) is not None and (
                name == top or importlib.util.find_spec(name) is not None
            )
        except (ImportError, ValueError):
            found[name] = False
    return {'modules': found}


def version(job: dict[str, Any]) -> dict[str, Any]:
    import pygeoapi

    return {'pygeoapi': pygeoapi.__version__, 'python': platform.python_version()}


# ------------------------------------------------------------------ harness, sample


def harness(job: dict[str, Any]) -> dict[str, Any]:
    """The contract checks (PLAN.md §7.7) on the job's one resource."""
    from .checks import run_contract

    rid, body = _resource(job)
    levels = tuple(job.get('levels') or ('L1', 'L2', 'L3'))
    t0 = time.perf_counter()
    checklist = run_contract(
        rid, body, int(job.get('provider_index', 0)), job.get('capabilities'), levels
    )
    return {
        'checks': checklist.as_dicts(),
        'summary': checklist.summary(),
        'ok': checklist.ok(),
        'ms': _ms(t0),
    }


SAMPLE_HEADERS = ('content-type', 'link', 'x-total-count', 'x-total', 'content-length')


def sample(job: dict[str, Any]) -> dict[str, Any]:
    """One response from an HTTP source, for the template wizard (PLAN.md §7.4).

    The source block goes through the spec engine's own client, so the egress
    rules, auth and size cap are the ones the provider will use.
    """
    from pygeoapi_spec_providers.client import SourceClient, describe
    from pygeoapi_spec_providers.spec import normalize_source, validate_source

    source = _load_yaml(_materialize(job, 'source_yaml'))
    problems = validate_source(source)
    if problems:
        raise ValueError('; '.join(f'{p.pointer}: {p.message}' for p in problems))
    source = normalize_source(source)
    source['max_response_bytes'] = min(
        int(source['max_response_bytes']), int(job.get('max_bytes', 5_000_000))
    )
    client = SourceClient(source, [str(httpx_host(source['url']))])
    t0 = time.perf_counter()
    response = client.get(source['url'], job.get('params') or {})
    result: dict[str, Any] = {
        'status': response.status,
        'url': describe(response.url),
        'headers': {k: v for k, v in response.headers.items() if k.lower() in SAMPLE_HEADERS},
        'bytes': len(response.body),
        'ms': _ms(t0),
    }
    try:
        result['json'] = json.loads(response.body)
    except ValueError:
        result['text'] = response.body[:2000].decode('utf-8', errors='replace')
    return result


SQL_SAMPLE_ROWS = 50


def sql_sample(job: dict[str, Any]) -> dict[str, Any]:
    """The first rows of a SQL query, profiled, for the template wizard (PLAN.md §7.4).

    It runs through the spec engine's own guard and read-only connection, so the
    query is checked and executed exactly as the provider will run it.
    """
    from pygeoapi_spec_providers.sql import sample_rows

    source = remap_paths(_load_yaml(_materialize(job, 'source_yaml')), job.get('path_map', []))
    t0 = time.perf_counter()
    out = sample_rows(
        source,
        str(job['query']),
        int(job.get('limit', SQL_SAMPLE_ROWS)),
        params=job.get('params') or None,
    )
    names, found, db_types = out['columns'], out['rows'], out['types']
    columns = []
    for i, name in enumerate(names):
        values = [row[i] for row in found]
        present = [v for v in values if v is not None and v != '']
        kinds = sorted({type(v).__name__ for v in present})
        distinct = len({repr(v) for v in present})
        columns.append(
            {
                'name': name,
                'kinds': kinds,
                'db_type': db_types.get(name) or None,
                'geometry': _geometry_encoding(present, db_types.get(name)),
                'nulls': len(values) - len(present),
                'distinct': distinct,
                'unique': bool(present) and distinct == len(values) == len(present),
                'examples': [_cell(v) for v in present[:3]],
            }
        )
    encodings = [c['geometry'] for c in columns]

    def shown(row: tuple[Any, ...]) -> list[Any]:
        pairs = zip(row, encodings, strict=True)
        return [_geometry_summary(v, e) if e else _cell(v) for v, e in pairs]

    return {
        'columns': columns,
        'rows': [shown(row) for row in found[:20]],
        'row_count': len(found),
        'query': out['query'],
        'ms': _ms(t0),
    }


def _cell(value: Any) -> Any:
    from pygeoapi_spec_providers.sql import to_jsonable

    out = to_jsonable(value)
    if isinstance(out, dict) and 'hex' in out:
        return {'bytes': out['bytes'], 'hex': str(out['hex'])[:64]}
    if isinstance(out, str) and len(out) > 300:
        return out[:300] + '…'
    return out


def _geometry_summary(value: Any, encoding: str) -> Any:
    """'MultiLineString, 85 points' rather than kilobytes of hex in the sample table."""
    from pygeoapi_spec_providers.db import decode_geometry
    from shapely import get_num_coordinates
    from shapely.geometry import shape

    geometry = decode_geometry(value, encoding)
    if geometry is None:
        return None if value is None else _cell(value)
    count = int(get_num_coordinates(shape(geometry)))
    return f'{geometry["type"]}, {count} point{"s" if count != 1 else ""}'


def _geometry_encoding(values: list[Any], db_type: str | None) -> str | None:
    """native, gpkg, wkb, wkt or geojson when the sampled values read as geometries."""
    from pygeoapi_spec_providers.db import decode_geometry

    if db_type in ('geometry', 'geography'):
        return 'native'
    sample = values[:5]
    if not sample:
        return None
    for encoding in ('gpkg', 'wkb', 'wkt', 'geojson'):
        if encoding in ('gpkg', 'wkb') and not all(
            isinstance(v, bytes | bytearray | memoryview) for v in sample
        ):
            continue
        if encoding in ('wkt', 'geojson') and not all(isinstance(v, str | dict) for v in sample):
            continue
        if encoding == 'wkt' and not all(
            isinstance(v, str) and v.lstrip()[:1].isalpha() and '(' in v for v in sample
        ):
            continue
        if all(decode_geometry(v, encoding) is not None for v in sample):
            return encoding
    return None


def httpx_host(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or '').lower()


JOBS: dict[str, JobFn] = {
    'probe': probe,
    'harness': harness,
    'sample': sample,
    'sql_sample': sql_sample,
    'introspect': introspect,
    'openapi': openapi,
    'availability': availability,
    'version': version,
}
