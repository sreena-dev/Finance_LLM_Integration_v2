import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { fetchCatalog, generateReport, runSARChat } from '../../api/client';
import Icon from '../common/Icon';
import Select from '../common/Select';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import Composer from '../chat/Composer';
import AnswerCard from '../chat/AnswerCard';
import ReportDocument from './ReportDocument';
import './ReportView.css';
// The chat thread below reuses ChatView's classes (.chat__scroll, .chat__user…).
// Imported explicitly rather than relying on ChatView being in the bundle
// already: that happens to be true today, but it would make this view's styling
// depend on an unrelated component staying imported.
import '../chat/ChatView.css';

const PIPELINE_STEPS = [
  'Resolving the document',
  'Fetching report sections and financials',
  'Running deterministic pre-flight checks',
  'Extracting opinion, CARO and IFC',
  'Running coherence checks',
  'Drafting the review memorandum',
];

const CHAT_STEPS = [
  'Rewriting the question',
  'Retrieving report context',
  'Drafting the answer',
  'Applying guardrails',
];

/**
 * Statutory Auditor's Report mode — two ways to interrogate the same document.
 *
 * Both sub-modes take exactly the same two inputs (an entity and a financial
 * year, from the same catalog), so they share one selector and one sidebar
 * entry, and the Report/Chat toggle chooses what to do with that selection:
 *
 *   Report — the full SAR review memorandum, generated in one pass.
 *   Chat   — conversational Q&A against the same document.
 *
 * The two are separate pipelines behind separate URL namespaces. `chatMode` is
 * the companion mode object the gateway reported, so the chat calls are built
 * from its own `base_path` exactly like every other call in this app — nothing
 * here hard-codes a route, and the report pipeline cannot be reached from the
 * chat toggle or vice versa.
 *
 * The year list is nested under the chosen entity in the catalog response, so
 * the second dropdown can only ever offer a combination that has a document
 * behind it.
 */
