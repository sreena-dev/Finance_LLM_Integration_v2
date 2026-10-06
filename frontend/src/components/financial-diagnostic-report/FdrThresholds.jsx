/**
 * Auditor-tunable thresholds: the "Thresholds" button in the XBRL Direct topbar and the
 * centred dialog it opens.
 *
 * WHAT A SAVE DOES
 * ----------------
 * The dialog edits a local DRAFT. Nothing reaches the backend until "Save & apply", which
 * sends only the values that differ from what is already saved. The backend validates the
 * whole request (all-or-nothing), stores it, and every later report read uses it - so on
 * success we tell the parent (`onApplied`) to refetch the blocks. "Reset all" only moves
 * the draft back to the shipped defaults; it too takes effect on Save, so it cannot be
 * clicked by accident.
 *
 * CONTROLS
 * --------
 * Percentages and percentage-point gaps are SLIDERS (a bounded range an auditor reasons
 * about by feel, with the shipped default marked on the track). Ratios (x), day counts and
 * rupee amounts are STEPPERS (- / +), where a precise small step matters more than a range.
 * Neither is a free text box, so a value cannot be mistyped out of range.
 */

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchXbrlThresholds, saveXbrlThresholds } from './api';

const EASE = [0.22, 1, 0.36, 1];

function decimals(step) {
  const s = String(step);
  return s.includes('.') ? s.split('.')[1].length : 0;
}

function snap(value, item) {
  const d = Math.max(decimals(item.step), 0);
  const clamped = Math.min(item.max, Math.max(item.min, value));
  return Number(clamped.toFixed(d));
}

function fmt(value, item) {
  return `${Number(value).toLocaleString('en-IN', { maximumFractionDigits: Math.max(decimals(item.step), 2) })}`;
}

function Icon({ name }) {
  const common = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  if (name === 'sliders') {
    return (
      <svg {...common}>
        <path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0" />
        <circle cx="16" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="18" cy="18" r="2" />
      </svg>
    );
  }
  if (name === 'search') {
    return (<svg {...common}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>);
  }
  if (name === 'close') {
    return (<svg {...common}><path d="M6 6l12 12M18 6L6 18" /></svg>);
  }
  if (name === 'undo') {
    return (<svg {...common}><path d="M9 14L4 9l5-5" /><path d="M4 9h10a6 6 0 010 12h-3" /></svg>);
  }
  return null;
}

function SliderControl({ item, value, onChange }) {
  const span = item.max - item.min || 1;
  const p = (value - item.min) / span;
  const pd = (item.default - item.min) / span;
  return (
    <div className="fdr-thr__slider" style={{ '--p': p, '--pd': pd }}>
      <div className="fdr-thr__track">
        <input
          type="range"
          className="fdr-thr__range"
          min={item.min}
          max={item.max}
          step={item.step}
          value={value}
          aria-label={item.label}
          aria-valuetext={`${fmt(value, item)} ${item.unit}`}
          onChange={(e) => onChange(snap(Number(e.target.value), item))}
        />
        <span className="fdr-thr__tick" title={`Default ${fmt(item.default, item)} ${item.unit}`} />
      </div>
      <output className="fdr-thr__readout">
        {fmt(value, item)}
        <span className="fdr-thr__unit">{item.unit}</span>
      </output>
    </div>
  );
}

function StepperControl({ item, value, onChange }) {
  const dec = (dir) => onChange(snap(value + dir * item.step, item));
  return (
    <div className="fdr-thr__stepper" role="group" aria-label={item.label}>
      <button type="button" className="fdr-thr__stepbtn" onClick={() => dec(-1)}
        disabled={value <= item.min} aria-label={`Decrease ${item.label}`}>&minus;</button>
      <output
        className="fdr-thr__stepval"
        tabIndex={0}
        role="spinbutton"
        aria-valuenow={value}
        aria-valuemin={item.min}
        aria-valuemax={item.max}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp') { e.preventDefault(); dec(1); }
          if (e.key === 'ArrowDown') { e.preventDefault(); dec(-1); }
        }}
      >
        {fmt(value, item)}
        <span className="fdr-thr__unit">{item.unit}</span>
      </output>
      <button type="button" className="fdr-thr__stepbtn" onClick={() => dec(1)}
        disabled={value >= item.max} aria-label={`Increase ${item.label}`}>+</button>
    </div>
  );
}

