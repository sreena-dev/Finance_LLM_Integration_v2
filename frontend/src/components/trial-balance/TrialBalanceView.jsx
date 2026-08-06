import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  tbAsk, tbAskGeneral, tbAudit, tbUpload, tbUploadPdf, tbValidate,
} from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import AuditCard from './AuditCard';
import ColumnMapper from './ColumnMapper';
import GroupingMapper from './GroupingMapper';
import TbRunPicker from './TbRunPicker';
import './TrialBalanceView.css';

const AUDIT_STAGES = [
  'Mapping accounts', 'Screening', 'Analysing relationships',
  'Reading supporting documents', 'Mapping standards & rule docs', 'Drafting findings',
];
const ASK_STAGES = ['Reading the trial balance', 'Running tools', 'Composing the answer'];
const GENERAL_STAGES = ['Checking', 'Planning', 'Searching corpora', 'Composing answer'];

let _seq = 0;
const nextId = () => `m${++_seq}`;

/**
 * Trial Balance mode — one continuous stream rather than tabbed panels.
 *
 * Everything a run produces stays in the stream, so two audits — a single period
 * and a comparison, or one with a client grouping and one without — can be read
 * against each other instead of replacing one another. That is the whole reason
 * for the append-only model: these are documents to compare, not views to switch
 * between.
 *
 * A run is launched from the quick-action card, which opens the picker. Free-text
 * questions need no file at all: with a trial balance loaded they are answered
 * against it, and without one they go to the corpus pipeline (Ind AS, annual
 * reports, reference material) — see send().
 *
 * `documents` holds only what was uploaded here, and is deliberately NOT seeded
 * from `GET /documents`. That endpoint returns every trial balance ever stored on
 * a shared database — other companies, other engagements, other people's files —
 * so listing it made the picker a directory of unrelated data that happened to
 * contain your file. Uploads are content-addressed, so re-uploading the same
 * workbook returns the same `doc_id` and costs nothing.
 */
