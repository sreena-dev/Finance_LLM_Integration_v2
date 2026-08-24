import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { tbDeleteDocument, tbListDocuments } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import GroupingUpload from './GroupingUpload';
import './TbRunPicker.css';

const MODE_COPY = {
  single: {
    title: 'Single TB Analysis',
    sub: 'Pick one trial balance — from the database, or a fresh upload.',
    runLabel: 'Run analysis',
  },
  comparison: {
    title: 'Two TB Comparative Analysis',
    sub: 'Pick a Current Year (CY) and a Prior Year (PY) trial balance.',
    runLabel: 'Run comparison',
  },
};

/** A document row from the DB list, normalized into the same shape as an
 * uploaded document so it can flow through `documents`/`docOf`/`askTarget`
 * unchanged everywhere else. */
function normalizeDbDoc(row) {
  return {
    doc_id: row.tb_doc_id,
    filename: row.tb_doc_name || row.company_name || row.entity_name || row.tb_doc_id,
    sheet: null,
    periods: [row.financial_year].filter(Boolean),
    uploaded_at: row.fy_period_end || row.fy_period_start || null,
    source: 'database',
  };
}

function DocRow({ doc, role, busy, onClick, onDelete }) {
  return (
    <div className={`tbpick__row ${role ? 'is-on' : ''}`} onClick={onClick}>
      <div className="tbpick__row-main">
        <span className="tbpick__name">{doc.filename}</span>
        <span className="tbpick__meta">
          {(doc.periods || []).join(', ') || '—'}
          {doc.sheet ? ` · ${doc.sheet}` : ''}
        </span>
      </div>
      {role && <span className="tbpick__role">{role}</span>}
      {onDelete && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={busy}
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title="Delete this trial balance"
        >
          {busy ? '…' : 'Delete'}
        </button>
      )}
    </div>
  );
}

/**
 * A single upload slot, used for the two explicit Current Year (CY) / Prior
 * Year (PY) inputs in comparison mode. Shows the assigned document (with a
 * "Change" button that clears just this slot) or an upload button labeled
 * for the slot.
 */
function UploadSlot({ label, doc, uploading, onUpload, onClear }) {
  return (
    <div className="tbpick__slot">
      <span className="tbpick__slot-label">{label}</span>
      {doc ? (
        <div className="tbpick__row is-on">
          <div className="tbpick__row-main">
            <span className="tbpick__name">{doc.filename}</span>
            <span className="tbpick__meta">{(doc.periods || []).join(', ') || '—'}</span>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>Change</button>
        </div>
      ) : (
        <label className={`tbpick__upload ${uploading ? 'is-busy' : ''}`}>
          <Icon name={uploading ? 'refresh' : 'upload'} size={14} />
          {uploading ? 'Uploading…' : `Upload ${label} file`}
          <input
            type="file"
            accept=".xlsx,.xls"
            disabled={uploading}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) onUpload(file);
            }}
          />
        </label>
      )}
    </div>
  );
}

/**
 * Company + Period Year dropdown pair for picking a database-stored trial
 * balance — replaces a flat searchable list with two dependent selects, since
 * "which company, which year" is how an auditor actually thinks about it.
 * Selecting both automatically resolves and picks the matching document.
 */
