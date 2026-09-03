/**
 * Who is signed in, for the whole app.
 *
 * Mounted above <App/> in main.jsx, so `App` only ever renders with a user
 * present and its boot effect — which calls /api/modes unconditionally on mount
 * — needs no change at all. That is the reason the gate sits here rather than
 * inside App.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { fetchMe, signIn as apiSignIn, signUp as apiSignUp } from './api';
import {
  clearAuth,
  getStoredUser,
  getToken,
  setStoredUser,
  setToken,
  setUnauthorizedHandler,
} from './token';

const AuthContext = createContext(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>.');
  return ctx;
}

export function AuthProvider({ children }) {
  // Seeded from localStorage so a reload does not flash the sign-in screen
  // before the token check comes back. `status` is what actually gates.
  const [user, setUser] = useState(() => (getToken() ? getStoredUser() : null));
  const [status, setStatus] = useState(() => (getToken() ? 'checking' : 'signed-out'));
  const [notice, setNotice] = useState(null);

  const signOut = useCallback((reason) => {
    clearAuth();
    setUser(null);
    setStatus('signed-out');
    setNotice(reason || null);
  }, []);

  // A 401 from ANY call — a mode request, a conversation fetch — lands here.
  // Registered once, on a plain module rather than through React, because the
  // API client is not a component and cannot read context.
  useEffect(() => {
    setUnauthorizedHandler((reason) => signOut(reason || 'Your session ended. Please sign in again.'));
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  // A stored token may have expired while the tab was closed, so it is verified
  // before the app is shown rather than after the first request fails.
  useEffect(() => {
    if (!getToken()) return undefined;

    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (cancelled) return;
        setStoredUser(me);
        setUser(me);
        setStatus('signed-in');
      } catch (err) {
        if (cancelled) return;
        if (err.status === 401) {
          clearAuth();
          setUser(null);
          setStatus('signed-out');
        } else {
          // The server is unreachable or misconfigured — NOT a bad token.
          // Signing the user out here would hide a backend outage behind a
          // login form they cannot get past.
          setStatus('error');
          setNotice(err.message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const adopt = useCallback((payload) => {
    setToken(payload.token);
    setStoredUser(payload.user);
    setUser(payload.user);
    setStatus('signed-in');
    setNotice(null);
    return payload.user;
  }, []);

  const signIn = useCallback(async (creds) => adopt(await apiSignIn(creds)), [adopt]);
  const signUp = useCallback(async (details) => adopt(await apiSignUp(details)), [adopt]);

  const value = useMemo(
    () => ({ user, status, notice, signIn, signUp, signOut, retry: () => window.location.reload() }),
    [user, status, notice, signIn, signUp, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
