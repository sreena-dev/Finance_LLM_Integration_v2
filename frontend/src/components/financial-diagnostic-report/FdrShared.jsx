/**
 * Small shared primitives used across the Query, Report and XBRL Direct
 * panes: icons, labelled-field wrapper, empty state, the entity picker, and
 * the provenance/sources/tile-source disclosures that accompany an answer or
 * a figure. Split out of `FdrAnalysis.jsx`, which had grown past 2,800 lines;
 * nothing here changed shape or behavior in the split.
 */
import { useMemo } from 'react';

export function SendIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4.5 12l15.5-7.5-4 15.5-3.9-5.4zM12.1 14.6L20 4.5" />
    </svg>
  );
}

/**
 * A labelled control.
 *
 * The hint row is ALWAYS rendered, falling back to a non-breaking space when
 * there is nothing to say. Rendering it conditionally made the field grow the
 * moment an entity was selected, which pushed the whole header down and shunted
 * the Query/Report control out of line — the layout jumped as a direct result of
 * the user making a selection. Reserving the row costs one line of space and
 * keeps the header a fixed height for the life of the session.
 */
export function Field({ label, children, hint }) {
  return (
    <label className="fdr-field">
      <span className="fdr-field__label">{label}</span>
      {children}
      <span className="fdr-field__hint">{hint || ' '}</span>
    </label>
  );
}

export function Empty({ text, grow }) {
  return (
    <div className={`fdr-empty ${grow ? 'fdr-empty--grow' : ''}`}>
      <p className="fdr-empty__text">{text}</p>
    </div>
  );
}

/* ── Entity picker: one selection, shared by both segments ─────────────────── */

