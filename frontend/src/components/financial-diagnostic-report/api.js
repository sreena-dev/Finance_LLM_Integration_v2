/**
 * Financial Diagnostic Report — API client.
 *
 * Self-contained on purpose. The shared `src/api/client.js` is owned by the rest
 * of the app; this mode adds its calls here instead so wiring the FDR touches no
 * file another mode depends on.
 *
 * ONE DELIBERATE EXCEPTION: `auth/token.js`. Every route below now requires a
 * session token, and this file has independent `fetch` calls that never pass
 * through the shared client. The alternative was to re-read
 * `localStorage.getItem('artha.token')` here, which would mean two files that
 * must agree on a key name forever. Importing a leaf module that holds no state
 * of its own is the smaller coupling.
 *
 * It still follows that client's one binding rule: NO URL IS HARD-CODED. Every
 * call is built from the `base_path` the gateway reports for this mode, so the
 * mode the user selected is structurally the mode whose pipeline runs, and this
 * client cannot reach another mode's endpoints even by mistake.
 */

import { authHeaders, notifyUnauthorized } from '../../auth/token';

const MODE_ID = 'financial-diagnostic-report';

let basePathPromise = null;

/**
 * The gateway's own base path for this mode, fetched once and remembered.
 *
 * Resolved from `/api/modes` rather than written down, because the component is
 * mounted as `<FdrAnalysis embedded />` with no `mode` prop and the app's
 * registry is the single source of truth for where a mode lives.
 */
export function getBasePath() {
  if (!basePathPromise) {
    basePathPromise = (async () => {
      const res = await fetch('/api/modes', { headers: { ...authHeaders() } });
      if (!res.ok) throw new Error(`Could not read the mode registry (HTTP ${res.status}).`);
      const { modes } = await res.json();
      const mode = (modes || []).find((m) => m.id === MODE_ID);
      if (!mode) {
        throw new Error(
          `The gateway does not list a "${MODE_ID}" mode. It is registered in ` +
            `backend/app/registry.py — check the backend is the one you expect.`
        );
      }
      return mode.base_path;
    })().catch((err) => {
      // Do not cache a failure: a backend that was starting up should be
      // retried on the next interaction rather than poisoning the session.
      basePathPromise = null;
      throw err;
    });
  }
  return basePathPromise;
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), ...authHeaders() },
    });
  } catch {
    throw new Error(
      'Could not reach the backend. Start the stack with `docker compose up -d`, ' +
        'or run the gateway directly: uvicorn app.main:app --port $ARTHA_BACKEND_PORT'
    );
  }

  if (!res.ok) {
    if (res.status === 401) {
      const body = await res.json().catch(() => ({}));
      notifyUnauthorized(body.detail || 'Your session ended. Please sign in again.');
      const err = new Error(body.detail || 'Your session ended. Please sign in again.');
      err.status = 401;
      throw err;
    }
    const isJson = (res.headers.get('content-type') || '').includes('application/json');
    if (!isJson) {
      throw new Error(
        `The gateway did not answer with JSON (HTTP ${res.status}). Something other ` +
          `than the Artha.AI backend may be holding the API port.`
      );
    }
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** Is the mode able to answer, and if not, what would fix it. */
export async function fetchHealth() {
  const base = await getBasePath();
  return request(`${base}/health`);
}

/**
 * XBRL direct-fetch path (as_db) — separate from everything below.
 *
 * A different database, keyed by `doc_id` rather than `entity_id`, with no
 * panel and no report run behind it: `fetchXbrlDashboard` is one read, shaped
 * once, returned. See `pipeline/xbrl_fetch/__init__.py` for why this does not
 * share code with the fs_db-backed calls above.
 */
export async function fetchXbrlEntities() {
  const base = await getBasePath();
  const { entities } = await request(`${base}/xbrl/entities`);
  return entities || [];
}

export async function fetchXbrlDashboard(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/dashboard?doc_id=${encodeURIComponent(docId)}`);
}

export async function fetchXbrlCompanyOverview(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/company-overview?doc_id=${encodeURIComponent(docId)}`);
}

export async function fetchXbrlBusinessProfile(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/business-profile?doc_id=${encodeURIComponent(docId)}`);
}

export async function fetchXbrlRiskClusters(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/risk-clusters?doc_id=${encodeURIComponent(docId)}`);
}

export async function fetchXbrlHealthSummary(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/health-summary?doc_id=${encodeURIComponent(docId)}`);
}

export async function fetchXbrlTrends(docId) {
  const base = await getBasePath();
  return request(`${base}/xbrl/trends?doc_id=${encodeURIComponent(docId)}`);
}

/**
 * The XBRL Direct tab's five blocks as one PDF, reordered into the spec's
 * planning-first structure. Mirrors `downloadReport` below — the component
 * owns the anchor click, this module owns the request.
 */
export async function downloadXbrlReport(docId) {
  const base = await getBasePath();
  const res = await fetch(`${base}/xbrl/report/download?doc_id=${encodeURIComponent(docId)}`, {
    headers: { ...authHeaders() },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Could not build the file (HTTP ${res.status}).`);
  }
  const disposition = res.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  return { blob: await res.blob(), filename: match?.[1] || `FDR_XBRL_${docId}.pdf` };
}



