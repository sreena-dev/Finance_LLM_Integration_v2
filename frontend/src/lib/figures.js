/**
 * The four states of a figure, read off a table cell's text.
 *
 * The backend writes three markers into `table_md` and nothing else:
 *   `[unreadable: page 5, table t3, row "...", col "..."]`   withheld
 *   `[recovered 1,757.00; second read, confidence medium, ...]`  recovered, unconfirmed
 *   `1,234 [user-entered]`                                    typed by a person
 * Anything else is a plain, machine-read figure and is shown VERBATIM -- it is
 * never reformatted, because what the filing printed is the evidence.
 */

const UNREADABLE_RE = /^\s*\[\s*unreadable\b[^\]]*\]\s*$/i;
const RECOVERED_RE = /^\s*\[\s*recovered\s+([^;\]]*?)\s*;[^\]]*\]\s*$/i;
const USER_RE = /^\s*(.*?)\s*\[user-entered\]\s*$/i;

/** `{ kind, value }` where kind is plain | unreadable | recovered | you. */
export function parseFigureCell(text) {
  const raw = typeof text === 'string' ? text : '';
  if (UNREADABLE_RE.test(raw)) return { kind: 'unreadable', value: '' };
  const rec = RECOVERED_RE.exec(raw);
  if (rec) return { kind: 'recovered', value: rec[1].trim() };
  const user = USER_RE.exec(raw);
  if (user) return { kind: 'you', value: user[1].trim() };
  return { kind: 'plain', value: raw };
}

export const FIGURE_LABELS = {
  unreadable: 'unreadable',
  recovered: 'recovered ?',
  you: 'you',
};

/** Does this cell look like a figure (so it is right-aligned, in mono)? */
export function looksNumeric(text) {
  const t = (text || '').trim();
  return /^[(\-]?\s*(?:₹|rs\.?)?\s*\d[\d,]*(?:\.\d+)?\s*\)?%?$/i.test(t);
}

/** Answer tag blocks: the model leads a paragraph with a bold tag. */
export const TAGS = {
  FINDING: 'finding',
  'RISK FLAG': 'risk',
  'AUDIT POINTER': 'pointer',
  'COVERAGE NOTE': 'coverage',
};
