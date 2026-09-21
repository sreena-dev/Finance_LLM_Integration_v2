/**
 * Financial Diagnostic Report — UI.
 *
 * SCOPE: this file plus `api.js` are the entire FDR UI; nothing else in the
 * codebase is touched by them. One page, one scroll surface. A segmented control
 * (Query / Report) sits in a slim bar that hides on scroll-down and reveals on
 * scroll-up, so the two views never need their own separate screens or headers.
 *
 * THE ENTITY PICKER IS SHARED, AND IT IS ABOVE BOTH VIEWS.
 * Every answer this mode gives is scoped to one entity, so the entity cannot be
 * a control that lives inside one tab: a question asked on the Query tab and a
 * report generated on the Report tab must be about the same company, and the
 * only way to make that obvious is to put the selection where both can see it.
 * It also means the question text never has to name the company, which is what
 * removes the possibility of a question being answered from the wrong filing.
 *
 * NOTHING HERE FABRICATES DATA. The Report segment is still an empty shell — its
 * generation pipeline is not built — and says so rather than showing a mock.
 * Styles are scoped to .fdr-root and mirror the app's own tokens
 * (src/styles/index.css) so this drops into the existing theme unchanged.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { askQuestionStream, fetchEntities, fetchHealth } from './api';

function SendIcon({ size = 16 }) {
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
function Field({ label, children, hint }) {
  return (
    <label className="fdr-field">
      <span className="fdr-field__label">{label}</span>
      {children}
      <span className="fdr-field__hint">{hint || ' '}</span>
    </label>
  );
}

function Empty({ text, grow }) {
  return (
    <div className={`fdr-empty ${grow ? 'fdr-empty--grow' : ''}`}>
      <p className="fdr-empty__text">{text}</p>
    </div>
  );
}

/* ── Entity picker: one selection, shared by both segments ─────────────────── */

