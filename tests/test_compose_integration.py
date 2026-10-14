"""Integration: pygeoapi 0.24.0 serves what the builder exports (PLAN.md §12).

Needs the dev stack (``make stack-up``) for PostGIS. Each test starts its own
throwaway pygeoapi container with the exported config, so the dev stack's own
config is never modified.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from dockerutil import IMAGE, NETWORK, REPO
from forms import CASES
from provider_builder.images import STABLE_TAG
from provider_builder.render import FormState
from provider_builder.runner import DockerRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target, Workspace

pytestmark = pytest.mark.compose

DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
BASE = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'
SECRETS = {'PYGEOAPI_WATERWAYS_DB_PASSWORD': 'postgres'}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / 'data').mkdir()
    for name in ('obs.csv', 'ne_110m_lakes.geojson', 'poi_portugal.gpkg'):
        shutil.copy(DATA / name, tmp_path / 'data' / name)
    (tmp_path / 'config').mkdir()
    shutil.copy(BASE, tmp_path / 'config' / 'pygeoapi.config.yml')
    return tmp_path


@pytest.fixture
def service(workdir: Path) -> Service:
    settings = Settings(
        root=workdir,
        data_roots=[DataRoot(name='d', host_path=workdir / 'data', server_path='/data')],
        targets=[
            Target(id='t', title='t', base_config=workdir / 'config' / 'pygeoapi.config.yml')
        ],
        catalogs=[REPO / 'catalog'],
        sandbox=SandboxSettings(
            runner='docker', egress_network=NETWORK, private_hosts=['postgis', 'mockapi']
        ),
        workspace=Workspace(host_path=workdir / 'providers', server_path='/specs'),
    )
    return Service(
        settings, runner=DockerRunner(settings.sandbox, settings.data_roots, settings.workspace)
    )


@pytest.fixture
def serve(workdir: Path) -> Iterator[callable]:  # type: ignore[valid-type]
    names: list[str] = []

    def start(
        config_text: str,
        env: dict[str, str],
        image: str = IMAGE,
        volumes: tuple[str, ...] = (),
        args: tuple[str, ...] = (),
    ) -> str:
        (workdir / 'config' / 'pygeoapi.config.yml').write_text(config_text)
        name = f'pb-it-{uuid.uuid4().hex[:8]}'
        names.append(name)
        cmd = [
            'docker',
            'run',
            '-d',
            '--rm',
            '--name',
            name,
            '--network',
            NETWORK,
            '-p',
            '127.0.0.1::80',
            '-v',
            f'{workdir / "config"}:/config:ro',
            '-v',
            f'{workdir / "data"}:/data',
            '-e',
            'PYGEOAPI_CONFIG=/config/pygeoapi.config.yml',
        ]
        for key, value in env.items():
            cmd += ['-e', f'{key}={value}']
        for volume in volumes:
            cmd += ['-v', volume]
        subprocess.run([*cmd, *args, image], check=True, capture_output=True, timeout=60)
        port = (
            subprocess.run(
                ['docker', 'port', name, '80/tcp'],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            .stdout.split(':')[-1]
            .strip()
        )
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'{base}/collections', timeout=3).status_code == 200:
                    return base
            except httpx.HTTPError:
                pass
            time.sleep(1)
        logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr
        raise AssertionError(f'pygeoapi did not start:\n{logs[-3000:]}')

    yield start
    for name in names:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=60)


def publish(service: Service, name: str) -> tuple[FormState, str]:
    form = FormState.model_validate(CASES[name]())
    exported = service.export(form, 't')
    assert not exported.validation.blocking, [f.message for f in exported.validation.findings]
    return form, exported.merged_yaml


@pytest.mark.parametrize('name', ['csv', 'sqlitegpkg', 'postgresql-fields'])
def test_pygeoapi_serves_the_export(service: Service, serve: callable, name: str) -> None:
    form, merged = publish(service, name)
    base = serve(merged, SECRETS)
    rid = form.resource_id
    collection = httpx.get(f'{base}/collections/{rid}?f=json', timeout=30)
    assert collection.status_code == 200, collection.text
    items = httpx.get(f'{base}/collections/{rid}/items?f=json&limit=2', timeout=30).json()
    assert items['type'] == 'FeatureCollection' and len(items['features']) == 2
    html = httpx.get(f'{base}/collections/{rid}/items?f=html&limit=2', timeout=30)
    assert html.status_code == 200
    jsonld = httpx.get(f'{base}/collections/{rid}/items?f=jsonld&limit=2', timeout=30)
    assert jsonld.status_code == 200 and '@context' in json.loads(jsonld.text)


def test_postgis_probe_and_wrong_password(service: Service) -> None:
    form = FormState.model_validate(CASES['postgresql-fields']())
    ok = service.probe(form, 0, SECRETS)
    assert ok['ok'], ok.get('explanation')
    assert ok['result']['sample']['numberMatched'] == 14776
    bad = service.probe(form, 0, {'PYGEOAPI_WATERWAYS_DB_PASSWORD': 'not-the-password'})
    assert not bad['ok']
    assert bad['explanation']['message'] == (
        'The database rejected the user name or password for user postgres.'
    )
    assert 'not-the-password' not in json.dumps(bad)


def test_postgis_introspection(service: Service) -> None:
    provider = FormState.model_validate(CASES['postgresql-fields']()).providers[0]
    result = service.introspect(provider, 'waterways', SECRETS)
    assert result['ok'], result.get('explanation')
    tables = {(t['schema'], t['name']): t for t in result['result']['tables']}
    assert ('osm', 'hotosm_bdi_drains') in tables  # a materialized view
    waterways = tables[('osm', 'hotosm_bdi_waterways')]
    assert [c['name'] for c in waterways['columns'] if c['geometry']] == ['foo_geom']
    assert ('public', 'geometry_columns') not in tables  # PostGIS bookkeeping


# --- Mode B: created providers -----------------------------------------------------------

MOCK_SPEC = """spec_version: 1
template: http-features
id: mock-wells
title: Mock wells
version: 1.0.0
source:
  url: http://mockapi:8000/v2/wells
