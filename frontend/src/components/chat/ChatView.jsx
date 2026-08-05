import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { runQuery } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import Composer from './Composer';
import AnswerCard from './AnswerCard';
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
export default function ChatView({ mode, health, thread, setThread }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const endRef = useRef(null);

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
      const result = await runQuery(mode, query);
      setThread((prev) => [
        ...prev,
        { role: 'assistant', result, id: `a-${Date.now()}` },
      ]);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  const isEmpty = thread.length === 0;

  return (
    <div className="chat">
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
                  <AnswerCard result={msg.result} />
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

      <Composer
        onSubmit={submit}
        disabled={pending || blocked}
        placeholder={
          blocked
            ? 'This mode is unavailable — see the notice above.'
            : `Ask about ${mode.short_label.toLowerCase()}…`
        }
      />
    </div>
  );
}
