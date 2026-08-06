# Backend — mode gateway

One FastAPI app fronting one pipeline per mode.

```
app/
  main.py       app construction, .env loading, router registration
  registry.py   the single source of truth for what modes exist
  schemas.py    QueryRequest/Response (chat), ReportRequest/Response (SAR),
                TB*Request/Response (Trial Balance)
  errors.py     ModeUnavailableError (503) and ModeNotIntegratedError (501)
modes/
  <mode>/
    adapter.py  the ONLY file that knows how to call that branch's pipeline
    router.py   that mode's URL namespace
    <branch>/   the branch's own code, vendored verbatim — do not edit
```

## The contract every mode implements

```python
MODE_ID: str
BRANCH: str

def status() -> tuple[bool, str | None]:
    """(available, reason). Never raises — this backs GET /api/modes?probe=true."""

# chat modes
def run_query(query: str) -> dict:      # -> QueryResponse shape

# report modes
def catalog() -> list[dict]             # -> [{entity, years: [...]}]
def generate_report(entity, fy_start, fy_end, scope) -> dict

# trial-balance modes (upload once, get a doc_id, run independent analyses
# against it — see modes/trial_balance/adapter.py for the worked example)
def upload(filename, data) -> dict
def preview(token) -> dict              # column-mapper fallback
def upload_mapped(token, ...) -> dict
def list_documents() -> list[dict]
def delete_document(doc_id) -> bool
def ask(doc_id, question, session_id=None) -> dict
def audit(doc_id, doc_id_prior=None, ..., grouping_token=None) -> dict
def validate(doc_id, doc_id_prior=None, **params) -> dict
def validate_upload(data, label=None, data_prior=None, ...) -> dict   # raw bytes
def upload_grouping(filename, data, doc_id=None, doc_id_2=None) -> dict
def upload_grouping_mapped(token, ...) -> dict
def upload_pdf(filename, data) -> dict        # optional PDF evidence
def list_pdfs() -> list[dict]
def delete_pdf(doc_id) -> bool
def pdf_status() -> tuple[bool, str | None]   # that sub-feature's own health
```

Adapters raise `ModeUnavailableError` for anything the caller could act on; the
routers convert it to a 503 carrying the reason verbatim. Nothing is imported
at module scope, so an unconfigured or broken mode cannot take the gateway
down with it.

## Why pipelines load lazily

All three integrated sources do real work at import or construction time —
the Financial Statement branch's `Config` class raises if `DB_PASSWORD` is
unset, the SAR pipeline builds four LLM agents in its constructor, and Trial
Balance's `TBAnalysisPipeline`/`AuditPipeline` each build their own LLM
clients and agents in `__init__`. Importing eagerly would mean the whole
gateway fails to start whenever any single mode is misconfigured. Instead
each adapter imports inside a lock on first use, caches the result, and turns
any failure into that mode's `status()` reason. Trial Balance additionally
splits this per sub-feature — `status()` (and upload/list/delete/`validate`)
only needs the database to come up; `ask`/`audit` lazily build their own
pipeline singleton on first call, so a missing `yukta` install degrades only
those two, not the whole mode. Two of its entry points need even less:
`validate_upload` and the grouping-file uploads parse with pandas alone, so
they answer correctly with no database and no `yukta` at all. One needs more:
PDF evidence has its own database and embedding endpoint, so it gets a third
loader and reports through `pdf_status()` rather than the mode's `status()`.

Note that all these loaders cache their failure for the process's lifetime — the
probe runs once. Provisioning a missing database therefore needs a restart to be
picked up, which is the intended trade for not re-attempting a dead connection
on every request.

## Blocking work

All three integrated pipelines are synchronous (psycopg2, blocking LLM
round-trips). Every route offloads via `asyncio.to_thread` — running them on
the event loop would stall the entire gateway for the duration of a request.

## Running

```bash
uvicorn app.main:app --reload --port 12101
```

Interactive API docs: <http://localhost:12101/docs>

12101 is this project's reserved port (`ARTHA_BACKEND_PORT` in the root `.env`).
Containerised, the whole stack comes up with `docker compose up -d --build` —
see [../DOCKER.md](../DOCKER.md).

Note that a single worker is a correctness requirement, not a default worth
tuning: Trial Balance's `ask` mode holds conversation memory in an in-process
`SessionStore`, and each mode's pipeline is imported lazily into whichever
worker first serves it. Scale with replicas, not `--workers`.
