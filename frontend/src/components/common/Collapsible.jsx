import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Icon from './Icon';
import './Collapsible.css';

/**
 * Disclosure section with an animated body.
 *
 * Lifted out of the Trial Balance panels, where three copies had drifted apart.
 * `count` is rendered as a pill in the header so a section advertises its size
 * without being opened — which is what makes collapsing safe for long tables.
 */
export default function Collapsible({
  title,
  count,
  icon = 'ledger',
  defaultOpen = false,
  children,
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapse ${open ? 'is-open' : ''}`}>
      <button type="button" className="collapse__head" onClick={() => setOpen((v) => !v)}>
        <Icon name={icon} size={15} className="collapse__icon" />
        <span className="collapse__title">{title}</span>
        {count != null && <span className="pill pill--mute">{count}</span>}
        <motion.span className="collapse__caret" animate={{ rotate: open ? 180 : 0 }}>
          <Icon name="chevron" size={15} />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="collapse__body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
          >
            <div className="collapse__inner">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
