"""Minimal Phoenix REST client — read spans, write annotations.

Deliberately raw HTTP rather than `phoenix.Client`. Importing anything under
`phoenix.*` other than `phoenix.evals` drags in the server package, and its
version compatibility with this image's Python is fragile enough that it has
already broken once on an unpinned upgrade (see requirements.txt). The two
endpoints this job needs are simple and stable, so depending on them directly
removes the whole class of problem — and it means the job can run against any
Phoenix deployment without matching client versions.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


class PhoenixAPI:
    def __init__(self, base_url: str | None = None, timeout: int = DEFAULT_TIMEOUT):
        # PHOENIX_ENDPOINT points at the OTLP *ingest* path (…/v1/traces); the
        # REST API lives at the server root, so strip the trace suffix rather
        # than making the operator configure the same host twice.
        raw = base_url or os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006")
        for suffix in ("/v1/traces", "/v1/traces/"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        self.base_url = raw.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # ── projects ──────────────────────────────────────────────────────────

    def project_id(self, name: str) -> str:
        """Resolve a project name to its id, so callers configure a name."""
        resp = self.session.get(f"{self.base_url}/v1/projects", timeout=self.timeout)
        resp.raise_for_status()
        for project in resp.json().get("data", []):
            if project.get("name") == name:
                return project["id"]
        raise LookupError(
            f"No Phoenix project named {name!r} at {self.base_url}. "
            "It is created on the first exported span — run a query with "
            "ENABLE_TRACING=true before evaluating."
        )

    # ── spans ─────────────────────────────────────────────────────────────

    def iter_spans(self, project_id: str, limit: int = 500) -> Iterator[dict[str, Any]]:
        """Yield spans newest-first, following the cursor until `limit`."""
        cursor, seen = None, 0
        while seen < limit:
            params: dict[str, Any] = {"limit": min(100, limit - seen)}
            if cursor:
                params["cursor"] = cursor
            resp = self.session.get(
                f"{self.base_url}/v1/projects/{project_id}/spans",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("data", [])
            if not batch:
                return
            for span in batch:
                yield span
                seen += 1
            cursor = payload.get("next_cursor")
            if not cursor:
                return

    def existing_annotation_names(self, project_id: str, span_ids: set[str]) -> dict[str, set[str]]:
        """Annotation names already attached, per span id.

        Used to skip work that has already been done: the LLM-judged metrics are
        the expensive part of this job, and re-running it over an overlapping
        window would otherwise pay for them again and stack duplicate scores on
        the same span.
        """
        out: dict[str, set[str]] = {}
        if not span_ids:
            return out
        try:
            resp = self.session.get(
                f"{self.base_url}/v1/projects/{project_id}/spans/annotations",
                params=[("span_ids", s) for s in list(span_ids)[:100]],
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return out
            for ann in resp.json().get("data", []):
                out.setdefault(ann.get("span_id", ""), set()).add(ann.get("name", ""))
        except Exception as exc:  # noqa: BLE001 - absence of this is not fatal
            logger.debug("Could not read existing annotations (%s); not skipping any.", exc)
        return out

    # ── annotations ───────────────────────────────────────────────────────

    def log_annotations(self, annotations: list[dict[str, Any]], sync: bool = True) -> int:
        """POST annotations in batches. Returns how many were accepted."""
        written = 0
        for start in range(0, len(annotations), 100):
            chunk = annotations[start : start + 100]
            resp = self.session.post(
                f"{self.base_url}/v1/span_annotations",
                params={"sync": str(sync).lower()},
                headers={"Content-Type": "application/json"},
                data=json.dumps({"data": chunk}),
                timeout=self.timeout,
            )
            if resp.status_code >= 300:
                logger.error("Annotation POST failed (HTTP %s): %s", resp.status_code, resp.text[:300])
                continue
            written += len(chunk)
        return written


def annotation(
    span_id: str,
    name: str,
    *,
    label: str | None = None,
    score: float | None = None,
    explanation: str | None = None,
    annotator_kind: str = "LLM",
) -> dict[str, Any]:
    """Build one annotation payload.

    `annotator_kind` is what lets the Phoenix UI separate a model's judgement
    from a deterministic check — LLM for the judged metrics, CODE for the
    rule-based ones. Keeping them distinguishable matters when reading a score:
    a failed regex is a fact, a hallucination score is an opinion.
    """
    result: dict[str, Any] = {}
    if label is not None:
        result["label"] = label
    if score is not None:
        result["score"] = float(score)
    if explanation:
        result["explanation"] = explanation[:4000]
    return {
        "span_id": span_id,
        "name": name,
        "annotator_kind": annotator_kind,
        "result": result,
    }
