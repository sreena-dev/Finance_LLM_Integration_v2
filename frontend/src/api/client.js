/**
 * Gateway client.
 *
 * Every call is keyed off the `base_path` the gateway reports for a mode, so
 * the mode the user selected is structurally the mode whose pipeline runs.
 * No call site hard-codes a URL — that is what stops Trial Balance from ever
 * hitting the Financial Statement endpoint.
 */

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    // No port quoted here on purpose: the gateway's port comes from .env
    // (ARTHA_BACKEND_PORT), so a hard-coded number in this message would go
    // stale and send people looking at the wrong service.
    throw new Error(
      'Could not reach the backend. Start the stack with `docker compose up -d`, ' +
        'or run the gateway directly: uvicorn app.main:app --port $ARTHA_BACKEND_PORT'
    );
  }

  if (!res.ok) {
    const isJson = (res.headers.get('content-type') || '').includes('application/json');

    // A non-JSON 404 means something answered that isn't our gateway — almost
    // always another service (Apache, Tomcat) already holding the proxy target
    // port. Saying "HTTP 404" there sends people hunting for a missing route
    // that exists perfectly well.
    if (!isJson && (res.status === 404 || res.status === 403)) {
      throw new Error(
        `Port conflict: something other than the Artha.AI gateway answered on the ` +
          `API port (HTTP ${res.status}). Another service is likely holding it. ` +
          `Start the backend on a free port and point the dev server at it with ` +
          `VITE_API_TARGET=http://127.0.0.1:<port>.`
      );
    }

    // Vite's proxy reports an unreachable target as a 500.
    if (!isJson && res.status >= 500) {
      throw new Error(
        `The backend gateway is not responding (HTTP ${res.status}). Check it with ` +
          `\`docker compose ps\`, or start it from the backend/ directory with: ` +
          `uvicorn app.main:app --reload --port $ARTHA_BACKEND_PORT`
      );
    }

    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function post(path, payload) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// ── Gateway ───────────────────────────────────────────────────────────────

export async function fetchModes() {
  const { modes } = await request('/api/modes');
  return modes;
}

/** Probe one mode's readiness. Never throws — a probe failure is a result. */
export async function probeMode(mode) {
  try {
    return await request(`${mode.base_path}/health`);
  } catch (err) {
    return { mode: mode.id, available: false, reason: err.message };
  }
}

// ── Per-mode calls ────────────────────────────────────────────────────────

/** Chat-style modes: financial statement, trial balance, diagnostic report. */
export function runQuery(mode, query) {
  return post(`${mode.base_path}/query`, { query });
}

/** Statutory auditor's report: entities + the financial years available for each. */
export async function fetchCatalog(mode) {
  const { entities } = await request(`${mode.base_path}/catalog`);
  return entities;
}

export function generateReport(mode, { entity, fyStart, fyEnd, scope }) {
  return post(`${mode.base_path}/generate`, {
    entity,
    fy_start: fyStart,
    fy_end: fyEnd,
    scope,
  });
}

/**
 * SAR Q&A: one conversational turn about an already-selected entity and FY.
 *
 * `history` carries the prior turns so the pipeline's rewriter can resolve
 * back-references ("that clause", "why is it qualified?") into a standalone
 * question. Like every other call here it is built from `mode.base_path`, so
 * this cannot reach the report mode's pipeline.
 */
export function runSARChat(mode, { query, company, fyStart, history }) {
  return post(`${mode.base_path}/ask`, {
    query,
    company,
    fy_start: fyStart,
    history: history || [],
  });
}

/** Trial Balance: upload once, then ask / audit / validate against a doc_id. */

async function uploadFile(path, file, fields = {}) {
  const form = new FormData();
  form.append('file', file);
  // Extra multipart fields (the grouping upload sends the doc_id(s) the file is
  // matched against). Skipped when null/undefined so the backend sees an absent
  // optional Form field rather than the string "null".
  for (const [k, v] of Object.entries(fields)) {
    if (v !== null && v !== undefined) form.append(k, v);
  }
  let res;
  try {
    res = await fetch(path, { method: 'POST', body: form });
  } catch {
    throw new Error('Could not reach the backend to upload the file.');
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // A 422 needs_mapping response is not an error the caller should throw
    // away — the column-mapper needs its preview_token — so it's returned
    // as a structured object rather than raising.
    if (res.status === 422 && body?.detail?.needs_mapping) {
      return { needsMapping: true, ...body.detail };
    }
    const err = new Error(body.detail?.message || body.detail || `Upload failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

export function tbUpload(mode, file) {
  return uploadFile(`${mode.base_path}/upload`, file);
}

export async function tbPreview(mode, token) {
  return request(`${mode.base_path}/preview?token=${encodeURIComponent(token)}`);
}

export function tbUploadMapped(mode, mapping) {
  return post(`${mode.base_path}/upload-mapped`, mapping);
}

/**
 * Every trial balance stored on the server, newest first.
 *
 * Intentionally NOT called by the UI: the database is shared, so this returns
 * other engagements' and other people's files, and listing it turned the run
 * picker into a directory of unrelated data. The picker shows what the session
 * uploaded instead. Kept because the endpoint is part of the API surface and is
 * the right call for an operator or a future "browse stored files" screen.
 */
export async function tbListDocuments(mode) {
  return request(`${mode.base_path}/documents`);
}

export async function tbDeleteDocument(mode, docId) {
  return request(`${mode.base_path}/documents/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
  });
}

export function tbAsk(mode, { docId, question, sessionId }) {
  return post(`${mode.base_path}/ask`, { doc_id: docId, question, session_id: sessionId });
}

/**
 * A question with no trial balance attached, answered from the Ind AS /
 * annual-report / reference corpora. Separate memory from `tbAsk`, and a
 * different response shape: it can carry `computed` (a deterministic arithmetic
 * result) and `guardrail` (the pipeline declined to source the answer).
 */
export function tbAskGeneral(mode, { question, sessionId, uploadDocIds }) {
  return post(`${mode.base_path}/ask-general`, {
    question,
    session_id: sessionId,
    upload_doc_ids: uploadDocIds?.length ? uploadDocIds : null,
  });
}

export function tbAudit(
  mode,
  { docId, docIdPrior, entity, engagementContext, framework, groupingToken, uploadDocIds }
) {
  return post(`${mode.base_path}/audit`, {
    doc_id: docId,
    doc_id_prior: docIdPrior || null,
    entity: entity || null,
    engagement_context: engagementContext || null,
    framework: framework || null,
    grouping_token: groupingToken || null,
    upload_doc_ids: uploadDocIds?.length ? uploadDocIds : null,
  });
}

/**
 * Upload an optional client chart-of-accounts / FSLI grouping file, whose labels
 * then override keyword-based classification for the whole audit.
 *
 * `docId`/`docId2` are the trial balance(s) it applies to — sent so the backend
 * can detect columns by matching real account codes/names instead of guessing
 * from header text, and so it can report how many accounts the file covers.
 *
 * Resolves to `{ needsMapping: true, preview_token }` when the layout can't be
 * auto-detected — same convention as `tbUpload`, so the caller opens a mapper.
 */
export function tbUploadGrouping(mode, { file, docId, docId2 }) {
  return uploadFile(`${mode.base_path}/audit/upload-grouping`, file, {
    doc_id: docId,
    doc_id_2: docId2,
  });
}

export function tbUploadGroupingMapped(mode, mapping) {
  return post(`${mode.base_path}/audit/upload-grouping-mapped`, mapping);
}

/**
 * PDF evidence for `audit` — annual reports / auditor comments, page-chunked and
 * embedded so the audit can quantify and cite document-sourced risk items.
 *
 * This sub-feature has its own database and embedding endpoint, so it can be
 * unavailable while the rest of the mode works. `tbPdfHealth` reports that
 * separately, which is what the UI uses to explain itself rather than surfacing a
 * connection error on first upload.
 */
export async function tbPdfHealth(mode) {
  return request(`${mode.base_path}/pdfs/health`);
}

export function tbUploadPdf(mode, file) {
  return uploadFile(`${mode.base_path}/pdfs`, file);
}

export async function tbListPdfs(mode) {
  return request(`${mode.base_path}/pdfs`);
}

export async function tbDeletePdf(mode, docId) {
  return request(`${mode.base_path}/pdfs/${encodeURIComponent(docId)}`, { method: 'DELETE' });
}

export async function tbAuditWorkbook(mode, { docId, docIdPrior, uploadDocIds }) {
  let res;
  try {
    res = await fetch(`${mode.base_path}/audit/workbook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doc_id: docId,
        doc_id_prior: docIdPrior || null,
        upload_doc_ids: uploadDocIds?.length ? uploadDocIds : null,
      }),
    });
  } catch {
    throw new Error('Could not reach the backend to build the workbook.');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Workbook generation failed (HTTP ${res.status})`);
  }
  return res.blob();
}

export function tbValidate(mode, { docId, docIdPrior, ...params }) {
  return post(`${mode.base_path}/validate`, {
    doc_id: docId,
    doc_id_prior: docIdPrior || null,
    ...params,
  });
}