records: $.data.items
item_id:
  from: well_id
geometry:
  type: geojson
  field: location
properties:
  - name: well_id
    type: string
  - name: well_name
    type: string
  - name: county
    type: string
    from: county_name
  - name: measured
    type: datetime
    from: lastMeasurementDate
title_field: well_name
time_field: measured
pagination:
  style: offset
  offset_param: offset
  limit_param: limit
  total: $.meta.total
filters:
  bbox:
    param: bbox
    format: '{minx},{miny},{maxx},{maxy}'
"""


def test_the_harness_passes_in_the_sandbox_through_the_gateway(service: Service) -> None:
    out = service.spec_harness(MOCK_SPEC, {}, ['L1', 'L2', 'L3'])
    assert out['ok'], out.get('explanation')
    assert out['egress'] == ['mockapi:8000']
    checks = {c['id']: c['status'] for c in out['result']['checks']}
    assert out['result']['ok'], [c for c in out['result']['checks'] if c['status'] == 'fail']
    assert checks['L3.server'] == 'pass'  # gunicorn ran inside the sandbox
    assert checks['L1.bbox'] == 'pass'


def test_pygeoapi_serves_a_created_provider(
    service: Service, serve: callable, workdir: Path
) -> None:
    saved = service.workspace_save(MOCK_SPEC, None, '', None)
    form = FormState.model_validate(
        {
            'resource_id': 'wells',
            'resource': {
                'title': 'Wells',
                'description': 'Mock wells',
                'keywords': ['wells'],
                'extents': {'spatial': {'bbox': [-118, 41, -110, 49]}},
            },
            'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
        }
    )
    probe = service.probe(form, 0, {})
    assert probe['ok'], probe.get('explanation')
    exported = service.export(form, 't')
    assert not exported.validation.blocking, exported.validation.findings
    # Like the dev stack: the sandbox image runs pygeoapi, with the runtime and specs mounted.
    base = serve(
        exported.merged_yaml,
        {'PYTHONPATH': '/opt/runtime', 'PYGEOAPI_SPEC_PRIVATE_HOSTS': 'mockapi'},
        image=STABLE_TAG,
        volumes=(
            f'{REPO / "runtime" / "src"}:/opt/runtime:ro',
            f'{workdir / "providers"}:/specs:ro',
        ),
        args=('--user', 'root', '--entrypoint', '/entrypoint.sh'),
    )
    items = httpx.get(f'{base}/collections/wells/items?f=json&limit=2', timeout=60).json()
    assert items['numberMatched'] == 120 and len(items['features']) == 2
    nearby = httpx.get(f'{base}/collections/wells/items?f=json&bbox=-117,43,-115,45', timeout=60)
    assert 0 < nearby.json()['numberMatched'] < 120
    html = httpx.get(f'{base}/collections/wells/items?f=html&limit=2', timeout=60)
    assert html.status_code == 200 and '<html' in html.text.lower()


# --- Mode B: a SQL join (Phase 4 exit: "a multi-table join is published and passes the harness")

DEMO_SQL = REPO / 'docker' / 'postgis' / '21-demo-sql.sql'
READER = {'PB_READER_PASSWORD': 'reader-password'}  # docker/postgis/21-demo-sql.sql
JOIN = """SELECT w.osm_id, w.name, w.foo_geom, w.waterway, c.label AS waterway_label,
       c.natural_channel, c.reviewed_at
