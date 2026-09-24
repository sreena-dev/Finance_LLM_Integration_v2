import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import BrandMark from '../common/BrandMark';
import LedgerLoop from './LedgerLoop';
import './IngestProgress.css';

/**
 * Conversion progress, driven by real events -- a designed wait, not a spinner.
 *
 * A compact bar sits above the composer and the chat stays usable; clicking it
 * opens the ledger loop, the real stage in plain words, a live timer and a
 * rotating tip. Deliberately NOT `common/ProgressSteps.jsx`, which advances on a
 * timer because the chat pipelines stream nothing: converting a scan takes
 * minutes and is genuinely multi-stage, and a fake bar over that reads as a hang.
 *
 * The stage keys mirror ingestion/app/pipeline.py's `_STAGE_WEIGHTS`, which is
 * also where the uneven widths come from -- OCR and table structure are ~55% of
 * the work. "Finishing" has no event of its own: it is shown once the last
 * reported stage is nearly complete.
 */
const STAGES = [
  { key: 'render', label: 'Reading the PDF', say: 'Reading the PDF…' },
  { key: 'precheck', label: 'Checking page quality', say: 'Checking the quality of each page…' },
  { key: 'preprocess', label: 'Straightening pages', say: 'Straightening and cleaning the pages…' },
  { key: 'convert', label: 'Detecting layout and reading tables', say: 'Detecting layout and reading tables…' },
  { key: 'vlm', label: 'Second read by the vision model', say: 'Second read by the vision model…' },
  { key: 'verify', label: 'Verifying figures', say: 'Checking that every subtotal adds up…' },
  { key: 'identify', label: 'Identifying the document', say: 'Working out the entity, year and framework…' },
  { key: 'finish', label: 'Finishing', say: 'Finishing…' },
];

const TIPS = [
  'We never guess a figure. If it can’t be read, we tell you.',
  'A filing usually carries last year’s column too, so one upload answers a two-year question.',
  'Click any red or amber figure to enter it from the scan yourself.',
  'Every subtotal is checked by arithmetic before we rely on it.',
];

const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

/** Which of the eight stages is current, given the event and how far along it is. */
export function stageIndex(stage, fraction = 0) {
  if (stage === 'done') return STAGES.length;
  const i = STAGES.findIndex((s) => s.key === stage);
  if (i === STAGES.length - 2 && fraction >= 0.95) return STAGES.length - 1;
  return i;
}

/** A conversion failure in words a reviewer can act on. */
export function friendlyError(error) {
  const text = String(error || '');
  if (/password|encrypt/i.test(text)) {
    return 'This file is password-protected. Remove the password and attach it again.';
  }
  return text.replace(/^[^:]+\.pdf:\s*/i, '') || 'This file could not be converted.';
}

export default function IngestProgress({
  filename, stage, message, fraction = 0, error, status, ahead = 0, onRetry, onDismiss,
}) {
  const [open, setOpen] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [tip, setTip] = useState(0);

  const queued = status === 'queued' || !stage || stage === 'queued';
  const done = stage === 'done';
  const failed = Boolean(error);

  useEffect(() => {
    if (done || failed || queued) return undefined;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [done, failed, queued]);

  useEffect(() => {
    if (!open) return undefined;
    const t = setInterval(() => setTip((i) => (i + 1) % TIPS.length), 6000);
    return () => clearInterval(t);
  }, [open]);

  const index = stageIndex(stage, fraction);
  const current = STAGES[Math.max(0, Math.min(index, STAGES.length - 1))];
  const pct = Math.round((done ? 1 : fraction || 0) * 100);

  if (failed) {
    return (
      <div className="ingest is-error" role="alert">
        <Icon name="alert" size={16} className="ingest__erricon" />
        <div className="ingest__errbody">
          <span className="ingest__file" title={filename}>{filename}</span>
          <p className="ingest__msg">{friendlyError(error)}</p>
        </div>
        {onRetry && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry}>
            <Icon name="refresh" size={13} /> Retry
          </button>
        )}
        {onDismiss && (
          <button type="button" className="ingest__x" onClick={onDismiss} aria-label="Dismiss">×</button>
        )}
      </div>
    );
  }

  if (queued) {
    return (
      <div className="ingest ingest--queued">
        <Icon name="doc" size={14} />
        <span className="ingest__file" title={filename}>{filename}</span>
        <span className="pill pill--mute">
          Queued{ahead > 0 ? ` · ${ahead} ahead` : ''}
        </span>
      </div>
    );
  }

  return (
    <div className={`ingest ${open ? 'is-open' : ''}`}>
      <button type="button" className="ingest__bar" onClick={() => setOpen((v) => !v)}
              aria-expanded={open}>
        <span className="ingest__rupee" aria-hidden="true">₹</span>
        <span className="ingest__now">
          <strong>{done ? 'Done' : current.label}</strong>
          <span className="ingest__file" title={filename}> · {filename}</span>
        </span>
        <span className="ingest__time">{mmss(elapsed)}</span>
        <span className="ingest__track" role="progressbar" aria-valuenow={pct}
              aria-valuemin={0} aria-valuemax={100}>
          <motion.span className={`ingest__fill ${pct >= 100 ? 'is-done' : ''}`} initial={false}
                       animate={{ width: `${pct}%` }} transition={{ duration: 0.4, ease: 'easeOut' }} />
        </span>
        <span className="ingest__expand">{open ? 'Collapse' : 'Expand'}</span>
      </button>

      {open && (
        <div className="ingest__panel">
          <div className="ingest__marks"><BrandMark /></div>
          <LedgerLoop />
          <p className="ingest__step">
            <span className="pill pill--navy">Step {Math.min(index + 1, STAGES.length)} of {STAGES.length}</span>
            <span className="ingest__elapsed">{mmss(elapsed)} elapsed</span>
          </p>
          <h3 className="ingest__title">{done ? 'Ready' : current.say}</h3>
          <p className="ingest__msg">
            {message ? `${message.replace(/[.…]+$/, '')}. ` : 'This can take a few minutes for a long filing. '}
            You can close this and keep working; we will tell you when it is ready.
          </p>
          <p className="ingest__tip">Tip: {TIPS[tip]}</p>

          <ol className="ingest__steps">
            {STAGES.map((s, i) => {
              const state = done || i < index ? 'is-done' : i === index ? 'is-active' : 'is-todo';
              return (
                <li key={s.key} className={`ingest__stepitem ${state}`}>
                  <span className="ingest__dot" aria-hidden="true">
                    {state === 'is-done' ? <Icon name="check" size={11} strokeWidth={2.6} /> : i + 1}
                  </span>
                  {s.label}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
