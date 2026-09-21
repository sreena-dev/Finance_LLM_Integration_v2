import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { tbDeleteDocument, tbListDocuments, tbSuggestPriorityCompanies } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import CompanyDetailsFields from './CompanyDetailsFields';
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
  // Query-analysis staging: same Upload new / Existing (Database) tabs as the
  // other two modes. A fresh upload runs the same classify/quality-gate chain
  // but writes nothing to LIVE; a database pick is already durable, so
  // Proceed just confirms it -- either way, for the not-yet-built query
  // feature, never for a full audit.
  query: {
    title: 'Upload for Query Analysis',
    sub: 'Pick one trial balance — from the database, or a fresh upload — to prepare it for query analysis.',
    runLabel: 'Proceed',
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
 * Per-slot ingestion outcome, shown inline in the picker — this is the only
 * place any upload/parse/normalize/ingest message is ever displayed. `SUCCESS`
 * renders nothing (the slot already flips to showing the resolved document),
 * `WARNING` is a pass with a note, `CONFIRMATION_REQUIRED` offers an explicit
 * opt-in to proceed anyway (ingest_tb_to_live's own documented re-entry
 * point), and `FAILED`/a thrown network error requires re-staging.
 */
function IngestStatus({ slotState, onProceedAnyway }) {
  if (!slotState || slotState.status === 'running' || slotState.status === 'success') return null;
  if (slotState.status === 'warning') {
    return <Notice tone="warn" title="Ingested with warnings">{slotState.message}</Notice>;
  }
  if (slotState.status === 'confirmation_required') {
    return (
      <Notice
        tone="warn"
        title="Needs confirmation before ingesting"
        action={
          <button type="button" className="btn btn--primary btn--sm" onClick={onProceedAnyway}>
            Proceed anyway
          </button>
        }
      >
        {slotState.message}
      </Notice>
    );
  }
  return <Notice tone="error" title="Could not ingest this file">{slotState.message}</Notice>;
}

/**
 * A single upload slot — used for the single-mode TB input and the two
 * explicit Current Year (CY) / Prior Year (PY) inputs in comparison mode.
 * Three states: an already-ingested document (with a "Change" button that
 * clears the slot back to unset), a staged-but-not-yet-run file (with a
 * "Remove" button, no backend call happens until "Run analysis"), or an
 * empty upload button. `slotState` (set only while/after "Run analysis" is
 * in flight) drives the running spinner and any surfaced error/warning.
 */
function UploadSlot({ label, doc, staged, slotState, onStage, onRemoveStaged, onClear, onProceedAnyway }) {
  const running = slotState?.status === 'running';
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
      ) : staged ? (
        <div className="tbpick__row is-on">
          <div className="tbpick__row-main">
            <span className="tbpick__name">{staged.name}</span>
            <span className="tbpick__meta">{running ? 'Running…' : 'Staged — ready to run'}</span>
          </div>
          {!running && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onRemoveStaged}>Remove</button>
          )}
        </div>
      ) : (
        <label className="tbpick__upload">
          <Icon name="upload" size={14} />
          {`Upload ${label} file`}
          <input
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) onStage(file);
            }}
          />
        </label>
      )}

      <IngestStatus slotState={slotState} onProceedAnyway={onProceedAnyway} />
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
 * dropdowns rather than a flat list). A database pick is already ingested —
 * it resolves to a real doc_id immediately, no Run step needed for it. An
 * uploaded file only ever gets *staged* here (`onStage*`); nothing reaches
 * the backend until "Run analysis"/"Run comparison" is clicked — that single
 * action runs upload -> structural check -> quality checks -> normalization
 * -> canonical Parquet -> LIVE ingestion for every staged slot, with every
 * message from that sequence surfaced right here via `IngestStatus`/
 * `GroupingUpload`, never in the chat window. The picker only closes once
 * every required slot has actually resolved to a real ingested document.
 */
