import { useState } from 'react';
import Icon from '../common/Icon';
import QualityReport from './QualityReport';
import './DocumentChips.css';

/**
 * The documents attached to this conversation: one card each, with the numbers
 * worth seeing at a glance.
 *
 * A card carries which entity and year the document turned out to be, its scan
 * quality, and the counters that are reasons NOT to trust a silence in an answer:
 * figures withheld, figures recovered but unconfirmed, totals that did not foot,
 * and figures a person entered. The full quality report opens as a drawer,
 * because it is reference material rather than something to keep on screen.
 */

const GRADE_TONE = {
  excellent: 'pill--ok',
  good: 'pill--ok',
  fair: 'pill--warn',
  poor: 'pill--err',
};

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export default function DocumentChips({ docs, onDelete, onView, retentionDays = 30 }) {
  const [openId, setOpenId] = useState(null);
  if (!docs || docs.length === 0) return null;

  const open = docs.find((d) => d.doc_id === openId) || null;

  return (
    <div className="chips">
      {docs.map((doc) => {
        const quality = doc.quality || {};
        const withheld = (quality.unreadable_cells || []).length;
        const recovered = doc.recovered_cells || 0;
        const footing = (quality.failed_footings || []).length;
        const entered = doc.user_entered_cells || 0;
        const grade = doc.grade;

        return (
          <article key={doc.doc_id} className="docchip">
            <header className="docchip__head">
              <Icon name="doc" size={15} className="docchip__icon" />
              <span className="docchip__file" title={doc.filename}>{doc.filename}</span>
              <span className="docchip__meta">
                {[doc.company, doc.financial_year && `FY ${doc.financial_year}`,
                  doc.pages && plural(doc.pages, 'page', 'pages'),
                  doc.tables != null && plural(doc.tables, 'table', 'tables')]
                  .filter(Boolean).join(' · ')}
              </span>
              {onDelete && (
                <button type="button" className="docchip__del" aria-label={`Remove ${doc.filename}`}
                        title="Remove this document from the conversation"
                        onClick={() => { if (openId === doc.doc_id) setOpenId(null); onDelete(doc.doc_id); }}>
                  <Icon name="trash" size={13} />
                </button>
              )}
            </header>

            <div className="docchip__pills">
              <span className={`pill ${GRADE_TONE[grade] || 'pill--mute'}`}>
                Scan quality: {grade || 'unknown'}
                {doc.low_grade && doc.low_grade !== grade && <> · weakest {doc.low_grade}</>}
              </span>
              {doc.tables === 0 && <span className="pill pill--mute">0 tables — text only</span>}
              {withheld > 0 && (
                <span className="pill pill--err">
                  <Icon name="alert" size={12} /> {plural(withheld, 'figure', 'figures')} withheld
                </span>
              )}
              {recovered > 0 && <span className="pill pill--warn">{recovered} recovered</span>}
              {footing > 0 && (
                <span className="pill pill--warn">
                  {plural(footing, 'total', 'totals')} did not foot
                </span>
              )}
              {entered > 0 && <span className="pill pill--gold">{entered} entered by you</span>}
            </div>

            <div className="docchip__actions">
              {onView && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => onView(doc)}>
                  <Icon name="eye" size={13} /> Open pages
                </button>
              )}
              <button type="button" className="btn btn--ghost btn--sm"
                      aria-expanded={openId === doc.doc_id}
                      onClick={() => setOpenId(openId === doc.doc_id ? null : doc.doc_id)}>
                Quality report
              </button>
            </div>
          </article>
        );
      })}

      {open && (
        <QualityReport
          doc={open}
          retentionDays={retentionDays}
          onClose={() => setOpenId(null)}
          onOpenPages={onView ? () => { setOpenId(null); onView(open); } : undefined}
          onEnter={onView ? (cell) => { setOpenId(null); onView(open, cell); } : undefined}
        />
      )}
    </div>
  );
}
