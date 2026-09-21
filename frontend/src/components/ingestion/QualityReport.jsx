import { useEffect } from 'react';
import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import './QualityReport.css';

/**
 * What the extraction could and could not read, as a drawer over the chat.
 *
 * This is the surface for the spec's section 4.3 (input-quality checks before
 * analysis) and 15.3 (an amount that cannot be read is an extraction issue, not
 * a financial one). Its most important job is the withheld-figures list: the
 * point is not to reassure, it is to let an auditor see exactly which numbers
 * the system refused to vouch for, at which page and row, before trusting
 * anything computed from the rest. Each withheld or recovered figure has a
 * button that jumps straight to that cell in the document pane.
 */

const GRADE_TONE = { excellent: 'is-ok', good: 'is-ok', fair: 'is-warn', poor: 'is-err' };
const GRADE_TEXT = {
  excellent: 'This filing read very cleanly.',
  good: 'This filing read cleanly.',
  fair: 'Most of this filing can be relied on.',
  poor: 'Several pages read badly. Check figures against the original.',
};

/** Reason codes from verify.py, in words a reviewer can act on. */
export function reasonText(code) {
  const c = String(code || '');
  if (c === 'figures_not_extracted') return 'The page shows a figure the table left empty';
  if (c === 'unreadable_text') return 'Smudged or unreadable digits';
  if (c === 'readers_disagree') return 'Read differently by the two readers';
  if (c === 'unsupported_by_ocr') return 'Not backed by the page text';
  if (c === 'no_second_read_for_row') return 'No second read of this row';
  if (c === 'vlm_only_row') return 'Only the second reader saw this row';
  if (c.startsWith('low_ocr_confidence')) return 'Low OCR confidence';
  return c ? c.replace(/_/g, ' ') : 'Could not be established';
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** "till 21 Oct 2026" from the upload time and the server's retention. */
export function keptUntil(uploadedAt, retentionDays) {
  if (!uploadedAt || !retentionDays) return null;
  const d = new Date((uploadedAt + retentionDays * 86400) * 1000);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Distinct tables that carry at least one withheld or recovered figure. */
export function tablesWithProblems(quality) {
  const ids = new Set();
  [...(quality.unreadable_cells || []), ...(quality.recovered_cells || [])].forEach((c) => {
    if (c.table_id) ids.add(c.table_id);
  });
  return ids.size;
}

function Section({ title, children }) {
  return (
    <section className="qd__section">
      <h3 className="section-label">{title}</h3>
      {children}
    </section>
  );
}

export default function QualityReport({
  doc, retentionDays = 30, onClose, onOpenPages, onEnter,
}) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!doc) return null;

  const ident = doc.identification || {};
  const quality = doc.quality || {};
  const coverage = doc.coverage || {};
  const withheld = quality.unreadable_cells || [];
  // `unreadable_cells` still lists an unconfirmed recovery (it is withheld until
  // confirmed), so the list of figures that need a person is only the ones with
  // NO second-read value; the rest are reviewed under "recovered".
  const unreadable = withheld.filter((c) => c.recovered_text == null);
  const recovered = withheld.filter((c) => c.recovered_text != null);
  const failed = quality.failed_footings || [];
  const paths = coverage.paths || [];
  const canAnswer = paths.filter((p) => p.state === 'viable');
  const cannotAnswer = paths.filter((p) => p.state !== 'viable');
  const periods = doc.periods || [];
  const tables = doc.tables || 0;
  const problemTables = Math.min(tables, tablesWithProblems(quality));
  const needYou = unreadable.length;
  const grade = doc.grade || 'unknown';
  const until = keptUntil(doc.uploaded_at, retentionDays);
  const weakest = doc.low_grade && doc.low_grade !== 'good' ? doc.low_grade : null;

  const meta = [
    doc.company, ident.financial_year && `FY ${ident.financial_year}`,
    doc.pages && plural(doc.pages, 'page', 'pages'),
    doc.tables != null && plural(doc.tables, 'table', 'tables'),
    until && `kept until ${until}, or until you delete this conversation`,
  ].filter(Boolean).join(' · ');

  return (
    <div className="qd" role="presentation">
      <div className="qd__scrim" onClick={onClose} />
      <motion.aside
        className="qd__drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Quality report"
        initial={{ x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: 0.28, ease: [0.3, 0.7, 0.2, 1] }}
      >
        <header className="qd__head">
          <div>
            <p className="qd__eyebrow">Quality report</p>
            <h2 className="qd__file" title={doc.filename}>{doc.filename}</h2>
            <p className="qd__meta">{meta}</p>
          </div>
          <button type="button" className="qd__close" onClick={onClose} aria-label="Close quality report">×</button>
        </header>

        <div className="qd__body">
          <div className={`qd__grade ${GRADE_TONE[grade] || ''}`}>
            <span className="qd__badge">{grade[0].toUpperCase() + grade.slice(1)}</span>
            <div>
              <p className="qd__lead">
                {GRADE_TEXT[grade] || 'Scan quality could not be graded.'}
                {needYou > 0 && ` ${plural(needYou, 'figure needs', 'figures need')} you.`}
              </p>
              <p className="qd__sub">
                {tables > 0 && `${tables - problemTables} of ${tables} tables were read cleanly. `}
                {weakest && `The weakest page is graded ${weakest}. `}
                We never guess a figure: anything we could not establish is withheld or marked.
              </p>
            </div>
          </div>

          {unreadable.length > 0 && (
            <Section title={`${plural(unreadable.length, 'figure', 'figures')} could not be read from the scan`}>
              <table className="qd__table">
                <thead><tr><th>Where</th><th>Item</th><th>Why</th><th /></tr></thead>
                <tbody>
                  {unreadable.map((c, i) => (
                    <tr key={`u${i}`}>
                      <td className="num">p.{c.page_no}{c.table_id ? ` · ${String(c.table_id).split('_').slice(-2).join('_')}` : ''}</td>
                      <td>{c.row_label}<span className="qd__col">{c.column}</span></td>
                      <td>{(c.reasons || []).map(reasonText)[0]}</td>
                      <td className="qd__act">
                        {onEnter && (
                          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onEnter(c)}>Enter</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}

          {recovered.length > 0 && (
            <Section title="Recovered, not yet confirmed">
              {recovered.map((c, i) => (
                <div key={`r${i}`} className="qd__rec">
                  <span className="fig fig--recovered"><span className="fig__label">recovered ?</span></span>
                  <span className="qd__recmain">
                    <strong>{c.row_label}</strong>, {c.column}, p.{c.page_no} ·{' '}
                    <span className="num">{c.recovered_text}</span>
                  </span>
                  {c.confidence && <span className="pill pill--warn">Confidence: {c.confidence}</span>}
                  {onEnter && (
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => onEnter(c)}>Review</button>
                  )}
                </div>
              ))}
            </Section>
          )}

          {failed.length > 0 && (
            <Section title="Printed totals that did not add up">
              {failed.map((f, i) => (
                <p key={i} className="qd__warn">
                  <Icon name="alert" size={14} />
                  <span>
                    <strong>Page {f.page_no}: {f.subtotal_label}.</strong>{' '}
                    {f.recomputed != null
                      ? <>The rows add to <span className="num">{f.recomputed.toLocaleString('en-IN')}</span> but the printed total is <span className="num">{f.printed != null ? f.printed.toLocaleString('en-IN') : '—'}</span>. Shown as printed; answers will say so.</>
                      : <>The components could not be summed. Shown as printed.</>}
                  </span>
                </p>
              ))}
            </Section>
          )}

          {paths.length > 0 && (
            <Section title="What this document can answer">
              <div className="qd__cols">
                <div className="qd__box qd__box--ok">
                  <p className="qd__boxh"><Icon name="check" size={13} /> Can answer</p>
                  <ul>{canAnswer.map((p) => <li key={p.name}>{p.name}</li>)}</ul>
                </div>
                <div className="qd__box qd__box--no">
                  <p className="qd__boxh"><Icon name="alert" size={13} /> Cannot answer</p>
                  {cannotAnswer.length === 0
                    ? <p className="qd__none">Nothing is ruled out.</p>
                    : <ul>{cannotAnswer.map((p) => <li key={p.name}>{p.name}{p.detail ? ` — ${p.detail}` : ''}</li>)}</ul>}
                </div>
              </div>
            </Section>
          )}

          {periods.length > 0 && (
            <Section title="Periods in the data">
              <div className="qd__periods">
                {periods.map((p) => (
                  <span key={p.label} className="pill pill--navy">
                    {p.label} · {p.is_comparative ? 'prior-year column' : 'current'}
                  </span>
                ))}
              </div>
              {periods.length > 1 && (
                <p className="qd__hint">A single filing carries its prior-year column, so this one upload answers a two-year question.</p>
              )}
            </Section>
          )}

          {((quality.notes || []).length > 0 || !quality.vlm_used) && (
            <Section title="Notes from the reader">
              <ul className="qd__notes">
                {!quality.vlm_used && (
                  <li>No second independent read was available, so each figure rests on one reader plus its own arithmetic.</li>
                )}
                {(quality.notes || []).map((n, i) => <li key={i}>{n}</li>)}
                {(ident.unresolved_conflicts || []).map((n, i) => <li key={`c${i}`}>{n}</li>)}
              </ul>
            </Section>
          )}
        </div>

        <footer className="qd__foot">
          <span>Uploaded documents last {retentionDays} days and are removed with their conversation.</span>
          {onOpenPages && (
            <button type="button" className="btn btn--primary btn--sm" onClick={onOpenPages}>Open pages</button>
          )}
        </footer>
      </motion.aside>
    </div>
  );
}
