"""Adapter for the Financial Diagnostic Report mode.

Sourced from the `Financial_Diagnostic_Report` work in the `FIN_LLM/` project.
Two packages were vendored VERBATIM into `pipeline/` — zero patches, so both can
be re-pulled from source without a merge conflict:

  `pipeline/fdr/`    the audit spine. Stdlib only: no database, no HTTP, no
                     model. Signal rules, risk clusters, thresholds, report
                     assembly. Pure functions over a panel, which is what makes
                     an abstain mean "a figure was missing" and never "a socket
                     dropped".
  `pipeline/fs_db/`  the filings reader. Parses `table_chunks`, binds Schedule
                     III line items to canonical keys and runs the arithmetic
                     tie-outs that decide whether a figure can be trusted.

They import each other absolutely (`from fs_db import facts`), so `pipeline/`
goes on `sys.path` — the same arrangement the Financial Statement and Trial
Balance modes use for their vendored trees.

WHAT WAS DELIBERATELY NOT VENDORED
----------------------------------
The source project persisted every report into a local SQLite register and
answered questions out of it. That is not reproduced. It created itself empty on
first touch, so an unpopulated system answered "no entity has this risk firing"
— a clean bill of health — when the truth was "nothing has ever been run". Two
of its projections read a table that nothing had written since the extraction
path went live, and so reported "no trusted figure is stored for any entity"
about figures sitting in Postgres.

This mode answers from the corpus on every request. See `engine.py`.

WHAT THIS MODE NEEDS TO RUN
---------------------------
`FINANCE_DSN` (already required by the Statutory Auditor's Report mode — same
physical database) and the `psycopg` v3 driver. No LLM endpoint, no embedding
endpoint, no vector store. That is the entire list, and it is why this mode can
be available while the generation endpoints the other modes need are down.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from app.errors import InvalidRequestError, ModeUnavailableError, NotFoundError

from . import answers as ANS
from . import clients
from . import config as CFG
from . import disclosure as DISC
from . import engine as ENG
from . import intent as IT

logger = logging.getLogger(__name__)

MODE_ID = "financial-diagnostic-report"
BRANCH = "Financial_Diagnostic_Report"

# Readiness is probed by `GET /api/modes?probe=true` on page load, so it must not
# re-open a database connection on every call. The verdict is cached briefly —
# long enough to make a page load cheap, short enough that a database coming back
# up is noticed without a restart.
_STATUS_TTL_SECONDS = 30.0
_status_lock = threading.Lock()
_status_cache: dict[str, Any] = {"at": 0.0, "value": None}


def _probe() -> tuple[bool, str | None]:
    settings = CFG.load()
    if not settings.configured:
        return False, ("No filings database configured. Set FINANCE_DSN (the same "
                       "database the Statutory Auditor's Report mode uses) or FDR_DSN.")

    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False, ("The `psycopg` (v3) driver is not installed. Install it with "
                       "`pip install 'psycopg[binary]'` — the vendored fs_db reader "
                       "requires v3, which coexists with the psycopg2 the other modes use.")

    try:
        ENG._ensure_pipeline()
    except Exception as exc:  # noqa: BLE001 - any failure degrades this mode only
        return False, f"{type(exc).__name__}: {exc}"

    try:
        from fs_db import db as FSDB

        ok, message = FSDB.ping(timeout=4)
        if not ok:
            return False, f"Filings database unreachable: {message}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Filings database unreachable: {type(exc).__name__}: {exc}"

    return True, None


def status() -> tuple[bool, str | None]:
    """Is this mode able to answer right now, and if not, what would fix it."""
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache["value"]
        if cached is not None and (now - _status_cache["at"]) < _STATUS_TTL_SECONDS:
            return cached

    verdict = _probe()
    with _status_lock:
        _status_cache["at"] = time.monotonic()
        _status_cache["value"] = verdict
    if verdict[0]:
        _warm_reranker()
    return verdict


_warm_started = False


def _warm_reranker() -> None:
    """Load the cross-encoder off the request path, once.

    It costs ~20s to load and is needed only by the retrieval path. Doing it on
    the first health probe — which the UI fires on page load — means the first
    user to ask a narrative question does not pay for it. A daemon thread so it
    can never hold up shutdown, and failures are the reranker's own problem:
    `rerank` degrades to the fused order.
    """
    global _warm_started
    if _warm_started:
        return
    _warm_started = True

    def run() -> None:
        try:
            from . import rerank as RR
            RR.warm()
        except Exception:  # noqa: BLE001 - warming is best-effort by definition
            logger.debug("FDR reranker warm-up failed", exc_info=True)

    threading.Thread(target=run, name="fdr-rerank-warm", daemon=True).start()


def retrieval_status() -> dict[str, Any]:
    """What the narrative path needs, and whether it has it.

    Reported apart from `status()` on purpose. The diagnostics need none of
    this, so a reranker that failed to load or a generation endpoint that is
    down should show as a reduced capability, never as a dead mode.
    """
    from . import rerank as RR

    settings = CFG.load()
    return {
        "configured": settings.retrieval_configured,
        "embedding_model": settings.embedding_model,
        "generation_model": settings.llm_model,
        "reranker": RR.status(),
        "groundedness_check": settings.groundedness_check,
    }


def _require_available() -> None:
    available, reason = status()
    if not available:
        raise ModeUnavailableError(MODE_ID, reason or "unavailable")


def entities() -> list[dict[str, Any]]:
    """Every entity the corpus holds, for the picker."""
    _require_available()
    try:
        return ENG.entities()
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not list entities: {type(exc).__name__}: {exc}") from exc


def _envelope(entity_id: str, query: str, answer: "ANS.Answer",
              started: float) -> dict[str, Any]:
    """The response shape, built in one place.

    `Answer.text` becomes `answer` on the wire: inside this module "text" is the
    prose as opposed to the rows, while a caller reading JSON wants the field to
    say what it is.
    """
    return {
        "mode": MODE_ID,
        "entity_id": entity_id,
        "query": query,
        "kind": answer.kind,
        "answer": answer.text,
        "rows": answer.rows,
        "provenance": answer.provenance,
        "sources": getattr(answer, "sources", None) or [],
        "intent": answer.intent,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _stage_message(stage: str, detail: dict[str, Any]) -> str:
    """A progress event, phrased for a person watching it happen.

    Built HERE rather than in the browser so there is one wording of each stage
    no matter which client is watching, and so a new stage in the vendored
    extractor degrades to something readable instead of a raw event name.
    """
    if stage == "cached":
        age = detail.get("age_seconds", 0)
        return (f"Using the read from {int(age)}s ago"
                if age >= 1 else "Using the read just taken")
    if stage == "read:start":
        return "Opening the filings…"
    if stage == "extract":
        i, n = detail.get("i"), detail.get("n")
        fy = detail.get("fy") or detail.get("doc_id") or ""
        if detail.get("error"):
            return f"Filing {fy} could not be read ({i} of {n})"
        return f"Read {fy} — {detail.get('facts', 0)} figures ({i} of {n})"
    if stage == "cross_year":
        promoted = detail.get("promoted") or 0
        return (f"Reconciled scales across years ({promoted} settled)"
                if promoted else "Reconciled scales across years")
    if stage == "evaluate":
        return f"Running diagnostics over {len(detail.get('years') or [])} years…"
    if stage == "classified":
        return f"Read as a {detail.get('kind', 'question')} question"
    if stage == "resolve":
        return "Finding the filing…"
    if stage == "resolved":
        return f"Reading {detail.get('fy', '')} ({detail.get('doc_id', '')})"
    if stage == "retrieve":
        return "Searching the statements and the report text…"
    if stage == "retrieved":
        return f"{detail.get('candidates', 0)} candidate passages found"
    if stage == "rerank":
        return "Ranking passages by relevance…"
    if stage == "reranked":
        return (f"{detail.get('kept', 0)} passages kept as evidence"
                if detail.get("applied") else
                f"{detail.get('kept', 0)} passages kept (reranker unavailable)")
    if stage == "generate":
        return f"Answering from {detail.get('sources', 0)} cited sources…"
    if stage == "verify":
        return "Checking the answer against its sources…"
    return stage.replace(":", " ").replace("_", " ")


def _disclosure(entity_id: str, query: str, decision, started: float,
                emit, *, on_token=None, note: str = "") -> dict[str, Any]:
    """Run the retrieval path and shape it into the common response envelope."""
    try:
        result = DISC.answer(entity_id, query, progress=emit, on_token=on_token)
    except clients.EndpointError as exc:
        raise ModeUnavailableError(MODE_ID, exc.detail) from exc

    text = result["text"]
    if note:
        text = f"*{note}*\n\n{text}"
    return {
        "mode": MODE_ID,
        "entity_id": entity_id,
        "query": query,
        "kind": result["kind"],
        "answer": text,
        "rows": result.get("rows") or [],
        "sources": result.get("sources") or [],
        "provenance": result.get("provenance") or {},
        "intent": decision.to_dict(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def run_query(entity_id: str, query: str, *, refresh: bool = False,
              progress: Callable[[str, dict[str, Any]], None] | None = None,
              on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Answer one question about one entity, from a live read of its filings.

    `progress` receives (stage, detail) as the work proceeds. It is optional and
    the non-streaming route passes nothing, so both routes run the exact same
    code path — the stream is the same answer, watched rather than waited for.
    """
    started = time.perf_counter()
    emit = progress or (lambda *_a, **_k: None)

    entity_id = (entity_id or "").strip()
    if not entity_id:
        raise InvalidRequestError("An entity must be selected before asking a question.")
    if not (query or "").strip():
        raise InvalidRequestError("Query cannot be empty.")

    # Classify FIRST. A refusal, an ambiguity or an unsupported question is
    # answered without touching the database — the user gets it immediately
    # rather than after a read that could not have changed the answer.
    decision = IT.classify(query)
    emit("classified", {"kind": decision.kind, "reason_code": decision.reason_code})
    if decision.kind in (IT.REFUSED, IT.AMBIGUOUS, IT.UNSUPPORTED):
        return _envelope(entity_id, query, ANS.build(decision), started)

    _require_available()

    # The retrieval path answers from the filing's text and needs no panel, so it
    # runs BEFORE evaluation — extracting five years of statements to answer
    # "what is the registered office?" would be seconds spent on figures the
    # answer never touches.
    if decision.kind == IT.DISCLOSURE:
        return _disclosure(entity_id, query, decision, started, emit, on_token=on_token)

    try:
        evaluated = ENG.evaluate(entity_id, refresh=refresh, progress=emit)
    except LookupError as exc:
        # A reference that does not exist is a 404: retrying will not help, and
        # calling it an outage would send the operator looking at the database.
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("FDR evaluation failed for %s", entity_id)
        raise ModeUnavailableError(
            MODE_ID,
            f"Could not read this entity's filings: {type(exc).__name__}: {exc}") from exc

    # Readiness is only consulted by the two projections that report on gaps.
    # Computing it for every question would walk the whole registry to answer
    # "what is revenue".
    readiness_rows: list[dict[str, Any]] = []
    if decision.kind in (IT.COVERAGE, IT.BLOCKED):
        readiness_rows = ENG.readiness(evaluated)

    built = ANS.build(decision, evaluated, readiness_rows)

    # THE FALL-THROUGH. A figure question the panel cannot serve is not the end
    # of the road: the statements did not BIND that line item, but the filing may
    # still print it, and the retrieval path can go and read it. Without this,
    # "what were finance costs?" returns "not available" for an entity whose
    # income statement shows them plainly — a limitation of the binder reported
    # as a fact about the company.
    if decision.kind == IT.FIGURE and built.kind == "figure" and not built.rows:
        try:
            return _disclosure(entity_id, query, decision, started, emit,
                               on_token=on_token,
                               note=f"`{decision.canonical_key}` is not bound in this "
                                    f"entity's panel, so this was read from the filing "
                                    f"text instead.")
        except (clients.EndpointError, LookupError):
            # Retrieval unavailable — return the honest deterministic answer
            # rather than an outage, which is still true and still useful.
            pass

    return _envelope(entity_id, query, built, started)


def invalidate(entity_id: str | None = None) -> int:
    """Drop cached evaluations so the next question re-reads the corpus."""
    return ENG.invalidate(entity_id)
