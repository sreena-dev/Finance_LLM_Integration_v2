import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { tbAudit, tbAuditWorkbook } from '../../api/client';
import Icon from '../common/Icon';
import Markdown from '../common/Markdown';
import Notice from '../common/Notice';
import ProgressSteps from '../common/ProgressSteps';
import './AuditPanel.css';

const PIPELINE_STEPS = [
  'Screening the trial balance for anomalies',
  'Classifying accounts and computing materiality',
  'Retrieving Ind AS / Schedule III / SA 700 citations',
  'Drafting the risk-analytics narrative',
];

const RISK_ORDER = { high: 0, medium: 1, low: 2, information_request: 3 };
const RISK_TONE = { high: 'err', medium: 'warn', low: 'mute', information_request: 'navy' };

function Collapsible({ title, count, icon, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapse ${open ? 'is-open' : ''}`}>
      <button type="button" className="collapse__head" onClick={() => setOpen((v) => !v)}>
        <Icon name={icon} size={15} className="collapse__icon" />
        <span className="collapse__title">{title}</span>
        {count != null && <span className="pill pill--mute">{count}</span>}
        <motion.span className="collapse__caret" animate={{ rotate: open ? 180 : 0 }}>
          <Icon name="chevron" size={15} />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="collapse__body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
          >
            <div className="collapse__inner">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function num(v) {
  if (typeof v !== 'number') return v;
  return v.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function Finding({ f }) {
  return (
    <div className="tbaudit-finding">
      <div className="tbaudit-finding__head">
        <span className={`pill pill--${RISK_TONE[f.risk_rating] || 'mute'}`}>
          {f.risk_rating || 'info'}
        </span>
        {f.reference_id && <span className="tbaudit-finding__ref">{f.reference_id}</span>}
        <span className="tbaudit-finding__account">{f.account}</span>
      </div>
      {f.observation && <p className="tbaudit-finding__text">{f.observation}</p>}
      <div className="tbaudit-finding__meta">
        {f.amount != null && <span>Amount: {num(f.amount)}</span>}
        {f.gap && <span>Gap: {f.gap}</span>}
      </div>
      {f.proposed_response && (
        <p className="tbaudit-finding__response"><strong>Proposed response:</strong> {f.proposed_response}</p>
      )}
      {f.evidence_requested?.length > 0 && (
        <div className="tbaudit-finding__evidence">
          {f.evidence_requested.map((e, i) => (
            <span key={i} className="pill pill--mute">{e}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** FSLI/risk-analytics audit report + downloadable workbook, over one (or two, for comparison) stored TB(s). */
export default function AuditPanel({ mode, doc, priorDoc }) {
  const [pending, setPending] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function run() {
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const r = await tbAudit(mode, { docId: doc.doc_id, docIdPrior: priorDoc?.doc_id });
      setResult(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  async function downloadWorkbook() {
    if (downloading) return;
    setDownloading(true);
    try {
      const blob = await tbAuditWorkbook(mode, { docId: doc.doc_id, docIdPrior: priorDoc?.doc_id });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'TB_Audit.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  }

  const findings = [...(result?.findings || [])].sort(
    (a, b) => (RISK_ORDER[a.risk_rating] ?? 9) - (RISK_ORDER[b.risk_rating] ?? 9)
  );

  return (
    <div className="tbaudit">
      <div className="tbaudit__scroll">
        <div className="tbaudit__inner">
          <div className="tbaudit__toolbar">
            <div>
              <h3 className="tbaudit__title">FSLI &amp; risk-analytics audit</h3>
              <p className="tbaudit__sub">
                {priorDoc
                  ? `Comparing "${doc.filename}" against prior period "${priorDoc.filename}".`
                  : `Single-period analytics for "${doc.filename}".`}
              </p>
            </div>
            <div className="tbaudit__actions">
              {result && (
                <button type="button" className="btn btn--ghost" onClick={downloadWorkbook} disabled={downloading}>
                  <Icon name="download" size={15} />
                  {downloading ? 'Building…' : 'Download workbook'}
                </button>
              )}
              <button type="button" className="btn btn--primary" onClick={run} disabled={pending}>
                <Icon name="shield" size={15} />
                {pending ? 'Running…' : result ? 'Re-run audit' : 'Run audit'}
              </button>
            </div>
          </div>

          {pending && <ProgressSteps steps={PIPELINE_STEPS} intervalMs={7000} />}
          {error && <Notice tone="error" title="Audit failed">{error}</Notice>}

          {result && !pending && (
            <>
              <div className="tbaudit__stats">
                <div className="tbaudit__stat">
                  <span className="tbaudit__stat-label">Accounts</span>
                  <span className="tbaudit__stat-value">{num(result.input_quality?.n_accounts)}</span>
                </div>
                <div className="tbaudit__stat">
                  <span className="tbaudit__stat-label">Ties out</span>
                  <span className="tbaudit__stat-value">{result.input_quality?.balanced ? 'Yes' : 'No'}</span>
                </div>
                <div className="tbaudit__stat">
                  <span className="tbaudit__stat-label">Materiality basis</span>
                  <span className="tbaudit__stat-value">
                    {num(result.materiality?.provisional_overall_materiality)}
                  </span>
                </div>
                <div className="tbaudit__stat">
                  <span className="tbaudit__stat-label">Findings</span>
                  <span className="tbaudit__stat-value">
                    {Object.values(result.findings_summary || {}).reduce((a, b) => a + b, 0)}
                  </span>
                </div>
              </div>

              {(result.findings_summary) && (
                <div className="tbaudit__severity">
                  {Object.entries(result.findings_summary).map(([sev, count]) => (
                    <span key={sev} className={`pill pill--${RISK_TONE[sev] || 'mute'}`}>
                      {count} {sev.replace('_', ' ')}
                    </span>
                  ))}
                </div>
              )}

              {result.report && (
                <article className="card tbaudit-report">
                  <Markdown>{result.report}</Markdown>
                </article>
              )}

              {findings.length > 0 && (
                <Collapsible title="Findings" icon="alert" count={findings.length} defaultOpen>
                  <div className="tbaudit-findings">
                    {findings.map((f, i) => <Finding key={f.reference_id || i} f={f} />)}
                  </div>
                </Collapsible>
              )}

              {result.materiality?.bases && (
                <Collapsible title="Materiality" icon="scale">
                  <p className="tbaudit-note">{result.materiality.base_reason}</p>
                  <table className="tbaudit-table">
                    <thead>
                      <tr><th>Base</th><th>Amount</th><th>Benchmark</th><th>Provisional materiality</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(result.materiality.bases).map(([k, v]) => (
                        <tr key={k} className={k === result.materiality.chosen_base ? 'is-chosen' : ''}>
                          <td>{k.replace(/_/g, ' ')}</td>
                          <td>{num(v.amount)}</td>
                          <td>{v.benchmark_pct}%</td>
                          <td>{num(v.provisional_materiality)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Collapsible>
              )}

              {result.ind_as_gaps?.length > 0 && (
                <Collapsible title="Ind AS gaps" icon="doc" count={result.ind_as_gaps.length}>
                  <div className="tbaudit-findings">
                    {result.ind_as_gaps.map((g, i) => (
                      <div key={i} className="tbaudit-finding">
                        <div className="tbaudit-finding__head">
                          <span className="pill pill--navy">{g.standard}</span>
                          <span className="tbaudit-finding__account">{g.name}</span>
                        </div>
                        <p className="tbaudit-finding__text">{g.gap}</p>
                        {g.evidence_requested?.length > 0 && (
                          <div className="tbaudit-finding__evidence">
                            {g.evidence_requested.map((e, i2) => (
                              <span key={i2} className="pill pill--mute">{e}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </Collapsible>
              )}

              {result.tb_screen && (
                <Collapsible title="Trial balance screening" icon="search">
                  <div className="tbaudit__severity">
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
                      <p className="tbaudit-note">Evidence requested</p>
                      <ul className="tbaudit-list">
                        {result.evidence_request_list.map((e, i) => <li key={i}>{e}</li>)}
                      </ul>
                    </>
                  )}
                  {result.management_query_list?.length > 0 && (
                    <>
                      <p className="tbaudit-note">Management queries</p>
                      <ul className="tbaudit-list">
                        {result.management_query_list.map((e, i) => <li key={i}>{e}</li>)}
                      </ul>
                    </>
                  )}
                </Collapsible>
              )}
            </>
          )}

          {!result && !pending && !error && (
            <div className="tbaudit__idle">
              <Icon name="shield" size={26} className="tbaudit__idle-icon" />
              <p>Run the audit to see FSLI classification, materiality, screening and risk findings.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
