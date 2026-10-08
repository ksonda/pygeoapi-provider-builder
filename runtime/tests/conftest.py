"""Fixtures for the spec engine: the mock API served in-process on a free port."""

from __future__ import annotations

import importlib.util
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from werkzeug.serving import make_server

# The mock API listens on loopback, which the egress policy refuses unless allowed.
os.environ['PYGEOAPI_SPEC_PRIVATE_HOSTS'] = '127.0.0.1'

REPO = Path(__file__).resolve().parents[2]


def load_mock_api() -> Any:
    spec = importlib.util.spec_from_file_location(
        'mock_api', REPO / 'docker' / 'mock-api' / 'mock_api.py'
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def mock_api() -> Iterator[str]:
    server = make_server('127.0.0.1', 0, load_mock_api().app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
