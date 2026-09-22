"""HTTP client for the ingestion service.

The gateway never converts a PDF itself: docling, torch and an OCR engine are
multi-gigabyte dependencies and a conversion saturates a CPU for minutes, while
this process runs read-only on four shared cores and also serves chat. So the
bytes are forwarded to ``ARTHA_INGEST_URL`` and the progress stream is relayed
back to the browser.

Configuration is a single URL, and leaving it blank turns the feature off
cleanly: ``status()`` reports why, the upload routes answer 503 with that
reason, and every existing corpus route is untouched. That is the same
degradation posture the rest of this gateway takes -- a mode that cannot do one
thing says so rather than failing at the moment a user tries it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator

import requests

logger = logging.getLogger(__name__)

BASE_URL = (os.getenv("ARTHA_INGEST_URL") or "").rstrip("/")
#: Generous: a 35-page scan at 300 DPI takes minutes through OCR and
#: TableFormer, and the upload POST itself returns as soon as the job is
#: queued. This ceiling only has to cover the queueing request.
UPLOAD_TIMEOUT = float(os.getenv("ARTHA_INGEST_UPLOAD_TIMEOUT", "120"))
#: The event stream is long-lived; the service sends a keep-alive comment every
#: 15 seconds, so a read timeout comfortably above that is a real stall.
STREAM_TIMEOUT = float(os.getenv("ARTHA_INGEST_STREAM_TIMEOUT", "60"))


class IngestUnavailable(RuntimeError):
    """The ingestion service is not configured or cannot be reached."""


def configured() -> bool:
    return bool(BASE_URL)


def status() -> tuple[bool, str | None]:
    """``(available, reason)``. Never raises -- this backs the health probe."""
    if not BASE_URL:
        return False, (
            "Document upload is not configured. Set ARTHA_INGEST_URL to the "
            "ingestion service (see .env.example) and start it with "
            "`docker compose up -d ingest`."
        )
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return False, f"The ingestion service at {BASE_URL} is not reachable: {exc}"

    if not payload.get("available"):
        return False, payload.get("reason") or "The ingestion service reports itself unavailable."
    return True, None


def submit(filename: str, data: bytes) -> str:
    """Queue one document for conversion. Returns the job id."""
    if not BASE_URL:
        raise IngestUnavailable(status()[1] or "Document upload is not configured.")
    try:
        response = requests.post(
            f"{BASE_URL}/ingest",
            files={"file": (filename, data, "application/pdf")},
            timeout=UPLOAD_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IngestUnavailable(f"Could not reach the ingestion service: {exc}") from exc

    if response.status_code >= 400:
        # Forward the service's own message rather than a generic one: it is
        # specific and actionable ("the file is 91MB; the limit is 64MB").
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text[:300]
        raise IngestUnavailable(str(detail or f"Ingestion refused the file ({response.status_code})."))

    return response.json()["job_id"]


def events(job_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Relay the service's SSE stream as ``(event_name, payload)`` pairs.

    Parsed here rather than proxied verbatim so the gateway can re-frame the
    events for the browser and, more importantly, capture the final ``result``
    frame: that payload is what gets stored against the conversation, and the
    browser must not be the only thing that sees it.
    """
    if not BASE_URL:
        raise IngestUnavailable(status()[1] or "Document upload is not configured.")

    with requests.get(
        f"{BASE_URL}/ingest/{job_id}/events",
        stream=True,
        timeout=(10, STREAM_TIMEOUT),
        headers={"Accept": "text/event-stream"},
    ) as response:
        response.raise_for_status()
        event_name = "message"
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                event_name = "message"
                continue
            if line.startswith(":"):
                continue  # keep-alive
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if line.startswith("data:"):
                body = line[5:].strip()
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    continue
                yield event_name, payload


def poll(job_id: str) -> dict[str, Any]:
    if not BASE_URL:
        raise IngestUnavailable(status()[1] or "Document upload is not configured.")
    response = requests.get(f"{BASE_URL}/ingest/{job_id}", timeout=30)
    if response.status_code == 404:
        raise IngestUnavailable("That ingestion job is no longer available.")
    response.raise_for_status()
    return response.json()
