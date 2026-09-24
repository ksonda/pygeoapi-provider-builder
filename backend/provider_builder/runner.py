"""Run harness jobs in the sandbox (PLAN.md §8, ADR-0008).

``DockerRunner`` runs the worker in the pinned pygeoapi image: non-root,
read-only root filesystem, no capabilities, resource limits, data roots
mounted read-only at their *server* paths, and a hard timeout. Secrets
reach the container as environment variables passed by name, so they never
appear in process arguments. Egress control (the gateway) arrives in Phase 2;
until then the container uses the configured network, or none.

``SubprocessRunner`` runs the same worker with the local venv. It isolates
crashes and hangs, not untrusted code, and is used only when Docker is
unavailable or in tests.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import DataRoot, SandboxSettings

HARNESS_SRC = Path(__file__).resolve().parents[2] / 'harness' / 'src'
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


class Runner:
    name = 'base'

    def __init__(self, sandbox: SandboxSettings, data_roots: list[DataRoot]):
        self.sandbox = sandbox
        self.data_roots = data_roots

    def available(self) -> bool:
        return True

    def run(
        self,
        job: dict[str, Any],
        secrets: dict[str, str] | None = None,
        timeout_s: int | None = None,
    ) -> Outcome:
        raise NotImplementedError


class DockerRunner(Runner):
    name = 'docker'

    def __init__(self, sandbox: SandboxSettings, data_roots: list[DataRoot]):
        super().__init__(sandbox, data_roots)
        self.docker = shutil.which('docker') or 'docker'
        self._available: tuple[float, bool] | None = None

    def available(self) -> bool:
        now = time.monotonic()
        if self._available and now - self._available[0] < 30:
            return self._available[1]
        try:
            ok = (
                subprocess.run(  # noqa: S603
                    [self.docker, 'image', 'inspect', '--format', '{{.Id}}', self.sandbox.image],
                    capture_output=True,
                    timeout=10,
                    check=False,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        self._available = (now, ok)
        return ok

    def command(self, name: str, secret_names: list[str]) -> list[str]:
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
            '--cpus',
            str(s.cpus),
            '--network',
            s.network or 'none',
            '-v',
            f'{HARNESS_SRC}:/opt/harness:ro',
            '-e',
            'PYTHONPATH=/opt/harness',
            '-e',
            'HOME=/tmp',
            '-e',
            'HARNESS_LOG_LEVEL=INFO',
        ]
        for root in self.data_roots:
            cmd += ['-v', f'{root.host_path}:{root.server_path}:ro']
        for secret in secret_names:
            cmd += ['-e', secret]  # value comes from our environment, not argv
        cmd += [
            '--entrypoint',
            '/venv/bin/python3',
            s.image,
            '-m',
            'pygeoapi_provider_harness.worker',
        ]
        return cmd

    def run(
        self,
        job: dict[str, Any],
        secrets: dict[str, str] | None = None,
        timeout_s: int | None = None,
    ) -> Outcome:
        secrets = secrets or {}
        if not self.available():
            return Outcome(
                ok=False,
                runner=self.name,
                error=_error(
                    'SandboxUnavailable',
                    f'Docker or the image {self.sandbox.image} is not available',
                ),
            )
        name = f'pb-sandbox-{uuid.uuid4().hex[:12]}'
        job = {**job, 'protocol': PROTOCOL, 'secret_env': sorted(secrets)}
        env = {**os.environ, **secrets}
        timeout = timeout_s or self.sandbox.timeout_s
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(  # noqa: S603
                self.command(name, sorted(secrets)),
                input=json.dumps(job),
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(  # noqa: S603
                [self.docker, 'kill', name],
                capture_output=True,
                timeout=15,
                check=False,
            )
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
    ) -> Outcome:
        secrets = secrets or {}
        path_map = [{'server': r.server_path, 'host': str(r.host_path)} for r in self.data_roots]
        job = {**job, 'protocol': PROTOCOL, 'secret_env': sorted(secrets), 'path_map': path_map}
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ')}
        env.update(secrets)
        env['HARNESS_LOG_LEVEL'] = 'INFO'
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


def make_runner(sandbox: SandboxSettings, data_roots: list[DataRoot]) -> Runner:
    if sandbox.runner == 'subprocess':
        return SubprocessRunner(sandbox, data_roots)
    docker = DockerRunner(sandbox, data_roots)
    if sandbox.runner == 'docker' or docker.available():
        return docker
    return SubprocessRunner(sandbox, data_roots)