export default function TrialBalanceView({ mode, state, setState }) {
  const { documents, currentId, priorId, messages, pdfIds } = state;

  const [pendingUpload, setPendingUpload] = useState(null);
  const [pendingGrouping, setPendingGrouping] = useState(null);
  const [grouping, setGrouping] = useState(null);
  const [picker, setPicker] = useState(null);      // 'audit' | null
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);
  const endRef = useRef(null);
  const sessionId = useRef(
    (crypto.randomUUID && crypto.randomUUID()) || `sess-${Date.now()}-${Math.random()}`
  ).current;

  const patch = (fields) => setState((prev) => ({ ...prev, ...fields }));
  const push = (msg) => {
    const m = { id: nextId(), ...msg };
    setState((prev) => ({ ...prev, messages: [...(prev.messages || []), m] }));
    return m.id;
  };
  const replace = (id, msg) =>
    setState((prev) => ({
      ...prev,
      messages: (prev.messages || []).map((m) => (m.id === id ? { ...m, ...msg } : m)),
    }));
  const drop = (id) =>
    setState((prev) => ({ ...prev, messages: (prev.messages || []).filter((m) => m.id !== id) }));

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages?.length, busy]);

  // A grouping's coverage is measured against specific documents, so it cannot
  // survive a change of selection — carrying it over would misreport coverage
  // and could classify the wrong accounts.
  useEffect(() => { setGrouping(null); setPendingGrouping(null); }, [currentId, priorId]);

  const docOf = (id) => (documents || []).find((d) => d.doc_id === id) || null;
  const currentDoc = docOf(currentId);
  const priorDoc = docOf(priorId);
  // What `ask` will actually run against: the selection if there is one, else the
  // newest upload. Kept in one place so the placeholder cannot promise a
  // different file from the one send() uses.
  const askTarget = currentDoc || (documents || [])[0] || null;

  function addDocument(info) {
    setState((prev) => {
      const row = {
        doc_id: info.doc_id, filename: info.filename, sheet: info.sheet,
        periods: info.periods, uploaded_at: new Date().toISOString(),
      };
      // Fill the first free slot rather than always claiming CURRENT, so
      // uploading two files back to back for a comparison lands one in each
      // instead of the second silently replacing the first. Re-uploading a file
      // that already holds a slot keeps it (uploads are content-addressed, so
      // the same workbook returns the same doc_id).
      const held = prev.currentId === info.doc_id || prev.priorId === info.doc_id;
      const slot = held ? {}
        : !prev.currentId ? { currentId: info.doc_id }
        : !prev.priorId ? { priorId: info.doc_id }
        : { currentId: info.doc_id };
      return {
        ...prev,
        documents: [row, ...(prev.documents || []).filter((d) => d.doc_id !== info.doc_id)],
        ...slot,
      };
    });
    setPendingUpload(null);
  }

  /**
   * The single upload path, shared by the composer's attach button and the run
   * picker's own button, so a file lands in exactly the same place either way.
   * Kind is routed by extension, as in the source UI.
   */
  async function uploadFiles(files) {
    if (!files.length) return;
    setUploading(true);
    for (const file of files) {
      const isPdf = /\.pdf$/i.test(file.name);
      try {
        if (isPdf) {
          const info = await tbUploadPdf(mode, file);
          if (!pdfIds.includes(info.doc_id)) patch({ pdfIds: [...pdfIds, info.doc_id] });
          push({ kind: 'note', text: `Indexed "${info.filename}" — ${info.pages} pages, selected as audit evidence.` });
        } else {
          const info = await tbUpload(mode, file);
          if (info.needsMapping) {
            setPendingUpload({ ...info, filename: file.name });
          } else {
            addDocument({ ...info, filename: info.filename || file.name });
            push({ kind: 'note', text: `Loaded "${info.filename}" — ${info.accounts} accounts · ${(info.periods || []).join(', ')}.` });
          }
        }
      } catch (err) {
        push({ kind: 'error', text: err.message || `Could not upload "${file.name}".` });
      }
    }
    setUploading(false);
  }

  async function onFiles(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    await uploadFiles(files);
  }

  async function runAudit(docId, priorDocId) {
    const doc = docOf(docId);
    const prior = docOf(priorDocId);
    setPicker(null);
    push({
      kind: 'user',
      text: `Audit mode — ${doc?.filename}${prior ? ` vs ${prior.filename} (two periods)` : ''}`
        + `${pdfIds.length ? ` + ${pdfIds.length} supporting PDF(s)` : ''}`
        + `${grouping ? ' + chart-of-accounts grouping' : ''}`,
    });
    const loadingId = push({ kind: 'loading', stages: AUDIT_STAGES });
    setBusy(true);
    try {
      const result = await tbAudit(mode, {
        docId, docIdPrior: priorDocId,
        groupingToken: grouping?.grouping_token,
        uploadDocIds: pdfIds,
      });

      // The per-ledger arithmetic/variance table comes from the deterministic
      // validation engine — /audit does not emit one. Composed here so a failure
      // costs only that section; the report itself is already in hand.
      let fullTb = null;
      try {
        const v = await tbValidate(mode, {
          docId, docIdPrior: priorDocId, variance_materiality_pct: 5.0,
        });
        const halted = v.phase_reached === 'HALTED';
        const l1 = v.layer1_results || {};
        fullTb = {
          rows: v.full_table_rows || [],
          halted,
          mode: priorDocId ? 'comparison' : 'single',
          haltReasons: halted
            ? [...(l1.single || []), ...(l1.py || []), ...(l1.cy || []), ...(l1.cross_year_results || [])]
                .filter((x) => x.status === 'HALT')
            : [],
        };
      } catch { fullTb = null; }

      drop(loadingId);
      push({ kind: 'audit', result, fullTb, doc, priorDoc: prior, pdfIds: [...pdfIds] });
    } catch (err) {
      replace(loadingId, { kind: 'error', text: err.message || 'Audit mode failed.' });
    } finally {
      setBusy(false);
    }
  }

  /**
   * A question needs no trial balance.
   *
   * With one loaded, the question is answered against that file, where every
   * figure is computed from it. With none, it goes to the corpus pipeline, which
   * answers from Ind AS / annual reports / reference material. Both are real
   * answers — the earlier behaviour of refusing to ask anything until a workbook
   * was uploaded made a research tool feel like a spreadsheet importer.
   *
   * `askTarget` also covers the case where a usable file is loaded but nothing is
   * selected (dismissed, or a second upload took the PRIOR slot): adopt it rather
   * than silently answering from the corpora, since a question typed while a
   * trial balance is on screen is almost certainly about it.
   */
  async function send() {
    const text = input.trim();
    if (!text || busy) return;

    const target = askTarget;
    if (target && !currentDoc) patch({ currentId: target.doc_id });

    setInput('');
    push({ kind: 'user', text });
    const loadingId = push({
      kind: 'loading',
      stages: target ? ASK_STAGES : GENERAL_STAGES,
    });
    setBusy(true);
    try {
      const result = target
        ? await tbAsk(mode, { docId: target.doc_id, question: text, sessionId })
        : await tbAskGeneral(mode, { question: text, sessionId, uploadDocIds: pdfIds });
      drop(loadingId);
      push({ kind: 'answer', result, scope: target ? 'trial-balance' : 'corpora' });
    } catch (err) {
      replace(loadingId, {
        kind: 'error',
        text: err.message
          || (target ? 'Unable to reach the trial-balance service.'
                     : 'Unable to reach the research service.'),
      });
    } finally {
      setBusy(false);
    }
  }

  // Audit is the only launchable run. The deterministic validation engine is
  // still used on every audit — it produces the Full trial balance table inside
  // the audit card (see runAudit) — it just has no separate entry point.
  const quickActions = [
    {
      id: 'audit', title: 'Audit mode', icon: 'shield',
      desc: 'Mapping, screen, relationships, findings — 1 file, or 2 for a full comparison',
      duration: '~1–3 min',
    },
  ];

  return (
    <div className="tb">
      {currentDoc && (
        <div className="tb__active">
          <span className="tb__active-text">
            Asking about: <strong>{currentDoc.filename}</strong>
            {priorDoc && <> · prior: <strong>{priorDoc.filename}</strong></>}
          </span>
          <button
            type="button"
            className="tb__dismiss"
            onClick={() => patch({ currentId: null, priorId: null })}
          >
            Dismiss ✕
          </button>
        </div>
      )}

      <div className="tb__actions">
        {quickActions.map((qa) => (
          <button
            key={qa.id}
            type="button"
            className="tb__action"
            onClick={() => setPicker(qa.id)}
            disabled={busy || uploading}
          >
            <span className="tb__action-title">
              <Icon name={qa.icon} size={14} />
              {qa.title}
            </span>
            <span className="tb__action-desc">{qa.desc}</span>
            <span className="tb__action-time">{uploading ? 'waiting for uploads…' : qa.duration}</span>
          </button>
        ))}
      </div>

      <div className="tb__stream">
        <div className="tb__stream-inner">
          {(messages || []).length === 0 && (
            <div className="tb__idle">
              <Icon name="ledger" size={26} className="tb__idle-icon" />
              <p>
                Ask about Ind AS, a company or an annual report straight away — no file
                needed. Upload an Excel trial balance to ask about that file instead, or
                to run an audit over it. Results stay on this page so you can compare them.
              </p>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
              >
                <Icon name="upload" size={15} />
                {uploading ? 'Uploading…' : 'Upload trial balance'}
              </button>
            </div>
          )}

          {(messages || []).map((m) => {
            if (m.kind === 'user') {
              return <div key={m.id} className="tb__user">{m.text}</div>;
            }
            if (m.kind === 'note') {
              return <p key={m.id} className="tb__note">{m.text}</p>;
            }
            if (m.kind === 'error') {
              return (
                <Notice key={m.id} tone="error" title="Failed">{m.text}</Notice>
              );
            }
            if (m.kind === 'loading') {
              return <LoadingRow key={m.id} stages={m.stages} />;
            }
            if (m.kind === 'answer') {
              return (
                <article key={m.id} className="card tb__answer">
                  <div className="tb__answer-head">
                    <span className="pill pill--mute">
                      {m.scope === 'corpora' ? 'Ind AS & annual reports' : 'This trial balance'}
                    </span>
                    {m.result.guardrail && (
                      <span className="pill pill--warn">{m.result.guardrail.replace(/_/g, ' ')}</span>
                    )}
                  </div>
                  <Markdown>{m.result.answer || ''}</Markdown>
                  {m.result.computed && (
                    <pre className="tb__computed">{JSON.stringify(m.result.computed, null, 2)}</pre>
                  )}
                  {m.result.sources?.length > 0 && (
                    <div className="tb__sources">
                      {m.result.sources.map((s, i) => (
                        <span key={i} className="pill pill--mute">{s.label || s.source}</span>
                      ))}
                    </div>
                  )}
                </article>
              );
            }
            if (m.kind === 'audit') return <AuditCard key={m.id} mode={mode} msg={m} />;
            return null;
          })}
          <div ref={endRef} />
        </div>
      </div>

      <div className="tb__composer">
        <button
          type="button"
          className="tb__attach"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="Attach a trial balance (.xlsx/.xls) or a supporting PDF"
        >
          <Icon name={uploading ? 'refresh' : 'upload'} size={16} />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".xlsx,.xls,.pdf"
          className="tb__file"
          onChange={onFiles}
        />
        {/* Disabled with no trial balance loaded, so the state is visible before
            typing rather than surfacing as an error after submitting. */}
        <textarea
          className="tb__input"
          rows={1}
          value={input}
          placeholder={askTarget
            ? `Ask a question about ${askTarget.filename}…`
            : 'Ask about Ind AS, a company or an annual report…'}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <button
          type="button"
          className="btn btn--primary"
          onClick={send}
          disabled={busy || !input.trim()}
        >
          <Icon name="sparkle" size={15} />
          Ask
        </button>
      </div>

      {picker && (
        <TbRunPicker
          mode={mode}
          documents={documents}
          uploading={uploading}
          onUpload={uploadFiles}
          currentId={currentId}
          priorId={priorId}
          onSelect={(id) => {
            // current → prior → unselected, so one gesture covers both shapes.
            if (id === currentId) patch({ currentId: null });
            else if (id === priorId) patch({ priorId: null });
            else if (!currentId) patch({ currentId: id });
            else patch({ priorId: id });
          }}
          onDeleted={(docId) => patch({
            documents: (documents || []).filter((d) => d.doc_id !== docId),
            currentId: currentId === docId ? null : currentId,
            priorId: priorId === docId ? null : priorId,
          })}
          grouping={grouping}
          onGroupingChange={setGrouping}
          onGroupingNeedsMapping={setPendingGrouping}
          pdfIds={pdfIds}
          onPdfIdsChange={(ids) => patch({ pdfIds: ids })}
          onRun={() => runAudit(currentId, priorId)}
          onCancel={() => setPicker(null)}
        />
      )}

      {pendingUpload && (
        <ColumnMapper
          mode={mode}
          pending={pendingUpload}
          onDone={(info) => addDocument(info)}
          onCancel={() => setPendingUpload(null)}
        />
      )}

      {pendingGrouping && (
        <GroupingMapper
          mode={mode}
          pending={pendingGrouping}
          doc={currentDoc}
          priorDoc={priorDoc}
          onDone={(info) => { setGrouping(info); setPendingGrouping(null); }}
          onCancel={() => setPendingGrouping(null)}
        />
      )}
    </div>
  );
}

/** Rotating stage label — the runs are long enough that a bare spinner reads as a hang. */
function LoadingRow({ stages }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((v) => Math.min(v + 1, stages.length - 1)), 7000);
    return () => clearInterval(t);
  }, [stages.length]);
  return (
    <div className="tb__loading">
      <span className="tb__spinner" />
      <motion.span key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="tb__loading-text">
        {stages[i]}…
      </motion.span>
    </div>
  );
}
