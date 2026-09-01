/**
 * Auth calls.
 *
 * Kept out of `api/client.js` because these three are the only routes that are
 * reachable WITHOUT a token, and because `client.js`'s error handling is built
 * around "a mode is misbehaving" — its messages talk about starting the gateway
 * and port conflicts, which is unhelpful phrasing for "that password is wrong".
 */

import { authHeaders } from './token';

async function call(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new Error(
      'Could not reach the server. Check that the backend is running, then try again.'
    );
  }

  const isJson = (res.headers.get('content-type') || '').includes('application/json');
  const payload = isJson ? await res.json().catch(() => ({})) : {};

  if (!res.ok) {
    // FastAPI reports a validation failure as a list of per-field objects.
    // Rendering that raw shows the user "[object Object]", so the first message
    // is pulled out — it is the one that names what they need to change.
    let detail = payload.detail;
    if (Array.isArray(detail)) detail = detail[0]?.msg || 'That input is not valid.';
    if (typeof detail !== 'string') detail = `Request failed (HTTP ${res.status}).`;
    const err = new Error(detail.replace(/^Value error,\s*/i, ''));
    err.status = res.status;
    throw err;
  }
  return payload;
}

export function signUp({ username, email, password, displayName }) {
  return call('/api/auth/signup', {
    method: 'POST',
    body: { username, email, password, display_name: displayName || null },
  });
}

export function signIn({ login, password }) {
  return call('/api/auth/login', { method: 'POST', body: { login, password } });
}

/** Who the stored token belongs to. Throws with status 401 when it is no good. */
export function fetchMe() {
  return call('/api/auth/me');
}
