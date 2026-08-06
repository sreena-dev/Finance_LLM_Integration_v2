/**
 * Formatting shared by the Trial Balance panels.
 *
 * Matched to the source UI (`theme.js: fmtINR`) so a figure reads identically in
 * both apps: rupee symbol, whole rupees, Indian digit grouping (1,23,45,678 —
 * not 123,456,78). Nulls and NaN render as an em dash rather than "0", because a
 * missing opening balance and a zero balance mean different things in a TB.
 */
export function fmtINR(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return `₹${Math.round(Number(n)).toLocaleString('en-IN')}`;
}

/** Plain grouped integer, no currency symbol — for counts and tile figures. */
export function fmtNum(v) {
  if (typeof v !== 'number') return v ?? '—';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

export function fmtPct(v) {
  return v != null ? `${v}%` : '—';
}

/**
 * Variance/movement flag → severity tone. HIGH_PRIORITY and DROPPED are the two
 * that warrant attention; NO_THRESHOLD means "computed but not judged", because
 * no materiality threshold was supplied, so it must not look like a pass.
 */
export const FLAG_TONE = {
  HIGH_PRIORITY: 'err',
  DROPPED: 'err',
  MEDIUM: 'warn',
  NEW_ENTRY: 'navy',
  LOW: 'mute',
  NO_CHANGE: 'mute',
  NO_THRESHOLD: 'mute',
  NO_DATA: 'mute',
};

/**
 * Open a print window containing already-rendered report HTML.
 *
 * Takes HTML rather than markdown so there is exactly one markdown pipeline in
 * the app: the caller passes the innerHTML of the report it is already showing,
 * which guarantees the PDF matches what is on screen.
 */
export function printReportHtml(title, html) {
  if (!html) return;
  const w = window.open('', '_blank');
  if (!w) return; // popup blocked — nothing useful to do but not crash
  const css = `
    @page { margin: 16mm; }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           font-size: 12px; line-height: 1.5; color: #1a1a1a; padding: 0; margin: 0; }
    h1 { font-size: 20px; margin: 0 0 6px; }
    h2 { font-size: 16px; margin: 18px 0 6px; border-bottom: 1px solid #ccc; padding-bottom: 3px; }
    h3 { font-size: 13.5px; margin: 12px 0 4px; }
    table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 11px; }
    th, td { border: 1px solid #bbb; padding: 4px 7px; text-align: left; vertical-align: top; }
    th { background: #f0f0f0; }
    tr { page-break-inside: avoid; }
    blockquote { margin: 8px 0; padding: 6px 12px; border-left: 3px solid #ccc; color: #444; }
    em { color: #555; }
    code { font-family: Menlo, Consolas, monospace; background: #f3f3f3; padding: 1px 3px; border-radius: 3px; }
    hr { border: none; border-top: 1px solid #ccc; margin: 14px 0; }
    ul, ol { margin: 4px 0 8px; padding-left: 20px; }
  `;
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>` +
    `<style>${css}</style></head><body>${html}</body></html>`
  );
  w.document.close();
  w.focus();
  // Give the new document a tick to lay out before invoking print.
  setTimeout(() => w.print(), 250);
}
