import datetime as _dt
import itertools
import json
import re
from pathlib import Path

import polars as pl
from docx.shared import Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.reasoning import _build_management_query  # single source of truth for query text


def _classify_fsli(row):
    """Maps a canonical TB row to a (FS Head, FSLI, Note) triple derived directly
    from main_head/sub_head_1/sub_head_2 — there is no persisted hierarchy_path or
    per-note override table in the new canonical schema."""
    status = str(row.get("mapped_status") or "").upper()
    if status != MAPPED_STATUS_MAPPED:
        return "Unmapped", "Unmapped", str(row.get("sub_head_2") or "")
    fs_head = str(row.get("main_head") or "Unmapped").strip() or "Unmapped"
    fsli = str(row.get("sub_head_1") or row.get("sub_head_2") or "Unclassified").strip()
    note = str(row.get("sub_head_2") or "")
    return fs_head, fsli, note

def _load_canonical_tb_for_report(canonical_path):
    """Loads canonical_tb.parquet, coerces numerics, adds report_fs_head/report_fsli/report_note."""
    df = pl.read_parquet(canonical_path)
    if "gl_code" in df.columns:
        df = df.filter(pl.col("gl_code").is_not_null())
    df = df.with_columns(
        [
            (
                pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).alias(c)
                if c in df.columns
                else pl.lit(0.0).alias(c)
            )
            for c in ["opening_balance", "debit", "credit", "closing_balance"]
        ]
    )
    classified = [_classify_fsli(r) for r in df.iter_rows(named=True)]
    df = df.with_columns(
        [
            pl.Series("report_fs_head", [c[0] for c in classified]),
            pl.Series("report_fsli", [c[1] for c in classified]),
            pl.Series("report_note", [c[2] for c in classified]),
        ]
    )
    if "business area" not in df.columns:
        df = df.with_columns(pl.lit("").alias("business area"))
    return df

def _fy_label(tb_metadata_path, fallback):
    """Resolve an 'FY 2025-26' style label for a period.

    Prefers tb_metadata.json's structured financial_year field (real MAIN document
    data, e.g. "2025-2026") and only then falls back to parsing the source filename. The
    filename-only version this replaces returned the caller's fallback -- literally
    "CY"/"PY" -- for any export whose name does not happen to embed an FY pattern,
    which is most of them: "TB with Grouping 31.03.2026-APCPL_TB_GROUPING.xlsx" carries
    the period plainly but matches no FY regex, so a comparative report labelled its two
    periods "PY" and "CY" while the correct years sat unread in the same file.
    """
    import json
    try:
        with open(tb_metadata_path, "r") as f:
            meta = json.load(f)

        fy = str(meta.get("financial_year") or "").strip()
        m = re.match(r"(\d{4})\D+(\d{2,4})$", fy)
        if m:
            return f"FY {m.group(1)}-{m.group(2)[-2:]}"
        if fy:
            return fy

        src = meta.get("source_file", "")
        m = re.search(r"FY\s*(\d{2})[-_](\d{2})", src, re.IGNORECASE)
        if m:
            return f"FY 20{m.group(1)}-{m.group(2)}"
    except Exception:
        pass  # best-effort label extraction; caller's fallback is always a valid answer
    return fallback

def _load_canonical_tb(out_dir, filename="canonical_tb.parquet"):
    """Loads a canonical TB parquet with numeric coercion; returns None if absent."""
    p = Path(out_dir) / filename
    if not p.exists():
        return None
    try:
        df = pl.read_parquet(p)
        df = df.with_columns(
            [
                pl.col(col).cast(pl.Float64, strict=False).fill_null(0.0)
                for col in ("opening_balance", "closing_balance", "debit", "credit")
                if col in df.columns
            ]
        )
        return df
    except Exception:
        return None

def _concentration_rows_from_canonical(df, threshold_pct=5.0, limit=15):
    """Accounts individually exceeding threshold_pct of their FS Head's total absolute closing balance."""
    if df is None or df.is_empty() or "main_head" not in df.columns:
        return []
    head_totals = dict(
        df.group_by("main_head").agg(pl.col("closing_balance").abs().sum().alias("total")).iter_rows()
    )
    rows = []
    for r in df.iter_rows(named=True):
        head = str(r.get("main_head", "") or "")
        total = head_totals.get(head, 0.0)
        if not total:
            continue
        cb = float(r.get("closing_balance", 0.0))
        pct = abs(cb) / total * 100
        if pct >= threshold_pct:
            rows.append({
                "gl_code": r.get("gl_code", ""),
                "gl_name": r.get("gl_name", ""),
                "fs_head": head,
                "closing": cb,
                "pct": pct,
            })
    rows.sort(key=lambda x: x["pct"], reverse=True)
    return rows[:limit]

def _dormant_rows_from_canonical(df, limit=15):
    """Accounts with zero debit/credit movement in the period but a non-zero closing balance."""
    if df is None or df.is_empty() or not {"debit", "credit", "closing_balance"}.issubset(set(df.columns)):
        return []
    dormant = df.filter((pl.col("debit") == 0) & (pl.col("credit") == 0) & (pl.col("closing_balance") != 0))
    if dormant.is_empty():
        return []
    dormant = dormant.with_columns(pl.col("closing_balance").abs().alias("_abs")).sort("_abs", descending=True)
    return [
        {
            "gl_code": r.get("gl_code", ""),
            "gl_name": r.get("gl_name", ""),
            "fs_head": r.get("main_head", ""),
            "closing": float(r.get("closing_balance", 0.0)),
        }
        for r in dormant.head(limit).iter_rows(named=True)
    ]

_TITLE_FONT = Font(bold=True, size=16, color="FF1F3864")

_SUBTITLE_FONT = Font(size=10, color="FF595959")

_SECTION_FONT = Font(bold=True, size=10, color="FF44546A")

_TBL_HDR_FILL = PatternFill("solid", fgColor="FF1F3864")

_TBL_HDR_FONT = Font(bold=True, size=10, color="FFFFFFFF")

_ZEBRA_FILL = PatternFill("solid", fgColor="FFF2F2F2")

_THIN_SIDE = Side(style="thin")

_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)

_ACC_FMT = r'#,##0;\(#,##0\);\-'

_PCT_FMT = r'0.00\%'

def _write_sheet_title(ws, title, subtitle, span_cols):
    ws.cell(row=1, column=1, value=title).font = _TITLE_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(span_cols, 1))
    if subtitle:
        ws.cell(row=2, column=1, value=subtitle).font = _SUBTITLE_FONT
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(span_cols, 1))

def _write_section_header(ws, row, text):
    ws.cell(row=row, column=1, value=text).font = _SECTION_FONT
    return row + 1

def _write_table(ws, start_row, headers, rows, formats=None):
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.font = _TBL_HDR_FONT
        c.fill = _TBL_HDR_FILL
        c.border = _CELL_BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, row_vals in enumerate(rows):
        r = start_row + 1 + i
        zebra = (i % 2 == 1)
        for j, val in enumerate(row_vals, start=1):
            c = ws.cell(row=r, column=j, value=val)
            c.border = _CELL_BORDER
            c.font = Font(size=10)
            if zebra:
                c.fill = _ZEBRA_FILL
            fmt = formats[j - 1] if formats and j - 1 < len(formats) else None
            if fmt:
                c.number_format = fmt
                c.alignment = Alignment(horizontal="right")
            else:
                c.alignment = Alignment(horizontal="left")
    return start_row + 1 + len(rows)

def _set_col_widths(ws, widths):
    for col_letter, width in widths.items():
        ws.column_dimensions[col_letter].width = width

def _write_engagement_sheet(ws, tb_meta, engagement_ctx, normalisation, data_sufficiency,
                            mode_label, period_label, account_count):
    """Engagement identity, first sheet in the workbook.

    A reviewer opening this file cold previously met control totals with nothing naming
    the client, the period, or the kind of review anywhere in the workbook. Beyond
    identity, the four rows after it are the ones that decide whether the figures below
    can be read at all: currency and scale (a bare 708,593,986,735 means nothing without
    them), the accounting framework the taxonomy was applied under, how much of the
    ledger actually mapped, and the scope the numbers cover.
    """
    _write_sheet_title(ws, "Engagement Details",
                       "Identity of this review and the basis on which every figure in "
                       "this workbook should be read.", 4)

    cs = (normalisation or {}).get("currency_and_scale") or {}
    currency = cs.get("currency") or (engagement_ctx or {}).get("currency") or "unknown"
    scale = cs.get("scale") or "unknown"
    if str(currency).lower() == "unknown" and str(scale).lower() == "unknown":
        cur_scale = "Not declared in the source file - figures are shown exactly as supplied"
    else:
        cur_scale = f"{currency} / {scale}"

    framework = (engagement_ctx or {}).get("framework") or "unknown"
    if (engagement_ctx or {}).get("framework_basis") == "inferred_to_confirm":
        framework = f"{framework} (inferred from the ledger - confirm with the engagement team)"

    suff = (data_sufficiency or {}).get("grade") or "unknown"
    mapped_pct = (data_sufficiency or {}).get("mapped_percentage")
    if isinstance(mapped_pct, (int, float)):
        suff = f"{suff} - {mapped_pct:.0f}% of accounts mapped to a chart of accounts"

    rows = [
        ("Company Name", tb_meta.get("company_name") or tb_meta.get("entity_name") or "Not stated"),
        ("CIN", tb_meta.get("cin") or "Not supplied"),
        ("Review Type", mode_label),
        ("Period of Review", period_label),
        ("Currency and Scale", cur_scale),
        ("Accounting Framework", framework),
        ("Accounts Analysed", account_count),
        ("Data Sufficiency", suff),
    ]
    next_row = _write_table(ws, 4, ["Particular", "Detail"], rows, formats=None)

    # Provenance sits below the identity block rather than inside it: it describes the
    # file and the run, not the engagement.
    prov = (normalisation or {}).get("provenance") or {}
    prov_rows = [
        ("Source File", tb_meta.get("source_file") or prov.get("source_file") or "Not recorded"),
        ("Report Generated", _dt.datetime.now().strftime("%d %B %Y, %H:%M")),
    ]
    next_row = _write_table(ws, next_row + 2, ["Provenance", "Detail"], prov_rows, formats=None)

    note = ws.cell(
        row=next_row + 1, column=1,
        value=("This workbook is a mechanical analysis of the trial balance supplied. It expresses no "
               "audit opinion and concludes on no matter. Where a figure here disagrees with the signed "
               "financial statements, see the Materiality Workings sheet, which shows every input used "
               "so the difference can be traced to its source."),
    )
    note.font = Font(size=9, italic=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=next_row + 1, start_column=1, end_row=next_row + 1, end_column=4)
    ws.row_dimensions[next_row + 1].height = 46
    return next_row + 2


