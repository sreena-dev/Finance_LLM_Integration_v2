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
    throw new Error(
      'Could not reach the backend. Start it with: uvicorn app.main:app --port 8080'
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
        `The backend gateway is not responding (HTTP ${res.status}). Start it from ` +
          `the backend/ directory with: uvicorn app.main:app --reload --port 8090`
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

/** Trial Balance: upload once, then ask / audit / validate against a doc_id. */

async function uploadFile(path, file) {
  const form = new FormData();
  form.append('file', file);
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

export function tbAudit(mode, { docId, docIdPrior, entity, engagementContext, framework }) {
  return post(`${mode.base_path}/audit`, {
    doc_id: docId,
    doc_id_prior: docIdPrior || null,
    entity: entity || null,
    engagement_context: engagementContext || null,
    framework: framework || null,
  });
}

export async function tbAuditWorkbook(mode, { docId, docIdPrior }) {
  let res;
  try {
    res = await fetch(`${mode.base_path}/audit/workbook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: docId, doc_id_prior: docIdPrior || null }),
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
