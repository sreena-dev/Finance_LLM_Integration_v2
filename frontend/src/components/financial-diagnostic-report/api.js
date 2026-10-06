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
 * planning-first structure. The component owns the anchor click, this module
 * owns the request.
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
 * Auditor-tunable thresholds. Values cross this boundary in the units the UI shows
 * (25 for 25 %), never the code's units; the backend owns the conversion and the bounds
 * check, so the UI cannot persist an out-of-range value. A save changes the report for
 * every user, so callers should refetch the blocks afterwards.
 */
export async function fetchXbrlThresholds() {
  const base = await getBasePath();
  return request(`${base}/xbrl/thresholds`);
}

export async function saveXbrlThresholds(values) {
  const base = await getBasePath();
  return request(`${base}/xbrl/thresholds`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
}

export async function resetXbrlThresholds(keys = null) {
  const base = await getBasePath();
  return request(`${base}/xbrl/thresholds/reset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  });
}
