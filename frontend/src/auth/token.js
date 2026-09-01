/**
 * The session token, readable from plain modules.
 *
 * A LEAF ON PURPOSE. This file imports nothing — not React, not the API client.
 * `api/client.js` needs the token from inside a plain async function where no
 * hook can be called, and `AuthContext` needs to set it. If either of those
 * reached for the other directly the two would import in a cycle; both importing
 * this instead keeps the graph acyclic.
 *
 * The value is mirrored in localStorage so a refresh does not sign you out, and
 * held in a module variable so the common path is not a synchronous storage read
 * on every request.
 */

const TOKEN_KEY = 'artha.token';
const USER_KEY = 'artha.user';

let token = null;
let loaded = false;

// Every access is wrapped: a private window, cleared site data, or a browser
// configured to block storage makes these throw rather than return null, and a
// sign-in screen that crashes is worse than one that forgets you.
function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* in-memory only for this session */
  }
}

export function getToken() {
  if (!loaded) {
    token = read(TOKEN_KEY);
    loaded = true;
  }
  return token;
}

export function setToken(value) {
  token = value || null;
  loaded = true;
  write(TOKEN_KEY, token);
}

export function getStoredUser() {
  const raw = read(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setStoredUser(user) {
  write(USER_KEY, user ? JSON.stringify(user) : null);
}

export function clearAuth() {
  setToken(null);
  setStoredUser(null);
}

/** The Authorization header, or {} when signed out. Spread into a fetch init. */
export function authHeaders() {
  const value = getToken();
  return value ? { Authorization: `Bearer ${value}` } : {};
}

/**
 * What to do when the server says the token is no longer good.
 *
 * `AuthContext` registers a handler that clears the session and drops back to
 * the sign-in screen. Without this, an expired token renders as every mode
 * being "unavailable" — the least informative possible description of "your
 * session ended", and exactly the kind of misleading message this codebase
 * works hard to avoid elsewhere.
 */
let onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  onUnauthorized = typeof fn === 'function' ? fn : null;
}

export function notifyUnauthorized(reason) {
  clearAuth();
  if (onUnauthorized) onUnauthorized(reason);
}
