import { useRef, useState } from 'react';
import { tbUploadGrouping } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './GroupingUpload.css';

/**
 * Optional client chart-of-accounts / management FSLI grouping file.
 *
 * Supplying one changes the audit materially: every account the file names is
 * classified by the client's own FSLI label instead of the keyword engine, so
 * the FSLI Summary, Financial Snapshot and the grouping of abnormal-sign
 * findings all follow the client's chart of accounts. The report states which
 * source was used, so an audit run with and without this file is not the same
 * document.
 *
 * The coverage line is the important part of this UI, not the upload itself: a
 * grouping file can parse cleanly into hundreds of rows and still match none of
 * the selected trial balance's accounts (wrong file, or codes that don't
 * correspond). The audit then silently falls back to keyword inference, which
 * reads as a successful run. `n_matched` makes that visible up front.
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

  const noneMatched = grouping && grouping.n_matched === 0;

  return (
    <div className="tbgrp">
      <div className="tbgrp__head">
        <Icon name="ledger" size={15} className="tbgrp__icon" />
        <div>
          <p className="tbgrp__title">Chart of accounts / FSLI grouping <span className="tbgrp__opt">optional</span></p>
          <p className="tbgrp__sub">
            If supplied, the client's own FSLI labels replace keyword-based classification.
          </p>
        </div>
      </div>

      {grouping ? (
        <div className={`tbgrp__file ${noneMatched ? 'is-bad' : 'is-ok'}`}>
          <div className="tbgrp__file-row">
            <Icon name={noneMatched ? 'alert' : 'check'} size={14} />
            <span className="tbgrp__filename">{grouping.filename}</span>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onChange(null)}>
              Remove
            </button>
          </div>
          {grouping.n_tb_accounts > 0 ? (
            <p className="tbgrp__coverage">
              {noneMatched
                ? "None of this trial balance's accounts appear in this file — check it is the right grouping for this TB, or re-map its columns."
                : `Covers ${grouping.n_matched.toLocaleString('en-IN')} of ${grouping.n_tb_accounts.toLocaleString('en-IN')} accounts in the selected trial balance.`}
            </p>
          ) : (
            <p className="tbgrp__coverage">
              {grouping.n_entries.toLocaleString('en-IN')} lookup entries parsed. Coverage against
              this trial balance could not be measured.
            </p>
          )}
        </div>
      ) : (
        <>
          <label className={`tbgrp__btn ${uploading ? 'is-busy' : ''}`}>
            <Icon name="upload" size={14} />
            {uploading ? 'Parsing…' : 'Upload grouping file'}
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.xls"
              disabled={uploading}
              onChange={pick}
            />
          </label>
          <p className="tbgrp__hint">
            Excel (.xlsx/.xls). A flat code-to-FSLI lookup, or FSLI names as heading rows with
            their accounts listed underneath — both are detected automatically.
          </p>
        </>
      )}

      {error && <Notice tone="error" title="Could not read this grouping file">{error}</Notice>}
    </div>
  );
}
