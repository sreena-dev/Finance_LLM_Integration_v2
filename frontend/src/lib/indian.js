/**
 * Indian digit grouping (last three digits, then pairs) for figures the USER
 * types or the interface computes. Machine-read figures are never passed
 * through here: they are shown exactly as printed.
 */

/** "150000.5" -> "1,50,000.5". Decimals are kept exactly as typed, none added. */
export function groupIndian(input) {
  const s = String(input ?? '').trim();
  const m = /^(\(?-?)\s*(?:₹)?\s*(\d[\d,]*)(\.\d*)?(\)?)$/.exec(s);
  if (!m) return s;
  const [, open, whole, dec = '', close] = m;
  const digits = whole.replace(/,/g, '');
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')},${last3}` : last3;
  return `${open}${grouped}${dec}${close}`;
}

/** Lakh/crore words for PROSE only ("₹1.5 lakh"); tables always show full figures. */
export function inWords(amount) {
  const n = Math.abs(Number(amount));
  if (!Number.isFinite(n)) return '';
  if (n >= 1e7) return `₹${trim(n / 1e7)} crore`;
  if (n >= 1e5) return `₹${trim(n / 1e5)} lakh`;
  return `₹${groupIndian(String(n))}`;
}

const trim = (x) => String(Math.round(x * 100) / 100);
