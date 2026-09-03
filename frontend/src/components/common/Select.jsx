import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Icon from './Icon';
import './Select.css';

/**
 * Accessible listbox dropdown.
 *
 * A native <select> can't carry the two-line options (label + sublabel) the
 * entity and financial-year pickers need, so this is a custom listbox with the
 * ARIA roles and keyboard behaviour wired up by hand.
 *
 * options: [{ value, label, sublabel? }]
 */
export default function Select({
  label,
  value,
  options = [],
  onChange,
  placeholder = 'Select…',
  disabled = false,
  loading = false,
  emptyText = 'No options available',
  icon,
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const rootRef = useRef(null);
  const listRef = useRef(null);

  const selected = useMemo(
    () => options.find((o) => o.value === value) || null,
    [options, value]
  );

  const isDisabled = disabled || loading || options.length === 0;

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Opening should highlight the current selection, not the top of the list.
  useEffect(() => {
    if (open) setActive(options.findIndex((o) => o.value === value));
  }, [open, options, value]);

  useEffect(() => {
    if (!open || active < 0) return;
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [open, active]);

  function commit(index) {
    const opt = options[index];
    if (!opt) return;
    onChange?.(opt.value, opt);
    setOpen(false);
  }

  function onKeyDown(e) {
    if (isDisabled) return;

    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActive((i) => Math.min(i + 1, options.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
        break;
      case 'Home':
        e.preventDefault();
        setActive(0);
        break;
      case 'End':
        e.preventDefault();
        setActive(options.length - 1);
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        commit(active);
        break;
      case 'Tab':
        setOpen(false);
        break;
      default:
        break;
    }
  }

  return (
    <div className="sel" ref={rootRef}>
      {label && <label className="sel__label">{label}</label>}

      <button
        type="button"
        className={`sel__trigger ${open ? 'is-open' : ''} ${selected ? 'has-value' : ''}`}
        onClick={() => !isDisabled && setOpen((v) => !v)}
        onKeyDown={onKeyDown}
        disabled={isDisabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {icon && <Icon name={icon} size={16} className="sel__icon" />}

        <span className="sel__value">
          {loading ? 'Loading…' : selected ? selected.label : placeholder}
          {selected?.sublabel && <em className="sel__sub">{selected.sublabel}</em>}
        </span>

        <motion.span
          className="sel__caret"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <Icon name="chevron" size={15} />
        </motion.span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="sel__menu"
            role="listbox"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="sel__list" ref={listRef}>
              {options.length === 0 ? (
                <div className="sel__empty">{emptyText}</div>
              ) : (
                options.map((opt, i) => (
                  <div
                    key={opt.value}
                    role="option"
                    aria-selected={opt.value === value}
                    className={`sel__opt ${i === active ? 'is-active' : ''} ${
                      opt.value === value ? 'is-selected' : ''
                    }`}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => commit(i)}
                  >
                    <span className="sel__opt-text">
                      <span className="sel__opt-label">{opt.label}</span>
                      {opt.sublabel && (
                        <span className="sel__opt-sub">{opt.sublabel}</span>
                      )}
                    </span>
                    {opt.value === value && (
                      <Icon name="check" size={15} className="sel__opt-check" />
                    )}
                  </div>
                ))
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