def _write_benchmark_buildup_sheet(ws, out_dir, materiality):
    """Show how each benchmark base amount was actually built, head by head.

    The Materiality Workings sheet states that Total Assets is 302,562,931,892.31.
    This sheet states which balances summed to it. Without that an auditor cannot agree
    the base to the signed financial statements at all -- a single total is not
    re-performable, and "agree this to the accounts" is not an instruction anyone can
    follow against one number.

    Reproduces build_financial_snapshot's own arithmetic exactly rather than
    recalculating it a second way: the level-1 nodes of snapshot_drilldown.parquet,
    grouped by classify_row()'s head. If this sheet did not tie to
    financial_snapshot_statistics.json it would be worse than absent, so the tie is
    asserted on the sheet itself and flagged when it breaks.

    Level 1 is Schedule III main-head granularity ("Current assets", "Non-current
    assets"), which is the level the face of the balance sheet is presented at -- so the
    comparison an auditor makes is line against line, not total against total.
    """
    _write_sheet_title(ws, "Benchmark Build-Up - How Each Base Amount Was Derived",
                       "Every balance that sums into each benchmark, at the level the "
                       "financial statements present them. Agree these to the signed accounts.", 5)

    snap_path = out_dir / "snapshot_drilldown.parquet"
    stats_path = out_dir / "financial_snapshot_statistics.json"
    if not snap_path.exists():
        c = ws.cell(row=4, column=1,
                    value="snapshot_drilldown.parquet is not present for this run, so the "
                          "benchmark build-up cannot be shown. Run build_financial_snapshot first.")
        c.font = Font(size=9, italic=True)
        return 5

    tree = pl.read_parquet(snap_path)
    level1 = tree.filter(pl.col("hierarchy_level") == 1)

    # Same grouping build_financial_snapshot performs, so the figures below are the ones
    # materiality actually consumed rather than a parallel calculation of them.
    by_head = {}
    for r in level1.to_dicts():
        head, _, _ = classify_row(r)
        by_head.setdefault(head, []).append(
            (str(r.get("main_head") or r.get("node_name") or ""), float(r.get("balance") or 0.0))
        )

    stats = {}
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except Exception:
            stats = {}

    row = 4
    # Assets / Revenue / Expenses are the three benchmarks derived directly from a head;
    # PBT is derived from two of them and is shown separately below.
    for head, stat_key, label in (
        ("Assets", "total_assets", "Total Assets"),
        ("Revenue", "total_revenue", "Total Revenue"),
        ("Expenses", "total_expenses", "Total Expenses"),
    ):
        components = by_head.get(head, [])
        row = _write_section_header(ws, row, f"{label} - components")
        row += 1

        derived = sum(b for _, b in components)
        body = [[name, bal, "Agree to the corresponding line in the signed accounts"]
                for name, bal in sorted(components, key=lambda x: abs(x[1]), reverse=True)]
        body.append([f"{label} (sum of the above)", derived, "This is the base amount used"])
        row = _write_table(
            ws, row,
            ["Balance / Head", "Amount", "Verification"],
            body,
            formats=[None, _ACC_FMT, None],
        )

        # State the tie explicitly. A build-up that silently disagreed with the figure the
        # threshold was computed from would mislead more than showing nothing.
        reported = stats.get(stat_key)
        if reported is not None:
            ties = abs(derived - float(reported)) < 0.01
            msg = (f"Ties to financial_snapshot_statistics.json ({float(reported):,.2f})."
                   if ties else
                   f"DOES NOT TIE: this build-up gives {derived:,.2f} against "
                   f"{float(reported):,.2f} recorded in financial_snapshot_statistics.json. "
                   "Treat the benchmark as unreliable and report this.")
            c = ws.cell(row=row, column=1, value=msg)
            c.font = Font(size=9, italic=True, bold=not ties)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            row += 1
        row += 1

    # Profit Before Tax is not a head -- it is the difference of two, and an auditor
    # checking it needs to see which two rather than infer them.
    rev = sum(b for _, b in by_head.get("Revenue", []))
    exp = sum(b for _, b in by_head.get("Expenses", []))
    row = _write_section_header(ws, row, "Profit Before Tax - derivation")
    row += 1
    row = _write_table(
        ws, row,
        ["Component", "Amount", "Verification"],
        [
            ["Total Revenue (as built above)", abs(rev), "From the Total Revenue section"],
            ["Total Expenses (as built above)", abs(exp), "From the Total Expenses section"],
            ["Revenue - Expenses (as this pipeline computes it)", abs(rev) - abs(exp),
             "Read the caveat below before agreeing this to the statement of profit and loss"],
        ],
        formats=[None, _ACC_FMT, None],
    )
    row += 1

    # Disclosed rather than silently corrected. The components above are grouped by
    # classify_row()'s heads, and on a Schedule III chart "Expenses" absorbs the tax
    # charge while "Revenue" absorbs other comprehensive income. The subtraction is
    # therefore not profit BEFORE tax in the sense the statement of profit and loss uses
    # it, and an auditor told to agree it to the signed accounts would not be able to.
    # Whether to change the benchmark definition is an accounting-policy decision for the
    # engagement team, not a code change to be made unilaterally -- so the figure is left
    # exactly as the pipeline computes it and the caveat is stated on the sheet.
    tax_like = [(n, b) for n, b in by_head.get("Expenses", []) if "tax" in n.lower()]
    oci_like = [(n, b) for n, b in by_head.get("Revenue", []) if "comprehensive" in n.lower()]
    caveats = []
    if tax_like:
        amt = sum(abs(b) for _, b in tax_like)
        caveats.append(
            f"Total Expenses above includes {', '.join(n for n, _ in tax_like)} of {amt:,.2f}. "
            f"The subtraction is therefore profit AFTER that charge. Profit before it would be "
            f"{abs(rev) - abs(exp) + amt:,.2f}."
        )
    if oci_like:
        amt = sum(abs(b) for _, b in oci_like)
        caveats.append(
            f"Total Revenue above includes {', '.join(n for n, _ in oci_like)} of {amt:,.2f}, "
            "which the statement of profit and loss presents below the profit line rather than "
            "within revenue."
        )
    if caveats:
        c = ws.cell(row=row, column=1,
                    value="Caveat on this derivation: " + " ".join(caveats) +
                          " Confirm with the engagement team which basis the planning benchmark "
                          "should use before relying on a Profit Before Tax benchmark.")
        c.font = Font(size=9, italic=True, bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        ws.row_dimensions[row].height = 46
        row += 2

    note = ws.cell(
        row=row, column=1,
        value=("Signs are shown as the trial balance carries them. Revenue and other credit-balance "
               "heads therefore appear negative; the benchmark calculation uses their absolute value, "
               "which is why the Profit Before Tax derivation above works on magnitudes."),
    )
    note.font = Font(size=9, italic=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    ws.row_dimensions[row].height = 32
    row += 2

    # What to do when it does not agree. Placed on this sheet because this is where the
    # auditor is standing when they find the difference.
    row = _write_section_header(ws, row, "Where This Does Not Agree to the Signed Financial Statements")
    row += 1
    guidance = [
        ["Wrong trial balance was uploaded",
         "Wrong period, a partial export, or accounts missing from the extract.",
         "Obtain the correct trial balance from the client and upload it again. The analysis "
         "must be re-run; nothing in this workbook can be relied on until it is."],
        ["The grouping is wrong",
         "The trial balance is right, but an account has been mapped to the wrong head, so it "
         "lands in the wrong benchmark.",
         "Correct the grouping workbook and re-run. Do not alter the trial balance -- it is not "
         "the thing that is wrong."],
        ["A genuine reconciling item",
         "The signed accounts include audit adjustments, reclassifications or consolidation "
         "entries posted after this trial balance was extracted.",
         "Both figures are correct. Document the difference as a reconciling item in the audit "
         "file. Do NOT re-upload to force agreement -- doing so would discard a valid audit trail "
         "and fit the evidence to the conclusion."],
        ["Scale or sign convention not declared",
         "The source file did not state whether figures are units, thousands, lakh or crore, or "
         "the debit/credit convention could not be confirmed (see the Rule Register, TB-000 and "
         "TB-026).",
         "Establish the scale and convention with the client. Re-uploading the same file does not "
         "resolve this."],
    ]
    row = _write_table(
        ws, row,
        ["If the cause is...", "Which looks like...", "Then"],
        guidance, formats=None,
    )

    caution = ws.cell(
        row=row + 1, column=1,
        value=("Establish the cause before acting. The trial balance is the evidence and the signed "
               "accounts are the assertion being examined; adjusting the former until it agrees with "
               "the latter reverses that relationship. Re-upload where the extract was wrong -- not "
               "to remove a difference that is real."),
    )
    caution.font = Font(size=9, italic=True, bold=True)
    caution.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=3)
    ws.row_dimensions[row + 1].height = 46
    return row + 2


def _write_materiality_workings_sheet(ws, materiality):
    """The arithmetic behind the Materiality sheet, shown step by step.

    An auditor cannot accept a planning threshold they cannot re-perform. This sheet
    exists so the benchmark selection can be checked line by line against signed
    accounts -- every base amount, the percentage applied to it, the figure it produces,
    and why each candidate was or was not chosen.

    That transparency is also the answer to a specific risk. Where the trial balance or
    its grouping is inaccurate, the computed benchmark will be inaccurate too, and the
    finished number alone gives no way to tell a tool error from a data error. Showing
    the inputs lets the reviewer establish which of the two occurred and record it,
    rather than leaving an unexplained difference attributed to the software.
    """
    _write_sheet_title(ws, "Materiality - Workings and Re-performance",
                       "Every input to the benchmark selection, so the figure on the "
                       "Materiality sheet can be checked rather than taken on trust.", 7)

    sel = (materiality or {}).get("selected_materiality") or {}
    th = (materiality or {}).get("thresholds") or {}
    candidates = (materiality or {}).get("benchmark_analysis") or []

    # Step 1 -- the arithmetic actually performed, in the order performed.
    chosen = next((c for c in candidates if c.get("recommended")), {})
    base = chosen.get("base_amount", sel.get("base_amount", 0.0))
    pct = chosen.get("pct", 0.0)
    om = th.get("overall", 0.0)
    steps = [
        ["1", "Benchmark selected", chosen.get("benchmark", sel.get("benchmark_used", "Unknown")), ""],
        ["2", "Base amount taken from the trial balance", base,
         "Sum of the grouped accounts forming this benchmark. Agree this to the signed accounts."],
        ["3", "Percentage applied", f"{pct}%",
         "From backend/knowledge/materiality/benchmarks.json - configurable, not a rate mandated by any standard."],
        ["4", "Overall Materiality = base x percentage", om, "Step 2 multiplied by step 3."],
        ["5", "Performance Materiality = 75% of Overall", th.get("performance", 0.0),
         "Reduces the risk that uncorrected and undetected misstatements together exceed Overall Materiality."],
        ["6", "Clearly Trivial = 5% of Overall", th.get("trivial", om * 0.05 if om else 0.0),
         "Below this, items are not accumulated."],
    ]
    next_row = _write_table(
        ws, 4, ["Step", "Computation", "Value", "How to verify it"], steps, formats=None,
    )

    # Step 2 -- why this benchmark and not the others. Selection is the judgement an
    # auditor is most likely to disagree with, so every candidate is shown, not just the
    # one that won.
    row = _write_section_header(ws, next_row + 1,
                                "Every Benchmark Considered - and Why It Was or Was Not Used")
    cand_rows = [
        [
            c.get("benchmark", ""),
            c.get("base_amount", 0.0),
            f"{c.get('pct', 0)}%",
            c.get("hypothetical_materiality", 0.0),
            "Selected" if c.get("recommended") else "Not selected",
            c.get("confidence", ""),
            c.get("reason", ""),
        ]
        for c in candidates
    ]
    row = _write_table(
        ws, row + 1,
        ["Benchmark", "Base Amount", "%", "Would Give", "Outcome", "Confidence", "Reason"],
        cand_rows,
        formats=[None, _ACC_FMT, None, _ACC_FMT, None, None, None],
    )

    # Step 3 -- what the audit team is expected to do with all of the above.
    row = _write_section_header(ws, row + 1, "Re-performance by the Audit Team")
    checks = [
        ["1", "Agree the base amount at step 2 to the signed financial statements for the same period."],
        ["2", "Where it does not agree, identify whether the difference arises in the trial balance "
              "supplied, in the grouping applied to it, or in this computation. The candidate table "
              "above shows every input used, so the source of the difference can be established "
              "rather than assumed."],
        ["3", "Record the conclusion in the audit file. If the inputs were wrong, the threshold is "
              "wrong for that reason and the corrected data should be re-run; the computation itself "
              "is arithmetic and re-performable from the figures shown."],
        ["4", "Replace this provisional figure with the engagement team's approved materiality before "
              "it is relied on. This is a planning aid for prioritising the analytics in this "
              "workbook, not an audit-approved threshold."],
    ]
    row = _write_table(ws, row + 1, ["Step", "Procedure"], checks, formats=None)

    note = ws.cell(
        row=row + 1, column=1,
        value=("SA 320 requires materiality to reflect the engagement team's judgement about the "
               "information needs of users of the financial statements. Nothing on this sheet "
               "substitutes for that judgement; it is a mechanical calculation from the trial balance "
               "alone, offered so the team can see how the figure arose and decide whether to adopt, "
               "adjust or replace it."),
    )
    note.font = Font(size=9, italic=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=7)
    ws.row_dimensions[row + 1].height = 46
    return row + 2


def _write_materiality_basis_table(ws, start_row, materiality, mapping_quality=None, anomaly_data=None):
    """SA 320 (Materiality in Planning and Performing an Audit, ICAI) requires the
    materiality benchmark to be one appropriate to the entity's
    circumstances, and for that choice to be explainable, not asserted. build_materiality
    already scores every candidate (PBT/Revenue/Assets/Expenses) before picking a winner
    (see benchmark_analysis in materiality.json) -- this renders all of them, so the sheet
    shows the full working instead of a single unexplained number.

    Every candidate here is computed from the uploaded TB's own account_type/main_head
    classification (see classify_row) -- there is no external benchmark or prior-period
    reference involved. That means an incorrect FSLI mapping on the source TB silently
    produces a wrong materiality figure with no warning of its own, which is why this also
    renders whatever mapping-reliability signals the pipeline already computed elsewhere
    (mapping_quality.json's deterministic flags, TB-022's LLM semantic-mismatch spot-check)
    right beside the number, instead of leaving them buried in a different sheet."""
    candidates = materiality.get("benchmark_analysis", [])
    summ = materiality.get("summary", {})
    chosen_name = summ.get("benchmark_used", "")

    row = _write_section_header(ws, start_row, "Benchmark Comparison — All Bases Considered (SA 320)")
    row += 1
    cand_rows = [
        [
            c.get("benchmark", ""),
            c.get("base_amount", 0.0),
            f"{c.get('pct', 0.0):.2f}%",
            c.get("hypothetical_materiality", 0.0),
            "Yes" if c.get("positive") else "No",
            "Yes" if c.get("stable") else "No",
            c.get("confidence", ""),
            "SELECTED" if c.get("benchmark") == chosen_name else "",
            c.get("reason", ""),
        ]
        for c in candidates
    ]
    row = _write_table(
        ws, row,
        ["Benchmark", "Base Amount", "% Applied", "Hypothetical Materiality",
         "Positive?", "Stable?", "Confidence", "Selected", "Rationale"],
        cand_rows,
        formats=[None, _ACC_FMT, None, _ACC_FMT, None, None, None, None, None],
    )
    row += 1

    methodology_lines = [
        "Methodology (SA 320, Materiality in Planning and Performing an Audit, as issued by the "
        "ICAI): the benchmark "
        "must suit the entity's circumstances -- profit-oriented entities are ordinarily assessed "
        "against pre-tax profit, but revenue, total assets, or expenses are used instead when profit "
        "is small, volatile, negative, or the entity is asset-heavy or non-trading.",
        "This pipeline evaluates all four candidates above and selects one via a waterfall: Profit "
        "Before Tax (5%) is used only if PBT is positive, stable (>2% of revenue), and the revenue "
        "behind it is genuinely operating rather than investment/other income; otherwise Total Revenue "
        "(1%) if the entity has real operating revenue; otherwise Total Assets (1%) for dormant or "
        "holding entities; Total Expenses (1%) is the final fallback for non-profits or shell companies.",
        "Performance materiality is set at 75% of Overall Materiality and the Clearly Trivial threshold "
        "at 5% of Overall Materiality -- both configurable in backend/knowledge/materiality/benchmarks.json.",
        "This is a MECHANICAL, PROVISIONAL calculation for prioritising the analytics in this report -- "
        "it does not incorporate engagement risk assessment, qualitative factors, regulatory sensitivity, "
        "or prior-period misstatements. Supply the engagement team's audit-approved materiality to "
        "override it; the override replaces Overall Materiality above and is recorded as 'audit_approved' "
        "rather than 'computed_proxy' in materiality_lens.json.",
    ]
    for line in methodology_lines:
        c = ws.cell(row=row, column=1, value=line)
        c.font = Font(size=9, italic=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
        ws.row_dimensions[row].height = 30
        row += 1
    row += 1

    # Reliability of the FSLI mapping the four benchmarks above were computed from --
    # every figure in this sheet is only as trustworthy as this classification.
    coverage = materiality.get("coverage", {}) or {}
    mq = mapping_quality or {}
    mq_summary = mq.get("summary", {})
    anomaly = anomaly_data or {}
    tb022_findings = [f for f in anomaly.get("findings", []) if f.get("rule_id") == "TB-022"]
    tb022_check = anomaly.get("checks", {}).get("TB-022", {})
    tb022_status = tb022_check.get("status")
    if tb022_status == "SKIPPED":
        tb022_display = f"Not run — {tb022_check.get('reason', 'no LLM available')}"
    elif tb022_status in ("PASS", "FLAGGED"):
        tb022_display = f"{len(tb022_findings)} possible mismatch(es) flagged" if tb022_findings else "No mismatches flagged"
    else:
        tb022_display = "Not run for this session"

    row = _write_section_header(ws, row, "Mapping & Classification Reliability")
    reliability_rows = [
        ("Mapping Coverage Confidence", coverage.get("confidence", "Unknown")),
        ("Mapped Accounts / Value", f"{coverage.get('mapped_accounts', 0)} accounts / {coverage.get('mapped_value', 0):,.0f}"),
        ("Unmapped Accounts / Value", f"{coverage.get('unmapped_accounts', 0)} accounts / {coverage.get('unmapped_value', 0):,.0f}"),
        ("Mapping-Quality Flags (duplicate labels, implausible FSLI)", mq_summary.get("total_flags", "Not run for this session")),
        ("Semantic Mismatch Spot-Check (TB-022, LLM-assisted)", tb022_display),
    ]
    row = _write_table(ws, row, ["Indicator", "Value"], reliability_rows, formats=None)
    row += 1
    caveat = ws.cell(
        row=row, column=1,
        value=(
            "Every figure above is computed from this TB's own FSLI mapping (account_type/main_head) -- "
            "not from an external benchmark or a prior-period reference. If Mapping Coverage Confidence "
            "is Medium/Low, or any Mapping-Quality flags or semantic mismatches are present, treat this "
            "materiality figure as indicative only pending a mapping review."
        ),
    )
    caveat.font = Font(size=9, italic=True, bold=True)
    caveat.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
    ws.row_dimensions[row].height = 30
    row += 1
    return row

# Severity and Status answer two different questions and were previously shown side by
# side with nothing to say so, which reads as a contradiction: TB-000 reports
# status=WARNING with severity=Blocking, and a reader reasonably asks which it is.
#   severity = how serious this check is IF it fails      (a fixed property of the rule)
#   status   = what actually happened on THIS trial balance (the outcome of this run)
# Both are kept for traceability, but the plain-language Outcome column below is the one
# a reviewer should read, and the legend that follows the table states the distinction on
# the sheet itself rather than leaving it to be inferred.
_RULE_OUTCOMES = {
    ("PASS", None): "Passed",
    ("HALTED", "Blocking"): "Failed — must resolve",
    ("HALTED", None): "Failed",
    ("WARNING", "Blocking"): "Failed — must resolve",
    ("WARNING", "Warning"): "Failed — review",
    ("WARNING", "Info"): "Observation only",
    ("SKIPPED", None): "Not checked — data unavailable",
    ("NOT_IMPLEMENTED", None): "Not checked — needs master data",
}

def _rule_outcome(status, severity) -> str:
    """Collapse (status, severity) into the single plain-language verdict a reviewer reads."""
    status = (status or "").upper()
    severity = (severity or "").title()
    return (
        _RULE_OUTCOMES.get((status, severity))
        or _RULE_OUTCOMES.get((status, None))
        or (status.replace("_", " ").title() if status else "Unknown")
    )

_RULE_LEGEND = [
    ("Outcome", "The verdict for this check on this trial balance. Read this column first."),
    ("Passed", "The check ran and the condition held."),
    ("Failed — must resolve", "A Blocking check did not hold. Analysis downstream of it is unreliable until the underlying data is corrected."),
    ("Failed — review", "The check did not hold, but the condition is not fatal to the analysis. Apply judgement."),
    ("Observation only", "Informational. Recorded for completeness, no action implied."),
    ("Not checked — data unavailable", "This dataset lacked something the check needs (for example a comparative period). A different upload could supply it."),
    ("Not checked — needs master data", "The check requires an approved chart-of-accounts or Group master that this pipeline does not ingest from any source, for any client. No upload of this trial balance will enable it."),
    ("Severity", "How serious this check is IF it fails — a fixed property of the rule, not a result. Blocking / Warning / Info."),
    ("Status", "The raw engine result for this run, retained for traceability: PASS, WARNING, HALTED, SKIPPED or NOT_IMPLEMENTED."),
]

def _write_rule_register_legend(ws, start_row):
    """Print the Outcome/Severity/Status legend directly beneath the register.

    On the sheet, not in the covering document: the workbook is routinely detached from
    the report and circulated on its own, and a column whose meaning lives in another file
    is a column that gets misread."""
    c = ws.cell(row=start_row, column=1, value="Legend")
    c.font = Font(bold=True, size=10)
    row = start_row + 1
    for term, meaning in _RULE_LEGEND:
        t = ws.cell(row=row, column=1, value=term)
        t.font = Font(bold=True, size=9)
        t.alignment = Alignment(vertical="top")
        m = ws.cell(row=row, column=2, value=meaning)
        m.font = Font(size=9)
        m.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        ws.row_dimensions[row].height = 26
        row += 1
    return row + 1

def _write_rule_register_table(ws, start_row, rule_results, title="Rule Register — Every TB-0xx Check"):
    """Full population of every Layer-1/Layer-2 rule this pipeline evaluated, PASS through
    NOT_IMPLEMENTED alike -- moved here from the narrative Word report (which previously
    inlined up to 12 of these as bullet points under 'Standalone Analytical Review') so the
    complete, sortable, filterable working paper lives in the workbook and the Word document
    can cite it in one line instead. See validate_layer1_tb / validate_layer2_tb."""
    if not isinstance(rule_results, list) or not rule_results:
        row = _write_section_header(ws, start_row, title)
        c = ws.cell(row=row + 1, column=1, value="No rule results available for this session.")
        c.font = Font(size=9, italic=True)
        return row + 2

    row = _write_section_header(ws, start_row, title)
    row += 1

    # Rule-ID order, not outcome order. A register is read as a checklist -- a reviewer
    # looks up "what did TB-011 do", and re-runs are diffed row against row. Sorting by
    # status reshuffles the sheet whenever an outcome changes, which makes two runs of the
    # same entity impossible to compare side by side.
    ordered = sorted(rule_results, key=lambda r: str(r.get("rule") or "~"))
    reg_rows = [
        [
            r.get("rule", ""),
            r.get("rule_name", ""),
            _rule_outcome(r.get("status"), r.get("severity")),
            r.get("severity", ""),
            r.get("status", ""),
            r.get("message", ""),
        ]
        for r in ordered
    ]
    row = _write_table(
        ws, row,
        ["Rule", "Check", "Outcome", "Severity", "Status", "Message"],
        reg_rows, formats=None,
    )
    row += 1

    row = _write_rule_register_legend(ws, row)

    not_impl_count = sum(1 for r in rule_results if r.get("status") == "NOT_IMPLEMENTED")
    if not_impl_count:
        note = ws.cell(
            row=row, column=1,
            value=(
                f"{not_impl_count} rule(s) above are marked NOT_IMPLEMENTED: they require an approved "
                "chart-of-accounts/Group master reference that this pipeline does not currently ingest "
                "from any source, for any client -- distinct from SKIPPED, which means this particular "
                "dataset lacked something (e.g. a comparative period) that a different upload could supply."
            ),
        )
        note.font = Font(size=9, italic=True)
        note.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        ws.row_dimensions[row].height = 30
        row += 1
    return row

_SUBTOTAL_L1_FILL = PatternFill("solid", fgColor="FFD9E1F2")
_SUBTOTAL_L2_FILL = PatternFill("solid", fgColor="FFF2F2F2")
_SUBTOTAL_FONT = Font(bold=True, size=10)

# Excel cell fills for cluster-severity subtotal rows -- a separate palette from
# _SEVERITY_COLORS___shared (docx.shared.RGBColor, Word font colors only; PatternFill
# needs its own RGB hex strings, the two types are not interchangeable).
_SEVERITY_FILL_XLSX = {
    "CRITICAL": PatternFill("solid", fgColor="FFF4C7C3"),
    "HIGH": PatternFill("solid", fgColor="FFFBD9B4"),
    "MEDIUM": PatternFill("solid", fgColor="FFFFF2B2"),
    "LOW": PatternFill("solid", fgColor="FFE2EFDA"),
    "INFORMATION REQUEST": PatternFill("solid", fgColor="FFF2F2F2"),
}

def _write_grouped_canonical_sheet(ws, headers_row, df, account_type_col="account_type",
                                    head_col="report_head", note_col="report_note"):
    """Replaces the separate FSLI Summary sheet: FSLI-level and FS-Head-level totals are
    written as native Excel SUBTOTAL() rows directly below their own GL detail rows, with
    Excel's native row grouping (the +/- outline buttons) collapsing the detail beneath each
    total. A user who collapses to level 1 sees exactly what the old FSLI Summary sheet
    showed -- except every number is now a live formula over the visible GL rows above it,
    not a value this pipeline asserted separately in a second sheet.

    Root problem this fixes: FSLI Summary previously grouped by the RAW main_head/sub_head_1
    text (Schedule III wording, e.g. "Current Assets"), while the old Canonical TB sheet's
    "FS Head" column showed classify_row()'s normalized 5-bucket head (e.g. "Assets") under
    an identical-looking column name. A user's SUMIFS against one could never match the
    other's labels. This sheet groups by the SAME raw main_head/sub_head_1 text FSLI Summary
    always used, so there is exactly one taxonomy in the workbook, not two silently different
    ones sharing a column header.

    Returns the next free row."""
    if df is None or df.is_empty():
        c = ws.cell(row=headers_row, column=1, value="No canonical TB data available.")
        c.font = Font(size=9, italic=True)
        return headers_row + 1

    def _key(v):
        s = str(v).strip() if v is not None else ""
        return s if s else "Unmapped"

    rows = df.to_dicts()
    for r in rows:
        r["_main_key"] = _key(r.get("main_head"))
        sub_key = _key(r.get("sub_head_1"))
        r["_sub_key"] = sub_key if sub_key != "Unmapped" else r["_main_key"]
    rows.sort(key=lambda r: (r["_main_key"], r["_sub_key"], str(r.get("gl_code") or "")))

    headers = ["GL Code", "GL Name", "Account Type", "FSLI (Sub-head)", "GL Accounts",
               "Opening", "Debit", "Credit", "Closing", "Note", "Mapping Status"]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=headers_row, column=j, value=h)
        c.font = _TBL_HDR_FONT
        c.fill = _TBL_HDR_FILL
        c.border = _CELL_BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")

    NUM_COLS = [("opening_balance", 6), ("debit", 7), ("credit", 8), ("closing_balance", 9)]

    def _display_note(r):
        return None if r.get(head_col) == "Unmapped" else r.get(note_col)

    def _display_status(r):
        return r.get("mapped_status") or ("UNMAPPED" if r.get(head_col) == "Unmapped" else "MAPPED")

    def _write_detail_row(row_idx, r):
        vals = [r.get("gl_code"), r.get("gl_name"), r.get(account_type_col), r.get("_sub_key"), None,
                r.get("opening_balance", 0.0), r.get("debit", 0.0), r.get("credit", 0.0),
                r.get("closing_balance", 0.0), _display_note(r), _display_status(r)]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=j, value=v)
            c.border = _CELL_BORDER
            c.font = Font(size=10)
            if j in (6, 7, 8, 9):
                c.number_format = _ACC_FMT
                c.alignment = Alignment(horizontal="right")
        ws.row_dimensions[row_idx].outlineLevel = 2

    def _write_subtotal_row(row_idx, label, first_data_row, last_data_row, outline_level, gl_count=None):
        label_cell = ws.cell(row=row_idx, column=2, value=label)
        label_cell.font = _SUBTOTAL_FONT
        fill = _SUBTOTAL_L1_FILL if outline_level == 0 else _SUBTOTAL_L2_FILL
        for j in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=j).fill = fill
            ws.cell(row=row_idx, column=j).border = _CELL_BORDER
        if gl_count is not None:
            gc = ws.cell(row=row_idx, column=5, value=gl_count)
            gc.font = _SUBTOTAL_FONT
        for col_name, col_idx in NUM_COLS:
            col_letter = ws.cell(row=row_idx, column=col_idx).column_letter
            formula = f"=SUBTOTAL(9,{col_letter}{first_data_row}:{col_letter}{last_data_row})"
            fc = ws.cell(row=row_idx, column=col_idx, value=formula)
            fc.number_format = _ACC_FMT
            fc.font = _SUBTOTAL_FONT
            fc.alignment = Alignment(horizontal="right")
        ws.row_dimensions[row_idx].outlineLevel = outline_level

    row = headers_row + 1
    for main_key, main_group_iter in itertools.groupby(rows, key=lambda r: r["_main_key"]):
        main_group = list(main_group_iter)
        main_start_row = row
        for sub_key, sub_group_iter in itertools.groupby(main_group, key=lambda r: r["_sub_key"]):
            sub_group = list(sub_group_iter)
            sub_start_row = row
            for r in sub_group:
                _write_detail_row(row, r)
                row += 1
            sub_end_row = row - 1
            # Only worth its own subtotal row if it's a real subdivision of the FS head --
            # a sub-head identical to its own main head (the "Unmapped" bucket, or a flat
            # grouping workbook with no second tier) would just repeat the FS-head total below.
            if sub_key != main_key:
                _write_subtotal_row(row, f"Sub-total — {sub_key}", sub_start_row, sub_end_row,
                                     outline_level=1, gl_count=len(sub_group))
                row += 1
        main_end_row = row - 1
        _write_subtotal_row(row, f"TOTAL — {main_key}", main_start_row, main_end_row,
                             outline_level=0, gl_count=len(main_group))
        row += 1

    ws.sheet_properties.outlinePr.summaryBelow = True
    return row

def _write_exception_register(ws, headers_row, clusters, exceptions):
    """Consolidated Exception Register, with the breakup the cluster-only view couldn't
    show: every member account behind each cluster's headline count/amount, its own specific
    risk theme (rule_id), and -- new -- the plain-language reason that theme was raised at
    all, pulled straight from build_exception_consolidator's own triggered_rules[].description
    (already written for every engine; it just was never surfaced here). Cluster totals are
    native Excel SUBTOTAL() rows over these same visible account rows, collapsible via the
    row-group outline, matching Canonical TB's pattern -- so 'Accounts' and 'Amount' are
    verifiable arithmetic, not a second number asserted alongside the detail.

    `exceptions` is consolidated_exceptions.json's own "exceptions" array; each entry's
    cluster_context.cluster_id is the SAME id minted once for its cluster (TB-R07) -- grouping
    by it here, rather than re-deriving membership, cannot drift from the cluster it names."""
    if not clusters:
        c = ws.cell(row=headers_row, column=1, value="No exceptions were flagged.")
        c.font = Font(size=9, italic=True)
        return headers_row + 1

    headers = ["ID", "Cluster / Account", "Severity", "Accounts", "Amount",
               "Risk Theme", "Why This Is Flagged", "Composite Score", "Score Breakdown"]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=headers_row, column=j, value=h)
        c.font = _TBL_HDR_FONT
        c.fill = _TBL_HDR_FILL
        c.border = _CELL_BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")

    members_by_cluster = {}
    for exc in (exceptions or []):
        cid = (exc.get("cluster_context") or {}).get("cluster_id")
        if cid:
            members_by_cluster.setdefault(cid, []).append(exc)

    sev_rank = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Information Request": 0}
    ranked_clusters = sorted(clusters, key=lambda c: sev_rank.get(c.get("cluster_severity", "Low"), 0), reverse=True)

    def _write_row(row_idx, vals, outline_level, bold=False, fill=None):
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=j, value=v)
            c.border = _CELL_BORDER
            c.font = _SUBTOTAL_FONT if bold else Font(size=10)
            if fill:
                c.fill = fill
            if j in (4, 5, 8):
                c.number_format = _ACC_FMT
                c.alignment = Alignment(horizontal="right")
            elif j in (7, 9):
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row_idx].outlineLevel = outline_level

    row = headers_row + 1
    for cluster in ranked_clusters:
        cid = cluster.get("cluster_id", "")
        members = members_by_cluster.get(cid, [])
        start_row = row
        for exc in members:
            bc = exc.get("business_context") or {}
            fc = exc.get("financial_context") or {}
            entity_name = bc.get("gl_name") or (exc.get("entity") or {}).get("entity_name", "")
            gl_label = f"{bc.get('gl_code')} — {entity_name}" if bc.get("gl_code") else entity_name
            scoring = exc.get("scoring") or {}
            sev = scoring.get("severity", "Low")
            rules = exc.get("triggered_rules") or []
            theme_str = ", ".join(dict.fromkeys(r.get("rule_id", "") for r in rules if r.get("rule_id")))
            why_str = " | ".join(dict.fromkeys(r.get("description", "") for r in rules if r.get("description")))
            # remark #15 fix: weights and provenance already exist in triggered_rules[].weight
            # and scoring.composite_score -- this is a surfacing gap, not missing computation.
            composite_score = scoring.get("composite_score", 0.0)
            breakdown_str = ", ".join(
                f"{r.get('rule_id', '')} +{r.get('weight', 0):g}" for r in rules if r.get("weight") is not None
            )
            _write_row(
                row,
                [None, f"    {gl_label}", sev, 1, abs(float(fc.get("closing_balance", 0.0))), theme_str, why_str,
                 composite_score, breakdown_str],
                outline_level=1,
            )
            row += 1
        end_row = row - 1

        cluster_name = cluster.get("cluster_name", "Unnamed")
        cluster_sev = str(cluster.get("cluster_severity", "Low")).upper()
        risk_themes_summary = ", ".join(cluster.get("risk_themes", []))
        if members:
            accounts_val = f"=SUBTOTAL(9,D{start_row}:D{end_row})"
            amount_val = f"=SUBTOTAL(9,E{start_row}:E{end_row})"
            why_summary = "See rows above for the specific reason each account was flagged."
            score_val = f"=SUBTOTAL(9,H{start_row}:H{end_row})"
        else:
            # No traceable member rows (e.g. an older artifact without cluster_context)
            # -- fall back to the cluster's own asserted totals rather than a formula
            # over an empty range, and say so plainly.
            accounts_val = cluster.get("member_count", 0)
            amount_val = cluster.get("total_balance", 0.0)
            why_summary = "Member-account detail unavailable for this cluster."
            score_val = cluster.get("cluster_score", 0.0)
        _write_row(
            row,
            [cid, f"TOTAL — {cluster_name}", cluster_sev, accounts_val, amount_val, risk_themes_summary, why_summary,
             score_val, ""],
            outline_level=0, bold=True,
            fill=_SEVERITY_FILL_XLSX.get(cluster_sev, _SUBTOTAL_L1_FILL),
        )
        row += 1

    ws.sheet_properties.outlinePr.summaryBelow = True
    return row

