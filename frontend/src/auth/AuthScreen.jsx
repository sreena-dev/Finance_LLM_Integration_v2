/**
 * Sign in / sign up.
 *
 * The app's first real <form> — there is no form library here and no input
 * styling existed before this, so the fields are built from the same tokens
 * everything else uses (src/styles/index.css) and errors are rendered through
 * <Notice tone="error">, which is this codebase's established error idiom.
 *
 * A full-screen view rather than a modal: the two ad-hoc dialogs in this app
 * (ColumnMapper, TbRunPicker) exist because something is layered over a working
 * screen. Here there is no screen behind it yet, so a modal would be machinery —
 * overlay, focus trap, escape handling — around nothing.
 */

import { useState } from 'react';
import { motion } from 'framer-motion';
import Notice from '../components/common/Notice';
import { useAuth } from './AuthContext';
import './AuthScreen.css';

function Field({ id, label, hint, ...input }) {
  return (
    <div className="auth__field">
      <label className="auth__label" htmlFor={id}>
        {label}
      </label>
      <input id={id} className="auth__input" {...input} />
      {hint ? <span className="auth__hint">{hint}</span> : null}
    </div>
  );
}

export default function AuthScreen() {
  const { signIn, signUp, notice } = useAuth();
  const [mode, setMode] = useState('signin');
  const [values, setValues] = useState({
    login: '', username: '', email: '', password: '', displayName: '',
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const isSignUp = mode === 'signup';
  const set = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }));

  function switchTo(next) {
    setMode(next);
    setError(null);
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (isSignUp) {
        await signUp({
          username: values.username.trim(),
          email: values.email.trim(),
          password: values.password,
          displayName: values.displayName.trim(),
        });
      } else {
        await signIn({ login: values.login.trim(), password: values.password });
      }
      // No navigation: AuthGate re-renders into the app as soon as the context
      // reports a user. There is no router here to push to.
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <motion.div
        className="auth__card card"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="auth__brand">
          <svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--navy-700)" />
            <path
              d="M9 22L15 10L21 22"
              stroke="#fff"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path d="M11.3 17.5H18.7" stroke="var(--gold-600)" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
          <div>
            <p className="auth__name">Artha.AI</p>
            <p className="auth__tag">Audit Intelligence</p>
          </div>
        </div>

        <h1 className="auth__title">{isSignUp ? 'Create an account' : 'Sign in'}</h1>
        <p className="auth__sub">
          {isSignUp
            ? 'Your Financial Statements conversations are saved to your account.'
            : 'Sign in to continue to the audit platform.'}
        </p>

        {notice && !error ? (
          <div className="auth__notice">
            <Notice tone="warn" title="Signed out">
              {notice}
            </Notice>
          </div>
        ) : null}

        {error ? (
          <div className="auth__notice">
            <Notice tone="error" title={isSignUp ? 'Could not create the account' : 'Could not sign in'}>
              {error}
            </Notice>
          </div>
        ) : null}

        <form className="auth__form" onSubmit={onSubmit} noValidate>
          {isSignUp ? (
            <>
              <Field
                id="username"
                label="Username"
                value={values.username}
                onChange={set('username')}
                autoComplete="username"
                required
                hint="3–40 characters: letters, digits, dot, underscore or hyphen."
              />
              <Field
                id="email"
                label="Email"
                type="email"
                value={values.email}
                onChange={set('email')}
                autoComplete="email"
                required
              />
              <Field
                id="displayName"
                label="Display name"
                value={values.displayName}
                onChange={set('displayName')}
                autoComplete="name"
                hint="Optional — shown in the sidebar."
              />
              <Field
                id="password"
                label="Password"
                type="password"
                value={values.password}
                onChange={set('password')}
                autoComplete="new-password"
                required
                hint="At least 8 characters."
              />
            </>
          ) : (
            <>
              <Field
                id="login"
                label="Username or email"
                value={values.login}
                onChange={set('login')}
                autoComplete="username"
                required
              />
              <Field
                id="password"
                label="Password"
                type="password"
                value={values.password}
                onChange={set('password')}
                autoComplete="current-password"
                required
              />
            </>
          )}

          <button type="submit" className="btn btn--primary auth__submit" disabled={busy}>
            {busy ? 'Working…' : isSignUp ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <p className="auth__switch">
          {isSignUp ? 'Already have an account?' : 'No account yet?'}{' '}
          <button
            type="button"
            className="auth__link"
            onClick={() => switchTo(isSignUp ? 'signin' : 'signup')}
          >
            {isSignUp ? 'Sign in' : 'Create one'}
          </button>
        </p>
      </motion.div>
    </div>
  );
}
