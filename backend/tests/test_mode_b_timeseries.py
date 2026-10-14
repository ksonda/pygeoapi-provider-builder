"""Mode B's time-series template through the API, against the mock CO-OPS look-alike.

Runs with the subprocess runner on loopback; tests/test_compose_integration.py
serves the result from a real pygeoapi.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from mockserver import serve_mock_api

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime' / 'tests'))

from tsfixtures import coops  # noqa: E402


@pytest.fixture(scope='module')
def mock_api() -> Iterator[str]:
    with serve_mock_api() as base:
        yield base


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


def test_the_whole_time_series_path(client: TestClient, mock_api: str) -> None:
    doc = coops(mock_api)
    # Locations: the stations list through the HTTP sample job.
    stations = wait(
        client, client.post('/api/samples', json={'source': doc['locations']['http']['source']})
    )
    assert stations['ok'] and stations['suggestions']['records'] == '$.stations'
    assert stations['suggestions']['geometry'] == {'type': 'point', 'x': 'lng', 'y': 'lat'}

    # Observations: one station and parameter, the URL template filled in by the backend.
    obs = wait(
        client,
        client.post(
            '/api/samples/observations',
            json={
                'observations': doc['observations'],
                'location_id': '9414290',
                'parameter': 'water_level',
                'window': 'PT2H',
            },
        ),
    )
    assert obs['ok'], obs
    assert obs['request']['query']['station'] == '9414290'
    s = obs['suggestions']
    assert s['records'] == '$.data' and s['record_count'] >= 20
    assert s['time'] == {'from': 't', 'format': '%Y-%m-%d %H:%M'}
    assert s['value'] == {'from': 'v'}  # CO-OPS sends values as text

    text = yaml.safe_dump(doc, sort_keys=False)
    check = client.post('/api/specs/validate', json={'spec': text}).json()
    assert check['ok'], check['problems']
    assert len(check['hosts']) == 1  # the stations list and the datagetter share the mock host

    preview = wait(client, client.post('/api/specs/preview', json={'spec': text}))
    assert preview['ok'], preview
    assert preview['result']['query_types'] == ['locations', 'position', 'area', 'cube']
    assert len(preview['result']['sample']['features']) == 6

    harness = wait(
        client, client.post('/api/specs/harness', json={'spec': text, 'levels': ['L1', 'L2']})
    )
    assert harness['ok'], harness
    result = harness['result']
    assert result['ok'], [c for c in result['checks'] if c['status'] == 'fail']
    statuses = {c['id']: c['status'] for c in result['checks']}
    assert statuses['L1.edr_position'] == statuses['L2.edr_location'] == 'pass'

    saved = client.post('/api/workspace', json={'spec': text, 'harness': result}).json()
    card = {c['id']: c for c in client.get('/api/catalog').json()}[saved['descriptor']]
    assert card['provider_type'] == 'edr' and card['requires'] == [
        'pygeoapi_spec_providers',
        'httpx',
        'jsonpath',
    ]
    detail = client.get(f'/api/catalog/{saved["descriptor"]}').json()
    assert detail['query_types'] == ['locations', 'position', 'area', 'cube']
    form = {
        'resource_id': 'coops',
        'resource': {
            'title': 'CO-OPS water levels',
            'description': 'Water levels at NOAA tide stations',
            'keywords': ['water level'],
            'extents': {'spatial': {'bbox': [-160, 20, -70, 50]}},
        },
        'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
    }
    probe = wait(client, client.post('/api/probe', json={'form': form}))
    assert probe['ok'], probe.get('explanation')
    assert probe['result']['extent']['bbox'] == pytest.approx(
        [-157.8645, 21.3033, -71.0503, 47.6026]
    )
    exported = client.post('/api/export', json={'form': form, 'target': 'test'}).json()
    assert not exported['validation']['blocking'], exported['validation']['findings']
    provider = yaml.safe_load(exported['merged_yaml'])['resources']['coops']['providers'][0]
    assert provider == {
        'type': 'edr',
        'name': 'pygeoapi_spec_providers.timeseries.TimeSeriesEDRProvider',
        'data': '/specs/coops-water-levels/1.0.0/spec.yaml',
    }


def test_sql_observation_samples_bind_their_parameters(client: TestClient, settings: Any) -> None:
    import sqlite3

    db = settings.data_roots[0].host_path / 'obs.sqlite'
    conn = sqlite3.connect(db)
    conn.executescript(
        'CREATE TABLE obs (site TEXT, param TEXT, t TEXT, v REAL);'
        "INSERT INTO obs VALUES ('A', 'stage', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour'),"
        ' 1.5);'
        "INSERT INTO obs VALUES ('B', 'stage', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour'),"
        ' 9.0);'
    )
    conn.commit()
    conn.close()
    observations = {
        'from': 'sql',
        'sql': {
            'source': {'dialect': 'sqlite', 'path': '/data/obs.sqlite'},
            'query': 'SELECT t, v FROM obs WHERE site = :location_id AND param = :parameter '
            'AND t BETWEEN :start AND :end',
            'time': {'from': 't'},
            'value': {'from': 'v'},
        },
    }
    out = wait(
        client,
        client.post(
            '/api/samples/observations',
            json={'observations': observations, 'location_id': 'A', 'parameter': 'stage'},
        ),
    )
    assert out['ok'], out
    assert out['result']['row_count'] == 1 and out['result']['rows'][0][1] == 1.5
    assert out['suggestions']['properties'][0]['type'] == 'datetime'
