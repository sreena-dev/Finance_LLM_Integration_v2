"""
api_server.py — FastAPI REST backend for the Financial Audit RAG system.

Serves:
  1. POST /api/query          — runs the RAG pipeline, returns clean JSON
  2. GET  /                   — serves the client-facing HTML/JS/CSS frontend

Run with:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import re
import sys
from pathlib import Path

# Windows defaults stdout to the console codepage (cp1252 here), and every log
# line carrying a rupee sign, bullet or arrow then raises UnicodeEncodeError.
# That is not cosmetic: one such line inside discover_table_config once aborted
# startup discovery and left the rules-DB search silently returning nothing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import tools_fs as tool_names
from tools_fs import Config as config, Database as db
import agent
import tracing_setup

_orchestrator = agent.Orchestrator()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Register Phoenix tracing, then populate TABLE_CONFIG by auto-discovering DB tables."""
    if tracing_setup.setup_tracing():
        print(f"[api_server] Startup: Phoenix tracing enabled (project={config.PHOENIX_PROJECT_NAME}).")
    try:
        conn = db.get_connection()
        discovered = db.discover_table_config(conn)
        conn.close()
        if discovered:
            # Assigned before anything else can raise. An empty TABLE_CONFIG is
            # not a degraded mode — it makes every rules-DB search return "no
            # chunks found" while looking perfectly healthy.
            config.TABLE_CONFIG = discovered
            config.HTML_CONTENT_TABLES = {
                t["table_name"]
                for t in discovered
                if t["content_column"] in {"table_html", "table_content"}
            }
            print(f"[api_server] Startup: {len(discovered)} table(s) loaded into TABLE_CONFIG.")
        else:
            print("[api_server] WARNING: No tables discovered at startup — check DB connection.")
    except Exception as exc:
        print(f"[api_server] ERROR during startup table discovery: {exc}")
    yield  # app runs here


