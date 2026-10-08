"""Contract tests in the pinned image (PLAN.md §12).

Every generated resource, merged into the dev-stack base config, must pass
pygeoapi's own ``config validate`` and ``openapi generate`` (fail flag on, so
providers are loaded) inside ``geopython/pygeoapi:0.24.0``. The Docker runner
must also probe providers inside that image with the sandbox restrictions on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from dockerutil import IMAGE, REPO
from forms import CASES
from provider_builder.render import FormState
from provider_builder.runner import DockerRunner
from provider_builder.service import Service
from provider_builder.settings import DataRoot, SandboxSettings, Settings, Target

pytestmark = pytest.mark.docker

DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'
BASE = REPO / 'backend' / 'tests' / 'fixtures' / 'pygeoapi.config.yml'
FILE_CASES = ['csv', 'geojson', 'sqlitegpkg']


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
        sandbox=SandboxSettings(runner='docker'),
    )
    return Service(settings, runner=DockerRunner(settings.sandbox, settings.data_roots))


def pygeoapi_cli(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            'docker',
            'run',
            '--rm',
            '--user',
            '10001:0',
            '--read-only',
            '--tmpfs',
            '/tmp',
            '--network',
            'none',
            '-v',
            f'{workdir / "config"}:/config:ro',
            '-v',
            f'{workdir / "data"}:/data',
            '-e',
            'PYGEOAPI_DRAINS_DB_DSN=x',
            '--entrypoint',
            '/venv/bin/pygeoapi',
            IMAGE,
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize('name', FILE_CASES)
def test_merged_config_passes_pygeoapi_itself(service: Service, workdir: Path, name: str) -> None:
    form = FormState.model_validate(CASES[name]())
    result = service.validate(form, 't')
    assert not result.blocking, [f.message for f in result.findings]
    (workdir / 'config' / 'pygeoapi.config.yml').write_text(result.merged_yaml or '')

    validate = pygeoapi_cli(workdir, 'config', 'validate', '-c', '/config/pygeoapi.config.yml')
    assert validate.returncode == 0, validate.stdout + validate.stderr
    assert 'Valid configuration' in validate.stdout

    generate = pygeoapi_cli(
        workdir,
        'openapi',
        'generate',
        '/config/pygeoapi.config.yml',
        '--output-file',
        '/tmp/openapi.yml',
    )
    assert generate.returncode == 0, generate.stderr[-2000:]


def test_generate_fails_for_a_broken_provider(service: Service, workdir: Path) -> None:
    """The smoke test really loads providers (notes §6.2): a missing file fails it."""
    form = CASES['csv']()
    form['providers'][0]['values']['data'] = '/data/missing.csv'
    merged = service.merged_text(FormState.model_validate(form), 't')
    (workdir / 'config' / 'pygeoapi.config.yml').write_text(merged)
    generate = pygeoapi_cli(
        workdir,
        'openapi',
        'generate',
        '/config/pygeoapi.config.yml',
        '--output-file',
        '/tmp/openapi.yml',
    )
    assert generate.returncode != 0 and 'No such file or directory' in generate.stderr


def test_openapi_check_job_matches_the_cli(service: Service) -> None:
    ok = service.openapi_check(FormState.model_validate(CASES['csv']()), 't', {})
    assert ok['ok'] and '/collections/obs/items' in ok['result']['paths']
    broken = CASES['csv']()
    broken['providers'][0]['values']['data'] = '/data/missing.csv'
    bad = service.openapi_check(FormState.model_validate(broken), 't', {})
    assert not bad['ok']
    assert bad['explanation']['message'] == 'No file at /data/missing.csv, as the server sees it.'


@pytest.mark.parametrize('name', FILE_CASES)
def test_docker_probe(service: Service, name: str) -> None:
    result = service.probe(FormState.model_validate(CASES[name]()), 0, {})
    assert result['ok'], result.get('explanation')
    assert result['runner'] == 'docker'
    assert result['result']['sample']['features']


def test_gpkg_probe_leaves_the_file_untouched(service: Service, workdir: Path) -> None:
    gpkg = workdir / 'data' / 'poi_portugal.gpkg'
    before = gpkg.read_bytes()
    assert service.probe(FormState.model_validate(CASES['sqlitegpkg']()), 0, {})['ok']
    assert gpkg.read_bytes() == before


def test_sandbox_is_non_root_and_read_only(service: Service) -> None:
    runner = service.runner
    assert isinstance(runner, DockerRunner)
    cmd = runner.command('x', [])
    for flag in ('--read-only', '--cap-drop', 'no-new-privileges', '--pids-limit', '--memory'):
        assert flag in cmd
    assert cmd[cmd.index('--user') + 1] == '10001:0'
    assert all(':ro' in cmd[i + 1] for i, c in enumerate(cmd) if c == '-v')


def test_catalog_availability_in_the_image(service: Service) -> None:
    service.check_sandbox()
    assert service.sandbox_versions == {'pygeoapi': '0.24.0', 'python': '3.12.3'}
    assert all(c['availability']['status'] == 'ready' for c in service.cards())
