"""J2/J3 and publishing to file and Admin API targets (PLAN.md §6.4-6.5).

A fake pygeoapi serves ``/collections/{id}`` from the target's config file as it
is *now*, so verify, rollback and the guardrails run against real HTTP. Its
Admin API rewrites that file with PyYAML, as pygeoapi does.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from forms import csv_form
from provider_builder import configedit as ce
from provider_builder.app import create_app
from provider_builder.runner import SubprocessRunner
from provider_builder.service import Service
from provider_builder.settings import Settings, Target


class FakePygeoapi(ThreadingHTTPServer):
    config: Path
    broken: set[str]


class Handler(BaseHTTPRequestHandler):
    server: FakePygeoapi

    def log_message(self, *args: Any) -> None:
        pass

    def send(self, status: int, body: Any = None) -> None:
        data = json.dumps(body if body is not None else {}, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def config(self) -> dict[str, Any]:
        return yaml.safe_load(self.server.config.read_text()) or {}

    def save(self, cfg: dict[str, Any]) -> None:
        self.server.config.write_text(yaml.dump(cfg, sort_keys=False, indent=4))

    def body(self) -> Any:
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n)) if n else None

    def do_GET(self) -> None:
        path = self.path.split('?')[0]
        res = self.config().get('resources') or {}
        if path == '/admin/config':
            return self.send(200, self.config())
        if path.startswith('/admin/config/resources/'):
            rid = path.removeprefix('/admin/config/resources/')
            return self.send(200, res[rid]) if rid in res else self.send(400)
        if path.startswith('/collections/'):
            rest = path.removeprefix('/collections/')
            rid = next((r for r in sorted(res, key=len, reverse=True) if rest.startswith(r)), None)
            if rid is None:
                return self.send(404, {'code': 'NotFound'})
            if rid in self.server.broken:
                return self.send(500, {'code': 'NoApplicableCode'})
            if rest == rid:
                return self.send(200, {'id': rid})
            if rest == f'{rid}/items':
                return self.send(200, {'type': 'FeatureCollection', 'features': [{}]})
        return self.send(404)

    def do_POST(self) -> None:
        cfg = self.config()
        cfg.setdefault('resources', {}).update(self.body())
        self.save(cfg)
        self.send(201)

    def do_PUT(self) -> None:
        cfg = self.config()
        rid = self.path.split('?')[0].removeprefix('/admin/config/resources/')
        cfg['resources'][rid] = self.body()
        self.save(cfg)
        self.send(204)

    def do_DELETE(self) -> None:
        cfg = self.config()
        rid = self.path.split('?')[0].removeprefix('/admin/config/resources/')
        del cfg['resources'][rid]
        self.save(cfg)
        self.send(204)


@pytest.fixture
def fake(settings: Settings) -> Iterator[FakePygeoapi]:
    server = FakePygeoapi(('127.0.0.1', 0), Handler)
    server.config = settings.targets[0].base_config
    server.broken = set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def ops_client(settings: Settings, fake: FakePygeoapi) -> TestClient:
    url = f'http://127.0.0.1:{fake.server_address[1]}'
    config = settings.targets[0].base_config
    settings.state_dir = settings.root / 'state'
    settings.targets = [
        settings.targets[0],
        Target(
            id='file',
            title='File target',
            kind='file',
            base_config=config,
            server_url=url,
            reload='command',
            reload_command=[sys.executable, '-c', 'pass'],
            verify_timeout_s=3,
            keep_backups=3,
        ),
        Target(
            id='admin',
            title='Admin target',
            kind='admin',
            base_config=config,
            server_url=url,
            reload='watch',
            verify_timeout_s=3,
        ),
        Target(id='manual', title='Manual', kind='file', base_config=config, reload='none'),
    ]
    svc = Service(
        settings,
        runner=SubprocessRunner(settings.sandbox, settings.data_roots, settings.workspace),
    )
    app = create_app(service=svc, check_sandbox=False)
    return TestClient(app, base_url='http://localhost', headers={'X-Provider-Builder': '1'})


def finished(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(1200):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('done', 'failed'):
            return job  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError('job did not finish')


def wait(client: TestClient, job_id: str) -> dict[str, Any]:
    job = finished(client, job_id)
    assert job['status'] == 'done', job
    return job['result']  # type: ignore[no-any-return]


def publish(client: TestClient, **body: Any) -> dict[str, Any]:
    r = client.post('/api/publish', json=body)
    assert r.status_code == 202, r.text
    return wait(client, r.json()['job_id'])


def loaded(client: TestClient, target: str, rid: str) -> dict[str, Any]:
    r = client.get(f'/api/targets/{target}/resources/{rid}')
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ------------------------------------------------------------------ J2: load and list


def test_list_and_load(ops_client: TestClient) -> None:
    listing = ops_client.get('/api/targets/file/resources').json()
    assert [r['id'] for r in listing['resources']] == ['lakes']
    lakes = listing['resources'][0]
    assert lakes['providers'][0]['descriptor'] == 'GeoJSON'
    assert lakes['editable'] is True
    one = loaded(ops_client, 'file', 'lakes')
    assert one['etag'] == listing['etag']
    assert one['form']['providers'][0]['values']['data'] == '/data/ne_110m_lakes.geojson'
    assert ops_client.get('/api/targets/file/health/lakes').json()['ok'] is True
    assert ops_client.get('/api/targets/file/resources/nope').status_code == 400


def test_noop_update_writes_nothing(ops_client: TestClient, settings: Settings) -> None:
    config = settings.targets[0].base_config
    before = config.read_bytes()
    one = loaded(ops_client, 'file', 'lakes')
    req = {
        'target': 'file',
        'action': 'update',
        'resource_id': 'lakes',
        'form': one['form'],
        'etag': one['etag'],
    }
    plan = ops_client.post('/api/publish/plan', json=req).json()
    assert plan['changed'] is False
    assert plan['diff'] == ''
    result = publish(ops_client, **req)
    assert result['ok'] is True
    assert result['steps'][-1]['message'].startswith('Nothing changed')
    assert config.read_bytes() == before


# ------------------------------------------------------------------ publish and rollback


def test_update_publishes_a_minimal_diff(ops_client: TestClient, settings: Settings) -> None:
    config = settings.targets[0].base_config
    before = config.read_text()
    one = loaded(ops_client, 'file', 'lakes')
    form = one['form']
    form['resource']['title'] = 'Great Lakes'
    req = {
        'target': 'file',
        'action': 'update',
        'resource_id': 'lakes',
        'form': form,
        'etag': one['etag'],
    }
    plan = ops_client.post('/api/publish/plan', json=req).json()
    assert plan['blocking'] is False, plan['findings']
    assert [line for line in plan['diff'].splitlines() if line[:1] in '+-'][2:] == [
        '-        title: Large Lakes',
        '+        title: Great Lakes',
    ]
    result = publish(ops_client, **req)
    assert result['ok'], result['steps']
    assert [s['name'] for s in result['steps']] == [
        'validate',
        'openapi',
        'write',
        'reload',
        'verify',
    ]
    after = config.read_text()
    assert after == before.replace('title: Large Lakes', 'title: Great Lakes')
    entry = result['entry']
    assert entry['status'] == 'published'
    assert entry['providers'] == ['GeoJSON']
    assert (settings.state_dir / entry['backup']).read_text() == before
    log = ops_client.get('/api/publish/log?target=file').json()
    assert log[0]['id'] == entry['id']

    # Roll back: the file is restored byte for byte.
    r = ops_client.post(f'/api/publish/{entry["id"]}/rollback')
    undo = wait(ops_client, r.json()['job_id'])
    assert undo['ok'], undo['steps']
    assert config.read_text() == before
    log = ops_client.get('/api/publish/log').json()
    assert log[0]['action'] == 'rollback'
    assert log[1]['rolled_back_by'] == log[0]['id']
    again = ops_client.post(f'/api/publish/{entry["id"]}/rollback')
    assert 'already rolled back' in finished(ops_client, again.json()['job_id'])['error']


def test_add_then_delete_and_roll_back(ops_client: TestClient, settings: Settings) -> None:
    config = settings.targets[0].base_config
    before = config.read_text()
    etag = ops_client.get('/api/targets/file/config').json()['etag']
    added = publish(
        ops_client,
        target='file',
        action='add',
        resource_id='obs',
        form=csv_form(),
        etag=etag,
    )
    assert added['ok'], added['steps']
    assert config.read_text().startswith(before.rstrip('\n'))
    assert 'obs' in ce.resources(ce.load(config.read_text()))
    checks = added['steps'][-1]['detail']
    assert [c['url'].split('/collections/')[1] for c in checks] == [
        'obs?f=json',
        'obs/items?f=json&limit=1',
    ]

    with_obs = config.read_text()
    etag = ops_client.get('/api/targets/file/config').json()['etag']
    deleted = publish(ops_client, target='file', action='delete', resource_id='obs', etag=etag)
    assert deleted['ok'], deleted['steps']
    assert config.read_text() == before
    assert deleted['steps'][-1]['detail'][0]['detail'] == 'gone'

    r = ops_client.post(f'/api/publish/{deleted["entry"]["id"]}/rollback')
    assert wait(ops_client, r.json()['job_id'])['ok']
    assert config.read_text() == with_obs


def test_a_broken_publish_reports_and_rolls_back(
    ops_client: TestClient, settings: Settings, fake: FakePygeoapi
) -> None:
    config = settings.targets[0].base_config
    before = config.read_text()
    fake.broken.add('obs')
    etag = ops_client.get('/api/targets/file/config').json()['etag']
    result = publish(
        ops_client, target='file', action='add', resource_id='obs', form=csv_form(), etag=etag
    )
    assert result['ok'] is False
    assert result['entry']['status'] == 'verify-failed'
    assert result['steps'][-1]['detail'][0]['status'] == 500
    r = ops_client.post(f'/api/publish/{result["entry"]["id"]}/rollback')
    undo = wait(ops_client, r.json()['job_id'])
    assert undo['ok'], undo['steps']
    assert config.read_text() == before


def test_the_openapi_gate_blocks_a_provider_that_cannot_load(
    ops_client: TestClient, settings: Settings
) -> None:
    config = settings.targets[0].base_config
    before = config.read_bytes()
    form = csv_form()
    form['providers'][0]['values']['data'] = '/data/missing.csv'
    etag = ops_client.get('/api/targets/file/config').json()['etag']
    result = publish(
        ops_client, target='file', action='add', resource_id='obs', form=form, etag=etag
    )
    assert result['ok'] is False
    assert result['steps'][-1]['name'] == 'openapi'
    assert config.read_bytes() == before
    assert ops_client.get('/api/publish/log').json() == []


def test_stale_etag_is_a_conflict_with_a_diff(ops_client: TestClient, settings: Settings) -> None:
    config = settings.targets[0].base_config
    one = loaded(ops_client, 'file', 'lakes')
    config.write_text(config.read_text().replace('level: ERROR', 'level: DEBUG'))
    r = ops_client.post(
        '/api/publish/plan',
        json={
            'target': 'file',
            'action': 'update',
            'resource_id': 'lakes',
            'form': one['form'],
            'etag': one['etag'],
        },
    )
    assert r.status_code == 409
    body = r.json()
    assert '-    level: ERROR' in body['diff']
    assert '+    level: DEBUG' in body['diff']


def test_rollback_refuses_after_later_changes(ops_client: TestClient, settings: Settings) -> None:
    config = settings.targets[0].base_config
    one = loaded(ops_client, 'file', 'lakes')
    one['form']['resource']['title'] = 'Changed'
    result = publish(
        ops_client,
        target='file',
        action='update',
        resource_id='lakes',
        form=one['form'],
        etag=one['etag'],
    )
    config.write_text(config.read_text() + '# a later hand edit\n')
    r = ops_client.post(f'/api/publish/{result["entry"]["id"]}/rollback')
    job = finished(ops_client, r.json()['job_id'])
    assert job['status'] == 'failed'
    assert 'changed after that publish' in job['error']


def test_backups_are_pruned(ops_client: TestClient, settings: Settings) -> None:
    for title in ['A', 'B', 'C', 'D', 'E']:
        one = loaded(ops_client, 'file', 'lakes')
        one['form']['resource']['title'] = title
        assert publish(
            ops_client,
            target='file',
            action='update',
            resource_id='lakes',
            form=one['form'],
            etag=one['etag'],
        )['ok']
    assert len(list((settings.state_dir / 'backups' / 'file').iterdir())) == 3


def test_reload_none_writes_without_verifying(ops_client: TestClient) -> None:
    one = loaded(ops_client, 'manual', 'lakes')
    one['form']['resource']['title'] = 'Manual'
    result = publish(
        ops_client,
        target='manual',
        action='update',
        resource_id='lakes',
        form=one['form'],
        etag=one['etag'],
    )
    assert result['ok']
    assert result['entry']['status'] == 'written'
    assert 'restart pygeoapi' in result['steps'][-1]['message']


def test_export_targets_do_not_publish(ops_client: TestClient) -> None:
    r = ops_client.post(
        '/api/publish/plan',
        json={'target': 'test', 'action': 'delete', 'resource_id': 'lakes'},
    )
    assert r.status_code == 400
    assert 'export-only' in r.json()['detail']


# ------------------------------------------------------------------ J3


def test_j3_adding_a_second_feature_provider_is_blocked(ops_client: TestClient) -> None:
    one = loaded(ops_client, 'file', 'lakes')
    form = one['form']
    form['providers'].append(form['providers'][0])
    plan = ops_client.post(
        '/api/publish/plan',
        json={
            'target': 'file',
            'action': 'update',
            'resource_id': 'lakes',
            'form': form,
            'etag': one['etag'],
        },
    ).json()
    assert plan['blocking'] is True
    assert 'rule.one-provider-per-type' in [f['code'] for f in plan['findings']]


# ------------------------------------------------------------------ import


def test_import_snippet(ops_client: TestClient) -> None:
    snippet = """
