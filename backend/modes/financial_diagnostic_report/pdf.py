"""Markdown -> PDF for the downloadable Financial Diagnostic Report.

The report already has one structure: each block's own `to_markdown`
function (`report.py`) writes it in the exact order and wording the screen
renders — headings, bullet fields, tables, and inline "[n] citation" sources
written the same way the UI's `Sources` / `Provenance` panels write them
(`FdrAnalysis.jsx`). This module does not re-derive that structure or reword
anything; it renders the block markdown `adapter.report_pdf` hands it
through a themed HTML template into a PDF — a pure layout step. What text
goes IN is that caller's decision (deliberately just the blocks, no title or
disclaimer the screen does not show — see `adapter.report_pdf`); this module
guarantees only that nothing is added or reworded on the way to PDF bytes.

Pure-Python pipeline (`markdown` + `xhtml2pdf`, which wraps `reportlab`)
rather than a browser-rendering path (`weasyprint`, `wkhtmltopdf`): both need
native system libraries or a headless-browser binary that a container or a
Windows dev host may not carry, and this mode's one operating rule — it stays
available with nothing but a database — extends to what it hands back too.
"""
from __future__ import annotations

import io
import re

import markdown as _markdown
from xhtml2pdf import pisa

# `report.py` writes several "**Label**" / "Sources:" headers immediately
# followed by "- item" lines, with no blank line between — e.g. block 6's
# "**Contributing signals**\n- S04 ...". python-markdown, unlike the
# CommonMark parser the frontend's `react-markdown` uses, will not start a
# list straight out of a paragraph line: without a blank line it renders the
# citation and signal lists as one run-on paragraph with literal dashes,
# which loses exactly the structure this module exists to preserve. Rather
# than touch every call site in `report.py` — risking the `.md` export and
# its pinned tests — a blank line is inserted here, in front of the first
# item of any list that follows a non-blank, non-list line.
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")