def _write_rows_grouped_by_fs_head(ws, start_row, rows, headers, row_fn, formats=None, head_key="fs_head"):
    """Writes `rows` as one Excel table per distinct FS Head (sorted, section-headed) instead
    of a single flat list ordered only by score/materiality -- so 'what does this look like
    for Assets specifically' is answerable by scrolling to that head's own block, not by the
    reader re-sorting a flat table themselves. Mirrors the per-category grouping the Sensitive
    Accounts sheet already uses; promoted here so Movement (BS) and Risk Indicators can share
    it instead of three near-identical loops."""
    if not rows:
        c = ws.cell(row=start_row, column=1, value="No accounts to display.")
        c.font = Font(size=9, italic=True)
        return start_row + 1
    row = start_row
    heads = sorted({str(r.get(head_key) or "Unmapped") for r in rows})
    for head in heads:
        group = [r for r in rows if str(r.get(head_key) or "Unmapped") == head]
        row = _write_section_header(ws, row, head)
        table_rows = [row_fn(r) for r in group]
        row = _write_table(ws, row, headers, table_rows, formats=formats)
        row += 1
    return row

def _write_legend_sheet(ws, start_row):
    """One-stop reference for every High/Medium/Low(/Critical/Information Request) scale
    used anywhere in this workbook -- Materiality Priority, Sensitive-Account Priority,
    Exception/Cluster Severity, Movement Flag, Mapping Coverage Confidence, and Data
    Sufficiency Grade all classify things as "High" or "Medium" or "Low", but each is a
    DIFFERENT scale computed a DIFFERENT way. Rendering them side by side, each under its own
    heading with its own thresholds and worked logic, is deliberate: a reader who sees
    "Medium" on the Sensitive Accounts sheet and "Medium" on the Exception Register should be
    able to look up here and see these are not the same number on two labels, rather than
    assume one unified severity system runs the whole report.

    Two scales below (coverage confidence vs data-sufficiency mapping confidence) use
    genuinely different thresholds for what sounds like the same question -- that is not a
    typo; it is documented here rather than silently reconciled, since actually unifying them
    is a product decision, not a reporting one."""
    row = start_row

    def _section(title, subtitle=None):
        nonlocal row
        row = _write_section_header(ws, row, title)
        if subtitle:
            c = ws.cell(row=row, column=1, value=subtitle)
            c.font = Font(size=9, italic=True)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
            row += 1
        row += 1

    def _table(headers, rows_, formats=None):
        nonlocal row
        row = _write_table(ws, row, headers, rows_, formats=formats)
        row += 2

    _section(
        "1. Materiality-Based Priority",
        "Used on: Materiality sheet's material-GL/FSLI population. Basis: the account's "
        "absolute closing balance as a multiple of Overall Materiality (OM) -- see the "
        "Materiality sheet's own benchmark table for how OM itself is derived.",
    )
    _table(
        ["Priority", "Threshold (multiple of Overall Materiality)"],
        [
            ("Critical", "> 100x OM"),
            ("High", "20x – 100x OM"),
            ("Medium", "5x – 20x OM"),
            ("Low", "0.75x – 5x OM"),
            ("None", "< 0.75x OM (clearly trivial)"),
        ],
    )

    _section(
        "2. Sensitive-Account Priority",
        "Used on: Sensitive Accounts sheet's Priority column. Basis: a 0-100 sensitivity "
        "score -- category base weight (e.g. related-party, statutory dues) + a materiality "
        "proximity bonus (+20 if the balance exceeds OM, +15 if it exceeds Performance "
        "Materiality) + up to +35 carried over from that account's own Risk Indicator score, "
        "capped at 100.",
    )
    _table(
        ["Priority", "Sensitivity Score"],
        [
            ("Critical", "> 75"),
            ("High", "50 – 75"),
            ("Medium", "25 – 50"),
            ("Low", "< 25"),
        ],
    )

    _section(
        "3. Exception / Cluster Severity",
        "Used on: Exception Register sheet. Basis: resolve_severity() -- the single function "
        "every finding engine's composite weighted score passes through (never re-tiered "
        "per-sheet). A finding below the Clearly Trivial threshold is capped at Low regardless "
        "of score, however many rule-engines fired on it. A cluster's own severity is the "
        "worst severity among its member accounts.",
    )
    _table(
        ["Severity", "Composite Score", "Overridden to, if amount < Clearly Trivial threshold"],
        [
            ("Critical", "> 75", "Low"),
            ("High", "50 – 75", "Low"),
            ("Medium", "25 – 50", "Low"),
            ("Low", "0 – 25 (score > 0)", "Low"),
            ("Information Request", "0", "Information Request"),
        ],
    )

    _section(
        "4. Movement Flag",
        "Used on: Movement (BS) sheet. Basis: the account's absolute opening-to-closing "
        "movement against the same Overall Materiality (OM) / Performance Materiality (PM) "
        "used throughout this report.",
    )
    _table(
        ["Flag", "Threshold"],
        [
            ("Critical", "≥ 2x Overall Materiality"),
            ("High", "≥ 1x Overall Materiality"),
            ("Medium", "≥ Performance Materiality"),
        ],
    )

    _section(
        "5. Mapping Coverage Confidence — Materiality Sheet",
        "Used on: Materiality sheet's Mapping & Classification Reliability table. Basis: % of "
        "total TB value mapped to a chart-of-accounts head (by value, not by account count).",
    )
    _table(["Confidence", "Mapped Value %"], [("High", "≥ 95%"), ("Medium", "≥ 85%"), ("Low", "< 85%")])

    _section(
        "6. Mapping Confidence — Data Sufficiency Grade",
        "Used on: the Data Sufficiency Grade quoted in the Word report and data_sufficiency.json. "
        "Basis: % of GL ACCOUNTS (by count, not value) mapped to a chart of accounts. This is a "
        "DIFFERENT calculation with DIFFERENT thresholds from item 5 above -- both exist in this "
        "pipeline today; they are not meant to be read as the same figure.",
    )
    _table(
        ["Grade", "Mapped Accounts %", "Also requires"],
        [
            ("High", "≥ 80%", "Comparative (PY) data also supplied"),
            ("Medium", "≥ 50%", "—"),
            ("Low", "< 50%, or no canonical TB supplied", "—"),
            ("Information Request", "—", "Any Blocking rule HALTED (TB does not foot)"),
        ],
    )

    note = ws.cell(
        row=row, column=1,
        value=(
            "None of these scales are interchangeable, and none is a conclusion on fraud, "
            "misstatement, or non-compliance -- each is a mechanical screen for prioritising "
            "further audit work, described here so a reader always knows which scale, and "
            "which formula, produced the label they are looking at."
        ),
    )
    note.font = Font(size=9, italic=True, bold=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.row_dimensions[row].height = 30
    row += 2

    _section(
        "7. Scope and Limitations of This Tool",
        "What this pipeline deliberately does NOT do -- an absent CARO conclusion or journal-"
        "entry test should read as out of scope by design, never as something the run missed.",
    )
    for line in SCOPE_LIMITATIONS:
        c = ws.cell(row=row, column=1, value="•  " + line)
        c.font = Font(size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws.row_dimensions[row].height = 28
        row += 1

    return row

_SEVERITY_COLORS___shared = {
    "CRITICAL": RGBColor(0xC0, 0x00, 0x00),
    "HIGH": RGBColor(0xE0, 0x00, 0x00),
    "MEDIUM": RGBColor(0xE8, 0x7C, 0x00),
    "MED": RGBColor(0xE8, 0x7C, 0x00),
    "LOW": RGBColor(0xC9, 0xA6, 0x00),
    "INFORMATION REQUEST": RGBColor(0x1A, 0x1A, 0x1A),
}

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
                  6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}

def _apply_report_styles(doc):
    """Applies the firm's customised TB audit report typography — Times New Roman
    throughout (16pt dark blue title, 14/12/11pt black headings, 10.5pt body).

    Sized for the report's 5-8 page budget: 10.5pt body / 14pt Heading 1 is standard for
    a dense professional audit report (this was 12pt/16pt, i.e. noticeably larger than
    the firm norm) -- dropping it is a real page-count reduction with zero content loss,
    on top of the section-by-section trims elsewhere in this function."""
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(3)

    try:
        body = doc.styles["Body Text"]
        body.font.name = "Times New Roman"
        body.font.size = Pt(10.5)
        body.paragraph_format.space_after = Pt(3)
    except KeyError:
        pass

    try:
        list_bullet = doc.styles["List Bullet"]
        list_bullet.font.name = "Times New Roman"
        list_bullet.font.size = Pt(10.5)
        list_bullet.paragraph_format.space_after = Pt(2)
    except KeyError:
        pass

    title = doc.styles["Title"]
    title.font.name = "Times New Roman"
    title.font.size = Pt(16)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0x17, 0x36, 0x5D)
    title.paragraph_format.space_after = Pt(6)

    heading_color = RGBColor(0x00, 0x00, 0x00)
    # (size, space_before, space_after) -- Word's default Heading 1 space_before is 24pt;
    # at 25 numbered sections that alone is ~0.6 page of pure whitespace before any
    # content. Tightened here rather than per-heading call site, so it applies uniformly.
    heading_spacing = {"Heading 1": (14, 8, 2), "Heading 2": (12, 6, 2), "Heading 3": (11, 4, 2)}
    for name, (size, before, after) in heading_spacing.items():
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.font.name = "Times New Roman"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = heading_color
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)

def _add_page_number_field___shared(paragraph):
    """Inserts a Word PAGE field (auto-updating page number) into a paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    run.font.size = Pt(9)
    run.font.name = "Times New Roman"
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)

def _setup_page_layout___shared(doc, header_left: str, header_right: str):
    """Applies page setup — Portrait A4, 0.85/0.9in margins — plus a header (left:
    generation date-time, right: entity/year) and a footer with a centred,
    auto-numbered page count starting at 1."""
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Mm

    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.85)
    section.left_margin = Inches(0.9)
    section.right_margin = Inches(0.9)
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)

    sect_pr = section._sectPr
    pg_num_type = OxmlElement("w:pgNumType")
    pg_num_type.set(qn("w:start"), "1")
    sect_pr.append(pg_num_type)

    usable_width = section.page_width - section.left_margin - section.right_margin

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    hp.text = ""
    hp.paragraph_format.tab_stops.add_tab_stop(usable_width, WD_TAB_ALIGNMENT.RIGHT)
    run_left = hp.add_run(header_left)
    run_left.font.size = Pt(9)
    run_left.font.name = "Times New Roman"
    hp.add_run("\t")
    run_right = hp.add_run(header_right)
    run_right.font.size = Pt(9)
    run_right.font.name = "Times New Roman"
    run_right.bold = True

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.text = ""
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_page_number_field___shared(fp)

def _set_table_cell_margins___shared(table, top=0, bottom=0, left=80, right=80):
    """Sets tight cell padding (twips: 0 top/bottom, 80=~0.055in left/right) on every cell
    in `table`. python-docx's default Table Grid style carries a non-zero top/bottom cell
    margin at the STYLE level (not visible as a per-cell override, which is why this
    couldn't be read back from a freshly created table) -- at 8pt cell text and 150+ table
    rows across a report, that vertical padding is real page count. Uses the OOXML `left`/
    `right` tag names (not `start`/`end`, which some Word versions silently ignore in a
    `tcMar` and fall back to the style default for -- confirmed via a real Word page-count
    regression when this used start/end)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for row in table.rows:
        for cell in row.cells:
            tcPr = cell._tc.get_or_add_tcPr()
            mar = OxmlElement("w:tcMar")
            for tag, val in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
                node = OxmlElement(f"w:{tag}")
                node.set(qn("w:w"), str(val))
                node.set(qn("w:type"), "dxa")
                mar.append(node)
            tcPr.append(mar)

def _docx_add_table___shared(doc, headers, rows, cell_pt=8, header_pt=10):
    """Adds a Table Grid table with a bold header row, matching the template's table styling."""
    if not rows:
        doc.add_paragraph("No data available for this table.", style="Body Text")
        return None
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(h))
        run.bold = True
        run.font.size = Pt(header_pt)
        run.font.name = "Segoe UI"
    for row_vals in rows:
        row = table.add_row()
        for i, v in enumerate(row_vals):
            cell = row.cells[i]
            cell.text = ""
            run = cell.paragraphs[0].add_run(str(v))
            run.font.size = Pt(cell_pt)
            run.font.name = "Segoe UI"
    # A trailing empty full paragraph here (was: doc.add_paragraph("", style="Body Text"))
    # cost ~15pt of blank space per table -- ~0.6 page total across a report with 25+
    # tables. Body Text's own space_after already separates the table from what follows.
    _set_table_cell_margins___shared(table)
    return table

def _docx_add_note(doc, text, style="Body Text"):
    """Adds an italicised note paragraph -- one size down from body text (9pt vs 10.5pt),
    since this is always secondary material (a schedule cross-reference, a capping
    disclosure, a caveat) and there are ~20 of these across the report."""
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

def _docx_add_severity_heading___shared(doc, prefix: str, severity: str, title: str, level: int = 2):
    """Adds a Heading-style paragraph where the '[SEVERITY]' token is colour-coded."""
    style_name = f"Heading {level}"
    h = doc.add_paragraph(style=style_name)
    h.add_run(f"{prefix}. [")
    sev_run = h.add_run(severity)
    sev_run.font.color.rgb = _SEVERITY_COLORS___shared.get(severity.upper(), RGBColor(0x00, 0x00, 0x00))
    h.add_run(f"] {title}")
    return h

def _docx_add_subtitle(doc, text):
    """Adds a small grey subtitle line beneath the report title (period/date range)."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x7F, 0x7F, 0x7F)
    run.italic = True

def _docx_add_notice(doc, text):
    """Adds the small grey 'Notice to the reader' paragraph beneath the title block."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

def _docx_add_lead_bullet(doc, lead, rest):
    """Adds a bulleted line with a bold navy lead phrase, e.g. 'Concentration — <detail>'."""
    p = doc.add_paragraph(style="List Bullet")
    lead_run = p.add_run(f"{lead} — ")
    lead_run.bold = True
    lead_run.font.color.rgb = RGBColor(0x17, 0x36, 0x5D)
    p.add_run(rest)

def _write_limitations_section(doc, source_desc: str, comparative: bool = False):
    """Writes the 'Limitations & No-Opinion Statement' bullet list, grouped by theme
    (remark #17 fix) rather than as one flat, repeated list -- and, in comparative
    mode, with limitations specific to a PY-vs-CY review that a single-period review
    would never encounter."""
    doc.add_heading("24. Limitations & No-Opinion Statement", level=1)
    scope = "the two trial balance files and the grouping workbook supplied" if comparative else "the trial balance file and the grouping workbook supplied"
    doc.add_paragraph(f"This review is mechanical and confined entirely to {scope}:")

    data_limitations = [
        "Grouping not independently verified — classifications rely on the GL Grouping workbook as "
        "supplied and have not been independently verified against statutory financial statements.",
        "No rescaling — figures are presented exactly as extracted from source, unscaled.",
    ]
    model_methodology_limitations = [
        "Mechanical, rule-based classification — findings are generated by deterministic keyword and "
        "threshold rules and, where noted, an LLM narrative pass over that output; neither substitutes "
        "for professional audit judgment.",
        "No corroboration performed — no vouching, confirmation, analytical corroboration against "
        "external data, or management inquiry was performed.",
    ]
    audit_scope_limitations = [
        "No assurance opinion — it does not constitute an audit, review, or any other assurance "
        "engagement under any professional standard.",
    ]
    if not comparative:
        audit_scope_limitations.append(
            "Single-period scope — with only one period available, this review cannot distinguish "
            "ordinary year-on-year change from a genuine anomaly — a comparative (PY vs CY) review is "
            "recommended once a prior-period file is available."
        )
    else:
        data_limitations.append(
            "Prior-year grouping not independently tested — the PY trial balance's own classifications "
            "rely on the same unverified GL Grouping workbook process as CY, tested no more rigorously."
        )
        data_limitations.append(
            "PY figures not agreed to audited accounts — the prior-year trial balance used for "
            "comparison has not been agreed to the entity's own audited financial statements for that year."
        )
        audit_scope_limitations.append(
            "No restatement or transition check performed — this review does not test whether any "
            "PY-to-CY restatement, reclassification, or accounting-policy transition was properly "
            "reflected."
        )
        audit_scope_limitations.append(
            "Chart-of-account changes not agreed to a change log — new and removed ledgers between "
            "periods (Section 7) are reported as observed, not agreed to an approved chart-of-accounts "
            "change log."
        )

    groups = [
        ("Data limitations", data_limitations),
        ("Model / methodology limitations", model_methodology_limitations),
        ("Audit-scope limitations", audit_scope_limitations),
    ]
    for label, bullets in groups:
        p = doc.add_paragraph()
        run = p.add_run(label)
        run.bold = True
        for b in bullets:
            doc.add_paragraph(b, style="List Bullet")
    doc.add_paragraph("This document should be read alongside, not instead of, full substantive audit procedures.")







_OBSERVATION_TEXT_FIELDS = ["title", "executive_summary", "detailed_observation", "audit_risk", "conclusion"]

# remark #19 fix: the comparative report has a fixed 1-25 section numbering (several
# existing tests already assert on this invariant) -- an LLM-authored observation citing
# "Section N" outside this range is a hallucinated cross-reference, not a real one.
_VALID_SECTION_RANGE = range(1, 26)
_SECTION_CITATION_RE = re.compile(r"\bSection\s+(\d+)\b", re.IGNORECASE)

def _strip_invalid_section_citations(text: str) -> str:
    """Removes a 'Section N' citation from LLM-authored narrative text when N falls
    outside this report's actual 1-25 section range -- a lightweight self-consistency
    check so a hallucinated cross-reference is never silently shipped in the report."""
    if not text:
        return text

    def _replace(match):
        try:
            n = int(match.group(1))
        except ValueError:
            return match.group(0)
        return match.group(0) if n in _VALID_SECTION_RANGE else ""

    return re.sub(_SECTION_CITATION_RE, _replace, text)

_COMPARISON_REASONING_PROMPT_TEMPLATE = """You are a Senior Audit Manager performing audit planning and generating professional, evidence-based audit observations for a Prior Year (PY) vs Current Year (CY) trial balance comparison.

## Objective
Convert the deterministic comparison findings provided below into a professional audit observation following strict audit terminology.

## Design Principles
1. **Evidence-First**: Every statement must originate from the deterministic pipeline outputs provided.
2. **Never Invent**: Do not infer transactions not represented in the payload. Do not invent balances, relationships, account classifications, or findings.
3. **Conservative Language**: NEVER conclude fraud, error, misstatement, or non-compliance. Use conservative phrasing like "may indicate," "could suggest," "requires corroboration," or "warrants investigation." State uncertainty when evidence is insufficient.
4. **No Speculation**: Do not speculate about fraud, management intent, or accounting errors.
5. **Explainability**: Explain what happened, why it matters, possible legitimate explanations, and required corroboration.

## Input Context
**Finding Type**: {{finding_type}}
**Severity**: {{severity}}

**Comparison Evidence (Summarized)**:
```json
{{finding_payload}}
```

## Output Requirements
You must return a raw JSON object strictly matching the schema below. Do not wrap it in markdown block quotes (e.g., no ```json ... ```). Return ONLY the raw JSON.

### Output JSON Schema
{
    "observation_id": "OBS-{{finding_type}}",
    "title": "A concise, professional title for this observation.",
    "executive_summary": "One concise bullet explaining why the auditor should care about this comparison finding.",
    "detailed_observation": "Explain what the deterministic PY/CY comparison detected.",
    "audit_risk": "Explain why this increases audit attention (e.g., continuity, completeness, existence).",
    "affected_assertions": ["Assertion 1", "Assertion 2"],
    "recommended_procedures": ["Explicit audit procedure 1", "Explicit audit procedure 2"],
    "priority": "{{severity}}",
    "conclusion": "Final wrap-up sentence on the required audit focus."
}"""

def _build_comparison_findings(precheck_data: dict, structural_data: dict, variance_rows: list, sign_check_data: dict) -> list:
    """Deterministic, evidence-first extraction of comparison findings from Task 1 artifacts."""
    findings = []

    for check in precheck_data.get("checks", []):
        if check.get("check") == "opening_closing_continuity" and check.get("break_count", 0) > 0:
            findings.append({
                "type": "continuity_break",
                "severity": "High",
                "detail": (
                    f"{check['break_count']} GL code(s) show Opening(CY) does not equal Closing(PY), "
                    "a continuity break that requires corroboration."
                ),
                "evidence": check.get("breaks", [])[:10],
            })
        elif check.get("status") == "HALTED":
            findings.append({
                "type": "control_total_halt",
                "severity": "Critical",
                "detail": (
                    f"{check.get('check')}: debit/credit control totals differ by "
                    f"{check.get('diff_pct', 'n/a')}%, exceeding the tolerance for automatic continuation."
                ),
                "evidence": check,
            })
        elif check.get("status") == "WARNING":
            findings.append({
                "type": "control_total_warning",
                "severity": "Medium",
                "detail": (
                    f"{check.get('check')}: debit/credit control totals differ by "
                    f"{check.get('diff_pct', 'n/a')}%."
                ),
                "evidence": check,
            })

    new_ledgers = structural_data.get("new_ledgers", []) or []
    removed_ledgers = structural_data.get("removed_ledgers", []) or []
    if new_ledgers or removed_ledgers:
        findings.append({
            "type": "structural_delta",
            "severity": "Medium",
            "detail": structural_data.get("message", "PY/CY ledger structure changed."),
            "evidence": {"new_ledgers": new_ledgers[:20], "removed_ledgers": removed_ledgers[:20]},
        })

    high_priority = [r for r in variance_rows if r.get("flag") == "CRITICAL"]
    if high_priority:
        findings.append({
            "type": "material_variance",
            "severity": "High",
            "detail": (
                f"{len(high_priority)} GL code(s) show a PY/CY closing-balance variance exceeding "
                "2x provisional materiality."
            ),
            "evidence": high_priority[:20],
        })

    dropped = [r for r in variance_rows if r.get("flag") == "BALANCE_DISAPPEARED"]
    new_entries = [r for r in variance_rows if r.get("flag") == "BALANCE_APPEARED"]
    if dropped or new_entries:
        findings.append({
            "type": "balance_appearance_disappearance",
            "severity": "Medium",
            "detail": (
                f"{len(new_entries)} GL code(s) newly carry a balance in CY (zero in PY) and "
                f"{len(dropped)} GL code(s) that carried a balance in PY are zero in CY."
            ),
            "evidence": {"new_entries": new_entries[:20], "dropped": dropped[:20]},
        })

    for flag in sign_check_data.get("flags", []) or []:
        if flag.get("status") == "WARNING":
            findings.append({
                "type": "sign_convention_flag",
                "severity": "Medium",
                "detail": flag.get("message", "Sign convention warning."),
                "evidence": flag,
            })

    return findings

