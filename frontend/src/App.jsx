import { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  deleteConversation,
  fetchConversation,
  fetchModes,
  listConversations,
  fsUploadHealth,
  probeMode,
} from './api/client';
import Sidebar from './components/Sidebar';
import ConversationList from './components/chat/ConversationList';
import ChatView from './components/chat/ChatView';
import DocumentPane from './components/ingestion/DocumentPane';
import ReportView from './components/report/ReportView';
import TrialBalanceView from './components/trial-balance/TrialBalanceView';
import FdrAnalysis from './components/financial-diagnostic-report/FdrAnalysis';
import Icon from './components/common/Icon';
import Notice from './components/common/Notice';
import './App.css';

const emptyReportState = () => ({
  entities: null,
  catalogError: null,
  entity: null,
  fy: null,
  scope: 'standalone',
  report: null,
  error: null,
});

// Trial Balance is an append-only stream rather than tabbed panels: `messages`
// holds every run's result so an audit and a validation of the same file can be
// read against each other. `pdfIds` is the supporting-evidence selection, kept at
// mode level because it applies to the entity rather than to one trial balance.
// `documents` is what this session uploaded, not the stored-document catalog —
// see the note in TrialBalanceView on why the catalog is not listed.
const emptyTBState = () => ({
  documents: [],
  currentId: null,
  priorId: null,
  messages: [],
  pdfIds: [],
  // 'db' = free-form questions answered by intent from already-ingested data,
  // no file needed. 'upload' = questions scoped to a file uploaded this session.
  chatQueryMode: 'db',
});

function ModeHeader({ mode, health }) {
  const state = !mode.integrated
    ? { tone: 'mute', text: 'Not integrated' }
    : health === undefined
      ? { tone: 'mute', text: 'Checking…' }
      : health.available
        ? { tone: 'ok', text: 'Available' }
        : { tone: 'err', text: 'Unavailable' };

  return (
    <header className="head">
      <div className="head__text">
        <h1 className="head__title">{mode.label}</h1>
        <p className="head__sub">{mode.description}</p>
      </div>

      <div className="head__meta">
        <span className={`pill pill--${state.tone}`}>
          <span className="dot" />
          {state.text}
        </span>
      </div>
    </header>
  );
}