export default function ReportView({ mode, chatMode, health, onSubModeChange, state, setState }) {
  const { entities, catalogError, entity, fy, scope, report, error } = state;

  const [loadingCatalog, setLoadingCatalog] = useState(entities === null);
  const [generating, setGenerating] = useState(false);

  // Which sub-mode is showing. Local rather than lifted into `state`: it is a
  // view preference, not part of the report the parent caches per mode.
  const [view, setView] = useState('report');

  const [chatThread, setChatThread] = useState([]);
  const [chatPending, setChatPending] = useState(false);
  const [chatError, setChatError] = useState(null);
  const chatEndRef = useRef(null);

  // Only offer the toggle when the gateway actually reported a companion; with
  // the sar-chat mode absent this degrades to the plain report view.
  const hasChat = Boolean(chatMode);
  const isChat = hasChat && view === 'chat';
  const chatHealth = chatMode ? health?.[chatMode.id] : null;

  const patch = (fields) => setState((prev) => ({ ...prev, ...fields }));

  // Load the catalog once per session.
  useEffect(() => {
    if (entities !== null) return;
    let cancelled = false;

    (async () => {
      setLoadingCatalog(true);
      try {
        const list = await fetchCatalog(mode);
        if (!cancelled) patch({ entities: list, catalogError: null });
      } catch (err) {
        if (!cancelled) patch({ entities: [], catalogError: err.message });
      } finally {
        if (!cancelled) setLoadingCatalog(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.id]);

  // A thread is about one entity and year, so changing either has to clear it —
  // otherwise the next question would be answered for the new document while
  // the visible history above it belongs to the old one.
  useEffect(() => {
    setChatThread([]);
    setChatError(null);
  }, [entity, fy]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [chatThread.length, chatPending]);

  const entityOptions = useMemo(
    () =>
      (entities || []).map((e) => ({
        value: e.entity,
        label: e.entity,
        sublabel: `${e.years.length} ${e.years.length === 1 ? 'year' : 'years'}`,
      })),
    [entities]
  );

  const selectedEntity = useMemo(
    () => (entities || []).find((e) => e.entity === entity) || null,
    [entities, entity]
  );

  const yearOptions = useMemo(
    () =>
      (selectedEntity?.years || []).map((y) => ({
        value: `${y.fy_start}-${y.fy_end}`,
        label: y.label,
        sublabel: y.doc_name || undefined,
      })),
    [selectedEntity]
  );

  function onEntityChange(next) {
    // Clearing the year is what prevents an entity/FY pair that has no
    // document — the years belong to the previously selected entity.
    patch({ entity: next, fy: null });
  }

  const [fyStart, fyEnd] = fy ? fy.split('-').map(Number) : [null, null];

  // The catalog's own label ("FY 2024-25"), so the chat header and composer
  // name the year exactly as the dropdown above them does. Deriving it from the
  // raw value instead would render "2024–2025" next to a dropdown reading
  // "FY 2024-25" and look like two different selections.
  const fyLabel = useMemo(
    () => (selectedEntity?.years || []).find((y) => `${y.fy_start}-${y.fy_end}` === fy)?.label || fy,
    [selectedEntity, fy]
  );

  async function generate() {
    if (!entity || !fy || generating) return;
    patch({ error: null });
    setGenerating(true);
    try {
      // `scope` is no longer user-selectable — the selector was removed because
      // every ingested package is a standalone report. It stays in the request
      // because the pipeline and the generated document still record it.
      const result = await generateReport(mode, { entity, fyStart, fyEnd, scope });
      patch({ report: result, error: null });
    } catch (err) {
      patch({ error: err.message, report: null });
    } finally {
      setGenerating(false);
    }
  }

  async function submitChat(text) {
    if (!text.trim() || chatPending || !chatMode) return;
    const query = text.trim();
    setChatError(null);
    setChatPending(true);
    setChatThread((prev) => [...prev, { role: 'user', text: query, id: `u-${Date.now()}` }]);

    // Prior turns, so the pipeline's rewriter can resolve "it" / "that clause"
    // into a standalone question. Built from the thread *before* this turn.
    const history = chatThread.map((m) =>
      m.role === 'user'
        ? { role: 'user', content: m.text }
        : { role: 'assistant', content: m.result?.final_answer || '' }
    );

    try {
      const result = await runSARChat(chatMode, { query, company: entity, fyStart, history });
      setChatThread((prev) => [...prev, { role: 'assistant', result, id: `a-${Date.now()}` }]);
    } catch (err) {
      setChatError(err.message);
    } finally {
      setChatPending(false);
    }
  }

  const canGenerate = Boolean(entity && fy) && !generating;
  const ready = Boolean(entity && fy);

  const VIEWS = [
    { value: 'report', label: 'Report mode' },
    { value: 'chat', label: 'Chat mode' },
  ];

  return (
    <div className="report">
      <div className="report__scroll">
        <div className="report__inner">
          <motion.section
            className="setup card"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
          >
            <header className="setup__head">
              <span className="setup__mark">
                <Icon name={isChat ? 'sparkle' : 'seal'} size={18} />
              </span>
              <div>
                <h2 className="setup__title">
                  {isChat
                    ? 'Ask about an Independent Auditors Report'
                    : 'Analysis on Independent Auditors Report'}
                </h2>
                <p className="setup__sub">
                  {isChat
                    ? 'Select the entity and financial year, then ask your question below.'
                    : 'Select the entity and financial year to analyse the Independent Auditors Report.'}
                </p>
              </div>
            </header>

            {catalogError ? (
              <div className="setup__body">
                <Notice tone="error" title="Could not load the entity catalog">
                  {catalogError}
                </Notice>
              </div>
            ) : (
              <div className="setup__body">
                <div className="setup__grid">
                  <Select
                    label="Entity name"
                    icon="doc"
                    value={entity}
                    options={entityOptions}
                    onChange={onEntityChange}
                    loading={loadingCatalog}
                    placeholder="Select an entity"
                    emptyText="No entities ingested"
                    disabled={generating}
                  />

                  <Select
                    label="Financial year"
                    icon="ledger"
                    value={fy}
                    options={yearOptions}
                    onChange={(v) => patch({ fy: v })}
                    placeholder={entity ? 'Select a financial year' : 'Select an entity first'}
                    emptyText="No financial years for this entity"
                    disabled={!entity || generating}
                  />
                </div>

                <div className="setup__row">
                  {/* Report / Chat toggle. Sits where the scope selector used to
                      and reuses its segmented-control styling. */}
                  {hasChat && (
                    <div className="scope">
                      <span className="scope__label">Mode</span>
                      <div className="scope__group" role="radiogroup" aria-label="Report or chat">
                        {VIEWS.map((v) => (
                          <button
                            key={v.value}
                            type="button"
                            role="radio"
                            aria-checked={view === v.value}
                            className={`scope__btn ${view === v.value ? 'is-on' : ''}`}
                            onClick={() => {
                              setView(v.value);
                              // Let the page header retitle itself and show the
                              // namespace that will actually serve the request.
                              onSubModeChange?.(v.value === 'chat' ? chatMode.id : null);
                            }}
                            disabled={generating}
                          >
                            {view === v.value && (
                              <motion.span
                                className="scope__bg"
                                layoutId="sar-view-active"
                                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                              />
                            )}
                            <span className="scope__text">{v.label}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Report mode needs an explicit action; chat mode's action is
                      the composer, so it gets no second button. */}
                  {!isChat && (
                    <motion.button
                      type="button"
                      className="btn btn--primary setup__go"
                      onClick={generate}
                      disabled={!canGenerate}
                      whileHover={canGenerate ? { y: -1 } : {}}
                      whileTap={canGenerate ? { y: 1 } : {}}
                    >
                      {generating ? (
                        <>
                          <span className="setup__spinner" />
                          Generating…
                        </>
                      ) : (
                        <>
                          <Icon name="sparkle" size={16} />
                          Generate report
                        </>
                      )}
                    </motion.button>
                  )}
                </div>
              </div>
            )}
          </motion.section>

          {/* ── Chat ────────────────────────────────────────────────── */}
          {isChat && (
            <>
              {chatHealth && chatHealth.available === false && (
                <Notice tone="error" title="SAR Q&A is unavailable">
                  {chatHealth.reason}
                </Notice>
              )}

              {!ready ? (
                <div className="report__idle">
                  <Icon name="sparkle" size={28} className="report__idle-icon" />
                  <p className="report__idle-text">
                    Select an entity and financial year to start asking questions.
                  </p>
                </div>
              ) : (
                <div className="chat" style={{ marginTop: '1rem' }}>
                  <div className="chat__scroll">
                    <div className="chat__inner">
                      <AnimatePresence mode="popLayout" initial={false}>
                        {chatThread.length === 0 && !chatPending && (
                          <motion.div
                            key="empty"
                            className="chat__empty"
                            initial={{ opacity: 0, y: 14 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0 }}
                          >
                            <div className="chat__empty-mark">
                              <Icon name="sparkle" size={26} />
                            </div>
                            <h2 className="chat__empty-title">
                              {entity} · {fyLabel}
                            </h2>
                            <p className="chat__empty-sub">
                              Ask anything about this auditor’s report.
                            </p>
                          </motion.div>
                        )}

                        {chatThread.map((msg) =>
                          msg.role === 'user' ? (
                            <motion.div
                              key={msg.id}
                              className="chat__user"
                              layout
                              initial={{ opacity: 0, y: 12 }}
                              animate={{ opacity: 1, y: 0 }}
                            >
                              <div className="chat__user-bubble">{msg.text}</div>
                            </motion.div>
                          ) : (
                            <motion.div
                              key={msg.id}
                              layout
                              initial={{ opacity: 0, y: 14 }}
                              animate={{ opacity: 1, y: 0 }}
                            >
                              <AnswerCard result={msg.result} />
                            </motion.div>
                          )
                        )}

                        {chatPending && (
                          <motion.div
                            key="pending"
                            layout
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                          >
                            <ProgressSteps steps={CHAT_STEPS} />
                          </motion.div>
                        )}

                        {chatError && (
                          <motion.div key="err" layout>
                            <Notice tone="error" title="Could not get an answer">
                              {chatError}
                            </Notice>
                          </motion.div>
                        )}
                      </AnimatePresence>
                      <div ref={chatEndRef} />
                    </div>
                  </div>
                  <Composer
                    onSubmit={submitChat}
                    disabled={chatPending}
                    placeholder={`Ask about ${entity}'s ${fyLabel} report…`}
                  />
                </div>
              )}
            </>
          )}

          {/* ── Report document ─────────────────────────────────────── */}
          {!isChat && (
            <AnimatePresence mode="wait">
              {generating && (
                <motion.div
                  key="progress"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
                >
                  <ProgressSteps steps={PIPELINE_STEPS} intervalMs={6000} />
                </motion.div>
              )}

              {!generating && error && (
                <motion.div
                  key="error"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                >
                  <Notice tone="error" title="Report generation failed">
                    {error}
                  </Notice>
                </motion.div>
              )}

              {!generating && report && (
                <motion.div
                  key={`${report.entity}-${report.fy_label}-${report.scope}`}
                  initial={{ opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                >
                  <ReportDocument report={report} />
                </motion.div>
              )}

              {!generating && !report && !error && !catalogError && (
                <motion.div
                  key="idle"
                  className="report__idle"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.35 }}
                >
                  <Icon name="doc" size={28} className="report__idle-icon" />
                  <p className="report__idle-text">
                    The generated memorandum will appear here.
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          )}
        </div>
      </div>
    </div>
  );
}
