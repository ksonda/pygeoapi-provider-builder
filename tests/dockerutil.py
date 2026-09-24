"""Constants for the Docker-based suites."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE = 'geopython/pygeoapi:0.24.0'
NETWORK = 'provider-builder-dev'
