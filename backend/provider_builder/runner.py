"""Run harness jobs in the sandbox (PLAN.md §8, ADR-0008).

``DockerRunner`` runs the worker in the sandbox image (the pinned pygeoapi
image plus the spec engine's dependencies): non-root, read-only root
filesystem, no capabilities, resource limits, data roots and the workspace
mounted read-only at their *server* paths, and a hard timeout. Secrets reach
the container as environment variables passed by name, so they never appear
in process arguments.

Network: a job with no egress gets ``--network none``. Otherwise each run gets
its own ``--internal`` network (no route out) and one egress gateway per
declared host (``docker/gateway/gateway.py``). The gateway vets the addresses
it resolves, refusing link-local and metadata addresses, and private ones
unless the admin allowlist names the host. The sandbox reaches it through
``--add-host``, so an undeclared name doesn't even resolve.

``SubprocessRunner`` runs the same worker with the local venv. It isolates
crashes and hangs, not untrusted code, and is used only when Docker is
unavailable or in tests. It remaps the server paths a job's config names to
host paths, but not paths inside a spec (a SQLite file), so with it a spec must
name its file by a path that exists locally.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .egress import HostPort
from .settings import DataRoot, SandboxSettings, Workspace

REPO = Path(__file__).resolve().parents[2]
HARNESS_SRC = REPO / 'harness' / 'src'
RUNTIME_SRC = REPO / 'runtime' / 'src'
GATEWAY = REPO / 'docker' / 'gateway' / 'gateway.py'
GATEWAY_READY_S = 15
MAX_OUTPUT = 20 * 1024 * 1024
MAX_LOG = 20_000
PROTOCOL = 1


@dataclass
class Outcome:
    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    logs: str = ''
    runner: str = ''
    ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _error(kind: str, message: str) -> dict[str, Any]:
    return {
        'type': kind,
        'message': message,
        'user_msg': None,
        'chain': [{'type': kind, 'message': message}],
        'traceback': '',
    }


def _redact(text: str, secrets: dict[str, str]) -> str:
    for value in sorted((v for v in secrets.values() if v and len(v) >= 3), key=len, reverse=True):
        text = text.replace(value, '***')
    return text


def _parse(stdout: str, stderr: str, secrets: dict[str, str], runner: str, t0: float) -> Outcome:
    ms = round((time.perf_counter() - t0) * 1000, 1)
    logs = _redact(stderr, secrets)[-MAX_LOG:]
    try:
        payload = json.loads(_redact(stdout, secrets))
    except json.JSONDecodeError:
        return Outcome(
            ok=False,
            error=_error('SandboxError', 'The sandbox returned no result.'),
            logs=logs,
            runner=runner,
            ms=ms,
        )
    return Outcome(
        ok=bool(payload.get('ok')),
        result=payload.get('result'),
        error=payload.get('error'),
        logs=logs,
        runner=runner,
        ms=ms,
    )


class EgressSetupError(RuntimeError):
    """The per-run network or a gateway couldn't be started."""


class Runner:
    name = 'base'

    def __init__(
        self,
        sandbox: SandboxSettings,
        data_roots: list[DataRoot],
        workspace: Workspace | None = None,
    ):
        self.sandbox = sandbox
        self.data_roots = data_roots
        self.workspace = workspace

    def available(self) -> bool:
        return True

    def path_map(self) -> list[dict[str, str]]:
        mapping = [{'server': r.server_path, 'host': str(r.host_path)} for r in self.data_roots]
        if self.workspace:
            mapping.append(
                {'server': self.workspace.server_path, 'host': str(self.workspace.host_path)}
            )
        return mapping

    def run(
        self,
        job: dict[str, Any],
        secrets: dict[str, str] | None = None,
        timeout_s: int | None = None,
        egress: Iterable[HostPort] = (),
    ) -> Outcome:
        raise NotImplementedError


