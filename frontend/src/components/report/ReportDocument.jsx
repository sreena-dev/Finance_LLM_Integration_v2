import { useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import CopyButton from '../common/CopyButton';
import './ReportDocument.css';
// The format-switcher tabs below reuse ReportView's .scope/.scope__* segmented-
// control classes (same idiom as its own Report/Chat toggle). Imported
// explicitly rather than relying on ReportView.jsx having already loaded it —
// that's only true because this component currently always renders as
// ReportView's child; see ReportView.jsx's own identical note about ChatView.css
// for why that assumption isn't one to build on silently.
import './ReportView.css';

// The three formats LLM_Output_Specification_CAG_Statutory_Auditor_Report_
// Review.md requires (§2/§3/§4), in the order a reviewer would actually want
// them — shortest/most-material first. `report_md` (the pre-existing writer
// narrative, PART 1/2) is kept as a last-resort fallback only: a response
// from before this field set existed, or a run where the newer fields came
// back empty for some other reason, still has *something* to show rather
// than a blank sheet.
const FORMAT_TABS = [
  { key: 'display', label: 'Display Response', field: 'display_response', mono: true },
  { key: 'executive', label: 'Executive Summary', field: 'executive_summary' },
  { key: 'detailed', label: 'Detailed Report', field: 'detailed_report' },
  { key: 'legacy', label: 'Full Narrative', field: 'report_md' },
];

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

function download(report, activeTab, content) {
  const name = `SAR_${report.entity}_${report.fy_label}_${report.scope}_${activeTab.key}`
    .replace(/[^\w-]+/g, '_')
    .replace(/_+/g, '_');
  const blob = new Blob([content || ''], {
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

// Word export is always the Detailed Report (§4) specifically, independent of
// whichever format tab is currently active — a reviewer opening this in Word
// wants the full 16-section document, not whatever they happened to be
// looking at on screen. Built as an HTML-as-.doc file (the mso conditional
// comment + office/word XML namespaces are what make Word open it as a
// native document rather than warning about a foreign format) so no new
// docx-generation dependency is needed — `bodyHtml` is the already-rendered
// react-markdown output for detailed_report, captured via a hidden ref.
function downloadWord(report, bodyHtml) {
  const name = `SAR_${report.entity}_${report.fy_label}_${report.scope}_detailed_report`
    .replace(/[^\w-]+/g, '_')
    .replace(/_+/g, '_');
  const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<title>${name}</title>
<!--[if gte mso 9]>
<xml>
  <w:WordDocument>
    <w:View>Print</w:View>
    <w:DoNotOptimizeForBrowser/>
  </w:WordDocument>
</xml>
<![endif]-->
<style>
  /* Matches src/styles/index.css's --font-sans/--font-serif/--font-mono and
     its global "h1, h2, h3 { font-family: var(--font-serif); color:
     var(--navy-700); }" rule, plus ReportDocument.css's .doc__md rules
     (paragraphs/list items serif, tables sans, code/pre mono) — a Word
     document can't read CSS custom properties from another file, so each
     stack is restated here with the same fallback chain. Word only renders
     whichever of these is actually installed on the reader's machine and
     falls back through the chain, same as a browser would. */
  body {
    font-family: "Noto Sans", "Noto Sans Devanagari", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt; line-height: 1.5;
  }
  p, li {
    font-family: "Source Serif 4", "Noto Serif Devanagari", "Iowan Old Style", Georgia, serif;
  }
  h1, h2, h3 {
    font-family: "Source Serif 4", "Noto Serif Devanagari", "Iowan Old Style", Georgia, serif;
    color: #14285a;
  }
  h1 { font-size: 20pt; }
  h2 { font-size: 16pt; margin-top: 20pt; }
  h3 { font-size: 13pt; }
  table {
    border-collapse: collapse; width: 100%;
    font-family: "Noto Sans", "Noto Sans Devanagari", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }
  table, th, td { border: 1px solid #999; padding: 4pt 8pt; }
  th { background: #f0f0f0; text-align: left; }
  code, pre {
    font-family: "IBM Plex Mono", "SF Mono", "Cascadia Code", Consolas, monospace;
  }
</style>
</head>
<body>${bodyHtml || '<p>The pipeline returned an empty detailed report.</p>'}</body>
</html>`;

  const blob = new Blob(['﻿', html], { type: 'application/msword' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${name}.doc`;
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
    observations = [],
    quality_flags: flags = {},
    doc_meta: meta = {},
    elapsed_seconds: elapsed,
  } = report;

  const flagEntries = Object.entries(flags);

  // Only offer a tab for a format the pipeline actually returned something
  // for — an older cached response (before display_response/executive_
  // summary/detailed_report existed) still shows its report_md under "Full
  // Narrative" rather than three empty tabs and a fourth with content.
  const availableTabs = useMemo(
    () => FORMAT_TABS.filter((tab) => (report[tab.field] || '').trim().length > 0),
    [report]
  );

  // Default to the most complete format (Detailed Report) when it's present;
  // otherwise whatever the first available tab is.
  const defaultTab =
    availableTabs.find((tab) => tab.key === 'detailed') || availableTabs[0];
  const [activeKey, setActiveKey] = useState(defaultTab?.key);
  const activeTab = availableTabs.find((tab) => tab.key === activeKey) || defaultTab;
  const activeContent = activeTab ? report[activeTab.field] || '' : '';

  // Hidden render target for the Word export: keeps a rendered copy of the
  // Detailed Report in the DOM regardless of which tab is active, so its
  // innerHTML (real <h1>/<table>/etc markup, not raw markdown) is always
  // available to downloadWord() on click.
  const detailedRef = useRef(null);

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
          <CopyButton text={activeContent} label="Copy" />
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => download(report, activeTab, activeContent)}
            disabled={!activeContent}
          >
            <Icon name="download" size={14} />
            Download
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => downloadWord(report, detailedRef.current?.innerHTML)}
            disabled={!(report.detailed_report || '').trim()}
            title="Download the Detailed Report (§4) as a Word document"
          >
            <Icon name="doc" size={14} />
            Download Word
          </button>
        </div>
      </header>

      {/* Off-screen, always-rendered copy of the Detailed Report used only as
          an HTML source for the Word export above — not shown to the user,
          and independent of whichever format tab is currently selected. */}
      {(report.detailed_report || '').trim() && (
        <div style={{ display: 'none' }} aria-hidden="true">
          <div ref={detailedRef}>
            <Markdown>{report.detailed_report}</Markdown>
          </div>
        </div>
      )}

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
            Observations
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

      {/* ── Format switcher ─────────────────────────────────────────────
          LLM_Output_Specification_CAG_Statutory_Auditor_Report_Review.md
          §2/§3/§4: three distinct outputs generated from the same
          observation register, not one narrative — this is what lets a
          reviewer actually reach all three instead of only ever seeing
          report_md (which is what this view rendered exclusively before,
          regardless of what the API returned alongside it). */}
      {availableTabs.length > 1 && (
        <div className="scope doc__format-tabs">
          <span className="scope__label">Output format</span>
          <div className="scope__group" role="radiogroup" aria-label="Report output format">
            {availableTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="radio"
                aria-checked={activeTab?.key === tab.key}
                className={`scope__btn ${activeTab?.key === tab.key ? 'is-on' : ''}`}
                onClick={() => setActiveKey(tab.key)}
              >
                {activeTab?.key === tab.key && (
                  <motion.span
                    className="scope__bg"
                    layoutId="sar-format-active"
                    transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                  />
                )}
                <span className="scope__text">{tab.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── The memorandum ───────────────────────────────────────────── */}
      <section className="doc__sheet">
        {!activeContent ? (
          <p className="doc__blank">The pipeline returned an empty report.</p>
        ) : activeTab?.mono ? (
          // display_response is plain structured text (§2), not markdown —
          // a <pre> preserves the indentation the format relies on to read
          // as sections, which a markdown renderer would collapse.
          <pre className="doc__plain">{activeContent}</pre>
        ) : (
          <Markdown className="doc__md">{activeContent}</Markdown>
        )}
      </section>
    </div>
  );
}
