"""UI-independent background job manager."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import uuid
from typing import Callable, Any

from core.tasks import TaskCancelled

from .errors import AppError, InternalAppError
from .schemas import JobState, JobStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Job:
    status: JobStatus
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobManager:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="medicion-job")
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def submit(self, job_type: str,
               work: Callable[[threading.Event, Callable], str | None]) -> str:
        job_id = uuid.uuid4().hex
        job = _Job(JobStatus(job_id=job_id, type=job_type,
                             state=JobState.QUEUED, created_at=_now()))
        with self._lock:
            self._jobs[job_id] = job

        def progress(value: Any):
            percent, message = _progress_parts(value)
            with job.lock:
                if not job.cancel.is_set():
                    job.status.progress = percent
                    job.status.message = message

        def run():
            with job.lock:
                if job.cancel.is_set():
                    job.status.state = JobState.CANCELLED
                    job.status.finished_at = _now()
                    return
                job.status.state = JobState.RUNNING
                job.status.started_at = _now()
            try:
                result_id = work(job.cancel, progress)
                with job.lock:
                    if job.cancel.is_set():
                        job.status.state = JobState.CANCELLED
                    else:
                        job.status.state = JobState.COMPLETED
                        job.status.progress = 100
                        job.status.result_id = result_id
            except TaskCancelled:
                with job.lock:
                    job.status.state = JobState.CANCELLED
            except AppError as exc:
                with job.lock:
                    job.status.state = JobState.FAILED
                    job.status.error = exc.as_dict()
            except Exception as exc:
                error = InternalAppError(
                    "La operación falló de forma inesperada.",
                    details={"type": type(exc).__name__, "reason": str(exc)})
                with job.lock:
                    job.status.state = JobState.FAILED
                    job.status.error = error.as_dict()
            finally:
                with job.lock:
                    job.status.finished_at = _now()

        self._executor.submit(run)
        return job_id

    def status(self, job_id: str) -> JobStatus:
        job = self._get(job_id)
        with job.lock:
            return job.status.model_copy(deep=True)

    def cancel(self, job_id: str) -> JobStatus:
        job = self._get(job_id)
        job.cancel.set()
        with job.lock:
            if job.status.state == JobState.QUEUED:
                job.status.state = JobState.CANCELLED
                job.status.finished_at = _now()
            return job.status.model_copy(deep=True)

    def list(self) -> list[JobStatus]:
        with self._lock:
            ids = list(self._jobs)
        return [self.status(job_id) for job_id in ids]

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _get(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Job no encontrado: {job_id}")
        return job


def _progress_parts(value: Any) -> tuple[int, str]:
    if isinstance(value, tuple) and len(value) == 2:
        return max(0, min(100, int(value[0]))), str(value[1])
    return 0, str(value)