FROM osm.hotosm_bdi_waterways AS w
JOIN osm.waterway_classes AS c ON c.waterway = w.waterway"""
SQL_SOURCE = {
    'dialect': 'postgresql',
    'connection': {
        'host': 'postgis',
        'port': 5432,
        'dbname': 'test',
        'user': 'pb_reader',
        'password': '${PB_READER_PASSWORD}',
    },
}
SQL_SPEC = f"""spec_version: 1
template: sql-features
id: bdi-waterways
title: Waterways of Burundi, with their classes
version: 1.0.0
source:
  dialect: postgresql
  connection:
    host: postgis
    port: 5432
    dbname: test
    user: pb_reader
    password: ${{PB_READER_PASSWORD}}
query: |
{chr(10).join('  ' + line for line in JOIN.splitlines())}
item_id:
  from: osm_id
  type: integer
geometry:
  encoding: native
  column: foo_geom
  crs: http://www.opengis.net/def/crs/EPSG/0/4326
properties:
  - name: osm_id
    type: integer
  - name: name
    type: string
  - name: waterway
    type: string
  - name: waterway_label
    type: string
  - name: natural_channel
    type: boolean
  - name: reviewed_at
    type: datetime
time_field: reviewed_at
title_field: name
"""


@pytest.fixture(scope='module')
def demo_sql() -> None:
    """The join's second table and the read-only role; idempotent on an existing stack."""
    subprocess.run(
        ['docker', 'exec', '-i', 'provider-builder-dev-postgis-1', 'psql', '-U', 'postgres',
         '-d', 'test', '-v', 'ON_ERROR_STOP=1', '-q'],
        input=DEMO_SQL.read_bytes(), check=True, capture_output=True, timeout=60,
    )  # fmt: skip


def test_sql_sample_suggests_the_join_mapping(service: Service, demo_sql: None) -> None:
    out = service.sample_sql(SQL_SOURCE, JOIN, READER)
    assert out['ok'], out.get('explanation') or out.get('problems')
    assert out['egress'] == ['postgis:5432']
    s = out['suggestions']
    assert s['geometry'] == {'encoding': 'native', 'column': 'foo_geom'}
    by_name = {p['name']: p for p in s['properties']}
    assert by_name['reviewed_at']['type'] == 'datetime' and 'format' not in by_name['reviewed_at']
    assert by_name['natural_channel']['type'] == 'boolean'
    bad = service.sample_sql(SQL_SOURCE, JOIN, {'PB_READER_PASSWORD': 'not-the-password'})
    assert not bad['ok'] and 'not-the-password' not in json.dumps(bad)
    assert bad['error']['user_msg'] == 'The database rejected the credentials.'


def test_the_sql_join_passes_the_harness_in_the_sandbox(service: Service, demo_sql: None) -> None:
    out = service.spec_harness(SQL_SPEC, READER, ['L1', 'L2', 'L3'])
    assert out['ok'], out.get('explanation') or out.get('problems')
    assert out['egress'] == ['postgis:5432']
    assert out['result']['ok'], [c for c in out['result']['checks'] if c['status'] == 'fail']
    checks = {c['id']: c['status'] for c in out['result']['checks']}
    assert checks['L3.server'] == 'pass'
    for capability in ('bbox', 'datetime', 'properties', 'sortby', 'q'):
        assert checks[f'L1.{capability}'] == 'pass', capability


