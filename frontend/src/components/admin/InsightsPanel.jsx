import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import Icon from '../common/Icon';
import { clip, fmtDateTime, fmtDay, fmtNum, fmtPct, fmtSecs, pct } from './format';
import { deriveObservations } from './insightsLogic';

// Purely presentational: takes the insights payload as a prop so the render
// harness can test it populated (effects do not run under renderToString).

const AXIS = { fontSize: 11, fill: 'var(--ink-500)' };
const TOOLTIP = {
  contentStyle: {
    background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12,
  },
};

function Tile({ label, value, sub, tone }) {
  return (
    <div className={`adm-tile ${tone ? `adm-tile--${tone}` : ''}`}>
      <span className="adm-tile__label">{label}</span>
      <span className="adm-tile__value num">{value}</span>
      {sub ? <span className="adm-tile__sub">{sub}</span> : null}
    </div>
  );
}

function Panel({ title, note, children, wide }) {
  return (
    <section className={`adm-panel card ${wide ? 'adm-panel--wide' : ''}`}>
      <header className="adm-panel__head">
        <h3 className="adm-panel__title">{title}</h3>
        {note ? <span className="adm-panel__note">{note}</span> : null}
      </header>
      {children}
    </section>
  );
}

function Unavailable({ section }) {
  return (
    <p className="adm-empty">
      {section?.reason || 'Not available.'}
    </p>
  );
}

function Empty({ children = 'Nothing in this window.' }) {
  return <p className="adm-empty">{children}</p>;
}

function ChartBox({ children, height = 200 }) {
  return <div style={{ width: '100%', height }}>{children}</div>;
}

function LinkCell({ row, onOpen, children }) {
  if (!row.conversation_id || !onOpen) return <>{children}</>;
  return (
    <button type="button" className="adm-link"
            onClick={() => onOpen({ mode: 'fs', conversationId: row.conversation_id, userId: row.user_id })}
            title="Open this conversation">
      {children}
    </button>
  );
}