function Row({ item, draft, saved, onChange, onReset }) {
  const value = draft;
  const atDefault = Math.abs(value - item.default) < 1e-9;
  const pending = Math.abs(value - saved) > 1e-9;
  return (
    <li className={`fdr-thr__row${pending ? ' is-pending' : ''}`}>
      <div className="fdr-thr__info">
        <div className="fdr-thr__label">
          {item.label}
          <span className="fdr-thr__tag">{item.used_in}</span>
        </div>
        <p className="fdr-thr__hint">{item.hint}</p>
      </div>
      <div className="fdr-thr__ctl">
        {item.control === 'slider'
          ? <SliderControl item={item} value={value} onChange={onChange} />
          : <StepperControl item={item} value={value} onChange={onChange} />}
      </div>
      <div className="fdr-thr__meta">
        <span className="fdr-thr__default">Default {fmt(item.default, item)} {item.unit}</span>
        {!atDefault && (
          <button type="button" className="fdr-thr__reset" onClick={onReset} title="Restore the default">
            <Icon name="undo" /> Reset
          </button>
        )}
      </div>
    </li>
  );
}

function Dialog({ data, onClose, onSaved }) {
  const reduce = useReducedMotion();
  const [draft, setDraft] = useState(() => Object.fromEntries(data.items.map((i) => [i.key, i.value])));
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const searchRef = useRef(null);

  useEffect(() => {
    searchRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !saving) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, saving]);

  const savedByKey = useMemo(() => Object.fromEntries(data.items.map((i) => [i.key, i.value])), [data]);
  const pendingKeys = data.items.filter((i) => Math.abs(draft[i.key] - savedByKey[i.key]) > 1e-9).map((i) => i.key);
  const customCount = data.items.filter((i) => Math.abs(draft[i.key] - i.default) > 1e-9).length;

  const q = query.trim().toLowerCase();
  const visible = useMemo(() => data.items.filter((i) => {
    if (!q) return true;
    return `${i.label} ${i.group} ${i.used_in} ${i.hint} ${i.key}`.toLowerCase().includes(q);
  }), [data, q]);

  const grouped = useMemo(() => data.groups
    .map((g) => ({ group: g, items: visible.filter((i) => i.group === g) }))
    .filter((g) => g.items.length > 0), [data.groups, visible]);

  const setValue = useCallback((key, v) => setDraft((d) => ({ ...d, [key]: v })), []);

  const resetAll = () => setDraft(Object.fromEntries(data.items.map((i) => [i.key, i.default])));

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const values = Object.fromEntries(pendingKeys.map((k) => [k, draft[k]]));
      const next = await saveXbrlThresholds(values);
      onSaved(next);
    } catch (err) {
      setError(err.message || 'The thresholds could not be saved.');
      setSaving(false);
    }
  };

  const dur = reduce ? 0 : 0.24;
  return (
    <motion.div
      className="fdr-thr__overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.18, ease: 'easeIn' } }}
      transition={{ duration: dur, ease: EASE }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}
    >
      <motion.section
        className="fdr-thr"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fdr-thr-title"
        initial={{ opacity: 0, y: 16, scale: 0.965 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 10, scale: 0.975, transition: { duration: reduce ? 0 : 0.18, ease: 'easeIn' } }}
        transition={{ duration: dur, ease: EASE }}
      >
        <header className="fdr-thr__head">
          <div>
            <h2 id="fdr-thr-title" className="fdr-thr__title">Report thresholds</h2>
            <p className="fdr-thr__sub">
              Set where each signal and cluster starts to flag. Shipped defaults are marked on every control;
              a saved change applies to the report for everyone.
            </p>
          </div>
          <button type="button" className="fdr-thr__x" onClick={onClose} aria-label="Close" disabled={saving}>
            <Icon name="close" />
          </button>
        </header>

        <div className="fdr-thr__tools">
          <label className="fdr-thr__search">
            <Icon name="search" />
            <input
              ref={searchRef}
              type="search"
              placeholder="Search thresholds, signals (S09), blocks…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search thresholds"
            />
          </label>
          <span className="fdr-thr__count">{visible.length} of {data.items.length}</span>
        </div>

        <div className="fdr-thr__list" role="region" aria-label="Thresholds" tabIndex={-1}>
          {grouped.length === 0 && (
            <p className="fdr-thr__empty">
              No thresholds match "{query}".
            </p>
          )}
          {grouped.map(({ group, items }) => (
            <section key={group} className="fdr-thr__group">
              <h3 className="fdr-thr__ghead">{group}</h3>
              <ul className="fdr-thr__rows">
                {items.map((item) => (
                  <Row
                    key={item.key}
                    item={item}
                    draft={draft[item.key]}
                    saved={savedByKey[item.key]}
                    onChange={(v) => setValue(item.key, v)}
                    onReset={() => setValue(item.key, item.default)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>

        <footer className="fdr-thr__foot">
          <div className="fdr-thr__status" aria-live="polite">
            {error ? (
              <span className="fdr-thr__err">{error}</span>
            ) : pendingKeys.length > 0 ? (
              <span><strong>{pendingKeys.length}</strong> unsaved change{pendingKeys.length === 1 ? '' : 's'}</span>
            ) : customCount > 0 ? (
              <span>{customCount} custom value{customCount === 1 ? '' : 's'} in effect</span>
            ) : (
              <span>All thresholds at their defaults</span>
            )}
            {data.updated_by && pendingKeys.length === 0 && !error && (
              <span className="fdr-thr__who"> · last saved by {data.updated_by}</span>
            )}
          </div>
          <div className="fdr-thr__actions">
            <button type="button" className="fdr-btn fdr-btn--ghost" onClick={resetAll}
              disabled={saving || customCount === 0}>Reset all to defaults</button>
            <button type="button" className="fdr-btn fdr-btn--ghost" onClick={onClose} disabled={saving}>Cancel</button>
            <button type="button" className="fdr-btn fdr-btn--primary" onClick={save}
              disabled={saving || pendingKeys.length === 0}>
              {saving ? 'Saving…' : 'Save & apply'}
            </button>
          </div>
        </footer>
      </motion.section>
    </motion.div>
  );
}

/**
 * The topbar button. Loads the threshold list once (for the "N custom" badge) and again
 * each time the dialog opens, so it never shows values another user has since changed.
 */
export function XbrlThresholdButton({ onApplied }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const anchor = useRef(null);

  useEffect(() => {
    let live = true;
    fetchXbrlThresholds().then((d) => { if (live) setData(d); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const openDialog = async () => {
    setBusy(true);
    setLoadError('');
    try {
      setData(await fetchXbrlThresholds());
      setOpen(true);
    } catch (err) {
      setLoadError(err.message || 'Thresholds could not be loaded.');
    } finally {
      setBusy(false);
    }
  };

  const custom = data?.overridden_count || 0;
  const host = anchor.current?.closest('.fdr-root') || document.body;

  return (
    <div className="fdr-field fdr-field--thr" ref={anchor}>
      <span className="fdr-field__label">&nbsp;</span>
      <button type="button" className="fdr-btn fdr-btn--ghost fdr-thr__open" onClick={openDialog} disabled={busy}>
        <Icon name="sliders" />
        {busy ? 'Loading…' : 'Thresholds'}
        {custom > 0 && <span className="fdr-thr__badge" title={`${custom} custom threshold${custom === 1 ? '' : 's'} in effect`}>{custom}</span>}
      </button>
      <span className="fdr-field__hint fdr-thr__loaderr">{loadError || ' '}</span>
      {createPortal(
        <AnimatePresence>
          {open && data && (
            <Dialog
              key="thr"
              data={data}
              onClose={() => setOpen(false)}
              onSaved={(next) => { setData(next); setOpen(false); onApplied?.(next.version); }}
            />
          )}
        </AnimatePresence>,
        host,
      )}
    </div>
  );
}