export default function App() {
  const [modes, setModes] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [bootError, setBootError] = useState(null);
  const [booting, setBooting] = useState(true);

  const [health, setHealth] = useState({});
  const [threads, setThreads] = useState({});
  const [reports, setReports] = useState({});
  const [tbStates, setTBStates] = useState({});

  // Financial Statements chat history. Held here rather than in ChatView because
  // App already owns `threads`, and rehydrating a saved conversation means
  // replacing that array — which is App's to do.
  const [conversations, setConversations] = useState([]);
  const [convoId, setConvoId] = useState(null);
  const [convoState, setConvoState] = useState({ loading: false, error: null });

  // Which uploaded document (if any) the right-hand pane is showing.
  // Conversation-scoped, not mode-scoped: a document belongs to one
  // conversation, so switching conversations closes a stale pane rather than
  // leaving it open on a document the new conversation doesn't have.
  const [fsViewDoc, setFsViewDoc] = useState(null);
  // A cell to open straight away in the pane's editor (from the quality drawer's
  // Enter / Review buttons): `{ page_no, table_id, row_index, col_index }`.
  const [fsFocus, setFsFocus] = useState(null);
  const openDocPane = useCallback((doc, focus = null) => {
    setFsViewDoc(doc);
    setFsFocus(focus);
  }, []);
  useEffect(() => { setFsViewDoc(null); setFsFocus(null); }, [convoId]);

  // How long the server keeps an upload, for the sidebar note and the report.
  const [retentionDays, setRetentionDays] = useState(30);
  const [offline, setOffline] = useState(() => typeof navigator !== 'undefined' && navigator.onLine === false);
  useEffect(() => {
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);

  // ── Boot: load the mode list, then probe each mode in the background ────
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const list = await fetchModes();
        if (cancelled) return;
        setModes(list);
        // Companions are sub-modes rendered inside their parent, so one must
        // never become the initially selected mode. A refresh restores the
        // last-selected mode from localStorage when it is still a valid,
        // non-companion mode; otherwise it falls back to the first one.
        setActiveId((cur) => {
          if (cur) return cur;
          const stored = localStorage.getItem('artha.activeMode');
          const restorable = list.some((m) => m.id === stored && !m.companion_of);
          return (restorable && stored) || list.find((m) => !m.companion_of)?.id || null;
        });
        setBootError(null);

        // Probed in parallel and after first paint: probing imports pipelines
        // and opens DB connections, which is far too slow to block the UI on.
        list.forEach(async (mode) => {
          const res = await probeMode(mode);
          if (!cancelled) setHealth((prev) => ({ ...prev, [mode.id]: res }));
        });
      } catch (err) {
        if (!cancelled) setBootError(err.message);
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const activeMode = useMemo(
    () => modes.find((m) => m.id === activeId) || null,
    [modes, activeId]
  );

  // Remember the selected mode so a refresh returns here instead of the first
  // page. Restored on boot in the mode-list effect above.
  useEffect(() => {
    if (activeId) localStorage.setItem('artha.activeMode', activeId);
  }, [activeId]);

  // The switcher lists top-level modes only. A companion (SAR Q&A) shares its
  // parent's entity/FY dropdowns, so listing it separately would show the same
  // two selects twice under different names; its parent renders it as a toggle
  // instead. It is still probed and still has its own routes.
  const sidebarModes = useMemo(() => modes.filter((m) => !m.companion_of), [modes]);

  // Which companion (if any) the active mode is currently showing. The header
  // names the pipeline that will actually serve the next request, so switching
  // to SAR Q&A has to retitle it and swap the API namespace shown — otherwise
  // the header claims /api/statutory-auditor-report while the request goes to
  // /api/sar-chat.
  const [subModeId, setSubModeId] = useState(null);

  useEffect(() => {
    setSubModeId(null);
  }, [activeId]);

  const headerMode = useMemo(
    () => modes.find((m) => m.id === subModeId) || activeMode,
    [modes, subModeId, activeMode]
  );

  // Per-mode state setters, so one mode's thread can never leak into another.
  const setThreadFor = useCallback(
    (id) => (updater) =>
      setThreads((prev) => {
        const cur = prev[id] || [];
        return { ...prev, [id]: typeof updater === 'function' ? updater(cur) : updater };
      }),
    []
  );

  const setReportFor = useCallback(
    (id) => (updater) =>
      setReports((prev) => {
        const cur = prev[id] || emptyReportState();
        return { ...prev, [id]: typeof updater === 'function' ? updater(cur) : updater };
      }),
    []
  );

  const setTBFor = useCallback(
    (id) => (updater) =>
      setTBStates((prev) => {
        const cur = prev[id] || emptyTBState();
        return { ...prev, [id]: typeof updater === 'function' ? updater(cur) : updater };
      }),
    []
  );

  // ── Financial Statements conversations ──────────────────────────────────
  const fsMode = useMemo(() => modes.find((m) => m.id === 'financial-statement') || null, [modes]);
  useEffect(() => {
    if (!fsMode) return;
    fsUploadHealth(fsMode).then((h) => { if (h?.retention_days) setRetentionDays(h.retention_days); });
  }, [fsMode]);

  const refreshConversations = useCallback(async () => {
    if (!fsMode) return;
    setConvoState((s) => ({ ...s, loading: true, error: null }));
    try {
      setConversations(await listConversations(fsMode));
      setConvoState({ loading: false, error: null });
    } catch (err) {
      // A 401 has already dropped the session; anything else is worth showing
      // in place of the list rather than silently rendering it empty.
      setConvoState({ loading: false, error: err.status === 401 ? null : err.message });
    }
  }, [fsMode]);

  // Loaded when the mode is first opened, so a signed-in user sees their
  // history immediately rather than after asking something.
  useEffect(() => {
    if (activeId === 'financial-statement' && fsMode) refreshConversations();
  }, [activeId, fsMode, refreshConversations]);

  const openConversation = useCallback(
    async (id) => {
      if (!fsMode || id === convoId) return;
      setConvoState((s) => ({ ...s, error: null }));
      try {
        const { messages } = await fetchConversation(fsMode, id);
        // Rehydrated into exactly the shape ChatView already renders, so it
        // needs no loading code of its own. `payload` is the verbatim stored
        // response, which is why a reopened turn shows its evidence cards
        // identically to a live one.
        setThreads((prev) => ({
          ...prev,
          ['financial-statement']: (messages || []).map((m, i) =>
            m.role === 'user'
              ? { role: 'user', text: m.content, id: `u-${id}-${i}` }
              : { role: 'assistant', result: m.payload || { final_answer: m.content }, id: `a-${id}-${i}` }
          ),
        }));
        setConvoId(id);
      } catch (err) {
        setConvoState((s) => ({ ...s, error: err.message }));
      }
    },
    [fsMode, convoId]
  );

  const newConversation = useCallback(() => {
    setConvoId(null);
    setThreads((prev) => ({ ...prev, ['financial-statement']: [] }));
  }, []);

  const removeConversation = useCallback(
    async (id) => {
      if (!fsMode) return;
      try {
        await deleteConversation(fsMode, id);
        setConversations((prev) => prev.filter((c) => c.conversation_id !== id));
        if (id === convoId) newConversation();
      } catch (err) {
        setConvoState((s) => ({ ...s, error: err.message }));
      }
    },
    [fsMode, convoId, newConversation]
  );

  // The server assigns the id on the first turn; adopt it and refresh the list
  // so the new thread appears with its derived title.
  const onConversationChange = useCallback(
    (id) => {
      if (!id) return;
      setConvoId((cur) => (cur === id ? cur : id));
      refreshConversations();
    },
    [refreshConversations]
  );

  if (booting) {
    return (
      <div className="boot">
        <motion.div
          className="boot__mark"
          animate={{ scale: [1, 1.06, 1], opacity: [0.75, 1, 0.75] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
        >
          <svg viewBox="0 0 32 32" width="40" height="40" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--navy-700)" />
            <path d="M9 22L15 10L21 22" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M11.3 17.5H18.7" stroke="var(--gold-600)" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
        </motion.div>
        <p className="boot__text">Starting Artha.AI…</p>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="boot">
        <div className="boot__panel">
          <Notice tone="error" title="Cannot reach the backend gateway">
            {bootError}
            <p style={{ margin: '10px 0 0' }}>
              Start it from the <code>backend/</code> directory:
              <br />
              <code>uvicorn app.main:app --reload --port 8090</code>
            </p>
          </Notice>
          <button
            type="button"
            className="btn btn--primary"
            style={{ marginTop: 16 }}
            onClick={() => window.location.reload()}
          >
            <Icon name="refresh" size={15} />
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <Sidebar modes={sidebarModes} activeId={activeId} onSelect={setActiveId} health={health}
               rail={activeId === 'financial-statement' && Boolean(fsViewDoc)}
               retentionDays={activeId === 'financial-statement' ? retentionDays : null}>
        {/* Only Financial Statements persists a thread, so only it gets a
            history list. Rendering this for every mode would advertise a
            feature the others do not have. */}
        {activeId === 'financial-statement' && (
          <ConversationList
            conversations={conversations}
            activeId={convoId}
            loading={convoState.loading}
            error={convoState.error}
            onSelect={openConversation}
            onNew={newConversation}
            onDelete={removeConversation}
          />
        )}
      </Sidebar>

      <main className="main">
        {activeMode && (
          <>
            <ModeHeader mode={headerMode} health={health[headerMode.id]} />

            <div className="main__body">
              {/* No AnimatePresence here, deliberately: it wraps this pane in
                  an exit-animation lifecycle that framer-motion must signal
                  as complete before unmounting — and with ReportView nesting
                  its own AnimatePresence, that signal reliably never fired.
                  The old pane got stuck forever at its exit values (opacity
                  0, but still absolutely positioned and still hit-testable),
                  leaving the screen blank and — because an invisible element
                  keeps intercepting clicks — the next mode switch silently
                  failed too. A plain `key`-based swap has no exit lifecycle
                  to get stuck in: React unmounts the old pane and mounts the
                  new one synchronously, every time. The fade-in on `initial`
                  still plays; only the fade-out on removal is gone. */}
              <motion.div
                key={activeMode.id}
                className="main__pane"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                {offline && (
                  <div className="main__notice">
                    <Notice tone="warn" title="You are offline">
                      Anything you have typed is kept. Questions will send once the connection is back.
                    </Notice>
                  </div>
                )}

                {/* A mode that is down or unintegrated shows the gateway's
                    own reason string — it names the exact missing setting. */}
                {health[activeMode.id] && !health[activeMode.id].available && (
                  <div className="main__notice">
                    <Notice
                      tone={activeMode.integrated ? 'error' : 'warn'}
                      title={
                        activeMode.integrated
                          ? 'This pipeline is not available'
                          : `The ${activeMode.branch} branch is not integrated yet`
                      }
                    >
                      {health[activeMode.id].reason}
                    </Notice>
                  </div>
                )}

                {/* SAR Q&A is folded into this view rather than being its own
                    sidebar entry: `chatMode` is the companion mode the gateway
                    reported, and ReportView offers it as a Report/Chat toggle.
                    Passing the mode object (not a URL) keeps every request built
                    from the gateway's own base_path. */}
                {activeMode.ui === 'report' || activeMode.ui === 'report-chat' ? (
                  <ReportView
                    mode={activeMode}
                    chatMode={modes.find((m) => m.companion_of === activeMode.id) || null}
                    health={health}
                    onSubModeChange={setSubModeId}
                    state={reports[activeMode.id] || emptyReportState()}
                    setState={setReportFor(activeMode.id)}
                  />
                ) : activeMode.ui === 'trial-balance' ? (
                  <TrialBalanceView
                    mode={activeMode}
                    health={health[activeMode.id]}
                    state={tbStates[activeMode.id] || emptyTBState()}
                    setState={setTBFor(activeMode.id)}
                  />
                ) : activeMode.ui === 'fdr' ? (
                  // Self-contained mocked view; the gateway's ModeHeader already
                  // renders the title/pill/path, so it runs in `embedded` mode.
                  <FdrAnalysis embedded />
                ) : activeMode.ui === 'chat' ? (
                  <div className="main__split">
                    <ChatView
                      mode={activeMode}
                      health={health[activeMode.id]}
                      thread={threads[activeMode.id] || []}
                      setThread={setThreadFor(activeMode.id)}
                      conversationId={convoId}
                      onConversationChange={onConversationChange}
                      onViewDocument={
                        activeMode.id === 'financial-statement' ? openDocPane : undefined
                      }
                    />
                    {activeMode.id === 'financial-statement' && fsViewDoc && (
                      <DocumentPane
                        mode={activeMode}
                        conversationId={convoId}
                        doc={fsViewDoc}
                        focus={fsFocus}
                        retentionDays={retentionDays}
                        onClose={() => { setFsViewDoc(null); setFsFocus(null); }}
                      />
                    )}
                  </div>
                ) : (
                  // ChatView used to be the `else` fallback. It is now an
                  // explicit match, because a future mode with an unrecognised
                  // `ui` would otherwise silently inherit the FS chat surface —
                  // and with it FS conversation persistence, writing that mode's
                  // turns into artha_fs_messages.
                  <div className="main__notice">
                    <Notice tone="warn" title="This mode has no interface yet">
                      The gateway describes this mode as <code>{activeMode.ui}</code>,
                      which this build does not know how to render.
                    </Notice>
                  </div>
                )}
              </motion.div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
