"""In-memory jobs for long-running sandbox work (PLAN.md §10).

``POST`` endpoints return ``202 {job_id}``; clients poll ``GET /api/jobs/{id}``
or stream ``GET /api/jobs/{id}/events`` (server-sent events).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal['queued', 'running', 'done', 'failed']
MAX_JOBS = 200


@dataclass
class Job:
    id: str
    kind: str
    status: Status = 'queued'
    created: float = field(default_factory=time.time)
    finished: float | None = None
    result: Any = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'kind': self.kind,
            'status': self.status,
            'created': self.created,
            'finished': self.finished,
            'result': self.result,
            'error': self.error,
        }


class JobManager:
    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pb-job')
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)

        def run() -> None:
            job.status = 'running'
            try:
                job.result = fn()
                job.status = 'done'
            except Exception as err:  # noqa: BLE001 - reported to the client
                job.error = f'{type(err).__name__}: {err}'
                job.status = 'failed'
            finally:
                job.finished = time.time()

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout: float = 120) -> Job | None:
        deadline = time.monotonic() + timeout
        job = self.get(job_id)
        while job and job.status in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.05)
        return job

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
