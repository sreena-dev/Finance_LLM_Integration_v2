import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { tbPreview, tbUploadGroupingMapped } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './ColumnMapper.css';

const LAYOUT = { FLAT: 'flat', HEADING: 'heading' };

function colLabel(i) {
  return `Column ${i + 1}`;
}

/**
 * Manual fallback when a grouping file's layout can't be auto-detected.
 *
 * Deliberately a sibling of ColumnMapper rather than a mode of it: a trial
 * balance is mapped by account/debit/credit/balance, whereas a grouping file is
 * mapped by identifier -> FSLI label, and its two layouts are structurally
 * different rather than a variation on one shape:
 *
 *   flat     — one row per account, with a separate column holding the FSLI.
 *   heading  — the FSLI is its own row (blank identifier), and the accounts
 *              beneath it belong to it until the next heading row. There is no
 *              group column at all, so asking for one would be meaningless.
 *
 * It shares ColumnMapper.css, since the dialog chrome and raw-row preview are
 * the same and should stay visually identical.
 */
export default function GroupingMapper({ mode, pending, doc, priorDoc, onDone, onCancel }) {
  const { preview_token: token, filename, message } = pending;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sheets, setSheets] = useState([]);
  const [sheetName, setSheetName] = useState('');
  const [headerRow, setHeaderRow] = useState(0);
  const [layout, setLayout] = useState(LAYOUT.FLAT);
  const [codeCol, setCodeCol] = useState(null);
  const [nameCol, setNameCol] = useState(null);
  const [groupCol, setGroupCol] = useState(null);
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

  // At least one identifier column is required either way. The flat layout
  // additionally needs the column carrying the FSLI label; in heading layout
  // that label is a row, so there is nothing more to pick.
  const hasIdentifier = codeCol !== null || nameCol !== null;
  const canSubmit =
    activeSheet && hasIdentifier && (layout === LAYOUT.FLAT ? groupCol !== null : true);

  async function submit() {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await tbUploadGroupingMapped(mode, {
        token,
        sheet_name: sheetName,
        header_row: headerRow,
        code_col: codeCol,
        name_col: nameCol,
        group_col: layout === LAYOUT.FLAT ? groupCol : null,
        heading_mode: layout === LAYOUT.HEADING,
        doc_id: doc?.doc_id || null,
        doc_id_2: priorDoc?.doc_id || null,
      });
      onDone({ ...res, filename });
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const columnOptions = Array.from({ length: nCols }, (_, i) => (
    <option key={i} value={i}>{headerCells[i] || colLabel(i)}</option>
  ));

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
            <h2 className="colmap__title">Map grouping columns for "{filename}"</h2>
            <p className="colmap__sub">
              Automatic detection couldn't identify this grouping file's layout.
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
                  <select value={sheetName} onChange={(e) => setSheetName(e.target.value)}>
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

              <div className="colmap__mode">
                <button
                  type="button"
                  className={`colmap__mode-btn ${layout === LAYOUT.FLAT ? 'is-on' : ''}`}
                  onClick={() => setLayout(LAYOUT.FLAT)}
                >
                  FSLI in its own column
                </button>
                <button
                  type="button"
                  className={`colmap__mode-btn ${layout === LAYOUT.HEADING ? 'is-on' : ''}`}
                  onClick={() => setLayout(LAYOUT.HEADING)}
                >
                  FSLI as heading rows
                </button>
              </div>

              <label className="colmap__field">
                <span>Account code column</span>
                <select
                  value={codeCol ?? ''}
                  onChange={(e) => setCodeCol(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">None</option>
                  {columnOptions}
                </select>
              </label>

              <label className="colmap__field">
                <span>Account name column</span>
                <select
                  value={nameCol ?? ''}
                  onChange={(e) => setNameCol(e.target.value === '' ? null : Number(e.target.value))}
                >
                  <option value="">None</option>
                  {columnOptions}
                </select>
              </label>

              {layout === LAYOUT.FLAT && (
                <label className="colmap__field">
                  <span>FSLI / group column</span>
                  <select
                    value={groupCol ?? ''}
                    onChange={(e) => setGroupCol(e.target.value === '' ? null : Number(e.target.value))}
                  >
                    <option value="">Select…</option>
                    {columnOptions}
                  </select>
                </label>
              )}
            </div>

            {!hasIdentifier && (
              <Notice tone="info" title="Pick an identifier column">
                Choose the account code column, the account name column, or both — these are what
                get matched against the trial balance's accounts.
              </Notice>
            )}
            {layout === LAYOUT.HEADING && (
              <Notice tone="info" title="Heading-row layout">
                The FSLI name is read from rows that have no identifier, and applies to every
                account listed beneath it until the next such row.
              </Notice>
            )}

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

            {submitError && (
              <Notice tone="error" title="Could not parse with this mapping">{submitError}</Notice>
            )}

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
                {submitting ? 'Parsing…' : (
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
