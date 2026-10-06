/**
 * The "XBRL Direct" tab: dashboard/business-profile/health/trends/risk-cluster
 * cards computed straight from as_db for one filing, plus the pane that wires
 * them together with its own doc_id picker. Split out of `FdrAnalysis.jsx` —
 * see that file's header for why; nothing here changed shape or behavior.
 */
import { useCallback, useEffect, useState } from 'react';

import {
  downloadXbrlReport,
  fetchXbrlBusinessProfile,
  fetchXbrlCompanyOverview,
  fetchXbrlDashboard,
  fetchXbrlHealthSummary,
  fetchXbrlRiskClusters,
  fetchXbrlTrends,
} from './api';

/**
 * Shared numbered-card shell for every XBRL Direct block — the same `.fdr-blk`
 * card (white surface, border, numbered badge + title header) the Report tab
 * already uses for its own blocks, reused here so the two tabs read as one
 * consistent system rather than the Report tab looking like a set of cards
 * and this tab looking like a set of flat, divider-separated sections.
 */
function XbrlBlock({ number, title, children }) {
  return (
    <section className="fdr-blk">
      <header className="fdr-blk__head">
        <span className="fdr-blk__num">{String(number).padStart(2, '0')}</span>
        <h3 className="fdr-blk__title">{title}</h3>
      </header>
      <div className="fdr-blk__body">{children}</div>
    </section>
  );
}

/**
 * Per-tile "why / how" — both the plain-English explanation (why there's no
 * movement, or why the figure couldn't be computed) and the concept-level
 * calculation live behind one collapsed disclosure, closed by default, so
 * every tile in the grid keeps the same short, aligned footprint regardless
 * of how long its explanation is.
 */
