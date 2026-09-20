import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import './ProgressSteps.css';

/**
 * Inline "thinking" status line, Claude-style: one shimmering phrase at a
 * time rather than a static checklist.
 *
 * Neither pipeline streams progress events, so this advances on a timer and
 * then holds on the final phrase until the response lands. The phrases
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
    <div className="thinking" role="status" aria-live="polite">
      <span className="thinking__glyph" aria-hidden="true">
        <span className="thinking__glyph-dot" />
      </span>

      <AnimatePresence mode="wait">
        <motion.span
          key={current}
          className="thinking__label"
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -5 }}
          transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
        >
          {steps[current]}
        </motion.span>
      </AnimatePresence>

      <span className="thinking__timer">{elapsed}s</span>
    </div>
  );
}
