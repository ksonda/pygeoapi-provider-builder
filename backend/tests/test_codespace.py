"""J5, the code workspace, through the API (PLAN.md §7.5): scaffold, edit, lint, check, version.

The contract checks run with the subprocess runner here; the provider's ``src``
reaches the worker the way it reaches the sandbox, as a python_path entry under
the workspace's server path.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

ITEMS = {
    'type': 'FeatureCollection',
    'features': [
        {
            'type': 'Feature',
            'id': f'i{n}',
            'geometry': {'type': 'Point', 'coordinates': [n * 1.0, n * 0.5]},
            'properties': {'name': f'item {n}', 'value': n},
        }
        for n in range(1, 9)
    ],
}

IMPLEMENTED = '''"""A file-backed demo provider: the scaffold, implemented."""

from __future__ import annotations

import json
from typing import Any

from pygeoapi.provider.base import BaseProvider, ProviderItemNotFoundError


class DemoItemsProvider(BaseProvider):
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {'name': {'type': 'string'}, 'value': {'type': 'integer'}}

    def get_fields(self) -> dict[str, dict[str, Any]]:
        return self._fields

    def _items(self) -> list[dict[str, Any]]:
        with open(self.data, encoding='utf-8') as fh:
            return list(json.load(fh)['features'])

    def query(
        self,
        offset=0,
        limit=10,
        resulttype='results',
        bbox=None,
        datetime_=None,
        properties=None,
        sortby=None,
        select_properties=None,
        skip_geometry=False,
        q=None,
        **kwargs,
    ) -> dict[str, Any]:
        items = self._items()
        page = [] if resulttype == 'hits' else items[offset : offset + limit]
        for f in page:
            if select_properties:
                keep = set(select_properties)
                f['properties'] = {k: v for k, v in f['properties'].items() if k in keep}
            if skip_geometry:
                f['geometry'] = None
        return {
            'type': 'FeatureCollection',
            'features': page,
            'numberMatched': len(items),
            'numberReturned': len(page),
        }

    def get(self, identifier, **kwargs) -> dict[str, Any]:
        for f in self._items():
            if str(f['id']) == str(identifier):
                return f
        raise ProviderItemNotFoundError(user_msg='item not found')
'''


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


def create(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {
        'provider_type': 'feature',
        'id': 'demo-items',
        'title': 'Demo items',
        'style': 'files',
        'class_name': 'DemoItemsProvider',
        **extra,
    }
    response = client.post('/api/code', json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_scaffold_edit_check_and_version(client: TestClient) -> None:
    detail = create(client)
    assert detail['plugin'] == 'demo_items_provider.provider.DemoItemsProvider'
    assert 'src/demo_items_provider/provider.py' in detail['files']
    assert detail['python_path'] == '/specs/demo-items/src'
    assert detail['versions'] == [] and detail['changed'] == []
    cards = {c['id']: c for c in client.get('/api/catalog').json()}
    assert cards['created/demo-items']['plugin'] == detail['plugin']

    # The files API stays inside the package and its text files.
    for bad in ('../escape.py', '.git/config', 'src/x.so'):
        assert client.get(f'/api/code/demo-items/files/{bad}').status_code in (400, 404)
    source = client.get('/api/code/demo-items/files/src/demo_items_provider/provider.py').json()
    assert 'raise NotImplementedError' in source['content']

    # ruff over stdin: diagnostics, and formatting.
    lint = client.post(
        '/api/code/demo-items/lint', json={'path': 'x.py', 'content': 'import os\nx=1\n'}
    ).json()
    assert 'F401' in [d['code'] for d in lint]
    formatted = client.post(
        '/api/code/demo-items/format', json={'path': 'x.py', 'content': 'x=  {"a":1}\n'}
    ).json()
    assert formatted['content'] == "x = {'a': 1}\n"  # the package's own ruff config

    # The stubs fail the contract with NotImplementedError ...
    first = wait(client, client.post('/api/code/demo-items/harness', json={'levels': ['L1']}))
    assert first['ok'] and not first['result']['ok']
    failing = [c for c in first['result']['checks'] if c['status'] == 'fail']
    assert failing and all('NotImplementedError' in c['message'] for c in failing)

    # ... and the implemented provider passes it, against its example config.
    put = lambda path, content: client.put(  # noqa: E731
        f'/api/code/demo-items/files/{path}', json={'content': content}
    )
    assert put('tests/fixtures/items.json', json.dumps(ITEMS)).status_code == 200
    assert put('src/demo_items_provider/provider.py', IMPLEMENTED).status_code == 200
    snippet = client.get('/api/code/demo-items/files/examples/pygeoapi-config.snippet.yml').json()[
        'content'
    ]
    snippet = snippet.replace('/data/items.json', '/specs/demo-items/tests/fixtures/items.json')
    assert put('examples/pygeoapi-config.snippet.yml', snippet).status_code == 200
    passed = wait(
        client, client.post('/api/code/demo-items/harness', json={'levels': ['L1', 'L2']})
    )
    assert passed['ok'], passed
    assert passed['result']['ok'], [c for c in passed['result']['checks'] if c['status'] == 'fail']

    # A version needs a passing checklist; it's a Git tag with the version everywhere.
    refused = client.post('/api/code/demo-items/versions', json={'version': '1.0.0'})
    assert refused.status_code == 400
    saved = client.post(
        '/api/code/demo-items/versions',
        json={'version': '1.0.0', 'changes': 'First version.', 'harness': passed['result']},
    )
    assert saved.status_code == 200, saved.text
    detail = client.get('/api/code/demo-items').json()
    assert detail['versions'] == ['1.0.0'] and detail['changed'] == []
    pyproject = client.get('/api/code/demo-items/files/pyproject.toml').json()['content']
    assert 'version = "1.0.0"' in pyproject
    changelog = client.get('/api/code/demo-items/files/CHANGELOG.md').json()['content']
    assert '## 1.0.0' in changelog and 'First version.' in changelog
    descriptor = client.get(
        '/api/code/demo-items/files/src/demo_items_provider/descriptor.yaml'
    ).json()['content']
    assert yaml.safe_load(descriptor)['version'] == '1.0.0'

    # Mode A: the catalog entry probes (the src is on the worker's path) and exports.
    form = {
        'resource_id': 'demo-things',
        'resource': {
            'title': 'Items',
            'description': 'Demo items',
            'keywords': ['demo'],
            'extents': {'spatial': {'bbox': [0, 0, 10, 10]}},
        },
        'providers': [
            {
                'descriptor': 'created/demo-items',
                'values': {'data': '/specs/demo-items/tests/fixtures/items.json'},
            }
        ],
    }
    probe = wait(client, client.post('/api/probe', json={'form': form}))
    assert probe['ok'], probe.get('explanation')
    assert probe['result']['sample']['numberMatched'] == 8
    exported = client.post('/api/export', json={'form': form, 'target': 'test'}).json()
    assert not exported['validation']['blocking'], exported['validation']['findings']
    provider = yaml.safe_load(exported['merged_yaml'])['resources']['demo-things']['providers'][0]
    assert provider['name'] == 'demo_items_provider.provider.DemoItemsProvider'


@pytest.mark.parametrize('kind', ['feature', 'record', 'coverage', 'edr', 'tile', 'map'])
def test_every_type_scaffolds_and_passes_ruff(client: TestClient, kind: str) -> None:
    detail = create(
        client,
        provider_type=kind,
        id=f'demo-{kind}',
        title=f'Demo {kind}',
        style='rest',
        class_name=None,
        query_types=['position', 'locations'],
    )
    for path in detail['files']:
        if path.endswith('.py'):
            content = client.get(f'/api/code/demo-{kind}/files/{path}').json()['content']
            lint = client.post(
                f'/api/code/demo-{kind}/lint', json={'path': path, 'content': content}
            ).json()
            assert lint == [], (path, lint)
            formatted = client.post(
                f'/api/code/demo-{kind}/format', json={'path': path, 'content': content}
            ).json()
            assert formatted['content'] == content, path


def test_names_are_checked(client: TestClient) -> None:
    bad = client.post(
        '/api/code',
        json={'provider_type': 'feature', 'id': 'x', 'title': 'X', 'package': 'import'},
    )
    assert bad.status_code == 400
    assert client.post(
        '/api/code', json={'provider_type': 'feature', 'id': 'Bad Id', 'title': 'X'}
    ).status_code in (400, 422)
    create(client, id='twice')
    again = client.post(
        '/api/code', json={'provider_type': 'feature', 'id': 'twice', 'title': 'Twice'}
    )
    assert again.status_code == 400


def test_import_from_git(client: TestClient, settings: Any, tmp_path: Any) -> None:
    create(client, id='origin-provider', title='Origin')
    source = settings.workspace.host_path / 'origin-provider'
    clone = client.post(
        '/api/code/import', json={'id': 'cloned-provider', 'git_url': f'file://{source}'}
    )
    assert clone.status_code == 200, clone.text
    assert clone.json()['remote'] == f'file://{source}'
    (source / 'README.md').write_text('# Changed upstream\n')
    subprocess.run(['git', '-C', str(source), 'commit', '-qam', 'Change'], check=True)
    pulled = client.post('/api/code/cloned-provider/pull')
    assert pulled.status_code == 200, pulled.text
    readme = client.get('/api/code/cloned-provider/files/README.md').json()['content']
    assert readme == '# Changed upstream\n'
    not_ours = tmp_path / 'plain'
    not_ours.mkdir()
    refused = client.post('/api/code/import', json={'id': 'plain', 'folder': str(not_ours)})
    assert refused.status_code == 400