@pipeline_tool("build_comparison_reasoning", domain="comparison")
def build_comparison_reasoning(
    precheck_file: str,
    structural_file: str,
    variance_file: str,
    sign_check_file: str,
    output_dir: str = None,
    llm_client=None,
    **kwargs
) -> dict:
    """Builds comparison_reasoning.json — evidence-grounded audit observations from Task 1 comparison artifacts."""
    errors = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _load_json(path_str, label):
        if not path_str:
            errors.append({"type": "MissingArgument", "label": label})
            return {}
        p = Path(path_str)
        if not p.exists():
            errors.append({"type": "FileNotFoundError", "file": str(p), "label": label})
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": str(p), "error": str(e)})
            return {}

    precheck_data = _load_json(precheck_file, "precheck")
    structural_data = _load_json(structural_file, "structural")
    sign_check_data = _load_json(sign_check_file, "sign_check")

    variance_rows = []
    if variance_file:
        var_path = Path(variance_file)
        if var_path.exists():
            variance_rows = pl.read_parquet(var_path).to_dicts()
        else:
            errors.append({"type": "FileNotFoundError", "file": str(var_path), "label": "variance"})

    findings = _build_comparison_findings(precheck_data, structural_data, variance_rows, sign_check_data)

    if not findings:
        reasoning_json = {
            "summary": {"executive_summary": "No comparison exceptions were flagged by the deterministic pipeline."},
            "observations": [],
        }
        out_file = out_dir / "comparison_reasoning.json"
        write_json_atomic(reasoning_json, out_file, indent=4)
        return {
            "execution_status": "SUCCESS",
            "pipeline_status": "SUCCESS",
            "message": "No comparison findings; wrote empty comparison_reasoning.json.",
            "artifacts": [str(out_file)],
            "errors": errors,
        }

    observations = []
    stats = {"llm_calls": 0, "failures": 0}

    for finding in findings:
        finding_type = finding["type"]
        severity = finding["severity"]

        deterministic_obs = {
            "observation_id": f"OBS-{finding_type}",
            "title": finding_type.replace("_", " ").title(),
            "executive_summary": finding["detail"],
            "detailed_observation": finding["detail"],
            "audit_risk": (
                "This finding may indicate a break in continuity, completeness, or accuracy between "
                "the prior year and current year trial balances and warrants corroboration."
            ),
            "affected_assertions": ["Completeness", "Accuracy"],
            "recommended_procedures": [
                "Obtain and review the underlying ledger/reconciliation supporting the flagged GL code(s).",
                "Corroborate with management explanation and supporting documentation.",
            ],
            "priority": severity,
            "conclusion": (
                f"This {finding_type.replace('_', ' ')} finding requires corroboration before any view is formed."
            ),
        }

        obs_data = None
        if llm_client is not None:
            prompt = _COMPARISON_REASONING_PROMPT_TEMPLATE
            prompt = prompt.replace("{{finding_type}}", finding_type)
            prompt = prompt.replace("{{severity}}", severity)
            prompt = prompt.replace("{{finding_payload}}", json.dumps(finding, indent=2, default=str))
            messages = [{"role": "user", "content": prompt}]

            for _ in range(3):
                try:
                    stats["llm_calls"] += 1
                    response = llm_client.generate(messages, max_tokens=2048)
                except Exception:
                    # The client already retries internally with its own backoff on a
                    # connection failure (see its own retry logging) -- a failure here
                    # means the service is unreachable, not a one-off blip. Retrying this
                    # same failure again just re-multiplies an already-multiplied wait
                    # (up to 9 real attempts per finding before this fix); give up on
                    # this finding immediately and fall back to the deterministic
                    # template rather than let one down LLM stall the whole comparison.
                    break
                try:
                    content = response.get("content", "{}") if isinstance(response, dict) else getattr(response, "content", "{}")
                    cleaned = content.strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    if cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    obs_data = json.loads(cleaned.strip())
                    break
                except Exception:
                    continue  # malformed JSON from the model -- worth asking again

            if obs_data is None:
                stats["failures"] += 1

        observation = obs_data or deterministic_obs
        # Deterministic safe-wording pass -- applies regardless of LLM vs
        # deterministic-template origin, on every free-text field. Disclaimer
        # attached once as a structured field, not mashed into the prose.
        for field in _OBSERVATION_TEXT_FIELDS:
            if observation.get(field):
                observation[field] = apply_safe_wording(observation[field], append_disclaimer=False)
        observation["safe_limitation"] = SAFE_WORDING_DISCLAIMER
        # remark #19 fix (self-consistency validator): an LLM-authored observation can
        # cite a section number that doesn't exist in this report (a fixed 1-25 section
        # invariant several tests already assert on) -- strip rather than silently ship
        # a hallucinated cross-reference.
        if obs_data is not None:
            for field in _OBSERVATION_TEXT_FIELDS:
                if observation.get(field):
                    observation[field] = _strip_invalid_section_citations(observation[field])

        observations.append({"observation": observation, "finding": finding})

    reasoning_json = {
        "metadata": {
            "methodology": "Deterministic PY/CY Comparison Reasoning Engine",
            "llm_used": llm_client is not None,
            "stats": stats,
        },
        "summary": {
            "executive_summary": f"Generated {len(observations)} comparison observation(s) from the PY/CY trial balance comparison."
        },
        "observations": observations,
    }

    out_file = out_dir / "comparison_reasoning.json"
    write_json_atomic(reasoning_json, out_file, indent=4)
    artifacts.append(str(out_file))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": f"Generated comparison reasoning for {len(observations)} finding(s).",
        "artifacts": artifacts,
        "errors": errors,
    }


try:
    from docx import Document
except ImportError:
    Document = None