obs:
    type: collection
    title: Observations
    template: old.html
    providers:
        - type: feature
          name: pygeoapi.provider.csv_.CSVProvider
          data: /data/obs.csv
          id_field: id
          geometry: {x_field: long, y_field: lat}
          password: '${QUOTED}'
        - type: tile
          name: MVT-tippecanoe
          data: /data/tiles
"""
    [found] = ops_client.post('/api/import', json={'yaml': snippet}).json()
    assert found['resource_id'] == 'obs'
    providers = found['form']['providers']
    assert providers[0]['descriptor'] == 'CSV'
    assert providers[0]['extra'] == {'password': '${QUOTED}'}
    assert providers[1]['descriptor'] == '_raw'
    text = ' '.join(found['warnings'])
    assert 'placeholder is quoted' in text
    assert 'legacy template' in text
    assert 'not in the catalog' in text
    bad = ops_client.post('/api/import', json={'yaml': 'just a string'})
    assert bad.status_code == 400


# ------------------------------------------------------------------ Admin API


def test_admin_target_is_guarded_and_round_trips(
    ops_client: TestClient, settings: Settings
) -> None:
    config = settings.targets[0].base_config
    listing = ops_client.get('/api/targets/admin/resources').json()
    assert [r['id'] for r in listing['resources']] == ['lakes']
    one = loaded(ops_client, 'admin', 'lakes')
    one['form']['resource']['title'] = 'Admin lakes'
    req = {
        'target': 'admin',
        'action': 'update',
        'resource_id': 'lakes',
        'form': one['form'],
        'etag': one['etag'],
    }
    plan = ops_client.post('/api/publish/plan', json=req).json()
    assert any('drops comments' in w or 'comments' in w for w in plan['warnings'])
    result = publish(ops_client, **req)
    assert result['ok'], result['steps']
    assert [s['name'] for s in result['steps']][-4:] == ['write', 'reload', 'verify', 'admin-read']
    assert yaml.safe_load(config.read_text())['resources']['lakes']['title'] == 'Admin lakes'
    r = ops_client.post(f'/api/publish/{result["entry"]["id"]}/rollback')
    assert wait(ops_client, r.json()['job_id'])['ok']
    assert yaml.safe_load(config.read_text())['resources']['lakes']['title'] == 'Large Lakes'


def test_admin_target_needs_an_acknowledgement_for_secrets(ops_client: TestClient) -> None:
    from forms import postgres_form

    etag = ops_client.get('/api/targets/admin/config').json()['etag']
    req = {
        'target': 'admin',
        'action': 'add',
        'resource_id': 'waterways',
        'form': postgres_form(),
        'etag': etag,
    }
    result = publish(ops_client, **req)
    assert result['ok'] is False
    guard = next(s for s in result['steps'] if s['name'] == 'guard')
    assert '/openapi' in guard['message']


def test_admin_target_requires_a_restarting_reload(
    ops_client: TestClient, settings: Settings
) -> None:
    svc: Service = ops_client.app.state.service  # type: ignore[attr-defined]
    svc.settings.target('admin').reload = 'none'
    one = loaded(ops_client, 'admin', 'lakes')
    one['form']['resource']['title'] = 'X'
    result = publish(
        ops_client,
        target='admin',
        action='update',
        resource_id='lakes',
        form=one['form'],
        etag=one['etag'],
    )
    assert result['ok'] is False
    assert 'restart workers' in result['steps'][-1]['message']


# ------------------------------------------------------------------ app wiring


def test_targets_listing(ops_client: TestClient) -> None:
    kinds = {t['id']: t['kind'] for t in ops_client.get('/api/targets').json()}
    assert kinds == {'test': 'export', 'file': 'file', 'admin': 'admin', 'manual': 'file'}


def test_service_without_targets(settings: Settings) -> None:
    settings.targets = []
    app = create_app(
        service=Service(
            settings,
            runner=SubprocessRunner(settings.sandbox, settings.data_roots, settings.workspace),
        ),
        check_sandbox=False,
    )
    client = TestClient(app, base_url='http://localhost', headers={'X-Provider-Builder': '1'})
    assert client.get('/api/targets/nope/config').status_code == 400
