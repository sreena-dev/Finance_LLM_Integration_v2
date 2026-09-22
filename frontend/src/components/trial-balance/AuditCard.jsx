import { useRef, useState } from 'react';
import { tbAuditWorkbook } from '../../api/client';
import Collapsible from '../common/Collapsible';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import { fmtINR, fmtPct, humanize, printReportHtml } from './tbFormat';
import './AuditCard.css';

const RISK_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const RISK_TONE = { critical: 'err', high: 'err', medium: 'warn', low: 'mute' };
const SEVERITIES = [['critical', 'Critical', 'err'], ['high', 'High', 'err'], ['medium', 'Medium', 'warn'], ['low', 'Low', 'mute']];

/** Only claims what the validation engine reported: no table means no badge. */
function integrityOf(fullTb) {
  if (!fullTb) return null;
  if (fullTb.halted) return { tone: 'err', icon: 'alert', label: 'Arithmetic checks halted' };
  if ((fullTb.rows || []).length > 0) return { tone: 'ok', icon: 'check', label: 'Arithmetic checks passed' };
  return null;
}

function Finding({ f }) {
  return (
    <div className="tbcard-finding">
      <div className="tbcard-finding__head">
        <span className={`pill pill--${RISK_TONE[f.risk_rating] || 'mute'}`}>
          {f.risk_rating ? humanize(f.risk_rating) : 'Information'}
        </span>
        {f.reference_id && <span className="tbcard-finding__ref">{f.reference_id}</span>}
        {f.account && <span className="tbcard-finding__account">{f.account}</span>}
      </div>
      {f.observation && <p className="tbcard-finding__text">{f.observation}</p>}
      <div className="tbcard-finding__meta">
        {f.amount != null && <span>Amount: {fmtINR(f.amount)}</span>}
        {f.gap && <span>Gap: {f.gap}</span>}
      </div>
      {f.evidence_requested?.length > 0 && (
        <div className="tbcard-finding__evidence">
          {f.evidence_requested.map((e, i) => (
            <span key={i} className="pill pill--mute">{e}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One completed audit run.
 *
 * `msg.result` is the full `/audit` response envelope ({session_id, mode,
 * result, layer1_validation}) — `result` below is the structured report data
 * one level in, assembled server-side from materiality.json/audit_reasoning.json/
 * the markdown report (see backend/api/routes.py::_finalize_audit_result).
 */
export default function AuditCard({ mode, msg }) {
  const { result: envelope, doc, priorDoc, pdfIds } = msg;
  const result = envelope.result || {};
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState(null);
  const [showReport, setShowReport] = useState(true);
  const reportRef = useRef(null);

  const summary = result.findings_summary || {};
  const integrity = integrityOf(fullTb);
  const findings = [...(result.findings || [])].sort(
    (a, b) => (RISK_ORDER[a.risk_rating] ?? 9) - (RISK_ORDER[b.risk_rating] ?? 9)
  );

async function downloadWorkbook(format) {
    if (downloading) return;
    setDownloading(format);
    setDlError(null);
    try {
      const { blob, filename } = await tbAuditWorkbook(mode, {
        docId: doc.doc_id,
        docIdPrior: priorDoc?.doc_id,
        uploadDocIds: pdfIds,
        format,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDlError(err.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <article className="card tbcard">
      <header className="tbcard__head">
        <h3 className="tbcard__title">
          {priorDoc ? 'Two TB Comparative Analysis' : 'Single TB Analysis'} — {result.entity || doc.filename}
        </h3>
        <span className="pill pill--navy tbcard__badge">Indicators only — no opinion</span>
      </header>
      {integrity && (
        <p className="tbcard__integrity">
          <span className={`pill pill--${integrity.tone}`}><Icon name={integrity.icon} size={12} /> {integrity.label}</span>
          {priorDoc ? null : <span className="pill pill--mute">{doc.filename}</span>}
        </p>
      )}

      <div className="tbcard__sev">
        {SEVERITIES.map(([key, label, tone]) => (
          <div key={key} className={`tbcard__sevtile tbcard__sevtile--${tone}`}>
            <span className="tbcard__sevnum">{summary[key] || 0}</span>
            <span className="tbcard__sevlabel">{label}</span>
          </div>
        ))}
      </div>

      {result.context_note && <p className="tbcard__inset">{result.context_note}</p>}

      <div className="tbcard__stats">
        <div className="tbcard__stat">
          <span className="tbcard__stat-label">Materiality</span>
          <span className="tbcard__stat-value">
            {fmtINR(result.materiality?.overall_materiality)}
          </span>
        </div>
      </div>

      <button type="button" className="tbcard__toggle" onClick={() => setShowReport((v) => !v)}>
        {showReport ? '▾ Hide report' : '▸ Show report'}
      </button>
      {showReport && result.report && (
        <div className="tbcard__report" ref={reportRef}>
          <Markdown>{result.report}</Markdown>
        </div>
      )}

      {findings.length > 0 && (
        <Collapsible title="Focus areas" icon="alert" count={findings.length}>
          <div className="tbcard-findings">
            {findings.map((f, i) => <Finding key={f.reference_id || i} f={f} />)}
          </div>
        </Collapsible>
      )}

      {result.materiality?.benchmark_analysis?.length > 0 && (
        <Collapsible title="Materiality" icon="scale">
          <table className="tbcard-table">
            <thead>
              <tr><th>Benchmark</th><th>Amount</th><th>%</th><th>Hypothetical materiality</th></tr>
            </thead>
            <tbody>
              {result.materiality.benchmark_analysis.map((b) => (
                <tr key={b.benchmark} className={b.benchmark === result.materiality.benchmark_used ? 'is-chosen' : ''}>
                  <td>{b.benchmark}</td>
                  <td>{fmtINR(b.base_amount)}</td>
                  <td>{fmtPct(b.pct)}</td>
                  <td>{fmtINR(b.hypothetical_materiality)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Collapsible>
      )}

      <div className="tbcard__actions">
        <button type="button" className="btn btn--primary" onClick={() => downloadWorkbook('xlsx')} disabled={!!downloading}>
          <Icon name="download" size={15} />
          {downloading === 'xlsx' ? 'Preparing workbook…' : 'Download TB_Audit.xlsx'}
        </button>
        <button type="button" className="btn btn--primary" onClick={() => downloadWorkbook('docx')} disabled={!!downloading}>
          <Icon name="doc" size={15} />
          {downloading === 'docx' ? 'Preparing report…' : 'Download TB_Audit_Report.docx'}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => printReportHtml(
            `TB Audit Report — ${result.entity || doc.filename}`,
            reportRef.current?.innerHTML
          )}
          disabled={!showReport}
          title={showReport ? undefined : 'Show the report first'}
        >
          <Icon name="printer" size={15} />
          Print / Save as PDF
        </button>
      </div>
      {dlError && <p className="tbcard__dl-error">Download failed: {dlError}</p>}

      <p className="tbcard__disclaimer">
        Based solely on the trial balance supplied for {result.entity || doc.filename}, the items
        above are risk observations for audit planning — not conclusions on the accounts. They
        should be corroborated with the records requested before any view is formed. This analysis
        does not express an audit opinion.
      </p>
    </article>
  );
}
