import { useEffect, useState } from 'react';
import Icon from '../common/Icon';
import { fsTableSnippet } from '../../api/client';
import './CitationViewer.css';

/**
 * The scanned region a cited figure was read from.
 *
 * This is what turns a citation from traceable into checkable. The spec asks
 * for statement, note, page, table, row and column on every figure; showing the
 * crop lets an auditor confirm the number at a glance instead of reopening the
 * PDF and hunting for it — which, on a 35-page scan with no text layer, is not
 * a search they can do with ctrl-F.
 *
 * The image is fetched as a blob rather than pointed at with `<img src>`
 * because the route is authenticated and an img tag carries no Authorization
 * header. The object URL is revoked on unmount; without that every citation
 * opened leaks its bitmap until the tab reloads.
 *
 * Exit animation is deliberately absent. App.jsx:342 documents a deadlock from
 * nesting AnimatePresence inside a pane that also animates, and this renders
 * inside exactly such a pane.
 */
export default function CitationViewer({ mode, conversationId, docId, tableId, caption, onClose }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let objectUrl = null;
    let cancelled = false;

    fsTableSnippet(mode, conversationId, docId, tableId)
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
  }, [mode, conversationId, docId, tableId]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="cite__overlay" role="dialog" aria-modal="true" aria-label="Source scan"
         onClick={onClose}>
      <div className="cite__panel" onClick={(e) => e.stopPropagation()}>
        <header className="cite__head">
          <Icon name="doc" size={15} />
          <span className="cite__caption">{caption || 'Source'}</span>
          <button type="button" className="cite__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="cite__body">
          {error && <p className="cite__error">{error}</p>}
          {!error && !url && <p className="cite__loading">Loading the scan…</p>}
          {url && <img className="cite__img" src={url} alt={caption || 'Scanned source'} />}
        </div>

        <footer className="cite__foot">
          This is the region of the original scan the figures were read from.
          Check the printed values against it before relying on them.
        </footer>
      </div>
    </div>
  );
}
