import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { runQuery } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import Composer from './Composer';
import AnswerCard from './AnswerCard';
import UploadPanel from '../ingestion/UploadPanel';
import ErrorBoundary from '../common/ErrorBoundary';
import './ChatView.css';

const PIPELINE_STEPS = [
  'Interpreting the question',
  'Searching the knowledge base',
  'Reranking retrieved evidence',
  'Reasoning over the evidence',
  'Validating citations',
];

const SUGGESTIONS = {
  'financial-statement': [
    'What disclosures does Ind AS 115 require for revenue from contracts with customers?',
    'Summarise the going-concern assessment requirements under SA 570.',
    'How should leases be presented in the balance sheet under Ind AS 116?',
  ],
  'financial-diagnostic-report': [
    'Assess liquidity and solvency trends over the last three years.',
    'Which ratios indicate elevated audit risk this year?',
    'Summarise working-capital movement and its drivers.',
  ],
};

/**
 * Chat surface for the query-driven modes.
 *
 * Conversation state is owned per mode by App, so switching modes and coming
 * back preserves that mode's thread instead of replaying it against a
 * different pipeline.
 */
export default function ChatView({
  mode,
  health,
  thread,
  setThread,
  // Optional: only Financial Statements persists a thread. Absent, this
  // component behaves exactly as it did before conversations existed.
  conversationId = null,
  onConversationChange,
  // Opens the document pane for one document. Only wired for FS — see the
  // `mode.id === 'financial-statement'` guard below, same one `UploadPanel`
  // itself is already gated behind.
  onViewDocument,
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const endRef = useRef(null);

  // The upload panel publishes `accept(files)` here so the composer's attach
  // button and the pane's drop handler can both push files in without either
  // of them owning the queue.
  const uploadRef = useRef(null);
  const [uploadState, setUploadState] = useState({ busy: false, available: true });
  const onUploadReady = useCallback((api) => {
    uploadRef.current = api;
    setUploadState((prev) => (
      prev.busy === api.busy && prev.available === api.available
        ? prev
        : { busy: api.busy, available: api.available }
    ));
  }, []);

  // Drag depth, not a boolean: dragenter/dragleave fire for every child element
  // the pointer crosses, so a boolean flickers off the moment the cursor moves
  // over a message inside the pane.
  const [dragDepth, setDragDepth] = useState(0);
  const canUpload = mode.id === 'financial-statement' && uploadState.available;

  const dropFiles = useCallback((files) => {
    setDragDepth(0);
    if (canUpload) uploadRef.current?.accept(files);
  }, [canUpload]);

  const blocked = health && health.available === false;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [thread.length, pending]);

  async function submit(text) {
    const query = text.trim();
    if (!query || pending) return;

    setError(null);
    setPending(true);
    setThread((prev) => [...prev, { role: 'user', text: query, id: `u-${Date.now()}` }]);

    try {
      const result = await runQuery(mode, query, conversationId);
      setThread((prev) => [
        ...prev,
        { role: 'assistant', result, id: `a-${Date.now()}` },
      ]);
      // The server assigns the id on the first turn of a new conversation; the
      // next question sends it back so the thread continues rather than
      // starting over.
      if (result?.conversation_id && result.conversation_id !== conversationId) {
        onConversationChange?.(result.conversation_id);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  const isEmpty = thread.length === 0;

  return (
    <div
      className={`chat ${dragDepth > 0 ? 'is-dropping' : ''}`}
      // Drop anywhere in the conversation, the way every chat tool works —
      // rather than aiming at a panel. preventDefault on dragOver is what stops
      // the browser navigating to the dropped file instead of handing it over,
      // which looks like the app crashing.
      onDragEnter={(e) => { if (canUpload && e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); setDragDepth((d) => d + 1); } }}
      onDragOver={(e) => { if (canUpload && dragDepth > 0) e.preventDefault(); }}
      onDragLeave={(e) => { if (canUpload && dragDepth > 0) { e.preventDefault(); setDragDepth((d) => Math.max(0, d - 1)); } }}
      onDrop={(e) => {
        if (!canUpload) return;
        e.preventDefault();
        dropFiles(e.dataTransfer?.files);
      }}
    >
      {dragDepth > 0 && canUpload && (
        <div className="chat__dropveil" aria-hidden="true">
          <Icon name="upload" size={22} />
          <span>Drop financial statements to attach them to this conversation</span>
          <small>PDF only</small>
        </div>
      )}

      <div className="chat__scroll" ref={scrollRef}>
        <div className="chat__inner">
          <AnimatePresence mode="popLayout" initial={false}>
            {isEmpty && !pending && (
              <motion.div
                key="empty"
                className="chat__empty"
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
              >
                <motion.div
                  className="chat__empty-mark"
                  initial={{ scale: 0.86, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ delay: 0.06, duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                >
                  <Icon name="sparkle" size={26} />
                </motion.div>

                <h2 className="chat__empty-title">{mode.label}</h2>
                <p className="chat__empty-sub">{mode.description}</p>

                {!blocked && (
                  <div className="chat__suggestions">
                    {(SUGGESTIONS[mode.id] || []).map((s, i) => (
                      <motion.button
                        key={s}
                        type="button"
                        className="chat__suggestion"
                        onClick={() => submit(s)}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{
                          delay: 0.16 + i * 0.07,
                          duration: 0.38,
                          ease: [0.22, 1, 0.36, 1],
                        }}
                        whileHover={{ y: -2 }}
                      >
                        <Icon name="search" size={15} className="chat__suggestion-icon" />
                        <span>{s}</span>
                      </motion.button>
                    ))}
                  </div>
                )}
              </motion.div>
            )}

            {thread.map((msg) =>
              msg.role === 'user' ? (
                <motion.div
                  key={msg.id}
                  className="chat__user"
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
                >
                  <div className="chat__user-bubble">{msg.text}</div>
                </motion.div>
              ) : (
                <motion.div
                  key={msg.id}
                  layout
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
                >
                  <ErrorBoundary
                    label="This answer could not be displayed"
                    resetKey={msg.id}
                  >
                    <AnswerCard
                      result={msg.result}
                      mode={mode}
                      conversationId={conversationId}
                    />
                  </ErrorBoundary>
                </motion.div>
              )
            )}

            {pending && (
              <motion.div
                key="pending"
                layout
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <ProgressSteps steps={PIPELINE_STEPS} />
              </motion.div>
            )}

            {error && (
              <motion.div key="err" layout>
                <Notice tone="error" title="The request could not be completed">
                  {error}
                </Notice>
              </motion.div>
            )}
          </AnimatePresence>

          <div ref={endRef} />
        </div>
      </div>

      {/* Upload lives beside the composer rather than in its own mode: the
          documents are the context for the very next question, and a separate
          surface would make the user navigate away from the thread to add one.
          Only the FS mode has it — the gateway reports the route, and every
          other mode's base_path has no /upload. */}
      {mode.id === 'financial-statement' && (
        <ErrorBoundary label="The upload panel could not be displayed" resetKey={conversationId}>
          <UploadPanel
            mode={mode}
            conversationId={conversationId}
            onConversationChange={onConversationChange}
            onReady={onUploadReady}
            onView={onViewDocument}
          />
        </ErrorBoundary>
      )}

      <Composer
        onSubmit={submit}
        disabled={pending || blocked}
        placeholder={
          blocked
            ? 'This mode is unavailable — see the notice above.'
            : `Ask about ${mode.short_label.toLowerCase()}…`
        }
        onFiles={canUpload ? dropFiles : undefined}
        attachBusy={uploadState.busy}
        attachTitle={
          uploadState.busy
            ? 'Converting a document…'
            : 'Attach financial statements (PDF)'
        }
      />
    </div>
  );
}
