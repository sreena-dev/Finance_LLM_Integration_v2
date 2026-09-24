import Icon from '../common/Icon';
import Notice from '../common/Notice';
import { clip, fmtDateTime, fmtNum, fmtSecs } from './format';

// Presentational: everything arrives as props; AdminDashboard owns the fetching.

function Stat({ label, value }) {
  return (
    <div className="adm-tile">
      <span className="adm-tile__label">{label}</span>
      <span className="adm-tile__value num">{value}</span>
    </div>
  );
}

export default function UserDetail({
  user, conversations, convosState, mode, onMode,
  events, eventsState, statusFilter, onStatusFilter, onOpenConversation, onBack,
}) {
  if (!user) return null;

  return (
    <div className="adm-detail">
      <button type="button" className="adm-back" onClick={onBack}>
        <Icon name="chevron" size={14} className="adm-back__icon" /> All users
      </button>

      <header className="adm-detail__head">
        <div>
          <h2 className="adm-detail__name">
            {user.display_name || user.username}
            {user.is_super_admin && <span className="pill pill--navy">admin</span>}
          </h2>
          <p className="adm-detail__sub">{user.username} · {user.email}</p>
          <p className="adm-detail__sub">
            Joined {fmtDateTime(user.created_at)} · last sign-in {fmtDateTime(user.last_login_at)}
          </p>
        </div>
      </header>

      <div className="adm-tiles adm-tiles--compact">
        <Stat label="FS conversations" value={fmtNum(user.fs_conversations)} />
        <Stat label="TB conversations" value={fmtNum(user.tb_conversations)} />
        <Stat label="Queries" value={fmtNum(user.queries)} />
        <Stat label="Avg answer time" value={fmtSecs(user.avg_elapsed)} />
        <Stat label="Failed" value={fmtNum(user.errors)} />
        <Stat label="Uploads" value={fmtNum(user.uploads)} />
      </div>

      <section className="adm-panel card">
        <header className="adm-panel__head">
          <h3 className="adm-panel__title">Conversations</h3>
          <div className="adm-seg" role="tablist" aria-label="Conversation source">
            {[['fs', 'Financial Statement'], ['tb', 'Trial Balance']].map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={mode === id}
                      className={`adm-seg__btn ${mode === id ? 'is-on' : ''}`}
                      onClick={() => onMode(id)}>{label}</button>
            ))}
          </div>
        </header>

        {convosState?.loading ? <p className="adm-empty">Loading…</p>
          : convosState?.error ? <Notice tone="warn">{convosState.error}</Notice>
          : (conversations || []).length === 0 ? <p className="adm-empty">No conversations.</p> : (
            <ul className="adm-convos">
              {conversations.map((c) => (
                <li key={c.conversation_id}>
                  <button type="button" className="adm-convo"
                          onClick={() => onOpenConversation({ mode, conversationId: c.conversation_id, userId: user.user_id })}>
                    <span className="adm-convo__title">{c.title}</span>
                    <span className="adm-convo__meta">
                      {fmtNum(c.n_messages)} messages · {fmtDateTime(c.last_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
      </section>

      <section className="adm-panel card">
        <header className="adm-panel__head">
          <h3 className="adm-panel__title">Query log</h3>
          <div className="adm-seg" role="tablist" aria-label="Filter by status">
            {[['', 'All'], ['ok', 'OK'], ['error', 'Errors']].map(([id, label]) => (
              <button key={id || 'all'} type="button" role="tab" aria-selected={statusFilter === id}
                      className={`adm-seg__btn ${statusFilter === id ? 'is-on' : ''}`}
                      onClick={() => onStatusFilter(id)}>{label}</button>
            ))}
          </div>
        </header>

        {eventsState?.loading ? <p className="adm-empty">Loading…</p>
          : eventsState?.available === false
            ? <p className="adm-empty">{eventsState.reason}</p>
            : (events || []).length === 0 ? <p className="adm-empty">No recorded queries yet.</p> : (
              <div className="adm-scroll">
                <table className="adm-table">
                  <thead>
                    <tr>
                      <th>When</th><th>Question</th><th>Status</th>
                      <th className="is-num">Time</th><th className="is-num">Tools</th>
                      <th className="is-num">Tokens</th><th>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((e) => (
                      <tr key={e.event_id}>
                        <td>{fmtDateTime(e.created_at)}</td>
                        <td>
                          {e.conversation_id ? (
                            <button type="button" className="adm-link"
                                    onClick={() => onOpenConversation({ mode: 'fs', conversationId: e.conversation_id, userId: user.user_id })}>
                              {clip(e.query_text, 70)}
                            </button>
                          ) : clip(e.query_text, 70)}
                          {e.status !== 'ok' && e.error_message
                            ? <span className="adm-bad adm-sub-line">{e.error_stage}: {clip(e.error_message, 80)}</span> : null}
                        </td>
                        <td><span className={`pill pill--${e.status === 'ok' ? 'ok' : 'err'}`}>{e.status}</span></td>
                        <td className="is-num num">{fmtSecs(e.elapsed_seconds)}</td>
                        <td className="is-num num">{fmtNum(e.tool_calls)}</td>
                        <td className="is-num num">{fmtNum((e.prompt_tokens || 0) + (e.completion_tokens || 0)) }</td>
                        <td>{e.confidence || (e.unsourced ? 'unsourced' : '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </section>
    </div>
  );
}
