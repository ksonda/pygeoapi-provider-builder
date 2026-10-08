from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from provider_builder.app import create_app
from provider_builder.catalog import Catalog
from provider_builder.runner import SubprocessRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target, Workspace

REPO = Path(__file__).resolve().parents[2]
PYGEOAPI_DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
FIXTURES = ['obs.csv', 'ne_110m_lakes.geojson', 'poi_portugal.gpkg']
BASE_CONFIG = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'


@pytest.fixture(scope='session')
def catalog() -> Catalog:
    return Catalog([REPO / 'catalog'])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / 'data'
    data.mkdir()
    for name in FIXTURES:
        shutil.copy(PYGEOAPI_DATA / name, data / name)
    config = tmp_path / 'pygeoapi.config.yml'
    shutil.copy(BASE_CONFIG, config)
    return Settings(
        root=tmp_path,
        data_roots=[DataRoot(name='test-data', host_path=data, server_path='/data')],
        targets=[
            Target(
                id='test',
                title='Test target',
                base_config=config,
                server_url='http://localhost:5080',
            )
        ],
        catalogs=[REPO / 'catalog'],
        sandbox=SandboxSettings(runner='subprocess', timeout_s=60, private_hosts=['127.0.0.1']),
        workspace=Workspace(host_path=tmp_path / 'workspace' / 'providers', server_path='/specs'),
        allowed_hosts=['localhost', '127.0.0.1'],
        allowed_origins=['http://localhost:5173'],
    )


@pytest.fixture
def service(settings: Settings) -> Service:
    return Service(
        settings,
        runner=SubprocessRunner(settings.sandbox, settings.data_roots, settings.workspace),
    )


@pytest.fixture
def client(service: Service) -> TestClient:
    app = create_app(service=service, check_sandbox=False)
    return TestClient(app, base_url='http://localhost', headers={'X-Provider-Builder': '1'})
