// Small display helpers for the admin dashboard. Pure, no React.

export const fmtNum = (n, digits = 0) =>
  n === null || n === undefined || Number.isNaN(Number(n))
    ? '—'
    : Number(n).toLocaleString('en-IN', { maximumFractionDigits: digits });

export function fmtSecs(s) {
  if (s === null || s === undefined || Number.isNaN(Number(s))) return '—';
  const n = Number(s);
  if (n >= 60) return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
  return `${n.toFixed(n < 10 ? 1 : 0)}s`;
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

export function fmtDay(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(5, 10);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

export function relTime(iso) {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return fmtDay(iso);
}

/** part/total as a percentage, or null when there is nothing to divide. */
export const pct = (part, total) =>
  total ? (Number(part || 0) / Number(total)) * 100 : null;

export const fmtPct = (p, digits = 0) =>
  p === null || p === undefined ? '—' : `${p.toFixed(digits)}%`;

export function clip(text, n = 90) {
  const t = (text || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? `${t.slice(0, n)}…` : t || '—';
}
