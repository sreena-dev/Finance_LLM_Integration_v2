/**
 * Turns the backend's flat event stream into the rows the trace page draws.
 *
 * Pure functions, no React: the page polls for new events and folds them into the same state,
 * so a refresh and a live update produce identical output.
 */

export const KIND_LABEL = {
  upload: 'Upload',
  audit: 'Audit',
  ask: 'Question',
};

const HIDDEN = new Set([
  'type', 'ts', 'step', 'depth', 'seq', 'status', 'duration_ms', 'message', 'kind', 'call_id',
  'model', 'role', 'prompt_chars', 'response_chars', 'tool_calls', 'prompt_excerpt', 'response_excerpt',
]);

/** The counters an event carries beyond the standard fields (rows, mapped, tier ...). */
function pick(e) {
  const out = {};
  for (const [k, v] of Object.entries(e)) {
    if (!HIDDEN.has(k) && v !== null && v !== undefined) out[k] = v;
  }
  return out;
}

/** Fold `events` (oldest first) into `{ rows, ended, current, runningLlm, llm, steps }`.
 * rows:    ordered list of step / llm / note entries for the timeline
 * llm:     summary of LLM calls (count, total ms, failures)
 * current: the deepest step still running, or null */
export function foldEvents(events) {
  const rows = [];
  const openSteps = []; // indexes into rows, innermost last
  const llmByCall = new Map();
  let ended = null;

  for (const e of events) {
    switch (e.type) {
      case 'step_start':
        rows.push({
          kind: 'step', key: `s${e.seq}`, name: e.step, depth: e.depth || 0, status: 'running',
          startedAt: e.ts, detail: pick(e), tool: e.kind === 'tool',
        });
        openSteps.push(rows.length - 1);
        break;
      case 'step_end':
        for (let i = openSteps.length - 1; i >= 0; i -= 1) {
          const row = rows[openSteps[i]];
          if (row.name === e.step && row.depth === (e.depth || 0)) {
            row.status = e.status || 'ok';
            row.durationMs = e.duration_ms;
            row.result = { ...pick(e), ...(e.message ? { message: e.message } : {}) };
            openSteps.splice(i, 1);
            break;
          }
        }
        break;
      case 'llm_start': {
        const row = {
          kind: 'llm', key: `l${e.call_id || e.seq}`, name: e.step || 'LLM call', depth: e.depth || 0,
          status: 'running', startedAt: e.ts, role: e.role, model: e.model, promptChars: e.prompt_chars,
          promptExcerpt: e.prompt_excerpt,
        };
        rows.push(row);
        llmByCall.set(e.call_id, row);
        break;
      }
      case 'llm_end': {
        const row = llmByCall.get(e.call_id);
        if (row) {
          row.status = e.status || 'ok';
          row.durationMs = e.duration_ms;
          row.responseChars = e.response_chars;
          row.toolCalls = e.tool_calls;
          row.message = e.message;
          row.responseExcerpt = e.response_excerpt;
        }
        break;
      }
      case 'note':
        rows.push({ kind: 'note', key: `n${e.seq}`, depth: e.depth || 0, message: e.message, detail: pick(e), at: e.ts });
        break;
      case 'run_end':
        ended = { status: e.status, durationMs: e.duration_ms, message: e.message };
        break;
      default:
        break;
    }
  }

  const calls = rows.filter((r) => r.kind === 'llm');
  const current = openSteps.length ? rows[openSteps[openSteps.length - 1]] : null;
  const runningLlm = [...calls].reverse().find((c) => c.status === 'running') || null;
  return {
    rows,
    ended,
    current,
    runningLlm,
    llm: {
      count: calls.length,
      totalMs: calls.reduce((n, c) => n + (c.durationMs || 0), 0),
      failed: calls.filter((c) => c.status === 'failed').length,
    },
    steps: rows.filter((r) => r.kind === 'step').length,
  };
}

export function fmtDuration(ms) {
  if (ms === null || ms === undefined) return '';
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

export function fmtChars(n) {
  if (n === null || n === undefined) return '';
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k chars` : `${n} chars`;
}

export function fmtClock(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const STATUS_LABEL = {
  running: 'Running',
  ok: 'Done',
  failed: 'Failed',
  waiting: 'Waiting for you',
  partial: 'Partial',
};
