"""Eject (PLAN.md §7.6): the ejected code returns what the spec engine returns.

Each case ejects a spec, imports the package from its folder (with its ``src``
on the path, as the sandbox runs it) and compares the two providers call by
call: the result, or the pygeoapi error and its user message. The fixtures are
the engine's own: the mock API and SQLite here, PostGIS and a stock pygeoapi
image in tests/test_compose_integration.py.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from ejecthelp import Call, differences, eject
from fastapi.testclient import TestClient
from mockserver import serve_mock_api
from provider_builder.eject import EjectError, simple_path
from provider_builder.scaffold import ruff_binary
from shapely import wkt
from specs import provider as http_provider
from specs import wells
from sqlfixtures import make_database
from sqlfixtures import provider as sql_provider
from sqlfixtures import spec as sql_spec
from test_http_provider import AUTH, PAGINATION, PUSHDOWN, QUERIES, SPEC_YAML, styled
from tsfixtures import EDR_KWARGS, coops
from tsfixtures import provider as ts_provider


@pytest.fixture(scope='module')
def mock_api() -> Iterator[str]:
    with serve_mock_api() as base:
        yield base


@pytest.fixture(autouse=True)
def loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mock API is on loopback, which the engine's egress policy refuses unless allowed.
    monkeypatch.setenv('PYGEOAPI_SPEC_PRIVATE_HOSTS', '127.0.0.1')


HTTP_CALLS: list[Call] = [
    ('query', {}),
    ('query', {'offset': 10, 'limit': 5}),
    ('query', {'limit': 150}),
    ('query', {'resulttype': 'hits'}),
    ('query', {'limit': 2, 'select_properties': ['depth_ft'], 'skip_geometry': True}),
    ('query', {'filterq': 'x'}),
    *[('query', {'limit': 500, **q}) for q in QUERIES],
    ('query', {'limit': 500, 'datetime_': 'not a date'}),
    ('get', {'identifier': 'W0042'}),
    ('get', {'identifier': 'nope'}),
]


# --- http-features -----------------------------------------------------------------------


@pytest.mark.parametrize('pushdown', [False, True], ids=['local', 'pushdown'])
@pytest.mark.parametrize('style', list(PAGINATION))
def test_every_pagination_style(tmp_path: Path, mock_api: str, style: str, pushdown: bool) -> None:
    doc = styled(mock_api, style, **({'filters': PUSHDOWN} if pushdown else {}))
    ejected = eject(tmp_path, doc, 'http')
    assert differences(http_provider(doc), ejected.provider(), HTTP_CALLS) == []


def test_item_urls(tmp_path: Path, mock_api: str) -> None:
    doc = wells(mock_api, item={'url': mock_api + '/v2/wells/{id}', 'record': '$.data'})
    ejected = eject(tmp_path, doc, 'item')
    calls = [*HTTP_CALLS, ('get', {'identifier': '../../health'})]
    assert differences(http_provider(doc), ejected.provider(), calls) == []


def esri(base: str) -> dict[str, Any]:
    return wells(
        base,
        '/esri/wells',
        source={
            'url': base + '/esri/wells',
            'error_path': '$.error',
            'retries': {'max': 0},
            'auth': {'type': 'query', 'name': 'token', 'value': '${ESRI_TOKEN}'},
        },
        records='$.features',
        item_id={'from': '$.attributes.OBJECTID'},
        geometry={'type': 'point', 'x': '$.geometry.x', 'y': '$.geometry.y'},
        properties=[
            {'name': 'name', 'type': 'string', 'from': '$.attributes.NAME'},
            {
                'name': 'measured',
                'type': 'datetime',
                'from': '$.attributes.MEASURED',
                'format': 'epoch_ms',
            },
        ],
        time_field='measured',
        title_field='name',
        pagination={'style': 'none'},
    )


@pytest.mark.parametrize('token', ['test-key', 'nope'])
def test_relative_paths_epoch_times_and_api_errors(
    tmp_path: Path, mock_api: str, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    text = yaml.safe_dump(esri(mock_api), sort_keys=False)
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(text, encoding='utf-8')
    monkeypatch.setenv('ESRI_TOKEN', token)
    ejected = eject(tmp_path, text, 'esri')
    assert ejected.provider_def()['options'] == {'api_key': token}
    calls: list[Call] = [('query', {}), ('query', {'limit': 1}), ('get', {'identifier': '1'})]
    assert differences(http_provider(str(spec_file)), ejected.provider(), calls) == []


def test_geojson_geometries(tmp_path: Path, mock_api: str) -> None:
    doc = wells(
        mock_api,
        '/geojson/wells',
        records='$.items',
        item_id={'from': 'id'},
        geometry={'type': 'geojson', 'field': 'geometry'},
        properties=[{'name': 'name', 'type': 'string'}],
        title_field='name',
        pagination={'style': 'none'},
    )
    del doc['time_field']
    ejected = eject(tmp_path, doc, 'geojson')
    calls: list[Call] = [('query', {}), ('query', {'bbox': [-115.0, 43.0, -112.0, 46.0]})]
    assert differences(http_provider(doc), ejected.provider(), calls) == []


@pytest.mark.parametrize('kind', list(AUTH))
def test_auth_from_the_config(
    tmp_path: Path, mock_api: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, auth, secret = AUTH[kind]
    text = SPEC_YAML.format(url=mock_api + path, auth=auth)
    ejected = eject(tmp_path, text, f'auth-{kind}')
    assert (
        '${MOCK_KEY}' in (ejected.folder / 'examples' / 'pygeoapi-config.snippet.yml').read_text()
    )
    for value in (secret, 'wrong-secret'):
        monkeypatch.setenv('MOCK_KEY', value)
        spec_file = tmp_path / f'{value}.yaml'  # the engine caches spec files by path
        spec_file.write_text(text, encoding='utf-8')
        calls: list[Call] = [('query', {'limit': 1})]
        assert differences(http_provider(str(spec_file)), ejected.provider(), calls) == []
    assert all(secret not in content for content in ejected.files.values())


@pytest.mark.parametrize(
    'path,source',
    [
        ('/flaky/wells', {'retries': {'max': 1, 'backoff_s': 0}}),  # 429, then a retry
        ('/slow/wells', {'timeout_s': 0.5}),
        ('/error/wells', {}),
        ('/not-json/wells', {}),
        ('/redirect/in', {}),
        ('/redirect/out', {}),
        ('/big/wells', {'max_response_bytes': 1000}),
    ],
)
def test_failures(tmp_path: Path, mock_api: str, path: str, source: dict[str, Any]) -> None:
    doc = wells(mock_api, path)
    doc['source'].update(source)
    ejected = eject(tmp_path, doc, 'failure')
    assert differences(http_provider(doc), ejected.provider(), [('query', {'limit': 2})]) == []


# --- sql-features ------------------------------------------------------------------------

SQL_CALLS: list[Call] = [
    ('query', {}),
    ('query', {'offset': 10, 'limit': 5}),
    ('query', {'limit': 500}),
    ('query', {'resulttype': 'hits'}),
    ('query', {'limit': 500, 'bbox': [-115.0, 43.0, -112.0, 46.0]}),
    ('query', {'limit': 500, 'datetime_': '2021-01-01T00:00:00Z/2022-06-30T00:00:00Z'}),
    ('query', {'limit': 500, 'datetime_': '../2020-06-01T00:00:00Z'}),
    ('query', {'limit': 500, 'properties': [('county', 'Kootenai')]}),
    ('query', {'limit': 500, 'properties': [('active', 'true')]}),
    ('query', {'limit': 500, 'properties': [('depth_ft', 'abc')]}),
    ('query', {'limit': 500, 'q': 'bonner'}),
    ('query', {'limit': 500, 'q': '50%_x'}),
    ('query', {'limit': 500, 'sortby': [{'property': 'depth_ft', 'order': '-'}]}),
    (
        'query',
        {
            'limit': 50,
            'sortby': [{'property': 'well_name', 'order': '+'}],
            'properties': [('county', 'Canyon')],
        },
    ),
    ('query', {'limit': 3, 'select_properties': ['county'], 'skip_geometry': True}),
    ('query', {'filterq': 'x'}),
    ('get', {'identifier': 'W0042'}),
    ('get', {'identifier': 'nope'}),
]
GEOMETRIES = {
    'xy': {'encoding': 'xy', 'x': 'longitude', 'y': 'latitude'},
    'wkt': {'encoding': 'wkt', 'column': 'wkt'},
    'gpkg': {'encoding': 'gpkg', 'column': 'gpkg'},
}


@pytest.fixture(scope='module')
def wells_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_database(tmp_path_factory.mktemp('sql') / 'wells.sqlite')


@pytest.mark.parametrize('geometry', list(GEOMETRIES))
def test_sql_features(tmp_path: Path, wells_db: Path, geometry: str) -> None:
    doc = sql_spec(wells_db, GEOMETRIES[geometry])
    ejected = eject(tmp_path, doc, 'sql')
    assert ejected.provider_def()['data'] == str(wells_db)
    assert differences(sql_provider(doc), ejected.provider(), SQL_CALLS) == []


def test_sql_integer_ids(tmp_path: Path, wells_db: Path) -> None:
    query = (
        'SELECT rowid AS fid, w.well_id, w.well_name, w.county, w.longitude, w.latitude, '
        'w.wkt, w.gpkg, m.depth, m.measured_at, m.status, m.active '
        'FROM wells AS w JOIN measurements AS m ON m.well_id = w.well_id'
    )
    doc = sql_spec(wells_db, query=query, item_id={'from': 'fid', 'type': 'integer'})
    ejected = eject(tmp_path, doc, 'sql-ids')
    calls: list[Call] = [('get', {'identifier': i}) for i in ('3', '3.0', 'x', '999')]
    assert differences(sql_provider(doc), ejected.provider(), calls) == []


# --- timeseries-edr ----------------------------------------------------------------------


def edr(query_type: str, **kwargs: Any) -> Call:
    """A query() call as pygeoapi's EDR API makes it, with every keyword."""
    return ('query', {**EDR_KWARGS, 'query_type': query_type, **kwargs})


