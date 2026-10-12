# live_trace: what the Trial Balance backend is doing, right now

A self-contained add-on. **No existing file is edited.** It attaches to the gateway at startup and
records, per run, the steps taken, the pipeline tools called and every LLM call.

## Run it

```
cd backend
python -m uvicorn live_trace.asgi:app --reload --port 12101
```

Then open `http://localhost:12104/trace` (the Vite dev port) after signing in to the app.

Start with `app.main:app` instead and the gateway runs exactly as before, with no trace.

## What is in here

| File | Role |
|---|---|
| `asgi.py` | Entry point: imports the gateway, then calls `install(app)`. |
| `instrument.py` | Wraps existing behaviour in memory: tools, LLM calls, ingest stages, route handlers. |
| `recorder.py` | Event store (Valkey) and the step / LLM-call recording API. |
| `api.py` | `GET /api/trial-balance/trace/{status,runs,runs/{id}}`, behind the normal sign-in. |

The page itself is `frontend/trace.html` and `frontend/src/live-trace/`.

## What is recorded

Step names, counters, durations, which tool ran, and for each LLM call its purpose, model, how many
characters went out and came back, and any error. **Prompts and responses are not stored** unless
`TB_TRACE_CAPTURE_PAYLOADS=true` (short excerpts, for debugging only). `TB_TRACE_ENABLED=false` turns
recording off. Runs are kept in Valkey for 24 hours.

A person sees their own runs; a super admin sees everyone's. Someone else's run id answers 404.

## How it attaches

- Pipeline tools: each entry in `TOOL_REGISTRY` is replaced by a traced wrapper.
- LLM calls: `generate` on the yukta client classes, recorded only while a run is bound.
- Ingest stages: module-level functions in `db_bridge`, `classify_document` and `normalize`.
- Routes: `upload`, `upload-mapped`, `audit`, `ask`, `ask-general`, `validate` open and close a run. An
  upload token is aliased to its run so `/upload-mapped` continues the run that `/upload` started.

Every patch is independent. If a target is renamed in a future change, that one is skipped with a log
line starting `live_trace: skipped` and the rest still work.

## Production build

`/trace` is served by the Vite dev server with no configuration. For a production bundle, add
`trace: 'trace.html'` to `build.rollupOptions.input` in `frontend/vite.config.js` and an nginx
location for `/trace`; this folder deliberately does not edit either.

## Removing it

Delete `backend/live_trace/`, `frontend/trace.html` and `frontend/src/live-trace/`, and start the gateway
with `app.main:app`.
