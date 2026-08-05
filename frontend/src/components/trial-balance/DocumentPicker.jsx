import { useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { tbDeleteDocument, tbUpload } from '../../api/client';
import Icon from '../common/Icon';
import './DocumentPicker.css';

/**
 * Upload + select the trial balance(s) every tab operates against.
 *
 * "Current" is required; "prior" is optional and only meaningful for the
 * Audit and Validate tabs (a two-period comparison) — Ask always answers
 * against "current" alone.
 */
export default function DocumentPicker({
  mode,
  documents,
  loading,
  error,
  currentId,
  priorId,
  onSelectCurrent,
  onSelectPrior,
  onUploaded,
  onDeleted,
  onNeedsMapping,
}) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  async function handleFile(file) {
    if (!file || uploading) return;
    setUploadError(null);
    setUploading(true);
    try {
      const result = await tbUpload(mode, file);
      if (result.needsMapping) {
        onNeedsMapping(result);
      } else {
        onUploaded(result);
      }
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(e, docId) {
    e.stopPropagation();
    if (deletingId) return;
    setDeletingId(docId);
    try {
      await tbDeleteDocument(mode, docId);
      onDeleted(docId);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <motion.section
      className="tbdocs card"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="tbdocs__head">
        <span className="tbdocs__mark">
          <Icon name="layers" size={17} />
        </span>
        <div>
          <h2 className="tbdocs__title">Trial balances</h2>
          <p className="tbdocs__sub">
            Upload an Excel trial balance, then pick a current period (and
            optionally a prior period to compare against).
          </p>
        </div>
      </header>

      <div
        className={`tbdocs__drop ${dragOver ? 'is-over' : ''} ${uploading ? 'is-busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
        onClick={() => !uploading && inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls"
          hidden
          onChange={(e) => {
            handleFile(e.target.files?.[0]);
            e.target.value = '';
          }}
        />
        {uploading ? (
          <>
            <span className="tbdocs__spinner" />
            <span>Parsing and storing…</span>
          </>
        ) : (
          <>
            <Icon name="upload" size={18} />
            <span>Drop a .xlsx / .xls file here, or click to browse</span>
          </>
        )}
      </div>

      {uploadError && <p className="tbdocs__error">{uploadError}</p>}
      {error && <p className="tbdocs__error">{error}</p>}

      <div className="tbdocs__list">
        {loading && documents === null && (
          <div className="tbdocs__empty">
            <span className="tbdocs__spinner" />
            Loading documents…
          </div>
        )}

        {documents !== null && documents.length === 0 && !loading && (
          <div className="tbdocs__empty">No trial balances uploaded yet.</div>
        )}

        {(documents || []).map((doc) => {
          const isCurrent = doc.doc_id === currentId;
          const isPrior = doc.doc_id === priorId;
          return (
            <div
              key={doc.doc_id}
              className={`tbdocs__row ${isCurrent ? 'is-current' : ''} ${isPrior ? 'is-prior' : ''}`}
            >
              <button
                type="button"
                className="tbdocs__row-main"
                onClick={() => onSelectCurrent(doc.doc_id)}
                title="Set as current period"
              >
                <span className="tbdocs__row-name">{doc.filename}</span>
                <span className="tbdocs__row-meta">
                  {doc.sheet ? `${doc.sheet} · ` : ''}
                  {(doc.periods || []).join(', ')}
                </span>
              </button>

              <div className="tbdocs__row-actions">
                {isCurrent && <span className="pill pill--navy">Current</span>}
                {isPrior && <span className="pill pill--mute">Prior</span>}
                {!isCurrent && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => onSelectPrior(isPrior ? null : doc.doc_id)}
                    disabled={!currentId}
                    title={
                      !currentId
                        ? 'Select a current period first'
                        : isPrior
                          ? 'Remove as prior period'
                          : 'Compare against this as the prior period'
                    }
                  >
                    {isPrior ? 'Unset prior' : 'Set as prior'}
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn--ghost btn--sm tbdocs__delete"
                  onClick={(e) => handleDelete(e, doc.doc_id)}
                  disabled={deletingId === doc.doc_id}
                  aria-label="Delete"
                >
                  {deletingId === doc.doc_id ? (
                    <span className="tbdocs__spinner tbdocs__spinner--sm" />
                  ) : (
                    <Icon name="trash" size={14} />
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </motion.section>
  );
}
