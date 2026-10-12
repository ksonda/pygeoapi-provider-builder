"""Publishing to a file target (PLAN.md §6.4): backups, atomic writes, reload, verify.

The order matters: OpenAPI is written before the config, because a server that
reloads on a config change reads whatever OpenAPI document is there
(docs/pygeoapi-notes.md §9.1). Every publish and rollback appends to a JSON
Lines publish log (ADR-0013); a rollback restores the backups it recorded.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import signal
import subprocess  # noqa: S404 - reload commands come from the settings file
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from .configedit import etag
from .settings import Settings, Target

Action = Literal['add', 'update', 'delete', 'rollback']
Status = Literal['published', 'written', 'verify-failed', 'failed']


class LogEntry(BaseModel):
    id: str
    time: str
    target: str
    action: Action
    resource_id: str
    providers: list[str] = []
    status: Status
    config_hash_before: str
    config_hash_after: str | None = None
    backup: str | None = None
    openapi_backup: str | None = None
    rollback_of: str | None = None
    message: str | None = None
    rolled_back_by: str | None = None


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return now().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]


class PublishLog:
    """Append-only JSON Lines; a rollback is a new entry pointing at the one it undid."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, entry: LogEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as f:
            f.write(entry.model_dump_json(exclude={'rolled_back_by'}) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def entries(self) -> list[LogEntry]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                out.append(LogEntry.model_validate_json(line))
        undone = {e.rollback_of: e.id for e in out if e.rollback_of}
        for e in out:
            e.rolled_back_by = undone.get(e.id)
        return out

    def get(self, entry_id: str) -> LogEntry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)


# ------------------------------------------------------------------ files


def atomic_write(path: Path, text: str) -> None:
    """Temp file in the same directory, fsync, then ``os.replace`` (keeps the file's mode)."""
    data = text.encode('utf-8')
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class Backups:
    """``<state>/backups/<target>/<name>.<UTC timestamp>.<hash>.<ext>``, keeping the last N."""

    def __init__(self, settings: Settings, target: Target):
        self.dir = settings.state_dir / 'backups' / target.id
        self.state_dir = settings.state_dir
        self.keep = max(1, target.keep_backups)

    def save(self, path: Path, text: str) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime('%Y%m%dT%H%M%S%fZ')
        name = f'{path.stem}.{stamp}.{etag(text)[:12]}{path.suffix or ".yml"}'
        dest = self.dir / name
        atomic_write(dest, text)
        self.prune(path)
        return dest.relative_to(self.state_dir).as_posix()

    def prune(self, path: Path) -> None:
        pattern = re.compile(re.escape(path.stem) + r'\.\d{8}T\d+Z\.[0-9a-f]{12}')
        mine = sorted(p for p in self.dir.iterdir() if pattern.match(p.name))
        for old in mine[: -self.keep]:
            old.unlink()

    def read(self, rel: str) -> str:
        path = (self.state_dir / rel).resolve()
        if not path.is_relative_to(self.state_dir.resolve()):
            raise ValueError('Backup path outside the state directory')
        return path.read_text(encoding='utf-8')


# ------------------------------------------------------------------ reload


class Step(BaseModel):
    name: str
    ok: bool
    message: str = ''
    detail: Any = None


def reload(settings: Settings, target: Target) -> Step:
    if target.reload == 'none':
        return Step(
            name='reload',
            ok=True,
            message=(
                'This target does not reload the server. Regenerate the OpenAPI document and '
                'restart pygeoapi to serve the change.'
            ),
        )
    if target.reload == 'watch':
        return Step(name='reload', ok=True, message='The server reloads itself on a change.')
    if target.reload == 'signal':
        if target.pid_file is None or not target.pid_file.is_file():
            return Step(
                name='reload', ok=False, message='The pid_file for this target is missing.'
            )
        try:
            pid = int(target.pid_file.read_text().strip())
            os.kill(pid, signal.SIGHUP)
        except (ValueError, OSError) as err:
            return Step(name='reload', ok=False, message=f'Could not signal the server: {err}')
        return Step(name='reload', ok=True, message=f'Sent HUP to process {pid}.')
    if not target.reload_command:
        return Step(name='reload', ok=False, message='This target has no reload_command.')
    try:
        proc = subprocess.run(  # noqa: S603 - argv from the settings file, no shell
            target.reload_command,
            cwd=settings.root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return Step(name='reload', ok=False, message=f'The reload command failed: {err}')
    output = (proc.stdout + proc.stderr)[-2000:]
    if proc.returncode != 0:
        return Step(
            name='reload',
            ok=False,
            message=f'The reload command exited with {proc.returncode}.',
            detail=output,
        )
    return Step(name='reload', ok=True, message=' '.join(target.reload_command), detail=output)


# ------------------------------------------------------------------ verify

DATA_PATHS = {
    'feature': '/items?f=json&limit=1',
    'record': '/items?f=json&limit=1',
    'tile': '/tiles?f=json',
}


def check_once(
    base: str, resource_id: str, expect: Literal['present', 'absent'], provider_type: str | None
) -> list[dict[str, Any]]:
    """One round of checks. ``/collections/{id}``, not ``/collections`` (notes §1.5)."""
    urls = [f'{base}/collections/{resource_id}?f=json']
    if expect == 'present' and provider_type in DATA_PATHS:
        urls.append(f'{base}/collections/{resource_id}{DATA_PATHS[provider_type]}')
    checks: list[dict[str, Any]] = []
    for url in urls:
        try:
            r = httpx.get(url, timeout=10)
        except httpx.HTTPError as err:
            checks.append(
                {'url': url, 'status': None, 'ok': False, 'detail': f"Couldn't reach it: {err}"}
            )
            continue
        detail = None
        if expect == 'absent':
            ok = r.status_code == 404
            detail = 'gone' if ok else 'still served'
        else:
            ok = r.status_code == 200
            if ok and url.endswith('limit=1'):
                try:
                    n = len(r.json().get('features', []))
                    detail = f'{n} feature(s) returned'
                except ValueError:
                    ok, detail = False, 'not JSON'
            elif not ok:
                detail = r.text[:300]
        checks.append({'url': url, 'status': r.status_code, 'ok': ok, 'detail': detail})
    return checks


def verify(
    target: Target,
    resource_id: str,
    expect: Literal['present', 'absent'],
    provider_type: str | None,
    timeout_s: float | None = None,
) -> Step:
    """Poll until the checks pass or the timeout (the server may be restarting)."""
    if not target.server_url:
        return Step(name='verify', ok=False, message='This target has no server URL.')
    base = target.server_url.rstrip('/')
    deadline = time.monotonic() + (target.verify_timeout_s if timeout_s is None else timeout_s)
    checks: list[dict[str, Any]] = []
    while True:
        checks = check_once(base, resource_id, expect, provider_type)
        if all(c['ok'] for c in checks) or time.monotonic() >= deadline:
            break
        time.sleep(1.5)
    ok = all(c['ok'] for c in checks)
    if ok:
        message = 'The server serves the change.'
    else:
        message = 'The server did not serve the change in time. You can roll back.'
    return Step(name='verify', ok=ok, message=message, detail=checks)
