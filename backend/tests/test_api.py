"""The Mode A API flow, with the subprocess runner (no Docker needed)."""

from __future__ import annotations

import time
from typing import Any

import yaml
from fastapi.testclient import TestClient

from forms import csv_form


def wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(600):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('done', 'failed'):
            return job
        time.sleep(0.05)
    raise AssertionError('job did not finish')


def test_catalog(client: TestClient) -> None:
    cards = client.get('/api/catalog').json()
    assert {c['id'] for c in cards} == {'CSV', 'GeoJSON', 'SQLiteGPKG', 'PostgreSQL'}
    detail = client.get('/api/catalog/CSV').json()
    assert detail['json_schema']['required'] == ['data', 'geometry.x_field', 'geometry.y_field',
                                                  'id_field']
    assert client.get('/api/catalog/Nope').status_code == 400


def test_fs(client: TestClient) -> None:
    assert 'obs.csv' in [e['name'] for e in client.get('/api/fs?path=/data').json()['entries']]
    assert client.get('/api/fs?path=/etc').status_code == 400


def test_introspect_csv(client: TestClient) -> None:
    provider = {'descriptor': 'CSV', 'values': {'data': '/data/obs.csv'}}
    job = client.post('/api/introspect', json={'provider': provider})
    assert job.status_code == 202
    result = wait(client, job.json()['job_id'])['result']
    assert result['ok'], result
    assert [c['name'] for c in result['result']['columns']] == [
        'id', 'stn_id', 'datetime', 'value', 'lat', 'long']


def test_introspect_rejects_paths_outside_roots(client: TestClient) -> None:
    provider = {'descriptor': 'CSV', 'values': {'data': '/etc/passwd'}}
    result = wait(client, client.post('/api/introspect', json={'provider': provider})
                  .json()['job_id'])['result']
    assert not result['ok'] and 'outside the data roots' in result['explanation']['message']


def test_probe_validate_export(client: TestClient) -> None:
    form = csv_form()
    job = wait(client, client.post('/api/probe', json={'form': form}).json()['job_id'])
    probe = job['result']
    assert probe['ok'], probe
    assert probe['result']['sample']['numberMatched'] == 5
    assert probe['result']['extent'] == {
        'from': 'full', 'bbox': [-122.0, 43.0, -75.0, 49.0],
        'temporal': ['2000-10-30T18:24:39+00:00', '2007-10-30T08:57:29+00:00']}
    probes = [{'fields': probe['result']['fields'],
               'sample_stats': probe['result']['sample_stats']}]

    v = client.post('/api/validate', json={'form': form, 'target': 'test', 'probes': probes})
    assert v.status_code == 200 and v.json()['blocking'] is False, v.json()

    e = client.post('/api/export', json={'form': form, 'target': 'test', 'probes': probes}).json()
    merged = yaml.safe_load(e['merged_yaml'])
    assert list(merged['resources']) == ['lakes', 'obs']
    assert e['merged_yaml'].startswith('# pygeoapi config for the Provider Builder dev stack')
    assert any('restart pygeoapi' in s for s in e['instructions'])


def test_probe_failure_is_explained(client: TestClient) -> None:
    form = csv_form()
    form['providers'][0]['values']['geometry.x_field'] = 'lon'
    probe = wait(client, client.post('/api/probe', json={'form': form}).json()['job_id'])['result']
    assert not probe['ok']
    assert probe['explanation']['message'] == 'The CSV file has no column called lon.'
    assert 'KeyError' in probe['error']['traceback']


def test_validate_blocks_on_errors(client: TestClient) -> None:
    form = csv_form(resource_id='lakes')          # already in the base config
    v = client.post('/api/validate', json={'form': form, 'target': 'test'}).json()
    assert v['blocking'] and 'config.splice' in [f['code'] for f in v['findings']]


def test_openapi_document_is_served(client: TestClient) -> None:
    doc = client.get('/api/openapi.json').json()
    assert '/api/probe' in doc['paths'] and 'FormState' in doc['components']['schemas']
