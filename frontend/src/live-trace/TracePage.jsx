import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { authHeaders } from '../auth/token';
import {
  KIND_LABEL, STATUS_LABEL, ago, fmtChars, fmtClock, fmtDuration, foldEvents,
} from './traceLogic';
import './TracePage.css';

const BASE = '/api/trial-balance/trace';
const RUN_POLL_MS = 1000;
const FINISHED_POLL_MS = 4000;
const LIST_POLL_MS = 4000;

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  if (res.status === 401) {
    // This page has no sign-in screen of its own: it shares the app's session.
    const err = new Error('Sign in to the app first, then reload this page.');
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail; } catch { /* not JSON */ }
    const err = new Error(typeof detail === 'string' && detail ? detail : `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function initialRunId() {
  try { return new URLSearchParams(window.location.search).get('run'); } catch { return null; }
}

function setRunInUrl(id) {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('run', id); else url.searchParams.delete('run');
    window.history.replaceState(null, '', url);
  } catch { /* the URL is a convenience */ }
}

function StatusChip({ status }) {
  return <span className={`tr-chip tr-chip--${status || 'running'}`}>{STATUS_LABEL[status] || status}</span>;
}

function Detail({ data }) {
  const entries = Object.entries(data || {}).filter(([k]) => k !== 'message');
  if (!entries.length) return null;
  return (
    <span className="tr-detail">
      {entries.map(([k, v]) => (
        <span key={k} className="tr-kv"><span className="tr-k">{k.replace(/_/g, ' ')}</span> {String(v)}</span>
      ))}
    </span>
  );
}

function Row({ row }) {
  const indent = { paddingLeft: `${14 + Math.min(row.depth, 6) * 18}px` };
  if (row.kind === 'note') {
    return (
      <li className="tr-row tr-row--note" style={indent}>
        <span className="tr-icon tr-icon--note" aria-hidden="true">i</span>
        <span className="tr-body"><span className="tr-note">{row.message}</span></span>
        <span />
      </li>
    );
  }
  if (row.kind === 'llm') {
    return (
      <li className={`tr-row tr-row--llm tr-row--${row.status}`} style={indent}>
        <span className={`tr-icon tr-icon--${row.status}`} aria-hidden="true" />
        <span className="tr-body">
          <span className="tr-llm-badge">LLM</span>
          <span className="tr-name">{row.role ? `${row.role} call` : 'LLM call'}</span>
          <span className="tr-detail">
            {row.model && <span className="tr-kv">{row.model}</span>}
            <span className="tr-kv">sent {fmtChars(row.promptChars)}</span>
            {row.responseChars !== undefined && <span className="tr-kv">got {fmtChars(row.responseChars)}</span>}
            {row.toolCalls && <span className="tr-kv">asked for {row.toolCalls.join(', ')}</span>}
          </span>
          {row.message && <span className="tr-error">{row.message}</span>}
          {(row.promptExcerpt || row.responseExcerpt) && (
            <details className="tr-excerpt">
              <summary>Excerpt</summary>
              {row.promptExcerpt && <pre>{row.promptExcerpt}</pre>}
              {row.responseExcerpt && <pre>{row.responseExcerpt}</pre>}
            </details>
          )}
        </span>
        <span className="tr-time">{row.status === 'running' ? 'waiting for model…' : fmtDuration(row.durationMs)}</span>
      </li>
    );
  }
  return (
    <li className={`tr-row tr-row--step tr-row--${row.status}`} style={indent}>
      <span className={`tr-icon tr-icon--${row.status}`} aria-hidden="true" />
      <span className="tr-body">
        <span className="tr-name">{row.name}</span>
        {row.tool && <span className="tr-tool-badge">tool</span>}
        <Detail data={{ ...row.detail, ...(row.result || {}) }} />
        {row.result?.message && <span className="tr-error">{row.result.message}</span>}
      </span>
      <span className="tr-time">{row.status === 'running' ? 'running…' : fmtDuration(row.durationMs)}</span>
    </li>
  );
}

function RunList({ runs, selected, onSelect, error }) {
  if (error) return <p className="tr-empty">{error}</p>;
  if (!runs) return <p className="tr-empty">Loading runs…</p>;
  if (!runs.length) {
    return (
      <p className="tr-empty">
        No runs yet. Upload a trial balance or start an audit and it will appear here as it happens.
      </p>
    );
  }
  return (
    <ul className="tr-runs">
      {runs.map((r) => (
        <li key={r.run_id}>
          <button
            type="button"
            className={`tr-run${r.run_id === selected ? ' is-selected' : ''}`}
            onClick={() => onSelect(r.run_id)}
          >
            <span className="tr-run-top">
              <span className="tr-run-kind">{KIND_LABEL[r.kind] || r.kind}</span>
              <StatusChip status={r.status} />
            </span>
            <span className="tr-run-label">{r.label || r.run_id.slice(0, 8)}</span>
            <span className="tr-run-when">{ago(r.started_at)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function TracePage() {
  const [runs, setRuns] = useState(null);
  const [listError, setListError] = useState(null);
  const [selected, setSelected] = useState(initialRunId());
  const [meta, setMeta] = useState(null);
  const [events, setEvents] = useState([]);
  const [runError, setRunError] = useState(null);
  const [following, setFollowing] = useState(true);
  const lastSeq = useRef(0);
  const endRef = useRef(null);

  const loadRuns = useCallback(async () => {
    try {
      const data = await get('/runs');
      setRuns(data.runs);
      setListError(null);
      return data.runs;
    } catch (err) {
      setListError(err.message);
      return null;
    }
  }, []);

  useEffect(() => {
    let live = true;
    loadRuns().then((list) => {
      if (live && list?.length) setSelected((cur) => cur || list[0].run_id);
    });
    const t = setInterval(loadRuns, LIST_POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, [loadRuns]);

  useEffect(() => {
    setRunInUrl(selected);
    lastSeq.current = 0;
    setEvents([]);
    setMeta(null);
    setRunError(null);
    if (!selected) return undefined;
    let live = true;
    let timer = null;

    const tick = async () => {
      try {
        const data = await get(`/runs/${selected}?after=${lastSeq.current}`);
        if (!live) return;
        setMeta(data.run);
        setRunError(null);
        if (data.events.length) {
          lastSeq.current = data.last_seq;
          setEvents((prev) => prev.concat(data.events));
        }
        // A finished run is polled slowly: an upload that is "waiting" resumes under the same id.
        const done = data.run.status && data.run.status !== 'running';
        timer = setTimeout(tick, done ? FINISHED_POLL_MS : RUN_POLL_MS);
      } catch (err) {
        if (!live) return;
        setRunError(err.status === 404 ? 'This run has expired or is not yours.' : err.message);
        timer = setTimeout(tick, 5000);
      }
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [selected]);

  const folded = useMemo(() => foldEvents(events), [events]);

  useEffect(() => {
    if (following) endRef.current?.scrollIntoView({ block: 'end' });
  }, [events.length, following]);

  const running = meta?.status === 'running';
  const elapsed = meta ? ((meta.ended_at || Date.now() / 1000) - meta.started_at) * 1000 : null;

  return (
    <div className="tr-page">
      <header className="tr-top">
        <div>
          <h1>Run trace</h1>
          <p>What the backend is doing for an upload, audit or question, step by step, including every LLM call.</p>
        </div>
        <a className="tr-back" href="/">Back to the app</a>
      </header>

      <div className="tr-layout">
        <aside className="tr-side" aria-label="Recent runs">
          <h2>Recent runs</h2>
          <RunList runs={runs} selected={selected} onSelect={setSelected} error={listError} />
        </aside>

        <main className="tr-main">
          {!selected && <p className="tr-empty">Pick a run on the left.</p>}
          {selected && runError && <p className="tr-empty">{runError}</p>}
          {selected && meta && (
            <>
              <section className="tr-head">
                <div className="tr-head-line">
                  <span className="tr-run-kind">{KIND_LABEL[meta.kind] || meta.kind}</span>
                  <h2>{meta.label || selected}</h2>
                  <StatusChip status={meta.status} />
                </div>
                <dl className="tr-stats">
                  <div><dt>Elapsed</dt><dd>{fmtDuration(Math.round(elapsed))}</dd></div>
                  <div><dt>Steps</dt><dd>{folded.steps}</dd></div>
                  <div><dt>LLM calls</dt><dd>{folded.llm.count}</dd></div>
                  <div><dt>Time in LLM</dt><dd>{fmtDuration(folded.llm.totalMs) || '0 ms'}</dd></div>
                  {folded.llm.failed > 0 && (
                    <div className="is-bad"><dt>LLM failures</dt><dd>{folded.llm.failed}</dd></div>
                  )}
                </dl>
                {running && (
                  <div className="tr-now" role="status" aria-live="polite">
                    <span className="tr-spinner" aria-hidden="true" />
                    <span>
                      <strong>Right now:</strong>{' '}
                      {folded.runningLlm
                        ? `waiting for the LLM (${folded.runningLlm.name})`
                        : folded.current ? folded.current.name : 'starting…'}
                    </span>
                  </div>
                )}
                {meta.status === 'waiting' && (
                  <div className="tr-now tr-now--wait">Paused: this run is waiting for you to continue in the app.</div>
                )}
                {meta.error && <div className="tr-now tr-now--bad">{meta.error}</div>}
              </section>

              <ol className="tr-timeline">
                {folded.rows.map((row) => <Row key={row.key} row={row} />)}
              </ol>
              <div ref={endRef} />
              <label className="tr-follow">
                <input type="checkbox" checked={following} onChange={(e) => setFollowing(e.target.checked)} />
                Keep the latest step in view
              </label>
              {folded.rows.length > 0 && (
                <p className="tr-foot">Started {fmtClock(meta.started_at)}. Prompts and responses are not stored.</p>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
