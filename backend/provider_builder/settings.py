"""Settings, loaded from a YAML file (default: ``provider-builder.yaml``).

Relative paths in the file are resolved against the file's directory.
Settings are read-only in the v1 UI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_SETTINGS_FILE = 'provider-builder.yaml'
ENV_SETTINGS_FILE = 'PROVIDER_BUILDER_SETTINGS'


class DataRoot(BaseModel):
    """A directory of data files, as the builder and the pygeoapi server see it."""

    name: str
    host_path: Path = Field(description='Path on the machine running the builder')
    server_path: str = Field(description='Absolute path of the same directory on the server')

    @field_validator('server_path')
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith('/'):
            raise ValueError('server_path must be an absolute POSIX path')
        return v.rstrip('/') or '/'


class Target(BaseModel):
    """Where a resource is published (PLAN.md §6.4).

    - ``export``: copy or download YAML; ``base_config`` is only read.
    - ``file``: ``base_config`` is the server's config, edited in place:
      OpenAPI first, backups, atomic writes, then reload and verify.
    - ``admin``: the server's Admin API (guarded, ADR-0015); ``base_config`` is
      not used for writes.
    """

    id: str
    title: str
    kind: Literal['export', 'file', 'admin'] = 'export'
    base_config: Path = Field(description='The pygeoapi config (merged into, or edited)')
    server_url: str | None = Field(default=None, description='pygeoapi base URL')
    openapi: Path | None = Field(
        default=None,
        description=(
            'file: the OpenAPI document the server reads (PYGEOAPI_OPENAPI), written before '
            'the config. Leave empty when the server regenerates it at start (the Docker image)'
        ),
    )
    reload: Literal['none', 'command', 'signal', 'watch'] = Field(
        default='none',
        description=(
            'How the server picks up a new config: none (show instructions), command (run '
            'reload_command), signal (HUP the pid in pid_file) or watch (the server reloads '
            'itself, e.g. gunicorn --reload --reload-extra-file)'
        ),
    )
    reload_command: list[str] = Field(default=[], description='argv, run in the settings dir')
    pid_file: Path | None = None
    verify_timeout_s: int = 90
    keep_backups: int = 20
    admin_url: str | None = Field(
        default=None, description='admin: the Admin API base; default <server_url>/admin'
    )


class SandboxSettings(BaseModel):
    runner: Literal['auto', 'docker', 'subprocess'] = 'auto'
    image: str | None = Field(
        default=None, description='Sandbox image; empty means the one built from docker/sandbox'
    )
    egress_network: str | None = Field(
        default=None,
        description='Docker network the egress gateway joins (e.g. the dev stack); default bridge',
    )
    private_hosts: list[str] = Field(
        default=[],
        description=(
            'Hosts (or host:port) that may resolve to non-public addresses: the admin '
            'allowlist for egress to private networks (PLAN.md §8.2)'
        ),
    )
    timeout_s: int = 60
    memory: str = '1g'
    cpus: float = 1.0

    def resolved_image(self) -> str:
        from .images import sandbox_tag

        return self.image or sandbox_tag()

    def private(self, host: str, port: int) -> bool:
        return host in self.private_hosts or f'{host}:{port}' in self.private_hosts


class Workspace(BaseModel):
    """Where created providers live, and where the pygeoapi server sees their specs."""

    host_path: Path = Path('workspace/providers')
    server_path: str = '/specs'


class Settings(BaseModel):
    root: Path
    data_roots: list[DataRoot] = []
    targets: list[Target] = []
    catalogs: list[Path] = [Path('catalog')]
    sandbox: SandboxSettings = SandboxSettings()
    workspace: Workspace = Workspace()
    geoconnex: bool = True
    state_dir: Path = Field(
        default=Path('.provider-builder'),
        description='Backups and the publish log (ADR-0013)',
    )
    allowed_hosts: list[str] = ['127.0.0.1', 'localhost']
    allowed_origins: list[str] = []

    def target(self, target_id: str) -> Target:
        for t in self.targets:
            if t.id == target_id:
                return t
        raise KeyError(target_id)


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from ``path``, ``$PROVIDER_BUILDER_SETTINGS`` or the default file."""
    if path is None:
        path = Path(os.environ.get(ENV_SETTINGS_FILE, DEFAULT_SETTINGS_FILE))
    path = path.resolve()
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    root = path.parent

    def rel(p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (root / p).resolve()

    for dr in raw.get('data_roots', []):
        dr['host_path'] = rel(dr['host_path'])
    for t in raw.get('targets', []):
        for key in ('base_config', 'openapi', 'pid_file'):
            if t.get(key):
                t[key] = rel(t[key])
    raw['state_dir'] = rel(raw.get('state_dir', '.provider-builder'))
    raw['catalogs'] = [rel(c) for c in raw.get('catalogs', ['catalog'])]
    workspace = raw.setdefault('workspace', {})
    workspace['host_path'] = rel(workspace.get('host_path', 'workspace/providers'))
    return Settings(root=root, **raw)
