# Artha.AI — Integrated Audit Platform

A single frontend and a single backend gateway over several independent
pipelines. Four modes come from branches of
[`Fin_Audit_QA`](https://github.com/HARISH-027/Fin_Audit_QA); Trial Balance was
originally vendored from
[`Finance_llm_v2`](https://github.com/naveenchandramohan/Finance_llm_v2) and has
since been replaced wholesale by **TB-v2** (see *Trial Balance is now TB-v2*
below).

**Five modes are registered**, four of which are top-level in the sidebar — SAR
Q&A is presented as a Report/Chat toggle inside the Statutory Auditor's Report
mode rather than as its own entry (`companion_of` in `app/registry.py`).

| Mode | Source | UI (`registry.ui`) | Status |
|---|---|---|---|
| Statutory Auditor's Report | `Fin_Audit_QA` / `Statutory_Auditor_Report` | `report` — two dropdowns → report | **Working** |
| SAR Q&A | `Fin_Audit_QA` / `Statutory_Auditor_Report` | `report-chat` — entity/FY picker → chat | **Broken — `/ask` returns 503** |
| Financial Statements | `Fin_Audit_QA` / `Financial_Statement` | `chat` — corpus Q&A, plus upload a scan → live-ingested chat + editable document pane | **Working** — see *Financial Statements — live document upload* below |
| Trial Balance | TB-v2 | `trial-balance` — ask, or upload → audit | **Working** |
| Financial Diagnostic Report | `Fin_Audit_QA` / `Financial_Diagnostic_Report` | `fdr` — setup → workspace (report + Q&A) | **Working** |

---

## Layout

```
Integrated/
├── backend/
│   ├── app/
│   │   ├── main.py              gateway: router loading, auth wiring, health
│   │   ├── registry.py          the single source of truth for what modes exist
│   │   ├── mode_loader.py       imports each router in isolation (see below)
│   │   ├── schemas.py, errors.py, tracing.py
│   │   └── auth/                signup/login/me + artha_users, artha_fs_messages
│   ├── modes/
│   │   ├── financial_statement/
│   │   │   ├── adapter.py       thin wrapper over the branch pipeline
│   │   │   ├── router.py        /api/financial-statement/*
│   │   │   ├── conversations.py server-side chat history, scoped in the SQL
│   │   │   ├── rewriter.py      folds history + new question into one query
│   │   │   ├── entity_resolution.py  runtime-rebound DocumentResolver methods
│   │   │   ├── pipeline/        ← Financial_Statement branch, VERBATIM
│   │   │   └── upload/          live-ingested documents: store, bridge, editable cells
│   │   │                        (see *Financial Statements — live document upload*)
│   │   ├── statutory_auditor_report/
│   │   │   ├── adapter.py
│   │   │   ├── router.py        /api/statutory-auditor-report/*
│   │   │   └── sar_prod_v3/     ← Statutory_Auditor_Report branch Prod/, VERBATIM
│   │   ├── sar_chat/            adapter + router only; reuses sar_prod_v3
│   │   ├── trial_balance/
│   │   │   ├── adapter.py       upload/list/delete + ask/audit/validate/grouping
│   │   │   ├── router.py        /api/trial-balance/*
│   │   │   ├── tests/           371 tests — all passing
│   │   │   └── pipeline/        ← TB-v2: agent.py, tools.py, db.py, knowledge/
│   │   └── financial_diagnostic_report/
│   │       ├── adapter.py, engine.py, intent.py, answers.py, retrieval.py, …
│   │       └── pipeline/{fdr,fs_db}/   ← vendored VERBATIM
│   ├── scripts/audit_fs_entities.py   read-only entity-resolution audit
│   ├── requirements.txt
│   └── .env.example
├── ingestion/                    scanned PDF -> verified tables (own container, port 12102)
│   ├── app/                      render -> precheck -> preprocess -> convert -> vlm
│   │                             second read -> verify -> identify -> emit (see its own README)
│   ├── tests/accuracy/           human-verified-figure harness against data/
│   └── README.md                 the pipeline deep-dive; start there for docling/OCR/VLM detail
├── data/                         local sample corpus (gitignored — see *live document upload*)
└── frontend/                    React 18 + Vite
    └── src/
        ├── auth/                sign-in screen + token handling
        ├── api/client.js        every call keyed off the mode's base_path
        └── components/
            ├── chat/            query-driven modes + conversation list
            ├── report/          the auditor's-report form + document
            ├── trial-balance/   one stream: ask, upload, audit + result cards
            ├── ingestion/       upload flow, document pane, editable cells, quality report
            └── financial-diagnostic-report/
```

**One folder per branch, and the branch code inside it is untouched.** Branch
files are vendored exactly as they appear on origin, so you can re-pull a
branch over its folder without resolving a merge conflict. Each mode's
`adapter.py` is the only file that knows how to call into that pipeline.

---

## Mode isolation

Each mode owns a distinct URL namespace:

```
/api/health                                          ← open; the container healthcheck polls it
/api/auth/{signup,login,me,health}                   ← signup/login/health are the only other open routes
/api/modes[?probe=true]

/api/statutory-auditor-report/{catalog,generate,health}
/api/sar-chat/{catalog,ask,health}
/api/financial-statement/{query,health}
/api/financial-statement/conversations[/{conversation_id}]           GET, GET/DELETE
/api/trial-balance/{upload,preview,upload-mapped,documents,health}
/api/trial-balance/documents/{doc_id}                                GET, DELETE
/api/trial-balance/{ask,audit,audit/workbook,audit/upload-grouping,validate}
/api/trial-balance/ask-general                       ← corpus Q&A, no TB needed
/api/financial-diagnostic-report/{entities,query,query/stream,cache/invalidate,health}
```

That list is generated from the live `app.openapi()` surface, not from memory.
Earlier revisions of this file also documented `/api/trial-balance/pdfs*`,
`/api/trial-balance/validate/upload` and `/audit/upload-grouping-mapped`; **none
of those exist in TB-v2** and they have been removed here.

The frontend never hard-codes a URL. It reads `GET /api/modes`, and every call
is built from the `base_path` the gateway reports for the selected mode — so
selecting one mode structurally cannot reach another's pipeline.

Isolation also holds at import time. Two branches can ship a same-named
top-level module (e.g. both calling their own orchestrator `agent.py`) and a
naive `sys.path` + bare `import` would let one silently shadow the other —
whichever loaded first in the process would "win" that module-cache key. Each
adapter puts only its own directory on `sys.path` and imports lazily, on first
request, to avoid that collision.

---

## Running it

### With Docker (recommended)

```bash
cp .env.example .env                  # then fill in credentials
./scripts/build-yukta-wheel.sh        # once — yukta is not on PyPI
docker compose up -d --build
```

Open **http://localhost:12100**. Ports occupy one reserved 121xx series, the
timezone is IST in both containers, and all configuration comes from the single
root `.env`. See **[DOCKER.md](DOCKER.md)** for the full picture — port map,
networking, hardening, and why the gateway runs exactly one worker.

This also brings up the **ingestion service** (`ingest`, port 12102 — docling +
OCR + the VLM second read, behind `ARTHA_INGEST_URL`) and **Redis** (`redis`,
port 12105 — the durable store for live-ingested documents, behind
`ARTHA_REDIS_URL`). `ARTHA_INGEST_URL` unset only disables the upload
affordance itself. **`ARTHA_REDIS_URL` is a harder dependency than that:**
`POST /api/financial-statement/query` checks the upload store on every request
that carries a `conversation_id` — which a returning conversation always does,
upload or not — so any Redis outage 503s every follow-up turn of Financial
Statements chat, not only document upload. Only a brand-new conversation's
first question (no `conversation_id` yet) is unaffected. See *Financial
Statements — live document upload* below.

### Or from source

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
uvicorn app.main:app --reload --port 12101
```

Configuration comes from `.env` — the one at the repo root, or `backend/.env` if
you prefer it next to the code (`app/main.py` checks both, most specific first).
Copy `.env.example` from the root and fill it in.

`yukta` is **not on PyPI**; install it from your internal index or a source
checkout. Without it, the LLM-backed paths report themselves unavailable but
the gateway still runs.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:12103
```

Both ports come from the root `.env` (`ARTHA_BACKEND_PORT`, `ARTHA_DEV_PORT`),
so the dev server and the Docker stack cannot drift apart. Vite proxies `/api`
to `127.0.0.1:$ARTHA_BACKEND_PORT`; set `VITE_API_TARGET=http://host:port` to
point at a gateway running elsewhere.

### 3. Ingestion service + Redis (optional — needed for document upload)

```bash
cd ingestion
python -m venv venv
venv/Scripts/pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
uvicorn app.main:app --port 12102
```

Plus a Redis instance reachable at `ARTHA_REDIS_URL` (e.g. `redis-server` on
the default port and `ARTHA_REDIS_URL=redis://localhost:6379/0`). Docling,
torch and RapidOCR are heavy, first-run weight downloads if
`INGEST_DOCLING_ARTIFACTS` is not pointed at a local cache — see
**[ingestion/README.md](ingestion/README.md)** for the three install gotchas
that cost real time (`docling-slim` vs `docling`, `onnxruntime`, Windows long
paths) and for the pipeline itself. As with Docker, corpus chat runs without
this step; a returning conversation's follow-up turns still need Redis
reachable (see the note above).

---

## Configuration

Everything lives in the root `.env`, or `backend/.env` if you prefer it next to
the code (`app/main.py` checks both, most specific first). See `.env.example`
for the annotated version. The settings that decide whether a mode comes up:

| Variable | Mode | Effect if missing |
|---|---|---|
| `DB_PASSWORD` | Financial Statements | mode reports unavailable |
| `FINANCE_DSN` | SAR, SAR Q&A, Financial Diagnostic Report | catalogs, generation and diagnostics unavailable |
| `TB_DB_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` | Trial Balance | falls back to `localhost:5433/trial_balance_db`, which will not resolve |
| `ARTHA_JWT_SECRET` | all — sign-in | `/api/auth/{signup,login}` answer 503 |
| `GENERATION_BASE_URL` + `GENERATION_API_KEY`, `EMBEDDING_BASE_URL`, `RERANKER_BASE_URL` | SAR, SAR Q&A, Financial Statements | answers fail; catalogs and `validate` still work |
| `TB_GENERATION_BASE_URL` / `TB_GENERATION_MODEL` | Trial Balance | `ask` / `ask-general` / `audit` fail; upload, preview and `validate` still work |
| `ARTHA_INGEST_URL` | Financial Statements | document upload reports unavailable; corpus chat unaffected |
| `ARTHA_REDIS_URL` | Financial Statements | any chat turn carrying a `conversation_id` 503s (see *Running it* above), not only upload |

Note the one that has no LLM dependency at all: the **Financial Diagnostic
Report needs only `FINANCE_DSN` and `psycopg` v3** — no generation endpoint, no
embedding endpoint, no vector store. It is deterministic Python over the corpus,
which is why it can answer while every model endpoint is down.

A mode that cannot load degrades **on its own**: the gateway and the other modes
keep serving, and the UI shows that mode's exact reason string (e.g. *"DB_PASSWORD
is not set"*) rather than a generic failure. Trial Balance degrades further, per
sub-feature: `ask` / `ask-general` / `audit` need the LLM stack, while
upload / preview / list / delete / `validate` need only the database.

**The one caveat to that guarantee is SAR Q&A** — its `status()` does not probe
its own pipeline, so it advertises itself as available while `/ask` fails. See
*Known problems*.

Financial Statements and SAR read the same `EMBEDDING_BASE_URL` /
`EMBEDDING_MODEL` names — if those two ever need different embedding endpoints,
that has to be reconciled before running them together. Trial Balance avoids the
collision by reading its own `EMBEDDING_URL` / `TB_GENERATION_BASE_URL` /
`TB_GENERATION_MODEL` names — see the comments in `.env.example`.

---

## Verified status

Last verified **2026-09-01**, against the live `.env` (embedding, reranker and
both generation endpoints reachable), by exercising the real HTTP surface with
`fastapi.testclient` — not by reading the code.

### Gateway and auth

| Check | Result |
|---|---|
| All five mode routers import | pass — `IMPORT_FAILURES` is empty |
| `GET /api/health` without a token | 200 |
| `GET /api/modes` without a token | 401 |
| `POST /api/auth/{signup,login}`, `GET /api/auth/me` | pass; wrong password → 401 |
| `GET /api/auth/health` | `available: true`, tables `artha_users`, `artha_fs_messages` |
| Frontend `npm run build` | pass |

### Modes

| Mode | Endpoint exercised | Result |
|---|---|---|
| Statutory Auditor's Report | `/catalog` | 200 — 50 entities |
| | `/generate` | 200 — full report in ~56 s (4 agents: MAIN, CARO, IFC, WRITER) |
| **SAR Q&A** | `/catalog` | 200 — 50 entities |
| | **`/ask`** | **503 — see *SAR Q&A is broken* below** |
| | `/health` | 200 `available: true` — **this is wrong**, see below |
| Financial Statements | `/query` | 200 — 2211-char answer in ~29 s |
| | multi-turn + rewriter + history | pass — follow-up rewritten to a standalone question, 4 messages persisted and re-read |
| | `/conversations`, `/conversations/{id}` | 200; unknown id → 404 |
| Trial Balance | `/documents` | 200 — 8 stored TBs |
| | `/upload` (CSV) + `/preview` | 200 — token issued, preview parsed |
| | `/validate` | 200 — Layer 1: 5 passed, 0 halted, 8 warnings |
| | `/ask` (against a stored `doc_id`) | 200 — answered in ~25 s |
| | `/ask-general` | 200 — answered in ~19 s |
| | unknown `doc_id` | 404 |
| Financial Diagnostic Report | `/entities` | 200 — 50 entities |
| | `/query` | 200 — `kind=signal`, with provenance and computed rows, ~4 s |
| | `/query/stream` | 200 — SSE frames delivered |

### Test suites

**392 tests pass.** They need two things that are not obvious:

```bash
pip install pytest                       # NOT in requirements.txt
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1  # see below
python -m pytest modes/trial_balance/tests                  modes/financial_statement/test_entity_resolution.py                  modes/financial_diagnostic_report/test_intent.py
```

- `modes/trial_balance/tests` — 371 passed
- `modes/financial_statement/test_entity_resolution.py` — 16 passed
- `modes/financial_diagnostic_report/test_intent.py` — 5 passed

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is **required**: `arize-phoenix` ships a
pytest plugin whose import raises
`ValueError: mutable default <class 'mappingproxy'> ... use default_factory`
under Python 3.11, which aborts collection before any test file is read. It
looks like a failure in the project's own tests and is not one — the runtime app
is unaffected, because `app/tracing.py` never imports that path.

---

## Known problems

### SAR Q&A is broken — `/ask` returns 503

`POST /api/sar-chat/ask` fails on every request with:

```
ImportError: chat_tools.py: cannot locate the yukta_rag package. Expected it at
backend/modes/trial_balance/pipeline/yukta_rag, or in a directory above ...
```

**Cause.** `modes/statutory_auditor_report/sar_prod_v3/chat_tools.py` imports
`yukta_rag.retrieval.retrieval` and `yukta_rag.core.config` for its retrieval
layer. That package was never its own — it was vendored *under the Trial Balance
mode*, at `backend/modes/trial_balance/pipeline/yukta_rag/` (63 files), and
`chat_tools.py` reaches across to it deliberately, with the cross-mode
dependency commented at the site.

Replacing Trial Balance with TB-v2 (commit `38768bd`, reverted in `5d77cad`,
reapplied in `8249881`) deleted that directory. `git ls-tree -r HEAD | grep
yukta_rag` now returns nothing, and no copy exists anywhere else in the tree.
SAR Q&A was collateral damage — nothing in the SAR mode changed.

**It reports itself healthy anyway.** `modes/sar_chat/adapter.py`'s `status()`
only calls `catalog()`, which is a plain `documents` query against
`FINANCE_DSN`. It never touches `SARChatPipeline`, so `/api/sar-chat/health` and
`GET /api/modes?probe=true` both answer `available: true` while every `/ask`
503s — and the UI shows the mode as ready. This is the exact failure mode
`app/mode_loader.py` was written to prevent, one layer down: the import that
breaks is lazy and inside the adapter, so router-level isolation never sees it.

**To fix,** either restore the package and keep the cross-mode dependency:

```bash
git checkout 63eb893 -- backend/modes/trial_balance/pipeline/yukta_rag
```

or — better, since Trial Balance no longer uses it — vendor it under the mode
that actually needs it (`sar_prod_v3/`) and point `_find_yukta_rag_parent()`
there. Either way, `status()` should be made to probe `_get_chat_pipeline()` so
a broken pipeline stops advertising itself as available.

### Stale comment in `app/registry.py`

The Financial Diagnostic Report entry still carries a comment saying its data is
"currently mocked client-side" and that "the pipeline in adapter.py is still a
scaffold". Both are now false: `/query` returns real computed signals with
provenance out of `pipeline/fdr` + `pipeline/fs_db`, and there is no mocking in
`frontend/src/components/financial-diagnostic-report/api.js`.

---

## Notes on the integration

### One mode can no longer take down the gateway

- **Mode routers are imported one at a time, and a failure is isolated.**
  `app/main.py` used to import all five at module scope. A mode router is not
  inert — Trial Balance's verifies its knowledge packs and creates a directory at
  import time, and pulls in `pipeline/tools.py`, which imports `python-docx`. A
  single missing dependency there aborted the import of `app.main`, uvicorn
  refused to start, and four modes that never touch docx went down with it,
  presented as a stack trace that named no mode.
  That contradicted the guarantee this README makes and that the adapters already
  keep. `app/mode_loader.py` now imports each router on its own; a failure is
  recorded against that mode id and the rest are unaffected.
- **A mode that failed to load answers 503, not 404.** A 404 reads as "you asked
  for a route that does not exist" and sends someone hunting for a typo. The stub
  returns the real import error — including which package to `pip install` — and
  sits behind the same auth dependency as a working mode, so an anonymous caller
  gets 401 rather than an exception string naming internal packages.
- **`GET /api/modes` reports it without probing.** `registry.describe` checks the
  load failure first: a probe would otherwise succeed for some modes and report
  one "available" whose every endpoint 503s.

### Authentication, and Financial Statements chat history

- **Sign-in is required for the whole app, and no mode router was edited to get
  it.** `app/main.py` already included every mode router in one loop; that call
  now carries `dependencies=[Depends(require_user)]`, which authenticates all
  five modes at once. SAR, SAR Q&A, Trial Balance and the Diagnostic Report have
  no auth code in them at all.
- **`GET /api/health` is deliberately left open.** `backend/Dockerfile`'s
  HEALTHCHECK polls it every 30s with a plain `urllib` request carrying no
  token; authenticating it would mark the container unhealthy and restart-loop
  it. It touches no database and reports only that the gateway is up.
- **Two new tables, and nothing else changes in the database.** `artha_users` and
  `artha_fs_messages`, created by `app/auth/schema.py` with idempotent
  `CREATE TABLE IF NOT EXISTS` — the same shape `fdr/facts_store.py` already uses,
  since this repo has no migration tooling. There is no `ALTER`, no `DROP` and no
  write of any kind against `documents`, `text_chunks`, `table_chunks` or any
  other pre-existing table. They live in `finance_llm` (`FINANCE_DSN`), which is
  never scanned by `discover_table_config`, so they cannot leak into the FS
  retrieval surface the way a table added to the *rules* database would.
  Create them ahead of time with `python -m app.auth.schema` if the runtime user
  lacks `CREATE`.
- **The schema is created lazily, on first auth request, not at startup.** A
  database blip during boot would otherwise crash the gateway and take the four
  working modes down with it, and flap the healthcheck.
- **Chat history is server-side, keyed by `conversation_id`.** The client sends
  only that id; prior turns are read from the database against the authenticated
  user. A thread therefore survives a refresh and follows the user to another
  machine, and the client cannot rewrite its own history.
- **The follow-up rewriter lives in `adapter.py`, not in `pipeline/`.** It mirrors
  SAR Q&A's proven pattern — fold the last 8 turns plus the new question into one
  standalone question *before* retrieval — while leaving the vendored FS tree
  untouched and `Orchestrator.answer`'s signature (and its `python agent.py`
  self-test) intact. With no history it returns the question byte-for-byte and
  makes no model call, so first questions behave exactly as they did before.

### Financial Statements entity resolution

- **Three of the branch's staticmethods are replaced at runtime, not edited.**
  `DocumentResolver._resolve_document`, `.format_ambiguous` and `.latest_fy_end`
  are rebound from `modes/financial_statement/entity_resolution.py`, installed by
  `adapter._load()`. They are reached as `DocumentResolver.X(...)` from ~17 call
  sites, so rebinding fixes all of them while leaving `pipeline/` re-pullable —
  the same technique `adapter._normalise_embedding_url()` already uses on
  `Config`. `install()` checks the signatures first and raises if a re-pull
  changed that class's shape, so drift fails loudly instead of silently
  restoring the old bug.
  **Why:** the branch matched a company with a one-directional substring — the
  stored `documents.company` had to *contain* the user's string — so `Gujarat_Gas`
  matched "Gujarat Gas" but not "Gujarat Gas Limited". Entities that were sitting
  in the corpus reported themselves absent. `Reranker.rerank_chunks` is wrapped
  the same way, so a reranker outage costs ranking quality instead of the whole
  answer.
- **Four in-place edits inside `pipeline/`, each commented at the site.**
  (1) the 16 `company` tool-parameter descriptions, which said
  `e.g. 'Coal India', 'ONGC', 'BPCL'` and taught the model both that the corpus
  held only those companies and that it should expand short names into the legal
  forms the matcher then rejected; (2) `search_knowledge_base`'s description,
  which now says outright that it holds no company filings, because a company
  question routed there can only come back empty; (3) `prompt.md`'s citation
  example, where the ONGC reference became a `<Company>` placeholder — a format
  example needs no real entity; (4) `prompt.md` rule 16a, on passing the
  company name through as written. This follows the precedent of commit
  `c027bff`, which fixed the same anchoring for SAR chat.
- **A missing financial year no longer reads as a missing company.** The branch
  silently dropped the year filter and returned every year as an ambiguity list,
  which the model paraphrased as "no data for this company". It now says the
  company is present, names the years that are ingested, and is told explicitly
  not to report the entity as having no data.
- **`backend/scripts/audit_fs_entities.py`** lists every entity in the corpus and
  reports which spellings resolve. Strictly `SELECT`-only, on a session opened
  `default_transaction_read_only`. Run it before and after any change to this
  area.
- **An unreachable reports database now shows the mode as down.** It used to set
  `conn_reports = None`, which unregisters all 18 company tools while the sidebar
  stayed green — so the mode answered every company question by saying the data
  was unavailable. Set `ARTHA_FS_ALLOW_NO_REPORTS_DB=1` for the old
  standards-only behaviour.

### Vendoring the branch pipelines

- **`sar_prod_v3/` is a required name, not a preference.** The branch's `Prod/`
  package imports itself absolutely (`from sar_prod_v3.agent import ...`), so
  the folder must carry that name for those imports to resolve unedited.
- **The Financial Statement mode reuses the branch's own `api_server.py`** for
  its orchestrator and answer-parsing helpers, so its output matches running
  that branch standalone — there is no second copy of the parsing logic to
  drift.
- **Its table auto-discovery is replicated in the adapter.** That step lives in
  the branch's FastAPI lifespan handler, which importing the module does not
  run; skipping it would leave `TABLE_CONFIG` empty and make every search
  return no evidence while still looking healthy.

### Trial Balance is now TB-v2

The mode was replaced wholesale (commit `38768bd`, reverted in `5d77cad`,
reapplied in `8249881`). Everything below reflects TB-v2 as it stands; the
previous vendored `pipeline/yukta_rag/` tree, its `token_store.py`, its PDF
evidence feature and the `/pdfs*` and `/validate/upload` routes are **all gone**.

- **The vendored tree is `pipeline/`, not `pipeline/yukta_rag/`** — `agent.py`,
  `tools.py`, `db.py`, `config.py`, `Prompt.md` and a `knowledge/` directory of
  audit packs (anchors, assertions, compliance, estimation, language,
  materiality, relationships, risk, sensitive). `router.py` verifies every pack
  at import time and fails fast if one is missing or malformed, which is why
  this router is not inert and why `app/mode_loader.py` exists.
- **It reads discrete `TB_DB_*` settings, not a DSN.** `TB_DB_HOST`, `_PORT`,
  `_NAME`, `_USER`, `_PASSWORD`, `_POOL_MIN`, `_POOL_MAX`. The older
  `FINANCE_LLM_DSN` / `REFERENCE_DSN` notes no longer apply to this mode.
- **It still owns `TB_GENERATION_BASE_URL` / `TB_GENERATION_MODEL`,** now
  natively in `pipeline/config.py` rather than as a patch to a vendored file,
  and for the same reason as before: SAR already owns the generic
  `GENERATION_BASE_URL` / `GENERATION_MODEL` names for a different endpoint.
- **Three sub-features share one stored-TB layer** — upload once, get a
  `doc_id`, then `ask` / `audit` / `validate` against it. `ask` and `audit` need
  the LLM stack; `validate` is pure Python with zero LLM calls. Verified: the
  Layer 1 gate answered `5 passed, 0 halted, 8 warnings` on a stored TB with the
  generation endpoint untouched.
- **`ask` accepts a `doc_id` from `GET /documents`, and that is the `tb_doc_id`
  field, not `id`.** The listing returns both; `id` is the row's integer primary
  key and passing it gets a 422.
- **An uploaded FSLI grouping changes the audit materially, so the UI says which
  source a run used.** `audit/upload-grouping` takes the client's own
  chart-of-accounts / management FSLI mapping; every account it names is then
  classified by the client's label instead of the keyword engine, which changes
  the FSLI Summary, the Financial Snapshot rows and how abnormal-sign findings
  are grouped. A grouping file can also parse cleanly and match *nothing* — the
  audit then silently falls back to keyword inference — so the upload reports
  `n_matched` coverage up front and the report's Run log records the source.
- **The audit narrative falls back to the deterministic report on a large TB.**
  A 2000+ account trial balance overruns the generation context window and the
  pipeline uses its deterministic, safe-by-construction report instead. Every
  figure is computed in Python either way — the model only ever narrates — so
  this costs prose, not numbers.
- **`ask-general` needs no trial balance.** It answers from the reference
  corpora, and is what the UI uses whenever no file is selected. Requiring an
  upload before any question could be asked made a research tool behave like a
  spreadsheet importer.
- **Runtime artefacts land in `backend/modes/`.** A run writes `modes/output/`
  (`canonical_tb.parquet`, `layer1_*.json|parquet`, `tb_metadata.json`) and
  `modes/sessions/<session-id>/`. Both are untracked and neither is in
  `.gitignore` — worth adding.

- **A question needs no trial balance.** `ask-general` answers from the Ind AS /
  annual-report / reference corpora via the source repo's `FinanceRAG`, which is
  what the UI uses whenever no file is selected. Requiring an upload before any
  question could be asked made a research tool behave like a spreadsheet
  importer.
- **`FINANCE_LLM_DSN` and `REFERENCE_DSN` point at the same databases SAR
  already uses** — confirmed by inspecting the vendored code's own default
  connection strings, which matched `FINANCE_DSN`/`REFERENCE_DSN` in this
  project's `.env` exactly. The `tb_input_data` table this mode reads/writes
  already existed on that database with real uploaded trial balances in it.

---

## Financial Statements — live document upload

Financial Statements answers from a fixed pre-loaded corpus **and** from a
financial statement a user uploads on the spot — a scan of a filing that has
never been through the corpus's own ingestion. The two paths converge on the
same tool library and the same chat surface; the model does not need a
different vocabulary to answer about an uploaded document than about the
corpus.

```
PDF upload → ingestion service (docling + OCR + VLM second read) → Postgres
           (system of record) + Redis (2h cache) → bridge.py → the SAME 8,378-
           line FS tool library the corpus uses, unmodified → chat answer
```

### The ingestion service

A separate container (`ingestion/`, port 12102, `ARTHA_INGEST_URL`) because it
carries docling, torch and an OCR engine, and a conversion saturates a CPU for
minutes — neither belongs in the read-only gateway that also serves chat. See
**[ingestion/README.md](ingestion/README.md)** for the pipeline itself
(`render → precheck → preprocess → convert → vlm second read → verify →
identify → emit`), install gotchas, and the accuracy harness (68
human-verified figures across 3 real filings — `python -m tests.accuracy.runner
--mode inproc score-all`, in `ingestion/tests/accuracy/`, reading PDFs out of
the gitignored `data/` corpus described below).

**The anti-hallucination discipline that makes an upload trustworthy:**
`verify.py` withholds any cell it cannot establish beyond doubt — replaced in
the table by an `[unreadable: page N, table T, row "...", col "..."]` marker —
rather than ever guessing a figure. A second, independent vision-model read of
the same image may **recover** a value for a human to see
(`[recovered N; second read, confidence <band>, ...]`), but it is never treated
as confirmed unless the column's own arithmetic foots it; only then is it
promoted to a plain, quotable number. Every printed subtotal is independently
re-derived and checked against its own components. Prompt rule 21
(`backend/modes/financial_statement/pipeline/prompt.md`) is what stops the
model quoting, estimating or back-solving a withheld figure.

### Storage: Postgres is the system of record, Redis is the cache

`backend/modes/financial_statement/upload/`:

| File | Role |
|---|---|
| `client.py` | HTTP client to the ingestion service; relays its SSE progress |
| `store.py` | `DocumentStore` — Redis-backed hot path, `ARTHA_FS_UPLOAD_TTL_SECONDS` (2h default) |
| `pgstore.py` | Postgres mapping — one row per document, one per table/text-chunk/page, `ARTHA_FS_UPLOAD_RETENTION_DAYS` (30 default) |
| `schema.py` | The four Postgres tables (`python -m modes.financial_statement.upload.schema` to create/inspect) |
| `bridge.py` | Rebinds ~18 of the FS tool library's own lookup functions so they read an uploaded document instead of issuing corpus SQL — the load-bearing file; the 8,378-line tool library itself is untouched |
| `quality.py` | Renders the extraction-quality report both the model and the UI read |
| `coverage.py` | Which questions an uploaded document can actually answer, given what was extracted |
| `narrative.py`, `embeddings.py` | In-memory equivalents of the corpus's narrative SQL and vector search |
| `periods.py`, `materiality.py`, `sieve.py`, `diagnostics.py` | Period coverage, materiality legend, empty-result-must-say-why filters |
| `edits.py` | User-editable cells (below) |
| `tools.py` | The four tools that exist only for an uploaded document (list/quality/etc.) |

An upload survives a Redis restart or TTL expiry: `pgstore.load_one` reloads
it and re-warms the cache. Re-uploading the same file (`doc_id` is
content-addressed, `up_<sha256[:16]>`) replaces the previous extraction rather
than accumulating a duplicate.

### Editable cells — a human can supply a figure the extraction could not read

Gated behind `ARTHA_FS_UPLOAD_USER_EDITS` (**default off** — it changes the
trust model for every downstream answer, so an operator opts in
deliberately). When on, the document pane's Text tab renders a `[unreadable
...]` or `[recovered ...]` cell as a button; a reader who can see the scan can
type in (or confirm) the figure the extraction could not establish, through
`PATCH /documents/{doc_id}/tables/{table_id}/cells`.

- **Server-enforced scope**, not just a UI affordance: only a cell whose
  *stored* state is `unreadable`, `recovered`, or already `user_entered` (for
  re-edit or revert) can be changed — a clean or arithmetic-confirmed figure
  is refused with 409, regardless of what the client claims.
- **Usable but permanently tagged.** The cell becomes `1,234 [user-entered]`
  in the table text itself — never a bare number — so any reader (a tool, a
  citation snippet, the model reading raw table text) sees it was typed by a
  person, not read from the scan. A sidecar record (`quality["user_edits"]`)
  carries who, when, the original marker (for an exact revert), and an
  advisory, never-blocking footing re-check on the edited column.
- **Disclosed at every layer**: the inline tag, a `DATA QUALITY NOTE` line
  appended wherever a tool hands the table to the model, the quality report's
  own "entered by the user" section, and prompt rule 21's additive clause.
- **Known gap:** a *derived* result (a ratio, a tie-out) computed from a
  user-entered figure does not automatically disclose that provenance — only
  the figure itself does, at the point it is read. Full propagation through
  the tool library is future work, tracked as a deliberate phase 2.
- A cell edit is a narrow, transactional single-table Postgres update
  (`pgstore.update_table_cell`, `SELECT ... FOR UPDATE`), not the whole-document
  `save()` — and never touches the document's retention clock.

### The local sample corpus (`data/`) is not pushed

`data/` holds real scanned financial statements for three named entities,
used by the accuracy harness and for manual upload testing. It is gitignored
(along with the ad hoc `data.zip`) — client financial data does not belong in
a shared or public remote, the same reasoning `backend/modes/sessions/`
already documents for Trial Balance. A contributor who needs it fetches it out
of band and places it at the repo root; without it the accuracy harness fails
open with `FileNotFoundError` rather than silently reporting a false pass.

### Tables docling's layout model never finds

Table *location* is delegated to docling's own layout model
(`ingestion/app/convert.py`'s `_extract_tables` reads `document.tables`). A
statement with almost no ruling lines can come back with no table cluster at
all -- measured on a real filing (Startup Odisha's Statement of Income &
Expenditure): 55 layout clusters, none a table, every cell its own tiny
low-confidence text item. OCR read every figure correctly, but each was then
dropped as a short text block (`emit.py` skips blocks under 25 characters), with
no marker and no note.

Handled in `ingestion/app/structure_repair.py`, from docling's own text items and
their positions, and switchable with `INGEST_SYNTHESIZE_MISSED_TABLES`:

- **Rebuild.** Standalone figures outside any table that share a right-aligned
  column, with a label on most rows, are rebuilt as a table. It goes through the
  same footing, withholding and vision second read as any detected table, and a
  note in `quality["notes"]` says it was rebuilt.
- **Continuation.** Figures printed just below a detected table, in its columns,
  where its box stopped short (a balance sheet's whole assets side, in the same
  filing) are rebuilt as more rows under that table's headings.
- **Refuse, don't guess.** Ambiguous columns, a gap of several lines, too few
  labelled rows, or an overlap with a real table all fall back to a note naming
  the page and the number of figures that were read but are in no table.

Separately, `tables.py` now chooses the label column by how many text cells a
column holds (a spanned first row used to make it pick a blank column) and splits
a merged "Notes As at ..." header, so lookups by row label and period work.

Regression case: `ingestion/tests/accuracy/cases/od_2021_22_sfs.yaml`. Still
open: a rebuilt table depends on OCR reading its figures, so a page OCR cannot
read stays a withheld or missing figure; and the 2023-24 balance sheet's
"Property, Plant and Equipment" row is found but its cells are empty -- a
different defect, not covered here.

### Financial year detection and long documents

A 120-page filing for FY 2024-25 was being labelled FY 2017-18, and 43 pages
failed with "document timeout exceeded". Two separate causes, both in `ingestion/`:

- **Year detection** (`identify.py`) now weights only years that appear in
  statement or account headings ("for the year ended 31 March 2025", "as at ...")
  and no longer counts years in notes, dates of events or comparative tables.
  The result is an ordinary detection with a confidence, not a guess: an unclear
  document is reported as such.
- **Timeout** (`convert.py`, `config.py`): `INGEST_DOCUMENT_TIMEOUT` defaults to
  1800 s. Pages that still do not convert are counted, and the quality report
  says "N pages were not converted" once, instead of one error per page.

Tests: `ingestion/tests/test_identify.py`, `test_convert.py`.

---

## Frontend redesign

The UI follows the "Artha.AI Frontend Design" boards: a paper-toned light theme
(navy and muted gold), Source Serif 4 headings, Noto Sans body and IBM Plex Mono
figures. Fonts are self-hosted through `@fontsource` (run `npm install`), so
nothing is fetched from a CDN on an air-gapped host. Tokens live in
`frontend/src/styles/index.css`; old token names remain as aliases.

**Figures are never reformatted.** An extracted cell is shown exactly as the
filing printed it. Four states, each with a text label and not colour alone:
plain, *unreadable* (red), *recovered* (amber, `[recovered N; ...]`) and
*user-entered* (gold). Indian digit grouping is applied only to values a user
types or that are computed. `lib/figures.js` parses the markers, and
`components/common/FigureCell.jsx` renders them.

Financial Statements:

- **Sidebar and shell:** collapses to a 64 px rail while the document pane is
  open; the pane docks at 42 % and becomes an overlay below 900 px. An offline
  banner appears when the browser loses its connection, and a failed question
  keeps the draft with a "Try again" button.
- **Answers:** tagged blocks (FINDING, RISK FLAG, AUDIT POINTER, COVERAGE NOTE),
  a "read as" line, an "answered against N documents" strip, and a collapsed
  **How this was checked** section holding confidence (and why it was lowered),
  tools used and the unsourced warning. The backend adds a `checks` field to the
  answer (`modes/financial_statement/adapter.py::_extract_checks`, read from the
  rendered answer; the vendored pipeline is unchanged).
- **Uploads:** a compact wait bar with an expandable panel (`LedgerLoop`, eight
  stages driven by real progress events, live timer, reduced-motion safe); document
  chips with counters; a quality drawer whose **Enter** and **Review** buttons open
  the exact cell in the pane; retention shown as "kept until <date>", computed from
  `retention_days` returned by `/upload/health`.
- **Sign-in:** two-pane layout. There is no "Forgot password" link because the
  backend has no reset flow.
- **Brand marks:** the State Emblem and CAG logo are legally restricted, so
  `frontend/src/config/brand.js` holds two empty, default-off slots. The footer
  states "Independent audit-assistance tool. Not an official government service."

Other modes are re-themed through the shared tokens. Trial Balance results lead
with severity tiles and an "Arithmetic checks passed / halted" badge (shown only
when the validation engine reported it); the Statutory Auditor's Report and
Financial Diagnostic Report take the serif headings and navy top edges.
The Diagnostic Report's Report tab is still a stub.

**Not done / not verified:** the unit line above tables ("Amount in ₹ lakh") and a
corpus filing count have no reliable backend source and are omitted. The redesign
was verified with the render tests and a browser walk-through against mocked API
responses; a run against the real backend and ingestion stack is still to do.