export function EntityBar({ entities, entityId, onChange, state, error, onRetry }) {
  const selected = useMemo(
    () => entities.find((e) => e.entity_id === entityId) || null,
    [entities, entityId]
  );

  if (state === 'error') {
    return (
      <div className="fdr-entitybar fdr-entitybar--error">
        <span className="fdr-entitybar__err">{error}</span>
        <button type="button" className="fdr-btn fdr-btn--ghost" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="fdr-entitybar">
      <Field
        label="Entity"
        hint={
          selected
            ? `${selected.filings} filings · ${selected.first_fy_label}–${selected.last_fy_label}` +
              (selected.trend_capable
                ? ''
                : ` · under ${selected.min_trend_years} years, trend diagnostics will abstain`)
            : null
        }
      >
        <select
          className="fdr-select"
          value={entityId}
          onChange={(e) => onChange(e.target.value)}
          disabled={state !== 'ready' || entities.length === 0}
        >
          {state === 'loading' && <option value="">Loading entities…</option>}
          {state === 'ready' && entities.length === 0 && (
            <option value="">No entities in the corpus</option>
          )}
          {state === 'ready' && entities.length > 0 && <option value="">Select an entity…</option>}
          {entities.map((e) => (
            <option key={e.entity_id} value={e.entity_id}>
              {e.entity_id.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </Field>
    </div>
  );
}

/* ── Provenance: where the figures came from, and how old the read is ─────── */

/**
 * Shown under every answer that touched the corpus.
 *
 * This is not decoration. Answers are computed from a live read that is cached
 * briefly for latency, and a cached read whose age is invisible is the exact
 * failure this mode was built to avoid — an answer that looks current and is
 * not. Stating the age, the years covered and the pipeline fingerprint makes a
 * stale answer visible in the answer.
 */
export function Provenance({ provenance, intent }) {
  if (!provenance || !provenance.entity_id) return null;

  // The two paths have genuinely different provenance and must not be forced
  // into one summary. A computed answer is characterised by the PANEL it was
  // evaluated over — how many years, how old the read, which extractor built
  // it. A retrieved answer is characterised by the FILING it was read from and
  // by how the evidence was selected. Rendering the panel fields for a
  // retrieval answer produced "0 years", which reads as a failed read rather
  // than as a field that does not apply.
  const retrieved = provenance.path === 'retrieval';
  const age = Number(provenance.age_seconds || 0);
  const freshness = age < 2 ? 'read just now' : `read ${Math.round(age)}s ago`;
  const rerank = provenance.rerank || {};
  const grounded = provenance.groundedness || {};

  const summary = retrieved
    ? `From ${provenance.fy || 'the filing'} · ${rerank.kept ?? 0} of ` +
      `${rerank.candidates ?? 0} passages used` +
      (grounded.checked ? (grounded.grounded ? ' · verified' : ' · NOT verified') : '')
    : `Live from filings · ${(provenance.years || []).length} years · ${freshness}`;

  return (
    <details className="fdr-prov">
      <summary className="fdr-prov__summary">{summary}</summary>
      <dl className="fdr-prov__grid">
        <dt>Entity</dt>
        <dd>{provenance.entity_id}</dd>
        {retrieved ? (
          <>
            <dt>Filing</dt>
            <dd>{provenance.doc_id} ({provenance.fy})</dd>
            <dt>Year basis</dt>
            <dd>{provenance.year_basis || '—'}</dd>
            <dt>Reranker</dt>
            <dd>
              {rerank.applied
                ? `${rerank.model} · ${rerank.candidates} scored · ${rerank.kept} kept · ` +
                  `${rerank.dropped_below_threshold} below threshold ${rerank.min_logit}`
                : `not applied (${rerank.reason || 'unavailable'})`}
            </dd>
            <dt>Verification</dt>
            <dd>
              {grounded.checked
                ? `${grounded.grounded ? 'grounded' : 'NOT grounded'}${grounded.reason ? ` — ${grounded.reason}` : ''}`
                : `not checked (${grounded.reason || 'disabled'})`}
            </dd>
            {(provenance.retrieval?.degraded || []).length ? (
              <>
                <dt>Degraded</dt>
                <dd>{provenance.retrieval.degraded.join('; ')}</dd>
              </>
            ) : null}
          </>
        ) : (
          <>
            <dt>Years</dt>
            <dd>{(provenance.years || []).join(', ') || '—'}</dd>
            <dt>Basis</dt>
            <dd>{provenance.flavor}</dd>
            <dt>Extractor</dt>
            <dd>{provenance.extractor_version}</dd>
            <dt>Fingerprint</dt>
            <dd>{provenance.pipeline_fingerprint}</dd>
          </>
        )}
        <dt>Source</dt>
        <dd>{provenance.source}</dd>
        {intent?.reason_code ? (
          <>
            <dt>Read as</dt>
            <dd>
              {intent.kind} ({intent.reason_code})
              {intent.evidence?.length ? ` · matched ${intent.evidence.join(', ')}` : ''}
            </dd>
          </>
        ) : null}
      </dl>
    </details>
  );
}


/**
 * The numbered sources behind a generated answer.
 *
 * Present only on the retrieval path — the deterministic path names its
 * statement and derivation inside the prose, because those are computed rather
 * than retrieved. Each row says which filing, which chunk, which page, and
 * whether the answer actually leaned on it: the sources OFFERED to the model and
 * the sources it CITED are different facts, and collapsing them would hide the
 * ones it ignored.
 */
export function Sources({ sources }) {
  if (!sources?.length) return null;
  const cited = sources.filter((s) => s.cited).length;
  return (
    <details className="fdr-src">
      <summary className="fdr-src__summary">
        {sources.length} sources · {cited} cited
      </summary>
      <ol className="fdr-src__list">
        {sources.map((s) => (
          <li key={s.n} className={`fdr-src__item ${s.cited ? 'is-cited' : ''}`}>
            <div className="fdr-src__head">
              <span className="fdr-src__n">[{s.n}]</span>
              <span className="fdr-src__title">{s.title}</span>
              {s.cited ? <span className="fdr-src__badge">cited</span> : null}
            </div>
            <div className="fdr-src__meta">
              {s.kind} · {s.doc_id}
              {s.page ? ` · p.${s.page}` : ''}
              {typeof s.rerank_score === 'number' ? ` · relevance ${s.rerank_score}` : ''}
            </div>
            {s.excerpt ? <p className="fdr-src__excerpt">{s.excerpt}</p> : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

export function TileSource({ citations }) {
  const label = citations.length === 1 ? 'Source' : `Sources (${citations.length})`;
  return (
    <details className="fdr-tile__src">
      <summary>{label}</summary>
      <ul>
        {citations.map((c, i) => (
          <li key={i}>
            {c.located ? (
              <>
                <span className="fdr-tile__srcrow">&ldquo;{c.row_label}&rdquo;</span>
                <span className="fdr-tile__srcloc">
                  {c.doc_id}
                  {c.page ? `, page ${c.page}` : ''}
                </span>
              </>
            ) : (
              <span className="fdr-tile__srcmissing">
                {c.key.replace(/_/g, ' ')} — computed from other bound figures, no single
                printed row to cite
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
