import { useRef, useState } from 'react';
import { tbUploadGrouping } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './GroupingUpload.css';

/**
 * Optional client chart-of-accounts / FSLI grouping file — a single upload
 * button, same simple pattern as the trial balance upload above it. Supplying
 * one changes the audit materially: every account the file names is
 * classified by the client's own FSLI label instead of the keyword engine.
 */
export default function GroupingUpload({ mode, doc, priorDoc, grouping, onChange, onNeedsMapping }) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  async function pick(event) {
    const file = event.target.files?.[0];
    // Reset immediately so re-picking the same filename still fires onChange.
    event.target.value = '';
    if (!file) return;

    setError(null);
    setUploading(true);
    try {
      const res = await tbUploadGrouping(mode, {
        file,
        docId: doc?.doc_id,
        docId2: priorDoc?.doc_id,
      });
      if (res.needsMapping) {
        onNeedsMapping({ ...res, filename: file.name });
      } else {
        onChange({ ...res, filename: file.name });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="tbgrp">
      {grouping ? (
        <div className="tbgrp__file-row">
          <Icon name="check" size={14} />
          <span className="tbgrp__filename">{grouping.filename}</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onChange(null)}>
            Remove
          </button>
        </div>
      ) : (
        <label className={`tbgrp__btn ${uploading ? 'is-busy' : ''}`}>
          <Icon name={uploading ? 'refresh' : 'upload'} size={14} />
          {uploading ? 'Uploading…' : 'Upload grouping file (optional)'}
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls"
            disabled={uploading}
            onChange={pick}
          />
        </label>
      )}

      {error && <Notice tone="error" title="Could not read this grouping file">{error}</Notice>}
    </div>
  );
}