def test_pygeoapi_serves_the_sql_join(
    service: Service, serve: callable, workdir: Path, demo_sql: None
) -> None:
    saved = service.workspace_save(SQL_SPEC, None, '', None)
    form = FormState.model_validate(
        {
            'resource_id': 'waterways-classes',
            'resource': {
                'title': 'Waterways with classes',
                'description': 'A join of two PostGIS tables',
                'keywords': ['waterways'],
                'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
            },
            'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
        }
    )
    probe = service.probe(form, 0, READER)
    assert probe['ok'], probe.get('explanation')
    exported = service.export(form, 't')
    assert not exported.validation.blocking, exported.validation.findings
    assert [e.name for e in exported.env] == ['PB_READER_PASSWORD']
    base = serve(
        exported.merged_yaml,
        {'PYTHONPATH': '/opt/runtime', **READER},
        image=STABLE_TAG,
        volumes=(
            f'{REPO / "runtime" / "src"}:/opt/runtime:ro',
            f'{workdir / "providers"}:/specs:ro',
        ),
        args=('--user', 'root', '--entrypoint', '/entrypoint.sh'),
    )
    url = f'{base}/collections/waterways-classes/items'
    items = httpx.get(f'{url}?f=json&limit=2', timeout=60).json()
    assert items['numberMatched'] == 14768 and len(items['features']) == 2
    assert items['features'][0]['properties']['waterway_label']
    rivers = httpx.get(f'{url}?f=json&waterway=river&limit=1', timeout=60).json()
    assert rivers['numberMatched'] == 436
    march = httpx.get(
        f'{url}?f=json&datetime=2026-03-01T00:00:00Z/2026-03-31T00:00:00Z', timeout=60
    )
    assert march.json()['numberMatched'] == 88 + 157  # canals and drains
    one = httpx.get(f'{url}/{items["features"][0]["id"]}?f=json', timeout=60)
    assert one.status_code == 200


# --- Mode B: time series as EDR (Phase 4 exit: "CO-OPS water levels are served as EDR") ------


def coops_spec_text() -> str:
    sys.path.insert(0, str(REPO / 'runtime' / 'tests'))
    import yaml
    from tsfixtures import coops

    return yaml.safe_dump(coops('http://mockapi:8000'), sort_keys=False)


def test_the_time_series_harness_passes_in_the_sandbox(service: Service) -> None:
    out = service.spec_harness(coops_spec_text(), {}, ['L1', 'L2', 'L3'])
    assert out['ok'], out.get('explanation') or out.get('problems')
    assert out['egress'] == ['mockapi:8000']
    assert out['result']['ok'], [c for c in out['result']['checks'] if c['status'] == 'fail']
    checks = {c['id']: c['status'] for c in out['result']['checks']}
    assert checks['L3.server'] == 'pass'
    assert checks['L3.json:/collections/coops-water-levels/locations/9414290'] == 'pass'
    assert checks['L3.html:/collections/coops-water-levels/locations/9414290'] == 'pass'


