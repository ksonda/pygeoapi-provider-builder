"""Host/Origin/CSRF guard (ADR-0012) and the data-root path guard (PROMPT.md §7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from provider_builder.app import create_app
from provider_builder.fs import PathError, list_dir, resolve_server_path
from provider_builder.service import Service
from provider_builder.settings import Settings


@pytest.fixture
def raw_client(service: Service) -> TestClient:
    return TestClient(
        create_app(service=service, check_sandbox=False), base_url='http://localhost'
    )


def test_get_is_allowed(raw_client: TestClient) -> None:
    assert raw_client.get('/api/health').status_code == 200


@pytest.mark.parametrize('host', ['evil.example', 'localhost.evil.example', '10.0.0.5'])
def test_foreign_host_is_refused(service: Service, host: str) -> None:
    c = TestClient(create_app(service=service, check_sandbox=False), base_url=f'http://{host}')
    assert c.get('/api/health').status_code == 403


def test_state_change_needs_custom_header(raw_client: TestClient) -> None:
    r = raw_client.post('/api/render', json={'form': {'resource_id': 'x'}})
    assert r.status_code == 403 and 'x-provider-builder' in r.json()['detail']


def test_foreign_origin_is_refused(raw_client: TestClient) -> None:
    r = raw_client.post(
        '/api/render',
        json={'form': {'resource_id': 'x'}},
        headers={'X-Provider-Builder': '1', 'Origin': 'https://evil.example'},
    )
    assert r.status_code == 403


def test_cross_site_fetch_is_refused(raw_client: TestClient) -> None:
    r = raw_client.post(
        '/api/render',
        json={'form': {'resource_id': 'x'}},
        headers={'X-Provider-Builder': '1', 'Sec-Fetch-Site': 'cross-site'},
    )
    assert r.status_code == 403


def test_allowed_origin_passes(raw_client: TestClient) -> None:
    r = raw_client.post(
        '/api/render',
        json={'form': {'resource_id': 'x'}},
        headers={'X-Provider-Builder': '1', 'Origin': 'http://localhost:5173'},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------- paths


def test_resolves_inside_root(settings: Settings) -> None:
    r = resolve_server_path(settings, '/data/obs.csv')
    assert r.host_path == settings.data_roots[0].host_path.resolve() / 'obs.csv'


@pytest.mark.parametrize(
    'path', ['/data/../etc/passwd', '/etc/passwd', 'data/obs.csv', '/database/x', '/data/\x00']
)
def test_rejects_outside_or_traversal(settings: Settings, path: str) -> None:
    with pytest.raises(PathError):
        resolve_server_path(settings, path)


def test_rejects_symlink_escape(settings: Settings, tmp_path: Path) -> None:
    secret = tmp_path / 'outside.txt'
    secret.write_text('secret')
    os.symlink(secret, settings.data_roots[0].host_path / 'link.csv')
    with pytest.raises(PathError, match='symbolic link'):
        resolve_server_path(settings, '/data/link.csv')
    listing = list_dir(settings, '/data')
    assert 'link.csv' not in [e['name'] for e in listing['entries']]


def test_list_dir(settings: Settings) -> None:
    listing = list_dir(settings, '/data')
    assert {e['name'] for e in listing['entries']} >= {'obs.csv', 'poi_portugal.gpkg'}
    assert all(e['server_path'].startswith('/data/') for e in listing['entries'])
    assert listing['parent'] is None


def test_same_origin_passes(raw_client: TestClient) -> None:
    r = raw_client.post(
        '/api/render',
        json={'form': {'resource_id': 'x'}},
        headers={'X-Provider-Builder': '1', 'Origin': 'http://localhost'},
    )
    assert r.status_code == 200
