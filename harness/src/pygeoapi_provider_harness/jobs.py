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
    (``data: @files@/spec.yaml``) or a provider module under test. They're written
    to a fresh temporary folder; ``job['python_path']`` entries (``@files@``) are
    added to ``sys.path``. This runs only inside the sandbox.
    """
    text = str(job[key])
    files = job.get('files') or {}
    if files:
        workdir = job.setdefault('_files_dir', tempfile.mkdtemp(prefix='job-files-'))
        for name, content in files.items():
            if not name or '/' in name or '\\' in name or name.startswith('.'):
                raise ValueError(f'Invalid job file name {name!r}')
            Path(workdir, name).write_text(content, encoding='utf-8')
        text = text.replace(FILES_PLACEHOLDER, workdir)
        # Code that comes with the job (a provider under test) is importable from there.
        for entry in job.get('python_path') or []:
            path = str(entry).replace(FILES_PLACEHOLDER, workdir)
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


def _provider_def(job: dict[str, Any]) -> dict[str, Any]:
    """The provider definition, placeholders resolved, paths remapped."""
    _rid, body = _resource(job)
    return dict(body['providers'][int(job.get('provider_index', 0))])


# --------------------------------------------------------------------------- probe


def probe(job: dict[str, Any]) -> dict[str, Any]:
    """Load a provider with ``load_plugin`` and exercise it like the API does."""
    from pygeoapi.plugin import load_plugin

    pdef = _provider_def(job)
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
        'fields': fields,
        'fields_cached': fields_cached,
        'timings': timings,
    }

    if pdef.get('type') not in ('feature', 'record'):
        result['note'] = 'Only feature and record probes are implemented in Phase 1.'
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
    from dateutil.parser import isoparse

    values = []
    for feature in features:
        raw = (feature.get('properties') or {}).get(time_field)
        if raw in (None, ''):
            continue
        try:
            values.append(isoparse(str(raw)))
        except ValueError:
            continue
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
    raise ValueError(f'unknown introspection kind: {what}')


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
    """Which top-level modules can be imported (without importing them)."""
    found = {}
    for name in job.get('modules', []):
        top = name.split('.')[0]
        try:
            found[name] = importlib.util.find_spec(top) is not None
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


def httpx_host(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or '').lower()


JOBS: dict[str, JobFn] = {
    'probe': probe,
    'harness': harness,
    'sample': sample,
    'introspect': introspect,
    'openapi': openapi,
    'availability': availability,
    'version': version,
}
