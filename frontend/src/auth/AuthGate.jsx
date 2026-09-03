/**
 * Decides whether the app or the sign-in screen renders.
 *
 * Sits above <App/> so App mounts only with a signed-in user. That matters
 * concretely: App's boot effect calls /api/modes on mount, that route now
 * requires a token, and a signed-out App would render "Cannot reach the backend
 * gateway" — an outage message for what is really just being logged out.
 */

import { motion } from 'framer-motion';
import Notice from '../components/common/Notice';
import Icon from '../components/common/Icon';
import AuthScreen from './AuthScreen';
import { useAuth } from './AuthContext';

function Splash() {
  // Deliberately the same markup and animation as App's own boot splash, so
  // verifying a stored token looks like the app starting rather than like a
  // different screen flashing past.
  return (
    <div className="boot">
      <motion.div
        className="boot__mark"
        animate={{ scale: [1, 1.06, 1], opacity: [0.75, 1, 0.75] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
      >
        <svg viewBox="0 0 32 32" width="40" height="40" aria-hidden="true">
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
      </motion.div>
      <p className="boot__text">Starting Artha.AI…</p>
    </div>
  );
}

export default function AuthGate({ children }) {
  const { user, status, notice, retry } = useAuth();

  if (status === 'checking') return <Splash />;

  // A server that is down or misconfigured is NOT a failed login. Showing the
  // sign-in form here would trap someone in a form no password can satisfy.
  if (status === 'error') {
    return (
      <div className="boot">
        <div className="boot__panel">
          <Notice tone="error" title="Cannot verify your session">
            {notice}
            <p style={{ margin: '10px 0 0' }}>
              This is a server problem, not a password problem. Check the gateway
              and its database, then retry.
            </p>
          </Notice>
          <button type="button" className="btn btn--primary" style={{ marginTop: 16 }} onClick={retry}>
            <Icon name="refresh" size={15} />
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!user) return <AuthScreen />;
  return children;
}