function EntityBar({ entities, entityId, onChange, state, error, onRetry }) {
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
function Provenance({ provenance, intent }) {
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
function Sources({ sources }) {
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

/* ── Query segment ────────────────────────────────────────────────────────── */

function Turn({ turn }) {
  if (turn.role === 'user') {
    return (
      <div className="fdr-turn fdr-turn--user">
        <div className="fdr-bubble fdr-bubble--user">{turn.text}</div>
      </div>
    );
  }

  const tone =
    turn.kind === 'refused' || turn.kind === 'unsupported' || turn.kind === 'ambiguous'
      ? 'fdr-bubble--note'
      : turn.kind === 'error'
        ? 'fdr-bubble--error'
        : '';

  return (
    <div className="fdr-turn">
      <div className={`fdr-bubble ${tone}`}>
        {turn.kind && turn.kind !== 'error' ? (
          <span className="fdr-kind">{turn.kind}</span>
        ) : null}
        <div className="fdr-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.text}</ReactMarkdown>
        </div>
        <Sources sources={turn.sources} />
        <Provenance provenance={turn.provenance} intent={turn.intent} />
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  'What does this entity raise?',
  'Is there working-capital stress?',
  'Is S04 firing?',
  'Trade receivables',
  'What is missing?',
  'What should I fix first?',
];

function QueryPane({ turns, pending, progress, draft, entityId, onAsk }) {
  const endRef = useRef(null);
  const count = turns.length;

  // Scroll only when a TURN is added. Keying this on `pending` as well made the
  // view scroll twice per question — once when the placeholder appeared and
  // again when it was replaced — which is what read as the answer flapping.
  useEffect(() => {
    if (count === 0) return;
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [count]);

  if (!entityId) {
    return <Empty text="Select an entity to start asking questions." grow />;
  }

  if (count === 0 && !pending) {
    return (
      <div className="fdr-intro">
        <p className="fdr-intro__lead">
          Suggested questions for <strong>{entityId.replace(/_/g, ' ')}</strong>
        </p>
        <div className="fdr-intro__list">
          {SUGGESTIONS.map((question) => (
            <button
              key={question}
              type="button"
              className="fdr-chip"
              onClick={() => onAsk(question)}
            >
              {question}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="fdr-log">
      {turns.map((t) => (
        <Turn key={t.id} turn={t} />
      ))}
      {pending ? (
        <div className="fdr-turn">
          <div className="fdr-bubble fdr-bubble--pending">
            {/* The live read, stage by stage. Only the LAST few lines are kept
                on screen: a six-filing entity emits eight events, and a bubble
                that grows with every one of them pushes the conversation
                around while the user is trying to read it. */}
            {progress.length === 0 && !draft ? (
              <span className="fdr-stage">Working…</span>
            ) : null}
            {/* Stages stop once prose starts arriving: the read is finished and
                the answer is being written, so the stage log has nothing left
                to say and would only compete with the text for attention. */}
            {!draft &&
              progress.slice(-4).map((line, i, shown) => (
                <span
                  key={`${line}-${i}`}
                  className={`fdr-stage ${i === shown.length - 1 ? 'is-current' : ''}`}
                >
                  {line}
                </span>
              ))}
            {draft ? (
              <div className="fdr-md fdr-md--draft">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function QueryComposer({ onSend, disabled, placeholder }) {
  const [text, setText] = useState('');
  const areaRef = useRef(null);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    if (areaRef.current) areaRef.current.style.height = 'auto';
  };

  const onKeyDown = (e) => {
    // Enter sends, Shift+Enter breaks the line — the convention the rest of the
    // app's composers use.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const onInput = (e) => {
    setText(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  };

  return (
    <div className="fdr-composer">
      <div className={`fdr-composer__box ${disabled ? 'is-disabled' : ''}`}>
        <textarea
          ref={areaRef}
          className="fdr-composer__input"
          rows={1}
          value={text}
          placeholder={placeholder}
          disabled={disabled}
          onChange={onInput}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="fdr-composer__send"
          onClick={submit}
          disabled={disabled || !text.trim()}
          aria-label="Send question"
        >
          <SendIcon />
        </button>
      </div>
    </div>
  );
}

/* ── Report segment — still a shell, and says so ──────────────────────────── */

function ReportPane({ entityId }) {
  return (
    <div className="fdr-report">
      <div className="fdr-report__controls">
        <p className="fdr-report__note">
          Report generation is not built yet. The Query tab answers from the same live
          evaluation the report will be assembled from, so nothing here is blocked on it.
        </p>
        <button type="button" className="fdr-btn fdr-btn--primary" disabled>
          Generate report
        </button>
      </div>
      <Empty
        text={
          entityId
            ? `No report generated for ${entityId.replace(/_/g, ' ')} yet.`
            : 'No report generated yet.'
        }
        grow
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════ */

let turnSeq = 0;

export default function FdrAnalysis() {
  const [segment, setSegment] = useState('query'); // 'query' | 'report'

  const [entities, setEntities] = useState([]);
  const [entityState, setEntityState] = useState('loading'); // loading | ready | error
  const [entityError, setEntityError] = useState('');
  const [entityId, setEntityId] = useState('');

  const [unavailable, setUnavailable] = useState('');

  // One thread per entity: switching companies must not carry another company's
  // answers into the new context, and switching back should not have lost them.
  const [threads, setThreads] = useState({});
  const [pending, setPending] = useState(false);
  // Live stage lines for the read currently in flight.
  const [progress, setProgress] = useState([]);
  // Text streamed back while a generated answer is still being written. Only
  // the retrieval path produces it; the deterministic path leaves it empty and
  // the pending bubble shows stages instead.
  const [draft, setDraft] = useState('');

  const turns = threads[entityId] || [];

  const loadEntities = useCallback(async () => {
    setEntityState('loading');
    setEntityError('');
    setUnavailable('');
    try {
      const [health, rows] = await Promise.all([
        fetchHealth().catch(() => null),
        fetchEntities(),
      ]);
      if (health && health.available === false && health.reason) setUnavailable(health.reason);
      setEntities(rows);
      setEntityState('ready');
      if (rows.length === 1) setEntityId(rows[0].entity_id);
    } catch (err) {
      setEntityError(err.message || String(err));
      setEntityState('error');
    }
  }, []);

  useEffect(() => {
    loadEntities();
  }, [loadEntities]);

  const append = useCallback((id, turn) => {
    setThreads((prev) => ({ ...prev, [id]: [...(prev[id] || []), turn] }));
  }, []);

  const send = useCallback(
    async (text) => {
      const target = entityId;
      if (!target) return;
      append(target, { id: `u${(turnSeq += 1)}`, role: 'user', text });
      setProgress([]);
      setDraft('');
      setPending(true);
      try {
        const res = await askQuestionStream(
          { entityId: target, query: text },
          {
            // Progress is dropped if the user has moved to another entity
            // mid-read: showing one company's read under another's thread would
            // be exactly the confusion the shared picker exists to prevent.
            onProgress: (message) =>
              setProgress((prev) => (prev.length > 40 ? prev : [...prev, message])),
            onToken: (piece) => setDraft((prev) => prev + piece),
          }
        );
        append(target, {
          id: `a${(turnSeq += 1)}`,
          role: 'assistant',
          text: res.answer,
          kind: res.kind,
          provenance: res.provenance,
          sources: res.sources,
          intent: res.intent,
        });
      } catch (err) {
        // Surfaced as a turn rather than a toast: the failure belongs next to
        // the question that caused it, and a 503 here carries the operator's
        // actual fix ("set FINANCE_DSN", "install psycopg").
        append(target, {
          id: `e${(turnSeq += 1)}`,
          role: 'assistant',
          kind: 'error',
          text: `**Could not answer.** ${err.message || String(err)}`,
        });
      } finally {
        setPending(false);
        setProgress([]);
        setDraft('');
      }
    },
    [append, entityId]
  );

  // The header is FIXED. It used to collapse on scroll-down and reveal on
  // scroll-up, which is a pattern for long reading surfaces and the wrong one
  // here: this pane scrolls every time an answer arrives, so the bar animated
  // its own height on almost every interaction and the content below it moved
  // with it. Combined with the entity picker growing on selection, that is what
  // read as the whole view flapping. The header also carries the entity in
  // force — the single most important piece of context on the screen — and
  // hiding that while the user reads an answer about it is wrong regardless of
  // the motion.
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [segment]);

  const composerDisabled = pending || !entityId || entityState !== 'ready';
  const placeholder = !entityId
    ? 'Select an entity above to ask a question…'
    : pending
      ? 'Reading the filings…'
      : `Ask about ${entityId.replace(/_/g, ' ')}…`;

  return (
    <div className="fdr-root">
      <style>{CSS}</style>

      <div className="fdr-page">
        <div className="fdr-topbar">
          <div className="fdr-topbar__inner">
            {/* Same label / control / hint structure as the entity field, so the
                two columns share one vertical rhythm and neither can drift when
                the other's contents change. */}
            <div className="fdr-field fdr-field--seg">
              <span className="fdr-field__label">View</span>
              <div className="fdr-seg" role="tablist" aria-label="Diagnostic report view">
                <button
                  type="button"
                  role="tab"
                  aria-selected={segment === 'query'}
                  className={`fdr-seg__btn ${segment === 'query' ? 'is-on' : ''}`}
                  onClick={() => setSegment('query')}
                >
                  {segment === 'query' && <span className="fdr-seg__bg" />}
                  <span className="fdr-seg__text">Query</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={segment === 'report'}
                  className={`fdr-seg__btn ${segment === 'report' ? 'is-on' : ''}`}
                  onClick={() => setSegment('report')}
                >
                  {segment === 'report' && <span className="fdr-seg__bg" />}
                  <span className="fdr-seg__text">Report</span>
                </button>
              </div>
              <span className="fdr-field__hint">&nbsp;</span>
            </div>

            <EntityBar
              entities={entities}
              entityId={entityId}
              onChange={setEntityId}
              state={entityState}
              error={entityError}
              onRetry={loadEntities}
            />
          </div>

          {unavailable ? <div className="fdr-banner">{unavailable}</div> : null}
        </div>

        <div className="fdr-scroll" ref={scrollRef}>
          <div
            id="fdr-panel-query"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'query' ? 'flex' : 'none' }}
          >
            <QueryPane
              turns={turns}
              pending={pending}
              progress={progress}
              draft={draft}
              entityId={entityId}
              onAsk={send}
            />
          </div>

          <div
            id="fdr-panel-report"
            role="tabpanel"
            className="fdr-panel"
            style={{ display: segment === 'report' ? 'flex' : 'none' }}
          >
            <ReportPane entityId={entityId} />
          </div>
        </div>

        {segment === 'query' && (
          <QueryComposer onSend={send} disabled={composerDisabled} placeholder={placeholder} />
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════ STYLES ═══════════════════════════════════
 * Uses the global tokens from src/styles/index.css. Scoped to .fdr-root only. */

const CSS = `
.fdr-root{
  position:absolute; inset:0; background:var(--bg); color:var(--ink-800);
  font-family:var(--font-sans); font-size:14px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.fdr-root *{box-sizing:border-box;}
.fdr-root button,.fdr-root select,.fdr-root textarea{font:inherit;color:inherit;}
.fdr-root :focus-visible{outline:2px solid var(--navy-600); outline-offset:2px; border-radius:4px;}

.fdr-page{position:absolute; inset:0; display:flex; flex-direction:column; min-height:0;}

/* Fixed height, no collapse, no transition. Nothing in this bar may move in
   response to scrolling or to a selection — see the note in the component. */
.fdr-topbar{
  flex:none; background:var(--surface); border-bottom:1px solid var(--border);
}
.fdr-topbar__inner{display:flex; align-items:flex-start; gap:20px; flex-wrap:wrap; padding:10px 24px 12px;}

.fdr-seg{display:inline-flex; gap:3px; padding:3px; background:var(--ink-100); border-radius:var(--radius);}
.fdr-seg__btn{position:relative; padding:7px 20px; background:none; border:none;
  border-radius:var(--radius-sm); font-size:13px; font-weight:550; color:var(--ink-600);
  cursor:pointer; transition:color .18s var(--ease);}
.fdr-seg__btn.is-on{color:var(--navy-900);}
.fdr-seg__bg{position:absolute; inset:0; background:var(--surface); border-radius:var(--radius-sm); box-shadow:var(--shadow-sm);}
.fdr-seg__text{position:relative; z-index:1;}

.fdr-entitybar{display:flex; align-items:flex-end; gap:12px; flex:1 1 320px; min-width:260px;}
.fdr-entitybar--error{align-items:center;}
.fdr-entitybar__err{flex:1; font-size:12.5px; color:var(--err-600);}
.fdr-banner{padding:8px 24px; background:var(--warn-100); color:var(--warn-600);
  font-size:12.5px; border-top:1px solid var(--border);}

.fdr-field{display:flex; flex-direction:column; gap:5px; flex:1 1 240px; min-width:200px;}
.fdr-field--seg{flex:0 0 auto; min-width:0;}
.fdr-field__label{font-size:11.5px; font-weight:600; letter-spacing:.045em; text-transform:uppercase; color:var(--ink-500);}
/* Fixed height and always present: the hint appearing on selection is what used
   to push the header down and knock the Query/Report control out of line. */
.fdr-field__hint{display:block; min-height:16px; font-size:11.5px; line-height:16px;
  color:var(--ink-400); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.fdr-select{appearance:none; -webkit-appearance:none; width:100%; padding:9px 32px 9px 12px;
  background:var(--surface)
    url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2397a3b0' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><path d='M6 9l6 6 6-6'/></svg>")
    no-repeat right 10px center;
  border:1px solid var(--border-strong); border-radius:var(--radius);
  font-size:13.5px; color:var(--ink-900); cursor:pointer;}
.fdr-select:disabled{background-color:var(--ink-50); color:var(--ink-400); opacity:1; cursor:not-allowed;}

.fdr-btn{display:inline-flex; align-items:center; justify-content:center; gap:8px;
  padding:9px 18px; border:1px solid transparent; border-radius:var(--radius);
  font-size:13.5px; font-weight:550; cursor:pointer;
  transition:background .18s var(--ease),box-shadow .18s var(--ease);}
.fdr-btn:disabled{cursor:not-allowed; opacity:.55;}
.fdr-btn--primary{background:var(--navy-700); color:var(--white); box-shadow:var(--shadow-sm);}
.fdr-btn--primary:hover:not(:disabled){background:var(--navy-800); box-shadow:var(--shadow-md);}
.fdr-btn--ghost{background:var(--surface); border-color:var(--border-strong); color:var(--ink-700);}

.fdr-scroll{flex:1; min-height:0; overflow-y:auto;}
.fdr-panel{min-height:100%; display:flex; flex-direction:column; padding:20px 24px;}

/* Conversation */
.fdr-log{display:flex; flex-direction:column; gap:16px; width:100%; max-width:820px; margin:0 auto;}
.fdr-turn{display:flex;}
.fdr-turn--user{justify-content:flex-end;}
.fdr-bubble{max-width:100%; padding:14px 18px; background:var(--surface);
  border:1px solid var(--border); border-radius:var(--radius-lg); box-shadow:var(--shadow-xs);}
.fdr-turn:not(.fdr-turn--user) .fdr-bubble{border-top:3px solid var(--navy-700);}
.fdr-bubble--user{max-width:78%; background:var(--navy-700); color:var(--white); border-color:transparent;}
.fdr-bubble--note{background:var(--warn-100); border-color:var(--gold-600);}
.fdr-bubble--error{background:var(--err-100); border-color:var(--err-600);}
.fdr-bubble--pending{display:flex; flex-direction:column; gap:3px; color:var(--ink-500); font-size:12.5px;}
/* Stage lines fade back as they are superseded, so the newest reads as current
   without the bubble needing to move or re-order. */
.fdr-stage{opacity:.45; font-family:var(--font-mono); font-size:11.5px; line-height:1.7;}
.fdr-stage.is-current{opacity:1; color:var(--ink-700);}
.fdr-md--draft{color:var(--ink-800); font-size:14px;}
.fdr-md--draft::after{content:"▌"; color:var(--navy-600); animation:fdr-blink 1s steps(2) infinite;}
@keyframes fdr-blink{50%{opacity:0;}}

/* Sources drawer */
.fdr-src{margin-top:12px; border-top:1px solid var(--border); padding-top:9px;}
.fdr-src__summary{cursor:pointer; font-size:11.5px; font-weight:600; color:var(--navy-700); list-style:none;}
.fdr-src__summary::-webkit-details-marker{display:none;}
.fdr-src__summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-src[open] .fdr-src__summary::before{content:"▾ ";}
.fdr-src__list{margin:10px 0 0; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px;}
.fdr-src__item{padding:9px 11px; background:var(--ink-50); border:1px solid var(--border);
  border-left:3px solid var(--ink-300); border-radius:var(--radius-sm);}
.fdr-src__item.is-cited{border-left-color:var(--navy-600); background:var(--navy-50);}
.fdr-src__head{display:flex; align-items:baseline; gap:7px;}
.fdr-src__n{font-family:var(--font-mono); font-size:11.5px; font-weight:700; color:var(--navy-700);}
.fdr-src__title{flex:1; font-size:12.5px; font-weight:600; color:var(--ink-800);}
.fdr-src__badge{padding:1px 7px; border-radius:100px; background:var(--navy-600); color:var(--white);
  font-size:9.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;}
.fdr-src__meta{margin-top:3px; font-family:var(--font-mono); font-size:10.5px; color:var(--ink-500);}
.fdr-src__excerpt{margin:6px 0 0; font-size:11.5px; line-height:1.55; color:var(--ink-600);
  max-height:76px; overflow:hidden;}

.fdr-kind{display:inline-block; margin-bottom:8px; padding:2px 8px; border-radius:100px;
  background:var(--gold-100); color:var(--gold-700); font-size:10.5px; font-weight:650;
  letter-spacing:.05em; text-transform:uppercase;}

.fdr-md > :first-child{margin-top:0;}
.fdr-md > :last-child{margin-bottom:0;}
.fdr-md p{margin:0 0 10px;}
.fdr-md ul,.fdr-md ol{margin:0 0 10px; padding-left:20px;}
.fdr-md li{margin:3px 0;}
.fdr-md code{padding:1px 5px; background:var(--ink-100); border-radius:4px;
  font-family:var(--font-mono); font-size:12.5px;}
.fdr-md strong{color:var(--ink-900); font-weight:650;}
.fdr-md em{color:var(--ink-600);}
.fdr-md table{border-collapse:collapse; width:100%; margin:0 0 10px; font-size:13px;}
.fdr-md th,.fdr-md td{border:1px solid var(--border); padding:6px 9px; text-align:left;}
.fdr-md thead th{background:var(--paper-2); border-bottom:2px solid var(--navy-700);}
.fdr-md td:not(:first-child){font-family:var(--font-mono); font-variant-numeric:tabular-nums; text-align:right;}

/* Provenance */
.fdr-prov{margin-top:12px; border-top:1px solid var(--border); padding-top:9px;}
.fdr-prov__summary{cursor:pointer; font-size:11.5px; color:var(--ink-500); list-style:none;}
.fdr-prov__summary::-webkit-details-marker{display:none;}
.fdr-prov__summary::before{content:"▸ "; color:var(--ink-400);}
.fdr-prov[open] .fdr-prov__summary::before{content:"▾ ";}
.fdr-prov__grid{display:grid; grid-template-columns:auto 1fr; gap:4px 14px; margin:10px 0 0;
  font-size:11.5px; color:var(--ink-600);}
.fdr-prov__grid dt{color:var(--ink-400); font-weight:600;}
.fdr-prov__grid dd{margin:0; font-family:var(--font-mono); font-size:11px; word-break:break-word;}

/* Intro — the suggestions are buttons that ask the question, not decoration. */
.fdr-intro{width:100%; max-width:820px; margin:0 auto; padding:8px 0;}
.fdr-intro__lead{margin:0 0 12px; font-size:11.5px; font-weight:600;
  letter-spacing:.045em; text-transform:uppercase; color:var(--ink-500);}
.fdr-intro__lead strong{color:var(--ink-700); font-weight:700;}
.fdr-intro__list{display:flex; flex-wrap:wrap; gap:8px;}
.fdr-chip{padding:8px 14px; background:var(--surface); border:1px solid var(--border-strong);
  border-radius:100px; font-size:12.5px; color:var(--ink-700); cursor:pointer; text-align:left;
  transition:background .16s var(--ease), border-color .16s var(--ease), color .16s var(--ease);}
.fdr-chip:hover{background:var(--navy-50); border-color:var(--navy-600); color:var(--navy-800);}
.fdr-chip:active{background:var(--navy-100);}

/* Report shell */
.fdr-report{display:flex; flex-direction:column; flex:1; min-height:0; gap:16px;}
.fdr-report__controls{display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  padding:18px 20px; background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-lg); box-shadow:var(--shadow-xs);}
.fdr-report__note{flex:1 1 320px; margin:0; font-size:13px; color:var(--ink-500);}

.fdr-empty{display:flex; align-items:center; justify-content:center; padding:32px 0;}
.fdr-empty--grow{flex:1;}
.fdr-empty__text{margin:0; font-size:13px; color:var(--ink-400);}

/* Composer */
.fdr-composer{flex:none; padding:12px 24px 16px; background:linear-gradient(to top,var(--bg) 62%,rgba(244,246,249,0));}
.fdr-composer__box{display:flex; align-items:flex-end; gap:10px; max-width:820px; margin:0 auto;
  padding:9px 9px 9px 16px; background:var(--surface); border:1px solid var(--border-strong);
  border-radius:var(--radius-xl); box-shadow:var(--shadow-sm);
  transition:border-color .16s var(--ease), box-shadow .16s var(--ease);}
/* The focus ring belongs to the BOX, never to the textarea inside it. The
   root's :focus-visible rule drew a rounded outline around the textarea, which
   rendered as a second rectangle floating inside the rounded composer. */
.fdr-composer__box:focus-within{border-color:var(--navy-600); box-shadow:var(--shadow-md);}
.fdr-composer__box.is-disabled{background:var(--ink-50); box-shadow:none;}
.fdr-composer__input{flex:1; min-width:0; border:none; outline:none; resize:none; background:none;
  padding:8px 0; font-size:14px; line-height:1.6; color:var(--ink-900); max-height:120px;}
.fdr-composer__input:focus,
.fdr-composer__input:focus-visible{outline:none; border:none; box-shadow:none;}
.fdr-composer__input::placeholder{color:var(--ink-400);}
.fdr-composer__input:disabled{cursor:not-allowed;}
.fdr-composer__send{display:flex; align-items:center; justify-content:center; width:36px; height:36px;
  flex:none; border:none; border-radius:50%; background:var(--navy-700); color:var(--white); cursor:pointer;}
.fdr-composer__send:disabled{background:var(--ink-300); cursor:not-allowed;}

@media (max-width:720px){
  .fdr-topbar__inner,.fdr-panel,.fdr-composer,.fdr-banner{padding-left:16px; padding-right:16px;}
  .fdr-report__controls{flex-direction:column; align-items:stretch;}
  .fdr-field{min-width:0;}
}
`;
