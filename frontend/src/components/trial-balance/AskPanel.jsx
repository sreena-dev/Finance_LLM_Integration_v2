import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { tbAsk } from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import Composer from '../chat/Composer';
import './AskPanel.css';

const PIPELINE_STEPS = [
  'Running deterministic checks against the trial balance',
  'Retrieving relevant Ind AS excerpts',
  'Reasoning over the computed figures',
];

const SUGGESTIONS = [
  'Summarise this trial balance and flag anything unusual.',
  'What does the ratio analysis show for liquidity and solvency?',
  'Does the trial balance tie out, and are there any variances worth investigating?',
];

function SourcePill({ source }) {
  const label =
    source.label || (source.source === 'ind_as' ? `Ind AS ${source.standard_number}` : source.source);
  return (
    <div className="tbask-source">
      <span className="pill pill--mute">{source.source}</span>
      <span className="tbask-source__label">{label}</span>
      {source.snippet && <p className="tbask-source__snippet">{source.snippet}</p>}
    </div>
  );
}

/** Conversational Q&A over one stored trial balance, with per-session follow-up memory. */
export default function AskPanel({ mode, doc, sessionId, thread, setThread }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [thread.length, pending]);

  async function submit(text) {
    const question = text.trim();
    if (!question || pending) return;

    setError(null);
    setPending(true);
    setThread((prev) => [...prev, { role: 'user', text: question, id: `u-${Date.now()}` }]);

    try {
      const result = await tbAsk(mode, { docId: doc.doc_id, question, sessionId });
      setThread((prev) => [...prev, { role: 'assistant', result, id: `a-${Date.now()}` }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  const isEmpty = thread.length === 0;

  return (
    <div className="tbask">
      <div className="tbask__scroll">
        <div className="tbask__inner">
          <AnimatePresence mode="popLayout" initial={false}>
            {isEmpty && !pending && (
              <motion.div
                key="empty"
                className="tbask__empty"
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
              >
                <Icon name="sparkle" size={24} className="tbask__empty-icon" />
                <h3 className="tbask__empty-title">Ask about "{doc.filename}"</h3>
                <p className="tbask__empty-sub">
                  Grounded in deterministic tie-out, classification, ratio and
                  variance figures computed straight from this trial balance —
                  never estimated by the model.
                </p>
                <div className="tbask__suggestions">
                  {SUGGESTIONS.map((s, i) => (
                    <motion.button
                      key={s}
                      type="button"
                      className="tbask__suggestion"
                      onClick={() => submit(s)}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.1 + i * 0.07, duration: 0.35 }}
                      whileHover={{ y: -2 }}
                    >
                      <Icon name="search" size={14} />
                      <span>{s}</span>
                    </motion.button>
                  ))}
                </div>
              </motion.div>
            )}

            {thread.map((msg) =>
              msg.role === 'user' ? (
                <motion.div
                  key={msg.id}
                  className="tbask__user"
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                >
                  <div className="tbask__user-bubble">{msg.text}</div>
                </motion.div>
              ) : (
                <motion.div
                  key={msg.id}
                  layout
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                >
                  <article className="tbask-answer card">
                    <Markdown>{msg.result.answer}</Markdown>
                    {msg.result.sources?.length > 0 && (
                      <div className="tbask-answer__sources">
                        {msg.result.sources.map((s, i) => (
                          <SourcePill key={i} source={s} />
                        ))}
                      </div>
                    )}
                  </article>
                </motion.div>
              )
            )}

            {pending && (
              <motion.div key="pending" layout initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <ProgressSteps steps={PIPELINE_STEPS} intervalMs={3200} />
              </motion.div>
            )}

            {error && (
              <motion.div key="err" layout>
                <Notice tone="error" title="The request could not be completed">{error}</Notice>
              </motion.div>
            )}
          </AnimatePresence>
          <div ref={endRef} />
        </div>
      </div>

      <Composer onSubmit={submit} disabled={pending} placeholder="Ask about this trial balance…" />
    </div>
  );
}
