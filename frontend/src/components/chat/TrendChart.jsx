import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceDot, ResponsiveContainer,
} from 'recharts';
import Icon from '../common/Icon';
import './TrendChart.css';

/**
 * One line-item pair from a trend entry's `divergences`, rendered as a small
 * two-line chart with the divergence year marked.
 *
 * Deliberately narrow in scope: this renders only pairs the backend already
 * flagged (`trend_pairs.py`, spec section 9.3), not a generic "pick any two
 * line items" chart builder. The two lines being compared are exactly the two
 * the divergence is about, so there is never a question of which series the
 * marker belongs to.
 */
function DivergenceLineChart({ yearsSorted, rows, divergence }) {
  const rowA = rows.find((r) => r.label === divergence.line_a);
  const rowB = rows.find((r) => r.label === divergence.line_b);
  if (!rowA || !rowB) return null;

  const data = yearsSorted.map((year, i) => ({
    year: `FY${year}`,
    [rowA.label]: rowA.cells[i] ?? null,
    [rowB.label]: rowB.cells[i] ?? null,
  }));

  const markerIndex = yearsSorted.indexOf(divergence.year);
  const markerYear = markerIndex >= 0 ? `FY${divergence.year}` : null;
  const markerValue = markerIndex >= 0 ? rowB.cells[markerIndex] : null;

  return (
    <div className="trend-chart">
      <div className="trend-chart__head">
        <Icon name="alert" size={13} className="trend-chart__flag-icon" />
        <span className="trend-chart__pair">
          {rowA.label} <span className="trend-chart__vs">vs</span> {rowB.label}
        </span>
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--ink-200)" />
          <XAxis dataKey="year" tick={{ fontSize: 11, fill: 'var(--ink-500)' }} axisLine={{ stroke: 'var(--ink-200)' }} tickLine={false} />
          <YAxis tick={{ fontSize: 11, fill: 'var(--ink-500)' }} axisLine={false} tickLine={false}
                 tickFormatter={(v) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}k` : v)} width={44} />
          <Tooltip
            contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            formatter={(value) => (value == null ? 'N/A' : value.toLocaleString())}
          />
          <Line type="monotone" dataKey={rowA.label} stroke="var(--navy-700)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Line type="monotone" dataKey={rowB.label} stroke="var(--warn-600)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          {markerYear && markerValue != null && (
            <ReferenceDot x={markerYear} y={markerValue} r={6}
                          stroke="var(--err-600)" strokeWidth={2} fill="var(--err-100)" />
          )}
        </LineChart>
      </ResponsiveContainer>

      <p className="trend-chart__note">
        <strong>FY{divergence.year}:</strong> {rowA.label} {divergence.pct_a >= 0 ? '+' : ''}
        {divergence.pct_a.toFixed(1)}%, {rowB.label} {divergence.pct_b >= 0 ? '+' : ''}
        {divergence.pct_b.toFixed(1)}% — {divergence.label}.
      </p>
    </div>
  );
}

/** All divergence charts from one `trend_data` response, across every
 * statement type the trend tool was called for this turn. Renders nothing
 * when there is no trend data, or when nothing diverged. */
export default function TrendChart({ trendData = [] }) {
  const withDivergences = trendData.filter((e) => (e.divergences || []).length > 0);
  if (withDivergences.length === 0) return null;

  return (
    <div className="trend-charts">
      {withDivergences.map((entry) => (
        <div key={entry.statement_label} className="trend-charts__group">
          {entry.divergences.map((d, i) => (
            <DivergenceLineChart
              key={`${entry.statement_label}-${i}`}
              yearsSorted={entry.years_sorted}
              rows={entry.rows}
              divergence={d}
            />
          ))}
        </div>
      ))}
      <p className="trend-charts__caption">
        Figures as extracted from the face statements — see the answer's own
        unit note for scale. Flagged automatically where paired line items
        that should move together diverged by more than 25% year-on-year.
      </p>
    </div>
  );
}
