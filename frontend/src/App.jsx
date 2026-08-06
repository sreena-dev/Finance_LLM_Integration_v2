import { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchModes, probeMode } from './api/client';
import Sidebar from './components/Sidebar';
import ChatView from './components/chat/ChatView';
import ReportView from './components/report/ReportView';
import TrialBalanceView from './components/trial-balance/TrialBalanceView';
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
});

function ModeHeader({ mode, health }) {
  const state = !mode.integrated
    ? { tone: 'mute', text: 'Not integrated' }
    : health === undefined
      ? { tone: 'mute', text: 'Checking…' }
      : health.available
        ? { tone: 'ok', text: 'Pipeline ready' }
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
        <code className="head__path" title="This mode's API namespace">
          {mode.base_path}
        </code>
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

  // ── Boot: load the mode list, then probe each mode in the background ────
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const list = await fetchModes();
        if (cancelled) return;
        setModes(list);
        // Companions are sub-modes rendered inside their parent, so one must
        // never become the initially selected mode.
        setActiveId((cur) => cur || list.find((m) => !m.companion_of)?.id || null);
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
      <Sidebar modes={sidebarModes} activeId={activeId} onSelect={setActiveId} health={health} />

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
                ) : (
                  <ChatView
                    mode={activeMode}
                    health={health[activeMode.id]}
                    thread={threads[activeMode.id] || []}
                    setThread={setThreadFor(activeMode.id)}
                  />
                )}
              </motion.div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
