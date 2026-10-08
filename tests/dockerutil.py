"""Constants for the Docker-based suites."""

from __future__ import annotations

from pathlib import Path

from provider_builder.images import sandbox_tag

REPO = Path(__file__).resolve().parents[1]
IMAGE = 'geopython/pygeoapi:0.24.0'
SANDBOX = sandbox_tag()  # make images
NETWORK = 'provider-builder-dev'
