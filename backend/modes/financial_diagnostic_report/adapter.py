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
    if stage == "framework":
        return "Reading the applicable accounting framework…"
    if stage == "entity_type":
        return "Reading the entity's legal form…"
    if stage == "blocks:start":
        n = detail.get("count") or 0
        return f"Building {n} section{'' if n == 1 else 's'}…"
    if stage == "block":
        return f"{detail.get('title', 'Section')} ready"
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


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def report_manifest() -> list[dict[str, Any]]:
    """What the report will contain. No database, so it answers instantly."""
    from . import report as REP
    return REP.manifest()


def _evaluate_for_report(entity_id: str, *, refresh: bool,
                         emit: Callable[[str, dict[str, Any]], None]):
    """The one shared cost. Every block is built from this single read."""
    entity_id = (entity_id or "").strip()
    if not entity_id:
        raise InvalidRequestError("An entity must be selected before a report can be built.")

    _require_available()
    try:
        return ENG.evaluate(entity_id, refresh=refresh, progress=emit)
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    except ModeUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("FDR report evaluation failed for %s", entity_id)
        raise ModeUnavailableError(
            MODE_ID,
            f"Could not read this entity's filings: {type(exc).__name__}: {exc}") from exc


def _report_context(entity_id: str, evaluated, *, refresh: bool,
                    emit: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
    """Enrichment the blocks read but must not fetch for themselves.

    Gathered once, here, for two reasons. A builder that issues its own query is
    a builder that can be slow and can fail on its own, which is exactly what
    block-at-a-time delivery is meant to prevent. And an enrichment that several
    blocks want — the framework is already wanted by two — should be read once
    per report rather than once per block.

    Every read here is OPTIONAL by construction. A failure returns nothing for
    that key and the block words itself accordingly; it never takes the report
    down, because none of this is the audit spine.

    THE ONE ENTRY THAT IS NOT CHEAP
    --------------------------------
    `business_profile` costs a model call and, before it, two or three retrieval
    round trips — seconds, not the milliseconds everything else here costs. It
    still has to resolve before block 1 can be handed over, because that is
    where it was asked to live. What it must not do is hold up every OTHER
    block behind it, so `run_report` submits it to the same pool the blocks run
    in and only block 1's worker ever waits on it — see the `Future` handed
    back under `business_profile_future` below.
    """
    from . import entity_type as ET
    from . import framework as FW

    context: dict[str, Any] = {}
    try:
        emit("framework", {"entity_id": entity_id})
        context["framework"] = FW.detect(entity_id, refresh=refresh).to_dict()
    except Exception:  # noqa: BLE001 - optional enrichment, never fatal
        logger.debug("FDR framework detection failed for %s", entity_id, exc_info=True)
    try:
        emit("entity_type", {"entity_id": entity_id})
        context["entity_type"] = ET.detect(entity_id, refresh=refresh).to_dict()
    except Exception:  # noqa: BLE001
        logger.debug("FDR entity-type detection failed for %s", entity_id, exc_info=True)
    return context


def _fetch_business_profile(entity_id: str, evaluated) -> dict[str, Any]:
    """The one slow enrichment, isolated so it can run on its own pool thread.

    Never raises: an unreachable generation endpoint or an entity with nothing
    retrievable both produce an honest `formed: False` profile, which block 1
    is written to render as a stated gap, not a crash.
    """
    from . import business_profile as BP
    try:
        profile = BP.build(entity_id, evaluated.panel, evaluated.panel.latest)
        return profile.to_dict()
    except Exception as exc:  # noqa: BLE001 - optional enrichment, never fatal
        logger.warning("FDR business profile failed for %s", entity_id, exc_info=True)
        return {"formed": False,
                "reason": f"Could not be drafted this run ({type(exc).__name__}).",
                "fields": [], "citations": [], "grounded_figures": []}


def run_report(entity_id: str, *, refresh: bool = False,
               progress: Callable[[str, dict[str, Any]], None] | None = None,
               on_block: Callable[[dict[str, Any]], None] | None = None,
               ) -> dict[str, Any]:
    """Build every block for one entity, handing each one over as it lands.

    THE SHAPE OF THE WAIT, AND WHY IT IS THIS SHAPE
    -----------------------------------------------
    One read of the filings, then every block off that read. The read is the
    only part a person waits on — a second or so per filing — so it reports
    progress per filing, and the blocks follow it rather than each re-reading
    anything.

    Blocks are built in a pool and delivered by completion, not by number. Today
    every block is arithmetic and finishes at once, so the pool buys nothing;
    it is here because the narrated blocks are next, each costs a model call,
    and they are independent. When those land, this loop already streams the
    fast ones out while the slow ones are still being written, and the browser
    already orders what it receives.

    A block that raises does not take the report down with it. It is delivered
    as a failed block naming what went wrong, because a report missing one
    section that says so is worth more than an error page.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from . import report as REP

    started = time.perf_counter()
    emit = progress or (lambda *_a, **_k: None)
    deliver = on_block or (lambda _b: None)

    evaluated = _evaluate_for_report(entity_id, refresh=refresh, emit=emit)

    specs = REP.BLOCKS
    emit("blocks:start", {"count": len(specs)})
    blocks: list[dict[str, Any]] = []

    # The pool opens BEFORE the context fetch, not after, specifically so the one
    # slow enrichment (business_profile — a model call plus retrieval, seconds
    # rather than milliseconds) can be handed to a pool thread immediately and
    # run WHILE the cheap framework/entity-type reads and the other blocks'
    # arithmetic proceed. Its Future goes into `context`, not its resolved
    # value — only block 1's builder ever calls `.result()` on it, so a slow
    # profile holds up block 1 exactly, and nothing else.
    with ThreadPoolExecutor(max_workers=max(2, len(specs) + 1),
                            thread_name_prefix="fdr-block") as pool:
        profile_future = pool.submit(_fetch_business_profile, evaluated.entity_id, evaluated)
        context = _report_context(evaluated.entity_id, evaluated, refresh=refresh, emit=emit)
        context["business_profile_future"] = profile_future

        futures = {pool.submit(REP.build, spec.id, evaluated, context): spec
                   for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                block = future.result()
            except Exception as exc:  # noqa: BLE001 - one block, not the report
                logger.exception("FDR block %s failed for %s", spec.id, entity_id)
                block = {"id": spec.id, "number": spec.number, "title": spec.title,
                         "payload": None, "report_version": REP.REPORT_VERSION,
                         "error": f"This section could not be built "
                                  f"({type(exc).__name__}). The rest of the report is "
                                  f"unaffected."}
            blocks.append(block)
            emit("block", {"id": spec.id, "number": spec.number, "title": spec.title})
            deliver(block)

    blocks.sort(key=lambda b: b.get("number", 99))
    return {
        "mode": MODE_ID,
        "entity_id": evaluated.entity_id,
        "blocks": blocks,
        "provenance": evaluated.provenance(),
        "versions": evaluated.payload.get("versions") or {},
        "report_version": REP.REPORT_VERSION,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def report_markdown(entity_id: str, *, refresh: bool = False) -> tuple[str, str]:
    """The report as markdown: (filename, text).

    Built by running the same block builders the screen runs, so the file and
    the screen cannot drift apart. This is a standalone document rather than
    a rendering of the screen, though, and adds a title line, a standing
    disclaimer and a version-stamp table around the blocks for that reason —
    none of which `FdrAnalysis.jsx` shows, since the screen renders block
    components only. `report_pdf` does NOT build on this function; see its
    own docstring for why.
    """
    from . import report as REP

    result = run_report(entity_id, refresh=refresh)
    text = REP.to_markdown(result["entity_id"],
                           [b for b in result["blocks"] if b.get("payload")],
                           result.get("versions"))
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in result["entity_id"])
    return f"FDR_{safe}.md", text


def report_pdf(entity_id: str, *, refresh: bool = False) -> tuple[str, bytes]:
    """The downloadable report: (filename, PDF bytes).

    Built from each block's OWN markdown (`report.py`'s per-block
    `to_markdown`), concatenated in block order — deliberately NOT
    `report_markdown`'s wrapped text. That wrapper adds a title line, a
    standing disclaimer and a version-stamp table that exist only in the
    `.md` export: the screen shows none of the three, so a PDF meant to
    mirror what the screen shows must not carry them either. Every block's
    own markdown is still exactly the text `report_markdown` would use for
    that block — pinned to match the screen's own payload by
    `test_report.py` — so nothing here is reworded, only the surrounding
    frame is left out.
    """
    from . import pdf as PDF
    from . import report as REP

    result = run_report(entity_id, refresh=refresh)
    sections = [
        REP.BY_ID[block["id"]].to_markdown(block["payload"])
        for block in sorted(result["blocks"], key=lambda b: b.get("number", 99))
        if block.get("payload") and block.get("id") in REP.BY_ID
    ]
    text = "\n".join(sections)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in result["entity_id"])
    return f"FDR_{safe}.pdf", PDF.to_pdf(text)


# ===================================================================================
# XBRL direct-fetch path (as_db) — additive, parallel to everything above.
#
# Deliberately does not call `ENG.ensure_importable()`: that function also exports
# `FSDB_*` env vars from THIS mode's own `finance_llm` connection settings
# (`config.py`), which `xbrl_fetch` has no business depending on. `xbrl_fetch` reads
# its own `XBRLAS_*` env vars directly, against a different, already-hosted database
# (as_db). The only thing shared with the rest of this module is the sys.path entry
# that makes `pipeline/` importable — done here independently so the two paths stay
# decoupled, per the reasoning in `pipeline/xbrl_fetch/__init__.py`.
# ===================================================================================

_xbrl_path_ready = False


def _ensure_xbrl_importable() -> None:
    global _xbrl_path_ready
    if _xbrl_path_ready:
        return
    import sys
    from pathlib import Path

    pipeline_dir = Path(__file__).resolve().parent / "pipeline"
    if str(pipeline_dir) not in sys.path:
        sys.path.insert(0, str(pipeline_dir))
    _xbrl_path_ready = True


def _xbrl_meta(doc_id: str, fetch_meta_fn, action_desc: str) -> dict[str, Any]:
    """Shared doc_id-validate / fetch-meta / not-found boilerplate for every
    `/xbrl/*` block below. Only the plumbing is shared — each block's own
    build_* logic and its own fetch calls stay in that block's function."""
    if not doc_id:
        raise InvalidRequestError(f"A doc_id must be selected before {action_desc}.")

    try:
        meta = fetch_meta_fn(doc_id)
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not reach as_db: {type(exc).__name__}: {exc}") from exc

    if meta is None:
        raise NotFoundError(f"No filing with doc_id {doc_id!r} in as_db.")

    return meta


