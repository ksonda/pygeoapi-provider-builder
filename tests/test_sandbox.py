"""The sandbox holds against a provider that tries to escape (PLAN.md §8.2).

A hostile provider arrives as a job file, as code-built providers will. Its
constructor tries each escape and reports what happened through its fields,
which a probe returns. The runner must stop: undeclared hosts,
host.docker.internal, the metadata address, the Docker socket, host files,
writes to read-only mounts and the root filesystem, fork bombs, memory
hogs and sleeping past the timeout. Declared egress goes through the gateway,
which still refuses private addresses that the allowlist doesn't name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dockerutil import NETWORK, REPO
from provider_builder.egress import HostPort
from provider_builder.runner import DockerRunner
from provider_builder.settings import DataRoot, SandboxSettings

pytestmark = pytest.mark.docker

HOSTILE = """
import os, socket, time, urllib.request
from pygeoapi.provider.base import BaseProvider

def attempt(fn):
    try:
        fn()
        return 'ALLOWED'
    except Exception as err:
        return f'blocked: {type(err).__name__}'

def fork_bomb():
    children = []
    try:
        for _ in range(1000):
            pid = os.fork()
            if pid == 0:
                time.sleep(30)
                os._exit(0)
            children.append(pid)
        return 'ALLOWED'
    except OSError as err:
        return f'blocked after {len(children)} processes: {type(err).__name__}'
    finally:
        for pid in children:
            os.kill(pid, 9)

class Probe(BaseProvider):
    def __init__(self, provider_def):
        super().__init__(provider_def)
        action = provider_def.get('action', 'escape')
        if action == 'sleep':
            time.sleep(600)
        if action == 'memory':
            blob = bytearray(3 * 1024 ** 3)
        if action == 'fetch':
            urllib.request.urlopen(provider_def['url'], timeout=10).read()
        results = {
            'undeclared_host': attempt(
                lambda: urllib.request.urlopen('https://example.org', timeout=5)),
            'host_docker_internal': attempt(
                lambda: socket.create_connection(('host.docker.internal', 80), timeout=5)),
            'metadata': attempt(
                lambda: socket.create_connection(('169.254.169.254', 80), timeout=5)),
            'docker_socket': attempt(
                lambda: socket.socket(socket.AF_UNIX).connect('/var/run/docker.sock')),
            'host_files': attempt(lambda: open(provider_def['host_file']).read()),
            'write_data_root': attempt(lambda: open('/data/hostile.txt', 'w').write('x')),
            'write_root_fs': attempt(lambda: open('/pygeoapi/hostile.txt', 'w').write('x')),
            'fork_bomb': fork_bomb() if action == 'escape' else 'not tried',
            'uid': str(os.getuid()),
        }
        self._fields = {k: {'type': 'string', 'title': v} for k, v in results.items()}

    def get_fields(self):
        return self._fields

    def query(self, **kwargs):
        return None  # the probe asks a map provider for an image; this one has none
"""


def resource(**options: Any) -> str:
    lines = [
        'hostile:',
        '  type: collection',
        '  providers:',
        '    - type: map',
        '      name: hostile.Probe',
        '      data: unused',
    ]
    lines += [f'      {k}: {v}' for k, v in options.items()]
    return '\n'.join(lines) + '\n'


@pytest.fixture
def runner(tmp_path: Path) -> DockerRunner:
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'readme.txt').write_text('data root')
    sandbox = SandboxSettings(
        runner='docker', timeout_s=60, egress_network=NETWORK, private_hosts=['mockapi']
    )
    roots = [DataRoot(name='d', host_path=tmp_path / 'data', server_path='/data')]
    return DockerRunner(sandbox, roots)


def probe(
    runner: DockerRunner,
    timeout_s: int | None = None,
    egress: tuple[HostPort, ...] = (),
    **options: Any,
) -> Any:
    job = {
        'kind': 'probe',
        'resource_yaml': resource(**options),
        'files': {'hostile.py': HOSTILE},
        'python_path': ['@files@'],
    }
    return runner.run(job, timeout_s=timeout_s, egress=egress)


def results(outcome: Any) -> dict[str, str]:
    assert outcome.ok, outcome.error
    return {k: v['title'] for k, v in outcome.result['fields'].items()}


def test_every_escape_is_blocked(runner: DockerRunner) -> None:
    got = results(probe(runner, host_file=str(REPO / 'README.md')))
    assert got['uid'] == '10001'
    allowed = {k: v for k, v in got.items() if v == 'ALLOWED'}
    assert allowed == {}, allowed
    assert got['fork_bomb'].startswith('blocked after')


def test_declared_egress_goes_through_the_gateway(runner: DockerRunner) -> None:
    """Declared and allowlisted: reachable, while everything else stays blocked."""
    outcome = probe(
        runner,
        egress=(HostPort('mockapi', 8000),),
        action='fetch',
        url='http://mockapi:8000/health',
        host_file='/nonexistent',
    )
    got = results(outcome)
    assert got['undeclared_host'].startswith('blocked')
    assert got['metadata'].startswith('blocked')
    assert outcome.extra['egress'] == ['mockapi:8000']


def test_the_gateway_refuses_private_hosts_off_the_allowlist(runner: DockerRunner) -> None:
    runner.sandbox = runner.sandbox.model_copy(update={'private_hosts': []})
    outcome = probe(
        runner,
        egress=(HostPort('mockapi', 8000),),
        action='fetch',
        url='http://mockapi:8000/health',
        host_file='/nonexistent',
    )
    assert not outcome.ok
    refused = outcome.extra['egress_refused']
    assert refused[0]['event'] == 'refused'
    assert refused[0]['reason'] == 'non-public address, not on the allowlist'
    assert 'egress gateway' in outcome.logs


def test_sleeping_past_the_timeout_is_stopped(runner: DockerRunner) -> None:
    outcome = probe(runner, timeout_s=8, action='sleep', host_file='/nonexistent')
    assert not outcome.ok
    assert outcome.error['type'] == 'SandboxTimeout'


def test_a_memory_hog_is_killed_cleanly(runner: DockerRunner) -> None:
    outcome = probe(runner, action='memory', host_file='/nonexistent')
    assert not outcome.ok
    assert outcome.error['type'] in ('SandboxError', 'MemoryError')


def test_no_gateway_or_network_is_left_behind(runner: DockerRunner) -> None:
    import subprocess

    probe(runner, egress=(HostPort('mockapi', 8000),), host_file='/nonexistent')
    names = subprocess.run(
        ['docker', 'ps', '-a', '--format', '{{.Names}}'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    networks = subprocess.run(
        ['docker', 'network', 'ls', '--format', '{{.Name}}'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert 'pb-gw-' not in names and 'pb-sandbox-' not in names
    assert 'pb-net-' not in networks
