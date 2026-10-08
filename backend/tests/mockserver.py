"""The mock API (docker/mock-api/mock_api.py) on a free loopback port, for API tests."""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from werkzeug.serving import make_server

MOCK_API = Path(__file__).resolve().parents[2] / 'docker' / 'mock-api' / 'mock_api.py'


@contextmanager
def serve_mock_api() -> Iterator[str]:
    spec = importlib.util.spec_from_file_location('mock_api_b', MOCK_API)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = make_server('127.0.0.1', 0, module.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
