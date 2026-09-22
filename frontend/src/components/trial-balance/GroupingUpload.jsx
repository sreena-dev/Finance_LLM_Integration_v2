import { useRef } from 'react';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './GroupingUpload.css';

/**
 * Optional client chart-of-accounts / FSLI grouping file — a single upload
 * button, same simple pattern as the trial-balance upload above it. Supplying
 * one changes the audit materially: every account the file names is
 * classified by the client's own FSLI label instead of the keyword engine.
 *
 * Pure staging component: picking a file only hands it up to the parent via
 * `onStage` — it is not uploaded to the backend until "Run analysis" fires
 * (see TrialBalanceView's runIngestionThenAudit), so a bare TB and its
 * grouping file always reach ingest_tb_to_live together, never separately.
 * `slotState` (set by the parent during Run) drives the running/success/
 * error display; this component has no fetch state of its own any more.
 */
export default function GroupingUpload({ staged, result, slotState, onStage, onRemove }) {
  const inputRef = useRef(null);

  function pick(event) {
    const file = event.target.files?.[0];
    // Reset immediately so re-picking the same filename still fires onStage.
    event.target.value = '';
    if (!file) return;
    onStage(file);
  }

  const running = slotState?.status === 'running';
  const filename = result?.filename || staged?.name;

  return (
    <div className="tbgrp">
      {filename ? (
        <div className="tbgrp__file-row">
          <Icon name={running ? 'refresh' : 'check'} size={14} />
          <span className="tbgrp__filename">{filename}</span>
          {!running && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onRemove}>
              Remove
            </button>
          )}
        </div>
      ) : (
        <label className="tbgrp__btn">
          <Icon name="upload" size={14} />
          Upload grouping file
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls"
            onChange={pick}
          />
        </label>
      )}

      {slotState?.status === 'failed' && (
        <Notice tone="error" title="Could not read this grouping file">{slotState.message}</Notice>
      )}
    </div>
  );
}
