import { useMemo, useState } from 'react';
import { fmtNum, fmtSecs, relTime } from './format';

// Presentational: the users payload arrives as a prop.

function lastSeen(u) {
  return [u.last_login_at, u.last_message_at, u.last_query_at, u.tb_last_at]
    .filter(Boolean)
    .sort()
    .pop() || null;
}

export default function UsersTable({ users = [], tb, onOpen }) {
  const [q, setQ] = useState('');

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return users
      .filter((u) => !needle
        || (u.username || '').toLowerCase().includes(needle)
        || (u.email || '').toLowerCase().includes(needle)
        || (u.display_name || '').toLowerCase().includes(needle))
      .map((u) => ({ ...u, _seen: lastSeen(u) }))
      .sort((a, b) => String(b._seen || '').localeCompare(String(a._seen || '')));
  }, [users, q]);

  return (
    <div className="adm-users">
      <div className="adm-users__bar">
        <input
          type="search"
          className="adm-input"
          placeholder="Search name or email…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search users"
        />
        <span className="adm-users__count">{rows.length} of {users.length} users</span>
      </div>

      {tb && tb.available === false && (
        <p className="adm-foot">
          Trial Balance counts are unavailable right now ({tb.reason || 'its database could not be reached'}).
          Financial Statement data is unaffected.
        </p>
      )}

      <div className="adm-scroll">
        <table className="adm-table adm-table--rows">
          <thead>
            <tr>
              <th>User</th>
              <th className="is-num">FS chats</th>
              <th className="is-num">TB chats</th>
              <th className="is-num">Queries</th>
              <th className="is-num">Avg time</th>
              <th className="is-num">Errors</th>
              <th className="is-num">Uploads</th>
              <th>Last active</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.user_id} className="is-clickable" onClick={() => onOpen?.(u.user_id)}
                  tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') onOpen?.(u.user_id); }}>
                <td>
                  <span className="adm-user">
                    <span className="adm-user__name">{u.display_name || u.username}</span>
                    {u.is_super_admin && <span className="pill pill--navy">admin</span>}
                  </span>
                  <span className="adm-user__email">{u.username} · {u.email}</span>
                </td>
                <td className="is-num num">{fmtNum(u.fs_conversations)}</td>
                <td className="is-num num">{fmtNum(u.tb_conversations)}</td>
                <td className="is-num num">{fmtNum(u.queries)}</td>
                <td className="is-num num">{fmtSecs(u.avg_elapsed)}</td>
                <td className={`is-num num ${Number(u.errors) > 0 ? 'adm-bad' : ''}`}>{fmtNum(u.errors)}</td>
                <td className="is-num num">{fmtNum(u.uploads)}</td>
                <td title={u._seen || ''}>{relTime(u._seen)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={8} className="adm-empty">No users match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
