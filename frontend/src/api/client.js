/**
 * Gateway client.
 *
 * Every call is keyed off the `base_path` the gateway reports for a mode, so
 * the mode the user selected is structurally the mode whose pipeline runs.
 * No call site hard-codes a URL — that is what stops Trial Balance from ever
 * hitting the Financial Statement endpoint.
 *
 * Every request carries the session token. It is read from `auth/token.js` — a
 * leaf module with no React in it — rather than from context, because these are
 * plain functions where no hook can be called.
 */

import { authHeaders, notifyUnauthorized } from '../auth/token';

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), ...authHeaders() },
    });
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
    // An expired or missing token is a session event, not a mode failure.
    // Without this it would surface as every mode being "unavailable", which
    // tells the user nothing about what actually happened or what to do.
    if (res.status === 401) {
      const body = await res.json().catch(() => ({}));
      notifyUnauthorized(body.detail || 'Your session ended. Please sign in again.');
      const err = new Error(body.detail || 'Your session ended. Please sign in again.');
      err.status = 401;
      throw err;
    }

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

/**
 * Chat-style modes: currently only Financial Statements.
 *
 * `conversationId` is optional and is the ONLY thing sent about the past — the
 * prior turns live server-side against the signed-in user, so a thread resumes
 * after a refresh or on another machine, and the client cannot rewrite its own
 * history. Omit it and the server starts a new conversation.
 */
export function runQuery(mode, query, conversationId = null) {
  return post(`${mode.base_path}/query`, {
    query,
    conversation_id: conversationId || null,
  });
}

/** This user's saved conversations for a mode, most recently active first. */
export async function listConversations(mode) {
  const { conversations } = await request(`${mode.base_path}/conversations`);
  return conversations || [];
}

/**
 * Every turn of one conversation.
 *
 * Assistant turns carry the stored `payload` — the full original response — so a
 * reopened thread renders with its evidence and citations intact rather than as
 * plain text.
 */
export function fetchConversation(mode, conversationId) {
  return request(`${mode.base_path}/conversations/${encodeURIComponent(conversationId)}`);
}

export async function deleteConversation(mode, conversationId) {
  const res = await fetch(
    `${mode.base_path}/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE', headers: { ...authHeaders() } }
  );
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Could not delete the conversation (HTTP ${res.status}).`);
  }
  return true;
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
    // Headers are set explicitly here and deliberately WITHOUT Content-Type:
    // FormData must set its own multipart boundary, and naming the type would
    // overwrite it with one that has no boundary at all.
    res = await fetch(path, { method: 'POST', body: form, headers: { ...authHeaders() } });
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

export function tbUploadMapped(mode, { token, groupingToken, acceptDataQualityRisk, companyDetails, persistToLive }) {
  const cd = companyDetails || {};
  return post(`${mode.base_path}/upload-mapped`, {
    token,
    grouping_token: groupingToken || null,
    accept_data_quality_risk: acceptDataQualityRisk || false,
    company_name: cd.companyName || null,
    cin: cd.cin || null,
    financial_year: cd.financialYear || null,
    standard: cd.standard || null,
    // Omitted -> backend default True (the ordinary upload-and-audit path).
    // False is the query-analysis staging path (TbRunPicker's 'query' mode):
    // same classify/quality-gate chain, canonical_tb.parquet still written,
    // nothing reaches LIVE.
    ...(persistToLive === false ? { persist_to_live: false } : {}),
  });
}

export async function tbSuggestPriorityCompanies(mode) {
  return request(`${mode.base_path}/companies/priority`);
}

/**
 * Every trial balance already ingested into the database, newest first.
 * Backs TbRunPicker's "Existing (Database)" tab — the database is shared
 * across engagements, so callers should filter with `entityId`/`financialYear`
 * where possible; the picker also applies a client-side text filter on top.
 */
export async function tbListDocuments(mode, { entityId, financialYear } = {}) {
  const params = new URLSearchParams();
  if (entityId) params.set('entity_id', entityId);
  if (financialYear) params.set('financial_year', financialYear);
  const qs = params.toString();
  return request(`${mode.base_path}/documents${qs ? `?${qs}` : ''}`);
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

export async function tbAuditWorkbook(mode, { docId, docIdPrior, uploadDocIds, format }) {
  let res;
  try {
    res = await fetch(`${mode.base_path}/audit/workbook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        doc_id: docId,
        doc_id_prior: docIdPrior || null,
        upload_doc_ids: uploadDocIds?.length ? uploadDocIds : null,
        format: format || 'xlsx',
      }),
    });
  } catch {
    throw new Error('Could not reach the backend to build the workbook.');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Workbook generation failed (HTTP ${res.status})`);
  }
  // The backend names the file after the input TB (see router.py's
  // _tb_filename_suffix) via Content-Disposition — parsed here rather than
  // hardcoded client-side, so the two never drift apart.
  const disposition = res.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match ? match[1] : (format === 'docx' ? 'TB_Audit_Report.docx' : 'TB_Audit.xlsx');
  return { blob: await res.blob(), filename };
}

export function tbValidate(mode, { docId, docIdPrior, ...params }) {
  return post(`${mode.base_path}/validate`, {
    doc_id: docId,
    doc_id_prior: docIdPrior || null,
    ...params,
  });
}
