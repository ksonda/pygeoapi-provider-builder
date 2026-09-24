"""Data roots: browse them, and map server paths to host paths safely.

A path is accepted only if it lies inside a configured data root, both as
written (no ``..`` components) and after resolving symlinks on the host.
PROMPT.md §7: "Restrict file-based providers to configured data roots and
block path traversal."
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from pathlib import Path

from .settings import DataRoot, Settings

MAX_ENTRIES = 1000


class PathError(ValueError):
    """A path outside the data roots, or otherwise unusable."""


@dataclass(frozen=True)
class ResolvedPath:
    root: DataRoot
    server_path: str
    host_path: Path


def resolve_server_path(settings: Settings, server_path: str) -> ResolvedPath:
    """Map a server-side path to the host, refusing anything outside the data roots."""
    if not server_path.startswith('/'):
        raise PathError('Use an absolute path inside a data root, e.g. /data/file.csv')
    if '\x00' in server_path or any(part == '..' for part in server_path.split('/')):
        raise PathError('Paths may not contain ".." or NUL characters')
    clean = posixpath.normpath(server_path)
    for root in sorted(settings.data_roots, key=lambda r: len(r.server_path), reverse=True):
        base = root.server_path
        if clean != base and not clean.startswith(base.rstrip('/') + '/'):
            continue
        rel = clean[len(base) :].lstrip('/')
        host_root = root.host_path.resolve()
        host = (host_root / rel).resolve()
        if os.path.commonpath([host_root, host]) != str(host_root):
            raise PathError('That path leaves its data root (via a symbolic link)')
        return ResolvedPath(root=root, server_path=clean, host_path=host)
    roots = ', '.join(r.server_path for r in settings.data_roots) or '(none configured)'
    raise PathError(f'{server_path} is outside the data roots: {roots}')


def list_dir(settings: Settings, server_path: str) -> dict[str, object]:
    """List one directory inside a data root (server paths in, server paths out)."""
    resolved = resolve_server_path(settings, server_path)
    if not resolved.host_path.is_dir():
        raise PathError(f'{server_path} is not a directory')
    entries = []
    for entry in sorted(resolved.host_path.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        if entry.name.startswith('.'):
            continue
        try:
            target = resolve_server_path(
                settings, posixpath.join(resolved.server_path, entry.name)
            )
        except PathError:
            continue  # symlink pointing outside the root: don't show it
        is_dir = target.host_path.is_dir()
        entries.append(
            {
                'name': entry.name,
                'kind': 'dir' if is_dir else 'file',
                'size': None if is_dir else target.host_path.stat().st_size,
                'server_path': target.server_path,
            }
        )
        if len(entries) >= MAX_ENTRIES:
            break
    parent = None
    if resolved.server_path != resolved.root.server_path:
        parent = posixpath.dirname(resolved.server_path)
    return {
        'root': resolved.root.name,
        'server_path': resolved.server_path,
        'parent': parent,
        'entries': entries,
    }
