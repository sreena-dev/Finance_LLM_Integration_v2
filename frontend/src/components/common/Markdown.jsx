import { Children, isValidElement } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import FigureCell from './FigureCell';
import { TAGS, looksNumeric, parseFigureCell } from '../../lib/figures';
import './Markdown.css';

/** The plain text of a react-markdown node's children, or null if it is not just text. */
function plainText(children) {
  const parts = Children.toArray(children);
  if (parts.length === 0) return '';
  return parts.every((p) => typeof p === 'string' || typeof p === 'number')
    ? parts.join('')
    : null;
}

/**
 * Renders pipeline markdown. Both pipelines emit GFM tables (financial
 * statements, CARO clause grids), so remark-gfm is required, and tables are
 * wrapped so a wide one scrolls inside its own box instead of stretching the
 * page.
 *
 * Two additions for the audit surface:
 *  - a table cell holding a withheld / recovered / user-entered marker is drawn
 *    as a labelled figure (see lib/figures.js), and a plain figure is right-
 *    aligned in mono but otherwise left exactly as printed;
 *  - a paragraph the model opens with a bold tag (FINDING, RISK FLAG, AUDIT
 *    POINTER, COVERAGE NOTE) becomes a tag block with its own treatment.
 */
export default function Markdown({ children, className = '' }) {
  if (!children) return null;
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ node, ...props }) => (
            <div className="md__table-wrap">
              <table {...props} />
            </div>
          ),
          tr: ({ node, children: rowChildren, ...props }) => {
            const first = Children.toArray(rowChildren).find((c) => isValidElement(c));
            const label = first ? plainText(first.props.children) : null;
            const total = label && /^\s*(total|totai)\b/i.test(label);
            return <tr {...props} className={total ? 'md__total' : undefined}>{rowChildren}</tr>;
          },
          td: ({ node, children: cellChildren, ...props }) => {
            const text = plainText(cellChildren);
            if (text === null) return <td {...props}>{cellChildren}</td>;
            const state = parseFigureCell(text).kind;
            const numeric = state !== 'plain' || looksNumeric(text);
            return (
              <td {...props} className={numeric ? 'md__num' : undefined}>
                <FigureCell text={text} />
              </td>
            );
          },
          p: ({ node, children: pChildren, ...props }) => {
            const [first, ...rest] = Children.toArray(pChildren);
            if (isValidElement(first) && first.type === 'strong') {
              const tag = plainText(first.props.children)?.trim().replace(/:$/, '').toUpperCase();
              if (tag && TAGS[tag]) {
                return (
                  <div className={`tagblock tagblock--${TAGS[tag]}`}>
                    <span className="tagblock__tag">{tag}</span>
                    <span className="tagblock__text">{rest}</span>
                  </div>
                );
              }
            }
            return <p {...props}>{pChildren}</p>;
          },
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
