import { useState } from 'react';
import Icon from '../common/Icon';
import QualityReport from './QualityReport';
import './DocumentChips.css';

/**
 * The documents attached to this conversation, as one compact row.
 *
 * Replaces a full-width dropzone that sat permanently between the thread and
 * the composer. That panel was always on screen whether or not anything had
 * been uploaded, ate vertical space in the conversation area, and pushed the
 * input down the page — the attach control belongs in the composer, and what
 * belongs here is only the result.
 *
 * A chip carries the two things worth seeing at a glance: which year the
 * document turned out to be, and whether any figures were withheld. The full
 * quality report — page grades, unreadable cells, failed footings, which
 * lookups cannot work — opens on click, because it is reference material rather
 * than something to keep on screen.
 */

const GRADE_TONE = {
  excellent: 'is-ok',
  good: 'is-ok',
  fair: 'is-warn',
  poor: 'is-bad',
};

export default function DocumentChips({ docs, onDelete, onView }) {
  const [openId, setOpenId] = useState(null);
  if (!docs || docs.length === 0) return null;

  const open = docs.find((d) => d.doc_id === openId) || null;

  return (
    <div className="chips">
      <div className="chips__row">
        {docs.map((doc) => {
          const withheld = (doc.quality?.unreadable_cells || []).length;
          const unavailable = (doc.coverage?.unavailable || []).length;
          const label = doc.financial_year || doc.filename;
          const isOpen = doc.doc_id === openId;

          return (
            <span
              key={doc.doc_id}
              className={`chip ${GRADE_TONE[doc.grade] || ''} ${isOpen ? 'is-open' : ''}`}
            >
              <button
                type="button"
                className="chip__main"
                onClick={() => setOpenId(isOpen ? null : doc.doc_id)}
                title={`${doc.filename}${doc.company ? ` — ${doc.company}` : ''}`}
                aria-expanded={isOpen}
              >
                <Icon name="doc" size={13} />
                <span className="chip__label">{label}</span>

                {/* Both counts are reasons NOT to trust a silence in the answer,
                    so they sit on the chip rather than behind a click. */}
                {withheld > 0 && (
                  <span className="chip__badge" title={`${withheld} figure(s) withheld as unreadable`}>
                    {withheld}
                  </span>
                )}
                {unavailable > 0 && (
                  <span
                    className="chip__badge is-muted"
                    title={`${unavailable} lookup(s) cannot work on this document`}
                  >
                    !
                  </span>
                )}
              </button>

              {/* Separate from the chip's own click, which toggles the inline
                  quality report above -- this opens the fuller document pane
                  instead, and the two are deliberately different actions on
                  the same chip rather than one click doing double duty. */}
              {onView && (
                <button
                  type="button"
                  className="chip__view"
                  onClick={() => onView(doc)}
                  title="View the processed pages"
                  aria-label={`View ${doc.filename}`}
                >
                  <Icon name="eye" size={12} />
                </button>
              )}

              {onDelete && (
                <button
                  type="button"
                  className="chip__del"
                  onClick={() => {
                    if (isOpen) setOpenId(null);
                    onDelete(doc.doc_id);
                  }}
                  title="Remove this document from the conversation"
                  aria-label={`Remove ${doc.filename}`}
                >
                  <Icon name="trash" size={12} />
                </button>
              )}
            </span>
          );
        })}

        <span className="chips__note">
          held in memory for this conversation only
        </span>
      </div>

      {open && (
        <div className="chips__detail">
          <QualityReport doc={open} />
        </div>
      )}
    </div>
  );
}
