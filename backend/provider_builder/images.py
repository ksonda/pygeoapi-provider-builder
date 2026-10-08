"""The sandbox image: the pinned pygeoapi image plus the spec engine's dependencies.

Its tag is a hash of ``docker/sandbox``'s Dockerfile and requirements, so a
changed recipe never runs with a stale image. ``tools/images.py`` (``make
images``) builds it; the dev stack's pygeoapi runs the same image.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SANDBOX_DIR = REPO / 'docker' / 'sandbox'
BASE_IMAGE = 'geopython/pygeoapi:0.24.0'
#: A stable name for the current build, so plain `docker compose` commands work.
STABLE_TAG = 'provider-builder/sandbox:0.24.0'


def sandbox_tag() -> str:
    digest = hashlib.sha256()
    for name in ('Dockerfile', 'requirements.txt'):
        digest.update(name.encode())
        digest.update((SANDBOX_DIR / name).read_bytes())
    return f'provider-builder/sandbox:0.24.0-{digest.hexdigest()[:12]}'


def image_exists(tag: str, docker: str | None = None) -> bool:
    docker = docker or shutil.which('docker') or 'docker'
    try:
        return (
            subprocess.run(  # noqa: S603 - fixed arguments
                [docker, 'image', 'inspect', '--format', '{{.Id}}', tag],
                capture_output=True,
                timeout=10,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_sandbox(docker: str | None = None) -> str:
    """Build the sandbox image if it's missing; returns its tag. Needs network (PyPI)."""
    docker = docker or shutil.which('docker') or 'docker'
    tag = sandbox_tag()
    if not image_exists(tag, docker):
        subprocess.run([docker, 'build', '-t', tag, str(SANDBOX_DIR)], check=True)  # noqa: S603
    subprocess.run([docker, 'tag', tag, STABLE_TAG], check=True)  # noqa: S603
    return tag
