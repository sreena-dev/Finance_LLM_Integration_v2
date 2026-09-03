import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { tbPreview, tbUploadMapped } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './ColumnMapper.css';

const COL_MODE = { DC: 'debit_credit', BAL: 'balance' };

function colLabel(i) {
  return `Column ${i + 1}`;
}

/**
 * Shown when auto-detection can't identify a trial balance's columns.
 * Lets the user pick the header row and which column is which directly off
 * a raw row/column preview, bypassing every keyword heuristic.
 */
export default function ColumnMapper({ mode, pending, onDone, onCancel }) {
  const { preview_token: token, filename, message } = pending;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sheets, setSheets] = useState([]);
  const [sheetName, setSheetName] = useState('');
  const [headerRow, setHeaderRow] = useState(0);
  const [accountCol, setAccountCol] = useState(null);
  const [codeCol, setCodeCol] = useState(null);
  const [colMode, setColMode] = useState(COL_MODE.DC);
  const [debitCol, setDebitCol] = useState(null);
  const [creditCol, setCreditCol] = useState(null);
  const [balanceCol, setBalanceCol] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await tbPreview(mode, token);
        if (cancelled) return;
        setSheets(result.sheets || []);
        setSheetName(result.sheets?.[0]?.name || '');
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const activeSheet = useMemo(
    () => sheets.find((s) => s.name === sheetName) || null,
    [sheets, sheetName]
  );

  const nCols = activeSheet?.n_cols || 0;
  const headerCells = activeSheet?.rows?.[headerRow] || [];

  const canSubmit =
    activeSheet &&
    accountCol !== null &&
    (colMode === COL_MODE.BAL ? balanceCol !== null : debitCol !== null && creditCol !== null);

  async function submit() {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const info = await tbUploadMapped(mode, {
        token,
        sheet_name: sheetName,
        header_row: headerRow,
        account_col: accountCol,
        debit_col: colMode === COL_MODE.DC ? debitCol : null,
        credit_col: colMode === COL_MODE.DC ? creditCol : null,
        balance_col: colMode === COL_MODE.BAL ? balanceCol : null,
        code_col: codeCol,
      });
      onDone(info);
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="colmap-overlay" role="dialog" aria-modal="true">
      <motion.div
        className="colmap card"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      >
        <header className="colmap__head">
          <div>
            <h2 className="colmap__title">Map the columns for "{filename}"</h2>
            <p className="colmap__sub">
              Automatic detection couldn't identify this file's layout.
              {message ? ` ${message}` : ''}
            </p>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            Cancel
          </button>
        </header>

        {loading && <div className="colmap__loading">Loading preview…</div>}
        {error && (
          <div className="colmap__body">
            <Notice tone="error" title="Could not load a preview">{error}</Notice>
          </div>
        )}

        {!loading && !error && activeSheet && (
          <div className="colmap__body">
            <div className="colmap__controls">
              {sheets.length > 1 && (
                <label className="colmap__field">
                  <span>Sheet</span>
                  <select
                    value={sheetName}
                    onChange={(e) => setSheetName(e.target.value)}
                  >
                    {sheets.map((s) => (
                      <option key={s.name} value={s.name}>{s.name}</option>
                    ))}
                  </select>
                </label>
              )}

              <label className="colmap__field">
                <span>Header row</span>
                <input
                  type="number"
                  min={0}
                  max={Math.max(0, (activeSheet.rows?.length || 1) - 1)}
                  value={headerRow}
                  onChange={(e) => setHeaderRow(Number(e.target.value) || 0)}
                />
              </label>

              <label className="colmap__field">
                <span>Account name column</span>
                <select
                  value={accountCol ?? ''}
                  onChange={(e) => setAccountCol(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">Select…</option>
                  {Array.from({ length: nCols }, (_, i) => (
                    <option key={i} value={i}>
                      {headerCells[i] || colLabel(i)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="colmap__field">
                <span>Account code column (optional)</span>
                <select
                  value={codeCol ?? ''}
                  onChange={(e) => setCodeCol(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">None</option>
                  {Array.from({ length: nCols }, (_, i) => (
                    <option key={i} value={i}>
                      {headerCells[i] || colLabel(i)}
                    </option>
                  ))}
                </select>
              </label>

              <div className="colmap__mode">
                <button
                  type="button"
                  className={`colmap__mode-btn ${colMode === COL_MODE.DC ? 'is-on' : ''}`}
                  onClick={() => setColMode(COL_MODE.DC)}
                >
                  Debit + Credit columns
                </button>
                <button
                  type="button"
                  className={`colmap__mode-btn ${colMode === COL_MODE.BAL ? 'is-on' : ''}`}
                  onClick={() => setColMode(COL_MODE.BAL)}
                >
                  Single balance column
                </button>
              </div>

              {colMode === COL_MODE.DC ? (
                <>
                  <label className="colmap__field">
                    <span>Debit column</span>
                    <select
                      value={debitCol ?? ''}
                      onChange={(e) => setDebitCol(e.target.value === '' ? null : Number(e.target.value))}
                    >
                      <option value="">Select…</option>
                      {Array.from({ length: nCols }, (_, i) => (
                        <option key={i} value={i}>{headerCells[i] || colLabel(i)}</option>
                      ))}
                    </select>
                  </label>
                  <label className="colmap__field">
                    <span>Credit column</span>
                    <select
                      value={creditCol ?? ''}
                      onChange={(e) => setCreditCol(e.target.value === '' ? null : Number(e.target.value))}
                    >
                      <option value="">Select…</option>
                      {Array.from({ length: nCols }, (_, i) => (
                        <option key={i} value={i}>{headerCells[i] || colLabel(i)}</option>
                      ))}
                    </select>
                  </label>
                </>
              ) : (
                <label className="colmap__field">
                  <span>Balance column (positive = debit, negative = credit)</span>
                  <select
                    value={balanceCol ?? ''}
                    onChange={(e) => setBalanceCol(e.target.value === '' ? null : Number(e.target.value))}
                  >
                    <option value="">Select…</option>
                    {Array.from({ length: nCols }, (_, i) => (
                      <option key={i} value={i}>{headerCells[i] || colLabel(i)}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <div className="colmap__preview">
              <table>
                <tbody>
                  {(activeSheet.rows || []).map((row, ri) => (
                    <tr key={ri} className={ri === headerRow ? 'is-header' : ''}>
                      <td className="colmap__rowno">{ri}</td>
                      {row.map((cell, ci) => (
                        <td key={ci}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {submitError && <Notice tone="error" title="Could not ingest with this mapping">{submitError}</Notice>}

            <div className="colmap__footer">
              <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={submitting}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={submit}
                disabled={!canSubmit || submitting}
              >
                {submitting ? 'Ingesting…' : (
                  <>
                    <Icon name="check" size={15} />
                    Use this mapping
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </motion.div>
    </div>
  );
}
