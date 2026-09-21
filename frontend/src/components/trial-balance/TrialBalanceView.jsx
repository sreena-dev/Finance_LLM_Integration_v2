import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  tbAsk, tbAskGeneral, tbAudit, tbUpload, tbValidate,
} from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import AuditCard from './AuditCard';
import ColumnMapper from './ColumnMapper';
import GroupingMapper from './GroupingMapper';
import TbRunPicker from './TbRunPicker';
import './TrialBalanceView.css';

const SINGLE_AUDIT_STAGES = [
  'Validating trial balance', 'Computing materiality & FSLI summary',
  'Analysing relationships & risk indicators', 'Screening sensitive & anomalous accounts',
  'Consolidating exceptions', 'Drafting findings',
];
const COMPARISON_AUDIT_STAGES = [
  'Validating both trial balances', 'Comparing structure (new/removed accounts)',
  'Analysing variances', 'Checking sign conventions', 'Drafting comparison findings',
];
const ASK_STAGES = ['Reading the trial balance', 'Running tools', 'Composing the answer'];
const GENERAL_STAGES = ['Checking', 'Planning', 'Searching corpora', 'Composing answer'];

let _seq = 0;
const nextId = () => `m${++_seq}`;

// Small-talk greetings answer instantly from the client — no reason to spend a
// tool-calling turn (or a network round trip) on "hi". Matched as a whole
// message (with light punctuation tolerance) so this never intercepts a real
// question that merely starts with "hello" mid-sentence.
const GREETING_RULES = [
  { re: /^(hi+|hello+|hey+|yo|greetings)$/i, reply: 'Hi! How can I help you?' },
  { re: /^good\s*morning$/i, reply: 'Good morning! How can I help you?' },
  { re: /^good\s*afternoon$/i, reply: 'Good afternoon! How can I help you?' },
  { re: /^good\s*evening$/i, reply: 'Good evening! How can I help you?' },
  { re: /^good\s*night$/i, reply: 'Good night! Let me know if you need anything before you go.' },
  { re: /^(how are you\??|how'?s it going\??)$/i, reply: "I'm doing well, thanks for asking! How can I help you?" },
  { re: /^(thanks|thank you|thx|ty)!?$/i, reply: "You're welcome! Anything else I can help with?" },
  { re: /^(bye|goodbye|see ya|see you)!?$/i, reply: 'Goodbye! Come back anytime you have a question.' },
];

function matchGreeting(text) {
  const normalized = text.trim().replace(/[!.\s]+$/, '');
  const rule = GREETING_RULES.find(({ re }) => re.test(normalized));
  return rule ? rule.reply : null;
}

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
 * `documents` starts as whatever was uploaded here, but also picks up any
 * document the run picker's "Existing (Database)" tab adds — selecting a
 * DB-ingested trial balance normalizes it into the same row shape as an
 * upload, so this state stays the single source of truth for `docOf`,
 * `askTarget`, and the active-selection banner either way. Uploads are
 * content-addressed, so re-uploading the same workbook returns the same
 * `doc_id` and costs nothing.
 */
export default function TrialBalanceView({ mode, state, setState }) {
  const { documents, currentId, priorId, messages, pdfIds, chatQueryMode } = state;

  const [pendingUpload, setPendingUpload] = useState(null);
  const [pendingGrouping, setPendingGrouping] = useState(null);
  const [grouping, setGrouping] = useState(null);
  const [picker, setPicker] = useState(null);      // 'single' | 'comparison' | null
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
   * Merges a document picked from the "Existing (Database)" tab into
   * `documents` (deduped by doc_id, same as an upload) without touching the
   * upload-only bookkeeping `addDocument` does (no `setPendingUpload` reset,
   * no slot auto-assignment — the picker calls `onSelect` right after this to
   * place it, the same as it would for an uploaded row).
   */
  function mergeDbDocument(row) {
    setState((prev) => ({
      ...prev,
      documents: (prev.documents || []).some((d) => d.doc_id === row.doc_id)
        ? prev.documents
        : [row, ...(prev.documents || [])],
    }));
  }

  /**
   * Places a document into an explicit slot ('current' | 'prior'), used by
   * comparison mode's Current Year (CY) / Prior Year (PY) upload slots and
   * database pickers — unlike `addDocument`'s "first free slot" heuristic,
   * the caller here already knows exactly which period this document is.
   */
  function placeDocumentInSlot(info, slot) {
    setState((prev) => {
      const row = {
        doc_id: info.doc_id, filename: info.filename, sheet: info.sheet,
        periods: info.periods, uploaded_at: new Date().toISOString(),
      };
      return {
        ...prev,
        documents: [row, ...(prev.documents || []).filter((d) => d.doc_id !== info.doc_id)],
        [slot === 'prior' ? 'priorId' : 'currentId']: info.doc_id,
      };
    });
    setPendingUpload(null);
  }

  function pickDbDocForSlot(normalizedRow, slot) {
    mergeDbDocument(normalizedRow);
    patch({ [slot === 'prior' ? 'priorId' : 'currentId']: normalizedRow.doc_id });
  }

  async function uploadFileForSlot(file, slot) {
    if (!file) return;
    setUploading(true);
    try {
      const info = await tbUpload(mode, file);
      if (info.needsMapping) {
        setPendingUpload({ ...info, filename: file.name, targetSlot: slot });
      } else {
        placeDocumentInSlot({ ...info, filename: info.filename || file.name }, slot);
        push({ kind: 'note', text: `Loaded "${info.filename}" — ${info.accounts} accounts · ${(info.periods || []).join(', ')}.` });
      }
    } catch (err) {
      push({ kind: 'error', text: err.message || `Could not upload "${file.name}".` });
    } finally {
      setUploading(false);
    }
  }

  /**
   * The single upload path, shared by the composer's attach button and the run
   * picker's own button, so a file lands in exactly the same place either way.
   */
  async function uploadFiles(files) {
    if (!files.length) return;
    setUploading(true);
    for (const file of files) {
      try {
        const info = await tbUpload(mode, file);
        if (info.needsMapping) {
          setPendingUpload({ ...info, filename: file.name });
        } else {
          addDocument({ ...info, filename: info.filename || file.name });
          push({ kind: 'note', text: `Loaded "${info.filename}" — ${info.accounts} accounts · ${(info.periods || []).join(', ')}.` });
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
    const label = prior ? 'Two TB Comparative Analysis' : 'Single TB Analysis';
    setPicker(null);
    push({
      kind: 'user',
      text: `${label} — ${doc?.filename}${prior ? ` vs ${prior.filename} (two periods)` : ''}`
        + `${pdfIds.length ? ` + ${pdfIds.length} supporting PDF(s)` : ''}`
        + `${grouping ? ' + chart-of-accounts grouping' : ''}`,
    });
    const loadingId = push({ kind: 'loading', stages: prior ? COMPARISON_AUDIT_STAGES : SINGLE_AUDIT_STAGES });
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
      replace(loadingId, { kind: 'error', text: err.message || `${label} failed.` });
    } finally {
      setBusy(false);
    }
  }

  /**
   * Two explicit chat sub-modes, chosen via the toggle above the composer:
   *
   * 'db' — free-form questions, no file involved. Answered from whatever the
   * agent's own tools decide is relevant (already-ingested DB documents, Ind
   * AS / annual-report reference corpora, etc.) — always `tbAskGeneral`.
   *
   * 'upload' — scoped to a specific file uploaded this session. `askTarget`
   * covers the case where a usable file is loaded but nothing is explicitly
   * selected (dismissed, or a second upload took the PRIOR slot): adopt it
   * rather than asking again, since a question typed with a trial balance on
   * screen is almost certainly about it. With nothing uploaded yet, the user
   * is nudged to upload first rather than silently falling back to 'db'.
   */
  async function send() {
    const text = input.trim();
    if (!text || busy) return;

    setInput('');
    push({ kind: 'user', text });

    const greeting = matchGreeting(text);
    if (greeting) {
      push({ kind: 'greeting', text: greeting });
      return;
    }

    if (chatQueryMode === 'upload' && !askTarget) {
      push({
        kind: 'note',
        text: 'Upload a trial balance first (attach button below) — then ask your question about it.',
      });
      return;
    }

    const target = chatQueryMode === 'upload' ? askTarget : null;
    if (target && !currentDoc) patch({ currentId: target.doc_id });

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

  // Two explicit launchable runs, replacing the old single "Audit mode" whose
  // single-vs-comparison split was only implicit (however many slots got
  // filled). The deterministic validation engine still backs both — it
  // produces the Full trial balance table inside the audit card (see
  // runAudit) — it just has no separate entry point.
  const quickActions = [
    {
      id: 'single', title: 'Single TB Analysis', icon: 'doc',
      desc: 'Mapping, screening, relationships, findings — one trial balance.',
      duration: '~1–2 min',
    },
    {
      id: 'comparison', title: 'Two TB Comparative Analysis', icon: 'layers',
      desc: 'Everything Single TB Analysis does, plus period-over-period variance.',
      duration: '~2–3 min',
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

      <div className="tb__stream">
        <div className="tb__stream-inner">
          {(messages || []).length === 0 && (
            <div className="tb__idle">
              <Icon name="sparkle" size={26} className="tb__idle-icon" />
              <p className="tb__idle-welcome">Hi, how can I help you today?</p>
              <p>
                Database query for quick questions, Single TB Analysis for one file,
                or Two TB Comparative Analysis to compare two periods.
              </p>
            </div>
          )}

          {(messages || []).map((m) => {
            if (m.kind === 'user') {
              return <div key={m.id} className="tb__user">{m.text}</div>;
            }
            if (m.kind === 'note') {
              return <p key={m.id} className="tb__note">{m.text}</p>;
            }
            if (m.kind === 'greeting') {
              return (
                <div key={m.id} className="tb__assistant">
                  <Icon name="sparkle" size={14} className="tb__ai-mark" />
                  {m.text}
                </div>
              );
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
                    <Icon name="sparkle" size={14} className="tb__ai-mark" />
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

      <div className="tb__toprow">
        <div className="tb__chatmode" role="tablist" aria-label="Chat query mode">
          <button
            type="button"
            role="tab"
            aria-selected={chatQueryMode === 'db'}
            className={`tb__chatmode-tab ${chatQueryMode === 'db' ? 'is-active' : ''}`}
            onClick={() => patch({ chatQueryMode: 'db' })}
          >
            <Icon name="search" size={13} />
            Database query
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={chatQueryMode === 'upload'}
            className={`tb__chatmode-tab ${chatQueryMode === 'upload' ? 'is-active' : ''}`}
            onClick={() => patch({ chatQueryMode: 'upload' })}
          >
            <Icon name="upload" size={13} />
            Upload & analyze
          </button>
        </div>

        <div className="tb__actions">
          {quickActions.map((qa) => (
            <button
              key={qa.id}
              type="button"
              className="tb__action"
              title={`${qa.desc} (${qa.duration})`}
              onClick={() => {
                // Single mode never uses the PRIOR slot — clear a leftover
                // selection from an earlier comparison run so the picker
                // doesn't show a stale PRIOR badge.
                if (qa.id === 'single' && priorId) patch({ priorId: null });
                setPicker(qa.id);
              }}
              disabled={busy || uploading}
            >
              <Icon name={qa.icon} size={13} />
              {qa.title}
            </button>
          ))}
        </div>
      </div>

      <div className="tb__composer">
        <button
          type="button"
          className="tb__attach"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title="Attach a trial balance (.xlsx/.xls)"
        >
          <Icon name={uploading ? 'refresh' : 'upload'} size={16} />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".xlsx,.xls"
          className="tb__file"
          onChange={onFiles}
        />
        <textarea
          className="tb__input"
          rows={1}
          value={input}
          placeholder={chatQueryMode === 'upload'
            ? (askTarget ? `Ask a question about ${askTarget.filename}…` : 'Upload a trial balance, then ask about it…')
            : 'Ask a question about data already in the database — no file needed…'}
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
          pickerMode={picker}
          documents={documents}
          uploading={uploading}
          onUpload={uploadFiles}
          onUploadForSlot={(file, slot) => uploadFileForSlot(file, slot)}
          onClearSlot={(slot) => patch(slot === 'prior' ? { priorId: null } : { currentId: null })}
          currentId={currentId}
          priorId={priorId}
          // Single mode only — comparison mode's explicit CY/PY slots use
          // onUploadForSlot/onPickDbDocForSlot instead, so a doc is never
          // ambiguous about which period it belongs to.
          onSelect={(id) => patch({ currentId: id === currentId ? null : id, priorId: null })}
          onPickDbDoc={(row) => mergeDbDocument(row)}
          onPickDbDocForSlot={(row, slot) => pickDbDocForSlot(row, slot)}
          onDeleted={(docId) => patch({
            documents: (documents || []).filter((d) => d.doc_id !== docId),
            currentId: currentId === docId ? null : currentId,
            priorId: priorId === docId ? null : priorId,
          })}
          grouping={grouping}
          onGroupingChange={setGrouping}
          onGroupingNeedsMapping={setPendingGrouping}
          onRun={() => runAudit(currentId, priorId)}
          onCancel={() => setPicker(null)}
        />
      )}

      {pendingUpload && (
        <ColumnMapper
          mode={mode}
          pending={pendingUpload}
          onDone={(info) => (pendingUpload?.targetSlot
            ? placeDocumentInSlot(info, pendingUpload.targetSlot)
            : addDocument(info))}
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
