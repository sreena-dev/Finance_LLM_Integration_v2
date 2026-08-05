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

import sys
import threading
import time
from pathlib import Path

from app.errors import ModeUnavailableError

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
            _warm_table_config(fs_api)
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
# Public surface
# ---------------------------------------------------------------------------

def status() -> tuple[bool, str | None]:
    """(available, reason) — never raises. Used by GET /api/modes."""
    try:
        _load()
        return True, None
    except ModeUnavailableError as exc:
        return False, exc.reason


def run_query(query: str) -> dict:
    """Run the full FS RAG pipeline for one query. Blocking — call in a thread."""
    fs_api = _load()

    started = time.perf_counter()
    result = fs_api._orchestrator.answer(
        query,
        conn=fs_api._get_conn(),
        conn_reports=fs_api._get_reports_conn(),
    )

    if "error" in result:
        raise ModeUnavailableError(
            MODE_ID,
            f"Pipeline error at stage '{result['error']}': {result.get('message', '')}",
        )

    structured = fs_api._parse_structured_answer(result.get("answer", ""))

    return {
        "mode": MODE_ID,
        "query": result["query"],
        "summary": structured["summary"],
        "final_answer": structured["final_answer"],
        "evidences_md": structured["evidences_md"],
        "chunks": fs_api._format_chunks_for_api(result.get("retrieved_chunks", [])),
        "num_tables_searched": result.get("num_tables_searched", 0),
        "num_chunks_retrieved": result.get("num_chunks_retrieved", 0),
        "elapsed_seconds": result.get(
            "total_elapsed_seconds", round(time.perf_counter() - started, 2)
        ),
    }
