"""Integration: file-target publishes reach a real pygeoapi 0.24.0 (PLAN.md §6.4).

Each test runs a throwaway pygeoapi container whose config directory is a
target's folder, so the builder edits the file the server reads:

- ``reload: command`` with ``docker restart``: the image's entrypoint
  regenerates OpenAPI at start.
- ``reload: watch`` with ``run-with-hot-reload`` (gunicorn ``--reload
  --reload-extra-file``): nothing regenerates OpenAPI, so the builder writes it
  first (docs/pygeoapi-notes.md §9.1).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dockerutil import IMAGE, NETWORK, REPO
from forms import CASES
from provider_builder.runner import DockerRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target, Workspace
from provider_builder.targets import PublishRequest

pytestmark = pytest.mark.compose

DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
BASE = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / 'data').mkdir()
    for name in ('obs.csv', 'ne_110m_lakes.geojson'):
        shutil.copy(DATA / name, tmp_path / 'data' / name)
    (tmp_path / 'config').mkdir()
    shutil.copy(BASE, tmp_path / 'config' / 'pygeoapi.config.yml')
    return tmp_path


def free_port() -> int:
    """A fixed host port: with ``127.0.0.1::80`` Docker picks a new one on each restart."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def wait_up(base: str, name: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if httpx.get(f'{base}/collections', timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr
    raise AssertionError(f'pygeoapi did not start:\n{logs[-3000:]}')


@pytest.fixture
def server(workdir: Path) -> Iterator[Any]:
    names: list[str] = []

    def start(mode: str) -> tuple[str, str]:
        name = f'pb-pub-{uuid.uuid4().hex[:8]}'
        names.append(name)
        port = free_port()
        cmd = [
            'docker', 'run', '-d', '--name', name, '--network', NETWORK,
            '-p', f'127.0.0.1:{port}:80',
            '-v', f'{workdir / "config"}:/config',
            '-v', f'{workdir / "data"}:/data',
            '-e', 'PYGEOAPI_CONFIG=/config/pygeoapi.config.yml',
            '-e', 'PYGEOAPI_OPENAPI=/config/openapi.yml',
            IMAGE,
        ]  # fmt: skip
        if mode == 'watch':
            cmd.append('run-with-hot-reload')
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        base = f'http://127.0.0.1:{port}'
        wait_up(base, name)
        return name, base

    yield start
    for name in names:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=60)


def make_service(workdir: Path, target: Target) -> Service:
    settings = Settings(
        root=workdir,
        data_roots=[DataRoot(name='d', host_path=workdir / 'data', server_path='/data')],
        targets=[target],
        catalogs=[REPO / 'catalog'],
        sandbox=SandboxSettings(runner='docker', egress_network=NETWORK),
        workspace=Workspace(host_path=workdir / 'providers', server_path='/specs'),
        state_dir=workdir / 'state',
    )
    return Service(
        settings, runner=DockerRunner(settings.sandbox, settings.data_roots, settings.workspace)
    )


def title(base: str, rid: str) -> Any:
    r = httpx.get(f'{base}/collections/{rid}?f=json', timeout=30)
    return r.json().get('title') if r.status_code == 200 else r.status_code


def test_publish_update_delete_and_roll_back(workdir: Path, server: Any) -> None:
    name, base = server('restart')
    config = workdir / 'config' / 'pygeoapi.config.yml'
    original = config.read_text()
    svc = make_service(
        workdir,
        Target(
            id='t',
            title='t',
            kind='file',
            base_config=config,
            server_url=base,
            reload='command',
            reload_command=['docker', 'restart', name],
            verify_timeout_s=120,
        ),
    )
    ops = svc.targets

    form = CASES['csv']()
    added = ops.publish(
        PublishRequest(target='t', action='add', resource_id='obs', form=form, etag=None)
    )
    assert added['ok'], added['steps']
    after_add = config.read_text()
    assert after_add.startswith(original.rstrip('\n'))
    items = httpx.get(f'{base}/collections/obs/items?f=json&limit=2', timeout=30).json()
    assert len(items['features']) == 2

    loaded = ops.resource('t', 'obs')
    form = loaded['form']
    form['resource']['title'] = 'Observations, renamed'
    updated = ops.publish(
        PublishRequest(
            target='t', action='update', resource_id='obs', form=form, etag=loaded['etag']
        )
    )
    assert updated['ok'], updated['steps']
    assert title(base, 'obs') == 'Observations, renamed'
    diff = [d for d in updated['plan']['diff'].splitlines() if d[:1] in '+-'][2:]
    assert diff == ['-        title: Observations', '+        title: Observations, renamed']

    undone = ops.rollback(updated['entry']['id'])
    assert undone['ok'], undone['steps']
    assert config.read_text() == after_add
    assert title(base, 'obs') == 'Observations'

    deleted = ops.publish(
        PublishRequest(target='t', action='delete', resource_id='obs', etag=None)
    )
    assert deleted['ok'], deleted['steps']
    assert config.read_text() == original
    assert title(base, 'obs') == 404

    restored = ops.rollback(deleted['entry']['id'])
    assert restored['ok'], restored['steps']
    assert title(base, 'obs') == 'Observations'
    actions = [e.action for e in ops.log.entries()]
    assert actions == ['add', 'update', 'rollback', 'delete', 'rollback']


def test_watch_reload_serves_openapi_written_first(workdir: Path, server: Any) -> None:
    name, base = server('watch')
    config = workdir / 'config' / 'pygeoapi.config.yml'
    svc = make_service(
        workdir,
        Target(
            id='t',
            title='t',
            kind='file',
            base_config=config,
            openapi=workdir / 'config' / 'openapi.yml',
            server_url=base,
            reload='watch',
            verify_timeout_s=60,
        ),
    )
    result = svc.targets.publish(
        PublishRequest(target='t', action='add', resource_id='obs', form=CASES['csv']())
    )
    assert result['ok'], result['steps']
    assert [s['name'] for s in result['steps']][-2:] == ['reload', 'verify']
    assert result['entry']['openapi_backup']
    # The hot reload re-read OpenAPI from the file the builder wrote.
    deadline = time.monotonic() + 30
    paths: dict[str, Any] = {}
    while time.monotonic() < deadline:
        paths = httpx.get(f'{base}/openapi?f=json', timeout=30).json()['paths']
        if '/collections/obs/items' in paths:
            break
        time.sleep(1)
    assert '/collections/obs/items' in paths
