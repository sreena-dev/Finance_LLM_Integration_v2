import { useState } from 'react';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './QualityReport.css';

/**
 * What the extraction could and could not read.
 *
 * This is the surface for the spec's section 4.3 (input-quality checks before
 * analysis) and 15.3 (an amount that cannot be read is an extraction issue, not
 * a financial one). Its most important job is the withheld-figures list: the
 * point is not to reassure, it is to let an auditor see exactly which numbers
 * the system refused to vouch for, at which page and row, before trusting
 * anything computed from the rest.
 */

// A retrieval path that cannot work on this document is not a warning about
// scan quality — it changes what a "not found" answer means. Given its own
// vocabulary so it does not read as another grade.
const COVERAGE_PILL = {
  viable: 'pill--ok',
  degraded: 'pill--warn',
  unavailable: 'pill--err',
};

const GRADE_PILL = {
  excellent: 'pill--ok',
  good: 'pill--ok',
  fair: 'pill--warn',
  poor: 'pill--err',
  unspecified: 'pill--mute',
};

function Grade({ value }) {
  return <span className={`pill ${GRADE_PILL[value] || 'pill--mute'}`}>{value || 'unknown'}</span>;
}

function Row({ label, children }) {
  return (
    <div className="qr__row">
      <dt className="qr__key">{label}</dt>
      <dd className="qr__val">{children}</dd>
    </div>
  );
}

export default function QualityReport({ doc, onDelete }) {
  const [open, setOpen] = useState(false);
  if (!doc) return null;

  const ident = doc.identification || {};
  const quality = doc.quality || {};
  const coverage = doc.coverage || {};
  const paths = coverage.paths || [];
  const blocked = paths.filter((p) => p.state !== 'viable');
  const withheld = quality.unreadable_cells || [];
  const failed = quality.failed_footings || [];
  const pages = quality.pages || [];
  const problems = pages.filter((p) => p.grade === 'poor' || p.grade === 'fair');

  return (
    <section className="qr card">
      <header className="qr__head">
        <Icon name="doc" size={15} className="qr__icon" />
        <span className="qr__file" title={doc.filename}>{doc.filename}</span>
        <Grade value={doc.grade} />
        {onDelete && (
          <button type="button" className="qr__del" onClick={() => onDelete(doc.doc_id)}
                  title="Remove this document from the conversation">
            <Icon name="trash" size={14} />
          </button>
        )}
      </header>

      <dl className="qr__grid">
        <Row label="Financial year">
          {ident.financial_year || <em>not readable</em>}
          {ident.financial_year && (
            <span className="qr__note"> · inferred from the document, confidence {ident.fy_confidence}</span>
          )}
        </Row>
        <Row label="Entity">{doc.company || <em>not readable</em>}</Row>
        <Row label="Framework">
          {ident.framework
            ? `${ident.framework}${ident.framework_division ? ` · Division ${ident.framework_division}` : ''}`
            : <em>not determined</em>}
        </Row>
        <Row label="Extent">
          {doc.pages} page{doc.pages === 1 ? '' : 's'} · {doc.tables} table{doc.tables === 1 ? '' : 's'}
        </Row>
      </dl>

      {/* The two things that decide whether the numbers can be trusted, given
          the most prominent position rather than buried in a collapsible. */}
      <div className="qr__stats">
        <span className={`qr__stat ${withheld.length ? 'is-bad' : 'is-ok'}`}>
          <strong>{withheld.length}</strong> figure{withheld.length === 1 ? '' : 's'} withheld
        </span>
        <span className={`qr__stat ${failed.length ? 'is-bad' : 'is-ok'}`}>
          <strong>{failed.length}</strong> total{failed.length === 1 ? '' : 's'} did not foot
        </span>
        <span className="qr__stat">
          weakest page <Grade value={doc.low_grade} />
        </span>
      </div>

      {!quality.vlm_used && (
        <Notice tone="warn">
          No second independent read was available, so each figure rests on one
          reader plus its own arithmetic.
        </Notice>
      )}

      {(quality.notes || []).map((note, i) => (
        <Notice key={i} tone="info">{note}</Notice>
      ))}

      {/* What can be ASKED of this document, as opposed to how well it scanned.
          A lookup with no candidates returns "not found" for a reason that has
          nothing to do with the filing, and an auditor who does not know that
          will read the silence as an omission. Only the paths that are not
          fully viable are shown — listing the working ones is noise. */}
      {blocked.length > 0 && (
        <div className="qr__coverage">
          <h4 className="qr__h">What cannot be asked of this document</h4>
          <ul className="qr__paths">
            {blocked.map((path) => (
              <li key={path.name}>
                <span className={`pill ${COVERAGE_PILL[path.state] || 'pill--mute'}`}>
                  {path.state === 'unavailable' ? 'not available' : path.state}
                </span>
                <span className="qr__path-name">{path.name}</span>
                <span className="qr__path-detail">{path.detail}</span>
                {path.workaround && (
                  <span className="qr__path-fix">{path.workaround}</span>
                )}
              </li>
            ))}
          </ul>
          {(coverage.unavailable || []).length > 0 && (
            <p className="qr__lead">
              For these, a “not found” result says nothing about whether the filing
              contains the disclosure — the scan did not yield what the lookup needs.
            </p>
          )}
        </div>
      )}

      {(ident.unresolved_conflicts || []).map((c, i) => (
        <Notice key={`c${i}`} tone="warn">{c}</Notice>
      ))}

      {(withheld.length > 0 || failed.length > 0 || problems.length > 0) && (
        <button type="button" className="qr__toggle" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide detail' : 'What could not be read'}
          <Icon name="chevron" size={13} className={open ? 'is-open' : ''} />
        </button>
      )}

      {open && (
        <div className="qr__detail">
          {withheld.length > 0 && (
            <>
              <h4 className="qr__h">Figures withheld</h4>
              <p className="qr__lead">
                These could not be read reliably and were removed from the tables the
                model sees, so no answer can quote them. They are an extraction
                limitation, not a disclosure the entity failed to make.
              </p>
              <ul className="qr__list">
                {withheld.map((c, i) => (
                  <li key={i}>
                    <code>{c.raw}</code> — page {c.page_no}, row “{c.row_label}”,
                    column “{c.column}” <span className="qr__why">({(c.reasons || []).join(', ')})</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {failed.length > 0 && (
            <>
              <h4 className="qr__h">Totals that did not add up</h4>
              <p className="qr__lead">
                Each is either a misread or a genuine error in the filing; the two
                cannot be told apart from a scan. Check against the original.
              </p>
              <ul className="qr__list">
                {failed.map((f, i) => (
                  <li key={i}>
                    page {f.page_no}, “{f.subtotal_label}” — printed{' '}
                    {f.printed != null ? f.printed.toLocaleString() : '—'}
                    {f.recomputed != null
                      ? `, components sum to ${f.recomputed.toLocaleString()}`
                      : ', components could not be summed'}
                  </li>
                ))}
              </ul>
            </>
          )}

          {problems.length > 0 && (
            <>
              <h4 className="qr__h">Pages needing attention</h4>
              <ul className="qr__list">
                {problems.map((p) => (
                  <li key={p.page_no}>
                    page {p.page_no} <Grade value={p.grade} />
                    <ul className="qr__sub">
                      {(p.defects || [])
                        .filter((d) => d.code !== 'preprocessed')
                        .map((d, i) => <li key={i}>{d.detail}</li>)}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
