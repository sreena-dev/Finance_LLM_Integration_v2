import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import CopyButton from '../common/CopyButton';
import './AnswerCard.css';

function Collapsible({ title, count, icon, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`collapse ${open ? 'is-open' : ''}`}>
      <button type="button" className="collapse__head" onClick={() => setOpen((v) => !v)}>
        <Icon name={icon} size={15} className="collapse__icon" />
        <span className="collapse__title">{title}</span>
        {count != null && <span className="pill pill--mute">{count}</span>}
        <motion.span
          className="collapse__caret"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <Icon name="chevron" size={15} />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="collapse__body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="collapse__inner">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SourceChunk({ chunk }) {
  const [expanded, setExpanded] = useState(false);
  const content = chunk.content || '';
  const isLong = content.length > 520;
  const shown = expanded || !isLong ? content : `${content.slice(0, 520)}…`;

  return (
    <div className="chunk">
      <div className="chunk__head">
        <span className="chunk__index">{chunk.index}</span>
        <span className="chunk__source">{chunk.source}</span>
        {chunk.hint_matched && <span className="pill pill--navy">hint</span>}
        {chunk.rerank_score != null && (
          <span className="chunk__score" title="Reranker score">
            {chunk.rerank_score.toFixed(3)}
          </span>
        )}
      </div>

      <p className="chunk__text">{shown}</p>

      {isLong && (
        <button type="button" className="chunk__more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show less' : 'Show full extract'}
        </button>
      )}
    </div>
  );
}

export default function AnswerCard({ result }) {
  const {
    summary,
    final_answer: answer,
    evidences_md: evidence,
    chunks = [],
    num_tables_searched: tables,
    num_chunks_retrieved: retrieved,
    elapsed_seconds: elapsed,
  } = result;

  const copyText = [summary, answer, evidence].filter(Boolean).join('\n\n');

  return (
    <article className="answer card">
      <header className="answer__head">
        <span className="answer__mark">
          <Icon name="sparkle" size={15} />
        </span>
        <span className="answer__label">Answer</span>

        <div className="answer__meta">
          {tables > 0 && <span className="answer__stat">{tables} tables searched</span>}
          {retrieved > 0 && <span className="answer__stat">{retrieved} chunks retrieved</span>}
          {elapsed > 0 && <span className="answer__stat">{elapsed.toFixed(1)}s</span>}
        </div>

        <CopyButton text={copyText} />
      </header>

      {summary && (
        <div className="answer__summary">
          <Markdown>{summary}</Markdown>
        </div>
      )}

      {answer && (
        <div className="answer__body">
          <Markdown>{answer}</Markdown>
        </div>
      )}

      {!summary && !answer && (
        <p className="answer__blank">The pipeline returned an empty answer.</p>
      )}

      {(evidence || chunks.length > 0) && (
        <div className="answer__extras">
          {evidence && (
            <Collapsible title="Evidence" icon="doc">
              <Markdown>{evidence}</Markdown>
            </Collapsible>
          )}

          {chunks.length > 0 && (
            <Collapsible title="Retrieved sources" icon="search" count={chunks.length}>
              <div className="answer__chunks">
                {chunks.map((c) => (
                  <SourceChunk key={c.index} chunk={c} />
                ))}
              </div>
            </Collapsible>
          )}
        </div>
      )}
    </article>
  );
}
