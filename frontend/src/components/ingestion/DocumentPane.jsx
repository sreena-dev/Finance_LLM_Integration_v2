import { useCallback, useEffect, useState } from 'react';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import { fsDocumentPages, fsPageImage, fsPageText } from '../../api/client';
import './DocumentPane.css';

/**
 * The processed pages of one uploaded document, page by page.
 *
 * `CitationViewer` shows a single table's cropped scan region, reached from a
 * citation. This is the wider version: the whole document, reached from a
 * document chip, so a reader can page through what OCR actually saw rather
 * than checking one figure at a time. The scanned page image is the default
 * view; a toggle switches the same page to what was extracted from it
 * (narrative chunks and verified tables), so the reader can compare the two.
 *
 * Docked as a persistent right-hand pane rather than a modal — App.jsx wraps
 * this and `ChatView` in a flex row, so it sits beside the conversation
 * instead of covering it. Exit animation is deliberately absent, same
 * reasoning `CitationViewer` already documents: App.jsx's `.main__pane` has no
 * `AnimatePresence` around it (a nested one deadlocked previously), and this
 * pane lives inside that same wrapper.
 */
export default function DocumentPane({ mode, conversationId, doc, onClose }) {
  const [pageNos, setPageNos] = useState([]);
  const [pageNo, setPageNo] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [viewMode, setViewMode] = useState('image'); // 'image' | 'text'

  const docId = doc?.doc_id;

  // The manifest, fetched once per document: which pages actually have an
  // image (blank/duplicate pages are skipped during ingestion, so numbers can
  // jump), so prev/next steps through what exists rather than raw ±1.
  useEffect(() => {
    let cancelled = false;
    setPageNos([]);
    setPageNo(null);
    setManifestError(null);
    if (!mode || !conversationId || !docId) return undefined;

    fsDocumentPages(mode, conversationId, docId)
      .then(({ pages }) => {
        if (cancelled) return;
        const nos = (pages || []).map((p) => p.page_no).filter((n) => n != null);
        setPageNos(nos);
        setPageNo(nos[0] ?? null);
      })
      .catch((e) => { if (!cancelled) setManifestError(e.message); });

    return () => { cancelled = true; };
  }, [mode, conversationId, docId]);

  const index = pageNos.indexOf(pageNo);
  const goPrev = useCallback(() => { if (index > 0) setPageNo(pageNos[index - 1]); }, [index, pageNos]);
  const goNext = useCallback(() => {
    if (index >= 0 && index < pageNos.length - 1) setPageNo(pageNos[index + 1]);
  }, [index, pageNos]);

  return (
    <div className="docpane">
      <header className="docpane__head">
        <Icon name="doc" size={15} />
        <span className="docpane__title" title={doc?.filename}>{doc?.filename || 'Document'}</span>
        <div className="docpane__toggle" role="tablist" aria-label="View">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'image'}
            className={viewMode === 'image' ? 'is-active' : ''}
            onClick={() => setViewMode('image')}
          >
            Image
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'text'}
            className={viewMode === 'text' ? 'is-active' : ''}
            onClick={() => setViewMode('text')}
          >
            Text
          </button>
        </div>
        <button type="button" className="docpane__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>

      <div className="docpane__body">
        {manifestError && <p className="docpane__error">{manifestError}</p>}
        {!manifestError && pageNos.length === 0 && (
          <p className="docpane__empty">No processed pages were kept for this document.</p>
        )}
        {!manifestError && pageNo != null && (
          viewMode === 'image'
            ? <PageImage mode={mode} conversationId={conversationId} docId={docId} pageNo={pageNo} />
            : <PageText mode={mode} conversationId={conversationId} docId={docId} pageNo={pageNo} />
        )}
      </div>

      {pageNos.length > 0 && (
        <footer className="docpane__nav">
          <button type="button" onClick={goPrev} disabled={index <= 0} aria-label="Previous page">
            <Icon name="chevron" size={14} className="docpane__nav-prev" />
          </button>
          <span className="docpane__nav-label">
            Page {pageNo} · {index + 1} of {pageNos.length}
          </span>
          <button
            type="button"
            onClick={goNext}
            disabled={index < 0 || index >= pageNos.length - 1}
            aria-label="Next page"
          >
            <Icon name="chevron" size={14} className="docpane__nav-next" />
          </button>
        </footer>
      )}
    </div>
  );
}

/** The scanned page image. Same blob/revoke pattern as `CitationViewer`. */
function PageImage({ mode, conversationId, docId, pageNo }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let objectUrl = null;
    let cancelled = false;
    setUrl(null);
    setError(null);

    fsPageImage(mode, conversationId, docId, pageNo)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch((e) => { if (!cancelled) setError(e.message); });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mode, conversationId, docId, pageNo]);

  if (error) return <p className="docpane__error">{error}</p>;
  if (!url) return <p className="docpane__loading">Loading the page…</p>;
  return <img className="docpane__img" src={url} alt={`Page ${pageNo}`} />;
}

/** The reconstructed narrative and verified tables for one page. */
function PageText({ mode, conversationId, docId, pageNo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);

    fsPageText(mode, conversationId, docId, pageNo)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); });

    return () => { cancelled = true; };
  }, [mode, conversationId, docId, pageNo]);

  if (error) return <p className="docpane__error">{error}</p>;
  if (!data) return <p className="docpane__loading">Loading…</p>;

  const { narrative = [], tables = [] } = data;
  if (narrative.length === 0 && tables.length === 0) {
    return <p className="docpane__empty">Nothing was extracted from this page.</p>;
  }

  return (
    <div className="docpane__text">
      {narrative.map((chunk) => (
        <p key={chunk.chunk_id} className={`docpane__chunk docpane__chunk--${chunk.chunk_type || 'text'}`}>
          {chunk.content}
        </p>
      ))}
      {tables.map((table) => (
        <Markdown key={table.table_id}>{table.table_md}</Markdown>
      ))}
    </div>
  );
}
