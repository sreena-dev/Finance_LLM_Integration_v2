import { motion } from 'framer-motion';
import Icon from './Icon';
import './Notice.css';

const ICONS = { error: 'alert', warn: 'alert', info: 'info', ok: 'check' };

/**
 * Inline status block: info, ok, warn, err. Used for unreachable backends,
 * unconfigured modes and pipeline failures -- the `reason` strings the gateway
 * returns are written to be actionable, so they are surfaced verbatim rather
 * than replaced with a generic "something went wrong". Every tone carries an
 * icon AND plain text; colour is never the only signal.
 */
export default function Notice({ tone = 'info', title, children, action }) {
  return (
    <motion.div
      className={`notice notice--${tone}`}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.2, 0.7, 0.2, 1] }}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <Icon name={ICONS[tone] || 'info'} size={18} className="notice__icon" />
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children && <div className="notice__text">{children}</div>}
        {action && <div className="notice__action">{action}</div>}
      </div>
    </motion.div>
  );
}
