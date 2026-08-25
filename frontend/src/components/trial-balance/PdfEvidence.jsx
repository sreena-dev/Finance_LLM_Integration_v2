import { useEffect, useState } from 'react';
import { tbDeletePdf, tbListPdfs, tbPdfHealth, tbUploadPdf } from '../../api/client';
import Icon from '../common/Icon';
import Notice from '../common/Notice';
import './PdfEvidence.css';

/**
 * Optional supporting PDFs (annual report, auditor comments) for `audit`.
 *
 * Selected documents are retrieved against and quantified, filling the report's
 * Quantified Risk Areas section with page-cited items. With none selected the
 * audit reports `doc_evidence_status: "no_docs"` and is otherwise identical, so
 * this is purely additive corroboration.
 *
 * This sub-feature owns a separate database and the embedding endpoint, either of
 * which can be down while the rest of Trial Balance is fine. It therefore probes
 * its own health on mount and explains itself up front rather than letting the
 * user hit a connection error on their first upload.
 */
export default function PdfEvidence({ mode, selectedIds, onChange }) {
  const [health, setHealth] = useState(null); // {available, reason} | null while probing
  const [docs, setDocs] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await tbPdfHealth(mode);
        if (cancelled) return;
        setHealth(h);
        if (h.available) setDocs(await tbListPdfs(mode));
      } catch (err) {
        if (!cancelled) setHealth({ available: false, reason: err.message });
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.id]);

  async function pick(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const info = await tbUploadPdf(mode, file);
      setDocs(await tbListPdfs(mode));
      // Selecting it immediately is the point of having just uploaded it.
      if (!selectedIds.includes(info.doc_id)) onChange([...selectedIds, info.doc_id]);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function remove(docId) {
    setBusyId(docId);
    setError(null);
    try {
      await tbDeletePdf(mode, docId);
      setDocs((prev) => prev.filter((d) => d.doc_id !== docId));
      onChange(selectedIds.filter((id) => id !== docId));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  function toggle(docId) {
    onChange(
      selectedIds.includes(docId)
        ? selectedIds.filter((id) => id !== docId)
        : [...selectedIds, docId]
    );
  }

  // Unavailable is a normal state here, not a failure of the audit — say so
  // plainly and keep the reason, which names the missing configuration.
  if (health && !health.available) {
    return (
      <div className="tbpdf">
        <div className="tbpdf__head">
          <Icon name="doc" size={15} className="tbpdf__icon" />
          <div>
            <p className="tbpdf__title">
              Supporting PDFs <span className="tbpdf__opt">unavailable</span>
            </p>
            <p className="tbpdf__sub">
              The audit runs without them; only document-sourced risk items are skipped.
            </p>
          </div>
        </div>
        <p className="tbpdf__reason">{health.reason}</p>
      </div>
    );
  }

  return (
    <div className="tbpdf">
      <div className="tbpdf__head">
        <Icon name="doc" size={15} className="tbpdf__icon" />
        <div>
          <p className="tbpdf__title">
            Supporting PDFs <span className="tbpdf__opt">optional</span>
          </p>
          <p className="tbpdf__sub">
            Annual report or auditor comments. Selected files are cited by page in the
            report's quantified risk areas.
          </p>
        </div>
      </div>

      {docs.length > 0 && (
        <ul className="tbpdf__list">
          {docs.map((d) => {
            const checked = selectedIds.includes(d.doc_id);
            return (
              <li key={d.doc_id} className={`tbpdf__item ${checked ? 'is-on' : ''}`}>
                <label className="tbpdf__label">
                  <input type="checkbox" checked={checked} onChange={() => toggle(d.doc_id)} />
                  <span className="tbpdf__name">{d.filename}</span>
                  <span className="tbpdf__meta">
                    {d.total_pages} pp · {d.total_chunks} chunks
                  </span>
                </label>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => remove(d.doc_id)}
                  disabled={busyId === d.doc_id}
                  title="Delete this PDF and its indexed pages"
                >
                  {busyId === d.doc_id ? '…' : 'Delete'}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <label className={`tbpdf__btn ${uploading || !health ? 'is-busy' : ''}`}>
        <Icon name="upload" size={14} />
        {!health ? 'Checking…' : uploading ? 'Indexing…' : 'Upload PDF'}
        <input
          type="file"
          accept=".pdf"
          disabled={uploading || !health}
          onChange={pick}
        />
      </label>
      {docs.length === 0 && health && (
        <p className="tbpdf__hint">
          Text-based PDFs only — scanned or image-only files can't be indexed, as there is no OCR.
        </p>
      )}

      {error && <Notice tone="error" title="Could not add this PDF">{error}</Notice>}
    </div>
  );
}
