# Artha.AI — Integrated Audit Platform

A single frontend and a single backend gateway over several independent
pipelines: three modes come from branches of
[`Fin_Audit_QA`](https://github.com/HARISH-027/Fin_Audit_QA); Trial Balance
comes from a separate repo,
[`Finance_llm_v2_naveen`](https://github.com/HARISH-027/Finance_llm_v2_naveen)
(`main` branch — only its trial-balance-related code is integrated; that repo
also ships an unrelated chat/RAG feature and a Financial Diagnostic Report
feature, neither of which is used here).

Four modes are exposed. Three are integrated today:

| Mode | Source | UI | Status |
|---|---|---|---|
| Statutory Auditor's Report | `Fin_Audit_QA` / `Statutory_Auditor_Report` | two dropdowns → report | **Integrated** |
| Financial Statements | `Fin_Audit_QA` / `Financial_Statement` | chat | **Integrated** |
| Trial Balance | `Finance_llm_v2_naveen` (`main`) | upload → ask / audit / validate | **Integrated** |
| Financial Diagnostic Report | `Fin_Audit_QA` / `Financial_Diagnostic_Report` | chat | Scaffolded |

---

## Layout

```
Integrated/
├── backend/
│   ├── app/                     gateway: registry, schemas, errors, main
│   ├── modes/
│   │   ├── financial_statement/
│   │   │   ├── adapter.py       thin wrapper over the branch pipeline
│   │   │   ├── router.py        /api/financial-statement/*
│   │   │   └── pipeline/        ← Financial_Statement branch, VERBATIM
│   │   ├── statutory_auditor_report/
│   │   │   ├── adapter.py
│   │   │   ├── router.py        /api/statutory-auditor-report/*
│   │   │   └── sar_prod_v3/     ← Statutory_Auditor_Report branch Prod/, VERBATIM
│   │   ├── trial_balance/
│   │   │   ├── adapter.py       upload/list/delete + ask/audit/validate
│   │   │   ├── router.py        /api/trial-balance/*
│   │   │   └── pipeline/yukta_rag/  ← Finance_llm_v2_naveen's TB code, VERBATIM
│   │   └── financial_diagnostic_report/  scaffold — drop the branch in here
│   ├── requirements.txt
│   └── .env.example
└── frontend/                    React 18 + Vite
    └── src/
        ├── api/client.js        every call keyed off the mode's base_path
        └── components/
            ├── chat/            query-driven modes
            ├── report/          the auditor's-report form + document
            └── trial-balance/   upload + doc picker + Ask/Audit/Validate tabs
```

**One folder per branch, and the branch code inside it is untouched.** Branch
files are vendored exactly as they appear on origin, so you can re-pull a
branch over its folder without resolving a merge conflict. Each mode's
`adapter.py` is the only file that knows how to call into that pipeline.

---

## Mode isolation

Each mode owns a distinct URL namespace:

```
/api/statutory-auditor-report/{catalog,generate,health}
/api/financial-statement/{query,health}
/api/trial-balance/{upload,preview,upload-mapped,documents,ask,audit,audit/workbook,validate,health}
/api/financial-diagnostic-report/{query,health}
```

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

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
cp .env.example .env            # then fill it in — see below
uvicorn app.main:app --reload --port 8090
```

`yukta` is **not on PyPI**; install it from your internal index or a source
checkout. Without it, the LLM-backed paths report themselves unavailable but
the gateway still runs.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Vite proxies `/api` to `http://127.0.0.1:8090`. If the gateway runs elsewhere,
set `VITE_API_TARGET=http://host:port`.

---

## Configuration

Everything lives in `backend/.env` (see `.env.example` for the annotated
version). The required settings:

| Variable | Mode | Effect if missing |
|---|---|---|
| `DB_PASSWORD` | Financial Statements | mode reports unavailable |
| `FINANCE_DSN` | Statutory Auditor's Report | dropdowns and generation unavailable |
| `FINANCE_LLM_DSN` | Trial Balance | falls back to the vendored pipeline's own default (same DB as `FINANCE_DSN`) |

A mode that cannot load degrades **on its own**: the gateway and the other
modes keep serving, and the UI shows that mode's exact reason string (e.g.
*"DB_PASSWORD is not set"*) rather than a generic failure. Trial Balance
degrades further, per sub-feature: `ask`/`audit` need the LLM stack;
upload/list/delete/`validate` need only the database.

Note that Financial Statements and SAR read the same `EMBEDDING_BASE_URL` /
`EMBEDDING_MODEL` names — if those two need different embedding endpoints,
that has to be reconciled before running them together. Trial Balance avoids
that collision by reading its own `EMBEDDING_URL`/`TB_GENERATION_BASE_URL`/
`TB_GENERATION_MODEL` names — see the comments in `.env.example`.

---

## Adding the remaining mode (Financial Diagnostic Report)

The scaffold is already wired end to end — registry entry, router, frontend
card. Only the adapter is missing:

1. Vendor the source verbatim, as the integrated modes do:
   ```bash
   git show origin/<Branch>:<file> > backend/modes/<mode>/pipeline/<file>
   ```
2. Implement this mode's real surface in `adapter.py` and call it from
   `router.py` — see the worked examples in the integrated modes.
3. Flip `integrated=True` for that mode in `app/registry.py`.

The registry's `ui` field is what the frontend switches its view on:
- `"chat"` — pure text query (Financial Statements): mirror
  `modes/financial_statement/adapter.py` + `router.py`.
- `"report"` — form inputs → one generated document (SAR): mirror
  `modes/statutory_auditor_report/`.
- `"trial-balance"` — file upload(s) → a doc picker, then several independent
  analyses against the resulting `doc_id`(s): mirror `modes/trial_balance/`
  and `frontend/src/components/trial-balance/`. Use this shape (rather than
  inventing a new one) if a future source is also upload-and-analyze rather
  than answering from a live database.

---

## Notes on the integration

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
- **Trial Balance integrates only the trial-balance-related code** from
  `Finance_llm_v2_naveen` — its chat/RAG feature and Financial Diagnostic
  Report feature are out of scope and were not vendored. Three independent
  sub-features share one stored-TB layer (upload once, get a `doc_id`, then
  `ask`/`audit`/`validate` against it): `ask` and `audit` need the LLM stack,
  `validate` is pure Python with zero LLM calls.
- **Two small patches inside the vendored `pipeline/yukta_rag/` tree**, both
  commented in place: `core/config.py` reads `TB_GENERATION_BASE_URL`/
  `TB_GENERATION_MODEL` instead of the generic `GENERATION_BASE_URL`/
  `GENERATION_MODEL` names, because SAR already owns those names for a
  different endpoint; `trial_balance/_sources.py` is a copy of three small
  helper functions out of the source repo's (unvendored) `chat/pipeline.py`,
  so Trial Balance doesn't need to pull in the whole chat/RAG feature for a
  14-line dedup function.
- **`FINANCE_LLM_DSN` and `REFERENCE_DSN` point at the same databases SAR
  already uses** — confirmed by inspecting the vendored code's own default
  connection strings, which matched `FINANCE_DSN`/`REFERENCE_DSN` in this
  project's `.env` exactly. The `tb_input_data` table this mode reads/writes
  already existed on that database with real uploaded trial balances in it.
