"""Mode B through the API (PLAN.md §7.4): sample, suggest, spec, preview, harness, save, publish.

Runs with the subprocess runner against the mock API on loopback; the Docker
suites cover the same jobs in the sandbox.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from mockserver import serve_mock_api


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


def spec_from(suggestions: dict[str, Any], url: str, **extra: Any) -> str:
    pagination = {k: v for k, v in suggestions['pagination'].items() if k != 'hints'}
    doc: dict[str, Any] = {
        'spec_version': 1,
        'template': 'http-features',
        'id': 'mock-wells',
        'title': 'Mock wells',
        'description': 'Wells from the mock API',
        'version': '1.0.0',
        'source': {'url': url},
        'records': suggestions['records'],
        'item_id': {'from': suggestions['item_id']['from']},
        'geometry': suggestions['geometry'],
        'properties': suggestions['properties'],
        'time_field': suggestions['time_field'],
        'title_field': suggestions['title_field'],
        'pagination': pagination,
        **extra,
    }
    return yaml.safe_dump(doc, sort_keys=False)


def test_the_whole_template_path(client: TestClient, mock_api: str) -> None:
    url = f'{mock_api}/v2/wells'
    sampled = wait(
        client,
        client.post(
            '/api/samples', json={'source': {'url': url, 'query': {'offset': 0, 'limit': 25}}}
        ),
    )
    assert sampled['ok'], sampled
    s = sampled['suggestions']
    assert (s['records'], s['item_id']['from'], s['title_field']) == (
        '$.data.items',
        'well_id',
        'well_name',
    )
    assert s['pagination']['style'] == 'offset'

    text = spec_from(s, url)
    check = client.post('/api/specs/validate', json={'spec': text}).json()
    assert check['ok'], check['problems']
    assert check['hosts'] == ['127.0.0.1:' + url.split(':')[2].split('/')[0]]

    preview = wait(client, client.post('/api/specs/preview', json={'spec': text}))
    assert preview['ok'], preview
    assert preview['result']['sample']['numberMatched'] == 120
    assert len(preview['result']['sample']['features']) == 10

    harness = wait(
        client, client.post('/api/specs/harness', json={'spec': text, 'levels': ['L1', 'L2']})
    )
    assert harness['ok'], harness
    result = harness['result']
    assert result['ok'], [c for c in result['checks'] if c['status'] == 'fail']

    saved = client.post(
        '/api/workspace', json={'spec': text, 'changes': 'First version.', 'harness': result}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        'id': 'mock-wells',
        'version': '1.0.0',
        'data': '/specs/mock-wells/1.0.0/spec.yaml',
        'descriptor': 'created/mock-wells',
    }
    again = client.post('/api/workspace', json={'spec': text})
    assert again.status_code == 400
    assert 'saved versions never change' in again.json()['detail']

    listing = client.get('/api/workspace').json()
    assert listing == [
        {
            'id': 'mock-wells',
            'title': 'Mock wells',
            'template': 'http-features',
            'versions': ['1.0.0'],
            'descriptor': 'created/mock-wells',
        }
    ]
    detail = client.get('/api/workspace/mock-wells').json()
    assert detail['versions'][0]['harness']['ok'] is True
    assert '## 1.0.0' in detail['changelog'] and 'First version.' in detail['changelog']

    # Mode A picks it up: catalog card, compiled form, probe and export.
    cards = {c['id']: c for c in client.get('/api/catalog').json()}
    assert cards['created/mock-wells']['origin'] == 'created'
    compiled = client.get('/api/catalog/created/mock-wells').json()
    data_field = compiled['ui']['fields']['data']
    assert data_field['choices'] == ['/specs/mock-wells/1.0.0/spec.yaml']
    form = {
        'resource_id': 'wells',
        'resource': {
            'title': 'Wells',
            'description': 'Mock wells',
            'keywords': ['wells'],
            'extents': {'spatial': {'bbox': [-118, 41, -110, 49]}},
        },
        'providers': [
            {
                'descriptor': 'created/mock-wells',
                'values': {'data': '/specs/mock-wells/1.0.0/spec.yaml'},
            }
        ],
    }
    probe = wait(client, client.post('/api/probe', json={'form': form}))
    assert probe['ok'], probe.get('explanation')
    assert probe['result']['sample']['numberMatched'] == 120
    exported = client.post('/api/export', json={'form': form, 'target': 'test'}).json()
    provider = yaml.safe_load(exported['merged_yaml'])['resources']['wells']['providers'][0]
    assert provider == {
        'type': 'feature',
        'name': 'pygeoapi_spec_providers.http.HTTPFeatureProvider',
        'data': '/specs/mock-wells/1.0.0/spec.yaml',
    }


def test_auth_secrets_are_used_and_never_echoed(client: TestClient, mock_api: str) -> None:
    url = f'{mock_api}/secure/header/wells'
    source = {'url': url, 'auth': {'type': 'header', 'name': 'X-API-Key', 'value': '${MOCK_KEY}'}}
    ok = wait(
        client,
        client.post(
            '/api/samples',
            json={'source': source, 'test_secrets': {'MOCK_KEY': 'test-key', 'UNRELATED': 'x'}},
        ),
    )
    assert ok['ok'] and ok['result']['status'] == 200
    bad = wait(
        client,
        client.post(
            '/api/samples', json={'source': source, 'test_secrets': {'MOCK_KEY': 'wrong-key-123'}}
        ),
    )
    assert bad['ok'] and bad['result']['status'] == 401
    assert 'wrong-key-123' not in json.dumps(bad)

    text = spec_from(ok['suggestions'], url, source=source)
    check = client.post('/api/specs/validate', json={'spec': text}).json()
    assert check['ok'] and check['env'] == ['MOCK_KEY']
    preview = wait(
        client,
        client.post(
            '/api/specs/preview',
            json={'spec': text, 'test_secrets': {'MOCK_KEY': 'wrong-key-123'}},
        ),
    )
    assert not preview['ok']
    assert 'rejected the credentials' in json.dumps(preview['error'])
    assert 'wrong-key-123' not in json.dumps(preview)

    saved = client.post('/api/workspace', json={'spec': text}).json()
    compiled = client.get(f'/api/catalog/{saved["descriptor"]}').json()
    assert compiled['env'] == [{'name': 'MOCK_KEY', 'secret': True}]
    form = {
        'resource_id': 'secure',
        'resource': {
            'title': 'S',
            'description': 'S',
            'keywords': ['s'],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
        },
        'providers': [{'descriptor': saved['descriptor'], 'values': {'data': saved['data']}}],
    }
    probe = wait(
        client,
        client.post('/api/probe', json={'form': form, 'test_secrets': {'MOCK_KEY': 'test-key'}}),
    )
    assert probe['ok'], probe
    exported = client.post('/api/export', json={'form': form, 'target': 'test'}).json()
    assert [e['name'] for e in exported['env']] == ['MOCK_KEY']
    assert any('MOCK_KEY' in step for step in exported['instructions'])


def test_spec_problems_come_back_with_pointers(client: TestClient) -> None:
    text = (
        'spec_version: 1\ntemplate: http-features\nid: x\ntitle: X\nsource:\n'
        '  url: https://a.example/x\n  auth:\n    type: bearer\n    value: "${TOKEN}"\n'
    )
    check = client.post('/api/specs/validate', json={'spec': text}).json()
    assert not check['ok']
    assert check['problems'][0]['pointer'] == '/source/auth/value'
    saved = client.post('/api/workspace', json={'spec': text})
    assert saved.status_code == 400
    assert saved.json()['problems'][0]['message'].startswith('This placeholder is quoted')