def test_pygeoapi_serves_coops_water_levels_as_edr(
    service: Service, serve: callable, workdir: Path
) -> None:
    from pygeoapi_provider_harness import coveragejson

    saved = service.workspace_save(coops_spec_text(), None, '', None)
    form = FormState.model_validate(
        {
            'resource_id': 'coops',
            'resource': {
                'title': 'CO-OPS water levels',
                'description': 'Water levels and air temperature at tide stations',
                'keywords': ['water level', 'tides'],
                'extents': {'spatial': {'bbox': [-160, 20, -70, 50]}},
            },
            'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
        }
    )
    probe = service.probe(form, 0, {})
    assert probe['ok'], probe.get('explanation')
    exported = service.export(form, 't')
    assert not exported.validation.blocking, exported.validation.findings
    base = serve(
        exported.merged_yaml,
        {'PYTHONPATH': '/opt/runtime', 'PYGEOAPI_SPEC_PRIVATE_HOSTS': 'mockapi'},
        image=STABLE_TAG,
        volumes=(
            f'{REPO / "runtime" / "src"}:/opt/runtime:ro',
            f'{workdir / "providers"}:/specs:ro',
        ),
        args=('--user', 'root', '--entrypoint', '/entrypoint.sh'),
    )
    collection = httpx.get(f'{base}/collections/coops?f=json', timeout=60).json()
    assert set(collection['data_queries']) == {'locations', 'position', 'area', 'cube'}
    assert collection['parameter_names']['water_level']['unit']['symbol']['value'] == 'm'
    url = f'{base}/collections/coops'
    stations = httpx.get(f'{url}/locations?f=json', timeout=60).json()
    assert stations['type'] == 'FeatureCollection' and len(stations['features']) == 6
    day = 'datetime=2026-10-01T00:00:00Z/2026-10-02T00:00:00Z'
    one = httpx.get(f'{url}/locations/9414290?f=json&{day}&parameter-name=water_level', timeout=60)
    assert one.status_code == 200, one.text
    assert one.headers['content-type'].startswith('application/vnd.cov+json')
    assert coveragejson.problems(one.json()) == []
    assert one.json()['coverages'][0]['ranges']['water_level']['shape'] == [241]
    for query in (
        'position?coords=POINT(-122.47 37.80)',
        'area?coords=POLYGON((-80 35, -65 35, -65 45, -80 45, -80 35))',
        'cube?bbox=-125,40,-115,50',
    ):
        r = httpx.get(f'{url}/{query}&f=json&{day}', timeout=60)
        assert r.status_code == 200, (query, r.text)
        assert coveragejson.problems(r.json()) == [], query
    html = httpx.get(f'{url}/locations/9414290?f=html&{day}', timeout=60)
    assert html.status_code == 200 and '<html' in html.text.lower()
    far = httpx.get(f'{url}/position?coords=POINT(-100 40)&f=json&{day}', timeout=60)
    assert far.status_code == 204  # no station within the snap radius: no data
    long = httpx.get(
        f'{url}/locations/9414290?f=json&datetime=2026-01-01T00:00:00Z/2026-03-01T00:00:00Z',
        timeout=60,
    )
    assert long.status_code == 400 and 'P31D' in long.text


# --- Mode B: a code-built provider (J5) -----------------------------------------------------


def implemented_code_provider(service: Service) -> str:
    sys.path.insert(0, str(REPO / 'backend' / 'tests'))
    from provider_builder.scaffold import ScaffoldRequest
    from test_codespace import IMPLEMENTED, ITEMS

    service.code_create(
        ScaffoldRequest(
            provider_type='feature',
            id='demo-items',
            title='Demo items',
            style='files',
            class_name='DemoItemsProvider',
        )
    )
    code = service.code
    code.write_file('demo-items', 'tests/fixtures/items.json', json.dumps(ITEMS))
    code.write_file('demo-items', 'src/demo_items_provider/provider.py', IMPLEMENTED)
    snippet = code.read('demo-items', 'examples/pygeoapi-config.snippet.yml').replace(
        '/data/items.json', '/specs/demo-items/tests/fixtures/items.json'
    )
    code.write_file('demo-items', 'examples/pygeoapi-config.snippet.yml', snippet)
    return '/specs/demo-items/tests/fixtures/items.json'


def test_a_code_built_provider_passes_the_harness_in_the_sandbox(service: Service) -> None:
    implemented_code_provider(service)
    out = service.code_harness('demo-items', {}, ['L1', 'L2', 'L3'])
    assert out['ok'], out.get('explanation') or out.get('problems')
    assert out['egress'] == []  # it declares no hosts, so it gets no network
    assert out['result']['ok'], [c for c in out['result']['checks'] if c['status'] == 'fail']
    checks = {c['id']: c['status'] for c in out['result']['checks']}
    assert checks['L3.server'] == 'pass'