app = FastAPI(
    title="Artha.AI API",
    version="1.0.0",
    description="Client-facing API for Artha.AI — the Financial Audit RAG system.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend files
FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ---------------------------------------------------------------------------
# Shared DB connection (module-level; re-opened if closed)
# ---------------------------------------------------------------------------

_db_conn = None
_reports_db_conn = None


def _get_conn():
    global _db_conn
    try:
        if _db_conn is None or _db_conn.closed:
            _db_conn = db.get_connection()
    except Exception:
        _db_conn = db.get_connection()
    return _db_conn


def _get_reports_conn():
    """Reports DB is optional — returns None if it isn't configured/reachable."""
    global _reports_db_conn
    try:
        if _reports_db_conn is None or _reports_db_conn.closed:
            _reports_db_conn = db.get_reports_connection()
    except Exception as exc:
        print(f"[api_server] Reports DB unavailable: {exc}")
        _reports_db_conn = None
    return _reports_db_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# HTML tables are dynamically configured at startup in config.HTML_CONTENT_TABLES

_strip_html = tool_names.Reranker._strip_html


def _clean_section(text: str) -> str:
    """Remove confidence score, tools_used, chain-of-thought, and other
    internal fields that must NOT appear in the client-facing UI."""
    # Strip <details>…</details> blocks (reasoning trace / chain-of-thought)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)
    # Strip **Confidence**: … lines (any capitalisation)
    text = re.sub(r"\*\*[Cc]onfidence\*\*\s*:.*", "", text)
    # Strip Confidence: … plain lines
    text = re.sub(r"^[Cc]onfidence\s*:.*$", "", text, flags=re.MULTILINE)
    # Strip **Tools used**: … lines
    text = re.sub(r"\*\*[Tt]ools\s+used\*\*\s*:.*", "", text)
    # Strip tools_used: … plain lines
    text = re.sub(r"^[Tt]ools\s+used\s*:.*$", "", text, flags=re.MULTILINE)
    # Collapse excessive blank lines introduced by stripping
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(text: str) -> list[tuple[str, str]]:
    """
    Split cleaned markdown into (heading, body) pairs on top-level '### '
    headings. Content before the first heading (if any) is captured with
    heading=''.
    """
    parts = re.split(r"\n(?=### )", text.strip())
    sections = []
    for part in parts:
        m = re.match(r"### (.+?)\n(.*)", part, re.DOTALL)
        if m:
            sections.append((m.group(1).strip(), m.group(2).strip()))
        elif part.strip():
            sections.append(("", part.strip()))
    return sections


def _parse_structured_answer(answer_markdown: str) -> dict:
    """
    Break the LLM answer markdown into structured sections:
      - summary        : the ### Summary section
      - final_answer   : everything else EXCEPT Summary and Evidences &
                          Citations — not just a section literally titled
                          "### Final Answer". Verified real bug: an Audit
                          Risk Analysis answer used topic-specific headings
                          ("### 1. Movement Analysis", "### 2. Key Risk
                          Factors...") instead of a literal "Final Answer"
                          heading. The old exact-heading regex found nothing,
                          left final_answer empty, and the frontend's
                          fallback re-displayed the Summary text in its
                          place — visually duplicating the summary and
                          silently dropping the entire substantive answer.
      - evidences_md   : the ### Evidences & Citations section

    If no "### " headings are found at all (plain-text fallback), the whole
    cleaned text goes into final_answer.

    Confidence scores, tools_used, and reasoning traces are always removed
    before returning — they must never appear in the client-facing UI.
    """
    cleaned = _clean_section(answer_markdown)
    sections = _split_sections(cleaned)

    summary = ""
    evidences_md = ""
    remaining_parts = []

    for heading, body in sections:
        h_lower = heading.lower()
        if h_lower == "summary":
            summary = body
        elif h_lower == "evidences & citations":
            evidences_md = body
        else:
            # Preserve whatever heading the model actually used (if any) —
            # "Final Answer", "Movement Analysis", "Risk Factors", etc. all
            # render as their own subsections rather than being dropped for
            # not matching one exact expected heading string.
            remaining_parts.append(f"### {heading}\n{body}" if heading else body)

    final_answer = "\n\n".join(remaining_parts).strip()

    # Ultimate fallback: no recognizable sections at all.
    if not summary and not final_answer and not evidences_md:
        final_answer = cleaned

    return {
        "summary": summary,
        "final_answer": final_answer,
        "evidences_md": evidences_md,
    }


def _format_chunks_for_api(chunks: list[dict]) -> list[dict]:
    """Format retrieved chunks into a clean list for the frontend."""
    out = []
    for i, chunk in enumerate(chunks, start=1):
        label = chunk.get("table_label", chunk.get("table_name", "Unknown"))
        sim = chunk.get("similarity", 0.0)
        rerank = chunk.get("rerank_score", 0.0)
        is_hint = chunk.get("_matched_hint", False)
        content = chunk.get("content", "")
        if chunk.get("table_name") in config.HTML_CONTENT_TABLES:
            content = _strip_html(content)

        skip_keys = {
            "table_name", "table_label", "id",
            "content", "similarity", "rerank_score", "_matched_hint",
        }
        metadata = {
            k: v for k, v in chunk.items()
            if k not in skip_keys and v is not None and v != ""
        }

        out.append({
            "index": i,
            "source": label,
            "similarity": round(sim, 4),
            "rerank_score": round(rerank, 4),
            "hint_matched": is_hint,
            "content": content,
            "metadata": metadata,
        })
    return out


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    query: str
    summary: str
    final_answer: str
    evidences_md: str
    chunks: list[dict]
    num_tables_searched: int
    num_chunks_retrieved: int
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/query", response_model=QueryResponse)
async def run_query(req: QueryRequest):
    """Run the full RAG pipeline and return a clean, structured response.

    retrieve_and_answer() is CPU/IO-bound (blocking requests, psycopg2 calls)
    so we offload it to a threadpool executor to avoid blocking the async
    event loop — this ensures the LLM tool-calling iterations run to completion.
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,  # default threadpool
        lambda: _orchestrator.answer(query, conn=_get_conn(), conn_reports=_get_reports_conn()),
    )

    if "error" in result:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline error at stage '{result['error']}': {result.get('message', '')}",
        )

    structured = _parse_structured_answer(result.get("answer", ""))

    return QueryResponse(
        query=result["query"],
        summary=structured["summary"],
        final_answer=structured["final_answer"],
        evidences_md=structured["evidences_md"],
        chunks=_format_chunks_for_api(result.get("retrieved_chunks", [])),
        num_tables_searched=result.get("num_tables_searched", 0),
        num_chunks_retrieved=result.get("num_chunks_retrieved", 0),
        elapsed_seconds=result.get("total_elapsed_seconds", 0.0),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": config.LLM_MODEL_NAME}


# ---------------------------------------------------------------------------
# Serve the frontend HTML
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Frontend not found. Ensure the frontend/ directory exists.</h1>", status_code=404)
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon to suppress browser 404 noise in logs."""
    favicon_path = FRONTEND_DIR / "favicon.ico"
    if favicon_path.exists():
        return Response(content=favicon_path.read_bytes(), media_type="image/x-icon")
    # Minimal valid SVG favicon — no file needed (matches client/public/favicon.svg)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#1e3a5f"/>'
        '<path d="M9 22L15 10L21 22" stroke="#ffffff" stroke-width="2.6" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M11.3 17.5H18.7" stroke="#b8860b" stroke-width="2.2" stroke-linecap="round"/>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")
