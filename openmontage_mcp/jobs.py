"""Background jobs for renders and generations.

An MCP call must return quickly; a render or a video generation takes
minutes. Every such call runs in a worker thread (`asyncio.to_thread`) and
the client polls it by job id.

Cancellation is honest. A Python thread blocked in ffmpeg or in an HTTP call
to a provider cannot be interrupted, so `cancel` does not pretend to stop it:
it records the request, the work runs to its end, files it writes stay on
disk and the spend it incurs is charged to the project budget like any other.
The job then reports `cancel_requested` next to its real final status.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

ACTIVE = "running"
DONE = ("completed", "failed")


@dataclass
class Job:
    job_id: str
    tool_name: str
    project_id: str
    status: str = ACTIVE
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "tool_name": self.tool_name,
            "project_id": self.project_id,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "elapsed_seconds": round((self.completed_at or time.time()) - self.created_at, 1),
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload["error"] = self.error
            payload["code"] = self.error_code
        payload["next_action"] = "done" if self.status in DONE else "poll_get_job_status"
        return payload


class JobLimitError(RuntimeError):
    """Too many jobs running at once."""


class JobTracker:
    """In-memory, per-server-process registry of background jobs."""

    def __init__(self, max_running: int | None = None) -> None:
        self.max_running = max_running or int(os.environ.get("OPENMONTAGE_MCP_MAX_JOBS", "2"))
        self._jobs: dict[str, Job] = {}

    def running(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == ACTIVE)

    def submit(
        self,
        work: Callable[[], dict[str, Any]],
        *,
        tool_name: str,
        project_id: str,
        on_error: Callable[[BaseException], tuple[str, str]],
    ) -> Job:
        """Start `work` in a thread; `on_error` maps an exception to (code, message)."""
        if self.running() >= self.max_running:
            raise JobLimitError(
                f"{self.max_running} jobs already running; poll list_jobs and retry when one is done"
            )
        job = Job(job_id=uuid.uuid4().hex[:12], tool_name=tool_name, project_id=project_id)

        async def _run() -> None:
            try:
                result = await asyncio.to_thread(work)
                job.result = result
                job.status = "completed" if result.get("success", True) else "failed"
            except Exception as exc:  # the job must always land in a final state
                job.error_code, job.error = on_error(exc)
                job.status = "failed"
            finally:
                job.completed_at = time.time()

        job.task = asyncio.get_running_loop().create_task(_run())
        self._jobs[job.job_id] = job
        self._forget_oldest_finished()
        return job

    def _forget_oldest_finished(self, keep: int = 200) -> None:
        finished = [j for j in self._jobs.values() if j.status in DONE]
        for job in finished[: max(0, len(finished) - keep)]:
            del self._jobs[job.job_id]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        return [
            job.public() for job in self._jobs.values()
            if status is None or job.status == status
        ]

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in DONE:
            return {**job.public(), "message": "job already finished; nothing to cancel"}
        job.cancel_requested = True
        return {
            **job.public(),
            "message": (
                "cancellation recorded, but the running tool cannot be interrupted: "
                "it will run to its end, its files stay on disk and any spend is "
                "still charged to the project budget"
            ),
        }