export default function TbRunPicker({
  mode,
  pickerMode,
  documents,
  running,
  staged,
  runState,
  onStage,
  onRemoveStaged,
  onStageGrouping,
  onRemoveGroupingStaged,
  onProceedAnyway,
  onClearSlot,
  currentId,
  priorId,
  onSelect,
  onPickDbDoc,
  onPickDbDocForSlot,
  onDeleted,
  groupingResult,
  onRun,
  onProceed,
  onCancel,
  companyDetails,
  onCompanyDetailsChange,
  queryDbDocId,
  onPickQueryDbDoc,
}) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('upload'); // 'upload' | 'database'
  const [dbDocs, setDbDocs] = useState(null);
  const [dbLoading, setDbLoading] = useState(false);
  const [dbError, setDbError] = useState(null);
  const [priorityCompanies, setPriorityCompanies] = useState(null);

  // Fetched once per picker open, shared across every slot's Company Name
  // suggestions — small reference list (order-of-hundreds rows), needed
  // immediately since "upload" is the default active tab (unlike the
  // database tab's own lazy-on-open fetch below).
  useEffect(() => {
    let cancelled = false;
    tbSuggestPriorityCompanies(mode)
      .then((res) => { if (!cancelled) setPriorityCompanies(res.companies || []); })
      .catch(() => { if (!cancelled) setPriorityCompanies([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const copy = MODE_COPY[pickerMode] || MODE_COPY.single;
  const current = (documents || []).find((d) => d.doc_id === currentId) || null;
  const prior = (documents || []).find((d) => d.doc_id === priorId) || null;
  const singleReady = Boolean(currentId) || Boolean(staged?.single);
  const currentReady = Boolean(currentId) || Boolean(staged?.current);
  const priorReady = Boolean(priorId) || Boolean(staged?.prior);
  const queryReady = Boolean(staged?.query) || Boolean(queryDbDocId);
  const canRun = (
    pickerMode === 'comparison' ? currentReady && priorReady
      : pickerMode === 'query' ? queryReady
        : singleReady
  ) && !running;

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

          {activeTab === 'upload' && pickerMode === 'query' && (
            <>
              <UploadSlot
                label="trial balance"
                doc={null}
                staged={staged?.query}
                slotState={runState?.slots?.query}
                onStage={(file) => onStage('query', file)}
                onRemoveStaged={() => onRemoveStaged('query')}
                onClear={() => onClearSlot('query')}
                onProceedAnyway={() => onProceedAnyway('query')}
              />
              <CompanyDetailsFields
                slot="query"
                value={companyDetails?.query}
                onChange={onCompanyDetailsChange}
                companies={priorityCompanies}
              />
            </>
          )}

          {activeTab === 'upload' && pickerMode === 'single' && (
            <>
              <div className="tbpick__list">
                {(documents || []).filter((d) => d.source !== 'database' && d.doc_id !== currentId).length === 0 && !current && (
                  <p className="tbpick__empty">
                    No trial balances uploaded yet — use “Upload trial balance” below.
                  </p>
                )}
                {(documents || [])
                  .filter((d) => d.source !== 'database' && d.doc_id !== currentId)
                  .map((d) => (
                    <DocRow
                      key={d.doc_id}
                      doc={d}
                      role=""
                      busy={busyId === d.doc_id}
                      onClick={() => onSelect(d.doc_id)}
                      onDelete={() => remove(d.doc_id)}
                    />
                  ))}
              </div>

              <UploadSlot
                label="trial balance"
                doc={current}
                staged={staged?.single}
                slotState={runState?.slots?.single}
                onStage={(file) => onStage('single', file)}
                onRemoveStaged={() => onRemoveStaged('single')}
                onClear={() => onClearSlot('single')}
                onProceedAnyway={() => onProceedAnyway('single')}
              />
              <CompanyDetailsFields
                slot="single"
                value={companyDetails?.single}
                onChange={onCompanyDetailsChange}
                companies={priorityCompanies}
              />

              {error && <Notice tone="error" title="Could not delete">{error}</Notice>}
            </>
          )}

          {activeTab === 'upload' && pickerMode === 'comparison' && (
            <div className="tbpick__slots">
              <div className="tbpick__slot-group">
                <UploadSlot
                  label="Current Year (CY)"
                  doc={current}
                  staged={staged?.current}
                  slotState={runState?.slots?.current}
                  onStage={(file) => onStage('current', file)}
                  onRemoveStaged={() => onRemoveStaged('current')}
                  onClear={() => onClearSlot('current')}
                  onProceedAnyway={() => onProceedAnyway('current')}
                />
                <CompanyDetailsFields
                  slot="current"
                  value={companyDetails?.current}
                  onChange={onCompanyDetailsChange}
                  companies={priorityCompanies}
                />
              </div>
              <div className="tbpick__slot-group">
                <UploadSlot
                  label="Prior Year (PY)"
                  doc={prior}
                  staged={staged?.prior}
                  slotState={runState?.slots?.prior}
                  onStage={(file) => onStage('prior', file)}
                  onRemoveStaged={() => onRemoveStaged('prior')}
                  onClear={() => onClearSlot('prior')}
                  onProceedAnyway={() => onProceedAnyway('prior')}
                />
                <CompanyDetailsFields
                  slot="prior"
                  value={companyDetails?.prior}
                  onChange={onCompanyDetailsChange}
                  companies={priorityCompanies}
                />
              </div>
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

          {activeTab === 'database' && pickerMode === 'query' && (
            <DbCompanyPeriodPicker
              dbDocs={dbDocs}
              dbLoading={dbLoading}
              dbError={dbError}
              selectedId={queryDbDocId}
              onPick={(row) => onPickQueryDbDoc(normalizeDbDoc(row))}
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

          {activeTab === 'upload' && (
            <div className="tbpick__options">
              <GroupingUpload
                staged={staged?.grouping}
                result={groupingResult}
                slotState={runState?.slots?.grouping}
                onStage={onStageGrouping}
                onRemove={onRemoveGroupingStaged}
              />
            </div>
          )}
        </div>

        <footer className="tbpick__footer">
          <span className="tbpick__summary">
            {pickerMode === 'query'
              ? (queryDbDocId
                  ? (dbDocs || []).find((r) => r.tb_doc_id === queryDbDocId)?.tb_doc_name || 'Ready to proceed'
                  : staged?.query ? 'Ready to proceed' : 'Select a trial balance')
              : current
                ? `${current.filename}${prior ? ` vs ${prior.filename}` : ''}`
                : staged?.single || staged?.current || staged?.prior
                  ? 'Ready to run'
                  : pickerMode === 'comparison' ? 'Select a CY and a PY trial balance' : 'Select a trial balance'}
          </span>
          <button
            type="button"
            className="btn btn--primary"
            onClick={pickerMode === 'query' ? onProceed : onRun}
            disabled={!canRun}
          >
            <Icon name={running ? 'refresh' : (pickerMode === 'comparison' ? 'layers' : 'doc')} size={15} />
            {running ? 'Running…' : copy.runLabel}
          </button>
        </footer>
      </motion.div>
    </div>
  );
}
