import { motion } from 'framer-motion';
import Icon from './common/Icon';
import { useAuth } from '../auth/AuthContext';
import './Sidebar.css';

export const MODE_ICONS = {
  'statutory-auditor-report': 'seal',
  'financial-statement': 'ledger',
  'trial-balance': 'scales',
  'financial-diagnostic-report': 'pulse',
};

function StatusDot({ status }) {
  // status: 'checking' | 'ready' | 'down' | 'pending'
  const title = {
    checking: 'Checking availability…',
    ready: 'Pipeline ready',
    down: 'Pipeline unavailable — check backend configuration',
    pending: 'Branch not integrated yet',
  }[status];

  return <span className={`side__status side__status--${status}`} title={title} />;
}

export default function Sidebar({ modes, activeId, onSelect, health, children }) {
  const { user, signOut } = useAuth();

  return (
    <aside className="side">
      <div className="side__brand">
        <div className="side__mark">
          <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
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
        </div>
        <div className="side__brand-text">
          <span className="side__name">Artha.AI</span>
          <span className="side__tag">Audit Intelligence</span>
        </div>
      </div>

      <div className="side__scroll">
      <nav className="side__nav" aria-label="Modes">
        <p className="side__heading">Modes</p>

        {modes.map((mode, i) => {
          const isActive = mode.id === activeId;
          const state = !mode.integrated
            ? 'pending'
            : health[mode.id] === undefined
              ? 'checking'
              : health[mode.id]?.available
                ? 'ready'
                : 'down';

          return (
            <motion.button
              key={mode.id}
              type="button"
              className={`side__item ${isActive ? 'is-active' : ''}`}
              onClick={() => onSelect(mode.id)}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.05 + i * 0.05, duration: 0.34, ease: [0.22, 1, 0.36, 1] }}
              aria-current={isActive ? 'page' : undefined}
            >
              {isActive && (
                <motion.span
                  className="side__active-bg"
                  layoutId="side-active"
                  transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                />
              )}

              <span className="side__item-inner">
                <Icon name={MODE_ICONS[mode.id] || 'doc'} size={18} className="side__item-icon" />
                <span className="side__item-label">{mode.short_label}</span>
                <StatusDot status={state} />
              </span>
            </motion.button>
          );
        })}
      </nav>

        {/* The conversation list, when the active mode has one. Passed as
            children rather than fetched here so the sidebar stays a
            presentation component and only App owns the data. */}
        {children}
      </div>

      {/* Signed-in user. Reads context directly instead of taking props: the
          sidebar is app-shell, not a shared surface, so drilling `user` and
          `onSignOut` through App would add two props to no benefit. */}
      <div className="side__footer">
        <div className="side__user">
          <span className="side__avatar" aria-hidden="true">
            <Icon name="user" size={14} />
          </span>
          <span className="side__user-text">
            <span className="side__user-name" title={user?.email || ''}>
              {user?.display_name || user?.username || 'Signed in'}
            </span>
            <span className="side__user-sub">{user?.email || ''}</span>
          </span>
        </div>
        <button
          type="button"
          className="side__signout"
          onClick={() => signOut()}
          title="Sign out"
          aria-label="Sign out"
        >
          <Icon name="logout" size={15} />
        </button>
      </div>
    </aside>
  );
}