def test_pygeoapi_serves_a_code_built_provider(
    service: Service, serve: callable, workdir: Path
) -> None:
    data = implemented_code_provider(service)
    form = FormState.model_validate(
        {
            'resource_id': 'demo-things',
            'resource': {
                'title': 'Items',
                'description': 'A code-built provider',
                'keywords': ['demo'],
                'extents': {'spatial': {'bbox': [0, 0, 10, 10]}},
            },
            'providers': [{'descriptor': 'created/demo-items', 'values': {'data': data}}],
        }
    )
    probe = service.probe(form, 0, {})
    assert probe['ok'], probe.get('explanation')
    exported = service.export(form, 't')
    assert not exported.validation.blocking, exported.validation.findings
    # Like the dev stack: the workspace at /specs, and sitecustomize puts each src on the path.
    base = serve(
        exported.merged_yaml,
        {'PYTHONPATH': '/opt/runtime:/opt/devpath'},
        image=STABLE_TAG,
        volumes=(
            f'{REPO / "runtime" / "src"}:/opt/runtime:ro',
            f'{REPO / "docker" / "pygeoapi" / "devpath"}:/opt/devpath:ro',
            f'{workdir / "providers"}:/specs:ro',
        ),
        args=('--user', 'root', '--entrypoint', '/entrypoint.sh'),
    )
    items = httpx.get(f'{base}/collections/demo-things/items?f=json&limit=3', timeout=60).json()
    assert items['numberMatched'] == 8 and [f['id'] for f in items['features']] == [
        'i1',
        'i2',
        'i3',
    ]
    one = httpx.get(f'{base}/collections/demo-things/items/i5?f=json', timeout=60)
    assert one.status_code == 200 and one.json()['properties']['value'] == 5
    assert (
        httpx.get(f'{base}/collections/demo-things/items/nope?f=json', timeout=60).status_code
        == 404
    )


# --- Mode B: eject (PLAN.md §7.6; PROMPT.md: "Ejected code runs in stock pygeoapi without
# the runtime package and returns the same results as its spec") --------------------------