function QueryTable({ rows, cols, onOpen }) {
  if (!rows?.length) return <Empty />;
  return (
    <div className="adm-scroll">
      <table className="adm-table">
        <thead>
          <tr>{cols.map((c) => <th key={c.key} className={c.num ? 'is-num' : ''}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.event_id || `${r.conversation_id}-${i}`}>
              {cols.map((c) => (
                <td key={c.key} className={c.num ? 'is-num num' : ''}>
                  {c.key === 'query_text' || c.key === 'question'
                    ? <LinkCell row={r} onOpen={onOpen}>{clip(r[c.key], 80)}</LinkCell>
                    : c.render ? c.render(r) : r[c.key] ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TONE_ICON = { warn: 'alert', err: 'alert', info: 'info', ok: 'check' };

export default function InsightsPanel({ insights, onOpenConversation }) {
  if (!insights) return null;
  if (!insights.telemetry_available) {
    return (
      <div className="adm-notice adm-notice--warn">
        <Icon name="alert" size={16} />
        <div>
          <strong>Query telemetry is not recording on this database.</strong>
          <p>
            Usage, latency, cost and reliability insights need the
            {' '}<code>artha_query_events</code> table. It is created automatically when the
            database user is allowed to create tables; check the backend log for
            &ldquo;Could not create artha_query_events&rdquo;. Live-ingestion quality is shown below
            regardless.
          </p>
        </div>
      </div>
    );
  }

  const { usage, performance: perf, cost, reliability: rel, quality, tools, repeats, ingestion } = insights;
  const observations = deriveObservations(insights);
  const total = usage?.available ? Number(usage.queries || 0) : 0;
  const failed = rel?.available
    ? (rel.status || []).filter((s) => s.status !== 'ok').reduce((n, s) => n + Number(s.n || 0), 0)
    : 0;
  const unsRate = quality?.available ? pct(quality.unsourced?.unsourced, quality.unsourced?.total) : null;

  const usageDaily = (usage?.daily || []).map((d) => ({ ...d, label: fmtDay(d.day) }));
  const perfDaily = (perf?.daily || []).map((d) => ({
    label: fmtDay(d.day), p50: Number(d.p50), p90: Number(d.p90),
  }));
  const costDaily = (cost?.daily || []).map((d) => ({
    label: fmtDay(d.day),
    Prompt: Number(d.prompt_tokens), Completion: Number(d.completion_tokens),
  }));

  return (
    <div className="adm-insights">
      {observations.length > 0 && (
        <Panel title="Where to look first" note="Rules of thumb, ranked by attention needed" wide>
          <ul className="adm-obs">
            {observations.map((o) => (
              <li key={o.title} className={`adm-obs__item adm-obs__item--${o.tone}`}>
                <Icon name={TONE_ICON[o.tone] || 'info'} size={16} className="adm-obs__icon" />
                <div>
                  <strong>{o.title}</strong>
                  {o.detail ? <p>{o.detail}</p> : null}
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="adm-tiles">
        <Tile label="Queries" value={fmtNum(total)} sub={usage?.available ? `${fmtNum(usage.active_users)} active users` : null} />
        <Tile label="Median answer" value={fmtSecs(perf?.percentiles?.p50)} sub={`p90 ${fmtSecs(perf?.percentiles?.p90)}`} />
        <Tile label="Failed" value={fmtNum(failed)} sub={total ? fmtPct(pct(failed, total), 1) : null}
              tone={failed && pct(failed, total) > 5 ? 'err' : undefined} />
        <Tile label="Unsourced" value={fmtPct(unsRate)} sub="answered with no tool"
              tone={unsRate > 10 ? 'warn' : undefined} />
        <Tile label="Avg prompt" value={fmtNum(cost?.averages?.avg_prompt)} sub="tokens / query" />
        <Tile label="Uploads" value={fmtNum(ingestion?.documents)} sub={ingestion?.available ? `${fmtNum(ingestion.hand_edited)} hand-edited` : null} />
      </div>

      <div className="adm-grid">
        <Panel title="Queries per day">
          {usage?.available && usageDaily.length ? (
            <ChartBox>
              <ResponsiveContainer>
                <BarChart data={usageDaily} margin={{ top: 6, right: 8, left: -14, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--ink-200)" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: 'var(--ink-200)' }} />
                  <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip {...TOOLTIP} />
                  <Bar dataKey="queries" fill="var(--navy-700)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ChartBox>
          ) : usage?.available ? <Empty /> : <Unavailable section={usage} />}
        </Panel>

        <Panel title="Answer time" note="median and slowest 10%">
          {perf?.available && perfDaily.length ? (
            <ChartBox>
              <ResponsiveContainer>
                <LineChart data={perfDaily} margin={{ top: 6, right: 8, left: -14, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--ink-200)" />
                  <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: 'var(--ink-200)' }} />
                  <YAxis tick={AXIS} tickLine={false} axisLine={false} unit="s" />
                  <Tooltip {...TOOLTIP} formatter={(v) => `${Number(v).toFixed(1)}s`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="p50" name="median" stroke="var(--navy-700)" strokeWidth={2} dot={{ r: 2 }} />
                  <Line type="monotone" dataKey="p90" name="p90" stroke="var(--warn-600)" strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </ChartBox>
          ) : perf?.available ? <Empty /> : <Unavailable section={perf} />}
        </Panel>

        <Panel title="Tokens per day" note="cost proxy">
          {cost?.available && costDaily.length ? (
            <ChartBox>
              <ResponsiveContainer>
                <BarChart data={costDaily} margin={{ top: 6, right: 8, left: -6, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--ink-200)" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: 'var(--ink-200)' }} />
                  <YAxis tick={AXIS} tickLine={false} axisLine={false}
                         tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v)} />
                  <Tooltip {...TOOLTIP} formatter={(v) => fmtNum(v)} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="Prompt" stackId="t" fill="var(--navy-600)" />
                  <Bar dataKey="Completion" stackId="t" fill="var(--gold-600)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ChartBox>
          ) : cost?.available ? <Empty /> : <Unavailable section={cost} />}
        </Panel>

        <Panel title="Confidence of answers">
          {quality?.available ? (
            (quality.confidence || []).length ? (
              <ul className="adm-bars">
                {quality.confidence.map((c) => {
                  const sum = quality.confidence.reduce((n, x) => n + Number(x.n), 0);
                  const p = pct(c.n, sum);
                  return (
                    <li key={c.confidence}>
                      <span className="adm-bars__label">{c.confidence}</span>
                      <span className="adm-bars__track"><span className="adm-bars__fill" style={{ width: `${p}%` }} /></span>
                      <span className="adm-bars__val num">{fmtNum(c.n)} · {fmtPct(p)}</span>
                    </li>
                  );
                })}
              </ul>
            ) : <Empty />
          ) : <Unavailable section={quality} />}
        </Panel>
      </div>

      <div className="adm-grid adm-grid--tables">
        <Panel title="Slowest answers" note="click a question to read it" wide>
          {perf?.available ? (
            <QueryTable rows={perf.slowest} onOpen={onOpenConversation} cols={[
              { key: 'query_text', label: 'Question' },
              { key: 'username', label: 'User' },
              { key: 'elapsed_seconds', label: 'Time', num: true, render: (r) => fmtSecs(r.elapsed_seconds) },
              { key: 'tool_calls', label: 'Tools', num: true },
              { key: 'prompt_tokens', label: 'Prompt tok', num: true, render: (r) => fmtNum(r.prompt_tokens) },
            ]} />
          ) : <Unavailable section={perf} />}
        </Panel>

        <Panel title="Heaviest prompts" note="where context is bloated" wide>
          {cost?.available ? (
            <QueryTable rows={cost.heaviest_queries} onOpen={onOpenConversation} cols={[
              { key: 'query_text', label: 'Question' },
              { key: 'username', label: 'User' },
              { key: 'prompt_tokens', label: 'Prompt tok', num: true, render: (r) => fmtNum(r.prompt_tokens) },
              { key: 'completion_tokens', label: 'Completion', num: true, render: (r) => fmtNum(r.completion_tokens) },
              { key: 'tool_calls', label: 'Tools', num: true },
            ]} />
          ) : <Unavailable section={cost} />}
        </Panel>

        <Panel title="Recent failures" note="never stored in chat history" wide>
          {rel?.available ? (
            <QueryTable rows={rel.recent_failures} cols={[
              { key: 'created_at', label: 'When', render: (r) => fmtDateTime(r.created_at) },
              { key: 'username', label: 'User' },
              { key: 'query_text', label: 'Question' },
              { key: 'status', label: 'Status' },
              { key: 'error_stage', label: 'Stage' },
              { key: 'error_message', label: 'Message', render: (r) => clip(r.error_message, 70) },
            ]} />
          ) : <Unavailable section={rel} />}
        </Panel>

        <Panel title="Unsourced answers" note="no tool was called" wide>
          {quality?.available ? (
            <QueryTable rows={quality.unsourced_recent} onOpen={onOpenConversation} cols={[
              { key: 'created_at', label: 'When', render: (r) => fmtDateTime(r.created_at) },
              { key: 'username', label: 'User' },
              { key: 'query_text', label: 'Question' },
            ]} />
          ) : <Unavailable section={quality} />}
        </Panel>

        <Panel title="Questions that found nothing" note="detected by phrase — a hint, not a measurement" wide>
          {quality?.available ? (
            <QueryTable rows={quality.no_data_answers} onOpen={onOpenConversation} cols={[
              { key: 'created_at', label: 'When', render: (r) => fmtDateTime(r.created_at) },
              { key: 'username', label: 'User' },
              { key: 'question', label: 'Question' },
            ]} />
          ) : <Unavailable section={quality} />}
        </Panel>

        <Panel title="Repeated questions" note="candidates for a suggestion or cache">
          {repeats?.available ? (
            (repeats.questions || []).length ? (
              <ul className="adm-list">
                {repeats.questions.map((q) => (
                  <li key={q.question}>
                    <span className="adm-list__main">{clip(q.question, 90)}</span>
                    <span className="adm-list__meta num">{fmtNum(q.asked)}× · {fmtNum(q.users)} user(s)</span>
                  </li>
                ))}
              </ul>
            ) : <Empty>No question was asked more than once.</Empty>
          ) : <Unavailable section={repeats} />}
        </Panel>

        <Panel title="Tool usage" note={tools?.available ? `${(tools.unused_in_window || []).length} unused this window` : null}>
          {tools?.available ? (
            <>
              {(tools.used || []).length ? (
                <ul className="adm-bars">
                  {tools.used.slice(0, 12).map((t) => {
                    const max = Number(tools.used[0].calls) || 1;
                    return (
                      <li key={t.tool}>
                        <span className="adm-bars__label adm-bars__label--wide" title={t.tool}>{t.tool}</span>
                        <span className="adm-bars__track"><span className="adm-bars__fill" style={{ width: `${(Number(t.calls) / max) * 100}%` }} /></span>
                        <span className="adm-bars__val num">{fmtNum(t.calls)}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : <Empty />}
              {(tools.unused_in_window || []).length > 0 && (
                <p className="adm-foot">Seen before, unused now: {tools.unused_in_window.join(', ')}</p>
              )}
            </>
          ) : <Unavailable section={tools} />}
        </Panel>

        <Panel title="Live ingestion quality" note="how well uploaded scans were read" wide>
          {ingestion?.available ? (
            Number(ingestion.documents) === 0 ? <Empty>No uploads in this window.</Empty> : (
              <div className="adm-ingest">
                <div className="adm-ingest__stats">
                  <Tile label="Documents" value={fmtNum(ingestion.documents)} />
                  <Tile label="Avg unreadable cells" value={fmtNum(ingestion.avg_unreadable, 1)} />
                  <Tile label="Avg recovered" value={fmtNum(ingestion.avg_recovered, 1)} />
                  <Tile label="Hand-edited" value={fmtNum(ingestion.hand_edited)} sub="user corrected extraction" />
                </div>
                <div className="adm-ingest__cols">
                  <div>
                    <h4 className="adm-sub">By grade</h4>
                    <ul className="adm-list">
                      {(ingestion.by_grade || []).map((g) => (
                        <li key={g.grade}><span className="adm-list__main">{g.grade}</span><span className="adm-list__meta num">{fmtNum(g.n)}</span></li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="adm-sub">By ingest version</h4>
                    <ul className="adm-list">
                      {(ingestion.by_version || []).map((v) => (
                        <li key={v.version}>
                          <span className="adm-list__main">{v.version}</span>
                          <span className="adm-list__meta num">{fmtNum(v.n)} docs · {fmtNum(v.avg_unreadable, 1)} unreadable avg</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
                <h4 className="adm-sub">Documents with the most unreadable cells</h4>
                <QueryTable rows={ingestion.worst_documents} cols={[
                  { key: 'filename', label: 'File', render: (r) => clip(r.filename, 44) },
                  { key: 'company', label: 'Company', render: (r) => clip(r.company, 30) },
                  { key: 'financial_year', label: 'FY' },
                  { key: 'grade', label: 'Grade' },
                  { key: 'unreadable', label: 'Unreadable', num: true },
                  { key: 'failed_footings', label: 'Failed footings', num: true },
                ]} />
              </div>
            )
          ) : <Unavailable section={ingestion} />}
        </Panel>
      </div>
    </div>
  );
}