@pipeline_tool("build_comparison_report", domain="comparison")
def build_comparison_report(output_dir: str = None, **kwargs) -> dict:
    """
    Procedural PY/CY Comparison Report Composition Engine.
    Composes a structured DOCX in the firm's audit-reasoning narrative format (Title
    -> Notice to the Reader -> Executive Summary -> 1. Scope & Inputs -> 2. Data
    Quality & Control Totals -> 3. Provisional Materiality (CY basis) -> 4. FSLI
    Summary — Current Year -> 5. Structural Delta -> 6. PY Closing -> CY Opening
    Continuity -> 7. Variance Analysis -> 8. Sign Convention Consistency -> 9. Risk
    Indicators — Current Year -> 10. Sensitive Account Categories — Current Year ->
    11. Focus Areas -> 12. Suggested Audit Focus Sequence -> 13. Limitations &
    No-Opinion Statement), matching the single-TB report's typography and
    Observed/Reasoning/Implication register. Reads the Task 1 comparison artifacts
    (precheck_results.json, structural_delta.json, comparison_variance.parquet,
    sign_convention_flags.json, comparison_reasoning.json) plus, optionally, the
    current-year single-TB run's artifacts (fsli_summary.parquet, materiality.json,
    sensitive_accounts.json, canonical_tb.parquet — located via cy_output_dir, or
    the sibling "cy" directory by default) for the CY-specific sections. Generates
    TB_Comparison_Audit.xlsx and TB_Comparison_Report.docx.
    """
    errors = []
    artifacts = []

    if Document is None:
        errors.append({"type": "ImportError", "message": "python-docx is not installed."})
        return {"execution_status": "FAILED", "errors": errors}

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def load_json(base_dir, filename, required=True):
        p = Path(base_dir) / filename
        if not p.exists():
            if required:
                errors.append({"type": "MissingDataWarning", "message": f"File {filename} not found in {base_dir}."})
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": filename, "error": str(e)})
            return None

    reasoning = load_json(out_dir, "comparison_reasoning.json") or {}
    precheck_data = load_json(out_dir, "precheck_results.json") or {"checks": []}
    structural_data = load_json(out_dir, "structural_delta.json") or {}
    sign_check_data = load_json(out_dir, "sign_convention_flags.json") or {"flags": []}

    variance_rows = []
    var_path = out_dir / "comparison_variance.parquet"
    if var_path.exists():
        try:
            variance_rows = pl.read_parquet(var_path).to_dicts()
        except Exception as e:
            errors.append({"type": "ParseError", "file": "comparison_variance.parquet", "error": str(e)})
    else:
        errors.append({"type": "MissingDataWarning", "message": "comparison_variance.parquet not found."})

    # CY-specific artifacts (FSLI, materiality, sensitive accounts, canonical TB) live in the
    # single-TB CY run's output directory — conventionally the sibling "cy" directory next to
    # this comparison output directory, unless overridden via cy_output_dir.
    cy_dir_override = kwargs.get("cy_output_dir")
    cy_dir = Path(cy_dir_override) if cy_dir_override else (out_dir.parent / "cy")
    if not cy_dir.exists():
        cy_dir = out_dir
    cy_materiality = load_json(cy_dir, "materiality.json", required=False) or {}
    cy_mapping_quality = load_json(cy_dir, "mapping_quality.json", required=False) or {}
    cy_anomaly_data = load_json(cy_dir, "anomaly_findings.json", required=False) or {}
    cy_layer1_results = load_json(cy_dir, "layer1_results.json", required=False) or []
    cy_sensitive = load_json(cy_dir, "sensitive_accounts.json", required=False) or {}
    cy_fsli_df = None
    cy_fsli_path = cy_dir / "fsli_summary.parquet"
    if cy_fsli_path.exists():
        try:
            cy_fsli_df = pl.read_parquet(cy_fsli_path)
        except Exception as e:
            errors.append({"type": "ParseError", "file": "fsli_summary.parquet", "error": str(e)})
    cy_canonical_df = _load_canonical_tb(cy_dir)

    def safe_fmt(val):
        try:
            return f"{float(val):,.0f}"
        except Exception:
            return str(val)

    tb_meta_cy = load_json(cy_dir, "tb_metadata.json", required=False) or {}
    manifest_cy = load_json(cy_dir, "processing_manifest.json", required=False) or {}
    py_dir_override = kwargs.get("py_output_dir")
    py_dir = Path(py_dir_override) if py_dir_override else (out_dir.parent / "py")
    tb_meta_py = load_json(py_dir, "tb_metadata.json", required=False) or {} if py_dir.exists() else {}
    manifest_py = load_json(py_dir, "processing_manifest.json", required=False) or {} if py_dir.exists() else {}
    mapping_py = load_json(py_dir, "mapping_summary.json", required=False) or {} if py_dir.exists() else {}
    # Remark #30 fix: PY now gets the full ~30-rule register too (routes.py's PY-leg
    # branch calls validate_layer2_tb, which patches TB-012/013/016/017/018/020 into
    # this same layer1_results.json in place) -- present both registers side by side
    # rather than CY-only, per the remark's own requested fix.
    py_layer1_results = load_json(py_dir, "layer1_results.json", required=False) or []
    mapping_cy = load_json(cy_dir, "mapping_summary.json", required=False) or {}

    # Remark #21 fix: Sections 6/8/10 were CY-only because these PY artifacts were
    # never loaded here at all -- routes.py's PY-leg branch now produces all of them
    # (build_materiality/build_fsli_summary always did; build_financial_ratios/
    # build_audit_ratio_pack/build_relationship_analytics/build_risk_indicators are
    # new as of this same fix). Mirrors the cy_materiality/cy_fsli_df/cy_canonical_df
    # loads above exactly; each degrades gracefully (empty/None) if PY's artifact is
    # missing, same as every other optional load in this function.
    py_materiality = load_json(py_dir, "materiality.json", required=False) or {}
    py_fsli_df = None
    py_fsli_path = py_dir / "fsli_summary.parquet"
    if py_fsli_path.exists():
        try:
            py_fsli_df = pl.read_parquet(py_fsli_path)
        except Exception as e:
            errors.append({"type": "ParseError", "file": "fsli_summary.parquet (PY)", "error": str(e)})
    py_canonical_df = _load_canonical_tb(py_dir)

    entity_label, _cy_fy, cy_source = resolve_entity_and_fy(tb_meta_cy, manifest_cy)
    _, _, py_source = resolve_entity_and_fy(tb_meta_py, manifest_py)
    stem = Path(cy_source).stem if cy_source else "Unknown Entity"
    cy_fy_match = re.search(r"FY\s*[\d\-/]+", stem, re.IGNORECASE)
    cy_fy_label = cy_fy_match.group(0).upper() if cy_fy_match else "CY"
    py_stem = Path(py_source).stem if py_source else ""
    py_fy_match = re.search(r"FY\s*[\d\-/]+", py_stem, re.IGNORECASE)
    py_fy_label = py_fy_match.group(0).upper() if py_fy_match else "PY"

    generated_at = _dt.datetime.now().strftime("%d-%b-%Y %H:%M")
    header_left = f"Generated: {generated_at}"
    header_right = f"{entity_label} - {py_fy_label} vs {cy_fy_label}"

    doc = Document()
    _apply_report_styles(doc)
    _setup_page_layout___shared(doc, header_left=header_left, header_right=header_right)

    def add_table(headers, rows):
        return _docx_add_table___shared(doc, headers, rows)

    def add_note(text, style="Body Text"):
        _docx_add_note(doc, text, style=style)

    def add_body(text, style="Body Text"):
        doc.add_paragraph(text, style=style)

    def add_bullets(items):
        for it in items:
            doc.add_paragraph(it, style="List Bullet")

    checks = precheck_data.get("checks", [])
    py_ctrl = next((c for c in checks if c.get("check") == "py_debit_credit_control_totals"), None)
    cy_ctrl = next((c for c in checks if c.get("check") == "cy_debit_credit_control_totals"), None)
    continuity_check = next((c for c in checks if c.get("check") == "opening_closing_continuity"), None)

    cy_materiality_thr = cy_materiality.get("thresholds", {})
    om = cy_materiality_thr.get("overall", 0.0)
    perf_mat = cy_materiality_thr.get("performance", 0.0)
    triv = cy_materiality_thr.get("clearly_trivial", 0.0)
    cy_summ = cy_materiality.get("summary", {})
    cy_sens_cat = cy_sensitive.get("category_summary", {})
    cy_susp = cy_sens_cat.get("suspense_control", {})

    cy_conc_rows = _concentration_rows_from_canonical(cy_canonical_df)
    cy_dormant_rows = _dormant_rows_from_canonical(cy_canonical_df)
    py_conc_rows = _concentration_rows_from_canonical(py_canonical_df)
    py_dormant_rows = _dormant_rows_from_canonical(py_canonical_df)

    new_ledgers = structural_data.get("new_ledgers", [])
    removed_ledgers = structural_data.get("removed_ledgers", [])
    py_count = structural_data.get("py_count", 0)
    cy_count = structural_data.get("cy_count", 0)

    # Continuity breaks: run_comparison_prechecks.py already excludes expected P&L resets
    # (bs_pl == "PL" accounts with a nil CY opening balance -- they carry no balance
    # forward by design) from break_count/breaks at the source, so continuity_check's
    # `breaks` list here is already the genuine list -- no re-filtering needed.
    #
    # BUG FIX (comparative-analysis QA review): this used to independently re-derive
    # "P&L or not" by string-matching each break's raw main_head label against the literal
    # strings "revenue"/"expenses" -- but main_head holds sub-category labels ("Wages",
    # "Rent", "Auditor fees", ...), never those literal words, so the exclusion almost
    # never matched here (nor in build_comparison_report_markdown.py's identical copy, nor
    # in build_comparison_reasoning.py's independent HIGH-severity finding, which read the
    # same unfiltered break_count directly). Live verification against a real PY->CY
    # comparison (OVRL FY2024-25 -> FY2025-26) found 29 of 30 breaks were ordinary P&L
    # resets wrongly reported as genuine, producing a false HIGH-severity finding that
    # buried the one real (Equity) break under 29 false positives. Filtering once at the
    # source in run_comparison_prechecks.py (using the canonical bs_pl column directly,
    # not a pattern-matched label) fixes all three consumers at once.
    genuine_breaks = []
    expected_reset = 0
    total_breaks = 0
    if continuity_check:
        expected_reset = continuity_check.get("expected_pl_reset_count", 0)
        total_breaks = continuity_check.get("total_raw_break_count", continuity_check.get("break_count", 0))
        name_map = {}
        ref_df = cy_canonical_df if cy_canonical_df is not None else _load_canonical_tb(py_dir)
        if ref_df is not None and "gl_code" in ref_df.columns and "gl_name" in ref_df.columns:
            name_map = dict(zip(ref_df["gl_code"].cast(pl.Utf8).to_list(), ref_df["gl_name"].to_list()))
        for b in continuity_check.get("breaks", []):
            code = str(b.get("gl_code", ""))
            try:
                diff_val = float(b.get("diff", 0) or 0)
            except (TypeError, ValueError):
                diff_val = 0.0
            genuine_breaks.append({
                "gl_code": code, "gl_name": name_map.get(code, ""),
                "py_closing": b.get("closing_balance"), "cy_opening": b.get("opening_balance"),
                "diff": diff_val,
            })
        genuine_breaks.sort(key=lambda x: abs(x["diff"]), reverse=True)

    # "moved by more than twice overall materiality" == CRITICAL; "moved by more than the
    # threshold alone" == HIGH (>=1x overall) -- the new MEDIUM tier (>=performance
    # materiality, a lower bar than overall) is more granular than either of the two old
    # buckets and isn't folded into this narrative's two-tier framing.
    variance_flagged = [r for r in variance_rows if r.get("flag") not in (None, "NO_CHANGE")]
    high_priority = [r for r in variance_flagged if r.get("flag") == "CRITICAL"]
    medium_variance = [r for r in variance_flagged if r.get("flag") == "HIGH"]
    top_movements = sorted(variance_flagged, key=lambda r: abs(r.get("variance") or 0), reverse=True)[:10]

    flags = sign_check_data.get("flags", [])

    # -------------------------------------------------------------------------
    # Title + Notice to the Reader
    # -------------------------------------------------------------------------
    def _render_title_and_notice():
        doc.add_heading(f"Trial Balance - ({entity_label}) — PY vs CY Comparative Analysis", 0)
        _docx_add_subtitle(doc, f"{py_fy_label}  →  {cy_fy_label}")
        _docx_add_notice(
            doc,
            "Notice to the reader: this document is a mechanical, evidence-based analytical review "
            "derived solely from the two trial balance files and the GL grouping workbook supplied. No "
            "external documents, confirmations, or entity knowledge were used. It is a risk-assessment "
            "and planning aid only — it contains no audit opinion and no conclusion on misstatement, "
            "fraud, non-compliance, recoverability, or going concern. All figures are shown as supplied, "
            "unscaled and in the original currency; scale (units/thousands/lakh/crore) was not stated in "
            "the source files."
        )
        add_body("Scope and limitations of this tool, applying throughout: " + " · ".join(SCOPE_LIMITATIONS_BRIEF))

    _render_title_and_notice()

    # -------------------------------------------------------------------------
    # Executive Summary
    # -------------------------------------------------------------------------
    # An empty manifest until the workbook below writes the real one. Sections 4, 5, 9
    # and 11 render before the XLSX half runs, so they carry no inline schedule
    # reference in the comparative report; section 25's appendix -- rendered after the
    # manifest exists -- names every sheet without exception, which is what the
    # cross-referencing guarantee requires.
    manifest = {"workbook": None, "sheet_count": 0, "sheets": [], "unregistered_sheets": []}

    def _render_executive_summary():
        doc.add_heading("1. Executive Audit Dashboard", level=1)
        if py_count and cy_count:
            add_body(
                f"The {entity_label} trial balance {'grew' if cy_count >= py_count else 'reduced'} from "
                f"{py_count} GL accounts in {py_fy_label} to {cy_count} in {cy_fy_label}. Both years are "
                + ("internally self-balanced — debit and credit control totals match exactly in each "
                   "period — so the mapping and comparison analytics below rest on a sound base rather "
                   "than a data-quality artefact."
                   if (py_ctrl and py_ctrl.get("status") == "PASS" and cy_ctrl and cy_ctrl.get("status") == "PASS")
                   else "compared below; see Section 2 for the detailed control-total position.")
            )
        # remark #19 fix: this list must mirror each bullet's own render condition below,
        # not a different variable -- the Continuity bullet renders under `if total_breaks:`
        # (a superset including expected Revenue/Expense resets), so counting by
        # `genuine_breaks` (a strict subset) could read 0 while the bullet still renders,
        # producing a stated count that doesn't match the bullet list.
        highlights = [new_ledgers or removed_ledgers, total_breaks, high_priority or medium_variance, flags]
        highlight_count = sum(1 for x in highlights if x)
        add_body(f"Across the two periods, {_NUMBER_WORDS.get(highlight_count, str(highlight_count)).lower()} things stand out:" if highlight_count else "Key observations:")
        if new_ledgers or removed_ledgers:
            _docx_add_lead_bullet(
                doc, "Structural change",
                f"{len(new_ledgers)} account(s) appear only in {cy_fy_label} and {len(removed_ledgers)} "
                f"only in {py_fy_label} — a {len(new_ledgers) + len(removed_ledgers)}-account "
                f"chart-of-accounts change against a base of roughly {py_count or cy_count} (Section 5)."
            )
        if total_breaks:
            _docx_add_lead_bullet(
                doc, "Continuity",
                f"{expected_reset} of {total_breaks} PY-closing-to-CY-opening differences are "
                "Revenue/Expense accounts resetting to nil, which is expected and not a control "
                f"weakness. {len(genuine_breaks)} account(s) carry a genuine, unexplained discontinuity "
                "(Section 6)." if genuine_breaks else
                f"All {total_breaks} PY-closing-to-CY-opening differences are Revenue/Expense accounts "
                "resetting to nil, which is expected and not a control weakness (Section 6)."
            )
        if high_priority or medium_variance:
            _docx_add_lead_bullet(
                doc, "Material movements",
                f"{len(high_priority)} account(s) moved by more than twice the overall materiality "
                f"threshold and a further {len(medium_variance)} moved by more than the threshold alone "
                "— each large enough to corroborate against its underlying schedule (Section 7)."
            )
        if flags:
            _docx_add_lead_bullet(
                doc, "Sign convention",
                "sign-convention screens were run for both periods; see Section 8 for the detailed "
                "per-year comparison — this is a broad screen, not a specific finding."
            )

        if not any((new_ledgers, removed_ledgers, total_breaks, high_priority, medium_variance, flags)):
            add_bullets(["No high-priority focus areas were flagged deterministically in this dataset."])

    _render_executive_summary()

    # -------------------------------------------------------------------------
    # 1. Scope & Inputs
    # -------------------------------------------------------------------------
    def _render_scope_and_inputs():
        doc.add_heading("2. Scope, Inputs and Limitations", level=1)
        scope_rows = [
            ("Prior Year TB", py_source, str(mapping_py.get("total_tb_rows", py_count or "-")),
             str(mapping_py.get("mapped_rows", "-")), str(mapping_py.get("unmapped_rows", "-"))),
            ("Current Year TB", cy_source, str(mapping_cy.get("total_tb_rows", cy_count or "-")),
             str(mapping_cy.get("mapped_rows", "-")), str(mapping_cy.get("unmapped_rows", "-"))),
        ]
        add_table(["Input", "File", "Rows", "Accounts Mapped", "Unmapped"], scope_rows)
        add_body(
            "Grouping structure: FS Head (Assets/Liabilities/Equity/Revenue/Expenses) was derived by "
            "mapping the supplied grouping workbook's hierarchy to its statement classification, "
            "applied consistently across both periods."
        )

    _render_scope_and_inputs()

    # -------------------------------------------------------------------------
    # 2. Data Quality & Control Totals
    # -------------------------------------------------------------------------
    def _render_tb_integrity():
        doc.add_heading("3. TB Integrity and Reconciliation", level=1)
        if py_ctrl or cy_ctrl:
            add_table(
                ["Check", f"{py_fy_label} (PY)", f"{cy_fy_label} (CY)"],
                [
                    ("Total Debit", safe_fmt((py_ctrl or {}).get("total_debit", 0)), safe_fmt((cy_ctrl or {}).get("total_debit", 0))),
                    ("Total Credit", safe_fmt((py_ctrl or {}).get("total_credit", 0)), safe_fmt((cy_ctrl or {}).get("total_credit", 0))),
                    ("Debit − Credit Difference", safe_fmt((py_ctrl or {}).get("diff", 0)), safe_fmt((cy_ctrl or {}).get("diff", 0))),
                ],
            )
            both_pass = (py_ctrl or {}).get("status") == "PASS" and (cy_ctrl or {}).get("status") == "PASS"
            add_body(
                "Observed: debit and credit control totals "
                + ("match exactly in both periods (zero difference)." if both_pass else
                   "do not match exactly in one or both periods — see the status flags above.")
            )
            add_body(
                "Implication: the underlying trial balance extracts are internally consistent before any "
                "grouping logic is applied. This is a necessary precondition for every analytic that "
                "follows — it does not by itself confirm the TB agrees to the general ledger or statutory "
                "accounts, only that the files as supplied are arithmetically sound."
            )
        else:
            add_body("Control totals could not be computed — precheck_results.json was not available.")

    _render_tb_integrity()

    # -------------------------------------------------------------------------
    # 6. PY Closing → CY Opening Continuity
    # -------------------------------------------------------------------------
    def _render_continuity_section():
        doc.add_heading("PY closing to CY opening continuity", level=2)
        if continuity_check:
            add_body(
                f"Observed: {total_breaks} account(s) show a difference between PY closing balance and "
                "CY opening balance beyond a rounding tolerance."
            )
            add_body(
                f"Difference decomposed: {expected_reset} of these {total_breaks} breaks are Revenue and "
                "Expense accounts whose opening balance is nil in both periods — this is the expected "
                "behaviour for P&L accounts, which reset to zero at the start of every financial year "
                "rather than carrying forward a balance, so these do not indicate a continuity problem."
                + (f" Removing them leaves {len(genuine_breaks)} account(s) with a genuine "
                   "opening-to-closing mismatch:" if genuine_breaks else " No genuine continuity breaks remain after removing them.")
            )
            if genuine_breaks:
                add_table(
                    ["GL Code", "GL Name", "PY Closing", "CY Opening", "Difference"],
                    [(b["gl_code"], b["gl_name"], safe_fmt(b["py_closing"]), safe_fmt(b["cy_opening"]), safe_fmt(b["diff"])) for b in genuine_breaks[:10]],
                )
                add_body(
                    "Audit concern: a genuine PY-closing-to-CY-opening mismatch outside the P&L reset "
                    "pattern is the single most consequential item this comparison can flag, since it sits "
                    "directly in opening equity or another carry-forward balance — a common reference "
                    "point for both the balance sheet and the statement of changes in equity."
                )
                add_body(
                    "Recommended procedure: obtain the entity's roll-forward schedule for each flagged "
                    "account (opening balance, current-year movement, any restatement, closing balance) "
                    "and agree the difference to it before relying on the CY opening position."
                )
        else:
            add_body("Continuity check data was not available for this comparison run.")

    _render_continuity_section()

    # -------------------------------------------------------------------------
    # 8. Sign Convention Consistency
    # -------------------------------------------------------------------------
    def _render_sign_convention_narrative():
        doc.add_heading("Sign convention consistency", level=2)
        if flags:
            add_table(
                ["Period", "Status", "Detail"],
                [(f.get("year", ""), f.get("status", ""),
                  f"debit anchors {f.get('debit_anchors', '-')} ({f.get('debit_positive_pct', '-')}% positive), "
                  f"credit anchors {f.get('credit_anchors', '-')} ({f.get('credit_negative_pct', '-')}% negative)")
                 for f in flags],
            )
            add_body(
                "Reasoning: this is a broad screen for mixed sign conventions in the source file, run "
                "independently for each period. A stable rate across both periods is more consistent with "
                "a structural feature of the chart of accounts than with a new classification problem "
                "introduced in the current year."
            )
            add_body(
                "Implication: each flagged account should be checked against its expected nature before "
                "being treated as a misclassification — this is a screen, not a defect finding."
            )
        else:
            add_body("No sign convention flags available.")
        _write_front_sections(doc, cy_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(4, 5))

    _render_sign_convention_narrative()

    # -------------------------------------------------------------------------
    # 3. Provisional Materiality (CY basis)
    # -------------------------------------------------------------------------
    def _render_materiality_narrative():
        doc.add_heading("6. Materiality Assessment", level=1)
        py_materiality_thr = py_materiality.get("thresholds", {})
        py_om = py_materiality_thr.get("overall", 0.0)
        py_perf_mat = py_materiality_thr.get("performance", 0.0)
        py_triv = py_materiality_thr.get("clearly_trivial", 0.0)
        py_summ = py_materiality.get("summary", {})
        if cy_materiality or py_materiality:
            add_table(
                ["Basis", f"{py_fy_label} (PY)", f"{cy_fy_label} (CY)"],
                [
                    ("Materiality Basis Used", py_summ.get("benchmark_used", "Unknown") if py_materiality else "—",
                     cy_summ.get("benchmark_used", "Unknown") if cy_materiality else "—"),
                    ("Overall Materiality", safe_fmt(py_om) if py_materiality else "—", safe_fmt(om) if cy_materiality else "—"),
                    ("Performance Materiality (75% of OM)", safe_fmt(py_perf_mat) if py_materiality else "—",
                     safe_fmt(perf_mat) if cy_materiality else "—"),
                    ("Clearly Trivial Threshold", safe_fmt(py_triv) if py_materiality else "—", safe_fmt(triv) if cy_materiality else "—"),
                ],
            )
            if om and py_om:
                common_size_note = (
                    f"CY overall materiality is {om / py_om * 100:.1f}% of PY's, in absolute terms — "
                    "each period's threshold is set from that period's own benchmark, so this ratio "
                    "reflects benchmark-base movement, not a like-for-like tightening or loosening."
                )
                add_body(common_size_note)
            add_note(
                "Provisional only — a mechanical benchmark-based calculation on each period's own trial "
                "balance. Not a substitute for engagement-level materiality judgment.",
                style="Normal",
            )
        else:
            add_body("Materiality was not available for either period in this comparison run.")

    _render_materiality_narrative()

    # -------------------------------------------------------------------------
    # 5. Structural Delta — PY vs CY Ledger Composition
    # -------------------------------------------------------------------------
    def _render_structural_delta():
        doc.add_heading("7. Comparative Analytical Review", level=1)
        if structural_data:
            add_body(structural_data.get(
                "message", f"{py_fy_label} ledgers: {py_count}, {cy_fy_label} ledgers: {cy_count}."
            ))
            if new_ledgers or removed_ledgers:
                total_ledgers = py_count or cy_count or (len(new_ledgers) + len(removed_ledgers))
                churn_pct = (
                    100.0 * (len(new_ledgers) + len(removed_ledgers)) / total_ledgers
                    if total_ledgers else 0.0
                )
                add_body(
                    f"Chart-of-accounts change: {len(new_ledgers)} account(s) added and "
                    f"{len(removed_ledgers)} account(s) removed, {churn_pct:.1f}% of the "
                    f"{total_ledgers}-account population. Obtain the approved chart-of-accounts "
                    "change log (if one exists) and agree the additions/deletions to it; the "
                    "choice of procedure beyond that is for the audit team to determine."
                )
                bank_new = [g for g in new_ledgers if is_bank_cash_named(g.get("gl_name"))]
                bank_removed = [g for g in removed_ledgers if is_bank_cash_named(g.get("gl_name"))]
                if bank_new or bank_removed:
                    add_body(
                        f"Of the above, {len(bank_new)} added and {len(bank_removed)} removed "
                        "account(s) are bank/cash-named — new or removed bank/cash ledgers warrant "
                        "separate enquiry, not blanket confirmation."
                    )
            if new_ledgers:
                doc.add_heading(f"Sample of accounts added in {cy_fy_label} (not present in {py_fy_label})", level=2)
                add_table(["GL Code", "GL Name"], [(g.get("gl_code", ""), g.get("gl_name", "")) for g in new_ledgers[:15]])
            if removed_ledgers:
                doc.add_heading(f"Sample of accounts removed from {py_fy_label} (not present in {cy_fy_label})", level=2)
                add_table(["GL Code", "GL Name"], [(g.get("gl_code", ""), g.get("gl_name", "")) for g in removed_ledgers[:15]])
            add_note(
                "Full lists of added/removed ledgers are provided in the accompanying Excel workbook "
                "(sheets \"New Ledgers (CY)\" / \"Removed Ledgers (PY)\")."
            )
        else:
            add_body("No structural delta data available.")

    _render_structural_delta()

    # -------------------------------------------------------------------------
    # 7. Variance Analysis — PY vs CY Closing Balance
    # -------------------------------------------------------------------------
    def _render_variance_analysis_narrative():
        doc.add_heading("Variance analysis — PY vs CY closing balance", level=2)
        if variance_rows:
            add_body(
                f"Observed: variance is computed for the {len(variance_rows)} account(s) present in "
                f"either period and flagged against materiality. {len(high_priority)} account(s) moved "
                f"by more than twice overall materiality and a further {len(medium_variance)} moved by "
                "more than the threshold alone."
            )
            flag_counts = {}
            for r in variance_rows:
                flag_counts[r.get("flag", "UNKNOWN")] = flag_counts.get(r.get("flag", "UNKNOWN"), 0) + 1
            add_table(
                ["Flag", "Account Count"],
                [(flag, str(count)) for flag, count in sorted(flag_counts.items(), key=lambda x: -x[1])],
            )
            if flag_counts.get("BALANCE_APPEARED") or flag_counts.get("BALANCE_DISAPPEARED"):
                add_note(
                    "BALANCE_APPEARED/BALANCE_DISAPPEARED above are distinct from the chart-of-account "
                    "additions/removals reported in the paragraph above this section: these flag accounts "
                    "that remained on the chart of accounts in both years but whose closing balance moved "
                    "to/from nil (e.g. a dormant or fully-cleared ledger) — not a genuine ledger addition "
                    "or deletion.",
                    style="Normal",
                )
            if top_movements:
                doc.add_heading("Top Material Movements (by absolute variance)", level=2)
                add_table(
                    ["GL Code", "GL Name", "PY Closing", "CY Closing", "Variance", "Flag"],
                    [(r.get("gl_code", ""), r.get("gl_name", ""), safe_fmt(r.get("py_closing", 0)),
                      safe_fmt(r.get("cy_closing", 0)), safe_fmt(r.get("variance", 0)), r.get("flag", "")) for r in top_movements],
                )
                add_body(
                    "Implication: none of these, on the trial balance alone, can be confirmed as correctly "
                    "stated — only that each has a plausible operational story worth corroborating against "
                    "its underlying schedule."
                )
                add_body(
                    "Recommended procedure: obtain management's explanation and the supporting schedule "
                    "for each CRITICAL-flagged account above before relying on the movement."
                )
        else:
            add_body("No variance data available.")

    _render_variance_analysis_narrative()

    # -------------------------------------------------------------------------
    # 4. FSLI Summary — Current Year
    # -------------------------------------------------------------------------
    def _fsli_head_level_rows(df):
        if df is None or df.is_empty():
            return {}
        lvl1 = df.filter(pl.col("hierarchy_level") == 1) if "hierarchy_level" in df.columns else df
        out = {}
        for r in lvl1.iter_rows(named=True):
            head = r.get("main_head") or r.get("fs_head") or r.get("line_item") or ""
            out[head] = {
                "gl_count": int(r.get("descendant_gl_count") or r.get("leaf_gl_count") or r.get("gl_accounts") or 0),
                "closing_balance": float(r.get("closing_balance", 0.0)),
            }
        return out

    def _render_fsli_narrative():
        doc.add_heading("8. FSLI and Ledger-Level Movements", level=1)
        cy_heads = _fsli_head_level_rows(cy_fsli_df)
        py_heads = _fsli_head_level_rows(py_fsli_df)
        if cy_heads or py_heads:
            all_heads = sorted(set(cy_heads) | set(py_heads),
                                key=lambda h: abs(cy_heads.get(h, py_heads.get(h, {})).get("closing_balance", 0.0)),
                                reverse=True)
            fsli_rows = []
            for head in all_heads:
                cy_bal = cy_heads.get(head, {}).get("closing_balance")
                py_bal = py_heads.get(head, {}).get("closing_balance")
                variance = (cy_bal - py_bal) if (cy_bal is not None and py_bal is not None) else None
                fsli_rows.append((
                    head,
                    cy_heads.get(head, {}).get("gl_count", "—"),
                    safe_fmt(py_bal) if py_bal is not None else "—",
                    safe_fmt(cy_bal) if cy_bal is not None else "—",
                    safe_fmt(variance) if variance is not None else "—",
                ))
            add_table(["FS Head", "GL Accounts (CY)", f"Closing Balance ({py_fy_label})",
                       f"Closing Balance ({cy_fy_label})", "Variance"], fsli_rows)
        else:
            add_body("FSLI summary was not available for either period in this comparison run.")
        _write_front_sections(doc, cy_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(9,))

    _render_fsli_narrative()

    # -------------------------------------------------------------------------
    # 9. Risk Indicators — Current Year
    # -------------------------------------------------------------------------
    def _render_risk_indicators_narrative():
        doc.add_heading("10. Account-Risk Scoring", level=1)
        add_body(
            f"Concentration and dormancy are computed independently for each period ({py_fy_label} "
            f"and {cy_fy_label}): {len(py_conc_rows)} vs {len(cy_conc_rows)} concentration account(s), "
            f"{len(py_dormant_rows)} vs {len(cy_dormant_rows)} dormant account(s)."
        )
        doc.add_heading(f"Concentration — accounts exceeding 5% of their FS Head ({cy_fy_label})", level=2)
        if cy_conc_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "% of FS Head"],
                [(c["gl_code"], c["gl_name"], c["fs_head"], safe_fmt(c["closing"]), f"{c['pct']:.1f}%") for c in cy_conc_rows],
            )
        else:
            add_body(f"No account individually exceeds 5% of its FS Head in the {cy_fy_label} dataset.")
        doc.add_heading(f"Concentration — accounts exceeding 5% of their FS Head ({py_fy_label})", level=2)
        if py_conc_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "% of FS Head"],
                [(c["gl_code"], c["gl_name"], c["fs_head"], safe_fmt(c["closing"]), f"{c['pct']:.1f}%") for c in py_conc_rows],
            )
        else:
            add_body(f"No account individually exceeds 5% of its FS Head in the {py_fy_label} dataset.")
        doc.add_heading(f"Dormant accounts (no debit/credit movement in year) with material closing balance ({cy_fy_label})", level=2)
        if cy_dormant_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                [(d["gl_code"], d["gl_name"], d["fs_head"], safe_fmt(d["closing"])) for d in cy_dormant_rows],
            )
        else:
            add_body(f"No dormant accounts with a material closing balance were identified in the {cy_fy_label} dataset.")
        doc.add_heading(f"Dormant accounts (no debit/credit movement in year) with material closing balance ({py_fy_label})", level=2)
        if py_dormant_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                [(d["gl_code"], d["gl_name"], d["fs_head"], safe_fmt(d["closing"])) for d in py_dormant_rows],
            )
        else:
            add_body(f"No dormant accounts with a material closing balance were identified in the {py_fy_label} dataset.")
        _write_front_sections(doc, cy_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(11,))

    _render_risk_indicators_narrative()

    # Narrated comparison observations, read once. Sections 17 and 22 both consume
    # this, so it is assigned in the enclosing scope rather than inside either closure.
    observations = reasoning.get("observations", [])

    # Rendered inside sections 12, 17 and 22 by _write_phase4_sections. Passed as
    # closures rather than moved, because each reads this function's own locals.
    def _render_sensitive_categories():

        # -------------------------------------------------------------------------
        # 10. Sensitive Account Categories — Current Year
        # -------------------------------------------------------------------------
        doc.add_heading("Sensitive account categories (Current Year)", level=2)
        if cy_sens_cat:
            sens_rows = [(k.replace("_", " "), str(v.get("count", 0)), safe_fmt(v.get("total_balance", 0))) for k, v in cy_sens_cat.items()]
            add_table(["Category", "Accounts", "Combined Closing Balance"], sens_rows)
            add_body(
                "These categories are name-pattern screens over the CY account list, not confirmed "
                "classifications, and each should be verified individually rather than relied upon as a "
                "complete or precise population."
            )
            if cy_susp.get("count"):
                add_body(
                    f"The suspense/control tag carries {safe_fmt(cy_susp.get('total_balance', 0))} "
                    "combined exposure and is the category most worth reconciling first."
                )
        else:
            add_body("Current-year sensitive-account categories were not available for this comparison run.")

    def _render_focus_areas():

        # -------------------------------------------------------------------------
        # 11. Focus Areas
        # -------------------------------------------------------------------------
        doc.add_heading("Narrated focus areas", level=2)
        # Capped to the top 3 -- this is full narrative per item, the most page-expensive
        # content in the report; the Exception Register sheet holds the complete population.
        _FOCUS_AREA_CAP = 3
        if observations:
            shown = observations[:_FOCUS_AREA_CAP]
            for i, obs_wrap in enumerate(shown, start=1):
                obs = obs_wrap.get("observation", obs_wrap) if isinstance(obs_wrap, dict) else {}
                sev = str(obs.get("priority", "LOW")).upper()
                title = obs.get("title", "Comparison Finding")
                _docx_add_severity_heading___shared(doc, f"F{i:02d}", sev, title, level=2)
                add_body(_brief(obs.get("detailed_observation", obs.get("executive_summary", "")), 220))
                assertions = obs.get("affected_assertions", [])
                procs = obs.get("recommended_procedures", [])
                add_body(
                    f"{', '.join(assertions)} · Risk: {sev} · "
                    f"Evidence: {_brief(', '.join(procs) if procs else 'reconciliation, ledger dump', 80)}",
                    style="Normal",
                )
            if len(observations) > _FOCUS_AREA_CAP:
                add_body(
                    f"Showing the top {_FOCUS_AREA_CAP} of {len(observations)} observations; see the "
                    "Exception Register sheet for the complete list.",
                    style="Normal",
                )
        else:
            add_body("No comparison observations were generated for this dataset.")

    def _render_focus_sequence():

        # -------------------------------------------------------------------------
        # 12. Suggested Audit Focus Sequence
        # -------------------------------------------------------------------------
        doc.add_heading("Suggested focus sequence", level=2)
        add_note("Priority order for planning, driven by risk-ranked findings across both periods.")
        eligible = []
        for obs_wrap in observations:
            obs = obs_wrap.get("observation", obs_wrap) if isinstance(obs_wrap, dict) else {}
            if str(obs.get("priority", "LOW")).upper() in ("CRITICAL", "HIGH", "MEDIUM"):
                eligible.append(obs)
        for obs in eligible[:_SECTION_CAP]:
            sev = str(obs.get("priority", "LOW")).upper()
            name = obs.get("title", "")
            reason = obs.get("executive_summary", "")
            doc.add_paragraph(f"{name} — {sev}-rated finding: {_brief(reason, 100)}", style="List Bullet")
        if not eligible:
            add_body("No High/Medium-rated findings to sequence for audit focus.")
        elif len(eligible) > _SECTION_CAP:
            add_note(f"Showing the top {_SECTION_CAP} of {len(eligible)} — see the Exception Register sheet for the complete, risk-ranked list.")


    # The Word document is saved AFTER the workbook below, not here: sections 12-25
    # cross-reference the schedule manifest, and the manifest does not exist until
    # build_comparison_report's XLSX half has written it. python-docx holds the
    # document in memory until save(), so deferring costs nothing.
    docx_path = out_dir / "TB_Comparison_Report.docx"

    # -------------------------------------------------------------------------
    # XLSX — Comparative TB Review (12 sheets), matching TB_Comparative_Review.xlsx
    # -------------------------------------------------------------------------
    excel_path = out_dir / "TB_Comparison_Audit.xlsx"

    try:
        cy_canonical_path = cy_dir / "canonical_tb.parquet"
        py_canonical_path = py_dir / "canonical_tb.parquet"
        if not cy_canonical_path.exists() or not py_canonical_path.exists():
            missing = cy_canonical_path if not cy_canonical_path.exists() else py_canonical_path
            errors.append({"type": "FileNotFoundError", "file": str(missing)})
            return {"execution_status": "FAILED", "errors": errors, "message": "canonical_tb.parquet not found for CY/PY."}

        # Canonical TB (CY)/(PY) sheets show raw joined data, not a computed analytic --
        # _load_canonical_tb_for_report's report_fs_head/report_fsli/report_note labels
        # are the right fit for a full-TB row dump and stay as-is.
        cy = _load_canonical_tb_for_report(cy_canonical_path)
        py = _load_canonical_tb_for_report(py_canonical_path)

        # BUG FIX (comparative-analysis QA review, follow-up): every analytic below used
        # to be independently recomputed here via backend/tools/comparison/_shared.py's
        # private _compute_* engine (11 functions) -- a parallel implementation to the one
        # feeding the DOCX/Markdown report (spine artifacts already loaded earlier in this
        # function: cy_materiality, cy_sensitive, cy_fsli_df, precheck_data,
        # structural_data, variance_rows, genuine_breaks) and, for concentration/dormant/
        # sign-convention, to the already-fixed canonical functions in the MAIN
        # backend/tools/_shared.py (TB-R13/R14/R15). Two non-communicating
        # implementations of the same concept is exactly the TB-R06 pattern from the
        # original defect registry -- confirmed concretely for Variance (Excel showed
        # CRITICAL via _movement_flag_for_report while the report showed NO_THRESHOLD via
        # a completely different, broken vocabulary in run_comparison_variance.py) and for
        # Continuity Breaks (_compute_continuity_breaks had ZERO P&L-reset exclusion, so
        # the Excel "Continuity Breaks" tab never got last session's fix at all). Every
        # sheet below now reads the same spine data the DOCX/Markdown report reads, or
        # calls the same already-fixed canonical functions single-TB reports use.
        cy_classified = load_canonical_tb(cy_canonical_path)
        py_classified = load_canonical_tb(py_canonical_path)

        dq_cy = control_totals(cy_classified) or {}
        dq_py = control_totals(py_classified) or {}

        # om/perf_mat/triv already resolved from cy_materiality_thr earlier in this
        # function for the DOCX section -- reused here, not recomputed.
        conc_rows_xlsx = concentration_rows_from_canonical(cy_classified, clearly_trivial=triv)
        dormant_rows_xlsx = dormant_rows_from_canonical(cy_classified)
        sign_stats_cy = sign_convention_stats(cy_classified) or {}
        sign_stats_py = sign_convention_stats(py_classified) or {}

        sensitive_df_xlsx = None
        sensitive_pq_path = cy_dir / "sensitive_accounts.parquet"
        if sensitive_pq_path.exists():
            try:
                sensitive_df_xlsx = pl.read_parquet(sensitive_pq_path)
            except Exception as e:
                errors.append({"type": "ParseError", "file": "sensitive_accounts.parquet", "error": str(e)})

        # Display-only lookups (gl_code -> report head/name/closing) for spine records
        # that carry only gl_code/gl_name (structural_data's new/removed ledgers) or no FS
        # classification at all (variance_rows) -- not a recomputation of any analytic,
        # just labeling already-computed rows for display.
        head_by_code, closing_by_code = {}, {}
        if cy_classified is not None and "gl_code" in cy_classified.columns:
            head_by_code.update(dict(zip(cy_classified["gl_code"].cast(pl.Utf8).to_list(), cy_classified["report_head"].to_list())))
            closing_by_code.update(dict(zip(cy_classified["gl_code"].cast(pl.Utf8).to_list(), cy_classified["closing_balance"].to_list())))
        py_head_by_code, py_closing_by_code = {}, {}
        if py_classified is not None and "gl_code" in py_classified.columns:
            py_head_by_code.update(dict(zip(py_classified["gl_code"].cast(pl.Utf8).to_list(), py_classified["report_head"].to_list())))
            py_closing_by_code.update(dict(zip(py_classified["gl_code"].cast(pl.Utf8).to_list(), py_classified["closing_balance"].to_list())))

        cy_full_fy = _fy_label(cy_dir / "tb_metadata.json", cy_fy_label)
        py_full_fy = _fy_label(py_dir / "tb_metadata.json", py_fy_label)

        def _short_fy(label):
            m = re.search(r"(\d{2})\D*$", label)
            return f"FY{m.group(1)}" if m else label

        py_label_short = _short_fy(py_full_fy)
        cy_label_short = _short_fy(cy_full_fy)

        registry_problems = validate_registry()

        if registry_problems:

            errors.append({"type": "ScheduleRegistryError", "problems": registry_problems})

            return {"execution_status": "FAILED", "errors": errors,

                    "message": f"Schedule registry invalid: {registry_problems}"}


        wb = Workbook()

        wb.remove(wb.active)


        # Every sheet is created through this helper so the manifest cannot fall out

        # of step with the workbook -- the Word document cross-references the

        # manifest, so a sheet created directly would exist but never be cited.

        sheets_written = []


        def new_sheet(name: str):

            if name not in SCHEDULES:

                raise KeyError(

                    f"Sheet {name!r} is not in backend/tools/_report_schedules.py::SCHEDULES. "

                    "Add it there so the Word document can reference it."

                )

            sheets_written.append(name)

            return wb.create_sheet(name)

        # 0. Engagement -- identity first, same block as the single-TB workbook but
        # labelled as a comparative review and carrying both periods. Read from the CY
        # run directory; each artifact is optional and a missing one degrades one row.
        def _load_cy(name):
            pth = cy_dir / name
            return json.loads(pth.read_text(encoding="utf-8")) if pth.exists() else {}

        ws = new_sheet("Engagement")
        _write_engagement_sheet(
            ws,
            _load_cy("tb_metadata.json"),
            _load_cy("engagement_context.json"),
            _load_cy("normalisation_note.json"),
            _load_cy("data_sufficiency.json"),
            mode_label="Comparative TB Review",
            period_label=f"{py_full_fy} (prior) vs {cy_full_fy} (current)",
            account_count=len(cy_classified) if cy_classified is not None else 0,
        )
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 26, "B": 74})

        # 1. Data Quality
        def _write_data_quality_sheet():
            ws = new_sheet("Data Quality")
            _write_sheet_title(ws, "Data Quality & Control Totals",
                                "Layer-1 style checks on the raw TB files, prior to any grouping join", 5)
            dq_keys = [
                ("Total Debit (closing-balance foot)", "total_debit"),
                ("Total Credit (closing-balance foot)", "total_credit"),
                ("Debit − Credit Difference", "difference"), ("Sum of Closing Balances (tie-out)", "sum_closing"),
                ("Turnover — Debit reporting period (Dr rept. period)", "turnover_total_debit"),
                ("Turnover — Credit reporting period (Cr rept. period)", "turnover_total_credit"),
                ("Duplicate GL Codes", "duplicate_gl_codes"), ("Fully Zero Rows", "fully_zero_rows"),
            ]
            dq_table_rows = [[label, dq_py.get(key, 0), dq_cy.get(key, 0)] for label, key in dq_keys]
            next_row = _write_table(ws, 4, ["Check", f"{py_label_short} (PY)", f"{cy_label_short} (CY)"],
                         dq_table_rows, formats=[None, _ACC_FMT, _ACC_FMT])
            next_row = _write_rule_register_table(ws, next_row + 2, cy_layer1_results, title="Rule Register — Every TB-0xx Check (CY)")
            if py_layer1_results:
                _write_rule_register_table(ws, next_row + 2, py_layer1_results, title="Rule Register — Every TB-0xx Check (PY)")
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 12, "B": 40, "C": 12, "D": 16, "E": 60})

        _write_data_quality_sheet()

        # 2. Materiality (CY basis) -- reads cy_materiality (materiality.json), already
        # loaded for the DOCX section, instead of recomputing.
        def _write_materiality_sheet():
            ws = new_sheet("Materiality")
            _write_sheet_title(ws, "Provisional Planning Materiality",
                                "CY figures, from the CY single-TB run. Not a substitute for engagement-level judgment.", 3)
            mat_rows = [
                ("Materiality Basis Used", cy_summ.get("benchmark_used", "Unknown")),
                ("Overall Materiality", om),
                ("Performance Materiality (75% of OM)", perf_mat),
                ("Clearly Trivial Threshold", triv),
            ]
            next_row = _write_table(ws, 4, ["Basis", "Value"], mat_rows, formats=[None, _ACC_FMT])
            _write_materiality_basis_table(ws, next_row + 2, cy_materiality, mapping_quality=cy_mapping_quality, anomaly_data=cy_anomaly_data)
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 38, "B": 22, "C": 16, "D": 22, "E": 12, "F": 12, "G": 12, "H": 12})

        _write_materiality_sheet()

        # 2b. Materiality Workings -- same sheet build_excel_report writes for the
        # single-TB workbook, on the CY materiality already loaded above. Without this
        # the comparative workbook stated a threshold with no way to re-perform it,
        # while the single-TB workbook (added in the same change) did.
        ws = new_sheet("Materiality Workings")
        _write_materiality_workings_sheet(ws, cy_materiality)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 7, "B": 42, "C": 20, "D": 62, "E": 14, "F": 13, "G": 60})

        # 2c. Benchmark Build-Up -- which CY balances sum into each benchmark. Reads
        # cy_dir directly since the build-up is derived from cy_dir/snapshot_drilldown.parquet,
        # not from any value already loaded into this function.
        ws = new_sheet("Benchmark Build-Up")
        _write_benchmark_buildup_sheet(ws, cy_dir, cy_materiality)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 46, "B": 24, "C": 66})

        # 3. Canonical TB (CY) / (PY) -- FSLI Summary's replacement: every GL row plus
        # native Excel SUBTOTAL() rows and row-group outlining at FS-Head/FSLI level,
        # computed by Excel itself over these same visible rows (see
        # _write_grouped_canonical_sheet). Both periods carry the same grouping so a
        # reviewer can collapse either sheet to the old FSLI Summary's exact view.
        def _as_business_area(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return v

        def _write_canonical_sheet(sheet_name, df_period, period_label):
            # Through new_sheet, not wb.create_sheet: a sheet created directly would
            # exist in the workbook but never enter the manifest, and so never be
            # referenced from the Word document. The cross-reference test caught
            # exactly that for these two sheets.
            ws = new_sheet(sheet_name)
            _write_sheet_title(ws, f"Canonical Trial Balance — {period_label}",
                                "Every ledger account, joined to grouping ground truth. Collapse the "
                                "row-group outline (top-left +/- buttons) for FS-Head/FSLI subtotals.", 9)
            df_display = df_period
            if "business area" in df_display.columns:
                df_display = df_display.with_columns(
                    pl.col("business area").map_elements(_as_business_area, return_dtype=pl.Utf8).alias("business area")
                )
            _write_grouped_canonical_sheet(ws, 4, df_display, account_type_col="business area",
                                            head_col="report_fs_head", note_col="report_note")
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 10, "B": 34, "C": 10, "D": 20, "E": 12, "F": 14, "G": 14, "H": 14, "I": 14, "J": 30, "K": 16})

        # 4. Canonical TB (CY)
        _write_canonical_sheet("Canonical TB (CY)", cy, f"{cy_full_fy} (CY)")
        # 5. Canonical TB (PY)
        _write_canonical_sheet("Canonical TB (PY)", py, f"{py_full_fy} (PY)")

        # 6. Variance -- reads variance_rows (comparison_variance.parquet), already loaded
        # for the DOCX section, instead of recomputing. Same flag vocabulary the DOCX
        # report's §7 uses (post run_comparison_variance.py's vocabulary fix), so Excel
        # and DOCX now agree by construction.
        def _write_variance_sheet():
            ws = new_sheet("Variance")
            _write_sheet_title(ws, "PY vs CY Variance Analysis",
                                "Common accounts only — materiality-flagged closing-balance movement", 8)
            var_rows = [
                [r.get("gl_code"), r.get("gl_name"), head_by_code.get(str(r.get("gl_code", ""))) or "",
                 r.get("py_closing"), r.get("cy_closing"), r.get("variance"), r.get("variance_pct"), r.get("flag")]
                for r in variance_rows
            ]
            _write_table(ws, 4, ["GL Code", "GL Name", "FS Head", "PY Closing", "CY Closing", "Variance", "Variance %", "Flag"],
                         var_rows, formats=[None, None, None, _ACC_FMT, _ACC_FMT, _ACC_FMT, None, None])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 10, "B": 32, "C": 12, "D": 18, "E": 18, "F": 18, "G": 12, "H": 12})

        _write_variance_sheet()

        # 7. New Ledgers (CY) -- reads structural_data (structural_delta.json), already
        # loaded for the DOCX section, instead of recomputing.
        def _write_new_ledgers_sheet():
            ws = new_sheet("New Ledgers (CY)")
            _write_sheet_title(ws, "New Ledgers — Present in CY, Not in PY", None, 4)
            new_rows = [
                [g.get("gl_code"), g.get("gl_name"), head_by_code.get(str(g.get("gl_code", ""))) or "", closing_by_code.get(str(g.get("gl_code", "")), 0.0)]
                for g in new_ledgers
            ]
            _write_table(ws, 4, ["GL Code", "GL Name", "FS Head", "CY Closing"], new_rows, formats=[None, None, None, _ACC_FMT])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 10, "B": 40, "C": 14, "D": 18})

        _write_new_ledgers_sheet()

        # 8. Removed Ledgers (PY) -- same source as New Ledgers.
        def _write_removed_ledgers_sheet():
            ws = new_sheet("Removed Ledgers (PY)")
            _write_sheet_title(ws, "Removed Ledgers — Present in PY, Not in CY", None, 4)
            removed_rows = [
                [g.get("gl_code"), g.get("gl_name"), py_head_by_code.get(str(g.get("gl_code", ""))) or "", py_closing_by_code.get(str(g.get("gl_code", "")), 0.0)]
                for g in removed_ledgers
            ]
            _write_table(ws, 4, ["GL Code", "GL Name", "FS Head", "PY Closing"], removed_rows, formats=[None, None, None, _ACC_FMT])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 10, "B": 40, "C": 14, "D": 18})

        _write_removed_ledgers_sheet()

        # 9. Continuity Breaks -- reads genuine_breaks, already computed for the DOCX
        # section from precheck_results.json's opening_closing_continuity check, which
        # run_comparison_prechecks.py already excludes expected P&L resets from at the
        # source. The old _compute_continuity_breaks had NO P&L-reset exclusion at all, so
        # this tab previously showed the same false positives the DOCX report used to.
        def _write_continuity_breaks_sheet():
            ws = new_sheet("Continuity Breaks")
            _write_sheet_title(ws, "PY Closing → CY Opening Continuity Breaks (genuine only — P&L resets excluded)",
                                "Common accounts where PY closing ≠ CY opening beyond tolerance, excluding expected P&L resets", 5)
            cont_rows = [[b["gl_code"], b["gl_name"], b["py_closing"], b["cy_opening"], b["diff"]] for b in genuine_breaks]
            _write_table(ws, 4, ["GL Code", "GL Name", "PY Closing", "CY Opening", "Difference"],
                         cont_rows, formats=[None, None, _ACC_FMT, _ACC_FMT, _ACC_FMT])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 10, "B": 34, "C": 18, "D": 18, "E": 18})

        _write_continuity_breaks_sheet()

        # 10. Sign Convention -- calls the same canonical backend/tools/_shared.py::
        # sign_convention_stats() single-TB reports use, called once per period, instead
        # of a comparison-specific reimplementation.
        def _write_sign_convention_sheet():
            ws = new_sheet("Sign Convention")
            sign_stats_basis = sign_stats_cy.get("basis") or sign_stats_py.get("basis") or SIGN_CONVENTION_STATS_BASIS_LABEL
            _write_sheet_title(
                ws, "Sign Convention Consistency",
                f"Accounts sitting opposite their FS Head normal side -- {sign_stats_basis}. "
                "See TB-000/run_comparison_sign_check for the separate anchor-keyword screens.",
                7,
            )
            sign_rows_tbl = [
                [f"{py_label_short} (PY)", sign_stats_py.get("debit_flip", 0), sign_stats_py.get("debit_total", 0), sign_stats_py.get("debit_flip_pct", 0.0),
                 sign_stats_py.get("credit_flip", 0), sign_stats_py.get("credit_total", 0), sign_stats_py.get("credit_flip_pct", 0.0)],
                [f"{cy_label_short} (CY)", sign_stats_cy.get("debit_flip", 0), sign_stats_cy.get("debit_total", 0), sign_stats_cy.get("debit_flip_pct", 0.0),
                 sign_stats_cy.get("credit_flip", 0), sign_stats_cy.get("credit_total", 0), sign_stats_cy.get("credit_flip_pct", 0.0)],
            ]
            _write_table(ws, 4, ["Period", "Debit-side w/ Credit Balance", "Debit-side Total", "% Anomalous (Debit)",
                                  "Credit-side w/ Debit Balance", "Credit-side Total", "% Anomalous (Credit)"],
                         sign_rows_tbl, formats=[None, _ACC_FMT, _ACC_FMT, None, _ACC_FMT, _ACC_FMT, None])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 12, "B": 22, "C": 16, "D": 18, "E": 22, "F": 16, "G": 18})

        _write_sign_convention_sheet()

        # 11. Risk Indicators (CY) -- concentration/dormant now call the already-fixed
        # canonical functions in the main backend/tools/_shared.py (TB-R13/R14 contra-pair
        # netting and denominator-degeneracy guard, TB-R15 estimation-risk tagging), the
        # same ones the DOCX report's §7/single-TB reports use, instead of a comparison-
        # local reimplementation with none of those fixes. Sign anomalies rendered as
        # aggregate stats (sign_stats_cy, already computed above), matching how single-TB's
        # Excel sheet renders this post-Phase-1, instead of a per-account list.
        def _write_risk_indicators_sheet():
            ws = new_sheet("Risk Indicators")
            _write_sheet_title(ws, "Risk Indicators — Current Year", "Concentration / dormant-balance / sign-convention screens", 6)
            r = _write_section_header(ws, 4, "Concentration (>5% of FS Head)")
            conc_rows = [[c["gl_code"], c["gl_name"], c["fs_head"], c["closing"], c["pct"], c.get("note", "")] for c in conc_rows_xlsx]
            r = _write_table(ws, r, ["GL Code", "GL Name", "FS Head", "Closing", "% of FS Head", "Note"],
                              conc_rows, formats=[None, None, None, _ACC_FMT, _PCT_FMT, None])
            r += 2
            r = _write_section_header(ws, r, "Dormant Accounts (no movement, material balance)")
            dorm_rows = [
                [d["gl_code"], d["gl_name"], d["fs_head"], d["closing"], "ESTIMATION RISK" if d.get("is_estimation_risk") else ""]
                for d in dormant_rows_xlsx
            ]
            r = _write_table(ws, r, ["GL Code", "GL Name", "FS Head", "Closing", "Flag"], dorm_rows, formats=[None, None, None, _ACC_FMT, None])
            r += 2
            r = _write_section_header(ws, r, f"Sign Convention Consistency ({sign_stats_cy.get('basis') or SIGN_CONVENTION_STATS_BASIS_LABEL})")
            sign_anomaly_rows = [
                ["Debit-side (Assets/Expenses) w/ credit balance", sign_stats_cy.get("debit_flip", 0), sign_stats_cy.get("debit_total", 0), f"{sign_stats_cy.get('debit_flip_pct', 0.0)}%"],
                ["Credit-side (Liabilities/Equity/Revenue) w/ debit balance", sign_stats_cy.get("credit_flip", 0), sign_stats_cy.get("credit_total", 0), f"{sign_stats_cy.get('credit_flip_pct', 0.0)}%"],
            ]
            _write_table(ws, r, ["Screen", "Flagged", "Total in Group", "% Flagged"], sign_anomaly_rows,
                         formats=[None, _ACC_FMT, _ACC_FMT, None])
            _set_col_widths(ws, {"A": 40, "B": 30, "C": 12, "D": 18, "E": 14})

        _write_risk_indicators_sheet()

        # 12. Sensitive Accounts (CY) -- reads sensitive_accounts.parquet's real per-account
        # classification (loaded above as sensitive_df_xlsx), the same file single-TB's
        # Excel sheet reads post-Phase-1, instead of a comparison-local keyword re-scan.
        def _write_sensitive_accounts_sheet():
            ws = new_sheet("Sensitive Accounts")
            _write_sheet_title(ws, "Sensitive Account Categories — Current Year",
                                "Related party / grant-subsidy / MSME / CSR / statutory-dues / suspense / propriety / deposits / advances / FCY", 4)
            r = 4
            if sensitive_df_xlsx is not None and not sensitive_df_xlsx.is_empty() and "category" in sensitive_df_xlsx.columns:
                for category in sensitive_df_xlsx["category"].unique(maintain_order=True).sort().to_list():
                    sub = sensitive_df_xlsx.filter(pl.col("category") == category).sort("gl_code")
                    r = _write_section_header(ws, r, str(category).replace("_", " ").title())
                    cat_rows = [[a["gl_code"], a["gl_name"], a["balance"], a["priority"]] for a in sub.to_dicts()]
                    r = _write_table(ws, r, ["GL Code", "GL Name", "Closing", "Priority"], cat_rows, formats=[None, None, _ACC_FMT, None])
                    r += 1
            _set_col_widths(ws, {"A": 26, "B": 40, "C": 18, "D": 12})

        _write_sensitive_accounts_sheet()

        # The current-year leg ran the full single-TB analytics chain, so its Phase-2

        # populations exist in cy_dir. They are carried into this workbook so the

        # comparative report can reference the same complete populations the

        # single-TB report does, rather than sending a reader to a second workbook.

        _cy_population_sheets(new_sheet, cy_dir)
        _ratio_trend_sheet(new_sheet, py_dir, cy_dir, py_fy_label, cy_fy_label)


        resolved = []

        for _name in sheets_written:

            _ws = wb[_name]

            resolved.append((_name, max(_ws.max_row - 4, 0)))


        with atomic_write(excel_path) as tmp:
            wb.save(tmp)

        artifacts.append(str(excel_path.resolve()))


        manifest_path = write_manifest(out_dir, resolved, excel_path.name)

        artifacts.append(str(manifest_path.resolve()))

        # Sections 12-25 and the schedule appendix, now that the manifest exists.
        manifest = read_manifest(out_dir)
        _write_phase4_sections(doc, cy_dir, manifest, add_body, add_note, add_table,
                               add_bullets, safe_fmt,
                               render_sensitive=_render_sensitive_categories,
                               render_focus_areas=_render_focus_areas,
                               render_focus_sequence=_render_focus_sequence)
        _write_limitations_section(doc, f"{py_source} and {cy_source}", comparative=True)
        _write_schedule_appendix(doc, manifest, add_body, add_note, add_table)

        try:
            with atomic_write(docx_path) as tmp:
                doc.save(tmp)
            artifacts.append(str(docx_path))
        except Exception as e:
            errors.append(log_and_redact_exception("build_comparison_report", e, error_type="ExportError"))
            return {"execution_status": "FAILED", "errors": errors,
                    "message": "Failed to save comparison DOCX."}
    except Exception as e:
        errors.append(log_and_redact_exception("build_comparison_report", e, error_type="ExcelError"))
        return {"execution_status": "FAILED", "errors": errors, "message": "Failed to create comparison Excel report."}

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": f"Generated TB_Comparison_Audit.xlsx and TB_Comparison_Report.docx in {out_dir}.",
        "artifacts": artifacts,
        "errors": errors,
    }