def recent_window(hours: int = 6) -> str:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    return f'{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}'


def test_time_series_over_http(tmp_path: Path, mock_api: str) -> None:
    window = recent_window()
    point = wkt.loads('POINT(-122.4659 37.8063)')
    california = wkt.loads('POLYGON((-125 30, -115 30, -115 40, -125 40, -125 30))')
    calls = [
        edr('locations'),
        edr('locations', bbox=[-125, 30, -115, 40]),
        edr('locations', limit=2),
        edr('locations', location_id='9414290', datetime_=window),
        edr(
            'locations', location_id='9414290', datetime_=window, select_properties=['water_level']
        ),
        edr('locations', location_id='1612340', datetime_=window),  # no air temperature
        edr('locations', location_id='nope', datetime_=window),
        edr('locations', location_id='9414290', datetime_=window, select_properties=['nope']),
        edr(
            'locations',
            location_id='9414290',
            datetime_='2020-01-01T00:00:00Z/2020-03-01T00:00:00Z',
        ),
        edr(
            'locations',
            location_id='9414290',
            datetime_='2020-03-01T00:00:00Z/2020-01-01T00:00:00Z',
        ),
        edr('position', wkt=point, datetime_=window),
        edr('position', wkt=wkt.loads('POINT(0 0)'), datetime_=window),
        edr('position', wkt=california, datetime_=window),
        edr('area', wkt=california, datetime_=window),
        edr('cube', bbox=[-125, 30, -115, 40], datetime_=window),
        edr('cube', datetime_=window),
    ]
    doc = coops(mock_api)
    engine, ejected = ts_provider(doc), eject(tmp_path, doc, 'coops').provider()
    assert ejected.get_query_types() == engine.get_query_types()
    assert ejected.get_fields() == engine.get_fields()
    assert differences(engine, ejected, calls) == []
    narrow = coops(mock_api, query_types=['locations', 'position'])
    ejected = eject(tmp_path, narrow, 'coops-narrow').provider()
    assert ejected.get_query_types() == ['locations', 'position']
    assert differences(ts_provider(narrow), ejected, calls[:4]) == []


