/**
 * The Query pane: chat log (Turn), suggestion chips, and the composer.
 * Split out of `FdrAnalysis.jsx` — see that file's header for why.
 */
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Empty, SendIcon, Sources, Provenance } from './FdrShared';

export function Turn({ turn }) {
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

export function QueryPane({ turns, pending, progress, draft, entityId, onAsk }) {
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

export function QueryComposer({ onSend, disabled, placeholder }) {
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
