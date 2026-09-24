import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import AnswerCard from '../chat/AnswerCard';
import { fmtDateTime } from './format';

// A conversation, READ-ONLY: no composer, no delete, nothing that writes.
//
// Financial Statement answers render through the same AnswerCard the user saw,
// so an admin reviews exactly what was shown (evidence, checks, trend chart).
// `mode` is deliberately not passed: AnswerCard's "show the scan" links call the
// per-user document endpoints, which correctly refuse another user's files, so
// the affordance is left out rather than offered and failing.
//
// Trial Balance turns are shown as their stored text. Their rich audit cards
// need that user's loaded documents to render, which an admin view doesn't have.

export default function ConversationViewer({ conversation, onBack }) {
  if (!conversation) return null;
  const { mode, username, messages = [] } = conversation;

  return (
    <div className="adm-viewer">
      <button type="button" className="adm-back" onClick={onBack}>
        <Icon name="chevron" size={14} className="adm-back__icon" /> Back
      </button>

      <header className="adm-viewer__head">
        <h2 className="adm-detail__name">
          {mode === 'tb' ? 'Trial Balance' : 'Financial Statement'} conversation
        </h2>
        <p className="adm-detail__sub">
          {username || 'Unknown user'} · {messages.length} message{messages.length === 1 ? '' : 's'} · read-only
        </p>
      </header>

      <div className="adm-thread">
        {messages.map((m, i) => (
          <div key={`${m.seq}-${i}`} className={`adm-msg adm-msg--${m.role}`}>
            <div className="adm-msg__meta">
              {m.role === 'user' ? (username || 'User') : 'Artha.AI'} · {fmtDateTime(m.created_at)}
              {m.rewritten_query ? <span className="adm-msg__rewrite"> · read as “{m.rewritten_query}”</span> : null}
            </div>

            {m.role === 'user' ? (
              <div className="adm-msg__bubble">{m.content}</div>
            ) : mode === 'fs' ? (
              <AnswerCard result={m.payload || { final_answer: m.content }} />
            ) : (
              <div className="adm-msg__bubble adm-msg__bubble--md">
                <Markdown>{m.content || ''}</Markdown>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