def observations_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sites (site TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL);
        CREATE TABLE obs (site TEXT, param TEXT, t TEXT, v REAL);
        INSERT INTO sites VALUES ('A', 'Alpha', -105.0, 40.0), ('B', 'Beta', -104.0, 39.0);
        """
    )
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(48):
        moment = (start + timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('INSERT INTO obs VALUES (?, ?, ?, ?)', ('A', 'stage', moment, i / 10))
        conn.execute('INSERT INTO obs VALUES (?, ?, ?, ?)', ('B', 'stage', moment, i / 5))
    conn.commit()
    conn.close()
    return path


def test_time_series_from_sql(tmp_path: Path) -> None:
    from test_timeseries_provider import sql_spec as ts_sql_spec

    doc = ts_sql_spec(observations_db(tmp_path / 'obs.sqlite'))
    window = '2026-10-01T00:00:00Z/2026-10-01T05:00:00Z'
    calls = [
        edr('locations'),
        edr('locations', location_id='B', datetime_=window),
        edr(
            'area',
            wkt=wkt.loads('POLYGON((-106 39.5, -104.5 39.5, -104.5 41, -106 41, -106 39.5))'),
            datetime_=window,
        ),
        edr('position', wkt=wkt.loads('POINT(-104.0 39.0)'), datetime_=window),
        edr('cube', bbox=[-110, 35, -100, 45], datetime_=window),
    ]
    ejected = eject(tmp_path, doc, 'gages')
    # One database for locations and observations: the config names it once, as data.
    assert 'options' not in ejected.provider_def()
    assert differences(ts_provider(doc), ejected.provider(), calls) == []


# --- the package -------------------------------------------------------------------------


def test_simple_paths_become_indexing() -> None:
    assert simple_path('$') == ([], False)
    assert simple_path('$.data.items') == (['data', 'items'], False)
    assert simple_path("$['a b'][0].c") == (['a b', 0, 'c'], False)
    assert simple_path('$.features[*]') == (['features'], True)
    assert simple_path('$[-1]') == ([-1], False)
    for path in ("$.links[?@.rel=='next'].href", '$..x', '$.a[*].b', '$.a[0:2]', "$['a','b']"):
        assert simple_path(path) is None, path


def test_packages_stand_alone_and_pass_ruff(tmp_path: Path, mock_api: str, wells_db: Path) -> None:
    from test_timeseries_provider import sql_spec as ts_sql_spec

    linked = styled(mock_api, 'next_link', filters=PUSHDOWN)  # a JSONPath filter: python-jsonpath
    packages = [
        eject(tmp_path, linked, 'standalone-http'),
        eject(tmp_path, sql_spec(wells_db), 'standalone-sql'),
        eject(tmp_path, coops(mock_api), 'standalone-coops'),
        eject(tmp_path, ts_sql_spec(observations_db(tmp_path / 'o.sqlite')), 'standalone-ts'),
    ]
    assert f'src/{packages[0].package}/jsonpaths.py' in packages[0].files
    assert 'python-jsonpath' in packages[0].files['pyproject.toml']
    assert 'jsonpaths.py' not in ' '.join(packages[2].files)  # CO-OPS paths are plain keys
    for p in packages:
        for name, content in p.files.items():
            if name.endswith('.py'):
                assert 'pygeoapi_spec_providers' not in content, name
        for args in (['check', '.'], ['format', '--check', '.']):
            proc = subprocess.run(  # noqa: S603 - our ruff, on generated files
                [ruff_binary(), *args], cwd=p.folder, capture_output=True, text=True, check=False
            )
            assert proc.returncode == 0, (p.package, proc.stdout, proc.stderr)
        modules = [f'{p.package}.provider'] + (
            [f'{p.package}.locations'] if (p.src / p.package / 'locations.py').exists() else []
        )
        # Import with the spec engine made unimportable: the package must not need it.
        script = (
            'import sys; sys.modules["pygeoapi_spec_providers"] = None; '
            f'sys.path.insert(0, {str(p.src)!r}); ' + '; '.join(f'import {m}' for m in modules)
        )
        proc = subprocess.run(  # noqa: S603 - the generated package, in a child process
            [sys.executable, '-I', '-c', script], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stderr


def test_spec_values_stay_literals(tmp_path: Path, mock_api: str) -> None:
    nasty = "x'''\"\"\"\\\n{__import__('pathlib').Path('PWNED').touch()}"
    doc = wells(mock_api, title=nasty, description=nasty)
    doc['source']['headers'] = {'X-Note': nasty}
    doc['source']['query'] = {'note': nasty}
    doc['properties'].append({'name': 'odd', 'type': 'string', 'from': nasty})
    doc['properties'].append({'name': 'odd2', 'type': 'string', 'from': "$['a\\'b']"})
    ejected = eject(tmp_path, doc, 'nasty')
    for name, content in ejected.files.items():
        if name.endswith('.py'):
            ast.parse(content, filename=name)
    provider = ejected.provider()
    assert not (Path.cwd() / 'PWNED').exists()
    module = sys.modules[f'{ejected.package}.provider']
    headers, query = module.HEADERS, module.QUERY
    assert headers == {'X-Note': nasty}
    assert query == {'note': nasty}
    assert "{__import__('pathlib').Path('PWNED').touch()}" in (module.__doc__ or '')
    assert provider.get_fields()['odd'] == {'type': 'string'}
    query = 'SELECT \'"""{x}\' AS "we""ird{}", well_id, longitude, latitude FROM wells'
    sql = eject(
        tmp_path,
        {
            'spec_version': 1,
            'template': 'sql-features',
            'id': 'odd-sql',
            'title': 'Odd',
            'source': {'dialect': 'sqlite', 'path': str(tmp_path / 'x.sqlite')},
            'query': query,
            'item_id': {'from': 'well_id'},
            'geometry': {'encoding': 'xy', 'x': 'longitude', 'y': 'latitude'},
            'properties': [{'name': 'weird', 'type': 'string', 'from': 'we"ird{}'}],
        },
        'odd-sql',
    )
    source = sql.files[f'src/{sql.package}/provider.py']
    ast.parse(source)
    assert '"we""ird{}"' in source


