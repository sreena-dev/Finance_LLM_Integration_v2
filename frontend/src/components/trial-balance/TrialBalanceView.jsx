import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  tbAsk, tbAskGeneral, tbAudit, tbUpload, tbUploadGrouping, tbUploadMapped,
} from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import AuditCard from './AuditCard';
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

let _seq = 0;
const nextId = () => `m${++_seq}`;

// Small-talk greetings answer instantly from the client — no reason to spend a
// tool-calling turn (or a network round trip) on "hi". Matched as a whole
// message (with light punctuation tolerance) so this never intercepts a real
// question that merely starts with "hello" mid-sentence. Restored from main
// (e5213f7) after this file's TB-v2 re-sync silently dropped it.
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
 * A run is launched from the quick-action card, which opens the picker.
 *
 * `documents` starts as whatever was uploaded here, but also picks up any
 * document the run picker's "Existing (Database)" tab adds — selecting a
 * DB-ingested trial balance normalizes it into the same row shape as an
 * upload, so this state stays the single source of truth for `docOf` and the
 * active-selection banner either way. Uploads are content-addressed, so
 * re-uploading the same workbook returns the same `doc_id` and costs nothing.
 *
 * `viewMode` ('analysis' | 'chat') is a physical on/off toggle switching
 * between the two top-level modes: 'analysis' (OFF, the default) shows only
 * Single TB Analysis / Two TB Comparative Analysis, composer hidden;
 * 'chat' (ON) reveals the composer (Upload button + text input + Ask) and
 * greys out the two analysis buttons instead. Asking a question there calls
 * `/ask` (scoped to whatever document is currently active, `currentId`) or
 * `/ask-general` (the reference corpora, when nothing is active) — see
 * runChatQuestion — and persists as a conversation the same way Financial
 * Statement's chat does, via `conversationId`/`onConversationChange`. The
 * composer's Upload button is a separate, still-unwired feature: it opens a
 * picker mirroring Single TB Analysis's own, but its "Proceed" only stages a
 * TB for a not-yet-built "chat about a document not yet in LIVE" feature
 * (session-only, no LIVE write — see stageForQuery); it plays no part in
 * answering a question about the document already loaded.
 */
