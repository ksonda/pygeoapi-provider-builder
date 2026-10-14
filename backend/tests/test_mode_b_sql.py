"""Mode B's SQL template through the API: sample, suggest, spec, preview, harness, save, Mode A.

A SQLite join in the test data root, run with the subprocess runner; the
Docker suites cover PostGIS in the sandbox (tests/test_sql_postgis.py).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from provider_builder.settings import DataRoot, Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime' / 'tests'))

from sqlfixtures import JOIN, make_database  # noqa: E402


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


@pytest.fixture
def settings(settings: Settings, tmp_path: Path) -> Settings:
    """Adds a data root whose server path is its host path.

    The subprocess runner remaps the paths a config names, but not paths inside a
    spec; in the Docker sandbox, data roots are mounted at their server paths.
    """
    root = tmp_path / 'sql-data'
    root.mkdir()
    return settings.model_copy(
        update={
            'data_roots': [
                *settings.data_roots,
                DataRoot(name='sql', host_path=root, server_path=str(root)),
            ]
        }
    )


@pytest.fixture
def db(settings: Settings) -> str:
    path = settings.data_roots[-1].host_path / 'wells.sqlite'
    make_database(path)
    return str(path)


def spec_text(suggestions: dict[str, Any], source: dict[str, Any], query: str) -> str:
    item_id = {k: v for k, v in suggestions['item_id'].items() if k != 'candidates'}
    doc = {
        'spec_version': 1,
        'template': 'sql-features',
        'id': 'sql-wells',
        'title': 'Wells and measurements',
        'version': '1.0.0',
        'source': source,
        'query': query,
        'item_id': item_id,
        'geometry': suggestions['geometry'],
        'properties': suggestions['properties'],
        'time_field': suggestions['time_field'],
        'title_field': suggestions['title_field'],
    }
    return yaml.safe_dump(doc, sort_keys=False)


def test_the_whole_sql_template_path(client: TestClient, db: str) -> None:
    source = {'dialect': 'sqlite', 'path': db}
    sampled = wait(client, client.post('/api/samples/sql', json={'source': source, 'query': JOIN}))
    assert sampled['ok'], sampled
    assert sampled['result']['row_count'] == 50
    s = sampled['suggestions']
    assert s['item_id']['from'] == 'well_id'
    assert s['geometry'] == {'encoding': 'gpkg', 'column': 'gpkg'}  # GeoPackage blobs win
    by_name = {p['name']: p for p in s['properties']}
    assert by_name['depth']['type'] == 'number'
    assert by_name['measured_at']['type'] == 'datetime'
    assert 'wkt' in by_name  # WKT text stays a property unless chosen as the geometry
    assert s['time_field'] == 'measured_at' and s['title_field'] == 'well_name'
    assert s['query'].startswith('SELECT w.well_id')

    text = spec_text(s, source, JOIN)
    check = client.post('/api/specs/validate', json={'spec': text}).json()
    assert check['ok'], check['problems']
    assert check['hosts'] == []  # a SQLite file needs no network

    preview = wait(client, client.post('/api/specs/preview', json={'spec': text}))
    assert preview['ok'], preview
    assert preview['result']['sample']['numberMatched'] == 120

    harness = wait(
        client, client.post('/api/specs/harness', json={'spec': text, 'levels': ['L1', 'L2']})
    )
    assert harness['ok'], harness
    assert harness['result']['ok'], [
        c for c in harness['result']['checks'] if c['status'] == 'fail'
    ]
    statuses = {c['id']: c['status'] for c in harness['result']['checks']}
    assert statuses['L1.bbox'] == statuses['L1.q'] == statuses['L1.sortby'] == 'pass'

    saved = client.post('/api/workspace', json={'spec': text, 'harness': harness['result']}).json()
    assert saved['descriptor'] == 'created/sql-wells'
    card = {c['id']: c for c in client.get('/api/catalog').json()}['created/sql-wells']
    assert card['requires'] == ['pygeoapi_spec_providers', 'sqlglot', 'sqlalchemy']
    form = {
        'resource_id': 'sql-wells',
        'resource': {
            'title': 'Wells',
            'description': 'Wells and measurements',
            'keywords': ['wells'],
            'extents': {'spatial': {'bbox': [-118, 41, -110, 49]}},
        },
        'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
    }
    probe = wait(client, client.post('/api/probe', json={'form': form}))
    assert probe['ok'], probe.get('explanation')
    exported = client.post('/api/export', json={'form': form, 'target': 'test'}).json()
    provider = yaml.safe_load(exported['merged_yaml'])['resources']['sql-wells']['providers'][0]
    assert provider == {
        'type': 'feature',
        'name': 'pygeoapi_spec_providers.sql.SQLFeatureProvider',
        'data': '/specs/sql-wells/1.0.0/spec.yaml',
    }


def test_sql_problems_and_secrets(client: TestClient, db: str) -> None:
    bad = wait(
        client,
        client.post(
            '/api/samples/sql',
            json={'source': {'dialect': 'sqlite', 'path': db}, 'query': 'DELETE FROM wells'},
        ),
    )
    assert not bad['ok'] and bad['problems'][0]['pointer'] == '/query'
    outside = wait(
        client,
        client.post(
            '/api/samples/sql',
            json={'source': {'dialect': 'sqlite', 'path': '/etc/passwd'}, 'query': 'SELECT 1'},
        ),
    )
    assert not outside['ok'] and outside['problems'][0]['pointer'] == '/source/path'
    literal = {
        'dialect': 'postgresql',
        'connection': {'host': 'db.example.org', 'dbname': 'x', 'user': 'u', 'password': 'pw'},
    }
    secret = wait(
        client, client.post('/api/samples/sql', json={'source': literal, 'query': 'SELECT 1'})
    )
    assert [p['pointer'] for p in secret['problems']] == ['/source/connection/password']
