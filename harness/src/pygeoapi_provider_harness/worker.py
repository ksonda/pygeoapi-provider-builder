"""Sandbox worker: one JSON job on stdin, one JSON result on stdout.

Usage: ``python -m pygeoapi_provider_harness.worker < job.json``

Anything a provider prints goes to stderr, so stdout carries only the result.
Values of the environment variables named in ``job['secret_env']`` are
redacted from the result, including error messages and tracebacks.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from . import PROTOCOL
from .jobs import JOBS
from .util import describe_exception, json_default, redact


def run(job: dict[str, Any]) -> dict[str, Any]:
    if job.get('protocol', PROTOCOL) != PROTOCOL:
        raise ValueError(f'unsupported protocol {job.get("protocol")!r}')
    kind = job.get('kind')
    if kind not in JOBS:
        raise ValueError(f'unknown job kind {kind!r}')
    return JOBS[kind](job)


def main() -> int:
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(level=os.environ.get('HARNESS_LOG_LEVEL', 'WARNING'), stream=sys.stderr)
    job: dict[str, Any] = {}
    try:
        job = json.loads(sys.stdin.read() or '{}')
        payload: dict[str, Any] = {'protocol': PROTOCOL, 'ok': True, 'result': run(job)}
    except Exception as err:  # noqa: BLE001 - every failure becomes a JSON result
        payload = {'protocol': PROTOCOL, 'ok': False, 'error': describe_exception(err)}
    secrets = [os.environ.get(name, '') for name in job.get('secret_env', [])]
    text = json.dumps(redact(payload, secrets), default=json_default)
    real_stdout.write(redact(text, secrets))
    real_stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
