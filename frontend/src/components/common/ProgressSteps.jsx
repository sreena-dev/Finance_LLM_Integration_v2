import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import Icon from './Icon';
import './ProgressSteps.css';

/**
 * Staged progress indicator.
 *
 * Neither pipeline streams progress events, so this advances on a timer and
 * then holds on the final stage until the response lands. The stage names
 * mirror the real pipeline steps, and the elapsed counter is live, so the
 * component never claims completion it can't observe.
 */
export default function ProgressSteps({ steps, intervalMs = 4200 }) {
  const [current, setCurrent] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const tick = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, []);

  useEffect(() => {
    if (current >= steps.length - 1) return;
    const t = setTimeout(() => setCurrent((i) => i + 1), intervalMs);
    return () => clearTimeout(t);
  }, [current, steps.length, intervalMs]);

  return (
    <div className="steps">
      <div className="steps__head">
        <span className="steps__pulse" />
        <span className="steps__title">Running pipeline</span>
        <span className="steps__timer">{elapsed}s</span>
      </div>

      <ol className="steps__list">
        {steps.map((label, i) => {
          const state = i < current ? 'done' : i === current ? 'active' : 'todo';
          return (
            <motion.li
              key={label}
              className={`steps__item is-${state}`}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.06, duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            >
              <span className="steps__marker">
                {state === 'done' ? (
                  <Icon name="check" size={12} strokeWidth={2.4} />
                ) : state === 'active' ? (
                  <span className="steps__spinner" />
                ) : (
                  <span className="steps__dot" />
                )}
              </span>
              <span className="steps__label">{label}</span>
            </motion.li>
          );
        })}
      </ol>
    </div>
  );
}