def _blank_line_before_lists(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if _LIST_ITEM.match(line) and out and out[-1].strip() and not _LIST_ITEM.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


# Mirrors the on-screen palette (`FdrAnalysis.jsx`'s `--navy-*` / `--ink-*`
# custom properties) so a reader who has seen the report on screen recognises
# the file. xhtml2pdf understands a CSS 2.1 subset only — no custom
# properties, no shadows, no radius — so the values are inlined and the
# rules kept to what it actually renders.
_CSS = """
  /* Landscape rather than portrait: the audit-planning matrix (block 7) is
     8 columns wide with one prose-heavy column, and portrait's ~17cm of
     usable width left it too cramped even after the fixed column split
     below — landscape's ~26cm gives every column room to hold its own
     header without the two colliding into unreadable overlap. */
  @page { size: A4 landscape; margin: 1.8cm 2.1cm; }
  body { font-family: Helvetica, sans-serif; font-size: 9.5pt; line-height: 1.5;
         color: #1d2733; }
  h1 { color: #0f2136; font-size: 18pt; margin: 0 0 4pt 0;
       border-bottom: 1.5pt solid #1e3a5f; padding-bottom: 8pt; }
  h1 + p { color: #6f7d8c; font-size: 9pt; font-style: italic; margin: 8pt 0 14pt 0; }
  h2 { color: #16324f; font-size: 13pt; margin: 20pt 0 8pt 0;
       border-bottom: 0.75pt solid #c3cbd4; padding-bottom: 4pt;
       page-break-after: avoid; }
  h3 { color: #1e3a5f; font-size: 10.5pt; margin: 12pt 0 5pt 0; page-break-after: avoid; }
  p { margin: 0 0 7pt 0; }
  strong { color: #16324f; }
  em { color: #52616f; }
  ul, ol { margin: 0 0 9pt 0; padding-left: 16pt; }
  li { margin: 0 0 3pt 0; }
  /* A quoted sentence read straight from the filing — the same evidence the
     screen shows behind a "Read from the filing" disclosure, kept visible
     here because a PDF has no disclosure to open. */
  blockquote { margin: 6pt 0 10pt 0; padding: 6pt 10pt; background-color: #f2f7fc;
               border-left: 2pt solid #2c5282; color: #33404f; font-style: italic;
               page-break-inside: avoid; }
  blockquote cite, blockquote p:last-child { font-style: normal; color: #6f7d8c;
                                              font-size: 8.5pt; }
  table { border-collapse: collapse; width: 100%; margin: 6pt 0 12pt 0; font-size: 8.5pt; }
  th { background-color: #eef1f4; color: #16324f; text-align: left; font-weight: bold;
       border: 0.5pt solid #c3cbd4; padding: 5pt 6pt; }
  td { border: 0.5pt solid #e0e5ea; padding: 5pt 6pt; vertical-align: top; }
  /* `.fdr-wide` is stamped onto any table `_mark_wide_tables` finds with 7+
     columns (currently only the audit-planning matrix) — see that function
     for why. xhtml2pdf auto-sizes columns from content it has already
     measured by the time it reaches the widest one, which on that table
     handed its one prose column (the recommended response) a column
     narrower than its own shortest word — reportlab then could not
     paginate that cell at all (a hard LayoutError, not an ugly wrap).
     xhtml2pdf does not implement `table-layout: fixed`, so the fix is
     explicit per-column widths; it does read `width` on `td`/`th` directly.
     Scoped to `.fdr-wide` rather than every table — the report's other
     tables (2-4 columns, no long-prose column) size correctly on their own,
     and this column split is tuned to this one table's shape, not a general
     rule. */
  .fdr-wide td:nth-child(1), .fdr-wide th:nth-child(1) { width: 3%; }
  .fdr-wide td:nth-child(2), .fdr-wide th:nth-child(2) { width: 14%; }
  .fdr-wide td:nth-child(3), .fdr-wide th:nth-child(3) { width: 12%; }
  .fdr-wide td:nth-child(4), .fdr-wide th:nth-child(4) { width: 10%; }
  .fdr-wide td:nth-child(5), .fdr-wide th:nth-child(5) { width: 34%; }
  .fdr-wide td:nth-child(6), .fdr-wide th:nth-child(6) { width: 10%; }
  .fdr-wide td:nth-child(7), .fdr-wide th:nth-child(7) { width: 8%; }
  .fdr-wide td:nth-child(8), .fdr-wide th:nth-child(8) { width: 9%; }
  hr { border: none; border-top: 0.75pt solid #e0e5ea; margin: 16pt 0; }
"""


_TABLE = re.compile(r"<table>.*?</table>", re.DOTALL)
_WIDE_HEADER_ROW = 7  # a header this many <th> cells long is dense enough to overflow.


def _mark_wide_tables(html: str) -> str:
    """Give any table with 7+ columns a `.fdr-wide` class for `_CSS` to key off.

    Found by column count, not by content — the report's other tables (2-4
    columns) are already sized fine by xhtml2pdf's own layout; only a table
    this dense is the one that needs the fixed column split at all, and this
    keeps that decision independent of which block happens to be wide today.
    """
    def tag_if_wide(match: re.Match) -> str:
        table_html = match.group(0)
        if table_html.count("<th>") >= _WIDE_HEADER_ROW:
            return table_html.replace("<table>", '<table class="fdr-wide">', 1)
        return table_html

    return _TABLE.sub(tag_if_wide, html)


def _sanitize_unicode(text: str) -> str:
    """Swap glyphs the base-14 Helvetica font xhtml2pdf/reportlab renders with
    cannot draw for an ASCII equivalent, so they show as the intended text
    instead of a missing-glyph box (`|_|`). The rupee sign is the one that
    actually appears in this mode's text (currency formatting in
    `xbrl_business_profile.py` / `xbrl_health.py` / `report.py`'s own
    formatters) — on screen it renders fine, since browsers carry a Unicode
    font; only this PDF path's font does not.
    """
    return text.replace("₹", "Rs. ")


def _html(markdown_text: str) -> str:
    # "tables" turns on GFM-style pipe tables — `report.py` uses them for the
    # dashboard, the matrix, the below-the-line list and the version stamp.
    # Without it those blocks would render as literal "| a | b |" text.
    text = _sanitize_unicode(markdown_text)
    text = _blank_line_before_lists(text)
    body = _markdown.markdown(text, extensions=["tables"])
    body = _mark_wide_tables(body)
    return f"<html><head><meta charset='utf-8'/><style>{_CSS}</style></head><body>{body}</body></html>"


def to_pdf(markdown_text: str) -> bytes:
    """Render the report's markdown to PDF bytes.

    Raises `RuntimeError` on a hard layout failure — a caller silently
    returning an empty or truncated file over a real error is a worse
    outcome than a 500 with the reason attached.
    """
    html = _html(markdown_text)
    buf = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=buf, encoding="utf-8", raise_exception=False)
    if result.err:
        raise RuntimeError(f"PDF rendering failed ({result.err} layout error(s)).")
    return buf.getvalue()
