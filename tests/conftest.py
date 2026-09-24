"""Shared helpers for the Docker-based suites (markers: docker, compose)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'tests'))

from dockerutil import IMAGE, NETWORK  # noqa: E402


def _ok(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker_ready() -> bool:
    return bool(shutil.which('docker')) and _ok(['docker', 'image', 'inspect', IMAGE])


def compose_ready() -> bool:
    return (
        docker_ready()
        and _ok(['docker', 'network', 'inspect', NETWORK])
        and _ok(
            ['docker', 'exec', 'provider-builder-dev-postgis-1', 'pg_isready', '-U', 'postgres']
        )
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_docker = pytest.mark.skip(reason=f'needs Docker and {IMAGE} (docker pull {IMAGE})')
    skip_compose = pytest.mark.skip(reason='needs the dev stack (make stack-up)')
    has_docker, has_compose = docker_ready(), None
    for item in items:
        if 'compose' in item.keywords:
            if has_compose is None:
                has_compose = compose_ready()
            if not has_compose:
                item.add_marker(skip_compose)
        elif 'docker' in item.keywords and not has_docker:
            item.add_marker(skip_docker)
