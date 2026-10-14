"""J6 (PLAN.md §7.8): packages, zips, Git delivery and usage, through the API.

Test images are built in tests/test_delivery_images.py (Docker).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from mockserver import serve_mock_api
from provider_builder.delivery import compare_url, module_name, safe_url

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime' / 'tests'))
from specs import wells  # noqa: E402
from test_codespace import IMPLEMENTED, ITEMS  # noqa: E402


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


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def save_spec(client: TestClient, base: str) -> tuple[str, str]:
    text = yaml.safe_dump(wells(base, version='1.2.0'), sort_keys=False)
    saved = client.post('/api/workspace', json={'spec': text, 'version': '1.2.0'})
    assert saved.status_code == 200, saved.text
    return text, saved.json()['version']


def test_a_spec_version_becomes_a_package(
    client: TestClient, mock_api: str, tmp_path: Path
) -> None:
    text, version = save_spec(client, mock_api)
    url = f'/api/workspace/mock-wells/versions/{version}/package.zip'
    # The gate: no checks recorded with this version yet.
    refused = client.get(url)
    assert refused.status_code == 400 and 'no passing contract checks' in refused.json()['detail']
    checks = wait(
        client, client.post(f'/api/workspace/mock-wells/versions/{version}/checks', json={})
    )
    assert checks['ok'] and checks['result']['ok'], checks
    detail = client.get('/api/workspace/mock-wells').json()
    assert detail['versions'][0]['harness']['ok'] is True

    got = client.get(url)
    assert got.status_code == 200
    assert got.headers['content-type'] == 'application/zip'
    assert 'mock-wells-spec-1.2.0.zip' in got.headers['content-disposition']
    assert client.get(url).content == got.content  # the same bytes each time
    files = members(got.content)
    root = 'mock-wells-spec-1.2.0/'
    assert all(name.startswith(root) for name in files)
    module = module_name('mock-wells')
    assert files[f'{root}src/{module}/spec.yaml'].decode() == text  # byte for byte
    pyproject = files[f'{root}pyproject.toml'].decode()
    assert 'version = "1.2.0"' in pyproject and 'pygeoapi-spec-providers>=' in pyproject
    assert f'{root}vendor/pygeoapi-spec-providers/pyproject.toml' in files
    assert f'{root}vendor/pygeoapi-spec-providers/src/pygeoapi_spec_providers/http.py' in files
    descriptor = yaml.safe_load(files[f'{root}src/{module}/descriptor.yaml'])
    data = next(f for f in descriptor['fields'] if f['key'] == 'data')
    assert data['choices'] == [f'pkg:{module}/spec.yaml'] and descriptor['version'] == '1.2.0'
    assert json.loads(files[f'{root}harness.json'])['ok'] is True
    snippet = yaml.safe_load(files[f'{root}examples/pygeoapi-config.snippet.yml'])
    assert snippet['mock-wells']['providers'][0]['data'] == f'pkg:{module}/spec.yaml'

    # The packaged spec loads as a package resource (ADR-0005).
    with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
        archive.extractall(tmp_path)
    script = (
        'from pygeoapi_spec_providers.spec import load_spec; '
        f"s = load_spec('pkg:{module}/spec.yaml'); print(s.id, s.data['version'])"
    )
    env = {**os.environ, 'PYTHONPATH': str(tmp_path / root / 'src')}
    proc = subprocess.run(  # noqa: S603 - our interpreter, our package
        [sys.executable, '-c', script], capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ['mock-wells', '1.2.0']


def code_version(client: TestClient) -> None:
    created = client.post(
        '/api/code',
        json={
            'provider_type': 'feature',
            'id': 'demo-items',
            'title': 'Demo items',
            'style': 'files',
            'class_name': 'DemoItemsProvider',
        },
    )
    assert created.status_code == 200, created.text

    def put(path: str, content: str) -> None:
        r = client.put(f'/api/code/demo-items/files/{path}', json={'content': content})
        assert r.status_code == 200, r.text

    put('tests/fixtures/items.json', json.dumps(ITEMS))
    put('src/demo_items_provider/provider.py', IMPLEMENTED)
    snippet = client.get('/api/code/demo-items/files/examples/pygeoapi-config.snippet.yml')
    put(
        'examples/pygeoapi-config.snippet.yml',
        snippet.json()['content'].replace(
            '/data/items.json', '/specs/demo-items/tests/fixtures/items.json'
        ),
    )
    checks = wait(client, client.post('/api/code/demo-items/harness', json={'levels': ['L1']}))
    assert checks['result']['ok'], checks
    saved = client.post(
        '/api/code/demo-items/versions',
        json={'version': '1.0.0', 'changes': 'First.', 'harness': checks['result']},
    )
    assert saved.status_code == 200, saved.text


def test_a_code_version_is_its_tag(client: TestClient) -> None:
    code_version(client)
    # A change after the version doesn't reach its package.
    client.put('/api/code/demo-items/files/README.md', json={'content': '# Changed\n'})
    got = client.get('/api/code/demo-items/versions/1.0.0/package.zip')
    assert got.status_code == 200
    files = members(got.content)
    root = 'demo-items-provider-1.0.0/'
    assert files[f'{root}README.md'] != b'# Changed\n'
    assert 'version = "1.0.0"' in files[f'{root}pyproject.toml'].decode()
    assert json.loads(files[f'{root}harness.json'])['ok'] is True
    assert f'{root}src/demo_items_provider/provider.py' in files
    assert not any('.git/' in name for name in files)
    missing = client.get('/api/code/demo-items/versions/9.9.9/package.zip')
    assert missing.status_code == 400 and 'no saved version 9.9.9' in missing.json()['detail']


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(  # noqa: S603 - git with fixed arguments
        ['git', *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def test_git_delivery(client: TestClient, mock_api: str, tmp_path: Path) -> None:
    code_version(client)
    remote = tmp_path / 'remote.git'
    git('init', '--bare', '-q', str(remote))
    pushed = client.post(
        '/api/code/demo-items/push', json={'remote': f'file://{remote}', 'branch': 'provider/demo'}
    )
    assert pushed.status_code == 200, pushed.text
    out = pushed.json()
    assert out['branch'] == 'provider/demo' and out['tags'] == ['v1.0.0']
    assert out['compare_url'] is None
    assert git('rev-parse', 'provider/demo', cwd=remote).strip() == out['commit']
    assert 'v1.0.0' in git('tag', '--list', cwd=remote).split()

    # The spec workspace pushes its versions and tags the same way.
    save_spec(client, mock_api)
    spec_remote = tmp_path / 'specs.git'
    git('init', '--bare', '-q', str(spec_remote))
    pushed = client.post(
        '/api/workspace/mock-wells/push', json={'remote': f'file://{spec_remote}'}
    )
    assert pushed.status_code == 200, pushed.text
    assert 'v1.2.0' in git('tag', '--list', cwd=spec_remote).split()
    assert '1.2.0/spec.yaml' in git('ls-tree', '-r', '--name-only', 'main', cwd=spec_remote)

    for body, message in (
        ({'remote': 'https://user:secret-token@example.org/x.git'}, 'password or token'),
        ({'remote': 'ftp://example.org/x.git'}, 'https://'),
        ({'remote': f'file://{remote}', 'branch': '../escape'}, 'not a branch name'),
        ({'remote': f'file://{remote}', 'branch': '--force'}, 'not a branch name'),
    ):
        refused = client.post('/api/code/demo-items/push', json=body)
        assert refused.status_code == 400 and message in refused.json()['detail'], body
        assert 'secret-token' not in refused.text


def test_compare_urls_and_safe_urls() -> None:
    assert compare_url('https://github.com/org/repo.git', 'b') == (
        'https://github.com/org/repo/compare/b?expand=1'
    )
    assert compare_url('git@github.com:org/repo.git', 'b') == (
        'https://github.com/org/repo/compare/b?expand=1'
    )
    assert compare_url('https://gitlab.example.org/org/repo.git', 'b') is None
    assert safe_url('https://u:pw@example.org/r.git') == 'https://u:***@example.org/r.git'


def test_usage_reads_the_targets_configs(client: TestClient, mock_api: str, settings: Any) -> None:
    _text, version = save_spec(client, mock_api)
    config = settings.targets[0].base_config
    doc = yaml.safe_load(config.read_text())
    doc['resources']['wells-a'] = {
        'type': 'collection',
        'title': 'Wells A',
        'description': 'Wells',
        'keywords': ['wells'],
        'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
        'providers': [
            {
                'type': 'feature',
                'name': 'pygeoapi_spec_providers.http.HTTPFeatureProvider',
                'data': f'/specs/mock-wells/{version}/spec.yaml',
            }
        ],
    }
    doc['resources']['wells-b'] = {
        **doc['resources']['wells-a'],
        'title': 'Wells B',
        'providers': [
            {
                'type': 'feature',
                'name': 'pygeoapi_spec_providers.http.HTTPFeatureProvider',
                'data': f'pkg:{module_name("mock-wells")}/spec.yaml',
            }
        ],
    }
    config.write_text(yaml.safe_dump(doc, sort_keys=False))
    usage = client.get('/api/workspace/mock-wells/usage').json()
    found = {(c['resource_id'], c['version'], c['via']) for c in usage['collections']}
    assert found == {('wells-a', version, 'spec path'), ('wells-b', None, 'package')}
    assert usage['errors'] == [] and usage['history'] == []