def _fy_label(meta: dict[str, Any]) -> str:
    fy_end = meta.get("fy_end")
    fy_start = meta.get("fy_start")
    return (f"FY{(fy_start.year if fy_start else fy_end.year - 1)}-{str(fy_end.year)[-2:]}"
            if fy_end else "unknown")


def _company_identity(meta: dict[str, Any]) -> tuple[str, str]:
    company_name = meta.get("company_name") or meta.get("entity_cin") or ""
    cin = meta.get("entity_cin") or ""
    return company_name, cin


def xbrl_entities() -> list[dict[str, Any]]:
    """Every filing in as_db, for the picker. Raises ModeUnavailableError if as_db
    cannot be reached — checked by trying, not by a separate probe, since listing
    filings IS the cheapest possible read against it."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_entities as XE

    try:
        return XE.list_entities()
    except Exception as exc:  # noqa: BLE001
        raise ModeUnavailableError(
            MODE_ID, f"Could not reach as_db: {type(exc).__name__}: {exc}") from exc


def xbrl_dashboard(doc_id: str) -> dict[str, Any]:
    """Block 2, computed directly from as_db for one filing.

    A `doc_id` that does not exist in `documents` is a 404, not an empty
    dashboard — the two mean different things to a caller (wrong id vs. a real
    filing with nothing computable, which the 8 known-broken filings in as_db
    still return successfully, with `computed_count: 0`, per
    `xbrl_ingestion_integrity_check.md`).
    """
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_dashboard as XD
    from xbrl_fetch import xbrl_fetch as XF

    meta = _xbrl_meta(doc_id, XF.fetch_doc_meta, "a dashboard can be built")

    rows = XF.fetch_doc_metrics(doc_id)
    payload = XD.build_dashboard(rows, doc_id)

    return {
        "doc_id": doc_id,
        "entity_cin": meta.get("entity_cin") or "",
        "company_name": meta.get("company_name") or meta.get("entity_cin") or "",
        "fy_label": _fy_label(meta),
        **payload,
    }


def xbrl_business_profile(doc_id: str) -> dict[str, Any]:
    """Block 3: Business Profile, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_business_profile as XBP
    from xbrl_fetch import xbrl_profile_fetch as XPF

    meta = _xbrl_meta(doc_id, XPF.fetch_profile_meta, "a business profile can be built")

    metric_rows = XPF.fetch_profile_metrics(doc_id)
    disclosure_rows = XPF.fetch_profile_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    # `clients` is this mode's own module (`financial_diagnostic_report/clients.py`),
    # importable here validly because this file lives alongside it — unlike
    # `xbrl_business_profile.py`, which cannot reach it with a relative import (see
    # that function's docstring). Passing the callable down is what makes the LLM
    # path genuinely usable rather than permanently, silently dead.
    return XBP.build_business_profile(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        disclosure_rows=disclosure_rows,
        chat_fn=clients.chat,
    )


