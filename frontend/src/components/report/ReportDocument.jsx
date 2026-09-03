import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import CopyButton from '../common/CopyButton';
import './ReportDocument.css';

const FLAG_TONE = {
  reliable: 'ok',
  partial: 'warn',
  low_confidence: 'warn',
  not_found: 'err',
};

/** Observations arrive as strings or as tagged objects depending on the check. */
function observationText(obs) {
  if (typeof obs === 'string') return obs;
  if (obs && typeof obs === 'object') {
    return obs.message || obs.observation || obs.text || JSON.stringify(obs);
  }
  return String(obs);
}

function observationTone(obs) {
  const raw = (
    (obs && typeof obs === 'object' && (obs.severity || obs.tag || obs.level)) ||
    ''
  )
    .toString()
    .toLowerCase();

  if (raw.includes('high') || raw.includes('error') || raw.includes('fail')) return 'err';
  if (raw.includes('med') || raw.includes('warn') || raw.includes('caution')) return 'warn';
  if (raw.includes('low') || raw.includes('info') || raw.includes('ok')) return 'ok';
  return 'mute';
}

function humanise(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function download(report) {
  const name = `SAR_${report.entity}_${report.fy_label}_${report.scope}`
    .replace(/[^\w-]+/g, '_')
    .replace(/_+/g, '_');
  const blob = new Blob([report.report_md || ''], {
    type: 'text/markdown;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${name}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function ReportDocument({ report }) {
  const {
    entity,
    fy_label: fyLabel,
    scope,
    report_md: markdown,
    observations = [],
    quality_flags: flags = {},
    doc_meta: meta = {},
    elapsed_seconds: elapsed,
  } = report;

  const flagEntries = Object.entries(flags);

  return (
    <div className="doc">
      {/* ── Masthead ─────────────────────────────────────────────────── */}
      <header className="doc__head card">
        <div className="doc__head-main">
          <span className="pill pill--navy">
            <Icon name="seal" size={13} />
            Statutory Auditor's Report
          </span>

          <h2 className="doc__entity">{entity}</h2>

          <div className="doc__facts">
            <span className="doc__fact">
              <strong>{fyLabel}</strong>
            </span>
            <span className="doc__sep" />
            <span className="doc__fact">{humanise(scope)}</span>
            {meta.cin && (
              <>
                <span className="doc__sep" />
                <span className="doc__fact">
                  CIN <code>{meta.cin}</code>
                </span>
              </>
            )}
            {elapsed > 0 && (
              <>
                <span className="doc__sep" />
                <span className="doc__fact">generated in {elapsed.toFixed(1)}s</span>
              </>
            )}
          </div>

          {meta.doc_name && (
            <p className="doc__source">
              <Icon name="doc" size={13} />
              {meta.doc_name}
              {meta.total_pages ? ` · ${meta.total_pages} pages` : ''}
            </p>
          )}
        </div>

        <div className="doc__actions">
          <CopyButton text={markdown} label="Copy" />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => download(report)}
            disabled={!markdown}
          >
            <Icon name="download" size={14} />
            Download
          </button>
        </div>
      </header>

      {/* ── Retrieval quality ────────────────────────────────────────── */}
      {flagEntries.length > 0 && (
        <section className="doc__flags card">
          <h3 className="doc__section-title">Source retrieval quality</h3>
          <div className="doc__flag-grid">
            {flagEntries.map(([key, value], i) => (
              <motion.div
                key={key}
                className="flagcell"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.035, duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <span className="flagcell__name">{humanise(key)}</span>
                <span className={`pill pill--${FLAG_TONE[value] || 'mute'}`}>
                  <span className="dot" />
                  {humanise(String(value))}
                </span>
              </motion.div>
            ))}
          </div>
        </section>
      )}

      {/* ── Observations ─────────────────────────────────────────────── */}
      {observations.length > 0 && (
        <section className="doc__obs card">
          <h3 className="doc__section-title">
            Coherence observations
            <span className="pill pill--mute">{observations.length}</span>
          </h3>
          <ul className="obs__list">
            {observations.map((obs, i) => (
              <motion.li
                key={i}
                className={`obs obs--${observationTone(obs)}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.04, duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
              >
                <span className="obs__bar" />
                <span className="obs__text">{observationText(obs)}</span>
              </motion.li>
            ))}
          </ul>
        </section>
      )}

      {/* ── The memorandum ───────────────────────────────────────────── */}
      <section className="doc__sheet">
        {markdown ? (
          <Markdown className="doc__md">{markdown}</Markdown>
        ) : (
          <p className="doc__blank">The pipeline returned an empty report.</p>
        )}
      </section>
    </div>
  );
}
