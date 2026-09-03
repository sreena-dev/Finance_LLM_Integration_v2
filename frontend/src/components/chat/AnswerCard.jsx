import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import CopyButton from '../common/CopyButton';
import CitationViewer from './CitationViewer';
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

function SourceChunk({ chunk, onShowScan }) {
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
        {onShowScan && (
          <button type="button" className="chunk__scan" onClick={onShowScan}
                  title="Show the scanned region this was read from">
            <Icon name="search" size={12} /> scan
          </button>
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

export default function AnswerCard({ result, mode, conversationId }) {
  const {
    summary,
    final_answer: answer,
    evidences_md: evidence,
    chunks = [],
    num_tables_searched: tables,
    num_chunks_retrieved: retrieved,
    elapsed_seconds: elapsed,
    // Upload-only fields. Defaulted rather than optional-chained at every use,
    // because a conversation saved before this feature existed rehydrates from
    // a stored payload that has neither key.
    materiality_legend: legend = null,
    uploaded_documents: uploaded = [],
  } = result || {};

  // Which citation's scanned region is open, if any.
  const [citation, setCitation] = useState(null);

  // The legend is part of the answer for anyone copying it into a working
  // paper: a set of flags without the threshold they were derived against is
  // not reviewable, which is the whole reason it is emitted.
  const copyText = [summary, answer, evidence, legend?.markdown]
    .filter(Boolean)
    .join('\n\n');

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

      {uploaded.length > 0 && (
        <div className="answer__uploads">
          <Icon name="doc" size={13} />
          <span>
            Answered against {uploaded.length} uploaded document
            {uploaded.length === 1 ? '' : 's'}:{' '}
            {uploaded.map((d) => d.financial_year || d.filename).join(', ')}
          </span>
          {/* The one number a reader needs to see without opening anything:
              if figures were withheld, any silence in this answer may be an
              extraction gap rather than a disclosure gap. */}
          {uploaded.some((d) => d.unreadable_cells > 0) && (
            <span className="pill pill--warn">
              {uploaded.reduce((n, d) => n + (d.unreadable_cells || 0), 0)} figure(s) withheld
            </span>
          )}
        </div>
      )}

      {legend?.markdown && (
        <div className={`answer__legend ${legend.provisional ? 'is-provisional' : ''}`}>
          <Markdown>{legend.markdown}</Markdown>
        </div>
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
                  <SourceChunk
                    key={c.index}
                    chunk={c}
                    onShowScan={
                      // Only uploaded documents carry a scan to show. Corpus
                      // chunks have no page image, so no affordance is offered
                      // for them rather than one that fails when clicked.
                      c.doc_id && c.table_id && conversationId && mode
                        ? () => setCitation({ docId: c.doc_id, tableId: c.table_id, caption: c.source })
                        : null
                    }
                  />
                ))}
              </div>
            </Collapsible>
          )}
        </div>
      )}

      {citation && (
        <CitationViewer
          mode={mode}
          conversationId={conversationId}
          docId={citation.docId}
          tableId={citation.tableId}
          caption={citation.caption}
          onClose={() => setCitation(null)}
        />
      )}
    </article>
  );
}