function XbrlTileDetails({ tile }) {
  const roleLabel = { numerator: 'Numerator', denominator: 'Denominator' };
  const hasCalc = tile.citations?.length > 0;
  const hasReason = !!tile.reason;
  if (!hasCalc && !hasReason) return null;

  return (
    <details className="fdr-tile__src">
      <summary>{hasCalc ? 'Calculation' : 'Why'}</summary>
      {hasReason && <p className="fdr-tile__reason-full">{tile.reason}</p>}
      {tile.formula && <p className="fdr-tile__formula">{tile.formula}</p>}
      {tile.formula_values && <p className="fdr-tile__formulavals">{tile.formula_values}</p>}
      {hasCalc && (
        <ul>
          {tile.citations.map((c, i) => (
            <li key={i}>
              {c.located ? (
                <>
                  <span className="fdr-tile__srcrow">
                    {c.role && <span className="fdr-tile__srcrole">{roleLabel[c.role] || c.role}</span>}
                    {c.concept_name}
                    {c.value != null && ` = ${c.value.toLocaleString()}`}
                  </span>
                  <span className="fdr-tile__srcloc">{c.doc_id} &middot; financial_facts.value_numeric</span>
                </>
              ) : (
                <span className="fdr-tile__srcmissing">{c.concept_name} — not tagged in this filing</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

/**
 * Block 1: Company Overview — entity identity, statement flavour, reporting
 * framework.
 */
export function XbrlCompanyOverviewCard({ overviewData, overviewState }) {
  if (overviewState === 'loading') {
    return (
      <XbrlBlock number={1} title="Company overview">
        <p className="fdr-dash__lede">Reading entity identity and reporting framework&hellip;</p>
      </XbrlBlock>
    );
  }
  if (overviewState === 'error' || !overviewData) return null;

  const { entity, framework, cin } = overviewData;

  return (
    <XbrlBlock number={1} title="Company overview">
      {/* No highlighted lead panel above this list (unlike the fs_db Coverage
          block, which this class was designed to trail) — margin-top zeroed
          so the block's own 16px body padding is the only gap before it. */}
      <dl className="fdr-cov__facts" style={{ marginTop: 0 }}>
        <div className="fdr-cov__fact">
          <dt>Entity</dt>
          <dd>
            <span className="fdr-cov__factvalue">
              {entity.name}
              {cin && <> &middot; CIN {cin}</>}
            </span>
            <span className="fdr-cov__factnote">
              {entity.filings_label} read &middot; {entity.period_label} &middot; {entity.comparable_label}
            </span>
          </dd>
        </div>

        <div className="fdr-cov__fact">
          <dt>Reporting framework</dt>
          <dd>
            <span className="fdr-cov__factline">
              <span
                className={`fdr-cov__factvalue ${framework.state === 'read' ? '' : 'fdr-cov__factvalue--absent'}`}
              >
                {framework.label}
              </span>
              {framework.confidence && (
                <span className="fdr-cov__conf">{framework.confidence} confidence</span>
              )}
            </span>
            {framework.evidence && (
              <details className="fdr-cov__quote">
                <summary>Read from the filing</summary>
                <blockquote>{framework.evidence}</blockquote>
                {framework.source?.doc_id && (
                  <cite>
                    {framework.source.doc_id}
                    {framework.source.page ? `, page ${framework.source.page}` : ''}
                  </cite>
                )}
              </details>
            )}
          </dd>
        </div>
      </dl>
    </XbrlBlock>
  );
}

const BUSINESS_MODEL_GRID_ORDER = [
  'model', 'financing', 'revenue', 'value_drivers', 'cost', 'inherent_risk_map',
];

/**
 * Block 2: Business Model and Drivers. Same data shape and grid layout as the
 * fs_db path's `BusinessProfileCard`, deliberately re-implemented here rather
 * than imported — the two paths' profiles are built by different backends
 * (as_db vs fs_db) and restyling this one must never touch the other.
 */
export function XbrlBusinessModelCard({ profile }) {
  if (!profile || !profile.formed) return null;

  const fields = profile.fields || [];
  const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));

  return (
    <XbrlBlock number={2} title="Business model and drivers">
      {fields.length > 0 && (
        <>
          <dl className="fdr-bp__grid">
            {BUSINESS_MODEL_GRID_ORDER.map((key) => {
              const f = byKey[key];
              if (!f) return null;
              return (
                <div className="fdr-bp__field" key={key}>
                  <dt>{f.label}</dt>
                  <dd>{f.text}</dd>
                </div>
              );
            })}
          </dl>
        </>
      )}
    </XbrlBlock>
  );
}

export function XbrlHealthSummaryCard({ healthData, healthState }) {
  if (healthState === 'loading') {
    return (
      <XbrlBlock number={4} title="Financial health summary — structure & performance, interpreted">
        <p className="fdr-dash__lede">Interpreting balance sheet structure and decomposing performance…</p>
      </XbrlBlock>
    );
  }

  if (healthState === 'error' || !healthData || !healthData.formed) {
    return null;
  }

  const {
    business_type = '',
    structure = '',
    performance = '',
    citations = [],
  } = healthData;

  return (
    <XbrlBlock number={4} title="Financial health summary — structure & performance, interpreted">
      {business_type && (
        <div style={{ marginBottom: 14, padding: '8px 12px', background: 'var(--surface-muted, #f8fafc)', borderRadius: 6, border: '1px solid var(--border)', fontSize: '12px', color: 'var(--ink-700)' }}>
          <strong style={{ color: 'var(--ink-900)' }}>Business Profile &amp; Core Activities:</strong> {business_type}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16, marginBottom: 16 }}>
        {/* Structure Card */}
        <div style={{ padding: 16, background: 'var(--surface-card, #ffffff)', border: '1px solid var(--border)', borderRadius: 6, borderLeft: '4px solid #2563eb' }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: '#1e3a8a', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
            Structure
          </div>
          <p style={{ fontSize: '13px', lineHeight: 1.6, color: 'var(--ink-900)', margin: 0 }}>
            {structure}
          </p>
        </div>

        {/* Performance Card */}
        <div style={{ padding: 16, background: 'var(--surface-card, #ffffff)', border: '1px solid var(--border)', borderRadius: 6, borderLeft: '4px solid #059669' }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: '#065f46', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
            Performance (decomposed)
          </div>
          <p style={{ fontSize: '13px', lineHeight: 1.6, color: 'var(--ink-900)', margin: 0 }}>
            {performance}
          </p>
        </div>
      </div>

      {citations.length > 0 && (
        <details style={{ marginTop: 8, border: '1px solid var(--border)', borderRadius: 6, padding: '6px 12px', background: '#ffffff' }}>
          <summary style={{ cursor: 'pointer', fontSize: '11.5px', fontWeight: 600, color: 'var(--ink-600)' }}>
            Grounded Figures &amp; Audit Trail ({citations.length} cited facts)
          </summary>
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {citations.map((c, idx) => (
              <span key={idx} style={{ fontSize: '11px', padding: '3px 8px', background: '#f1f5f9', borderRadius: 4, color: '#334155' }}>
                <strong>{c.concept}:</strong> {typeof c.value === 'number' ? c.value.toLocaleString() : c.value} ({c.scale || 'native'})
              </span>
            ))}
          </div>
        </details>
      )}
    </XbrlBlock>
  );
}


/** DOM id a DuPont period's calculation panel is addressed by — shared by the
 * clickable numbers in the table and the panel itself so a click can find and
 * open its own panel without any lifted state. */
const dupontCalcId = (idx) => `fdr-dupont-calc-${idx}`;

/** Opens (native `<details>`) and scrolls to a specific calculation panel,
 * then briefly highlights it so the reader's eye lands on the right one. */
function jumpToCalculation(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.open = !el.open;
  if (!el.open) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  el.classList.add('is-jumped');
  window.setTimeout(() => el.classList.remove('is-jumped'), 1200);
}

/** A table value that opens its own calculation panel on click, instead of
 * making the reader locate it inside one combined, scrolled-past list. */
function DupontNumber({ children, targetId }) {
  return (
    <button type="button" className="fdr-numlink" onClick={() => jumpToCalculation(targetId)}>
      {children}
    </button>
  );
}

/**
 * How each DuPont-schedule row was actually computed — one closed-by-default
 * disclosure per reporting period, addressable by id so a click on the
 * matching number in the table jumps straight to it, in the same
 * presentation as the Executive Dashboard tiles' "Calculation" box.
 */
function DupontCalculation({ periods }) {
  if (!periods || periods.length === 0) return null;

  return (
    <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {periods.map((dp, idx) => {
        if (dp.raw_revenue == null) return null;
        return (
        <details key={idx} id={dupontCalcId(idx)} className="fdr-tile__src fdr-tile__src--dupont">
          <summary>Calculation &mdash; {dp.period_date}</summary>
          <p className="fdr-tile__formula">
            Net margin = ProfitLossForPeriod &divide; RevenueFromOperations
          </p>
          <p className="fdr-tile__formulavals">
            {dp.raw_pat.toLocaleString()} &divide; {dp.raw_revenue.toLocaleString()} = {dp.margin_display}
          </p>
          <p className="fdr-tile__formula" style={{ marginTop: 8 }}>
            Asset turnover = RevenueFromOperations &divide; Assets
          </p>
          <p className="fdr-tile__formulavals">
            {dp.raw_revenue.toLocaleString()} &divide; {dp.raw_assets.toLocaleString()} = {dp.turnover_display}
          </p>
          <p className="fdr-tile__formula" style={{ marginTop: 8 }}>
            Equity multiplier = Assets &divide; Equity
          </p>
          <p className="fdr-tile__formulavals">
            {dp.raw_assets.toLocaleString()} &divide; {dp.raw_equity.toLocaleString()} = {dp.multiplier_display}
          </p>
          <p className="fdr-tile__formula" style={{ marginTop: 8 }}>
            ROE = Net margin &times; Asset turnover &times; Equity multiplier
          </p>
          <p className="fdr-tile__formulavals">
            {dp.margin_display} &times; {dp.turnover_display} &times; {dp.multiplier_display} = {dp.roe_display}
          </p>
        </details>
        );
      })}
    </div>
  );
}

/**
 * Block 5: Key Trends & Structural Drift (as_db direct XBRL).
 *
 * Implements FDR Specification §7, §8, §9, §14.1 Row 5.
 * Displays longitudinal time-series, common-size asset & funding drift,
 * extended DuPont profitability decomposition, and the 4 canonical audit trend archetypes.
 */
export function XbrlTrendsCard({ trendsData, trendsState }) {
  if (trendsState === 'loading') {
    return (
      <XbrlBlock number={5} title="Key trends & structural drift">
        <p className="fdr-dash__lede">Computing multi-period time series and structural drift…</p>
      </XbrlBlock>
    );
  }

  if (trendsState === 'error' || !trendsData || !trendsData.formed) {
    return null;
  }

  const {
    series_years = 0,
    period_labels = [],
    summary_lede = '',
    dupont_narrative = '',
    structural_drift_cards = [],
    common_size_highlights = [],
    common_size_schedule = [],
    dupont_schedule = [],
  } = trendsData;

  const isSinglePeriod = series_years <= 1;

  return (
    <XbrlBlock number={5} title="Key trends & structural drift">
      {isSinglePeriod ? (
        <div style={{ padding: 14, background: 'var(--surface-muted, #f8fafc)', borderRadius: 6, border: '1px solid var(--border)', fontSize: '13px', color: 'var(--ink-700)' }}>
          <strong>Initial Reporting Period Notice:</strong> {summary_lede}
        </div>
      ) : (
        <>
          {summary_lede && (
            <p className="fdr-dash__lede" style={{ marginBottom: 16 }}>
              <strong>Longitudinal Drift Analysis:</strong> {summary_lede}
            </p>
          )}

          {dupont_schedule.length > 0 && (
            <div style={{ marginBottom: 20, padding: 14, background: 'var(--surface-card, #ffffff)', border: '1px solid var(--border)', borderRadius: 6 }}>
              <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--ink-900)', marginBottom: 6 }}>
                Extended DuPont Profitability Decomposition (Margin × Turnover × Multiplier = ROE)
              </div>
              <p style={{ fontSize: '12px', color: 'var(--ink-600)', marginBottom: 12, lineHeight: 1.5 }}>
                {dupont_narrative}
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: '12px', textAlign: 'left', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--ink-500)' }}>
                      <th style={{ padding: '6px 8px' }}>Reporting Period</th>
                      <th style={{ padding: '6px 8px' }}>Net Margin (PAT / Rev)</th>
                      <th style={{ padding: '6px 8px' }}>Asset Turnover (Rev / Assets)</th>
                      <th style={{ padding: '6px 8px' }}>Equity Multiplier (Assets / Equity)</th>
                      <th style={{ padding: '6px 8px', fontWeight: 600 }}>Return on Equity (ROE)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dupont_schedule.map((dp, idx) => {
                      const hasCalc = dp.raw_revenue != null;
                      const targetId = dupontCalcId(idx);
                      return (
                      <tr key={idx} style={{ borderBottom: '1px solid var(--border-light, #f1f5f9)' }}>
                        <td style={{ padding: '6px 8px', fontWeight: 500 }}>{dp.period_date}</td>
                        <td style={{ padding: '6px 8px' }}>
                          {hasCalc ? <DupontNumber targetId={targetId}>{dp.margin_display}</DupontNumber> : dp.margin_display}
                        </td>
                        <td style={{ padding: '6px 8px' }}>
                          {hasCalc ? <DupontNumber targetId={targetId}>{dp.turnover_display}</DupontNumber> : dp.turnover_display}
                        </td>
                        <td style={{ padding: '6px 8px' }}>
                          {hasCalc ? <DupontNumber targetId={targetId}>{dp.multiplier_display}</DupontNumber> : dp.multiplier_display}
                        </td>
                        <td style={{ padding: '6px 8px', fontWeight: 600, color: 'var(--ink-900)' }}>
                          {hasCalc ? <DupontNumber targetId={targetId}>{dp.roe_display}</DupontNumber> : dp.roe_display}
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <DupontCalculation periods={dupont_schedule} />
            </div>
          )}

          {structural_drift_cards.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--ink-500)', marginBottom: 10 }}>
                Diagnostic Trend Leads &amp; Directional Drift ({structural_drift_cards.length})
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12 }}>
                {structural_drift_cards.map((card, idx) => {
                  const isHigh = (card.severity || '').toLowerCase() === 'high';
                  return (
                    <div
                      key={idx}
                      style={{
                        padding: 14,
                        background: 'var(--surface-card, #ffffff)',
                        border: `1px solid ${isHigh ? '#f59e0b' : 'var(--border)'}`,
                        borderLeft: `4px solid ${isHigh ? '#d97706' : '#2563eb'}`,
                        borderRadius: 6,
                        boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: '#e0f2fe', color: '#0369a1' }}>
                          [{card.signal_id}]
                        </span>
                        <span style={{ fontSize: '11px', fontWeight: 600, padding: '2px 6px', borderRadius: 4, background: isHigh ? '#fef3c7' : '#f1f5f9', color: isHigh ? '#92400e' : '#475569' }}>
                          {(card.severity || 'medium').toUpperCase()}
                        </span>
                      </div>
                      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--ink-900)', marginBottom: 6 }}>
                        {card.title}
                      </div>
                      <p style={{ fontSize: '12px', color: 'var(--ink-700)', margin: '0 0 8px', lineHeight: 1.5 }}>
                        <strong>Observation:</strong> {card.observation}
                      </p>
                      <div style={{ fontSize: '12px', color: '#1e3a8a', background: '#eff6ff', padding: '6px 10px', borderRadius: 4, marginBottom: 8, lineHeight: 1.45 }}>
                        <strong>Audit Lead:</strong> {card.audit_lead}
                      </div>
                      {card.evidence_lead && (
                        <p style={{ fontSize: '11.5px', color: 'var(--ink-500)', margin: 0, fontStyle: 'italic' }}>
                          <strong>Evidence to Verify:</strong> {card.evidence_lead}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {common_size_highlights.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--ink-500)', marginBottom: 8 }}>
                Common-Size Balance Sheet Drift Highlights
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: '12.5px', color: 'var(--ink-700)', lineHeight: 1.6 }}>
                {common_size_highlights.map((hl, idx) => (
                  <li key={idx} style={{ marginBottom: 4 }}>
                    <strong>{hl.item} ({hl.drift_pp > 0 ? `+${hl.drift_pp}` : hl.drift_pp} pp):</strong> {hl.narrative}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {common_size_schedule.length > 0 && (
            <details style={{ marginTop: 12, border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px' }}>
              <summary style={{ cursor: 'pointer', fontSize: '12px', fontWeight: 600, color: 'var(--ink-700)' }}>
                View Full Common-Size Balance Sheet &amp; Funding Schedule ({period_labels.join(' → ')})
              </summary>
              <div style={{ marginTop: 10, overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: '11.5px', textAlign: 'left', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--ink-500)' }}>
                      <th style={{ padding: '4px 6px' }}>Category</th>
                      {common_size_schedule.map((p, idx) => (
                        <th key={idx} style={{ padding: '4px 6px' }}>{p.period_date}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Property, Plant &amp; Equipment (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.asset_mix?.ppe_share == null ? 'n/a' : `${p.asset_mix.ppe_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Capital WIP (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.asset_mix?.cwip_share == null ? 'n/a' : `${p.asset_mix.cwip_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Trade Receivables (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.asset_mix?.receivables_share == null ? 'n/a' : `${p.asset_mix.receivables_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Cash &amp; Bank Balances (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.asset_mix?.cash_bank_share == null ? 'n/a' : `${p.asset_mix.cash_bank_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Other Non-Current Assets (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.asset_mix?.other_noncur_share == null ? 'n/a' : `${p.asset_mix.other_noncur_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9', background: '#f8fafc' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Equity / Net Worth (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.funding_mix?.equity_share == null ? 'n/a' : `${p.funding_mix.equity_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9', background: '#f8fafc' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Total Borrowings (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.funding_mix?.borrowings_share == null ? 'n/a' : `${p.funding_mix.borrowings_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                    <tr style={{ borderBottom: '1px solid #f1f5f9', background: '#f8fafc' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 500 }}>Trade Payables (%)</td>
                      {common_size_schedule.map((p, idx) => (
                        <td key={idx} style={{ padding: '4px 6px' }}>{p.funding_mix?.payables_share == null ? 'n/a' : `${p.funding_mix.payables_share.toFixed(1)}%`}</td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </XbrlBlock>
  );
}


/**
 * Block 6: Key Risk Clusters with Interactions (as_db direct XBRL).
 *
 * Implements FDR Specification §10, §14.1 Row 6, Appendices D & E.
 * Synthesizes risk clusters directly from Ind-AS XBRL financial_facts
 * and textual disclosures, displaying multi-cluster interactions (reinforcing vs. offsetting),
 * affected assertions, control implications, recommended audit response (N/T/E),
 * and evidentiary requests.
 */
export function XbrlRiskClustersCard({ riskData, riskState }) {
  if (riskState === 'loading') {
    return (
      <XbrlBlock number={6} title="Key risk clusters with interactions">
        <p className="fdr-dash__lede">Synthesizing risk clusters and interaction dynamics…</p>
      </XbrlBlock>
    );
  }

  if (riskState === 'error' || !riskData || !riskData.formed) {
    return null;
  }

  const { risk_clusters = [], interactions = [] } = riskData;

  return (
    <XbrlBlock number={6} title="Key risk clusters with interactions">
      {/* Risk Interactions Dynamics (Reinforcing / Offsetting) */}
      {interactions.length > 0 && (
        <div className="fdr-rc__interaction-panel">
          <div className="fdr-rc__interaction-title">
            Risk-Interaction Dynamics (§10.2)
          </div>
          <div className="fdr-rc__interaction-list">
            {interactions.map((it, idx) => {
              const isReinforcing = (it.relationship || '').toLowerCase() === 'reinforcing';
              return (
                <div
                  key={idx}
                  className={`fdr-rc__interaction-item ${isReinforcing ? 'is-reinforcing' : 'is-offsetting'}`}
                >
                  <div className="fdr-rc__interaction-head">
                    <span className="fdr-rc__interaction-pair">
                      <strong>{it.cluster_a}</strong> ⇄ <strong>{it.cluster_b}</strong>
                    </span>
                    <span className={`fdr-rc__interaction-rel-badge ${isReinforcing ? 'badge--reinforcing' : 'badge--offsetting'}`}>
                      {it.relationship ? it.relationship.toUpperCase() : ''}
                    </span>
                  </div>
                  <p className="fdr-rc__interaction-rationale">{it.rationale}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Clustered Themes List */}
      {risk_clusters.length > 0 ? (
        <div className="fdr-rc__list" style={{ marginTop: 14 }}>
          {risk_clusters.map((cluster) => {
            const isRaised = cluster.raised !== false;
            const resp = cluster.recommended_response || {};
            const hasResponse = resp.nature || resp.timing || resp.extent;
            const signals = cluster.contributing_signals || [];
            const isHighInherent = (cluster.inherent_risk || '').toLowerCase() === 'high';

            return (
              <details
                key={cluster.id}
                className={`fdr-rc ${!isRaised ? 'is-unraised' : (cluster.significant_risk || isHighInherent ? 'is-raised' : '')}`}
                open={isRaised && cluster.priority_rank <= 2}
              >
                <summary className="fdr-rc__summary">
                  <div className="fdr-rc__summary-main">
                    <span className="fdr-rc__theme">{cluster.theme}</span>
                    <span className="fdr-rc__id">{cluster.id}</span>
                  </div>
                  <div className="fdr-rc__summary-badges">
                    {isRaised ? (
                      <span
                        className={`fdr-rc__badge fdr-rc__badge--${(cluster.inherent_risk || 'medium').toLowerCase()}`}
                      >
                        Inherent: {(cluster.inherent_risk || 'medium').toUpperCase()}
                      </span>
                    ) : (
                      <span className="fdr-rc__badge fdr-rc__badge--muted">
                        Not Raised / Within Thresholds
                      </span>
                    )}
                  </div>
                </summary>

                <div className="fdr-rc__body">
                  {!isRaised ? (
                    <div style={{ padding: '12px 0 6px' }}>
                      <p className="fdr-rc__note" style={{ margin: 0, fontSize: '12.5px', color: 'var(--ink-700)', lineHeight: 1.6 }}>
                        <strong>Audit Determination:</strong> {cluster.reason || 'No contributing anomaly signals detected in reported figures or disclosures.'}
                      </p>
                      {cluster.metrics?.length > 0 && (
                        <div className="fdr-rc__section" style={{ marginTop: 10 }}>
                          <h5 style={{ fontSize: '10.5px', color: 'var(--ink-400)' }}>Evaluated Figures</h5>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                            <tbody>
                              {cluster.metrics.map((m, mIdx) => (
                                <tr key={mIdx}>
                                  <td style={{ padding: '3px 12px 3px 0', color: 'var(--ink-500)' }}>{m.label}</td>
                                  <td style={{ padding: '3px 0', fontWeight: 600, color: 'var(--ink-800)' }}>{m.value}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      {cluster.affected_assertions?.length > 0 && (
                        <div className="fdr-rc__section" style={{ marginTop: 10 }}>
                          <h5 style={{ fontSize: '10.5px', color: 'var(--ink-400)' }}>Standard Assertions Evaluated</h5>
                          <div className="fdr-rc__assertion-pills">
                            {cluster.affected_assertions.map((ass, aIdx) => (
                              <span key={aIdx} className="fdr-rc__assertion-pill" style={{ opacity: 0.75 }}>
                                {ass}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      {/* Priority Context */}
                      {cluster.priority_reasoning && (
                        <p className="fdr-rc__note" style={{ fontStyle: 'italic', marginBottom: 12 }}>
                          <strong>Audit Priority Context:</strong> {cluster.priority_reasoning}
                        </p>
                      )}

                      {/* Contributing Signals */}
                      {signals.length > 0 && (
                        <div className="fdr-rc__section">
                          <h5>Contributing Signals &amp; Facts</h5>
                          <ul>
                            {signals.map((s, sIdx) => (
                              <li key={sIdx}>
                                <span className="fdr-rc__signal-text">{s.signal}</span>
                                {s.source_trace && (
                                  <span className="fdr-rc__source-tag" title={s.source_trace}>
                                    {s.source_trace}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Affected Assertions */}
                      {cluster.affected_assertions?.length > 0 && (
                        <div className="fdr-rc__section">
                          <h5>Affected Financial Statement Assertions</h5>
                          <div className="fdr-rc__assertion-pills">
                            {cluster.affected_assertions.map((ass, aIdx) => (
                              <span key={aIdx} className="fdr-rc__assertion-pill">
                                {ass}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Candidate Audit Response */}
                      {hasResponse && (
                        <div className="fdr-rc__section">
                          <h5>Candidate Audit Response (Nature · Timing · Extent)</h5>
                          <div className="fdr-rc__response-grid">
                            {resp.nature && (
                              <div className="fdr-rc__response-col">
                                <strong>Nature:</strong> {resp.nature}
                              </div>
                            )}
                            {resp.timing && (
                              <div className="fdr-rc__response-col">
                                <strong>Timing:</strong> {resp.timing}
                              </div>
                            )}
                            {resp.extent && (
                              <div className="fdr-rc__response-col">
                                <strong>Extent:</strong> {resp.extent}
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* Plausible Alternative Explanations */}
                      {cluster.alt_explanations?.length > 0 && (
                        <div className="fdr-rc__section">
                          <h5>Plausible Alternative Explanations (Non-Misstatement Rationale)</h5>
                          <ul>
                            {cluster.alt_explanations.map((alt, altIdx) => (
                              <li key={altIdx}>{alt}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Control Implications */}
                      {cluster.control_implications && (
                        <div className="fdr-rc__section">
                          <h5>Internal Control Implications</h5>
                          <p>{cluster.control_implications}</p>
                        </div>
                      )}

                      {/* Specialist Referral */}
                      {cluster.specialist_referral && cluster.specialist_referral !== 'None' && (
                        <div className="fdr-rc__section">
                          <h5>Specialist Referral Recommended</h5>
                          <p>
                            <span className="fdr-rc__specialist-badge">
                              {cluster.specialist_referral}
                            </span>
                          </p>
                        </div>
                      )}

                      {/* Evidence Request */}
                      {cluster.evidence_request && (
                        <div className="fdr-rc__section">
                          <h5>
                            Evidence Request Requisition{' '}
                            <span className="fdr-rc__hint">
                              — a management explanation is a lead to test, not evidence (§17.4)
                            </span>
                          </h5>
                          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '11.5px', background: 'var(--surface)', padding: '8px 10px', borderRadius: '4px', border: '1px solid var(--border)' }}>
                            {cluster.evidence_request}
                          </p>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      ) : (
        <p className="fdr-dash__note">No risk clusters met the activation threshold for this filing.</p>
      )}
    </XbrlBlock>
  );
}


/**
 * The XBRL Direct tab's own filing picker — keyed by doc_id against as_db.
 * Rendered in the fixed header row, not inside the scrollable panel body, so
 * switching to this tab does not move the picker to a different vertical
 * position on screen.
 */
export function XbrlFilingBar({ entities, docId, onChange, state, error, onRetry }) {
  if (state === 'error') {
    return (
      <div className="fdr-entitybar fdr-entitybar--error">
        <span className="fdr-entitybar__err">{error}</span>
        <button type="button" className="fdr-btn fdr-btn--ghost" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="fdr-entitybar">
      <div className="fdr-field">
        <span className="fdr-field__label">Filing (as_db)</span>
        <select
          className="fdr-select"
          value={docId}
          onChange={(e) => onChange(e.target.value)}
          disabled={state !== 'ready' || entities.length === 0}
        >
          {state === 'loading' && <option value="">Loading filings…</option>}
          {state === 'ready' && entities.length === 0 && (
            <option value="">No filings in as_db</option>
          )}
          {state === 'ready' && entities.length > 0 && <option value="">Select a filing…</option>}
          {entities.map((e) => (
            <option key={e.doc_id} value={e.doc_id} disabled={!e.usable}>
              {e.company_name} — {e.fy_label}
              {e.filing_type ? ` (${e.filing_type})` : ''}
              {!e.usable ? ' (no data ingested)' : ''}
            </option>
          ))}
        </select>
        <span className="fdr-field__hint">&nbsp;</span>
      </div>
    </div>
  );
}

/**
 * Self-contained Download PDF control for the XBRL Direct tab, rendered in the
 * fixed topbar row next to `XbrlFilingBar` so the two sit parallel instead of
 * the button trailing below the scrollable report body.
 */
export function XbrlDownloadButton({ docId }) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  const save = useCallback(async () => {
    if (!docId) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const { blob, filename } = await downloadXbrlReport(docId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError(err.message || 'The file could not be built.');
    } finally {
      setDownloading(false);
    }
  }, [docId]);

  if (!docId) return null;

  // Same label / control / hint column as every other control in the bar, so the button
  // shares their baseline and height instead of floating at the row's vertical centre.
  return (
    <div className="fdr-field fdr-xbrl-dl">
      <span className="fdr-field__label">&nbsp;</span>
      <button
        type="button"
        className="fdr-btn fdr-btn--primary"
        onClick={save}
        disabled={downloading}
      >
        {downloading ? 'Preparing…' : 'Download PDF'}
      </button>
      <span className="fdr-field__hint fdr-xbrl-dl__err">{downloadError || ' '}</span>
    </div>
  );
}

export function XbrlPane({ docId, thresholdsVersion = 0 }) {
  const [overviewData, setOverviewData] = useState(null);
  const [overviewState, setOverviewState] = useState('idle'); // idle | loading | ready | error

  const [dash, setDash] = useState(null);
  const [dashState, setDashState] = useState('idle'); // idle | loading | ready | error
  const [dashError, setDashError] = useState('');

  const [profile, setProfile] = useState(null);
  const [profileState, setProfileState] = useState('idle'); // idle | loading | ready | error

  const [healthData, setHealthData] = useState(null);
  const [healthState, setHealthState] = useState('idle'); // idle | loading | ready | error

  const [riskData, setRiskData] = useState(null);
  const [riskState, setRiskState] = useState('idle'); // idle | loading | ready | error

  const [trendsData, setTrendsData] = useState(null);
  const [trendsState, setTrendsState] = useState('idle'); // idle | loading | ready | error

  useEffect(() => {
    if (!docId) {
      setOverviewData(null);
      setOverviewState('idle');
      setDash(null);
      setDashState('idle');
      setProfile(null);
      setProfileState('idle');
      setHealthData(null);
      setHealthState('idle');
      setRiskData(null);
      setRiskState('idle');
      setTrendsData(null);
      setTrendsState('idle');
      return;
    }
    let live = true;
    setOverviewState('loading');
    setDashState('loading');
    setDashError('');
    setProfileState('loading');
    setHealthState('loading');
    setRiskState('loading');
    setTrendsState('loading');

    fetchXbrlCompanyOverview(docId)
      .then((res) => {
        if (!live) return;
        setOverviewData(res);
        setOverviewState('ready');
      })
      .catch(() => {
        if (!live) return;
        setOverviewState('error');
      });

    fetchXbrlDashboard(docId)
      .then((res) => {
        if (!live) return;
        setDash(res);
        setDashState('ready');
      })
      .catch((err) => {
        if (!live) return;
        setDashError(err.message || String(err));
        setDashState('error');
      });

    fetchXbrlBusinessProfile(docId)
      .then((res) => {
        if (!live) return;
        setProfile(res);
        setProfileState('ready');
      })
      .catch(() => {
        if (!live) return;
        setProfileState('error');
      });

    fetchXbrlHealthSummary(docId)
      .then((res) => {
        if (!live) return;
        setHealthData(res);
        setHealthState('ready');
      })
      .catch(() => {
        if (!live) return;
        setHealthState('error');
      });

    fetchXbrlTrends(docId)
      .then((res) => {
        if (!live) return;
        setTrendsData(res);
        setTrendsState('ready');
      })
      .catch(() => {
        if (!live) return;
        setTrendsState('error');
      });

    fetchXbrlRiskClusters(docId)
      .then((res) => {
        if (!live) return;
        setRiskData(res);
        setRiskState('ready');
      })
      .catch(() => {
        if (!live) return;
        setRiskState('error');
      });

    return () => {
      live = false;
    };
  }, [docId, thresholdsVersion]);

  return (
    <div style={{ width: '100%' }}>
      {dashState === 'loading' && <p className="fdr-dash__lede">Reading the filing…</p>}
      {dashState === 'error' && <p className="fdr-tile__reason">{dashError}</p>}

      {dashState === 'ready' && dash && (
        <div className="fdr-report__blocks">
          <XbrlCompanyOverviewCard overviewData={overviewData} overviewState={overviewState} />

          <XbrlBusinessModelCard profile={profile} />

          <XbrlBlock number={3} title="Executive dashboard">
            <div className="fdr-dash__grid">
              {dash.tiles.map((t) => (
                <div
                  key={t.id}
                  className={`fdr-tile ${t.computed ? '' : 'is-uncomputed'} ${
                    t.attention ? 'is-flagged' : ''
                  }`}
                >
                  <div className="fdr-tile__head">
                    <span className="fdr-tile__id">{t.id}</span>
                    {t.attention && (
                      <span className="fdr-tile__mark" aria-label="Marked for attention">
                        !
                      </span>
                    )}
                  </div>
                  <div className="fdr-tile__label">{t.label}</div>
                  {t.computed ? (
                    <>
                      <div className="fdr-tile__value">
                        {t.display}
                        {t.unit_label && <span className="fdr-tile__unit"> {t.unit_label}</span>}
                      </div>
                      <div className="fdr-tile__move">
                        {t.movement_label || 'No comparable year'}
                        {t.context_display && (
                          <span className="fdr-tile__ctx"> · {t.context_display}</span>
                        )}
                      </div>
                      <XbrlTileDetails tile={t} />
                    </>
                  ) : (
                    <>
                      <div className="fdr-tile__reason">Not computed for this filing</div>
                      <XbrlTileDetails tile={t} />
                    </>
                  )}
                </div>
              ))}
            </div>

            {dash.total_count > 0 && dash.computed_count < dash.total_count && (
              <p className="fdr-dash__note">
                {dash.total_count - dash.computed_count} of {dash.total_count} figures could not
                be computed for this filing — see each tile above for why.
              </p>
            )}
          </XbrlBlock>

          <XbrlHealthSummaryCard healthData={healthData} healthState={healthState} />

          <XbrlTrendsCard trendsData={trendsData} trendsState={trendsState} />

          <XbrlRiskClustersCard riskData={riskData} riskState={riskState} />
        </div>
      )}

      {dashState === 'idle' && (
        <p className="fdr-dash__lede">Select a filing above to compute its dashboard.</p>
      )}
    </div>
  );
}