_CY_POPULATIONS = [
    # Findings Register + Evidence Requests + Management Queries consolidated into 2
    # sheets here too, matching the SINGLE_TB workbook (see _write_findings_and_evidence_
    # sheets' docstring for the full rationale) -- a finding's management query is
    # injected as extra columns on its own row (see _cy_population_sheets' special case
    # below), not a third sheet.
    ("Findings & Queries Register", "finding_records.json", "finding_records",
     ["source_screen", "account", "fsli", "amount", "risk_rating", "regularity_flag",
      "observation", "expectation", "gap", "assertion", "proposed_response",
      "evidence_requested", "management_query", "candidate_explanations"],
     ["Screen", "Account", "FSLI", "Amount", "Risk", "Regularity", "Observation",
      "Expectation", "Gap", "Assertions", "Proposed response", "Evidence requested",
      "Management Query", "Candidate explanations"]),
    ("Evidence Request Register", "evidence_request_list.json", "evidence_request_list",
     ["record", "audit_area", "highest_risk_rating", "regularity_relevant", "requested_for"],
     ["Record to obtain", "Audit area", "Highest risk", "Regularity", "Requested for"]),
    ("Assertion Map", "assertion_evidence_map.json", "assertion_evidence_map",
     ["account", "account_area", "assertions", "evidence_requested", "previous_generic_evidence"],
     ["Account", "Account area", "Assertions", "Evidence requested", "Previously (generic)"]),
    ("Counterpart Gaps", "counterpart_screen.json", "finding_records",
     ["relationship_id", "source_head", "source_balance", "target_balance", "risk_rating", "gap"],
     ["Relationship", "Source head", "Source", "Counterpart", "Risk", "Gap"]),
    ("Relationship Expectations", "relationship_expectations.json", "results",
     ["id", "name", "status", "observed_ratio", "formula"],
     ["ID", "Relationship", "Status", "Observed ratio", "Formula"]),
    ("Abnormal Signs", "abnormal_sign_screen.json", "finding_records",
     ["account", "report_head", "amount", "normal_balance_expectation", "actual_balance_side"],
     ["Account", "Class", "Balance", "Normal side", "Actual side"]),
    ("Statutory Screen", "statutory_screen.json", "results",
     ["id", "label", "status", "base_balance", "dues_balance", "observed_ratio"],
     ["ID", "Levy relationship", "Status", "Base", "Dues", "Observed ratio"]),
    ("Public Sector Lens", "public_sector_lens.json", "finding_records",
     ["account", "regularity_question", "amount", "risk_rating", "evidence_requested"],
     ["Account", "Regularity question", "Balance", "Risk", "Evidence requested"]),
    ("CARO Indicators", "caro_indicators.json", "finding_records",
     ["fsli", "clause_hint", "amount", "risk_rating", "observation"],
     ["Area", "Clause hint", "Balance", "Rating", "Observation"]),
    ("Override Indicators", "override_indicators.json", "finding_records",
     ["indicator", "account", "amount", "risk_rating", "observation"],
     ["Indicator", "Account", "Amount", "Risk", "Observation"]),
    ("Going Concern", "going_concern_screen.json", "indicators",
     ["indicator", "present", "amount"], ["Indicator", "Present", "Amount"]),
]

