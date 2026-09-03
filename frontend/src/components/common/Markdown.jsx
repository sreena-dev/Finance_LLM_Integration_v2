import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './Markdown.css';

/**
 * Renders pipeline markdown. Both pipelines emit GFM tables (financial
 * statements, CARO clause grids), so remark-gfm is required, and tables are
 * wrapped so a wide one scrolls inside its own box instead of stretching the
 * page.
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
