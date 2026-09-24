import { useCallback, useEffect, useState } from 'react';
import {
  adminAudit, adminConversation, adminInsights, adminUser, adminUserConversations,
  adminUserEvents, adminUsers,
} from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import ConversationViewer from './ConversationViewer';
import InsightsPanel from './InsightsPanel';
import UserDetail from './UserDetail';
import UsersTable from './UsersTable';
import { fmtDateTime } from './format';
import './AdminDashboard.css';

const TABS = [
  ['insights', 'Insights'],
  ['users', 'Users & chats'],
  ['audit', 'Access log'],
];
const WINDOWS = [7, 30, 90];

/** A loader for one piece of data: `{ data, loading, error }`. A 403 means the
 * server no longer considers the caller an admin (e.g. revoked while signed in),
 * which ends the session in this view rather than showing a broken page. */
function useLoad(fn, deps, onForbidden) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => { if (live) setState({ data, loading: false, error: null }); })
      .catch((err) => {
        if (!live) return;
        if (err.status === 403) onForbidden?.(err);
        else if (err.status !== 401) setState({ data: null, loading: false, error: err.message });
      });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export default function AdminDashboard({ onExit }) {
  const [tab, setTab] = useState('insights');
  const [days, setDays] = useState(30);
  const [forbidden, setForbidden] = useState(null);

  // Drill-down. `userId` opens a user; `viewing` opens a conversation on top.
  const [userId, setUserId] = useState(null);
  const [viewing, setViewing] = useState(null); // { mode, conversationId, userId }
  const [convoMode, setConvoMode] = useState('fs');
  const [statusFilter, setStatusFilter] = useState('');

  const onForbidden = useCallback((err) => setForbidden(err.message), []);

  const insights = useLoad(() => adminInsights(days), [days], onForbidden);
  const users = useLoad(() => adminUsers(), [], onForbidden);
  const audit = useLoad(() => (tab === 'audit' ? adminAudit() : Promise.resolve(null)), [tab], onForbidden);

  const userState = useLoad(
    () => (userId ? adminUser(userId) : Promise.resolve(null)), [userId], onForbidden);
  const convos = useLoad(
    () => (userId ? adminUserConversations(userId, convoMode) : Promise.resolve(null)),
    [userId, convoMode], onForbidden);
  const events = useLoad(
    () => (userId ? adminUserEvents(userId, { status: statusFilter }) : Promise.resolve(null)),
    [userId, statusFilter], onForbidden);
  const conversation = useLoad(
    () => (viewing ? adminConversation(viewing.mode, viewing.conversationId) : Promise.resolve(null)),
    [viewing], onForbidden);

  const openConversation = useCallback((v) => setViewing(v), []);

  if (forbidden) {
    return (
      <div className="adm">
        <div className="adm__body">
          <Notice tone="warn" title="You don't have access to this area"
                  action={<button type="button" className="btn btn--ghost btn--sm" onClick={onExit}>Go back</button>}>
            {forbidden} Your account may have had admin rights removed.
          </Notice>
        </div>
      </div>
    );
  }

  const inConversation = Boolean(viewing);
  const inUser = Boolean(userId) && !inConversation;

  return (
    <div className="adm">
      <header className="adm__head">
        <div>
          <h1 className="adm__title">Admin</h1>
          <p className="adm__sub">Users, their conversations and activity, and where the system can be improved.</p>
        </div>
        <span className="pill pill--navy"><Icon name="shield" size={12} /> Super administrator</span>
      </header>

      {!inConversation && !inUser && (
        <nav className="adm__tabs" role="tablist" aria-label="Admin sections">
          {TABS.map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id}
                    className={`adm__tab ${tab === id ? 'is-on' : ''}`} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
          {tab === 'insights' && (
            <span className="adm__window" role="group" aria-label="Time window">
              {WINDOWS.map((d) => (
                <button key={d} type="button" className={`adm-seg__btn ${days === d ? 'is-on' : ''}`}
                        onClick={() => setDays(d)}>{d}d</button>
              ))}
            </span>
          )}
        </nav>
      )}

      <div className="adm__body">
        {inConversation ? (
          conversation.loading ? <p className="adm-empty">Loading conversation…</p>
            : conversation.error ? (
              <Notice tone="error" title="Could not open that conversation"
                      action={<button type="button" className="btn btn--ghost btn--sm" onClick={() => setViewing(null)}>Back</button>}>
                {conversation.error}
              </Notice>
            ) : (
              <ConversationViewer conversation={conversation.data} onBack={() => setViewing(null)} />
            )
        ) : inUser ? (
          userState.loading ? <p className="adm-empty">Loading user…</p>
            : userState.error ? <Notice tone="error">{userState.error}</Notice>
            : (
              <UserDetail
                user={userState.data?.user}
                conversations={convos.data?.conversations}
                convosState={convos}
                mode={convoMode}
                onMode={setConvoMode}
                events={events.data?.events}
                eventsState={{ loading: events.loading, available: events.data?.available, reason: events.data?.reason }}
                statusFilter={statusFilter}
                onStatusFilter={setStatusFilter}
                onOpenConversation={openConversation}
                onBack={() => { setUserId(null); setStatusFilter(''); setConvoMode('fs'); }}
              />
            )
        ) : tab === 'insights' ? (
          insights.loading ? <p className="adm-empty">Crunching the last {days} days…</p>
            : insights.error ? <Notice tone="error" title="Could not load insights">{insights.error}</Notice>
            : <InsightsPanel insights={insights.data} onOpenConversation={openConversation} />
        ) : tab === 'users' ? (
          users.loading ? <p className="adm-empty">Loading users…</p>
            : users.error ? <Notice tone="error" title="Could not load users">{users.error}</Notice>
            : <UsersTable users={users.data?.users} tb={users.data?.tb} onOpen={setUserId} />
        ) : (
          audit.loading ? <p className="adm-empty">Loading…</p>
            : audit.error ? <Notice tone="error">{audit.error}</Notice>
            : (
              <section className="adm-panel card">
                <header className="adm-panel__head">
                  <h3 className="adm-panel__title">Admin access log</h3>
                  <span className="adm-panel__note">Every time an admin opens a user or a conversation</span>
                </header>
                {(audit.data?.entries || []).length === 0 ? <p className="adm-empty">Nothing recorded yet.</p> : (
                  <div className="adm-scroll">
                    <table className="adm-table">
                      <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>About</th><th>Detail</th></tr></thead>
                      <tbody>
                        {audit.data.entries.map((a, i) => (
                          <tr key={`${a.at}-${i}`}>
                            <td>{fmtDateTime(a.at)}</td>
                            <td>{a.admin_username || '—'}</td>
                            <td>{a.action.replace(/_/g, ' ')}</td>
                            <td>{a.target_username || '—'}</td>
                            <td>{a.detail || ''}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )
        )}
      </div>
    </div>
  );
}