def _ratio_trend_sheet(new_sheet, py_dir, cy_dir, py_fy_label, cy_fy_label):
    """Remark #21 (Part E) fix: PY-vs-CY/direction per ratio -- a render job once
    build_financial_ratios runs for both legs (routes.py's PY-leg branch now calls it),
    not new ratio math. Degrades to whichever leg's file is actually present rather
    than requiring both."""
    py_path = Path(py_dir) / "financial_ratios.json"
    cy_path = Path(cy_dir) / "financial_ratios.json"
    py_ratios = {}
    cy_ratios = {}
    if py_path.exists():
        try:
            py_ratios = (json.loads(py_path.read_text(encoding="utf-8")) or {}).get("ratios") or {}
        except (json.JSONDecodeError, OSError):
            pass
    if cy_path.exists():
        try:
            cy_ratios = (json.loads(cy_path.read_text(encoding="utf-8")) or {}).get("ratios") or {}
        except (json.JSONDecodeError, OSError):
            pass
    if not py_ratios and not cy_ratios:
        return

    def _fmt(v):
        return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

    rows = []
    for key in sorted(set(py_ratios) | set(cy_ratios)):
        py_val = (py_ratios.get(key) or {}).get("value")
        cy_val = (cy_ratios.get(key) or {}).get("value")
        if isinstance(py_val, (int, float)) and isinstance(cy_val, (int, float)):
            direction = "Up" if cy_val > py_val else ("Down" if cy_val < py_val else "Flat")
        else:
            direction = "—"
        rows.append([key.replace("_", " ").title(), _fmt(py_val), _fmt(cy_val), direction])

    ws = new_sheet("Ratio Trend (PY vs CY)")
    _write_sheet_title(ws, "Ratio Trend — Prior Year vs Current Year",
                       "Each ratio's value in both periods and its direction of movement.", 4)
    _write_table(ws, 4, ["Ratio", f"{py_fy_label} (PY)", f"{cy_fy_label} (CY)", "Direction"], rows)
    ws.freeze_panes = "A5"
    _set_col_widths(ws, {"A": 26, "B": 16, "C": 16, "D": 12})


def _cy_population_sheets(new_sheet, cy_dir):
    for name, filename, key, columns, headers in _CY_POPULATIONS:
        path = Path(cy_dir) / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        records = (data or {}).get(key) or []
        if not records:
            continue
        if name == "Findings & Queries Register":
            for r in records:
                q = _build_management_query(r)
                r["management_query"] = q["query"] if q else ""
                r["candidate_explanations"] = q["candidate_explanations"] if q else []
        rows = []
        for r in records:
            row = []
            for c in columns:
                v = r.get(c)
                if isinstance(v, (list, tuple)):
                    v = "; ".join(str(x) for x in v)
                elif isinstance(v, dict):
                    v = json.dumps(v, default=str)
                row.append(v)
            rows.append(row)
        ws = new_sheet(name)
        _write_sheet_title(ws, f"{name} — Current Year",
                           "Full population; the comparative report's narrative is capped",
                           len(headers))
        _write_table(ws, 4, headers, rows, formats=[None] * len(headers))
        ws.freeze_panes = "A5"


@pipeline_tool("build_comparison_report_markdown", domain="comparison")
def build_comparison_report_markdown(output_dir: str = None, **kwargs) -> dict:
    """
    Renders the same PY vs CY Comparison report content as build_comparison_report (Executive
    Summary through Limitations & No-Opinion Statement) as a markdown string in `data.markdown`,
    for relaying verbatim in a CHAT_QUERY/COMPARISON final response instead of only pointing at
    the .docx file. Reads the identical comparison artifacts (precheck_results.json,
    structural_delta.json, comparison_variance.parquet, sign_convention_flags.json,
    comparison_reasoning.json) and CY single-TB artifacts as build_comparison_report, so figures
    never drift between the two -- only the rendering target differs.
    """
    errors = []
    out_dir = resolve_output_dir(output_dir)

    def load_json(base_dir, filename, required=True):
        p = Path(base_dir) / filename
        if not p.exists():
            if required:
                errors.append({"type": "MissingDataWarning", "message": f"File {filename} not found in {base_dir}."})
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": filename, "error": str(e)})
            return None

    reasoning = load_json(out_dir, "comparison_reasoning.json") or {}
    precheck_data = load_json(out_dir, "precheck_results.json") or {"checks": []}
    structural_data = load_json(out_dir, "structural_delta.json") or {}
    sign_check_data = load_json(out_dir, "sign_convention_flags.json") or {"flags": []}

    variance_rows = []
    var_path = out_dir / "comparison_variance.parquet"
    if var_path.exists():
        try:
            variance_rows = pl.read_parquet(var_path).to_dicts()
        except Exception as e:
            errors.append({"type": "ParseError", "file": "comparison_variance.parquet", "error": str(e)})

    cy_dir_override = kwargs.get("cy_output_dir")
    cy_dir = Path(cy_dir_override) if cy_dir_override else (out_dir.parent / "cy")
    if not cy_dir.exists():
        cy_dir = out_dir
    cy_materiality = load_json(cy_dir, "materiality.json", required=False) or {}
    cy_sensitive = load_json(cy_dir, "sensitive_accounts.json", required=False) or {}
    cy_canonical_df = _load_canonical_tb(cy_dir)

    def safe_fmt(val):
        try:
            return f"{float(val):,.0f}"
        except Exception:
            return str(val)

    tb_meta_cy = load_json(cy_dir, "tb_metadata.json", required=False) or {}
    manifest_cy = load_json(cy_dir, "processing_manifest.json", required=False) or {}
    py_dir_override = kwargs.get("py_output_dir")
    py_dir = Path(py_dir_override) if py_dir_override else (out_dir.parent / "py")
    tb_meta_py = load_json(py_dir, "tb_metadata.json", required=False) or {} if py_dir.exists() else {}
    manifest_py = load_json(py_dir, "processing_manifest.json", required=False) or {} if py_dir.exists() else {}

    entity_label, _cy_fy, cy_source = resolve_entity_and_fy(tb_meta_cy, manifest_cy)
    _, _, py_source = resolve_entity_and_fy(tb_meta_py, manifest_py)
    stem = Path(cy_source).stem if cy_source else "Unknown Entity"
    cy_fy_match = re.search(r"FY\s*[\d\-/]+", stem, re.IGNORECASE)
    cy_fy_label = cy_fy_match.group(0).upper() if cy_fy_match else "CY"
    py_stem = Path(py_source).stem if py_source else ""
    py_fy_match = re.search(r"FY\s*[\d\-/]+", py_stem, re.IGNORECASE)
    py_fy_label = py_fy_match.group(0).upper() if py_fy_match else "PY"

    checks = precheck_data.get("checks", [])
    py_ctrl = next((c for c in checks if c.get("check") == "py_debit_credit_control_totals"), None)
    cy_ctrl = next((c for c in checks if c.get("check") == "cy_debit_credit_control_totals"), None)
    continuity_check = next((c for c in checks if c.get("check") == "opening_closing_continuity"), None)

    cy_materiality_thr = cy_materiality.get("thresholds", {})
    om = cy_materiality_thr.get("overall", 0.0)
    perf_mat = cy_materiality_thr.get("performance", 0.0)
    triv = cy_materiality_thr.get("clearly_trivial", 0.0)
    cy_summ = cy_materiality.get("summary", {})
    cy_sens_cat = cy_sensitive.get("category_summary", {})

    cy_fsli_df = None
    cy_fsli_path = cy_dir / "fsli_summary.parquet"
    if cy_fsli_path.exists():
        try:
            cy_fsli_df = pl.read_parquet(cy_fsli_path)
        except Exception as e:
            errors.append({"type": "ParseError", "file": "fsli_summary.parquet", "error": str(e)})

    cy_conc_rows = _concentration_rows_from_canonical(cy_canonical_df)
    cy_dormant_rows = _dormant_rows_from_canonical(cy_canonical_df)

    new_ledgers = structural_data.get("new_ledgers", [])
    removed_ledgers = structural_data.get("removed_ledgers", [])
    py_count = structural_data.get("py_count", 0)
    cy_count = structural_data.get("cy_count", 0)

    # BUG FIX (comparative-analysis QA review): see build_comparison_report.py's identical
    # fix for the full explanation. run_comparison_prechecks.py now excludes expected P&L
    # resets from break_count/breaks at the source (using the canonical bs_pl column, not
    # a pattern-matched main_head label) -- continuity_check's `breaks` here is already the
    # genuine list, no re-filtering needed.
    genuine_breaks = []
    expected_reset = 0
    total_breaks = 0
    if continuity_check:
        expected_reset = continuity_check.get("expected_pl_reset_count", 0)
        total_breaks = continuity_check.get("total_raw_break_count", continuity_check.get("break_count", 0))
        name_map = {}
        ref_df = cy_canonical_df if cy_canonical_df is not None else _load_canonical_tb(py_dir)
        if ref_df is not None and "gl_code" in ref_df.columns and "gl_name" in ref_df.columns:
            name_map = dict(zip(ref_df["gl_code"].cast(pl.Utf8).to_list(), ref_df["gl_name"].to_list()))
        for b in continuity_check.get("breaks", []):
            code = str(b.get("gl_code", ""))
            try:
                diff_val = float(b.get("diff", 0) or 0)
            except (TypeError, ValueError):
                diff_val = 0.0
            genuine_breaks.append({
                "gl_code": code, "gl_name": name_map.get(code, ""),
                "py_closing": b.get("closing_balance"), "cy_opening": b.get("opening_balance"),
                "diff": diff_val,
            })
        genuine_breaks.sort(key=lambda x: abs(x["diff"]), reverse=True)

    # See build_comparison_report.py's identical mapping note: "twice overall materiality"
    # == CRITICAL, "more than the threshold alone" == HIGH (not the new, more granular
    # MEDIUM tier).
    variance_flagged = [r for r in variance_rows if r.get("flag") not in (None, "NO_CHANGE")]
    high_priority = [r for r in variance_flagged if r.get("flag") == "CRITICAL"]
    medium_variance = [r for r in variance_flagged if r.get("flag") == "HIGH"]
    top_movements = sorted(variance_flagged, key=lambda r: abs(r.get("variance") or 0), reverse=True)[:10]
    flags = sign_check_data.get("flags", [])
    observations = reasoning.get("observations", [])

    def md_table(headers, rows):
        if not rows:
            return ""
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in rows:
            lines.append("| " + " | ".join(str(c) for c in r) + " |")
        return "\n".join(lines) + "\n"

    lines = []
    a = lines.append
    def _render_title():
        a(f"# Trial Balance - ({entity_label}) — PY vs CY Comparative Analysis")
        a(f"### {py_fy_label} → {cy_fy_label}")
        a("")
        a("> Mechanical, evidence-based analytical review derived solely from the two trial balance "
          "files and the GL grouping workbook supplied. No audit opinion; risk-assessment and planning "
          "aid only.")
        a("")

    _render_title()

    def _render_executive_summary():
        a("## Executive Summary")
        if py_count and cy_count:
            a(
                f"The {entity_label} trial balance {'grew' if cy_count >= py_count else 'reduced'} from "
                f"{py_count} GL accounts in {py_fy_label} to {cy_count} in {cy_fy_label}. Both years are "
                + ("internally self-balanced — debit and credit control totals match exactly in each "
                   "period." if (py_ctrl and py_ctrl.get("status") == "PASS" and cy_ctrl and cy_ctrl.get("status") == "PASS")
                   else "compared below; see Data Quality for the detailed control-total position.")
            )
        a("")
        if new_ledgers or removed_ledgers:
            a(f"- **Structural change** — {len(new_ledgers)} account(s) appear only in {cy_fy_label} and "
              f"{len(removed_ledgers)} only in {py_fy_label} — a {len(new_ledgers) + len(removed_ledgers)}-"
              f"account chart-of-accounts change against a base of roughly {py_count or cy_count}.")
        if total_breaks:
            a("- **Continuity** — " + (
                f"{expected_reset} of {total_breaks} PY-closing-to-CY-opening differences are "
                "Revenue/Expense accounts resetting to nil (expected). "
                f"{len(genuine_breaks)} account(s) carry a genuine, unexplained discontinuity."
                if genuine_breaks else
                f"All {total_breaks} PY-closing-to-CY-opening differences are Revenue/Expense accounts "
                "resetting to nil, which is expected and not a control weakness."
            ))
        if high_priority or medium_variance:
            a(f"- **Material movements** — {len(high_priority)} account(s) moved by more than twice the "
              f"overall materiality threshold and a further {len(medium_variance)} moved by more than "
              "the threshold alone.")
        if flags:
            a("- **Sign convention** — sign-convention screens were run for both periods; see Sign "
              "Convention Consistency below — this is a broad screen, not a specific finding.")
        a("")
        a("**Where to focus first:**")
        focus_first = []
        if genuine_breaks:
            top_break = genuine_breaks[0]
            focus_first.append(
                f"- Continuity — obtain the retained-earnings/continuity roll-forward and agree the "
                f"{safe_fmt(top_break['diff'])} movement on GL {top_break['gl_code']}."
            )
        if top_movements:
            top_mv = top_movements[0]
            focus_first.append(
                f"- Variance — corroborate the largest variance accounts, especially "
                f"{top_mv.get('gl_name', '')} ({safe_fmt(top_mv.get('variance', 0))})."
            )
        if cy_conc_rows:
            top_c = cy_conc_rows[0]
            focus_first.append(
                f"- Concentration — {top_c['gl_name']} is {top_c['pct']:.1f}% of total {top_c['fs_head']} "
                "— disproportionate reliance on one balance being correctly stated."
            )
        a("\n".join(focus_first) if focus_first else "No high-priority focus areas were flagged deterministically in this dataset.")
        a("")

    _render_executive_summary()

    def _render_data_quality():
        a("## Data Quality & Control Totals")
        if py_ctrl or cy_ctrl:
            a(md_table(
                ["Check", f"{py_fy_label} (PY)", f"{cy_fy_label} (CY)"],
                [
                    ("Total Debit", safe_fmt((py_ctrl or {}).get("total_debit", 0)), safe_fmt((cy_ctrl or {}).get("total_debit", 0))),
                    ("Total Credit", safe_fmt((py_ctrl or {}).get("total_credit", 0)), safe_fmt((cy_ctrl or {}).get("total_credit", 0))),
                    ("Debit − Credit Difference", safe_fmt((py_ctrl or {}).get("diff", 0)), safe_fmt((cy_ctrl or {}).get("diff", 0))),
                ],
            ))
            both_pass = (py_ctrl or {}).get("status") == "PASS" and (cy_ctrl or {}).get("status") == "PASS"
            a(
                "Debit and credit control totals "
                + ("match exactly in both periods (zero difference)." if both_pass else
                   "do not match exactly in one or both periods — see the status flags above.")
            )
        else:
            a("Control totals could not be computed — precheck_results.json was not available.")
        a("")

    _render_data_quality()

    def _render_materiality():
        a("## Provisional Materiality (CY basis)")
        if cy_materiality:
            a(md_table(
                ["Basis", "Value"],
                [
                    ("Materiality Basis Used", cy_summ.get("benchmark_used", "Unknown")),
                    ("Overall Materiality", safe_fmt(om)),
                    ("Performance Materiality (75% of OM)", safe_fmt(perf_mat)),
                    ("Clearly Trivial Threshold", safe_fmt(triv)),
                ],
            ))
            a("*Provisional only — a mechanical benchmark-based calculation on the current-year trial balance. Not a substitute for engagement-level materiality judgment.*")
        else:
            a("Current-year materiality was not available for this comparison run.")
        a("")

    _render_materiality()

    def _render_fsli_summary():
        a("## FSLI Summary — Current Year")
        if cy_fsli_df is not None and not cy_fsli_df.is_empty():
            if "hierarchy_level" in cy_fsli_df.columns:
                lvl1 = cy_fsli_df.filter(pl.col("hierarchy_level") == 1)
            else:
                lvl1 = cy_fsli_df
            lvl1 = lvl1.with_columns(pl.col("closing_balance").abs().alias("_abs")).sort("_abs", descending=True)
            a(md_table(
                ["FS Head", "GL Accounts", "Closing Balance (CY)"],
                [
                    (r.get("main_head") or r.get("fs_head") or r.get("line_item") or "",
                     int(r.get("descendant_gl_count") or r.get("leaf_gl_count") or r.get("gl_accounts") or 0),
                     safe_fmt(r.get("closing_balance", 0.0)))
                    for r in lvl1.iter_rows(named=True)
                ],
            ))
        else:
            a("Current-year FSLI summary was not available for this comparison run.")
        a("")

    _render_fsli_summary()

    def _render_structural_delta():
        a("## Structural Delta — PY vs CY Ledger Composition")
        a(f"{py_count} GL account(s) in {py_fy_label} vs {cy_count} in {cy_fy_label}. "
          f"{len(new_ledgers)} added, {len(removed_ledgers)} removed.")
        a("")

    _render_structural_delta()

    def _render_continuity():
        a("## PY Closing → CY Opening Continuity")
        if continuity_check:
            a(f"{total_breaks} discontinuit(y/ies) found; {expected_reset} are expected Revenue/Expense "
              f"resets, {len(genuine_breaks)} are genuine.")
            if genuine_breaks:
                a(md_table(
                    ["GL Code", "GL Name", "PY Closing", "CY Opening", "Diff"],
                    [(g["gl_code"], g["gl_name"], safe_fmt(g["py_closing"]), safe_fmt(g["cy_opening"]), safe_fmt(g["diff"])) for g in genuine_breaks[:15]],
                ))
        else:
            a("Continuity check not available.")
        a("")

    _render_continuity()

    def _render_variance_analysis():
        a("## Variance Analysis — PY vs CY Closing Balance")
        if top_movements:
            a(md_table(
                ["GL Code", "GL Name", "PY Closing", "CY Closing", "Variance", "Flag"],
                [(m.get("gl_code", ""), m.get("gl_name", ""), safe_fmt(m.get("py_closing", 0)), safe_fmt(m.get("cy_closing", 0)), safe_fmt(m.get("variance", 0)), m.get("flag", "")) for m in top_movements],
            ))
        else:
            a("No material PY vs CY variances were flagged.")
        a("")

    _render_variance_analysis()

    def _render_sign_convention():
        a("## Sign Convention Consistency")
        a(f"{len(flags)} sign-convention flag(s) raised across both periods." if flags else
          "Sign convention could not be assessed or no flags were raised.")
        a("")

    _render_sign_convention()

    def _render_risk_indicators():
        a("## Risk Indicators — Current Year")
        a("**Concentration — accounts exceeding 5% of their FS Head**")
        if cy_conc_rows:
            a(md_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "% of FS Head"],
                [(c["gl_code"], c["gl_name"], c["fs_head"], safe_fmt(c["closing"]), f"{c['pct']:.1f}%") for c in cy_conc_rows],
            ))
        else:
            a("No account individually exceeds 5% of its FS Head in the current year.")
        a("")
        a("**Dormant accounts (current year)**")
        if cy_dormant_rows:
            a(md_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                [(d["gl_code"], d["gl_name"], d["fs_head"], safe_fmt(d["closing"])) for d in cy_dormant_rows],
            ))
        else:
            a("No dormant accounts with a material closing balance were identified.")
        a("")

    _render_risk_indicators()

    def _render_sensitive_accounts():
        a("## Sensitive Account Categories — Current Year")
        if cy_sens_cat:
            a(md_table(
                ["Category", "Accounts", "Combined Closing Balance"],
                [(k.replace("_", " "), str(v.get("count", 0)), safe_fmt(v.get("total_balance", 0))) for k, v in cy_sens_cat.items()],
            ))
        else:
            a("Current-year sensitive-account categories were not available for this comparison run.")
        a("")

    _render_sensitive_accounts()

    def _render_focus_areas():
        a("## Focus Areas")
        if observations:
            for i, obs_wrap in enumerate(observations, start=1):
                obs = obs_wrap.get("observation", obs_wrap) if isinstance(obs_wrap, dict) else {}
                sev = str(obs.get("priority", "LOW")).upper()
                title = obs.get("title", "Comparison Finding")
                a(f"### F{i:02d} [{sev}] {title}")
                a(f"- **Observation and Gap:** {obs.get('detailed_observation', obs.get('executive_summary', ''))}")
                assertions = obs.get("affected_assertions", [])
                a(f"- **Assertions/Risk Basis:** {', '.join(assertions)}. Risk Rating: {sev}")
                procs = obs.get("recommended_procedures", [])
                a(f"- **Evidence Requested:** {', '.join(procs) if procs else 'reconciliation, ledger dump'}")
        else:
            a("No comparison observations were generated for this dataset.")
        a("")

    _render_focus_areas()

    def _render_focus_sequence():
        a("## Suggested Audit Focus Sequence")
        seq_lines = []
        for obs_wrap in observations:
            obs = obs_wrap.get("observation", obs_wrap) if isinstance(obs_wrap, dict) else {}
            sev = str(obs.get("priority", "LOW")).upper()
            if sev not in ("CRITICAL", "HIGH", "MEDIUM"):
                continue
            name = obs.get("title", "")
            reason = obs.get("executive_summary", "")
            seq_lines.append(f"- {name} — {sev}-rated finding: {reason}")
        a("\n".join(seq_lines) if seq_lines else "No High/Medium-rated findings to sequence for audit focus.")
        a("")

    _render_focus_sequence()

    def _render_limitations():
        a("## Limitations & No-Opinion Statement")
        a("This review is mechanical and confined entirely to the two trial balance files and the "
          "grouping workbook supplied:")
        a("- No assurance opinion — it does not constitute an audit, review, or any other assurance "
          "engagement under any professional standard.")
        a("- No corroboration performed — no vouching, confirmation, analytical corroboration against "
          "external data, or management inquiry was performed.")
        a("- Grouping not independently verified — classifications rely on the GL Grouping workbook as "
          "supplied and have not been independently verified against statutory financial statements.")
        a("- No rescaling — figures are presented exactly as extracted from source, unscaled.")
        a("")
        a("*This document should be read alongside, not instead of, full substantive audit procedures.*")

    _render_limitations()

    markdown = "\n".join(lines)
    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": "Rendered Comparison report content as markdown.",
        "data": {"markdown": markdown},
        "artifacts": [],
        "errors": errors,
    }


def _require_file__run_comparison_prechecks(path, label: str):
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise PipelineFileError(str(p), label)
    return p

