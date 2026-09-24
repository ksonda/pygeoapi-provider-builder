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
    """Where a resource is published. Phase 1 supports ``export`` only."""

    id: str
    title: str
    kind: Literal['export'] = 'export'
    base_config: Path = Field(description='The pygeoapi config the resource is merged into')
    server_url: str | None = Field(default=None, description='pygeoapi base URL, for links')


class SandboxSettings(BaseModel):
    runner: Literal['auto', 'docker', 'subprocess'] = 'auto'
    image: str = 'geopython/pygeoapi:0.24.0'
    network: str | None = Field(
        default=None, description='Docker network for sandbox runs (e.g. the dev compose network)'
    )
    timeout_s: int = 60
    memory: str = '1g'
    cpus: float = 1.0


class Settings(BaseModel):
    root: Path
    data_roots: list[DataRoot] = []
    targets: list[Target] = []
    catalogs: list[Path] = [Path('catalog')]
    sandbox: SandboxSettings = SandboxSettings()
    geoconnex: bool = True
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
        t['base_config'] = rel(t['base_config'])
    raw['catalogs'] = [rel(c) for c in raw.get('catalogs', ['catalog'])]
    return Settings(root=root, **raw)
