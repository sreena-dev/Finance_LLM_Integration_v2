/**
 * Financial Diagnostic Report — API client.
 *
 * Self-contained on purpose. The shared `src/api/client.js` is owned by the rest
 * of the app; this mode adds its calls here instead so wiring the FDR touches no
 * file another mode depends on.
 *
 * It still follows that client's one binding rule: NO URL IS HARD-CODED. Every
 * call is built from the `base_path` the gateway reports for this mode, so the
 * mode the user selected is structurally the mode whose pipeline runs, and this
 * client cannot reach another mode's endpoints even by mistake.
 */

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
      const res = await fetch('/api/modes');
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
    res = await fetch(path, options);
  } catch {
    throw new Error(
      'Could not reach the backend. Start the stack with `docker compose up -d`, ' +
        'or run the gateway directly: uvicorn app.main:app --port $ARTHA_BACKEND_PORT'
    );
  }

  if (!res.ok) {
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
      headers: { 'Content-Type': 'application/json' },
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