@pipeline_tool("run_comparison_prechecks", domain="comparison")
def run_comparison_prechecks(py_canonical_tb: str, cy_canonical_tb: str, output_dir: str = None) -> dict:
    """Run PY/CY continuity and integrity pre-checks on two canonical TBs."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_df = pl.read_parquet(_require_file__run_comparison_prechecks(py_canonical_tb, "PY canonical TB"))
    cy_df = pl.read_parquet(_require_file__run_comparison_prechecks(cy_canonical_tb, "CY canonical TB"))

    checks = []
    # Opening(CY) == Closing(PY) per GL code — ported from
    # TB_comparison/tools/precheck_tools.py:240 check_opening_closing_continuity
    #
    # P&L accounts correctly reset to a nil opening balance every year (they carry no
    # balance forward by design) -- that is expected, not a continuity break. This is the
    # single source every downstream consumer (build_comparison_report.py,
    # build_comparison_report_markdown.py, build_comparison_reasoning.py's HIGH-severity
    # continuity_break finding) reads break_count/breaks from, so filtering here fixes all
    # three at once. A prior version instead let each of those three files independently
    # re-derive "P&L or not" by string-matching the raw main_head label against the literal
    # strings "revenue"/"expenses" -- but main_head holds sub-category labels ("Wages",
    # "Rent", "Auditor fees", ...), never those literal words, so the exclusion almost
    # never matched. Live verification against a real PY->CY comparison found 29 of 30
    # breaks were ordinary P&L resets wrongly reported as genuine, producing a false
    # HIGH-severity finding that buried the one real (Equity) break under 29 false
    # positives. bs_pl ("BS"/"PL", set once per row from the source data) is the reliable,
    # already-existing signal for this -- no pattern-matching needed.
    cy_select_cols = ["gl_code", "opening_balance"]
    has_bs_pl = "bs_pl" in cy_df.columns
    if has_bs_pl:
        cy_select_cols.append("bs_pl")
    merged = py_df.select(["gl_code", "closing_balance"]).join(
        cy_df.select(cy_select_cols), on="gl_code", how="full", coalesce=True
    )
    # A gl_code present in PY but entirely absent from CY (opening_balance genuinely null,
    # not zero) is a removed ledger -- a structural change already captured by
    # run_comparison_structural.py's removed_ledgers list, not a continuity anomaly.
    # "Did PY's closing balance correctly carry into CY's opening balance" only means
    # something when the account still exists in CY -- excluded here, not folded into
    # break_count under either bucket, so it isn't silently double-counted nor silently
    # dropped from the total.
    merged = merged.filter(pl.col("opening_balance").is_not_null())
    merged = merged.with_columns(
        (
            pl.col("closing_balance").cast(pl.Float64, strict=False).fill_null(0)
            - pl.col("opening_balance").cast(pl.Float64, strict=False).fill_null(0)
        ).abs().alias("diff")
    )
    raw_breaks = merged.filter(pl.col("diff") > 0.01)
    if has_bs_pl:
        is_expected_pl_reset = (
            (pl.col("bs_pl").cast(pl.Utf8, strict=False).str.to_uppercase() == "PL")
            & (pl.col("opening_balance").cast(pl.Float64, strict=False).fill_null(0) == 0)
        )
        expected_pl_resets = raw_breaks.filter(is_expected_pl_reset)
        genuine_breaks = raw_breaks.filter(~is_expected_pl_reset)
    else:
        expected_pl_resets = raw_breaks.head(0)
        genuine_breaks = raw_breaks
    checks.append({
        "check": "opening_closing_continuity",
        "status": "PASS" if genuine_breaks.is_empty() else "WARNING",
        "break_count": int(len(genuine_breaks)),
        "breaks": genuine_breaks.head(50).to_dicts(),
        "expected_pl_reset_count": int(len(expected_pl_resets)),
        "total_raw_break_count": int(len(raw_breaks)),
    })

    # PY/CY debit == credit control totals — ported from
    # TB_comparison/tools/sign_convention_tools.py:51 check_debit_credit_control_totals.
    # TB-R20: this used to sum the raw debit/credit TURNOVER columns directly (same
    # defect as _shared.py::control_totals before its fix) -- ties by construction in a
    # SAP period-extract and proves nothing about whether the TB itself foots. Delegate
    # to control_totals() so the comparative precheck checks the same signed
    # closing-balance identity the single-TB report now does, and reports turnover as a
    # separate figure rather than mislabeling it as the control total.
    for label, df in (("PY", py_df), ("CY", cy_df)):
        ctrl = control_totals(df)
        if ctrl:
            diff = round(ctrl["difference"], 2)
            activity_base = max(abs(ctrl["total_debit"]), abs(ctrl["total_credit"]))
            diff_pct = round(abs(diff) / activity_base * 100, 4) if activity_base > 0 else 0.0
            if diff == 0.0:
                status = "PASS"
            elif diff_pct <= 5.0:
                status = "WARNING"
            else:
                status = "HALTED"
            checks.append({
                "check": f"{label.lower()}_debit_credit_control_totals",
                "status": status,
                "total_debit": round(ctrl["total_debit"], 2),
                "total_credit": round(ctrl["total_credit"], 2),
                "diff": diff,
                "diff_pct": diff_pct,
                "turnover_total_debit": round(ctrl["turnover_total_debit"], 2),
                "turnover_total_credit": round(ctrl["turnover_total_credit"], 2),
            })

    result_path = out_dir / "precheck_results.json"
    write_json_atomic({"checks": checks}, result_path, indent=2, default=str)

    any_halted = any(c["status"] == "HALTED" for c in checks)
    any_warning = any(c["status"] == "WARNING" for c in checks)
    # TB-R18: a genuinely HALTED precheck (e.g. a PY or CY TB that doesn't foot) must
    # surface as pipeline_status="HALTED"/can_continue=False, not be downgraded to
    # WARNING "so the pipeline can continue" -- that downgrade is the same defect fixed
    # in validate_layer1_tb/validate_layer2_tb, mirrored here for the comparative chain.
    halted_checks = [c["check"] for c in checks if c["status"] == "HALTED"]
    pipeline_status = "HALTED" if any_halted else ("WARNING" if any_warning else "SUCCESS")
    return {
        "execution_status": "SUCCESS",
        "artifacts": [str(result_path)],
        "message": f"Ran {len(checks)} comparison pre-check(s); pipeline_status={pipeline_status}.",
        "pipeline_status": pipeline_status,
        "can_continue": not any_halted,
        "halted_checks": halted_checks,
    }


def _require_file__run_comparison_sign_check(path, label: str):
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise PipelineFileError(str(p), label)
    return p

# TB-R31: same anchor-keyword mechanism as canonical.py's TB-000, but scored per-period
# (PY and CY separately) rather than on one combined TB -- a smaller, differently-scoped
# denominator than TB-000's single-TB figure or sign_convention_stats()'s population-wide,
# report_head-membership basis. All three must say which basis they used so a reader never
# mistakes one screen's percentage for the only "sign convention agreement" number.
_SIGN_CHECK_BASIS_LABEL = "anchor-keyword screen (per-period basis, PY and CY scored separately)"


@pipeline_tool("run_comparison_sign_check", domain="comparison")
def run_comparison_sign_check(py_canonical_tb: str, cy_canonical_tb: str, output_dir: str = None) -> dict:
    """Heuristic PY/CY closing-balance sign convention check using normal-side keyword anchors."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_df = pl.read_parquet(_require_file__run_comparison_sign_check(py_canonical_tb, "PY canonical TB"))
    cy_df = pl.read_parquet(_require_file__run_comparison_sign_check(cy_canonical_tb, "CY canonical TB"))

    # Sign convention heuristic -- ported from
    # TB_comparison/tools/sign_convention_tools.py check_sign_convention.
    # The keyword lists were byte-identical to validate_layer1_tb.py's copy
    # (verified by AST comparison before extraction); both now read the single
    # definition in backend/knowledge/anchors/normal_side_anchors.json so they
    # cannot drift apart.
    debit_kws = debit_anchors()
    credit_kws = credit_anchors()

    def match_side(name):
        n = str(name).lower()
        is_debit = any(kw in n for kw in debit_kws)
        is_credit = any(kw in n for kw in credit_kws)
        if is_debit and not is_credit:
            return "debit"
        if is_credit and not is_debit:
            return "credit"
        return None

    def check_year(df, year_str):
        debit_positive = debit_negative = 0
        credit_positive = credit_negative = 0
        for row in df.iter_rows(named=True):
            try:
                bal = float(row.get("closing_balance", 0.0))
            except (ValueError, TypeError):
                bal = 0.0
            if bal != bal or bal == 0:  # bal != bal is the NaN check
                continue
            side = match_side(row.get("gl_name", ""))
            if side == "debit":
                if bal > 0:
                    debit_positive += 1
                else:
                    debit_negative += 1
            elif side == "credit":
                if bal < 0:
                    credit_negative += 1
                else:
                    credit_positive += 1

        debit_anchors = debit_positive + debit_negative
        credit_anchors = credit_positive + credit_negative
        min_anchors = 3
        threshold = 0.7

        if debit_anchors < min_anchors or credit_anchors < min_anchors:
            return {
                "year": year_str,
                "status": "WARNING",
                "basis": _SIGN_CHECK_BASIS_LABEL,
                "message": (
                    f"[{_SIGN_CHECK_BASIS_LABEL}] Not enough recognizable ledger names in {year_str} "
                    f"to confirm sign convention ({debit_anchors} debit-side, {credit_anchors} "
                    f"credit-side anchors found; need {min_anchors} each). Skipping."
                ),
                "debit_anchors": debit_anchors,
                "credit_anchors": credit_anchors,
            }

        debit_agreement = debit_positive / debit_anchors
        credit_agreement = credit_negative / credit_anchors

        if debit_agreement >= threshold and credit_agreement >= threshold:
            status = "PASS"
            message = (
                f"[{_SIGN_CHECK_BASIS_LABEL}] {year_str} closing balances follow the expected "
                "debit-positive / credit-negative convention."
            )
        else:
            inv_debit_agreement = debit_negative / debit_anchors
            inv_credit_agreement = credit_positive / credit_anchors
            if inv_debit_agreement >= threshold and inv_credit_agreement >= threshold:
                status = "WARNING"
                message = (
                    f"[{_SIGN_CHECK_BASIS_LABEL}] {year_str} closing balances appear to follow a "
                    "credit-positive / debit-negative convention, the reverse of what this pipeline assumes."
                )
            else:
                status = "WARNING"
                message = (
                    f"[{_SIGN_CHECK_BASIS_LABEL}] {year_str} closing balance signs are inconsistent "
                    "relative to normal-side anchors. This may indicate mixed sign conventions in the "
                    "source file."
                )

        return {
            "year": year_str,
            "status": status,
            "basis": _SIGN_CHECK_BASIS_LABEL,
            "message": message,
            "debit_anchors": debit_anchors,
            "credit_anchors": credit_anchors,
            "debit_positive_pct": round(debit_agreement * 100, 1),
            "credit_negative_pct": round(credit_agreement * 100, 1),
        }

    py_result = check_year(py_df, "PY")
    cy_result = check_year(cy_df, "CY")
    flags = [py_result, cy_result]

    result_path = out_dir / "sign_convention_flags.json"
    write_json_atomic({"flags": flags}, result_path, indent=2, default=str)

    any_warning = any(f["status"] == "WARNING" for f in flags)
    return {
        "execution_status": "SUCCESS",
        "artifacts": [str(result_path)],
        "message": f"Sign convention check complete; {'warnings present' if any_warning else 'all passed'}.",
        "pipeline_status": "WARNING" if any_warning else "SUCCESS",
    }


def _require_file__run_comparison_structural(path, label: str):
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise PipelineFileError(str(p), label)
    return p

def _grouping_delta(py_df: pl.DataFrame, cy_df: pl.DataFrame, column: str) -> dict:
    """Diffs the distinct, non-null values of a grouping column (main_head/sub_head_1/sub_head_2)."""
    if column not in py_df.columns or column not in cy_df.columns:
        return {"py_values": [], "cy_values": [], "new": [], "removed": []}
    py_values = {str(v).strip() for v in py_df[column].drop_nulls().to_list() if str(v).strip()}
    cy_values = {str(v).strip() for v in cy_df[column].drop_nulls().to_list() if str(v).strip()}
    return {
        "py_values": sorted(py_values),
        "cy_values": sorted(cy_values),
        "new": sorted(cy_values - py_values),
        "removed": sorted(py_values - cy_values),
    }

@pipeline_tool("run_comparison_structural", domain="comparison")
def run_comparison_structural(py_canonical_tb: str, cy_canonical_tb: str, output_dir: str = None) -> dict:
    """Compute PY vs CY ledger count delta and new/removed GL codes / FS groupings."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_df = pl.read_parquet(_require_file__run_comparison_structural(py_canonical_tb, "PY canonical TB"))
    cy_df = pl.read_parquet(_require_file__run_comparison_structural(cy_canonical_tb, "CY canonical TB"))

    # Ledger count delta — ported from TB_comparison/tools/structural_tools.py
    # check_ledger_count_delta
    py_ledgers = {
        str(row["gl_code"]).strip(): str(row.get("gl_name", "")).strip()
        for row in py_df.iter_rows(named=True)
    }
    cy_ledgers = {
        str(row["gl_code"]).strip(): str(row.get("gl_name", "")).strip()
        for row in cy_df.iter_rows(named=True)
    }

    py_codes = set(py_ledgers.keys())
    cy_codes = set(cy_ledgers.keys())

    new_codes = cy_codes - py_codes
    removed_codes = py_codes - cy_codes

    new_ledgers = [{"gl_code": c, "gl_name": cy_ledgers[c]} for c in new_codes]
    removed_ledgers = [{"gl_code": c, "gl_name": py_ledgers[c]} for c in removed_codes]

    delta = len(cy_codes) - len(py_codes)

    # FS-grouping delta — new/removed main_head, sub_head_1, sub_head_2 values,
    # derived on the fly (no persisted hierarchy in the new canonical schema).
    fs_grouping_delta = {
        "main_head": _grouping_delta(py_df, cy_df, "main_head"),
        "sub_head_1": _grouping_delta(py_df, cy_df, "sub_head_1"),
        "sub_head_2": _grouping_delta(py_df, cy_df, "sub_head_2"),
    }

    structural_delta = {
        "check": "ledger_count_delta",
        "py_count": len(py_codes),
        "cy_count": len(cy_codes),
        "delta": delta,
        "new_ledgers": new_ledgers,
        "removed_ledgers": removed_ledgers,
        "fs_grouping_delta": fs_grouping_delta,
        "message": (
            f"PY ledgers: {len(py_codes)}, CY ledgers: {len(cy_codes)}. Delta: {delta}. "
            f"New ledgers: {len(new_ledgers)}. Removed ledgers: {len(removed_ledgers)}."
        ),
    }

    result_path = out_dir / "structural_delta.json"
    write_json_atomic(structural_delta, result_path, indent=2, default=str)

    return {
        "execution_status": "SUCCESS",
        "artifacts": [str(result_path)],
        "message": structural_delta["message"],
        "pipeline_status": "SUCCESS",
    }


def _require_file__run_comparison_variance(path, label: str):
    p = Path(path)
    if not p.exists():
        raise PipelineFileError(str(p), label)
    return p

@pipeline_tool("run_comparison_variance", domain="comparison")
def run_comparison_variance(
    py_canonical_tb: str, cy_canonical_tb: str, materiality_file: str = None, output_dir: str = None
) -> dict:
    """Compute PY vs CY closing-balance variance per GL code, flagged against materiality thresholds."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_df = pl.read_parquet(_require_file__run_comparison_variance(py_canonical_tb, "PY canonical TB"))
    cy_df = pl.read_parquet(_require_file__run_comparison_variance(cy_canonical_tb, "CY canonical TB"))

    # Read the shared materiality threshold produced by build_materiality, so comparison
    # variance flagging stays consistent with the single-TB report instead of
    # reimplementing TB_comparison's own PRECHECK_TOLERANCE_PCT. Defaults to the canonical
    # filename in output_dir when not explicitly supplied (matching the pattern used by
    # build_risk_indicators.py etc.) -- makes this tool robust even when called without an
    # explicit materiality_file, rather than silently degrading to NO_THRESHOLD.
    mat_path = Path(materiality_file) if materiality_file else out_dir / "materiality.json"
    overall_mat = None
    performance_mat = None
    if mat_path.exists():
        with open(mat_path, "r", encoding="utf-8") as f:
            mat_data = json.load(f)
        thresholds = mat_data.get("thresholds", {})
        overall_mat = float(thresholds.get("overall", 0.0)) or None
        performance_mat = float(thresholds.get("performance", 0.0)) or None

    # Wave 2 Fix 4b: the continuity engine (run_comparison_prechecks) already calls an
    # opening-balance break "the single most consequential item this comparison can
    # flag" -- but this function used to independently re-grade the SAME GL movement via
    # movement_flag() against CY-only materiality, with no awareness the row was also a
    # continuity break, so it could land on LOW while the continuity engine called it
    # critical. Client's own suggested fix (simpler and more defensible than a blended
    # PY/CY threshold): an opening-balance break is material by nature, not by amount --
    # exempt these rows from the materiality filter entirely.
    precheck_path = out_dir / "precheck_results.json"
    continuity_break_codes = set()
    if precheck_path.exists():
        with open(precheck_path, "r", encoding="utf-8") as f:
            precheck_data = json.load(f)
        continuity_check = next(
            (c for c in precheck_data.get("checks", []) if c.get("check") == "opening_closing_continuity"), None
        )
        if continuity_check:
            continuity_break_codes = {str(b["gl_code"]).strip() for b in continuity_check.get("breaks", [])}

    # Variance calculation — ported from TB_comparison/tools/variance_tools.py:18
    # calculate_variance
    py_data = {
        str(row["gl_code"]).strip(): {
            "name": str(row.get("gl_name", "")).strip(),
            "bal": float(row.get("closing_balance")) if row.get("closing_balance") is not None else 0.0,
        }
        for row in py_df.iter_rows(named=True)
    }
    cy_data = {
        str(row["gl_code"]).strip(): {
            "name": str(row.get("gl_name", "")).strip(),
            "bal": float(row.get("closing_balance")) if row.get("closing_balance") is not None else 0.0,
        }
        for row in cy_df.iter_rows(named=True)
    }

    all_codes = set(py_data.keys()).union(set(cy_data.keys()))
    rows = []
    for code in all_codes:
        py_bal = py_data.get(code, {}).get("bal", 0.0)
        cy_bal = cy_data.get(code, {}).get("bal", 0.0)
        name = cy_data.get(code, {}).get("name") or py_data.get(code, {}).get("name", "")

        variance = cy_bal - py_bal
        variance_pct = (variance / py_bal * 100) if py_bal != 0 else None

        # BUG FIX (comparative-analysis QA review, follow-up): this used to have its own
        # flag vocabulary (NO_THRESHOLD/HIGH_PRIORITY/MEDIUM/LOW) that never included
        # CRITICAL at all, while the Excel workbook's Variance sheet independently
        # recomputed the same concept via a different function (_movement_flag_for_report
        # in backend/tools/comparison/_shared.py) with a CRITICAL/HIGH/MEDIUM/LOW
        # vocabulary -- two non-communicating implementations, same TB-R06 pattern as the
        # original defect registry. Now uses the single canonical severity function
        # (backend/tools/_shared.py::movement_flag) that both this file's output and the
        # Excel workbook (after the Excel writer is switched to read this parquet directly)
        # agree on.
        if py_bal == 0 and cy_bal != 0:
            # remark #6 fix: this measures a nil-to-nonzero *balance* transition, not a
            # chart-of-accounts addition/removal (that's structural_delta's new_ledgers/
            # removed_ledgers, a genuine GL-code-set diff) -- label distinctly so the two
            # populations are never conflated under the same-looking flag name.
            flag = "BALANCE_APPEARED"
        elif py_bal != 0 and cy_bal == 0:
            flag = "BALANCE_DISAPPEARED"
        elif variance == 0:
            flag = "NO_CHANGE"
        else:
            flag = movement_flag(abs(variance), overall_mat, performance_mat) or "LOW"

        continuity_break_exempt = code in continuity_break_codes
        if continuity_break_exempt and flag not in ("CRITICAL",):
            flag = "CRITICAL"

        rows.append({
            "gl_code": code,
            "gl_name": name,
            "py_closing": round(py_bal, 2),
            "cy_closing": round(cy_bal, 2),
            "variance": round(variance, 2),
            "variance_pct": round(variance_pct, 2) if variance_pct is not None else None,
            "flag": flag,
            "continuity_break_exempt": continuity_break_exempt,
        })

    rows = sorted(rows, key=lambda x: x["gl_code"])
    result_df = pl.DataFrame(rows)
    result_path = out_dir / "comparison_variance.parquet"
    write_parquet_atomic(result_df, result_path)

    critical_count = sum(1 for r in rows if r["flag"] == "CRITICAL")
    return {
        "execution_status": "SUCCESS",
        "artifacts": [str(result_path)],
        "message": f"Computed variance for {len(rows)} GL code(s); {critical_count} critical.",
        "pipeline_status": "SUCCESS",
    }


__all__ = [
    '_classify_fsli',
    '_load_canonical_tb_for_report',
    '_fy_label',
    '_load_canonical_tb',
    '_concentration_rows_from_canonical',
    '_dormant_rows_from_canonical',
    '_TITLE_FONT',
    '_SUBTITLE_FONT',
    '_SECTION_FONT',
    '_TBL_HDR_FILL',
    '_TBL_HDR_FONT',
    '_ZEBRA_FILL',
    '_THIN_SIDE',
    '_CELL_BORDER',
    '_ACC_FMT',
    '_PCT_FMT',
    '_write_sheet_title',
    '_write_section_header',
    '_write_table',
    '_set_col_widths',
    '_write_materiality_basis_table',
    '_write_rule_register_table',
    '_write_rule_register_legend',
    '_rule_outcome',
    '_write_engagement_sheet',
    '_write_materiality_workings_sheet',
    '_write_benchmark_buildup_sheet',

    '_SUBTOTAL_L1_FILL',
    '_SUBTOTAL_L2_FILL',
    '_SUBTOTAL_FONT',
    '_SEVERITY_FILL_XLSX',
    '_write_grouped_canonical_sheet',
    '_write_exception_register',
    '_write_rows_grouped_by_fs_head',
    '_write_legend_sheet',
    '_SEVERITY_COLORS___shared',
    '_NUMBER_WORDS',
    '_apply_report_styles',
    '_add_page_number_field___shared',
    '_setup_page_layout___shared',
    '_set_table_cell_margins___shared',
    '_docx_add_table___shared',
    '_docx_add_note',
    '_docx_add_severity_heading___shared',
    '_docx_add_subtitle',
    '_docx_add_notice',
    '_docx_add_lead_bullet',
    '_write_limitations_section',
    '_OBSERVATION_TEXT_FIELDS',
    '_COMPARISON_REASONING_PROMPT_TEMPLATE',
    '_build_comparison_findings',
    'build_comparison_reasoning',
    'build_comparison_report',
    '_CY_POPULATIONS',
    '_cy_population_sheets',
    '_ratio_trend_sheet',
    'build_comparison_report_markdown',
    '_require_file__run_comparison_prechecks',
    'run_comparison_prechecks',
    '_require_file__run_comparison_sign_check',
    'run_comparison_sign_check',
    '_require_file__run_comparison_structural',
    '_grouping_delta',
    'run_comparison_structural',
    '_require_file__run_comparison_variance',
    'run_comparison_variance',
]