/**
 * Every entity in the filings corpus, for the picker.
 *
 * One GROUP BY over `documents` on the server — it extracts nothing, so this is
 * safe to call on mount.
 */
export async function fetchEntities() {
  const base = await getBasePath();
  const { entities } = await request(`${base}/entities`);
  return entities || [];
}

/**
 * Ask one question about one entity, in a single request/response.
 *
 * `entity_id` is always sent and never inferred from the question text: the
 * picker is the entity, which is what removes the possibility of one entity's
 * figures being answered under another's name.
 *
 * Kept alongside the streaming call because it is the right shape for a script
 * or an integration that just wants the answer.
 */
export async function askQuestion({ entityId, query, refresh = false }) {
  const base = await getBasePath();
  return request(`${base}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_id: entityId, query, refresh }),
  });
}

/**
 * What the report will contain, before any of it is built.
 *
 * Fetched first so the blocks can be laid out as pending placeholders while the
 * filings are still being read — a reader watching one section arrive can see
 * how many are still coming instead of guessing whether it has finished.
 */
export async function fetchReportManifest() {
  const base = await getBasePath();
  return request(`${base}/report/manifest`);
}

/**
 * Build the report, watched block by block.
 *
 * Two event kinds arrive and they mean different things: `progress` is the read
 * of the filings (the only part anyone waits on — about a second per filing),
 * and `block` is a finished section, handed over the moment it is built rather
 * than held until the last one is done.
 *
 * Blocks arrive by COMPLETION, not by number. Today they all finish at once, so
 * the order looks sequential; the narrated sections coming next will not, and a
 * caller that already sorts on arrival will not need changing then.
 */
export async function generateReportStream(
  { entityId, refresh = false },
  { onProgress, onBlock, signal } = {}
) {
  const base = await getBasePath();

  let res;
  try {
    res = await fetch(`${base}/report/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ entity_id: entityId, refresh }),
      signal,
    });
  } catch (err) {
    if (err?.name === 'AbortError') throw err;
    throw new Error('Could not reach the backend to build the report.');
  }

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completion = null;
  let failure = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue; // ": keep-alive" comments
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === 'progress') onProgress?.(event.message, event);
        else if (event.type === 'block') onBlock?.(event);
        else if (event.type === 'complete') completion = event;
        else if (event.type === 'error') failure = event;
      }
    }
  }

  if (failure) {
    const err = new Error(failure.detail || 'The report could not be built.');
    err.status = failure.status;
    throw err;
  }
  if (!completion) {
    throw new Error('The connection closed before the report finished. Please retry.');
  }
  return completion;
}

/**
 * The report as a file.
 *
 * Built by the same block builders the screen ran, so the download and the
 * screen cannot disagree. The blob is handed back rather than saved here: the
 * component owns the anchor click, this module owns the request.
 */
export async function downloadReport({ entityId, refresh = false }) {
  const base = await getBasePath();
  const res = await fetch(`${base}/report/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ entity_id: entityId, refresh }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Could not build the file (HTTP ${res.status}).`);
  }
  const disposition = res.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  return { blob: await res.blob(), filename: match?.[1] || `FDR_${entityId}.pdf` };
}

/**
 * The same answer, streamed.
 *
 * WHAT ARRIVES IN PIECES IS THE PROGRESS, NOT THE PROSE. There is no language
 * model behind this mode, so the answer text is not generated token by token —
 * it is formatted from computed values and exists in full the moment the
 * arithmetic completes. What takes real time is reading each filing, and that
 * is what streams: one event per filing as it is parsed and bound.
 *
 * `onProgress(message, event)` fires per stage; the promise resolves with the
 * finished answer, so a caller that ignores progress behaves exactly like
 * `askQuestion`.
 *
 * A POST is used rather than EventSource — which is GET-only — because the
 * question travels in the body, so the response stream is read directly.
 */
export async function askQuestionStream(
  { entityId, query, refresh = false },
  { onProgress, onToken, signal } = {}
) {
  const base = await getBasePath();

  let res;
  try {
    res = await fetch(`${base}/query/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ entity_id: entityId, query, refresh }),
      signal,
    });
  } catch (err) {
    if (err?.name === 'AbortError') throw err;
    throw new Error('Could not reach the backend to ask the question.');
  }

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (HTTP ${res.status})`);
    err.status = res.status;
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer = null;
  let failure = null;

  // SSE frames are separated by a blank line and may be split across chunks,
  // so the buffer is drained frame-by-frame rather than parsed per chunk.
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue; // ": keep-alive" comments
        let event;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === 'progress') onProgress?.(event.message, event);
        // Token deltas arrive only on the retrieval path, where the answer is
        // genuinely generated a piece at a time. The deterministic path sends
        // none, so a caller that handles both simply never sees these.
        else if (event.type === 'token') onToken?.(event.text);
        else if (event.type === 'answer') answer = event;
        else if (event.type === 'error') failure = event;
      }
    }
  }

  if (failure) {
    const err = new Error(failure.detail || 'The question could not be answered.');
    err.status = failure.status;
    throw err;
  }
  if (!answer) {
    // The stream ended without an answer or an error — a dropped connection
    // rather than a refusal. Saying so beats rendering an empty bubble.
    throw new Error('The connection closed before the answer arrived. Please retry.');
  }
  return answer;
}
