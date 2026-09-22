"""In-memory job registry for ingestion runs.

A run takes minutes, so the upload request cannot hold the connection until it
finishes. It returns a job id instead, and the caller either streams progress
events or polls.

Everything here lives in process memory and nothing touches a database. That
is what enforces the requirement that **the uploaded file itself is never
stored**: the bytes exist only inside this process, only while the job runs.

(The gateway does now persist the *extracted result* for 30 days -- see
``backend/modes/financial_statement/upload/schema.py``. That does not weaken
the rule here and must not be read as licence to relax it: what is stored is
markdown tables, text chunks and page images, never the PDF.)

Living in memory also means a restart loses in-flight jobs, and that a second
worker would not see the first worker's jobs -- so this service runs
single-worker, the same constraint (and for the same reason) that Trial
Balance's ``_PREVIEW_STORE`` already carries.
The concurrency cap exists because each job holds a whole document's page images
at 300 DPI plus a torch model in the same process; two at once is comfortable on
the 4GB the compose file gives it, four is not.

The original PDF bytes are held only while the job runs and are dropped the
moment it finishes. What survives is the extracted result, which is what the
gateway asks for.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict
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


def _relabel(result: dict[str, Any], filename: str) -> dict[str, Any]:
    """A cached result, renamed to the file the user actually uploaded.

    The same PDF is routinely uploaded under a different name, and the name is
    not cosmetic here: prompt rule 18 renders it into every citation, and each
    table and narrative chunk carries its own ``source_file`` so a citation
    still names the right file once several uploads are merged into one
    package. Serving a cache hit under its ORIGINAL name would therefore point
    every figure in the document at a file the user never uploaded.

    Copied rather than mutated -- the cached entry is shared, and a later
    upload of the same bytes must not inherit this one's name.
    """
    out = dict(result)
    document = dict(out.get("document") or {})
    document["filename"] = filename
    out["document"] = document
    for key in ("tables", "texts"):
        rows = out.get(key) or []
        out[key] = [
            {**row, "source_file": filename} if "source_file" in row else row
            for row in rows
        ]
    return out


class Registry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(Config.MAX_CONCURRENT_JOBS)
        #: sha256 of the uploaded bytes -> the extracted result, for the
        #: current pipeline version only. See _cached/_remember.
        self._results: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def create(self, filename: str) -> Job:
        self._reap()
        job = Job(job_id=uuid.uuid4().hex, filename=filename)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    # ---- conversion cache ------------------------------------------------

    def _cached(self, digest: str) -> dict[str, Any] | None:
        """A previous extraction of these exact bytes, if there is one.

        Keyed on the content hash, so this only ever returns a result for a
        file that is byte-identical to the one being uploaded -- not a
        same-named file, not a re-export.

        The entry also carries the pipeline version that produced it and is
        ignored when that differs. Without that check the cache would actively
        hide accuracy work: deploy a table-structure fix, re-upload the
        document it was written for, and get the old broken extraction back
        because the bytes had not changed.
        """
        with self._lock:
            entry = self._results.get(digest)
            if entry is None:
                return None
            if entry.get("version") != Config.PIPELINE_VERSION:
                self._results.pop(digest, None)
                return None
            self._results.move_to_end(digest)
            return entry["result"]

    def _remember(self, digest: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[digest] = {
                "version": Config.PIPELINE_VERSION,
                "result": result,
            }
            self._results.move_to_end(digest)
            while len(self._results) > Config.RESULT_CACHE_ENTRIES:
                # Oldest first. Each entry is a whole extracted document
                # (page images included), so this is bounded by memory, not by
                # bookkeeping -- the same reason MAX_CONCURRENT_JOBS exists.
                self._results.popitem(last=False)

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

            # Identical bytes, already converted by this same pipeline
            # version: return that result instead of spending minutes of OCR
            # reproducing it. The digest is computed here, BEFORE any work --
            # `emit` computes the same hash today but only at the very end, as
            # a label, which is why re-uploading a file used to redo 100% of
            # the conversion.
            digest = hashlib.sha256(data).hexdigest()
            cached = self._cached(digest)
            if cached is not None:
                result_dict = _relabel(cached, job.filename)
                job.publish(Event(
                    "convert",
                    "This file has already been read in this session; reusing "
                    "that result rather than re-reading it",
                    0.99,
                ))
                job.result = result_dict
                job.status = "done"
                return

            result = run(data, job.filename, progress=lambda s, m, f: job.publish(Event(s, m, f)))
            job.result = result.as_dict()
            self._remember(digest, job.result)
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

    def start_reaper(self) -> None:
        """Reap on a timer as well as on upload.

        ``_reap`` ran only from ``create``, so a service that finishes a batch
        of uploads and then goes quiet held every one of those results --
        each a whole extracted document, page images included -- until somebody
        happened to upload again. On a service sized for two concurrent jobs
        that is the difference between idling at a few hundred MB and idling
        near the container limit.

        A daemon thread rather than a scheduler: there is no event loop to hang
        this off at import time, and it must never keep the process alive.
        """
        def _loop() -> None:
            interval = max(60, Config.JOB_RETENTION_SECONDS // 4)
            while True:
                time.sleep(interval)
                try:
                    self._reap()
                except Exception:  # noqa: BLE001
                    logger.exception("periodic reap failed")

        thread = threading.Thread(target=_loop, name="ingest-reaper", daemon=True)
        thread.start()


REGISTRY = Registry()
