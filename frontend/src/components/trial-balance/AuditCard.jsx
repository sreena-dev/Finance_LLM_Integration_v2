import { useRef, useState } from 'react';
import { tbAuditWorkbook } from '../../api/client';
import Collapsible from '../common/Collapsible';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import FullTrialBalance from './FullTrialBalance';
import { fmtINR, fmtNum, printReportHtml } from './tbFormat';
import './AuditCard.css';

const RISK_ORDER = { high: 0, medium: 1, low: 2, information_request: 3 };
const RISK_TONE = { high: 'err', medium: 'warn', low: 'mute', information_request: 'navy' };

function Finding({ f }) {
  return (
    <div className="tbcard-finding">
      <div className="tbcard-finding__head">
        <span className={`pill pill--${RISK_TONE[f.risk_rating] || 'mute'}`}>
          {f.risk_rating || 'info'}
        </span>
        {f.reference_id && <span className="tbcard-finding__ref">{f.reference_id}</span>}
        <span className="tbcard-finding__account">{f.account}</span>
      </div>
      {f.observation && <p className="tbcard-finding__text">{f.observation}</p>}
      <div className="tbcard-finding__meta">
        {f.amount != null && <span>Amount: {fmtNum(f.amount)}</span>}
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
 * `fullTb` is composed in by the caller from a separate `/validate` call —
 * `/audit` returns no per-ledger table. It arrives as `null` when that call
 * failed, which is reported rather than hidden: a missing arithmetic table is
 * not the same as one that came back empty.
 */
export default function AuditCard({ mode, msg }) {
  const { result, fullTb, doc, priorDoc, pdfIds } = msg;
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState(null);
  const [showReport, setShowReport] = useState(true);
  const reportRef = useRef(null);

  const iq = result.input_quality || {};
  const summary = result.findings_summary || {};
  const findings = [...(result.findings || [])].sort(
    (a, b) => (RISK_ORDER[a.risk_rating] ?? 9) - (RISK_ORDER[b.risk_rating] ?? 9)
  );

  async function downloadWorkbook() {
    if (downloading) return;
    setDownloading(true);
    setDlError(null);
    try {
      const blob = await tbAuditWorkbook(mode, {
        docId: doc.doc_id,
        docIdPrior: priorDoc?.doc_id,
        uploadDocIds: pdfIds,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'TB_Audit.xlsx';
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
        <h3 className="tbcard__title">Audit mode — {result.entity || doc.filename}</h3>
        <span className="pill pill--navy tbcard__badge">Indicators only — no opinion</span>
      </header>
      <p className="tbcard__sev">
        {summary.high || 0} high · {summary.medium || 0} medium · {summary.low || 0} low
        {summary.information_request ? ` · ${summary.information_request} information request` : ''}
      </p>

      {result.context_note && <p className="tbcard__inset">{result.context_note}</p>}

      {/* Before the figures, not after: a low-coverage snapshot understates
          magnitudes silently and reads as complete if the caveat sits below it. */}
      {iq.low_value_coverage && (
        <Notice tone="warn" title="Low classification coverage">
          Only {iq.value_coverage_pct}% of total ledger value is classified
          {iq.n_contra_pairs_excluded
            ? ` (${iq.n_contra_pairs_excluded} contra/rollup pair(s) excluded as immaterial)`
            : ''}. The figures below may not reflect true magnitudes — do not use them for
          ratio or trend analysis until chart-of-accounts mapping is improved.
        </Notice>
      )}

      <div className="tbcard__stats">
        <div className="tbcard__stat">
          <span className="tbcard__stat-label">Accounts</span>
          <span className="tbcard__stat-value">{fmtNum(iq.n_accounts)}</span>
        </div>
        <div className="tbcard__stat">
          <span className="tbcard__stat-label">Ties out</span>
          <span className="tbcard__stat-value">{iq.balanced ? 'Yes' : 'No'}</span>
        </div>
        <div className="tbcard__stat">
          <span className="tbcard__stat-label">Materiality</span>
          <span className="tbcard__stat-value">
            {fmtNum(result.materiality?.provisional_overall_materiality)}
          </span>
        </div>
        <div className="tbcard__stat">
          <span className="tbcard__stat-label">Classification</span>
          <span className="tbcard__stat-value tbcard__stat-value--sm">
            {result.mapping_summary?.n_grouping_override > 0
              ? `Client grouping · ${fmtNum(result.mapping_summary.n_grouping_override)} of ${fmtNum(result.mapping_summary.n_accounts)}`
              : 'Keyword inference'}
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

      {fullTb === null ? (
        <p className="tbcard__inset">
          Full trial balance/variance check could not be completed
          {priorDoc ? ' for this comparison' : ''}.
        </p>
      ) : (
        <Collapsible
          title={`Full trial balance (${fullTb.rows.length} ledger${fullTb.rows.length === 1 ? '' : 's'})`}
          count={fullTb.halted ? undefined : fullTb.rows.length}
        >
          <FullTrialBalance fullTb={fullTb} />
        </Collapsible>
      )}

      {(result.comparison?.new_accounts?.length > 0
        || result.comparison?.dropped_accounts?.length > 0) && (
        <Collapsible title="Accounts added / dropped since the prior period" icon="search">
          <div className="tbcard__cmp">
            <div>
              <p className="tbcard-note">New accounts</p>
              {(result.comparison.new_accounts || []).map((r, i) => (
                <p key={i} className="tbcard__cmp-add">
                  + {[r.code, r.name].filter(Boolean).join(' ')} ({fmtINR(r.current)})
                </p>
              ))}
            </div>
            <div>
              <p className="tbcard-note">Dropped accounts</p>
              {(result.comparison.dropped_accounts || []).map((r, i) => (
                <p key={i} className="tbcard__cmp-drop">
                  − {[r.code, r.name].filter(Boolean).join(' ')} ({fmtINR(r.prior)})
                </p>
              ))}
            </div>
          </div>
        </Collapsible>
      )}

      {findings.length > 0 && (
        <Collapsible title="Focus areas" icon="alert" count={findings.length}>
          <div className="tbcard-findings">
            {findings.map((f, i) => <Finding key={f.reference_id || i} f={f} />)}
          </div>
        </Collapsible>
      )}

      {result.materiality?.bases && (
        <Collapsible title="Materiality" icon="scale">
          <p className="tbcard-note">{result.materiality.base_reason}</p>
          <table className="tbcard-table">
            <thead>
              <tr><th>Base</th><th>Amount</th><th>Benchmark</th><th>Provisional materiality</th></tr>
            </thead>
            <tbody>
              {Object.entries(result.materiality.bases).map(([k, v]) => (
                <tr key={k} className={k === result.materiality.chosen_base ? 'is-chosen' : ''}>
                  <td>{k.replace(/_/g, ' ')}</td>
                  <td>{fmtNum(v.amount)}</td>
                  <td>{v.benchmark_pct}%</td>
                  <td>{fmtNum(v.provisional_materiality)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Collapsible>
      )}

      {result.ind_as_gaps?.length > 0 && (
        <Collapsible title="Ind AS gaps" icon="doc" count={result.ind_as_gaps.length}>
          <div className="tbcard-findings">
            {result.ind_as_gaps.map((g, i) => (
              <div key={i} className="tbcard-finding">
                <div className="tbcard-finding__head">
                  <span className="pill pill--navy">{g.standard}</span>
                  <span className="tbcard-finding__account">{g.name}</span>
                </div>
                <p className="tbcard-finding__text">{g.gap}</p>
              </div>
            ))}
          </div>
        </Collapsible>
      )}

      {result.tb_screen && (
        <Collapsible title="Trial balance screening" icon="search">
          <div className="tbcard__pills">
            <span className="pill pill--warn">{result.tb_screen.n_abnormal || 0} abnormal signs</span>
            <span className="pill pill--mute">{result.tb_screen.n_round_sums || 0} round-sum accounts</span>
            <span className="pill pill--mute">{result.tb_screen.n_contra_pairs || 0} contra pairs</span>
          </div>
        </Collapsible>
      )}

      {(result.evidence_request_list?.length > 0 || result.management_query_list?.length > 0) && (
        <Collapsible title="Evidence & management queries" icon="ledger">
          {result.evidence_request_list?.length > 0 && (
            <>
              <p className="tbcard-note">Evidence requested</p>
              <ul className="tbcard-list">
                {result.evidence_request_list.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </>
          )}
          {result.management_query_list?.length > 0 && (
            <>
              <p className="tbcard-note">Management queries</p>
              <ul className="tbcard-list">
                {result.management_query_list.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </>
          )}
        </Collapsible>
      )}

      <div className="tbcard__actions">
        <button type="button" className="btn btn--primary" onClick={downloadWorkbook} disabled={downloading}>
          <Icon name="download" size={15} />
          {downloading ? 'Preparing workbook…' : 'Download TB_Audit.xlsx'}
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
          <Icon name="doc" size={15} />
          Download PDF
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
