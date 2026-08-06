import { useState } from 'react';
import { motion } from 'framer-motion';
import { tbDeleteDocument } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import GroupingUpload from './GroupingUpload';
import PdfEvidence from './PdfEvidence';
import './TbRunPicker.css';

/**
 * Launch dialog for an audit run.
 *
 * One click cycles a file through CURRENT → PRIOR → unselected, which is how the
 * source UI does it: picking two periods is the same gesture as picking one, so
 * a comparison never needs a separate mode.
 *
 * The optional inputs live here rather than beside the results because they are
 * inputs *to* a run — deciding them after seeing a report would invite re-reading
 * figures that were produced without them.
 */
export default function TbRunPicker({
  mode,
  documents,
  uploading,
  onUpload,
  currentId,
  priorId,
  onSelect,
  onDeleted,
  grouping,
  onGroupingChange,
  onGroupingNeedsMapping,
  pdfIds,
  onPdfIdsChange,
  onRun,
  onCancel,
}) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  const current = (documents || []).find((d) => d.doc_id === currentId) || null;
  const prior = (documents || []).find((d) => d.doc_id === priorId) || null;

  async function remove(docId) {
    setBusyId(docId);
    setError(null);
    try {
      await tbDeleteDocument(mode, docId);
      onDeleted(docId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="tbpick-overlay" role="dialog" aria-modal="true">
      <motion.div
        className="tbpick card"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      >
        <header className="tbpick__head">
          <div>
            <h2 className="tbpick__title">Audit mode</h2>
            <p className="tbpick__sub">
              Pick one trial balance, or two (current + prior) for a full comparison.
            </p>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>Cancel</button>
        </header>

        <div className="tbpick__body">
          {/* Only files uploaded in this session appear here — see the note in
              TrialBalanceView on why the stored-document catalog is not listed. */}
          <div className="tbpick__list">
            {(documents || []).length === 0 && (
              <p className="tbpick__empty">
                No trial balances uploaded yet — use “Upload trial balance” below.
              </p>
            )}
            {(documents || []).map((d) => {
              const role = d.doc_id === currentId ? 'CURRENT' : d.doc_id === priorId ? 'PRIOR' : '';
              return (
                <div
                  key={d.doc_id}
                  className={`tbpick__row ${role ? 'is-on' : ''}`}
                  onClick={() => onSelect(d.doc_id)}
                >
                  <div className="tbpick__row-main">
                    <span className="tbpick__name">{d.filename}</span>
                    <span className="tbpick__meta">
                      {(d.periods || []).join(', ') || '—'}
                      {d.sheet ? ` · ${d.sheet}` : ''}
                    </span>
                  </div>
                  {role && <span className="tbpick__role">{role}</span>}
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={busyId === d.doc_id}
                    onClick={(e) => { e.stopPropagation(); remove(d.doc_id); }}
                    title="Delete this trial balance"
                  >
                    {busyId === d.doc_id ? '…' : 'Delete'}
                  </button>
                </div>
              );
            })}
          </div>

          <label className={`tbpick__upload ${uploading ? 'is-busy' : ''}`}>
            <Icon name={uploading ? 'refresh' : 'upload'} size={14} />
            {uploading ? 'Uploading…' : 'Upload trial balance'}
            <input
              type="file"
              multiple
              accept=".xlsx,.xls"
              disabled={uploading}
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                e.target.value = '';
                onUpload(files);
              }}
            />
          </label>

          {error && <Notice tone="error" title="Could not delete">{error}</Notice>}

          <div className="tbpick__options">
            <GroupingUpload
              mode={mode}
              doc={current}
              priorDoc={prior}
              grouping={grouping}
              onChange={onGroupingChange}
              onNeedsMapping={onGroupingNeedsMapping}
            />
            <PdfEvidence mode={mode} selectedIds={pdfIds} onChange={onPdfIdsChange} />
          </div>
        </div>

        <footer className="tbpick__footer">
          <span className="tbpick__summary">
            {current
              ? `${current.filename}${prior ? ` vs ${prior.filename}` : ''}`
              : 'Select a trial balance'}
          </span>
          <button type="button" className="btn btn--primary" onClick={onRun} disabled={!currentId}>
            <Icon name="shield" size={15} />
            Run audit
          </button>
        </footer>
      </motion.div>
    </div>
  );
}
