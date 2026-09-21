/**
 * Sign in / sign up.
 *
 * Two panes: the form on the left, and on the right an illustration of the one
 * promise the product makes -- every figure is traced to its page, and a figure
 * that cannot be read is left for you, never guessed. The right pane is
 * decorative and labelled as an illustrative extract; it uses no real data and
 * carries no official mark (the reserved slot is empty unless configured).
 *
 * A full-screen view rather than a modal: there is no screen behind it yet, so a
 * modal would be machinery -- overlay, focus trap, escape handling -- around
 * nothing. There is no "Forgot password" link because the backend has no reset
 * flow; a link that led nowhere would be worse than none.
 */

import { useState } from 'react';
import { motion } from 'framer-motion';
import Icon from '../components/common/Icon';
import Notice from '../components/common/Notice';
import BrandMark from '../components/common/BrandMark';
import { DISCLAIMER } from '../config/brand';
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

/** The right-hand illustration: an illustrative statement extract. */
function LiveCheck() {
  return (
    <div className="auth__art" aria-hidden="true">
      <span className="pill pill--ok auth__art-float auth__art-float--top">
        <Icon name="check" size={12} /> 0 figures guessed
      </span>

      <div className="auth__extract">
        <header className="auth__extract-head">
          <div>
            <strong>Statement of Profit and Loss</strong>
            <small>Illustrative extract · amounts in lakh</small>
          </div>
          <span className="pill pill--ok"><span className="dot" /> Live check</span>
        </header>
        <ul>
          <li><span>Employee benefits expense</span><span className="num">12,859.00 <Icon name="check" size={12} /></span></li>
          <li>
            <span>Finance costs</span>
            <span className="fig fig--unreadable"><span className="fig__label">unreadable</span></span>
          </li>
          <li>
            <span>Impairment losses</span>
            <span className="fig fig--recovered"><span className="fig__label">recovered ?</span><span>1,757.00</span></span>
          </li>
          <li><span>Other expenses</span><span className="num">21,946.85 <Icon name="check" size={12} /></span></li>
          <li className="is-total"><span>Total expenses</span><span className="num">1,17,559.45</span></li>
        </ul>
        <p className="auth__extract-foot"><Icon name="check" size={13} /> Total held: one figure needs you. Nothing is estimated.</p>
      </div>

      <div className="auth__scanrow">
      <div className="auth__scan">
        <header><span>Source scan · page 61</span><span className="pill pill--mute">straightened</span></header>
        <div className="auth__scan-body">
          <i /><i className="short" />
          <p><span>Finance costs</span><span className="num">26</span><b /></p>
          <i /><i className="short" />
        </div>
      </div>

      <span className="pill auth__cite">
        <Icon name="search" size={12} /> Every answer cites its page
      </span>

      </div>

      <div className="auth__headline">
        <h2>Every figure, traced to its page.</h2>
        <p>If a number can’t be read, we say so and leave it for you. Nothing is guessed, and anything you enter is always labelled as yours.</p>
      </div>
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
      <motion.section
        className="auth__form-pane"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.32, ease: [0.2, 0.7, 0.2, 1] }}
      >
        <div className="auth__brand">
          <svg viewBox="0 0 32 32" width="36" height="36" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--navy-700)" />
            <path d="M9 11h9M9 16h6M9 21h5" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
            <path d="M17 20.5l3 3 5.5-7" stroke="var(--gold-600)" strokeWidth="2.4"
                  strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <p className="auth__name">Artha<span>.AI</span></p>
          <BrandMark height={28} />
        </div>

        <div className="auth__main">
          <p className="auth__eyebrow">Audit workspace</p>
          <h1 className="auth__title">{isSignUp ? 'Create your account.' : 'Welcome back.'}</h1>
          <p className="auth__sub">
            {isSignUp
              ? 'Your Financial Statements conversations are saved to your account.'
              : 'Sign in to ask questions of financial statements, draft auditor’s reports and check trial balances.'}
          </p>

          {notice && !error ? (
            <div className="auth__notice">
              <Notice tone="info">
                {notice} Your conversations are saved.
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

          <div className="auth__tabs" role="tablist" aria-label="Sign in or create an account">
            <button type="button" role="tab" aria-selected={!isSignUp}
                    className={!isSignUp ? 'is-active' : ''} onClick={() => switchTo('signin')}>Sign in</button>
            <button type="button" role="tab" aria-selected={isSignUp}
                    className={isSignUp ? 'is-active' : ''} onClick={() => switchTo('signup')}>Create account</button>
          </div>

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
                  label="Work email"
                  type="email"
                  placeholder="you@firm.in"
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
                  label="Work email or username"
                  placeholder="you@firm.in"
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
        </div>

        <footer className="auth__foot">
          <p className="auth__promise">
            <span><Icon name="check" size={13} /> Figures verified by arithmetic</span>
            <span><Icon name="check" size={13} /> Sources on every answer</span>
          </p>
          <p className="auth__disclaimer">{DISCLAIMER}</p>
        </footer>
      </motion.section>

      <aside className="auth__art-pane">
        <LiveCheck />
      </aside>
    </div>
  );
}
