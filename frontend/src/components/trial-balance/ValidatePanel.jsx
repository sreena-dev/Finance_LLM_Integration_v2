import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { tbValidate } from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import './ValidatePanel.css';

const STATUS_TONE = { PASS: 'ok', WARNING: 'warn', HALT: 'err', SKIPPED: 'mute' };
const PHASE_TONE = {
  HALTED: 'err',
  LAYER_1: 'navy',
  STRUCTURAL: 'navy',
  VARIANCE: 'ok',
};

const LAYER_LABELS = {
  single: 'Rules',
  py: 'Prior period rules',
  cy: 'Current period rules',
  cross_year_results: 'Cross-year rules',
};

function Collapsible({ title, count, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapse ${open ? 'is-open' : ''}`}>
      <button type="button" className="collapse__head" onClick={() => setOpen((v) => !v)}>
        <Icon name="ledger" size={15} className="collapse__icon" />
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

function RuleRow({ rule }) {
  const [open, setOpen] = useState(false);
  const hasDetails = rule.details && Object.keys(rule.details).length > 0;
  return (
    <div className="tbval-rule">
      <button
        type="button"
        className="tbval-rule__row"
        onClick={() => hasDetails && setOpen((v) => !v)}
        disabled={!hasDetails}
      >
        <span className={`pill pill--${STATUS_TONE[rule.status] || 'mute'}`}>{rule.status}</span>
        <span className="tbval-rule__id">{rule.rule_id}</span>
        <span className="tbval-rule__msg">{rule.message}</span>
        {hasDetails && <Icon name="chevron" size={13} className={`tbval-rule__chev ${open ? 'is-open' : ''}`} />}
      </button>
      {open && hasDetails && (
        <pre className="tbval-rule__details">{JSON.stringify(rule.details, null, 2)}</pre>
      )}
    </div>
  );
}

function num(v) {
  if (typeof v !== 'number') return v ?? '—';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

/** Deterministic PASS/WARNING/HALT/SKIPPED rule gate — no LLM involved. */
export default function ValidatePanel({ mode, doc, priorDoc }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function run() {
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const r = await tbValidate(mode, { docId: doc.doc_id, docIdPrior: priorDoc?.doc_id });
      setResult(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  const flaggedVariances = useMemo(
    () => (result?.variance_rows || []).filter((v) => v.flag && v.flag !== 'NO_THRESHOLD'),
    [result]
  );

  return (
    <div className="tbval">
      <div className="tbval__scroll">
        <div className="tbval__inner">
          <div className="tbval__toolbar">
            <div>
              <h3 className="tbval__title">Deterministic validation gate</h3>
              <p className="tbval__sub">
                {priorDoc
                  ? `Comparing "${doc.filename}" (current) against "${priorDoc.filename}" (prior).`
                  : `Single-table structural and arithmetic checks for "${doc.filename}".`}
                {' '}No LLM is involved — every result is a mechanical rule outcome.
              </p>
            </div>
            <button type="button" className="btn btn--primary" onClick={run} disabled={pending}>
              <Icon name="scale" size={15} />
              {pending ? 'Running…' : result ? 'Re-run' : 'Run validation'}
            </button>
          </div>

          {pending && (
            <div className="tbval__loading">
              <span className="tbdocs__spinner" />
              Evaluating rules…
            </div>
          )}
          {error && <Notice tone="error" title="Validation failed">{error}</Notice>}

          {result && !pending && (
            <>
              <div className="tbval__phase">
                <span className={`pill pill--${PHASE_TONE[result.phase_reached] || 'mute'}`}>
                  Phase reached: {result.phase_reached}
                </span>
              </div>

              {result.summary_narrative && (
                <article className="card tbval-narrative">
                  <Markdown>{result.summary_narrative}</Markdown>
                </article>
              )}

              {Object.entries(result.layer1_results || {}).map(([key, rules]) => (
                <Collapsible key={key} title={LAYER_LABELS[key] || key} count={rules.length} defaultOpen={key === 'single'}>
                  <div className="tbval-rules">
                    {rules.map((rule) => <RuleRow key={rule.rule_id} rule={rule} />)}
                  </div>
                </Collapsible>
              ))}

              {result.structural_result && (
                <Collapsible title="Structural changes (PY vs CY)">
                  <div className="tbval__stats">
                    <div className="tbval__stat">
                      <span className="tbval__stat-label">Prior ledgers</span>
                      <span className="tbval__stat-value">{num(result.structural_result.py_ledger_count)}</span>
                    </div>
                    <div className="tbval__stat">
                      <span className="tbval__stat-label">Current ledgers</span>
                      <span className="tbval__stat-value">{num(result.structural_result.cy_ledger_count)}</span>
                    </div>
                    <div className="tbval__stat">
                      <span className="tbval__stat-label">New ledgers</span>
                      <span className="tbval__stat-value">{result.structural_result.new_ledgers?.length ?? 0}</span>
                    </div>
                    <div className="tbval__stat">
                      <span className="tbval__stat-label">Removed ledgers</span>
                      <span className="tbval__stat-value">{result.structural_result.removed_ledgers?.length ?? 0}</span>
                    </div>
                  </div>
                </Collapsible>
              )}

              {result.variance_rows?.length > 0 && (
                <Collapsible title="Flagged variances" count={flaggedVariances.length}>
                  {flaggedVariances.length === 0 ? (
                    <p className="tbval-note">No ledger crossed the variance-materiality threshold.</p>
                  ) : (
                    <table className="tbaudit-table">
                      <thead>
                        <tr><th>Ledger</th><th>Prior</th><th>Current</th><th>Variance</th><th>Flag</th></tr>
                      </thead>
                      <tbody>
                        {flaggedVariances.slice(0, 200).map((v, i) => (
                          <tr key={i}>
                            <td>{v.ledger_code}</td>
                            <td>{num(v.py_balance)}</td>
                            <td>{num(v.cy_balance)}</td>
                            <td>{num(v.variance)} {v.variance_pct != null ? `(${v.variance_pct.toFixed(1)}%)` : ''}</td>
                            <td><span className="pill pill--warn">{v.flag}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Collapsible>
              )}

              {result.formula_flags?.length > 0 && (
                <Collapsible title="Formula flags" count={result.formula_flags.length}>
                  <pre className="tbval-note">{JSON.stringify(result.formula_flags, null, 2)}</pre>
                </Collapsible>
              )}
            </>
          )}

          {!result && !pending && !error && (
            <div className="tbaudit__idle">
              <Icon name="scale" size={26} className="tbaudit__idle-icon" />
              <p>Run validation to see PASS/WARNING/HALT rule outcomes for this trial balance.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
