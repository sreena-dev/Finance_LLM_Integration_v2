"""In-memory job registry for ingestion runs.

A run takes minutes, so the upload request cannot hold the connection until it
finishes. It returns a job id instead, and the caller either streams progress
events or polls.

Everything here lives in process memory and nothing touches a database, which
follows the requirement that an uploaded file is never stored. It also means a
restart loses in-flight jobs, and that a second worker would not see the first
worker's jobs -- so this service runs single-worker, the same constraint (and
for the same reason) that Trial Balance's ``_PREVIEW_STORE`` already carries.
The concurrency cap exists because each job holds a whole document's page images
at 300 DPI plus a torch model in the same process; two at once is comfortable on
the 4GB the compose file gives it, four is not.

The original PDF bytes are held only while the job runs and are dropped the
moment it finishes. What survives is the extracted result, which is what the
gateway asks for.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class Event:
    stage: str
    message: str
    fraction: float
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "fraction": round(self.fraction, 4),
        }


@dataclass
class Job:
    job_id: str
    filename: str
    status: str = "queued"          # queued | running | done | error
    error: str | None = None
    result: dict[str, Any] | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    events: list[Event] = field(default_factory=list)
    #: One queue per live subscriber. A list rather than a single queue so two
    #: browser tabs watching the same upload both see every event.
    subscribers: list["queue.Queue[Event | None]"] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event: Event) -> None:
        with self.lock:
            self.events.append(event)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Exception:
                # A subscriber that has gone away must never stall the pipeline.
                pass

    def close(self) -> None:
        with self.lock:
            subscribers = list(self.subscribers)
            self.subscribers.clear()
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(None)
            except Exception:
                pass

    def subscribe(self) -> "queue.Queue[Event | None]":
        """Attach a listener, replaying what it missed.

        The replay matters: the browser opens the event stream on the response
        to the upload request, by which time rendering and precheck have usually
        already run. Without it the progress bar starts at whatever stage
        happens to be next and appears to skip the beginning.
        """
        subscriber: "queue.Queue[Event | None]" = queue.Queue()
        with self.lock:
            for event in self.events:
                subscriber.put_nowait(event)
            if self.status in ("done", "error"):
                subscriber.put_nowait(None)
            else:
                self.subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber) -> None:
        with self.lock:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

    def as_dict(self, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status,
            "error": self.error,
            "progress": self.events[-1].as_dict() if self.events else None,
        }
        if include_result and self.status == "done":
            payload["result"] = self.result
        return payload


class Registry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(Config.MAX_CONCURRENT_JOBS)

    def create(self, filename: str) -> Job:
        self._reap()
        job = Job(job_id=uuid.uuid4().hex, filename=filename)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, job: Job, data: bytes) -> None:
        """Run the pipeline on a worker thread.

        A thread rather than a task on the event loop because the work is
        CPU-bound C extensions -- OpenCV, onnxruntime, torch -- which release
        the GIL. Run inline on the loop, a single ingestion would block every
        other request this service is serving, including the progress stream for
        that same job.
        """
        thread = threading.Thread(
            target=self._run, args=(job, data), name=f"ingest-{job.job_id[:8]}", daemon=True
        )
        thread.start()

    def _run(self, job: Job, data: bytes) -> None:
        from .pipeline import run  # imported here: pulls in torch, which is slow

        acquired = self._semaphore.acquire(timeout=900)
        if not acquired:
            job.status = "error"
            job.error = "The service is busy with other documents. Try again shortly."
            job.finished = time.time()
            job.close()
            return

        try:
            job.status = "running"
            job.publish(Event("queued", "Starting", 0.0))
            result = run(data, job.filename, progress=lambda s, m, f: job.publish(Event(s, m, f)))
            job.result = result.as_dict()
            job.status = "done"
        except Exception as exc:
            logger.exception("ingestion failed for %s", job.filename)
            job.status = "error"
            job.error = str(exc)
        finally:
            job.finished = time.time()
            # Drop the upload's bytes as soon as the run ends. Nothing after this
            # point needs them, and holding them would keep a whole PDF resident
            # for the job's retention window.
            del data
            self._semaphore.release()
            job.close()

    def _reap(self) -> None:
        cutoff = time.time() - Config.JOB_RETENTION_SECONDS
        with self._lock:
            stale = [
                job_id for job_id, job in self._jobs.items()
                if job.finished is not None and job.finished < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)
        if stale:
            logger.info("reaped %d finished ingestion job(s)", len(stale))


REGISTRY = Registry()