class DockerRunner(Runner):
    name = 'docker'

    def __init__(
        self,
        sandbox: SandboxSettings,
        data_roots: list[DataRoot],
        workspace: Workspace | None = None,
    ):
        super().__init__(sandbox, data_roots, workspace)
        self.docker = shutil.which('docker') or 'docker'
        self.image = sandbox.resolved_image()
        self._available: tuple[float, bool] | None = None

    def available(self) -> bool:
        now = time.monotonic()
        if self._available and now - self._available[0] < 30:
            return self._available[1]
        try:
            ok = self._docker('image', 'inspect', '--format', '{{.Id}}', self.image, timeout=10)
            available = bool(ok)
        except EgressSetupError:
            available = False
        self._available = (now, available)
        return available

    def _docker(self, *args: str, timeout: float = 30, check: bool = True) -> str:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed docker arguments
                [self.docker, *args], capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            raise EgressSetupError(f'docker {args[0]} failed: {err}') from err
        if check and proc.returncode != 0:
            raise EgressSetupError(f'docker {args[0]} failed: {proc.stderr.strip()[:500]}')
        return proc.stdout

    def command(
        self,
        name: str,
        secret_names: list[str],
        network: str = 'none',
        add_hosts: Iterable[tuple[str, str]] = (),
        env: dict[str, str] | None = None,
    ) -> list[str]:
        s = self.sandbox
        cmd = [
            self.docker,
            'run',
            '--rm',
            '-i',
            '--name',
            name,
            '--user',
            '10001:0',
            '--read-only',
            '--tmpfs',
            '/tmp:rw,size=512m',  # noqa: S108 (container path)
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--pids-limit',
            '256',
            '--memory',
            s.memory,
            '--memory-swap',  # the same: no swap, so the limit doesn't depend on the host's
            s.memory,
            '--cpus',
            str(s.cpus),
            '--network',
            network,
            '-v',
            f'{HARNESS_SRC}:/opt/harness:ro',
            '-v',
            f'{RUNTIME_SRC}:/opt/runtime:ro',
            '-e',
            'PYTHONPATH=/opt/harness:/opt/runtime',
            '-e',
            'HOME=/tmp',
            '-e',
            'HARNESS_LOG_LEVEL=INFO',
        ]
        for host, address in add_hosts:
            cmd += ['--add-host', f'{host}:{address}']
        for key, value in (env or {}).items():
            cmd += ['-e', f'{key}={value}']
        for root in self.data_roots:
            cmd += ['-v', f'{root.host_path}:{root.server_path}:ro']
        if self.workspace and self.workspace.host_path.is_dir():
            cmd += ['-v', f'{self.workspace.host_path}:{self.workspace.server_path}:ro']
        for secret in secret_names:
            cmd += ['-e', secret]  # value comes from our environment, not argv
        cmd += [
            '--entrypoint',
            '/venv/bin/python3',
            self.image,
            '-m',
            'pygeoapi_provider_harness.worker',
        ]
        return cmd

    @contextmanager
    def egress_network(
        self, targets: list[HostPort], run_id: str
    ) -> Iterator[tuple[str, list[tuple[str, str]], list[dict[str, Any]]]]:
        """An internal network plus one gateway per declared host, removed afterwards."""
        network = f'pb-net-{run_id}'
        gateways: list[str] = []
        events: list[dict[str, Any]] = []
        by_host: dict[str, list[int]] = {}
        for t in targets:
            by_host.setdefault(t.host, []).append(t.port)
        try:
            self._docker('network', 'create', '--internal', network)
            add_hosts = []
            for i, (host, ports) in enumerate(sorted(by_host.items())):
                name = f'pb-gw-{run_id}-{i}'
                listen = [
                    f'{p}:private' if self.sandbox.private(host, p) else str(p)
                    for p in sorted(set(ports))
                ]
                self._docker(
                    'run',
                    '-d',
                    '--rm',
                    '--name',
                    name,
                    '--network',
                    network,
                    '--read-only',
                    '--cap-drop',
                    'ALL',
                    '--security-opt',
                    'no-new-privileges',
                    '--user',
                    '10001:0',
                    '--sysctl',
                    'net.ipv4.ip_unprivileged_port_start=0',
                    '--memory',
                    '64m',
                    '--memory-swap',
                    '64m',
                    '--pids-limit',
                    '64',
                    '--cpus',
                    '0.5',
                    '-v',
                    f'{GATEWAY}:/opt/gateway/gateway.py:ro',
                    '--entrypoint',
                    '/venv/bin/python3',
                    self.image,
                    '/opt/gateway/gateway.py',
                    host,
                    *listen,
                )
                gateways.append(name)
                self._wait_ready(name)
                self._docker('network', 'connect', self.sandbox.egress_network or 'bridge', name)
                address = self._docker(
                    'inspect',
                    '-f',
                    f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}',
                    name,
                ).strip()
                add_hosts.append((host, address))
            yield network, add_hosts, events
        finally:
            for name in gateways:
                try:
                    for line in self._docker('logs', name, check=False).splitlines():
                        event = json.loads(line) if line.startswith('{') else {}
                        if event.get('event') in ('refused', 'unreachable'):
                            events.append(event)
                except (EgressSetupError, ValueError):
                    pass
                self._docker('rm', '-f', name, check=False)
            self._docker('network', 'rm', network, check=False)

    def _wait_ready(self, name: str) -> None:
        deadline = time.monotonic() + GATEWAY_READY_S
        while time.monotonic() < deadline:
            if '"event": "ready"' in self._docker('logs', name, check=False):
                return
            time.sleep(0.1)
        raise EgressSetupError(f'The egress gateway {name} did not start')

    def run(
        self,
        job: dict[str, Any],
        secrets: dict[str, str] | None = None,
        timeout_s: int | None = None,
        egress: Iterable[HostPort] = (),
    ) -> Outcome:
        secrets = secrets or {}
        if not self.available():
            return Outcome(
                ok=False,
                runner=self.name,
                error=_error(
                    'SandboxUnavailable',
                    f'Docker or the sandbox image {self.image} is not available (make images)',
                ),
            )
        run_id = uuid.uuid4().hex[:12]
        targets = sorted(set(egress))
        if not targets:
            return self._run(f'pb-sandbox-{run_id}', job, secrets, timeout_s)
        try:
            with self.egress_network(targets, run_id) as (network, add_hosts, events):
                outcome = self._run(
                    f'pb-sandbox-{run_id}',
                    job,
                    secrets,
                    timeout_s,
                    network,
                    add_hosts,
                    {'PYGEOAPI_SPEC_PRIVATE_HOSTS': '*'},  # the gateway enforces the policy
                )
        except EgressSetupError as err:
            return Outcome(
                ok=False, runner=self.name, error=_error('SandboxEgressError', str(err))
            )
        outcome.extra['egress'] = [str(t) for t in targets]
        if events:
            outcome.extra['egress_refused'] = events
            notes = '\n'.join(f'egress gateway: {e}' for e in events)
            outcome.logs = (outcome.logs + '\n' + notes).strip()
        return outcome

    def _run(
        self,
        name: str,
        job: dict[str, Any],
        secrets: dict[str, str],
        timeout_s: int | None,
        network: str = 'none',
        add_hosts: Iterable[tuple[str, str]] = (),
        env: dict[str, str] | None = None,
    ) -> Outcome:
        job = {**job, 'protocol': PROTOCOL, 'secret_env': sorted(secrets)}
        timeout = timeout_s or self.sandbox.timeout_s
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(  # noqa: S603
                self.command(name, sorted(secrets), network, add_hosts, env),
                input=json.dumps(job),
                text=True,
                capture_output=True,
                timeout=timeout,
                env={**os.environ, **secrets},
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._docker('kill', name, check=False, timeout=15)
            return Outcome(
                ok=False,
                runner=self.name,
                extra={'timeout': timeout},
                error=_error('SandboxTimeout', f'Stopped after {timeout} s'),
            )
        return _parse(proc.stdout[:MAX_OUTPUT], proc.stderr, secrets, self.name, t0)


class SubprocessRunner(Runner):
    name = 'subprocess'

    def run(
        self,
        job: dict[str, Any],
        secrets: dict[str, str] | None = None,
        timeout_s: int | None = None,
        egress: Iterable[HostPort] = (),
    ) -> Outcome:
        """No network isolation here: egress is checked only by the spec engine itself."""
        secrets = secrets or {}
        job = {
            **job,
            'protocol': PROTOCOL,
            'secret_env': sorted(secrets),
            'path_map': self.path_map(),
        }
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ')}
        env.update(secrets)
        env['HARNESS_LOG_LEVEL'] = 'INFO'
        env['PYGEOAPI_SPEC_PRIVATE_HOSTS'] = ','.join(
            sorted({h.split(':')[0] for h in self.sandbox.private_hosts})
        )
        timeout = timeout_s or self.sandbox.timeout_s
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='pb-worker-') as cwd:
            try:
                proc = subprocess.run(  # noqa: S603
                    [sys.executable, '-m', 'pygeoapi_provider_harness.worker'],
                    input=json.dumps(job),
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    env=env,
                    cwd=cwd,
                    check=False,
                    start_new_session=True,
                )
            except subprocess.TimeoutExpired:
                return Outcome(
                    ok=False,
                    runner=self.name,
                    extra={'timeout': timeout},
                    error=_error('SandboxTimeout', f'Stopped after {timeout} s'),
                )
        return _parse(proc.stdout[:MAX_OUTPUT], proc.stderr, secrets, self.name, t0)


def make_runner(
    sandbox: SandboxSettings, data_roots: list[DataRoot], workspace: Workspace | None = None
) -> Runner:
    if sandbox.runner == 'subprocess':
        return SubprocessRunner(sandbox, data_roots, workspace)
    docker = DockerRunner(sandbox, data_roots, workspace)
    if sandbox.runner == 'docker' or docker.available():
        return docker
    return SubprocessRunner(sandbox, data_roots, workspace)