def xbrl_risk_clusters(doc_id: str) -> dict[str, Any]:
    """Block 6: Key Risk Clusters with Interactions, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_risk_clusters as XRC
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "risk clusters can be built")

    metric_rows = XRF.fetch_risk_metrics(doc_id)
    disclosure_rows = XRF.fetch_risk_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XRC.build_risk_clusters(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        disclosure_rows=disclosure_rows,
        chat_fn=clients.chat,
    )


def xbrl_company_overview(doc_id: str) -> dict[str, Any]:
    """Block 1: Company Overview — entity identity, statement flavour, and reporting
    framework, read structurally from as_db (namespace + a genuine compliance
    disclosure, never inferred). Additive and standalone, like `xbrl_signals`."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_overview as XOV
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "the company overview can be built")
    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    overview = XOV.build_company_overview(doc_id, meta, company_name)
    overview["fy_label"] = fy_label
    overview["cin"] = cin
    return overview


def xbrl_signals(doc_id: str) -> dict[str, Any]:
    """The S01-S27 signal library, evaluated directly against as_db for one filing's
    entity. Additive and standalone: unlike `xbrl_risk_clusters` above, this does not
    synthesise cluster packages or call the LLM — it returns the raw, closed-set
    SignalResult list (FIRED / NOT_FIRED / ABSTAIN / NOT_APPLICABLE, each with its
    reason where it didn't run) so the signal engine can be inspected and reviewed on
    its own before any decision is made about wiring it into cluster synthesis."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_signal_engine as XSE
    from xbrl_fetch import xbrl_risk_fetch as XRF

    meta = _xbrl_meta(doc_id, XRF.fetch_risk_meta, "signals can be evaluated")
    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    results = XSE.evaluate_signals_as_dicts(doc_id)
    return {
        "doc_id": doc_id,
        "company_name": company_name,
        "cin": cin,
        "fy_label": fy_label,
        "signals": results,
        "caveats": (
            "The outputs are leads for audit attention, not findings, opinions or "
            "conclusions.",
            "Materiality is applied separately (xbrl_signal_materiality.py) and is not "
            "reflected in a signal's FIRED/NOT_FIRED status.",
            "Cluster synthesis (risk-cluster packages, interactions, LLM narration) is "
            "out of scope for this endpoint.",
        ),
    }


def xbrl_trends(doc_id: str) -> dict[str, Any]:
    """Block 5: Key Trends & Structural Drift, computed directly from as_db for one filing."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_trends as XT
    from xbrl_fetch import xbrl_trend_fetch as XTF

    meta = _xbrl_meta(doc_id, XTF.fetch_trend_meta, "trends can be computed")

    metric_rows = XTF.fetch_trend_facts(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XT.build_key_trends(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        metric_rows=metric_rows,
        chat_fn=clients.chat,
    )


def xbrl_health_summary(doc_id: str) -> dict[str, Any]:
    """Block 4: Financial Health Summary — Structure & Performance, Interpreted."""
    _ensure_xbrl_importable()
    from xbrl_fetch import xbrl_health as XH
    from xbrl_fetch import xbrl_health_fetch as XHF

    meta = _xbrl_meta(doc_id, XHF.fetch_health_meta, "health summary can be computed")

    facts = XHF.fetch_health_facts(doc_id)
    turnover_facts = XHF.fetch_business_turnover_facts(doc_id)
    disclosures = XHF.fetch_health_disclosures(doc_id)

    fy_label = _fy_label(meta)
    company_name, cin = _company_identity(meta)

    return XH.build_health_summary(
        doc_id=doc_id,
        company_name=company_name,
        cin=cin,
        fy_label=fy_label,
        facts=facts,
        turnover_facts=turnover_facts,
        disclosures=disclosures,
        meta=meta,
        chat_fn=clients.chat,
    )


def xbrl_report_pdf(doc_id: str) -> tuple[str, bytes]:
    """The downloadable XBRL Direct report: (filename, PDF bytes).

    Runs the same five block functions the XBRL Direct tab itself calls
    (`xbrl_dashboard`, `xbrl_business_profile`, `xbrl_health_summary`,
    `xbrl_trends`, `xbrl_risk_clusters`) and hands their payloads to
    `xbrl_report.to_markdown`, which reorders them into the spec's
    planning-first structure — same figures and wording the screen already
    shows, just reordered and paginated rather than scrolled. Mirrors
    `report_pdf`'s pattern for the fs_db Report tab.
    """
    from . import pdf as PDF
    from xbrl_fetch import xbrl_report as XR

    dash = xbrl_dashboard(doc_id)
    try:
        profile = xbrl_business_profile(doc_id)
    except Exception:  # noqa: BLE001 - optional enrichment, dashboard is the anchor
        profile = None
    try:
        health = xbrl_health_summary(doc_id)
    except Exception:  # noqa: BLE001
        health = None
    try:
        trends = xbrl_trends(doc_id)
    except Exception:  # noqa: BLE001
        trends = None
    try:
        risk = xbrl_risk_clusters(doc_id)
    except Exception:  # noqa: BLE001
        risk = None

    text = XR.to_markdown(dash, profile, health, trends, risk)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in dash.get("company_name", doc_id))
    return f"FDR_XBRL_{safe}.pdf", PDF.to_pdf(text)