function DbCompanyPeriodPicker({ dbDocs, dbLoading, dbError, selectedId, onPick }) {
  const [company, setCompany] = useState('');
  const [period, setPeriod] = useState('');

  const companies = Array.from(
    new Set((dbDocs || []).map((r) => r.company_name || r.entity_name).filter(Boolean))
  ).sort();
  const periods = Array.from(
    new Set(
      (dbDocs || [])
        .filter((r) => !company || (r.company_name || r.entity_name) === company)
        .map((r) => r.financial_year)
        .filter(Boolean)
    )
  ).sort().reverse();

  useEffect(() => { setPeriod(''); }, [company]);

  useEffect(() => {
    if (!company || !period) return;
    const match = (dbDocs || []).find(
      (r) => (r.company_name || r.entity_name) === company && r.financial_year === period
    );
    if (match) onPick(match);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company, period]);

  if (dbLoading) return <p className="tbpick__empty">Loading stored trial balances…</p>;
  if (dbError) return <Notice tone="error" title="Could not load">{dbError}</Notice>;

  const selectedDoc = selectedId ? (dbDocs || []).find((r) => r.tb_doc_id === selectedId) : null;

  return (
    <div className="tbpick__dbselect">
      <select
        className="tbpick__select"
        value={company}
        onChange={(e) => setCompany(e.target.value)}
      >
        <option value="">Choose company…</option>
        {companies.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <select
        className="tbpick__select"
        value={period}
        onChange={(e) => setPeriod(e.target.value)}
        disabled={!company}
      >
        <option value="">Choose period year…</option>
        {periods.map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      {selectedDoc && (
        <p className="tbpick__dbselect-picked">
          <Icon name="check" size={13} /> {selectedDoc.tb_doc_name || selectedDoc.tb_doc_id}
        </p>
      )}
    </div>
  );
}

/**
 * Launch dialog for a Single TB Analysis or Two TB Comparative Analysis run.
 *
 * `pickerMode` ('single' | 'comparison') controls the header copy and how a
 * document gets assigned: single mode always replaces CURRENT outright (see
 * `onSelect` in TrialBalanceView); comparison mode uses two explicit slots —
 * Current Year (CY) and Prior Year (PY) — for both sources below, rather than
 * a click-to-cycle gesture, since picking two specific periods benefits from
 * being unambiguous up front.
 *
 * Two source tabs supply files: "Upload new" and "Existing (Database)" (the
 * latter fetched lazily on first open, picked via Company + Period Year
 * dropdowns rather than a flat list).
 *
 * The optional inputs live here rather than beside the results because they are
 * inputs *to* a run — deciding them after seeing a report would invite re-reading
 * figures that were produced without them.
 */
export default function TbRunPicker({
  mode,
  pickerMode,
  documents,
  uploading,
  onUpload,
  onUploadForSlot,
  onClearSlot,
  currentId,
  priorId,
  onSelect,
  onPickDbDoc,
  onPickDbDocForSlot,
  onDeleted,
  grouping,
  onGroupingChange,
  onGroupingNeedsMapping,
  onRun,
  onCancel,
}) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('upload'); // 'upload' | 'database'
  const [dbDocs, setDbDocs] = useState(null);
  const [dbLoading, setDbLoading] = useState(false);
  const [dbError, setDbError] = useState(null);

  const copy = MODE_COPY[pickerMode] || MODE_COPY.single;
  const current = (documents || []).find((d) => d.doc_id === currentId) || null;
  const prior = (documents || []).find((d) => d.doc_id === priorId) || null;
  const canRun = pickerMode === 'comparison' ? Boolean(currentId && priorId) : Boolean(currentId);

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

  async function openDatabaseTab() {
    setActiveTab('database');
    if (dbDocs !== null || dbLoading) return;
    setDbLoading(true);
    setDbError(null);
    try {
      const res = await tbListDocuments(mode);
      setDbDocs(res.documents || []);
    } catch (err) {
      setDbError(err.message || 'Could not load stored trial balances.');
    } finally {
      setDbLoading(false);
    }
  }

  function selectDbRow(row) {
    onPickDbDoc(normalizeDbDoc(row));
    onSelect(row.tb_doc_id);
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
            <h2 className="tbpick__title">{copy.title}</h2>
            <p className="tbpick__sub">{copy.sub}</p>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>Cancel</button>
        </header>

        <div className="tbpick__body">
          <div className="tbpick__tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'upload'}
              className={`tbpick__tab ${activeTab === 'upload' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('upload')}
            >
              Upload new
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'database'}
              className={`tbpick__tab ${activeTab === 'database' ? 'is-active' : ''}`}
              onClick={openDatabaseTab}
            >
              Existing (Database)
            </button>
          </div>

          {activeTab === 'upload' && pickerMode === 'single' && (
            <>
              <div className="tbpick__list">
                {(documents || []).filter((d) => d.source !== 'database').length === 0 && (
                  <p className="tbpick__empty">
                    No trial balances uploaded yet — use “Upload trial balance” below.
                  </p>
                )}
                {(documents || [])
                  .filter((d) => d.source !== 'database')
                  .map((d) => (
                    <DocRow
                      key={d.doc_id}
                      doc={d}
                      role={d.doc_id === currentId ? 'CURRENT' : ''}
                      busy={busyId === d.doc_id}
                      onClick={() => onSelect(d.doc_id)}
                      onDelete={() => remove(d.doc_id)}
                    />
                  ))}
              </div>

              <label className={`tbpick__upload ${uploading ? 'is-busy' : ''}`}>
                <Icon name={uploading ? 'refresh' : 'upload'} size={14} />
                {uploading ? 'Uploading…' : 'Upload trial balance'}
                <input
                  type="file"
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
            </>
          )}

          {activeTab === 'upload' && pickerMode === 'comparison' && (
            <div className="tbpick__slots">
              <UploadSlot
                label="Current Year (CY)"
                doc={current}
                uploading={uploading}
                onUpload={(file) => onUploadForSlot(file, 'current')}
                onClear={() => onClearSlot('current')}
              />
              <UploadSlot
                label="Prior Year (PY)"
                doc={prior}
                uploading={uploading}
                onUpload={(file) => onUploadForSlot(file, 'prior')}
                onClear={() => onClearSlot('prior')}
              />
            </div>
          )}

          {activeTab === 'database' && pickerMode === 'single' && (
            <DbCompanyPeriodPicker
              dbDocs={dbDocs}
              dbLoading={dbLoading}
              dbError={dbError}
              selectedId={currentId}
              onPick={selectDbRow}
            />
          )}

          {activeTab === 'database' && pickerMode === 'comparison' && (
            <div className="tbpick__slots">
              <div className="tbpick__slot">
                <span className="tbpick__slot-label">Current Year (CY)</span>
                <DbCompanyPeriodPicker
                  dbDocs={dbDocs}
                  dbLoading={dbLoading}
                  dbError={dbError}
                  selectedId={currentId}
                  onPick={(row) => onPickDbDocForSlot(normalizeDbDoc(row), 'current')}
                />
              </div>
              <div className="tbpick__slot">
                <span className="tbpick__slot-label">Prior Year (PY)</span>
                <DbCompanyPeriodPicker
                  dbDocs={dbDocs}
                  dbLoading={dbLoading}
                  dbError={dbError}
                  selectedId={priorId}
                  onPick={(row) => onPickDbDocForSlot(normalizeDbDoc(row), 'prior')}
                />
              </div>
            </div>
          )}

          <div className="tbpick__options">
            <GroupingUpload
              mode={mode}
              doc={current}
              priorDoc={prior}
              grouping={grouping}
              onChange={onGroupingChange}
              onNeedsMapping={onGroupingNeedsMapping}
            />
          </div>
        </div>

        <footer className="tbpick__footer">
          <span className="tbpick__summary">
            {current
              ? `${current.filename}${prior ? ` vs ${prior.filename}` : ''}`
              : pickerMode === 'comparison' ? 'Select a CY and a PY trial balance' : 'Select a trial balance'}
          </span>
          <button type="button" className="btn btn--primary" onClick={onRun} disabled={!canRun}>
            <Icon name={pickerMode === 'comparison' ? 'layers' : 'doc'} size={15} />
            {copy.runLabel}
          </button>
        </footer>
      </motion.div>
    </div>
  );
}
