import { motion } from 'framer-motion';
import './IngestProgress.css';

/**
 * Conversion progress, driven by real events.
 *
 * Deliberately NOT `common/ProgressSteps.jsx`. That component advances on a
 * timer because neither chat pipeline streams progress — its own source says so
 * — and a timer is fine for a question answered in seconds. Converting a
 * 35-page scan takes minutes and is genuinely multi-stage, and a fake bar over
 * that reads as a hang: it reaches the last stage in half a minute and then
 * sits there while the user wonders whether to reload.
 *
 * The stage list mirrors ingestion/app/pipeline.py's `_STAGE_WEIGHTS`, which is
 * also where the uneven widths come from — OCR and table structure are ~55% of
 * the work, and giving every stage equal width would misrepresent where the
 * time goes just as badly as a timer does.
 */
const STAGES = [
  { key: 'render', label: 'Reading the PDF' },
  { key: 'precheck', label: 'Checking scan quality' },
  { key: 'preprocess', label: 'Straightening pages' },
  { key: 'convert', label: 'Detecting layout and tables' },
  { key: 'vlm', label: 'Second read' },
  { key: 'verify', label: 'Checking the arithmetic' },
  { key: 'identify', label: 'Reading year and framework' },
];

export default function IngestProgress({ filename, stage, message, fraction = 0, error }) {
  const index = STAGES.findIndex((s) => s.key === stage);
  const done = stage === 'done';

  return (
    <div className={`ingest ${error ? 'is-error' : ''}`}>
      <div className="ingest__head">
        <span className="ingest__file" title={filename}>{filename}</span>
        <span className="ingest__pct">
          {error ? 'failed' : done ? 'done' : `${Math.round((fraction || 0) * 100)}%`}
        </span>
      </div>

      <div className="ingest__track" role="progressbar"
           aria-valuenow={Math.round((fraction || 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <motion.div
          className="ingest__bar"
          initial={false}
          animate={{ width: `${Math.round((done ? 1 : fraction || 0) * 100)}%` }}
          transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>

      <p className="ingest__msg">{error || message || 'Queued…'}</p>

      <ol className="ingest__steps">
        {STAGES.map((s, i) => {
          const state = done || (index > -1 && i < index)
            ? 'is-done'
            : i === index ? 'is-active' : 'is-todo';
          return (
            <li key={s.key} className={`ingest__step ${state}`}>
              <span className="ingest__dot" aria-hidden="true" />
              <span className="ingest__step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
