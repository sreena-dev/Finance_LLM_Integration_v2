import { useState } from 'react';
import Collapsible from '../common/Collapsible';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import { FLAG_TONE, fmtINR, fmtNum, fmtPct } from './tbFormat';
import './AuditCard.css';
import './FullTrialBalance.css';

const STATUS_TONE = { PASS: 'ok', WARNING: 'warn', HALT: 'err', SKIPPED: 'mute' };
const PHASE_TONE = { HALTED: 'err', LAYER_1: 'navy', STRUCTURAL: 'navy', VARIANCE: 'ok' };
const PHASE_LABEL = {
  LAYER_1: 'Layer 1 complete',
  STRUCTURAL: 'Structural comparison complete',
  VARIANCE: 'Comparison complete',
  HALTED: 'Halted at Layer 1',
};
const LAYER_LABELS = {
  single: 'Rules',
  py: 'Prior period rules',
  cy: 'Current period rules',
  cross_year_results: 'Cross-year rules',
};

function RuleRow({ rule }) {
  const [open, setOpen] = useState(false);
  const hasDetails = rule.details && Object.keys(rule.details).length > 0;
  return (
    <div className="tbcard-rule">
      <button
        type="button"
        className="tbcard-rule__row"
        onClick={() => hasDetails && setOpen((v) => !v)}
        disabled={!hasDetails}
      >
        <span className={`pill pill--${STATUS_TONE[rule.status] || 'mute'}`}>{rule.status}</span>
        <span className="tbcard-rule__id">{rule.rule_id}</span>
        <span className="tbcard-rule__msg">{rule.message}</span>
        {hasDetails && (
          <Icon name="chevron" size={13} className={`tbcard-rule__chev ${open ? 'is-open' : ''}`} />
        )}
      </button>
      {open && hasDetails && (
        <pre className="tbcard-rule__details">{JSON.stringify(rule.details, null, 2)}</pre>
      )}
    </div>
  );
}

/**
 * One completed validation run — mechanical PASS/WARNING/HALT/SKIPPED only.
 *
 * The narrative is the primary content and is always visible; the rule lists and
 * the variance table supplement it. That ordering is deliberate: the narrative is
 * the part written to be read, and burying it under thirty collapsed rule rows
 * would invert the emphasis.
 */
export default function ValidateCard({ msg }) {
  const { result, doc, priorDoc } = msg;
  const varianceRows = result.variance_rows || [];
  const flagged = varianceRows.filter((v) => v.flag && v.flag !== 'NO_THRESHOLD').length;

  return (
    <article className="card tbcard">
      <header className="tbcard__head">
        <h3 className="tbcard__title">
          TB Validation{priorDoc ? ` — ${doc.filename} vs ${priorDoc.filename}` : ` — ${doc.filename}`}
        </h3>
        <span className={`pill pill--${PHASE_TONE[result.phase_reached] || 'mute'}`}>
          {PHASE_LABEL[result.phase_reached] || result.phase_reached}
        </span>
      </header>

      {result.summary_narrative && (
        <div className="tbcard__report">
          <Markdown>{result.summary_narrative}</Markdown>
        </div>
      )}

      {Object.entries(result.layer1_results || {}).map(([key, rules]) => (
        <Collapsible key={key} title={LAYER_LABELS[key] || key} count={rules.length}>
          <div className="tbcard-rules">
            {rules.map((rule, i) => <RuleRow key={rule.rule_id || i} rule={rule} />)}
          </div>
        </Collapsible>
      ))}

      {result.structural_result && (
        <Collapsible title="Structural changes (PY vs CY)">
          <div className="tbcard__stats">
            <div className="tbcard__stat">
              <span className="tbcard__stat-label">Prior ledgers</span>
              <span className="tbcard__stat-value">{fmtNum(result.structural_result.py_ledger_count)}</span>
            </div>
            <div className="tbcard__stat">
              <span className="tbcard__stat-label">Current ledgers</span>
              <span className="tbcard__stat-value">{fmtNum(result.structural_result.cy_ledger_count)}</span>
            </div>
            <div className="tbcard__stat">
              <span className="tbcard__stat-label">New ledgers</span>
              <span className="tbcard__stat-value">{result.structural_result.new_ledgers?.length ?? 0}</span>
            </div>
            <div className="tbcard__stat">
              <span className="tbcard__stat-label">Removed ledgers</span>
              <span className="tbcard__stat-value">{result.structural_result.removed_ledgers?.length ?? 0}</span>
            </div>
          </div>
        </Collapsible>
      )}

      {varianceRows.length > 0 && (
        <Collapsible
          title={`Variance (${varianceRows.length} ledger${varianceRows.length === 1 ? '' : 's'})`}
          count={flagged > 0 ? `${flagged} flagged` : undefined}
        >
          {/* Every ledger, not only the flagged ones: a ledger that moved but
              stayed under the threshold is a real result, and filtering it out
              makes this read as an exception list and hides the denominator. */}
          <p className="tbcard-note">
            Per ledger code, union of both periods: variance = CY closing − PY closing.
            Flags apply only when a materiality threshold is supplied — HIGH_PRIORITY
            (&gt;2× threshold), MEDIUM (&gt;threshold), LOW (≤threshold), NEW_ENTRY / DROPPED
            (present in one period only), NO_CHANGE (zero variance), NO_THRESHOLD (no threshold
            supplied, so computed but not judged).
          </p>
          <div className="tbfull__scroll">
            <table className="tbfull__table">
              <thead>
                <tr>
                  <th className="is-text">Ledger code</th>
                  <th>PY closing</th>
                  <th>CY closing</th>
                  <th>Variance</th>
                  <th>Variance %</th>
                  <th className="is-text">Flag</th>
                </tr>
              </thead>
              <tbody>
                {varianceRows.map((v, i) => (
                  <tr key={v.ledger_code || i}>
                    <td className="is-text">{v.ledger_code}</td>
                    <td>{fmtINR(v.py_balance)}</td>
                    <td>{fmtINR(v.cy_balance)}</td>
                    <td>{fmtINR(v.variance)}</td>
                    <td>{fmtPct(v.variance_pct)}</td>
                    <td className="is-text">
                      <span className={`pill pill--${FLAG_TONE[v.flag] || 'mute'}`}>{v.flag}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Collapsible>
      )}

      {result.formula_flags?.length > 0 && (
        <Collapsible title="Formula flags" count={result.formula_flags.length}>
          <pre className="tbcard-rule__details">{JSON.stringify(result.formula_flags, null, 2)}</pre>
        </Collapsible>
      )}
    </article>
  );
}
