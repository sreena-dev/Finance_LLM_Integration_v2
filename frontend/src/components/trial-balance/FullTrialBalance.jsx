import { FLAG_TONE, fmtINR, fmtPct } from './tbFormat';
import './FullTrialBalance.css';

/**
 * Every ledger line of the trial balance, with its arithmetic and movement.
 *
 * The figures come from the deterministic validation engine (`/validate`), not
 * from `/audit` — the audit report classifies and narrates, it does not emit a
 * per-ledger table. The Audit tab composes the two client-side, which is also
 * why a validation failure must never break the audit: this section simply
 * doesn't render.
 *
 * Two shapes, decided by `mode`:
 *   single      — one file's own opening → closing movement for the period.
 *   comparison  — union of both periods' ledger codes, with PY and CY columns
 *                 and variance = CY closing − PY closing.
 *
 * A HALT is shown instead of the table, never alongside it. The engine stops at
 * Layer 1 on an arithmetic or structural failure, so any movement computed past
 * that point would be derived from figures already known to be inconsistent.
 */
export default function FullTrialBalance({ fullTb }) {
  if (!fullTb) return null;

  const rows = fullTb.rows || [];
  const isComparison = fullTb.mode === 'comparison';

  if (fullTb.halted) {
    return (
      <div className="tbfull">
        <p className="tbfull__note">
          Arithmetic/structural integrity checks halted — the full trial balance and variance
          are not computed until these are resolved.
        </p>
        <div className="tbfull__halts">
          {(fullTb.haltReasons || []).map((r, i) => (
            <p key={i} className="tbfull__halt">
              <strong>{r.rule_id}</strong>: {r.message}
            </p>
          ))}
        </div>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <p className="tbfull__note">
        Opening balance could not be resolved for this trial balance — per-line movement
        could not be computed.
      </p>
    );
  }

  return (
    <div className="tbfull">
      <p className="tbfull__note">
        {isComparison
          ? 'Every ledger code (union of both periods): opening, debit, credit and closing for '
            + 'each period, plus variance = CY closing − PY closing. Layer 1 (row/aggregate '
            + 'arithmetic, opening→closing continuity) and Layer 2 (ledger count delta) passed '
            + 'with no halt for this pair.'
          : 'Every ledger line in this trial balance: opening balance, debit, credit, closing '
            + 'balance, and the opening→closing movement for the period. Layer 1 (row/aggregate '
            + 'arithmetic) passed with no halt for this file. Rows where this file has no '
            + 'opening-balance figure show "NO_DATA" for movement.'}
      </p>
      <div className="tbfull__scroll">
        <table className="tbfull__table">
          <thead>
            <tr>
              <th className="is-text">Code</th>
              <th className="is-text">Name</th>
              {isComparison ? (
                <>
                  <th>PY opening</th><th>PY debit</th><th>PY credit</th><th>PY closing</th>
                  <th>CY opening</th><th>CY debit</th><th>CY credit</th><th>CY closing</th>
                  <th>Variance</th><th>Variance %</th>
                </>
              ) : (
                <>
                  <th>Opening</th><th>Debit</th><th>Credit</th><th>Closing</th>
                  <th>Movement</th><th>Movement %</th>
                </>
              )}
              <th className="is-text">Flag</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.ledger_code || i}>
                <td className="is-text">{r.ledger_code}</td>
                <td className="is-text">{r.ledger_name || '—'}</td>
                {isComparison ? (
                  <>
                    <td>{fmtINR(r.py_opening)}</td>
                    <td>{fmtINR(r.py_debit)}</td>
                    <td>{fmtINR(r.py_credit)}</td>
                    <td>{fmtINR(r.py_closing)}</td>
                    <td>{fmtINR(r.cy_opening)}</td>
                    <td>{fmtINR(r.cy_debit)}</td>
                    <td>{fmtINR(r.cy_credit)}</td>
                    <td>{fmtINR(r.cy_closing)}</td>
                    <td>{fmtINR(r.variance)}</td>
                    <td>{fmtPct(r.variance_pct)}</td>
                  </>
                ) : (
                  <>
                    <td>{fmtINR(r.opening)}</td>
                    <td>{fmtINR(r.debit)}</td>
                    <td>{fmtINR(r.credit)}</td>
                    <td>{fmtINR(r.closing)}</td>
                    <td>{fmtINR(r.movement)}</td>
                    <td>{fmtPct(r.movement_pct)}</td>
                  </>
                )}
                <td className="is-text">
                  <span className={`pill pill--${FLAG_TONE[r.flag] || 'mute'}`}>{r.flag || '—'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
