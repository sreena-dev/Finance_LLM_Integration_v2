/**
 * Saved conversations for a mode, in the sidebar.
 *
 * Generic over any mode that persists a conversation server-side; App.jsx
 * mounts one instance per such mode (currently financial-statement and
 * trial-balance), each wired to that mode's own list/select/new/delete
 * handlers. No mode-specific logic lives in this file.
 *
 * Its own file and its own CSS: `chat/ChatView.css` is imported by ReportView
 * and shared with SAR Q&A, so adding rules there would reach a working screen
 * this change has no business touching.
 */

import { useState } from 'react';
import { motion } from 'framer-motion';
import Icon from '../common/Icon';
import './ConversationList.css';

function relative(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export default function ConversationList({
  conversations,
  activeId,
  loading,
  error,
  onSelect,
  onNew,
  onDelete,
}) {
  const [confirming, setConfirming] = useState(null);

  return (
    <div className="convo">
      <div className="convo__head">
        <p className="convo__heading">Conversations</p>
        <button type="button" className="convo__new" onClick={onNew} title="Start a new conversation">
          <Icon name="plus" size={14} />
          New
        </button>
      </div>

      <div className="convo__scroll">
        {error ? (
          <p className="convo__empty convo__empty--err">{error}</p>
        ) : loading ? (
          <p className="convo__empty">Loading…</p>
        ) : conversations.length === 0 ? (
          <p className="convo__empty">
            Your questions are saved here so you can pick a thread back up later.
          </p>
        ) : (
          conversations.map((c) => {
            const active = c.conversation_id === activeId;
            const isConfirming = confirming === c.conversation_id;
            return (
              <div key={c.conversation_id} className={`convo__row ${active ? 'is-active' : ''}`}>
                {/* A different layoutId from the mode switcher's "side-active".
                    Sharing it would make framer-motion animate one pill BETWEEN
                    the two lists as the selection moved. */}
                {active && (
                  <motion.span layoutId="side-convo-active" className="convo__active-bg" />
                )}
                <button
                  type="button"
                  className="convo__item"
                  onClick={() => onSelect(c.conversation_id)}
                  title={c.title}
                >
                  <span className="convo__title">{c.title}</span>
                  <span className="convo__meta">{relative(c.last_at)}</span>
                </button>

                {isConfirming ? (
                  <span className="convo__confirm">
                    <button
                      type="button"
                      className="convo__confirm-yes"
                      onClick={() => {
                        setConfirming(null);
                        onDelete(c.conversation_id);
                      }}
                    >
                      Delete
                    </button>
                    <button
                      type="button"
                      className="convo__confirm-no"
                      onClick={() => setConfirming(null)}
                    >
                      Keep
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="convo__del"
                    // Two steps, because this is not undoable and the target is
                    // a small control in a dense list.
                    onClick={() => setConfirming(c.conversation_id)}
                    title="Delete this conversation"
                    aria-label={`Delete conversation: ${c.title}`}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
