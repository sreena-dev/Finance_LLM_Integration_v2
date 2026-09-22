"""Adapter for the `Financial_Statement` branch pipeline.

The branch code under `pipeline/` is kept **verbatim** so it can be re-pulled
from origin/Financial_Statement without a merge conflict. That code uses
top-level absolute imports (`import tools_fs`, `import agent`), so the adapter
puts `pipeline/` on sys.path before importing it, rather than rewriting the
branch's import statements.

We import the branch's own `api_server` module and reuse its orchestrator and
its answer-parsing helpers. That keeps this mode's output byte-for-byte
identical to running the branch standalone — there is no second copy of the
markdown-section parsing logic to drift out of sync.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

from app.errors import ModeUnavailableError

logger = logging.getLogger(__name__)

MODE_ID = "financial-statement"
BRANCH = "Financial_Statement"

_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"

_lock = threading.Lock()
_state: dict = {"loaded": False, "error": None, "api": None}


def _load() -> object:
    """Import the branch pipeline once. Raises ModeUnavailableError on failure."""
    if _state["loaded"]:
        if _state["error"]:
            raise ModeUnavailableError(MODE_ID, _state["error"])
        return _state["api"]

    with _lock:
        if _state["loaded"]:
            if _state["error"]:
                raise ModeUnavailableError(MODE_ID, _state["error"])
            return _state["api"]

        try:
            if str(_PIPELINE_DIR) not in sys.path:
                sys.path.insert(0, str(_PIPELINE_DIR))

            # Importing api_server constructs agent.Orchestrator() at module
            # level and gives us _parse_structured_answer/_format_chunks_for_api.
            import api_server as fs_api  # type: ignore

            _require_yukta()
            _normalise_embedding_url()
            _install_entity_resolution()
            _install_upload_bridge()
            _install_reranker_fallback()
            _warm_table_config(fs_api)
            _require_reports_db(fs_api)
            _state["api"] = fs_api
            _state["error"] = None
        except Exception as exc:  # noqa: BLE001 - any failure degrades this mode only
            _state["error"] = f"{type(exc).__name__}: {exc}"
            _state["loaded"] = True
            raise ModeUnavailableError(MODE_ID, _state["error"]) from exc

        _state["loaded"] = True
        return _state["api"]


def _require_yukta() -> None:
    """Fail the readiness check when `yukta` is absent.

    Checked here rather than left to surface at query time because this
    pipeline imports yukta unconditionally when it builds its agent
    (agent.py's _build_fs_agent). Without this, the DB checks below would
    succeed, the mode would report itself ready, and every query would then
    fail with a 503 — a green light for a mode that cannot answer anything.

    Unlike SAR, this pipeline has no non-yukta fallback path, so a missing
    yukta genuinely means the mode is unavailable.
    """
    import importlib.util

    if importlib.util.find_spec("yukta") is None:
        raise RuntimeError(
            "The 'yukta' package is not installed, and this pipeline requires it "
            "to build its agent. It is not on PyPI — install it from your "
            "internal index or a source checkout."
        )


def _normalise_embedding_url() -> None:
    """Give this pipeline the embedding URL shape it expects.

    The two integrated pipelines read the same `EMBEDDING_BASE_URL` but disagree
    on what it contains: SAR appends "/v1/embeddings" to it, while this pipeline
    POSTs to it verbatim (see Embedder.embed_text). A single env value cannot
    satisfy both — with the bare URL this pipeline POSTs to the server root,
    and with the full path SAR would request ".../v1/embeddings/v1/embeddings".

    So `.env` holds the BARE base URL, and we patch the full path onto this
    pipeline's `Config` attribute. Patching the attribute rather than the
    environment variable is deliberate: this pipeline reads it once at import,
    whereas SAR re-reads the env var on every call, so mutating the environment
    here would silently break SAR later in the same process.
    """
    from tools_fs import Config as config  # type: ignore

    url = (config.EMBEDDING_BASE_URL or "").rstrip("/")
    if not url:
        return
    if not url.endswith("/v1/embeddings"):
        config.EMBEDDING_BASE_URL = f"{url}/v1/embeddings"
        print(f"[{MODE_ID}] Embedding endpoint normalised to {config.EMBEDDING_BASE_URL}")


def _install_upload_bridge() -> None:
    """Let the existing company tools read uploaded documents.

    Runs AFTER _install_entity_resolution() and deliberately so: the bridge
    wraps whatever `_resolve_document` is currently bound, so the entity
    ladder stays in the chain for corpus documents rather than being replaced
    by it. Reversing the order would leave the ladder wrapping the bridge, and
    an uploaded document would never be reached.

    A contract failure here is fatal to the mode for the same reason it is in
    entity resolution: shipping with a silently uninstalled bridge means every
    upload converts, reports success, and then answers "no data for that
    company".
    """
    import tools_fs  # type: ignore

    from .upload import bridge

    bridge.install(
        tools_fs.ComplianceTools,
        tools_fs.UnitResolver,
        tools_fs.DocumentResolver,
        tools_fs.SourceRef,
        tool_registry=tools_fs.ToolRegistry,
        # The narrative readers. Passed as a mapping so the bridge needs no
        # import of the vendored pipeline and this file stays the single place
        # that knows which classes exist.
        narrative_targets={
            "DisclosureSearchTools": tools_fs.DisclosureSearchTools,
            "AccountingPolicyTools": tools_fs.AccountingPolicyTools,
            "ReportReferenceTools": tools_fs.ReportReferenceTools,
            "ExecutiveSummaryTools": tools_fs.ExecutiveSummaryTools,
            "AuditorReportTools": tools_fs.AuditorReportTools,
            "GoingConcernTools": tools_fs.GoingConcernTools,
            "AuditRiskTools": tools_fs.AuditRiskTools,
            # The multi-year trend's document resolver. Without it, the one
            # tool that computes cross-year deltas cannot assemble a series
            # from uploads at all -- and prompt rule 15 forbids the model
            # computing the deltas itself, so a comparative question about
            # uploaded filings has no answer path.
            "TrendAnalysisTools": tools_fs.TrendAnalysisTools,
        },
    )


def _install_entity_resolution() -> None:
    """Replace the branch's company matcher with the normalised one.

    WHY THIS IS DONE HERE RATHER THAN BY EDITING THE PIPELINE
    ---------------------------------------------------------
    The branch matched a company with a one-directional substring — the stored
    `documents.company` had to CONTAIN the user's string — so `Gujarat_Gas`
    matched "Gujarat Gas" but not "Gujarat Gas Limited", and the sixteen tool
    descriptions that said `e.g. 'Coal India', 'ONGC', 'BPCL'` taught the model
    to emit exactly those expanded forms. Entities that were sitting in the
    corpus reported themselves absent.

    `_resolve_document`, `format_ambiguous` and `latest_fy_end` are staticmethods
    reached as `DocumentResolver.X(...)` from ~17 call sites, so rebinding the
    three attributes fixes every one of them while leaving `pipeline/` re-pullable
    from origin — the same technique `_normalise_embedding_url` above uses on
    `Config`, and that the branch's own `agent.py` uses on `_format_tool_result`.

    `install()` checks the signatures first: if a re-pull changes that class's
    shape, this raises and the mode reports itself unavailable with the reason.
    That is deliberate. Declining to install quietly would leave the original bug
    running while the logs claimed the fix had shipped.
    """
    from tools_fs import DocumentResolver  # type: ignore

    from . import entity_resolution

    entity_resolution.install(DocumentResolver)


def _install_reranker_fallback() -> None:
    """Let a reranker outage cost ranking quality instead of the whole answer.

    `_search_knowledge_base` wraps its rerank call and returns
    `"[tool error] Reranker failed: ..."` on any exception — abandoning results
    that were already retrieved and already sorted by similarity. The reranker
    refines an ordering; it is not what makes the chunks relevant, so discarding
    them because it is down throws away a good answer to avoid a slightly
    worse-ordered one. The model then reports having found nothing.

    Wrapping the staticmethod means the branch's `except` never fires and the
    fused similarity order stands. Nothing in `pipeline/` changes.
    """
    from tools_fs import Reranker  # type: ignore

    if getattr(Reranker, "_artha_fallback_installed", False):
        return

    original = Reranker.rerank_chunks

    def rerank_chunks(query: str, chunks: list) -> list:
        try:
            return original(query, chunks)
        except Exception as exc:  # noqa: BLE001 - degrade, never abort
            logger.warning(
                "[%s] Reranker unavailable (%s: %s) — keeping similarity order "
                "for %d chunk(s). Ranking is coarser; the evidence is unchanged.",
                MODE_ID, type(exc).__name__, exc, len(chunks),
            )
            return chunks

    Reranker.rerank_chunks = staticmethod(rerank_chunks)
    Reranker._artha_fallback_installed = True


def _reports_conn_alive(fs_api) -> tuple[bool, str | None]:
    """Is the reports database actually usable right now?

    `api_server._get_reports_conn` only tests `conn.closed`, which stays 0 after
    the SERVER terminates a connection — the object looks fine until the next
    query fails. So this issues a real `SELECT 1` and, on failure, drops the
    cached handle so the next call reconnects instead of reusing a dead socket.
    """
    conn = fs_api._get_reports_conn()
    if conn is None:
        return False, "the connection could not be opened"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, None
    except Exception as exc:  # noqa: BLE001 - any failure means "reconnect"
        try:
            conn.close()
        except Exception:
            pass
        fs_api._reports_db_conn = None
        return False, f"{type(exc).__name__}: {exc}"


def _require_reports_db(fs_api) -> None:
    """Refuse to report this mode healthy when it cannot reach company filings.

    WHY THIS IS FATAL RATHER THAN A DEGRADATION
    -------------------------------------------
    When the reports DB is unreachable the branch sets `conn_reports = None`
    (agent.py) and `build_tools` then skips its entire company-tool block --
    eighteen of the twenty-four tools, including every one that can read an
    annual report. The mode stays GREEN in the sidebar and answers company
    questions by saying the data is not available, because from inside the agent
    that is indistinguishable from the data not existing.

    That is the failure this whole change set exists to remove, so it must not
    be left as the one path that still produces it. A red light naming the
    connection error sends someone to the database; a green light sends them
    looking for a company that was there all along.

    Set ARTHA_FS_ALLOW_NO_REPORTS_DB=1 to keep the old behaviour and serve
    standards-only answers.
    """
    alive, why = _reports_conn_alive(fs_api)
    if alive:
        return

    message = (
        f"Reports database unreachable ({why}) — without it the 18 company "
        f"tools are not registered and this mode cannot answer any question "
        f"about a company's filings."
    )
    if os.environ.get("ARTHA_FS_ALLOW_NO_REPORTS_DB", "").strip() in {"1", "true", "yes"}:
        logger.error("[%s] %s Serving standards-only answers because "
                     "ARTHA_FS_ALLOW_NO_REPORTS_DB is set.", MODE_ID, message)
        return
    raise RuntimeError(message)


def _warm_table_config(fs_api) -> None:
    """Replicate the branch's FastAPI lifespan startup step.

    Importing `api_server` does NOT run its lifespan handler, and that handler
    is what populates `Config.TABLE_CONFIG` via table auto-discovery. Skipping
    it leaves TABLE_CONFIG empty, which makes every rules-DB search return "no
    chunks found" while the service still looks healthy — so we do it here.
    """
    from tools_fs import Config as config, Database as db  # type: ignore

    conn = db.get_connection()
    try:
        discovered = db.discover_table_config(conn)
    finally:
        conn.close()

    if not discovered:
        raise RuntimeError(
            "No tables discovered in the rules database — check DB_* settings. "
            "Serving this mode now would silently return empty evidence."
        )

    config.TABLE_CONFIG = discovered
    config.HTML_CONTENT_TABLES = {
        t["table_name"]
        for t in discovered
        if t["content_column"] in {"table_html", "table_content"}
    }
    print(f"[{MODE_ID}] {len(discovered)} table(s) loaded into TABLE_CONFIG.")


# ---------------------------------------------------------------------------
# Answer post-processing
# ---------------------------------------------------------------------------

# The pipeline caps its own confidence when a tool reports a figure it could not
# extract, and renders a blockquote saying so:
#
#   > Confidence was reduced from *High* automatically: a tool reported figures
#   > it could not extract or checks it could not perform, so parts of this
#   > answer rest on incomplete data.
#
# `api_server._clean_section` already strips the `**Confidence**:` line for the
# UI -- its docstring says these "must NOT appear in the client-facing UI" -- but
# it matches on that literal prefix and this blockquote slips past. Removed here
# at the user's request rather than in `pipeline/`, so the vendored tree stays
# byte-for-byte re-pullable and this is one line to revert.
#
# NOTE: this removes the DISPLAY of the caveat only. The pipeline still detects
# the gap and still caps its internal confidence; nothing about how the answer
# is produced or which evidence it rests on changes.
_CAVEAT_OPENER = "confidence was reduced from"

_CONFIDENCE_RE = re.compile(r"\*\*confidence\*\*\s*:\s*(high|medium|low)\b", re.I)
_REDUCED_RE = re.compile(
    r">\s*confidence was reduced from\s*\*?(high|medium|low)\*?\s*automatically\s*:\s*(.+)", re.I
)
_TOOLS_RE = re.compile(r"\*\*tools\s+used\*\*\s*:\s*(.+)", re.I)
_UNSOURCED_RE = re.compile(r">\s*\*\*no tool was called for this answer\*\*", re.I)


def _extract_checks(answer: str) -> dict:
    """How the answer was checked, read off the rendered answer BEFORE the UI
    copy of it is cleaned.

    Confidence, the tools used and the unsourced notice are stripped from the
    text the reader sees (`_strip_confidence_caveat`, `api_server._clean_section`)
    -- that display decision stands. They are returned here as separate fields so
    the interface can offer them in one collapsed "How this was checked" section
    instead of dropping the information altogether. Nothing here changes what the
    pipeline computes; it only reads what it already wrote.
    """
    text = answer or ""
    confidence = _CONFIDENCE_RE.search(text)
    reduced = _REDUCED_RE.search(text)
    tools = _TOOLS_RE.search(text)
    return {
        "confidence": confidence.group(1).capitalize() if confidence else None,
        "reduced_from": reduced.group(1).capitalize() if reduced else None,
        "reduced_reason": reduced.group(2).strip() if reduced else None,
        "tools_used": [t.strip() for t in tools.group(1).split(",") if t.strip()] if tools else [],
        "unsourced": bool(_UNSOURCED_RE.search(text)),
    }


def _strip_confidence_caveat(text: str) -> str:
    """Drop the auto-generated confidence-reduction blockquote.

    Line-based rather than a regex: the caveat is a markdown blockquote that
    may wrap onto continuation lines, and dropping the opener while leaving
    its `>` continuations behind would show the reader a fragment of a
    sentence -- worse than either keeping it or removing it cleanly.
    """
    if not text or _CAVEAT_OPENER not in text.lower():
        return text

    out: list[str] = []
    dropping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") and _CAVEAT_OPENER in stripped.lower():
            dropping = True
            continue
        # Keep swallowing the blockquote's continuation lines.
        if dropping and stripped.startswith(">"):
            continue
        dropping = False
        out.append(line)

    cleaned = "\n".join(out)
    # Collapse the blank run the removal leaves behind, the same way
    # api_server._clean_section does after its own strips.
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def status() -> tuple[bool, str | None]:
    """(available, reason) — never raises. Used by GET /api/modes."""
    try:
        _load()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


def run_query(
    query: str,
    *,
    history: list[dict] | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Run the full FS RAG pipeline for one query. Blocking — call in a thread.

    `history` is the earlier turns of this conversation, read server-side from
    the authenticated user's stored messages. When it is empty — a first
    question, or a caller that does not use conversations — the query is sent
    exactly as typed and no rewrite happens at all, so this path is byte-for-byte
    what it was before conversations existed.
    """
    fs_api = _load()

    # Re-checked per request, not just at load: this connection is long-lived
    # and the server can terminate it at any point in between. Failing here with
    # the connection error is honest; running without it would silently drop the
    # 18 company tools and answer "no data for that company" instead.
    alive, why = _reports_conn_alive(fs_api)
    if not alive and not os.environ.get("ARTHA_FS_ALLOW_NO_REPORTS_DB", "").strip():
        raise ModeUnavailableError(
            MODE_ID,
            f"Reports database unreachable ({why}). Company questions cannot be "
            f"answered until it is back; this is a connection failure, not an "
            f"absence of data.",
        )

    # Resolve a follow-up into a standalone question BEFORE retrieval, which is
    # the same order SAR Q&A uses. Degrades to the original on any failure.
    from . import rewriter

    effective = rewriter.rewrite(query, history or [])

    # Bind this request to the documents uploaded into THIS conversation. The
    # bridge's wrappers and the four upload-only tools both read the scope from
    # a ContextVar, because the tool closures the agent framework calls have no
    # parameter to thread it through. Set for the duration of the answer and
    # reset immediately after, so nothing leaks into the next request.
    from .upload import materiality as materiality_mod
    from .upload import store as upload_store

    # A Redis outage here is deliberately not treated as a reason to fail the
    # whole question. `scope_for` failing means we don't yet know whether this
    # conversation has an upload in scope -- corpus-only questions ("what does
    # Ind AS 115 require?") have nothing to do with that and must not be taken
    # down by it. But silently proceeding as if there were no upload would be
    # actively misleading for a conversation that genuinely has one, so the
    # failure is carried forward as a visible notice on the answer instead --
    # the same "say so when something couldn't be verified" pattern
    # materiality_legend/extraction_caveats already use elsewhere in this
    # feature, rather than staying silent about it.
    scope = None
    upload_store_notice = None
    if user_id:
        try:
            scope = upload_store.scope_for(user_id, conversation_id)
        except upload_store.UploadStoreError as exc:
            logger.warning(
                "uploaded-document store unavailable for this query "
                "(falling back to corpus-only): %s", exc,
            )
            upload_store_notice = (
                "Whether any documents are attached to this conversation could "
                "not be checked just now (the upload store was unreachable), so "
                "this answer reflects only the general corpus."
            )
    token = upload_store.set_scope(scope) if scope else None
    if scope and scope.documents:
        try:
            upload_store.STORE.touch(user_id or "", conversation_id or "")
        except upload_store.UploadStoreError as exc:
            # A housekeeping call (TTL refresh) failing must not fail an
            # otherwise-working answer.
            logger.warning("could not refresh %s's TTL: %s", conversation_id, exc)
        effective = _name_the_upload(effective, scope)

    started = time.perf_counter()
    threshold = None
    try:
        result = fs_api._orchestrator.answer(
            effective,
            conn=fs_api._get_conn(),
            conn_reports=fs_api._get_reports_conn(),
        )

        # Computed HERE, inside the scope, not after it.
        #
        # `_provisional_materiality` reads the document's figures through the
        # same bridged tools the agent uses, and the bridge decides what to serve
        # from the ContextVar. Run after `reset_scope` it saw no upload, fell
        # through to Postgres, found nothing, and every answer came back with no
        # legend at all -- while every unit test passed, because they set the
        # scope by hand and never exercised this ordering.
        if scope and scope.documents:
            threshold = materiality_mod.REGISTRY.get(user_id or "", conversation_id or "")
            if threshold is None:
                threshold = _provisional_materiality(fs_api, scope)
    finally:
        if token is not None:
            upload_store.reset_scope(token)

    if "error" in result:
        raise ModeUnavailableError(
            MODE_ID,
            f"Pipeline error at stage '{result['error']}': {result.get('message', '')}",
        )

    checks = _extract_checks(result.get("answer", ""))
    structured = fs_api._parse_structured_answer(result.get("answer", ""))

    # The materiality legend travels with the answer rather than being left for
    # the model to remember. The requirement is that any flag or risk rating
    # states the threshold it was derived against, and a legend the renderer
    # always has is a stronger guarantee than a sentence the model is asked to
    # write. Present only when documents were in scope: a corpus answer keeps
    # exactly the payload shape it had before this feature.
    legend = None
    if threshold is not None:
        legend = {
            "markdown": materiality_mod.legend(threshold),
            **threshold.as_dict(),
        }

    return {
        "mode": MODE_ID,
        # The user's own words, not the rewritten form — the UI echoes this back
        # in the thread and it must match what they typed.
        "query": query,
        "rewritten_query": effective if effective != query else "",
        "summary": _strip_confidence_caveat(structured["summary"]),
        "final_answer": _strip_confidence_caveat(structured["final_answer"]),
        "evidences_md": structured["evidences_md"],
        "chunks": fs_api._format_chunks_for_api(result.get("retrieved_chunks", [])),
        "num_tables_searched": result.get("num_tables_searched", 0),
        "num_chunks_retrieved": result.get("num_chunks_retrieved", 0),
        "elapsed_seconds": result.get(
            "total_elapsed_seconds", round(time.perf_counter() - started, 2)
        ),
        "materiality_legend": legend,
        "uploaded_documents": [d.summary() for d in scope.documents] if scope else [],
        "upload_store_notice": upload_store_notice,
        "checks": checks,
    }



def _name_the_upload(question: str, scope) -> str:
    """Make an uploaded document the implicit subject of an unqualified question.

    Every company tool takes a company and a financial year, and prompt rule 16a
    tells the model to pass them exactly as the user wrote them. When a document
    has been uploaded the user does not write them at all -- they ask "what are
    the total assets?" about the file in front of them, the way they would of
    Claude or ChatGPT. Measured against the live model, that question came back
    as "I cannot answer because you haven't specified a company or a financial
    year", with the document sitting in scope the whole time.

    So the subject is supplied here, deterministically, rather than hoped for
    from the model. This is the same seam the follow-up rewriter already uses:
    the user's own words are still what the UI echoes and what is stored, and
    only the text handed to the agent is made explicit.

    Left alone when the question already names a company, so asking about a
    different entity still reaches the corpus.
    """
    documents = getattr(scope, "documents", None) or []
    if not documents or not question:
        return question

    lowered = question.lower()

    # Already explicit about some entity: do not steer it towards the upload.
    for document in documents:
        name = (document.company or "").strip().lower()
        if name and (name in lowered or lowered in name):
            return question
    # A question that names a year is usually naming a filing too.
    if any(str(y) in question for y in range(1990, 2100, 1)) and "upload" not in lowered:
        # Only skip when it ALSO names something entity-like; a bare year with
        # an uploaded document still refers to that document.
        pass

    described = []
    for document in documents[:4]:
        company = document.company or "the uploaded entity"
        year = document.financial_year or "year not stated in the document"
        described.append(f"{company} ({year}), file {document.filename}")

    context = (
        "[Context: the user has uploaded "
        + ("this document" if len(described) == 1 else f"{len(described)} documents")
        + " into this conversation: " + "; ".join(described) + ". "
        "The question below is about "
        + ("it" if len(described) == 1 else "them")
        + " unless it names a different company. Pass that company name and "
        "financial year to the company tools; do not ask the user to supply "
        "them, and do not answer that no company was specified.]\n\n"
    )
    return context + question

def _provisional_materiality(fs_api, scope):
    """A provisional band from the first uploaded document that yields figures.

    Computed from the statements rather than assumed, and labelled provisional
    so the legend can say so. Failure is not an error: with no extractable
    benchmark the legend states that no threshold is in force, which is the
    honest outcome and better than a number nobody can justify.
    """
    from .upload import materiality as materiality_mod

    try:
        import tools_fs  # type: ignore
    except Exception:
        return None

    conn_reports = fs_api._get_reports_conn()
    for document in scope.documents:
        try:
            figures = tools_fs.RatioExtractionEngine.extract_all_figures(
                document.doc_id, conn_reports
            )
        except Exception:
            continue
        if not figures:
            continue
        try:
            units = tools_fs.UnitResolver.resolve(document.doc_id, conn_reports)
        except Exception:
            units = {}
        band = materiality_mod.provisional_from_figures(figures, units.get("label"))
        if band.amount is not None:
            return band
    return None