export default function TrialBalanceView({ mode, state, setState, conversationId, onConversationChange }) {
  const { documents, currentId, priorId, messages, pdfIds, viewMode } = state;

  // Staged (picked, not yet uploaded) files per slot -- nothing here has
  // touched the backend. Only "Run analysis"/"Run comparison" (see
  // runIngestionThenAudit) turns a staged File into a real ingest_tb_to_live
  // call, and only a SUCCESS/WARNING result ever promotes a slot into
  // `documents`/`currentId`/`priorId`.
  const [staged, setStaged] = useState({ single: null, current: null, prior: null, query: null, grouping: null });
  // Optional Company Details field values per slot ({companyName, cin,
  // financialYear, standard}) -- the picker's Upload new tab only, threaded
  // through to tbUploadMapped in ingestSlot below. Independent of `staged`
  // (a slot's file and its company details can be filled in either order).
  const [companyDetails, setCompanyDetails] = useState({ single: {}, current: {}, prior: {}, query: {} });
  // { slots: { single?/current?/prior?/grouping?: { status, message } } } while
  // a run is in flight or has just finished; null when the picker is idle.
  const [runState, setRunState] = useState(null);
  // Resolved grouping upload ({token, filename, ...}) from the most recent
  // Run -- distinct from `staged.grouping` (the raw File still pending upload).
  const [groupingResult, setGroupingResult] = useState(null);
  const [lastGroupingToken, setLastGroupingToken] = useState(null);
  const [picker, setPicker] = useState(null);      // 'single' | 'comparison' | 'query' | null
  // A doc picked from the query picker's "Existing (Database)" tab -- an
  // alternative to `staged.query` (a fresh file). Already durable in MAIN, so
  // "Proceed" for this needs no backend call at all, unlike a fresh upload.
  const [queryDbDoc, setQueryDbDoc] = useState(null);
  const [busy, setBusy] = useState(false);
  // Composer's query text, submitted by runChatQuestion.
  const [input, setInput] = useState('');
  const endRef = useRef(null);

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
  useEffect(() => { setGroupingResult(null); }, [currentId, priorId]);

  function resetRunState() {
    setStaged({ single: null, current: null, prior: null, query: null, grouping: null });
    setCompanyDetails({ single: {}, current: {}, prior: {}, query: {} });
    setQueryDbDoc(null);
    setRunState(null);
    setGroupingResult(null);
    setLastGroupingToken(null);
  }

  /**
   * Auto-advances once every slot the active picker mode requires is
   * resolved (already-ingested/DB-picked, or just successfully ingested by
   * runIngestionThenAudit or a "Proceed anyway" retry) and nothing is still
   * running. Declarative on purpose: a long-running async function's own
   * closure over currentId/priorId/staged goes stale the moment any of those
   * update mid-flight (exactly what happens across a parallel CY/PY ingest,
   * or a later standalone Proceed-anyway click) — reacting to committed state
   * instead sidesteps that entirely and handles both paths uniformly.
   */
  useEffect(() => {
    if (!picker || !runState) return;
    const anyRunning = Object.values(runState.slots || {}).some((s) => s?.status === 'running');
    if (anyRunning) return;
    const singleDone = picker === 'single' && Boolean(currentId) && !staged.single;
    const comparisonDone = picker === 'comparison'
      && Boolean(currentId) && Boolean(priorId) && !staged.current && !staged.prior;
    if (!(singleDone || comparisonDone)) return;

    setPicker(null);
    setRunState(null);
    const parts = [docOf(currentId)?.filename, picker === 'comparison' ? docOf(priorId)?.filename : null]
      .filter(Boolean);
    push({
      kind: 'note',
      text: `Loaded ${parts.map((p) => `"${p}"`).join(' and ')}`
        + `${groupingResult ? ' with chart-of-accounts grouping' : ''}.`,
    });
    runAudit(currentId, priorId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, priorId, staged, runState, picker]);

  const docOf = (id) => (documents || []).find((d) => d.doc_id === id) || null;
  const currentDoc = docOf(currentId);
  const priorDoc = docOf(priorId);

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
  }

  function pickDbDocForSlot(normalizedRow, slot) {
    mergeDbDocument(normalizedRow);
    patch({ [slot === 'prior' ? 'priorId' : 'currentId']: normalizedRow.doc_id });
  }

  /**
   * Uploads the staged grouping file: /audit/upload-grouping is a preview-
   * only structural check (not full ingestion) that hands back a token TB
   * slots can cite. Runs once per "Run analysis" click, before any TB slot's
   * ingestion, so every TB ingested this run gets the token — a bare TB with
   * no taxonomy columns of its own otherwise always lands in CASE_3 (see
   * quality_gate.py) even when the user did supply grouping data, just not
   * inside the TB file itself.
   */
  async function ingestGrouping(file) {
    setRunState((r) => ({ slots: { ...(r?.slots || {}), grouping: { status: 'running' } } }));
    try {
      const res = await tbUploadGrouping(mode, { file });
      if (res.needsMapping) {
        setRunState((r) => ({
          slots: {
            ...(r?.slots || {}),
            grouping: {
              status: 'failed',
              message: res.message || "Could not detect this grouping file's layout automatically.",
            },
          },
        }));
        return { ok: false };
      }
      setGroupingResult(res);
      setStaged((s) => ({ ...s, grouping: null }));
      setRunState((r) => ({ slots: { ...(r?.slots || {}), grouping: { status: 'success' } } }));
      return { ok: true, token: res.token };
    } catch (err) {
      setRunState((r) => ({
        slots: { ...(r?.slots || {}), grouping: { status: 'failed', message: err.message } },
      }));
      return { ok: false };
    }
  }

  /**
   * Uploads and ingests one staged TB file end to end: /upload (registers the
   * file server-side, runs a structural preview whose only remaining purpose
   * is to hand back a token -- ingest_tb_to_live has its own, separate auto-
   * detecting parser and does not depend on that preview having succeeded, so
   * a 422 there still carries a usable token) followed by /upload-mapped,
   * which runs the real classification engine (quality gate -> tier ->
   * confirmation gate -> taxonomy/LLM classification -> validation gate ->
   * LIVE write -> canonical Parquet). Every outcome is written into
   * `runState.slots[slot]`, never pushed to the chat stream — the picker is
   * the only place ingestion-phase messages ever surface. `staged[slot]` is
   * cleared only on a real SUCCESS/WARNING, so a CONFIRMATION_REQUIRED or
   * FAILED result leaves the file in place for a "Proceed anyway" retry or a
   * plain re-run without re-picking.
   */
  async function ingestSlot(slot, file, groupingToken, { acceptDataQualityRisk } = {}) {
    setRunState((r) => ({ slots: { ...(r?.slots || {}), [slot]: { status: 'running' } } }));
    try {
      const preview = await tbUpload(mode, file);
      const token = preview.token || preview.preview_token;
      const result = await tbUploadMapped(mode, {
        token, groupingToken, acceptDataQualityRisk, companyDetails: companyDetails[slot],
      });

      if (result.pipeline_status === 'CONFIRMATION_REQUIRED') {
        setRunState((r) => ({
          slots: {
            ...(r?.slots || {}),
            [slot]: {
              status: 'confirmation_required',
              message: result.message
                || `"${file.name}" needs a data-quality confirmation before it can be ingested.`,
            },
          },
        }));
        return { ok: false };
      }
      if (result.execution_status !== 'SUCCESS' || result.pipeline_status === 'FAILED') {
        setRunState((r) => ({
          slots: {
            ...(r?.slots || {}),
            [slot]: { status: 'failed', message: result.message || `Could not ingest "${file.name}".` },
          },
        }));
        return { ok: false };
      }

      // Multi-year input (Scenario D) ingests every detected fiscal year as
      // its own document -- the first fills this slot, the rest are added
      // as additional documents reachable from the DB tab / single-mode list.
      const [first, ...rest] = result.documents || [result];
      const toRow = (doc) => ({
        doc_id: doc.tb_doc_id, filename: file.name, sheet: null,
        periods: [doc.financial_year].filter(Boolean),
      });
      if (slot === 'single') addDocument(toRow(first));
      else placeDocumentInSlot(toRow(first), slot);
      for (const doc of rest) addDocument(toRow(doc));

      setRunState((r) => ({
        slots: {
          ...(r?.slots || {}),
          [slot]: {
            status: result.pipeline_status === 'WARNING' ? 'warning' : 'success',
            message: result.message,
          },
        },
      }));
      setStaged((s) => ({ ...s, [slot]: null }));
      return { ok: true, docId: first.tb_doc_id };
    } catch (err) {
      setRunState((r) => ({
        slots: { ...(r?.slots || {}), [slot]: { status: 'failed', message: err.message || `Could not upload "${file.name}".` } },
      }));
      return { ok: false };
    }
  }

  /**
   * The 'query' slot's own staging path -- structurally parallel to
   * ingestSlot (same tbUpload -> tbUploadMapped sequence, same
   * runState.slots.query bookkeeping so IngestStatus/"Proceed anyway" work
   * unchanged), but deliberately NOT ingestSlot itself: persistToLive: false
   * means nothing is written to LIVE, so there is no real tb_doc_id to add
   * to `documents`/`currentId`, and success must never trigger runAudit --
   * the canonical TB this produces is for the future query-analysis feature,
   * not an audit run. Closes the picker and drops one chat note on success.
   */
  async function stageForQuery(file, groupingToken, { acceptDataQualityRisk } = {}) {
    setRunState((r) => ({ slots: { ...(r?.slots || {}), query: { status: 'running' } } }));
    try {
      const preview = await tbUpload(mode, file);
      const token = preview.token || preview.preview_token;
      const result = await tbUploadMapped(mode, {
        token, groupingToken, acceptDataQualityRisk,
        companyDetails: companyDetails.query, persistToLive: false,
      });

      if (result.pipeline_status === 'CONFIRMATION_REQUIRED') {
        setRunState((r) => ({
          slots: {
            ...(r?.slots || {}),
            query: {
              status: 'confirmation_required',
              message: result.message
                || `"${file.name}" needs a data-quality confirmation before it can be staged.`,
            },
          },
        }));
        return;
      }
      if (result.execution_status !== 'SUCCESS' || result.pipeline_status === 'FAILED') {
        setRunState((r) => ({
          slots: { ...(r?.slots || {}), query: { status: 'failed', message: result.message || `Could not stage "${file.name}".` } },
        }));
        return;
      }

      setStaged((s) => ({ ...s, query: null }));
      setRunState(null);
      setPicker(null);
      push({
        kind: 'note',
        text: `"${file.name}" staged for query analysis (feature coming in a future update).`,
      });
    } catch (err) {
      setRunState((r) => ({
        slots: { ...(r?.slots || {}), query: { status: 'failed', message: err.message || `Could not upload "${file.name}".` } },
      }));
    }
  }

  /**
   * The 'query' picker's "Proceed" button. Two distinct sources, per the
   * picker's own two tabs:
   *  - "Existing (Database)" pick (`queryDbDoc`) -- already a durable MAIN
   *    document with its own canonical data; no backend call needed at all,
   *    the future query feature can `load_tb_from_db` it by tb_doc_id
   *    whenever it's built. Proceed here just confirms the selection.
   *  - "Upload new" (`staged.query`) -- a fresh file; uploads the grouping
   *    file (if staged) first, same as runIngestionThenAudit, then runs the
   *    real classify/quality-gate chain via stageForQuery.
   */
  async function runQueryStaging() {
    if (queryDbDoc) {
      setPicker(null);
      push({
        kind: 'note',
        text: `"${queryDbDoc.filename}" staged for query analysis (feature coming in a future update).`,
      });
      setQueryDbDoc(null);
      return;
    }
    if (!staged.query) return;
    setRunState((r) => r || { slots: {} });
    let groupingToken = groupingResult?.token || null;
    if (staged.grouping) {
      const g = await ingestGrouping(staged.grouping);
      if (g.ok) groupingToken = g.token;
    }
    setLastGroupingToken(groupingToken);
    await stageForQuery(staged.query, groupingToken);
  }

  /**
   * The single "Run analysis"/"Run comparison" entry point. Uploads the
   * grouping file (if staged) once, then ingests every staged TB slot --
   * comparison mode's CY/PY run in parallel via Promise.allSettled, since
   * each is an independent LIVE write keyed by its own tb_doc_id with no
   * shared mutable state, so a failure in one never blocks the other from
   * completing and reporting its own result. Does not itself decide when to
   * close the picker or hand off to /audit -- the auto-advance effect above
   * reacts to the resulting state once every required slot is resolved,
   * which uniformly covers both this initial run and any later standalone
   * "Proceed anyway" retry.
   */
  async function runIngestionThenAudit() {
    setRunState((r) => r || { slots: {} });

    let groupingToken = groupingResult?.token || null;
    if (staged.grouping) {
      const g = await ingestGrouping(staged.grouping);
      if (g.ok) groupingToken = g.token;
    }
    setLastGroupingToken(groupingToken);

    const jobs = picker === 'single'
      ? (staged.single ? [['single', staged.single]] : [])
      : [
          ...(staged.current ? [['current', staged.current]] : []),
          ...(staged.prior ? [['prior', staged.prior]] : []),
        ];

    await Promise.allSettled(jobs.map(([slot, file]) => ingestSlot(slot, file, groupingToken)));
  }

  /** Re-attempts one slot's ingestion with accept_data_quality_risk=True,
   * reusing the file still held in `staged[slot]` (never cleared on
   * CONFIRMATION_REQUIRED) and the grouping token from the run that produced
   * that result. */
  function proceedAnyway(slot) {
    const file = staged[slot];
    if (!file) return;
    if (slot === 'query') {
      stageForQuery(file, lastGroupingToken, { acceptDataQualityRisk: true });
      return;
    }
    ingestSlot(slot, file, lastGroupingToken, { acceptDataQualityRisk: true });
  }

  async function runAudit(docId, priorDocId) {
    const doc = docOf(docId);
    const prior = docOf(priorDocId);
    const label = prior ? 'Two TB Comparative Analysis' : 'Single TB Analysis';
    push({
      kind: 'user',
      text: `${label} — ${doc?.filename}${prior ? ` vs ${prior.filename} (two periods)` : ''}`
        + `${pdfIds.length ? ` + ${pdfIds.length} supporting PDF(s)` : ''}`
        + `${groupingResult ? ' + chart-of-accounts grouping' : ''}`,
    });
    const loadingId = push({ kind: 'loading', stages: prior ? COMPARISON_AUDIT_STAGES : SINGLE_AUDIT_STAGES });
    setBusy(true);
    try {
      const result = await tbAudit(mode, {
        docId, docIdPrior: priorDocId,
        groupingToken: groupingResult?.token,
        uploadDocIds: pdfIds,
        docLabel: doc?.filename,
      });

      drop(loadingId);
      push({ kind: 'audit', result, doc, priorDoc: prior, pdfIds: [...pdfIds] });
      // Each analysis run starts its own conversation server-side (see /audit's
      // own comment) -- adopt it as the active one so it's selected in the
      // sidebar, same as a chat turn's first message does.
      if (result.conversation_id) onConversationChange(result.conversation_id);
    } catch (err) {
      replace(loadingId, { kind: 'error', text: err.message || `${label} failed.` });
    } finally {
      setBusy(false);
    }
  }

  /**
   * Chat Mode's Ask button. Scoped to whatever document is currently active
   * (`currentId`) via `/ask`, or `/ask-general` when nothing is loaded — this
   * answers about the document already in this stream, not the composer's
   * separate stage-a-fresh-file flow (see the class doc-comment). Mirrors
   * runAudit's push/loading/replace/busy shape exactly.
   *
   * A greeting short-circuits before any network call (see matchGreeting) —
   * restored from main (e5213f7), which this file's TB-v2 re-sync had
   * silently dropped. Pushes the FULL response object as `result` (not just
   * its `.answer` text) so the answer card can also show `computed`/
   * `guardrail`/`sources` when present, same as before the drop.
   */
  async function runChatQuestion() {
    const question = input.trim();
    if (!question || busy) return;
    push({ kind: 'user', text: question });
    setInput('');

    const greeting = matchGreeting(question);
    if (greeting) {
      push({ kind: 'greeting', text: greeting });
      return;
    }

    const loadingId = push({ kind: 'loading', stages: ['Thinking'] });
    setBusy(true);
    try {
      const result = currentId
        ? await tbAsk(mode, { docId: currentId, question, conversationId })
        : await tbAskGeneral(mode, { question, conversationId });
      drop(loadingId);
      push({ kind: 'answer', result, scope: currentId ? 'trial-balance' : 'corpora' });
      if (result.conversation_id && result.conversation_id !== conversationId) {
        onConversationChange(result.conversation_id);
      }
    } catch (err) {
      replace(loadingId, { kind: 'error', text: err.message || 'Could not get an answer.' });
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
                Single TB Analysis for one file, or Two TB Comparative Analysis to
                compare two periods.
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
            if (m.kind === 'error') {
              return (
                <Notice key={m.id} tone="error" title="Unable to Complete Request">{m.text}</Notice>
              );
            }
            if (m.kind === 'loading') {
              return <LoadingRow key={m.id} stages={m.stages} />;
            }
            if (m.kind === 'audit') return <AuditCard key={m.id} mode={mode} msg={m} />;
            return null;
          })}
          <div ref={endRef} />
        </div>
      </div>

      <div className="tb__toprow">
        {/*
         * Analysis/Chat toggle -- top-left, a physical on/off switch, not a
         * tab. OFF ("Analysis", the default): Single TB Analysis / Two TB
         * Comparative Analysis are the active buttons, composer hidden. ON
         * ("Chat"): those two buttons grey out instead, and the composer
         * (Upload button + text input + Ask) appears in their place.
         */}
        <div className="tb__querytoggle">
          <button
            type="button"
            role="switch"
            aria-checked={viewMode === 'chat'}
            aria-label={viewMode === 'chat' ? 'Mode: Chat Mode' : 'Mode: Analysis Mode'}
            className={`tb__toggle ${viewMode === 'chat' ? 'is-on' : ''}`}
            onClick={() => patch({ viewMode: viewMode === 'chat' ? 'analysis' : 'chat' })}
          >
            <span className="tb__toggle-knob" />
          </button>
          <span className="tb__toggle-label">{viewMode === 'chat' ? 'Chat Mode' : 'Analysis Mode'}</span>
        </div>

        <div className="tb__actions">
          {quickActions.map((qa) => (
            <button
              key={qa.id}
              type="button"
              className="tb__action"
              title={viewMode === 'chat'
                ? 'Switch to Analysis Mode to run an audit'
                : `${qa.desc} (${qa.duration})`}
              onClick={() => {
                // Single mode never uses the PRIOR slot — clear a leftover
                // selection from an earlier comparison run so the picker
                // doesn't show a stale PRIOR badge.
                if (qa.id === 'single' && priorId) patch({ priorId: null });
                resetRunState();
                setPicker(qa.id);
              }}
              disabled={busy || viewMode === 'chat'}
            >
              <Icon name={qa.icon} size={13} />
              {qa.title}
            </button>
          ))}
        </div>
      </div>

      {/*
       * Composer -- only in Chat mode. Asks about `currentId` (the document
       * currently active in this stream) via runChatQuestion, or the
       * reference corpora when nothing is active. The Upload button is a
       * separate, still-unwired feature (see the class doc-comment) that
       * stages a fresh document for a not-yet-built query-only flow -- it
       * does not feed the question below.
       */}
      {viewMode === 'chat' && (
        <div className="tb__composer">
          <button
            type="button"
            className="tb__attach"
            title="Upload or pick a trial balance to prepare it for query analysis (feature coming in a future update)"
            disabled={busy}
            onClick={() => { resetRunState(); setPicker('query'); }}
          >
            <Icon name="upload" size={18} />
          </button>
          <textarea
            className="tb__input"
            rows={1}
            value={input}
            placeholder={currentDoc ? `Ask about ${currentDoc.filename}…` : 'Ask a question…'}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                runChatQuestion();
              }
            }}
          />
          <button
            type="button"
            className="btn btn--primary tb__ask"
            disabled={busy || !input.trim()}
            title="Ask"
            onClick={runChatQuestion}
          >
            <Icon name="sparkle" size={16} />
            Ask
          </button>
        </div>
      )}

      {picker && (
        <TbRunPicker
          mode={mode}
          pickerMode={picker}
          documents={documents}
          running={Boolean(runState)}
          staged={staged}
          runState={runState}
          onStage={(slot, file) => {
            // Uploading a fresh file for the query picker supersedes any
            // Existing-Database pick made in the same session.
            if (slot === 'query') setQueryDbDoc(null);
            setStaged((s) => ({ ...s, [slot]: file }));
          }}
          onRemoveStaged={(slot) => setStaged((s) => ({ ...s, [slot]: null }))}
          onStageGrouping={(file) => { setStaged((s) => ({ ...s, grouping: file })); setGroupingResult(null); }}
          onRemoveGroupingStaged={() => { setStaged((s) => ({ ...s, grouping: null })); setGroupingResult(null); }}
          onProceedAnyway={proceedAnyway}
          onClearSlot={(slot) => patch(slot === 'prior' ? { priorId: null } : { currentId: null })}
          currentId={currentId}
          priorId={priorId}
          // Single mode only — comparison mode's explicit CY/PY slots use
          // onStage/onPickDbDocForSlot instead, so a doc is never ambiguous
          // about which period it belongs to.
          onSelect={(id) => patch({ currentId: id === currentId ? null : id, priorId: null })}
          onPickDbDoc={(row) => mergeDbDocument(row)}
          onPickDbDocForSlot={(row, slot) => pickDbDocForSlot(row, slot)}
          onDeleted={(docId) => patch({
            documents: (documents || []).filter((d) => d.doc_id !== docId),
            currentId: currentId === docId ? null : currentId,
            priorId: priorId === docId ? null : priorId,
          })}
          groupingResult={groupingResult}
          companyDetails={companyDetails}
          onCompanyDetailsChange={(slot, field, value) =>
            setCompanyDetails((c) => ({ ...c, [slot]: { ...c[slot], [field]: value } }))}
          queryDbDocId={queryDbDoc?.doc_id}
          onPickQueryDbDoc={(row) => { setStaged((s) => ({ ...s, query: null })); setQueryDbDoc(row); }}
          onRun={runIngestionThenAudit}
          onProceed={runQueryStaging}
          onCancel={() => { resetRunState(); setPicker(null); }}
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