def test_the_ejected_sql_join_matches_the_engine_on_postgis(
    demo_sql: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From the host: native-geometry bbox, timestamps, ILIKE and sorting run in PostGIS."""
    sys.path.insert(0, str(REPO / 'backend' / 'tests'))
    from ejecthelp import differences, eject
    from pygeoapi_spec_providers.sql import SQLFeatureProvider

    text = SQL_SPEC.replace('host: postgis\n    port: 5432', 'host: 127.0.0.1\n    port: 55432')
    monkeypatch.setenv('PB_READER_PASSWORD', READER['PB_READER_PASSWORD'])
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(text, encoding='utf-8')
    engine = SQLFeatureProvider({'name': 'x', 'type': 'feature', 'data': str(spec_file)})
    ejected = eject(tmp_path / 'ejected', text, 'pg-join').provider()
    first = engine.query(limit=1)['features'][0]['id']
    march = '2026-03-01T00:00:00Z/2026-03-31T00:00:00Z'
    calls = [
        ('query', {}),
        ('query', {'offset': 100, 'limit': 7}),
        ('query', {'resulttype': 'hits'}),
        ('query', {'limit': 50, 'bbox': [29.3, -3.5, 29.5, -3.3]}),
        ('query', {'limit': 50, 'datetime_': march}),
        ('query', {'limit': 50, 'datetime_': '../2026-01-15T00:00:00Z'}),
        ('query', {'limit': 50, 'properties': [('waterway', 'river')]}),
        ('query', {'limit': 50, 'properties': [('natural_channel', 'true')]}),
        ('query', {'limit': 50, 'properties': [('osm_id', 'abc')]}),
        ('query', {'limit': 50, 'q': 'muha'}),
        ('query', {'limit': 50, 'sortby': [{'property': 'name', 'order': '-'}]}),
        (
            'query',
            {
                'limit': 20,
                'sortby': [{'property': 'reviewed_at', 'order': '+'}],
                'properties': [('waterway', 'canal')],
            },
        ),
        ('query', {'limit': 3, 'select_properties': ['name'], 'skip_geometry': True}),
        ('get', {'identifier': str(first)}),
        ('get', {'identifier': 'abc'}),
        ('get', {'identifier': '999999999999'}),
    ]
    assert differences(engine, ejected, calls) == []


def test_stock_pygeoapi_serves_ejected_providers(
    service: Service, serve: callable, workdir: Path, demo_sql: None
) -> None:
    """geopython/pygeoapi:0.24.0 as published: no runtime package, just the ejected src."""
    from provider_builder.eject import EjectRequest
    from pygeoapi_provider_harness import coveragejson

    stock = subprocess.run(
        ['docker', 'run', '--rm', '--entrypoint', '/venv/bin/python3', IMAGE, '-c',
         'import pygeoapi_spec_providers'],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert stock.returncode != 0 and 'ModuleNotFoundError' in stock.stderr

    service.workspace_save(SQL_SPEC, None, '', None)
    service.workspace_save(coops_spec_text(), None, '', None)
    sql = service.eject('bdi-waterways', EjectRequest(id='bdi-waterways-code'))
    edr = service.eject('coops-water-levels', EjectRequest(id='coops-code'))
    assert sql['ejected_from']['spec'] == 'bdi-waterways'
    forms = [
        {
            'resource_id': 'waterways-ejected',
            'resource': {
                'title': 'Waterways with classes (ejected)',
                'description': 'The SQL join, as standalone code',
                'keywords': ['waterways'],
                'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
            },
            'providers': [
                {
                    'descriptor': 'created/bdi-waterways-code',
                    'values': {
                        'data.host': 'postgis',
                        'data.port': 5432,
                        'data.dbname': 'test',
                        'data.user': 'pb_reader',
                    },
                    'secrets': {'data.password': {'env': 'PB_READER_PASSWORD'}},
                }
            ],
        },
        {
            'resource_id': 'coops-ejected',
            'resource': {
                'title': 'CO-OPS water levels (ejected)',
                'description': 'The time series, as standalone code',
                'keywords': ['water level'],
                'extents': {'spatial': {'bbox': [-160, 20, -70, 50]}},
            },
            'providers': [
                {
                    'descriptor': 'created/coops-code',
                    'values': {
                        'data': 'http://mockapi:8000/coops/mdapi/prod/webapi/stations.json'
                    },
                }
            ],
        },
    ]
    config = workdir / 'config' / 'pygeoapi.config.yml'
    for raw in forms:
        form = FormState.model_validate(raw)
        probe = service.probe(form, 0, READER)  # in the sandbox, with the descriptor's egress
        assert probe['ok'], probe.get('explanation')
        exported = service.export(form, 't')
        assert not exported.validation.blocking, exported.validation.findings
        config.write_text(exported.merged_yaml, encoding='utf-8')
    paths = ':'.join(f'/specs/{d["id"]}/src' for d in (sql, edr))
    base = serve(
        config.read_text(encoding='utf-8'),
        {'PYTHONPATH': paths, **READER},
        volumes=(f'{workdir / "providers"}:/specs:ro',),
    )
    url = f'{base}/collections/waterways-ejected/items'
    items = httpx.get(f'{url}?f=json&limit=2', timeout=60).json()
    assert items['numberMatched'] == 14768 and len(items['features']) == 2
    rivers = httpx.get(f'{url}?f=json&waterway=river&limit=1', timeout=60).json()
    assert rivers['numberMatched'] == 436
    march = httpx.get(
        f'{url}?f=json&datetime=2026-03-01T00:00:00Z/2026-03-31T00:00:00Z', timeout=60
    )
    assert march.json()['numberMatched'] == 88 + 157
    assert httpx.get(f'{url}/{items["features"][0]["id"]}?f=json', timeout=60).status_code == 200

    url = f'{base}/collections/coops-ejected'
    collection = httpx.get(f'{url}?f=json', timeout=60).json()
    assert set(collection['data_queries']) == {'locations', 'position', 'area', 'cube'}
    assert len(httpx.get(f'{url}/locations?f=json', timeout=60).json()['features']) == 6
    day = 'datetime=2026-10-01T00:00:00Z/2026-10-02T00:00:00Z'
    one = httpx.get(f'{url}/locations/9414290?f=json&{day}&parameter-name=water_level', timeout=60)
    assert one.status_code == 200, one.text
    assert coveragejson.problems(one.json()) == []
    assert one.json()['coverages'][0]['ranges']['water_level']['shape'] == [241]
    far = httpx.get(f'{url}/position?coords=POINT(-100 40)&f=json&{day}', timeout=60)
    assert far.status_code == 204