def test_placeholders_outside_the_config_are_refused(tmp_path: Path, mock_api: str) -> None:
    doc = wells(mock_api)
    doc['source']['query'] = {'state': '${STATE_CODE}'}
    with pytest.raises(EjectError) as err:
        eject(tmp_path, doc, 'placeholder')
    assert [p.pointer for p in err.value.problems] == ['/source/query/state']


# --- through the API ---------------------------------------------------------------------


def wait(client: TestClient, response: Any) -> dict[str, Any]:
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    for _ in range(1200):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('done', 'failed'):
            assert job['status'] == 'done', job
            return dict(job['result'])
        time.sleep(0.05)
    raise AssertionError('job did not finish')


def test_eject_through_the_api(client: TestClient, mock_api: str) -> None:
    path, auth, secret = AUTH['header']
    text = SPEC_YAML.format(url=mock_api + path, auth=auth)
    saved = client.post('/api/workspace', json={'spec': text})
    assert saved.status_code == 200, saved.text
    version = saved.json()['version']

    ejected = client.post('/api/workspace/secure-wells/eject', json={'id': 'secure-wells-code'})
    assert ejected.status_code == 200, ejected.text
    detail = ejected.json()
    assert detail['plugin'] == 'secure_wells_code_provider.provider.SecureWellsCodeProvider'
    assert detail['env'] == ['MOCK_KEY'] and detail['versions'] == []
    assert 'src/secure_wells_code_provider/http_client.py' in detail['files']
    # The spec stays, and remembers where it went.
    spec = client.get('/api/workspace/secure-wells').json()
    assert spec['ejected'][0]['code_id'] == 'secure-wells-code'
    assert spec['ejected'][0]['version'] == version
    cards = {c['id']: c for c in client.get('/api/catalog').json()}
    assert cards['created/secure-wells-code']['plugin'] == detail['plugin']
    assert 'created/secure-wells' in cards

    # The contract checks pass on the ejected package, with the test secret.
    checks = wait(
        client,
        client.post(
            '/api/code/secure-wells-code/harness',
            json={'levels': ['L1', 'L2'], 'test_secrets': {'MOCK_KEY': secret}},
        ),
    )
    assert checks['ok'], checks
    failing = [c for c in checks['result']['checks'] if c['status'] == 'fail']
    assert checks['result']['ok'], failing
    saved = client.post(
        '/api/code/secure-wells-code/versions',
        json={'version': '1.0.0', 'changes': 'Ejected.', 'harness': checks['result']},
    )
    assert saved.status_code == 200, saved.text

    again = client.post('/api/workspace/secure-wells/eject', json={'id': 'secure-wells-code'})
    assert again.status_code == 400 and 'already exists' in again.json()['detail']
    missing = client.post(
        '/api/workspace/secure-wells/eject', json={'id': 'x', 'version': '9.9.9'}
    )
    assert missing.status_code == 400
    snippet = client.get(
        '/api/code/secure-wells-code/files/examples/pygeoapi-config.snippet.yml'
    ).json()['content']
    assert 'api_key: ${MOCK_KEY}' in snippet
    assert json.loads(
        client.get('/api/code/secure-wells-code/files/.provider-builder.json').json()['content']
    )['ejected_from'] == {
        'spec': 'secure-wells',
        'version': version,
        'template': 'http-features',
        'date': datetime.now(UTC).strftime('%Y-%m-%d'),
    }
