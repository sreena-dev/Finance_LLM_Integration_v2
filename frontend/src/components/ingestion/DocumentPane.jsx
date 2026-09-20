import { useCallback, useEffect, useRef, useState } from 'react';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import { fsDocumentPages, fsEditCell, fsPageImage, fsPageText } from '../../api/client';
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
  // Bumped after a saved edit to force the effect below to refetch. A local
  // patch of `data` would have to re-derive cell state (unreadable/recovered/
  // user-entered, plus footing) itself -- `edits.cells_for_table` already
  // does that correctly server-side, so re-reading it is the one place that
  // logic needs to live, rather than a second, driftable copy in the client.
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (refreshKey === 0) { setData(null); setError(null); }

    fsPageText(mode, conversationId, docId, pageNo)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, conversationId, docId, pageNo, refreshKey]);

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
        Array.isArray(table.cells) && table.cells.length > 0 ? (
          <EditableTable
            key={table.table_id}
            mode={mode}
            conversationId={conversationId}
            docId={docId}
            pageNo={pageNo}
            table={table}
            onSaved={() => setRefreshKey((k) => k + 1)}
          />
        ) : (
          <Markdown key={table.table_id}>{table.table_md}</Markdown>
        )
      ))}
    </div>
  );
}

/** `edits.split_row`, mirrored: trims the outer `|`, splits, trims each cell. */
function splitRow(line) {
  let s = (line || '').trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

/**
 * One financial table with cells the extraction flagged as `[unreadable
 * ...]` or `[recovered ...]` (or already `[user-entered]`) rendered as
 * buttons instead of plain text, so the reader who can see the scan can
 * supply the figure the extraction could not.
 *
 * Only ever rendered for a table `page_text` returned a non-empty `cells`
 * array for -- everything else (the overwhelming majority of tables) stays
 * on the plain `<Markdown>` path, unchanged.
 */
export function EditableTable({ mode, conversationId, docId, pageNo, table, onSaved }) {
  const lines = (table.table_md || '').split('\n');
  const header = splitRow(lines[0] || '');
  const rows = lines.slice(2).map(splitRow);
  const cellMap = new Map((table.cells || []).map((c) => [`${c.row_index}:${c.col_index}`, c]));

  const [editingKey, setEditingKey] = useState(null); // "row:col" or null
  const [showScan, setShowScan] = useState(false);
  const triggerRefs = useRef({});

  const closeEditor = useCallback((key) => {
    setEditingKey(null);
    setShowScan(false);
    triggerRefs.current[key]?.focus();
  }, []);

  const editing = editingKey != null ? cellMap.get(editingKey) : null;
  const [rowIdx, colIdx] = editingKey ? editingKey.split(':').map(Number) : [null, null];
  const currentCellText = rowIdx != null ? (rows[rowIdx]?.[colIdx] ?? '') : '';

  return (
    <div className="md">
      <div className="md__table-wrap">
        <table>
          <thead>
            <tr>{header.map((h, i) => <th key={i}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((text, c) => {
                  const key = `${r}:${c}`;
                  const cell = cellMap.get(key);
                  if (!cell) return <td key={c}>{text}</td>;
                  const label = `${
                    cell.state === 'unreadable' ? 'Unreadable'
                      : cell.state === 'recovered' ? 'Recovered, unconfirmed'
                        : 'User-entered'
                  } figure, row ${cell.row_label || r + 1}, column ${cell.column || c + 1}. Press to ${
                    cell.state === 'user_entered' ? 'edit or revert' : 'enter'
                  }.`;
                  return (
                    <td key={c}>
                      <button
                        type="button"
                        ref={(el) => { triggerRefs.current[key] = el; }}
                        className={`docpane__cellbtn docpane__cellbtn--${cell.state}`}
                        aria-haspopup="dialog"
                        aria-expanded={editingKey === key}
                        aria-label={label}
                        onClick={() => { setEditingKey(key); setShowScan(false); }}
                      >
                        {text}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <CellEditor
          mode={mode}
          conversationId={conversationId}
          docId={docId}
          pageNo={pageNo}
          tableId={table.table_id}
          rowIndex={rowIdx}
          colIndex={colIdx}
          cell={editing}
          currentCellText={currentCellText}
          showScan={showScan}
          onToggleScan={() => setShowScan((v) => !v)}
          onClose={() => closeEditor(editingKey)}
          onSaved={() => { onSaved(); closeEditor(editingKey); }}
        />
      )}
    </div>
  );
}

/**
 * The inline editor for one flagged cell: the recovered value and
 * confidence when there is one, a labelled amount input, Save / Confirm /
 * Revert as the cell's state allows, and an optional inset of the scan for
 * this page so the reader doesn't have to leave the Text tab to check it.
 */
function CellEditor({
  mode, conversationId, docId, pageNo, tableId, rowIndex, colIndex, cell,
  currentCellText, showScan, onToggleScan, onClose, onSaved,
}) {
  const [value, setValue] = useState(
    cell.state === 'user_entered' ? (cell.edit?.value ?? '') : (cell.recovered_text ?? '')
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const dialogRef = useRef(null);

  useEffect(() => { dialogRef.current?.focus(); }, []);

  const run = useCallback(async (action, actionValue) => {
    setSaving(true);
    setError(null);
    try {
      await fsEditCell(mode, conversationId, docId, tableId, {
        rowIndex, colIndex, expectedCell: currentCellText, action,
        ...(actionValue !== undefined ? { value: actionValue } : {}),
      });
      onSaved();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [mode, conversationId, docId, tableId, rowIndex, colIndex, currentCellText, onSaved]);

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div
      className="docpane__editor"
      role="dialog"
      aria-modal="false"
      aria-label={`Edit figure: row ${cell.row_label || ''}, column ${cell.column || ''}`}
      tabIndex={-1}
      ref={dialogRef}
      onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
    >
      <div className="docpane__editor-head">
        <strong>{cell.row_label || 'Row'}</strong>
        <span className="docpane__editor-col">{cell.column}</span>
        <button type="button" className="docpane__editor-close" onClick={onClose} aria-label="Close editor">×</button>
      </div>

      {cell.state === 'recovered' && (
        <p className="docpane__editor-hint">
          A second read of the scan shows <strong>{cell.recovered_text}</strong>
          {cell.confidence ? ` (confidence: ${cell.confidence})` : ''}, not confirmed by this
          column's own arithmetic.
        </p>
      )}
      {cell.state === 'unreadable' && (
        <p className="docpane__editor-hint">The extraction could not read this figure from the scan.</p>
      )}
      {cell.state === 'user_entered' && (
        <p className="docpane__editor-hint">
          Entered by {cell.edit?.by || 'a user'}. Originally {cell.edit?.original_marker
            ? 'unreadable or recovered' : 'unreadable'} before that.
        </p>
      )}
      {cell.edit?.footing?.verdict === 'does_not_tie' && (
        <p className="docpane__editor-warn">
          Not confirmed by this column's arithmetic (simple check): printed total{' '}
          {cell.edit.footing.printed}, components sum to {cell.edit.footing.computed}.
        </p>
      )}
      {cell.edit?.footing?.verdict === 'ties' && (
        <p className="docpane__editor-ok">This column's total ties with this figure included.</p>
      )}

      <label className="docpane__editor-field">
        <span>Figure from the scan</span>
        <input
          type="text"
          inputMode="decimal"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={saving}
          placeholder="e.g. 12,859 or (5,000)"
        />
      </label>

      {error && <p className="docpane__editor-error" role="alert">{error}</p>}

      <div className="docpane__editor-actions">
        <button type="button" disabled={saving || !value.trim()} onClick={() => run('set', value)}>
          {cell.state === 'user_entered' ? 'Save change' : 'Save'}
        </button>
        {cell.state === 'recovered' && (
          <button type="button" disabled={saving} onClick={() => run('confirm')}>
            Confirm recovered value
          </button>
        )}
        {cell.state === 'user_entered' && (
          <button type="button" className="docpane__editor-danger" disabled={saving} onClick={() => run('revert')}>
            Revert
          </button>
        )}
        <button type="button" className="docpane__editor-ghost" onClick={onToggleScan}>
          {showScan ? 'Hide scan for this page' : 'Show scan for this page'}
        </button>
      </div>

      {showScan && (
        <div className="docpane__editor-scan">
          <PageImage mode={mode} conversationId={conversationId} docId={docId} pageNo={pageNo} />
        </div>
      )}
    </div>
  );
}
