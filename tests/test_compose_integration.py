"""Integration: pygeoapi 0.24.0 serves what the builder exports (PLAN.md §12).

Needs the dev stack (``make stack-up``) for PostGIS. Each test starts its own
throwaway pygeoapi container with the exported config, so the dev stack's own
config is never modified.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from dockerutil import IMAGE, NETWORK, REPO
from forms import CASES
from provider_builder.render import FormState
from provider_builder.runner import DockerRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target

pytestmark = pytest.mark.compose

DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
BASE = REPO / 'docker' / 'pygeoapi' / 'pygeoapi.config.yml'
SECRETS = {'PYGEOAPI_WATERWAYS_DB_PASSWORD': 'postgres'}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / 'data').mkdir()
    for name in ('obs.csv', 'ne_110m_lakes.geojson', 'poi_portugal.gpkg'):
        shutil.copy(DATA / name, tmp_path / 'data' / name)
    (tmp_path / 'config').mkdir()
    shutil.copy(BASE, tmp_path / 'config' / 'pygeoapi.config.yml')
    return tmp_path


@pytest.fixture
def service(workdir: Path) -> Service:
    settings = Settings(
        root=workdir,
        data_roots=[DataRoot(name='d', host_path=workdir / 'data', server_path='/data')],
        targets=[
            Target(id='t', title='t', base_config=workdir / 'config' / 'pygeoapi.config.yml')
        ],
        catalogs=[REPO / 'catalog'],
        sandbox=SandboxSettings(
            runner='docker', egress_network=NETWORK, private_hosts=['postgis']
        ),
    )
    return Service(settings, runner=DockerRunner(settings.sandbox, settings.data_roots))


@pytest.fixture
def serve(workdir: Path) -> Iterator[callable]:  # type: ignore[valid-type]
    names: list[str] = []

    def start(config_text: str, env: dict[str, str]) -> str:
        (workdir / 'config' / 'pygeoapi.config.yml').write_text(config_text)
        name = f'pb-it-{uuid.uuid4().hex[:8]}'
        names.append(name)
        cmd = [
            'docker',
            'run',
            '-d',
            '--rm',
            '--name',
            name,
            '--network',
            NETWORK,
            '-p',
            '127.0.0.1::80',
            '-v',
            f'{workdir / "config"}:/config:ro',
            '-v',
            f'{workdir / "data"}:/data',
            '-e',
            'PYGEOAPI_CONFIG=/config/pygeoapi.config.yml',
        ]
        for key, value in env.items():
            cmd += ['-e', f'{key}={value}']
        subprocess.run([*cmd, IMAGE], check=True, capture_output=True, timeout=60)
        port = (
            subprocess.run(
                ['docker', 'port', name, '80/tcp'],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            .stdout.split(':')[-1]
            .strip()
        )
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'{base}/collections', timeout=3).status_code == 200:
                    return base
            except httpx.HTTPError:
                pass
            time.sleep(1)
        logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr
        raise AssertionError(f'pygeoapi did not start:\n{logs[-3000:]}')

    yield start
    for name in names:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=60)


def publish(service: Service, name: str) -> tuple[FormState, str]:
    form = FormState.model_validate(CASES[name]())
    exported = service.export(form, 't')
    assert not exported.validation.blocking, [f.message for f in exported.validation.findings]
    return form, exported.merged_yaml


@pytest.mark.parametrize('name', ['csv', 'sqlitegpkg', 'postgresql-fields'])
def test_pygeoapi_serves_the_export(service: Service, serve: callable, name: str) -> None:
    form, merged = publish(service, name)
    base = serve(merged, SECRETS)
    rid = form.resource_id
    collection = httpx.get(f'{base}/collections/{rid}?f=json', timeout=30)
    assert collection.status_code == 200, collection.text
    items = httpx.get(f'{base}/collections/{rid}/items?f=json&limit=2', timeout=30).json()
    assert items['type'] == 'FeatureCollection' and len(items['features']) == 2
    html = httpx.get(f'{base}/collections/{rid}/items?f=html&limit=2', timeout=30)
    assert html.status_code == 200
    jsonld = httpx.get(f'{base}/collections/{rid}/items?f=jsonld&limit=2', timeout=30)
    assert jsonld.status_code == 200 and '@context' in json.loads(jsonld.text)


def test_postgis_probe_and_wrong_password(service: Service) -> None:
    form = FormState.model_validate(CASES['postgresql-fields']())
    ok = service.probe(form, 0, SECRETS)
    assert ok['ok'], ok.get('explanation')
    assert ok['result']['sample']['numberMatched'] == 14776
    bad = service.probe(form, 0, {'PYGEOAPI_WATERWAYS_DB_PASSWORD': 'not-the-password'})
    assert not bad['ok']
    assert bad['explanation']['message'] == (
        'The database rejected the user name or password for user postgres.'
    )
    assert 'not-the-password' not in json.dumps(bad)


def test_postgis_introspection(service: Service) -> None:
    provider = FormState.model_validate(CASES['postgresql-fields']()).providers[0]
    result = service.introspect(provider, 'waterways', SECRETS)
    assert result['ok'], result.get('explanation')
    tables = {(t['schema'], t['name']): t for t in result['result']['tables']}
    assert ('osm', 'hotosm_bdi_drains') in tables  # a materialized view
    waterways = tables[('osm', 'hotosm_bdi_waterways')]
    assert [c['name'] for c in waterways['columns'] if c['geometry']] == ['foo_geom']
    assert ('public', 'geometry_columns') not in tables  # PostGIS bookkeeping
