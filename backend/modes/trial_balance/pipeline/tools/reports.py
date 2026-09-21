import datetime as _dt
import json
import re
from pathlib import Path

import polars as pl
from docx.shared import Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403

# The Excel/Word writing helpers (_write_table, _apply_report_styles, format
# constants, etc.) are generic building blocks used by both the comparison and
# single-TB report writers -- they physically live in comparison.py's domain-local
# section (pre-split, this was one "comparison/_shared.py" block that turned out to
# not be comparison-exclusive after all; confirmed via static bytecode scan that
# comparison.py itself needs nothing back from reports.py, so this is one-directional).
from modes.trial_balance.pipeline.tools.comparison import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.reasoning import (  # single source of truth for query text / label normalization
    _build_management_query,
    _normalise,
)

try:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Mm, Pt, RGBColor
except ImportError:
    Document = None

_SEVERITY_COLORS__build_docx_report = {
    "CRITICAL": RGBColor(0xC0, 0x00, 0x00) if Document else None,
    "HIGH": RGBColor(0xE0, 0x00, 0x00) if Document else None,
    "MEDIUM": RGBColor(0xE8, 0x7C, 0x00) if Document else None,
    "MED": RGBColor(0xE8, 0x7C, 0x00) if Document else None,
    "LOW": RGBColor(0xC9, 0xA6, 0x00) if Document else None,
    "INFORMATION REQUEST": RGBColor(0x1A, 0x1A, 0x1A) if Document else None,
}

def _add_page_number_field__build_docx_report(paragraph):
    """Inserts a Word PAGE field (auto-updating page number) into a paragraph."""
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

def _setup_page_layout__build_docx_report(doc, header_left: str, header_right: str):
    """Applies page setup — Portrait A4, 0.85/0.9in margins — plus a header (left: generation
    date-time, right: entity/year) and a footer with a centred auto-numbered page count."""
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
    _add_page_number_field__build_docx_report(fp)

def _docx_add_table__build_docx_report(doc, headers, rows, cell_pt=8, header_pt=10):
    """Adds a Table Grid table with a bold header row."""
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

def _docx_add_severity_heading__build_docx_report(doc, prefix: str, severity: str, title: str, level: int = 2):
    """Adds a Heading-style paragraph where the '[SEVERITY]' token is colour-coded."""
    style_name = f"Heading {level}"
    h = doc.add_paragraph(style=style_name)
    h.add_run(f"{prefix}. [")
    sev_run = h.add_run(severity)
    sev_run.font.color.rgb = _SEVERITY_COLORS__build_docx_report.get(severity.upper(), RGBColor(0x00, 0x00, 0x00))
    h.add_run(f"] {title}")
    return h

@pipeline_tool("build_docx_report", domain="reports")
def build_docx_report(
    output_dir: str = None,
    canonical_tb_file: str = None,
    tb_metadata_file: str = None,
    processing_manifest_file: str = None,
    mapping_summary_file: str = None,
    grouping_metadata_file: str = None,
    grouping_statistics_file: str = None,
    materiality_file: str = None,
    sensitive_accounts_file: str = None,
    consolidated_exceptions_file: str = None,
    audit_reasoning_file: str = None,
    fsli_summary_file: str = None,
    estimation_exposure_file: str = None,
    fx_exposure_file: str = None,
    mapping_quality_file: str = None,
    **kwargs,
) -> dict:
    """Composes TB_Audit_Report.docx. Every figure is computed directly off the canonical
    trial balance and the deterministic pipeline artifacts; Focus Areas additionally draws
    on the LLM-narrated audit_reasoning.json (falling back to the deterministic exception
    clusters if that step was skipped). No numbers are invented at report-composition time."""
    errors = []
    artifacts = []

    if Document is None:
        errors.append({"type": "ImportError", "message": "python-docx is not installed."})
        return {"execution_status": "FAILED", "errors": errors, "message": "python-docx is not installed."}

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def load_json(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            errors.append({"type": "MissingDataWarning", "message": f"File {p.name} not found."})
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return None

    def load_parquet(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            return None
        try:
            return pl.read_parquet(p)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return None

    manifest = load_json(processing_manifest_file, "processing_manifest.json") or {}
    tb_meta = load_json(tb_metadata_file, "tb_metadata.json") or {}
    mapping_summary = load_json(mapping_summary_file, "mapping_summary.json") or {}
    grouping_meta = load_json(grouping_metadata_file, "grouping_metadata.json") or {}
    grouping_stats = load_json(grouping_statistics_file, "grouping_statistics.json") or {}
    materiality = load_json(materiality_file, "materiality.json") or {}
    sensitive_accounts = load_json(sensitive_accounts_file, "sensitive_accounts.json") or {}
    exceptions_db = load_json(consolidated_exceptions_file, "consolidated_exceptions.json") or {}
    audit_reasoning = load_json(audit_reasoning_file, "audit_reasoning.json") or {}
    fsli_df = load_parquet(fsli_summary_file, "fsli_summary.parquet")
    estimation_exposure = load_json(estimation_exposure_file, "estimation_exposure.json") or {}
    fx_exposure = load_json(fx_exposure_file, "fx_exposure.json") or {}
    mapping_quality = load_json(mapping_quality_file, "mapping_quality.json") or {}
    canonical_path = Path(canonical_tb_file) if canonical_tb_file else out_dir / "canonical_tb.parquet"
    canonical_df = load_canonical_tb(canonical_path)

    sev_rank = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Information Request": 0}

    thr = materiality.get("thresholds", {})
    om = thr.get("overall", 0.0)
    perf_mat = thr.get("performance", 0.0)
    triv = thr.get("clearly_trivial", 0.0)
    summ = materiality.get("summary", {})
    sens_cat = sensitive_accounts.get("category_summary", {})
    susp = sens_cat.get("suspense_control", {})

    # Header/footer + title labels — entity/FY prefer tb_metadata.json's structured
    # fields (real MAIN document data for DB-sourced runs); the source filename is
    # only used as a fallback signal (upload-sourced runs) and for the as-at date.
    generated_at = _dt.datetime.now().strftime("%d-%b-%Y %H:%M")
    header_left = f"Generated: {generated_at}"
    entity_label, fy_label, source_file = resolve_entity_and_fy(tb_meta, manifest)
    stem = Path(source_file).stem if source_file else "Unknown Entity"
    date_match = re.search(r"\d{2}\.\d{2}\.\d{4}", stem)
    as_at = f" (as at {date_match.group(0)})" if date_match else ""
    header_right = f"{entity_label} - {fy_label}" if fy_label else entity_label

    doc = Document()
    _apply_report_styles(doc)
    _setup_page_layout__build_docx_report(doc, header_left=header_left, header_right=header_right)

    def add_table(headers, rows):
        return _docx_add_table__build_docx_report(doc, headers, rows)

    def add_note(text, style="Body Text"):
        _docx_add_note(doc, text, style=style)

    def add_body(text, style="Body Text"):
        doc.add_paragraph(text, style=style)

    def add_bullets(items):
        for it in items:
            doc.add_paragraph(it, style="List Bullet")

    # TB-R01/R02: total_rows/mapped_rows must never come from mapping_summary.json alone --
    # that file is only written by the upload column-mapping stage and is absent for every
    # DB-sourced run (load_tb_from_db.py loads data already-canonical), so it silently
    # defaulted every count to 0 while Section 8 (working off canonical_df directly) showed
    # the real number. len(canonical_df) is the single source of truth for row/account count;
    # mapping_summary.json is now used only for the mapped/unmapped breakdown, and only when
    # it actually exists.
    total_rows = canonical_row_count(canonical_df) if canonical_df is not None else mapping_summary.get("total_tb_rows", 0)
    # TB-R03: mapping_summary.json is never written by the current pipeline
    # (ingest_tb_to_live/load_tb_from_db both bypass it -- see canonical.py's
    # header note), so has_mapping_summary is always False and mapped/unmapped
    # silently rendered "n/a" for every run, upload or DB-sourced alike.
    # canonical_df's own mapped_status column (MAPPED/UNMAPPED/UNMATCHED,
    # written by validate_layer1_tb/validation_gate.py for every row) is the
    # real source of truth -- use it directly, same as total_rows above.
    if canonical_df is not None and canonical_df.height > 0 and "mapped_status" in canonical_df.columns:
        mapped_rows = int((canonical_df["mapped_status"] == MAPPED_STATUS_MAPPED).sum())
        unmapped_rows = total_rows - mapped_rows
    else:
        has_mapping_summary = bool(mapping_summary)
        mapped_rows = mapping_summary.get("mapped_rows", 0) if has_mapping_summary else None
        unmapped_rows = mapping_summary.get("unmapped_rows", max(total_rows - (mapped_rows or 0), 0)) if has_mapping_summary else None

    ctrl = control_totals(canonical_df)
    movement_rows = movement_rows_from_canonical(canonical_df, om, perf_mat)
    conc_rows = concentration_rows_from_canonical(canonical_df, clearly_trivial=triv)
    dormant_rows = dormant_rows_from_canonical(canonical_df)
    sign_stats = sign_convention_stats(canonical_df)

    # -------------------------------------------------------------------------
    # Title + Notice to the Reader
    # -------------------------------------------------------------------------
    def _render_title_and_notice():
        doc.add_heading(f"Trial Balance Analysis {entity_label}", 0)
        if fy_label or as_at:
            _docx_add_subtitle(doc, f"{fy_label}{as_at}".strip())
        _docx_add_notice(
            doc,
            "Notice to the reader: this document is a mechanical, evidence-based analytical review "
            "derived solely from the trial balance file and the GL grouping workbook supplied. No "
            "external documents, confirmations, or entity knowledge were used. It is a risk-assessment "
            "and planning aid only — it contains no audit opinion and no conclusion on misstatement, "
            "fraud, non-compliance, recoverability, or going concern. All figures are shown as supplied, "
            "unscaled and in the original currency; scale (units/thousands/lakh/crore) was not stated in "
            "the source file."
        )
        # Engagement identity, immediately under the notice. A reader who receives this
        # document detached from the workbook previously had no statement of which entity,
        # which period or which kind of review it covered -- and no CIN, which is how a
        # company is identified unambiguously when several carry similar names.
        _ec = load_json(None, "engagement_context.json") or {}
        _nn = load_json(None, "normalisation_note.json") or {}
        _ds = load_json(None, "data_sufficiency.json") or {}
        _cs = _nn.get("currency_and_scale") or {}
        _cur, _scl = _cs.get("currency") or "unknown", _cs.get("scale") or "unknown"
        _cur_scale = ("Not declared in the source file"
                      if str(_cur).lower() == "unknown" and str(_scl).lower() == "unknown"
                      else f"{_cur} / {_scl}")
        _fw = _ec.get("framework") or "unknown"
        if _ec.get("framework_basis") == "inferred_to_confirm":
            _fw = f"{_fw} (inferred - confirm with the engagement team)"
        _grade = _ds.get("grade") or "unknown"
        _mp = _ds.get("mapped_percentage")
        if isinstance(_mp, (int, float)):
            _grade = f"{_grade} - {_mp:.0f}% of accounts mapped"

        add_table(
            ["Particular", "Detail"],
            [
                ["Company Name", tb_meta.get("company_name") or tb_meta.get("entity_name") or entity_label or "Not stated"],
                ["CIN", tb_meta.get("cin") or "Not supplied"],
                ["Review Type", "Single TB Review"],
                ["Period of Review", (fy_label or "Not stated").strip()],
                ["Currency and Scale", _cur_scale],
                ["Accounting Framework", _fw],
                ["Accounts Analysed", str(total_rows)],
                ["Data Sufficiency", _grade],
            ],
        )
        add_body("Scope and limitations of this tool, applying throughout: " + " · ".join(SCOPE_LIMITATIONS_BRIEF))

    _render_title_and_notice()

    # -------------------------------------------------------------------------
    # Executive Summary
    # -------------------------------------------------------------------------
    # Sheets the workbook actually contains, for the cross-reference every section
    # closes with. Read once, before the first section needs it.
    manifest = read_manifest(out_dir)

    # Read by both the Executive Summary and TB Integrity sections below.
    balanced = bool(ctrl) and abs(ctrl["difference"]) < 1.0

    def _render_executive_summary():
        doc.add_heading("1. Executive Audit Dashboard", level=1)
        if ctrl:
            add_body(
                f"The {entity_label} trial balance comprises {total_rows} GL account(s). "
                + ("It is internally self-balanced — debit and credit control totals match exactly, "
                   f"with {ctrl['duplicate_gl_codes']} duplicate GL code(s) and {ctrl['fully_zero_rows']} "
                   "fully-zero row(s) — so the classification and risk analytics below rest on a sound "
                   "base rather than a data-quality artefact." if balanced else
                   f"Control totals differ by {safe_fmt(ctrl['difference'])} — see Section 2 for the "
                   "detailed data-quality position.")
            )
        else:
            add_body(f"The {entity_label} trial balance comprises {total_rows} GL account(s).")
        add_body(
            "Because only one period is available, this review focuses on what the trial balance shows "
            "about itself: composition, concentration, within-year movement, and account-level risk "
            "flags, rather than year-on-year change."
        )

        highlights = [movement_rows, conc_rows, dormant_rows, sign_stats, sens_cat]
        highlight_count = sum(1 for x in highlights if x)
        add_body(f"{NUMBER_WORDS.get(highlight_count, str(highlight_count))} things stand out:" if highlight_count else "Key observations:")
        if movement_rows:
            _docx_add_lead_bullet(
                doc, "In-year movement",
                f"{len(movement_rows)} Balance Sheet account(s) moved by more than performance "
                "materiality within the year, once Revenue/Expense accounts (which structurally run "
                "from nil to their full-year figure) are set aside (Section 6)."
            )
        if conc_rows:
            top = conc_rows[0]
            _docx_add_lead_bullet(
                doc, "Concentration",
                f"{top['gl_name']} alone is {top['pct']:.1f}% of total {top['fs_head']} — "
                f"{len(conc_rows)} account(s) individually exceed 5% of their FS Head (Section 7)."
            )
        if dormant_rows:
            _docx_add_lead_bullet(
                doc, "Dormant balances",
                f"{len(dormant_rows)} account(s) show no debit/credit activity all year despite a "
                "non-trivial closing balance (Section 7)."
            )
        if sign_stats:
            _docx_add_lead_bullet(
                doc, "Sign convention",
                f"On a {sign_stats.get('basis', SIGN_CONVENTION_STATS_BASIS_LABEL)}, "
                f"{sign_stats['debit_flip']} of {sign_stats['debit_total']} Asset/Expense account(s) "
                f"({sign_stats['debit_flip_pct']}%) and {sign_stats['credit_flip']} of "
                f"{sign_stats['credit_total']} Liability/Equity/Revenue account(s) "
                f"({sign_stats['credit_flip_pct']}%) sit opposite their FS Head's normal balance side "
                "(Section 8; see Section 8's own screen for a separate, narrower anchor-keyword check)."
            )
        if susp.get("count"):
            _docx_add_lead_bullet(
                doc, "Suspense/control exposure",
                f"{safe_fmt(susp.get('total_balance', 0))} combined balance across {susp['count']} "
                "suspense/control-named account(s), which by nature should net to nil or a small "
                "residual (Section 9)."
            )
        if not any((movement_rows, conc_rows, dormant_rows, sign_stats, susp.get("count"))):
            add_bullets(["No high-priority focus areas were flagged deterministically in this dataset."])

    _render_executive_summary()

    # -------------------------------------------------------------------------
    # 1. Scope & Inputs
    # -------------------------------------------------------------------------
    def _render_scope_and_inputs():
        doc.add_heading("2. Scope, Inputs and Limitations", level=1)
        mapped_display = str(mapped_rows) if mapped_rows is not None else "n/a"
        unmapped_display = str(unmapped_rows) if unmapped_rows is not None else "n/a"
        scope_rows = [("Trial Balance", source_file, str(total_rows), mapped_display, unmapped_display)]
        if grouping_meta.get("source_file"):
            gl_classified = grouping_stats.get("mapped_gls") or grouping_stats.get("gl_accounts") or "-"
            scope_rows.append(("Grouping", grouping_meta.get("source_file", ""), f"{gl_classified} GL codes classified", "-", "-"))
        add_table(["Input", "File", "Rows", "Accounts Mapped", "Unmapped"], scope_rows)
        add_body(
            "Grouping structure: FS Head (Assets/Liabilities/Equity/Revenue/Expenses) was derived by "
            "mapping the supplied grouping workbook's hierarchy to its statement classification. "
            "Classification source: hierarchy-based mapping from the supplied grouping workbook."
        )

    _render_scope_and_inputs()

    # -------------------------------------------------------------------------
    # 2. Data Quality & Control Totals
    # -------------------------------------------------------------------------
    def _render_tb_integrity():
        doc.add_heading("3. TB Integrity and Reconciliation", level=1)
        if ctrl:
            add_table(
                ["Check", "Value"],
                [
                    ("Total Debit (closing-balance foot)", safe_fmt(ctrl["total_debit"])),
                    ("Total Credit (closing-balance foot)", safe_fmt(ctrl["total_credit"])),
                    ("Debit − Credit Difference", safe_fmt(ctrl["difference"])),
                    ("Sum of Closing Balances (tie-out)", f"{ctrl['sum_closing']:,.6f}"),
                    ("Turnover — Debit reporting period (Dr rept. period)", safe_fmt(ctrl["turnover_total_debit"])),
                    ("Turnover — Credit reporting period (Cr rept. period)", safe_fmt(ctrl["turnover_total_credit"])),
                    ("Duplicate GL Codes", str(ctrl["duplicate_gl_codes"])),
                    ("Fully Zero Rows", str(ctrl["fully_zero_rows"])),
                ],
            )
            add_body(
                "Debit and credit control totals "
                + ("match exactly, " if balanced else f"differ by {safe_fmt(ctrl['difference'])}, ")
                + (f"with {ctrl['duplicate_gl_codes']} duplicate GL code(s) and {ctrl['fully_zero_rows']} "
                   "fully-zero row(s) — " if (ctrl["duplicate_gl_codes"] or ctrl["fully_zero_rows"])
                   else "with no duplicate GL codes or fully-zero rows — ")
                + ("the TB is arithmetically sound before any grouping logic is applied."
                   if balanced else
                   "resolve this mismatch before relying on the analytics below.")
            )
        else:
            add_body("Control totals could not be computed — the canonical trial balance was not available.")
        if unmapped_rows:
            add_body(
                f"{unmapped_rows} account(s) did not match a distinct grouping-workbook entry — confirm "
                "these are deliberately excluded rather than simply missed.",
                style="Normal",
            )

    _render_tb_integrity()

    # -------------------------------------------------------------------------
    # 8. Sign Convention Consistency
    # -------------------------------------------------------------------------
    def _render_sign_convention():
        doc.add_heading("Sign convention consistency", level=2)
        if sign_stats:
            add_body(
                f"On a {sign_stats.get('basis', SIGN_CONVENTION_STATS_BASIS_LABEL)} — see the separate "
                "TB-000/anchor-keyword screen elsewhere in this report for a narrower, name-based check — "
                f"{sign_stats['debit_flip']} of {sign_stats['debit_total']} Asset/Expense account(s) "
                f"({sign_stats['debit_flip_pct']}%) carry a credit balance, and "
                f"{sign_stats['credit_flip']} of {sign_stats['credit_total']} Liability/Equity/Revenue "
                f"account(s) ({sign_stats['credit_flip_pct']}%) carry a debit balance — a screen, not a "
                "defect: check each against its expected nature (contra-asset, provision, or clearing "
                "accounts are supposed to run opposite their FS Head) before treating it as a misclassification."
            )
        else:
            add_body("Sign convention could not be assessed — FS Head classification was not available.")
        _write_front_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(4, 5))

    _render_sign_convention()

    # -------------------------------------------------------------------------
    # 3. Provisional Planning Materiality
    # -------------------------------------------------------------------------
    def _render_materiality():
        doc.add_heading("6. Materiality Assessment", level=1)
        if materiality:
            add_table(
                ["Basis", "Value"],
                [
                    ("Materiality Basis Used", summ.get("benchmark_used", "Unknown")),
                    ("Overall Materiality", safe_fmt(om)),
                    ("Performance Materiality (75% of OM)", safe_fmt(perf_mat)),
                    ("Clearly Trivial Threshold", safe_fmt(triv)),
                ],
            )
            # TB-R09: benchmark_analysis already carries the winning candidate's selection
            # rationale (build_materiality.py) -- it was sitting in the spine file unread.
            benchmark_analysis = materiality.get("benchmark_analysis", [])
            chosen_candidate = next(
                (c for c in benchmark_analysis if c.get("benchmark") == summ.get("benchmark_used")), {}
            )
            rationale = chosen_candidate.get("reason")
            add_body(
                f"Overall materiality of {safe_fmt(om)} is derived from "
                f"{summ.get('benchmark_used', 'the selected benchmark')}"
                + (f" ({rationale})" if rationale else "")
                + f". Performance materiality ({safe_fmt(perf_mat)}) is the threshold used for the "
                "movement and concentration findings later in this report."
            )
            add_note(
                "Provisional only — a mechanical benchmark-based calculation. Not a substitute for "
                "engagement-level materiality judgment, which should also weigh qualitative factors, "
                "regulatory sensitivity, and prior-period misstatements.",
                style="Normal",
            )
        else:
            add_body("Materiality could not be computed — materiality.json was not available.")
        _write_front_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(7,))

    _render_materiality()

    # -------------------------------------------------------------------------
    # 4. FSLI Summary
    # -------------------------------------------------------------------------
    def _render_fsli_summary():
        doc.add_heading("8. FSLI and Ledger-Level Movements", level=1)
        if fsli_df is not None and not fsli_df.is_empty() and "main_head" in fsli_df.columns:
            lvl1 = fsli_df
            if "hierarchy_level" in lvl1.columns:
                lvl1 = lvl1.filter(pl.col("hierarchy_level") == 1)
            if "closing_balance" in lvl1.columns:
                lvl1 = lvl1.with_columns(pl.col("closing_balance").abs().alias("_abs")).sort("_abs", descending=True)
            fsli_rows = [
                (
                    r.get("main_head") or r.get("sub_head_1") or "",
                    int(r.get("gl_accounts") or r.get("descendant_gl_count") or r.get("leaf_gl_count") or 0),
                    safe_fmt(r.get("closing_balance", 0.0)),
                )
                for r in lvl1.iter_rows(named=True)
            ]
            add_table(["FS Head", "GL Accounts", "Closing Balance"], fsli_rows)
        else:
            add_body("FSLI summary not available.")

        # TB-R19: grouping taxonomy is trusted as supplied everywhere else in this pipeline --
        # this section surfaces inconsistencies, it does not correct them.
        mq_flags = mapping_quality.get("flags", [])
        if mq_flags:
            doc.add_heading("Grouping-taxonomy quality flags", level=2)
            add_table(
                ["Type", "Detail"],
                [(f.get("type", ""), f.get("detail", "")) for f in mq_flags],
            )
            add_body(
                "These flag possible inconsistencies in the grouping workbook as supplied (duplicate "
                "FS-head labels, or an account name suggesting a different nature than its assigned "
                "head) — the grouping itself is not altered; confirm with the entity whether each "
                "flagged item is intentional."
            )

    _render_fsli_summary()

    # -------------------------------------------------------------------------
    # 6. Balance Sheet In-Year Movement (Opening → Closing)
    # -------------------------------------------------------------------------
    def _render_balance_sheet_movement():
        doc.add_heading("Balance-sheet in-year movement (opening to closing)", level=2)
        if movement_rows:
            add_body(
                f"{len(movement_rows)} Balance Sheet account(s) moved by more than performance "
                "materiality (Revenue/Expense excluded — they structurally run from nil to their "
                "full-year figure). Each is a screen, not a finding — corroborate against the "
                "relevant schedule, prioritising CRITICAL-flagged balances first."
            )
            add_table(
                ["GL Code", "GL Name", "FS Head", "Opening", "Closing", "Movement", "Flag"],
                [
                    (m["gl_code"], m["gl_name"], m["fs_head"], safe_fmt(m["opening"]), safe_fmt(m["closing"]), safe_fmt(m["movement"]), m["flag"])
                    for m in movement_rows[:_SECTION_CAP]
                ],
            )
            if len(movement_rows) > _SECTION_CAP:
                add_note(f"Showing the top {_SECTION_CAP} of {len(movement_rows)} — see the Movement (BS) sheet for the complete population.")
        else:
            add_body("No Balance Sheet accounts moved by more than performance materiality within the year.")
        _write_front_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(9,))

    _render_balance_sheet_movement()

    # -------------------------------------------------------------------------
    # 7. Concentration & Dormant Balances
    # -------------------------------------------------------------------------
    def _render_concentration_and_dormant():
        doc.add_heading("10. Account-Risk Scoring", level=1)
        doc.add_heading("Concentration — accounts exceeding 5% of their FS Head", level=2)
        if conc_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "% of FS Head", "Note"],
                [(c["gl_code"], c["gl_name"], c["fs_head"], safe_fmt(c["closing"]), f"{c['pct']:.1f}%", c.get("note", "")) for c in conc_rows[:_SECTION_CAP]],
            )
            add_body(
                "A prioritisation signal, not a finding: an error in one of these few accounts has an "
                "outsized effect simply because of its size relative to its FS Head. Offsetting pairs "
                "(TB-R13) net close to nil despite each leg being individually large.",
                style="Normal",
            )
        else:
            add_body("No account individually exceeds 5% of its FS Head in this dataset.")

        doc.add_heading("Dormant accounts (no debit/credit movement in year) with material closing balance", level=2)
        if dormant_rows:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                [(d["gl_code"], d["gl_name"], d["fs_head"], safe_fmt(d["closing"])) for d in dormant_rows[:_SECTION_CAP]],
            )
            # TB-R15: estimation-prone balances (acquisition cost, CWIP, exploration,
            # impairment provisions) never get the blanket "structurally expected" framing,
            # regardless of size -- a no-movement pattern there means the carrying value has
            # gone unchallenged, not that dormancy is ordinary-course.
            estimation_risk = [d for d in dormant_rows if d.get("is_estimation_risk")]
            if estimation_risk:
                add_body(
                    "Estimation-prone accounts above (rests on management judgement, not a transactable "
                    "price) are not structurally-expected dormancy — obtain the impairment/capitalisation "
                    "basis regardless of size or tenure.",
                    style="Normal",
                )
            else:
                add_body(
                    "Check each against its expected nature — one-time opening entries and fully-"
                    "capitalised assets are expected to show no movement; any other material dormant "
                    "balance warrants a closer look.",
                    style="Normal",
                )
        else:
            add_body("No dormant accounts with a material closing balance were identified in this dataset.")

        # TB-R12: estimation-exposure screen, independent of the dormant screen above -- flags
        # every acquisition-cost/CWIP/exploration/impairment account by size regardless of
        # whether it moved in the year.
        doc.add_heading("Estimation exposure — acquisition cost / CWIP / exploration / impairment", level=2)
        est_accounts = estimation_exposure.get("accounts", [])
        if est_accounts:
            add_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "Moved in Year"],
                [(a["gl_code"], a["gl_name"], a["fs_head"], safe_fmt(a["closing_balance"]), "Yes" if a.get("moved_in_year") else "No") for a in est_accounts[:_SECTION_CAP]],
            )
            add_body(
                f"{len(est_accounts)} account(s), totalling "
                f"{safe_fmt(estimation_exposure.get('summary', {}).get('total_exposure', 0))}, cannot be "
                "corroborated from the TB alone regardless of movement — obtain the impairment/"
                "capitalisation working papers, prioritised by size.",
                style="Normal",
            )
        else:
            add_body("No acquisition-cost/CWIP/exploration/impairment accounts were identified in this dataset.")

    _render_concentration_and_dormant()

    # -------------------------------------------------------------------------
    # 5. Exception Summary
    # -------------------------------------------------------------------------
    # Read by this section and by _render_focus_areas/_render_focus_sequence below.
    clusters = exceptions_db.get("clusters", [])

    def _render_exception_summary():
        doc.add_heading("Exception clusters", level=2)
        if clusters:
            ranked_clusters = sorted(clusters, key=lambda c: sev_rank.get(c.get("cluster_severity", "Low"), 0), reverse=True)
            # TB-R07: render the real cluster_id minted once in consolidated_exceptions.json --
            # never re-mint an EXC-xxx sequence here, or the ID means something different in
            # every artifact that renders this same cluster set.
            exc_rows = [
                (
                    c.get("cluster_id", f"CL-{i:03d}"),
                    c.get("cluster_name", "Unnamed"),
                    str(c.get("cluster_severity", "Low")).upper(),
                    str(c.get("member_count", 0)),
                    safe_fmt(c.get("total_balance", 0.0)),
                )
                for i, c in enumerate(ranked_clusters[:_SECTION_CAP], start=1)
            ]
            add_table(["ID", "Cluster", "Severity", "Accounts", "Combined Amount"], exc_rows)
            if len(ranked_clusters) > _SECTION_CAP:
                add_note(f"Showing the top {_SECTION_CAP} of {len(ranked_clusters)} — see the Exception Register sheet for the complete, risk-ranked list.")
        else:
            add_body("No exception clusters were generated deterministically for this dataset.")
        _write_front_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                              add_bullets, safe_fmt, sections=(11,))

    _render_exception_summary()
    # These three blocks belong under sections 12, 17 and 22, which are emitted by
    # _write_phase4_sections. Each closes over this function's own locals
    # (observations / clusters / om and the formatting helpers), so they are passed
    # in as closures rather than moved -- the content renders where its number says
    # without being recomputed or duplicated.
    def _render_sensitive_categories():

        # -------------------------------------------------------------------------
        # 9. Sensitive Account Categories
        # -------------------------------------------------------------------------
        doc.add_heading("Sensitive account categories", level=2)
        if sens_cat:
            sens_rows = [(k.replace("_", " "), str(v.get("count", 0)), safe_fmt(v.get("total_balance", 0))) for k, v in sens_cat.items()]
            add_table(["Category", "Accounts", "Combined Closing Balance"], sens_rows)
            add_body(
                "These categories are name-pattern screens over the account list, not confirmed "
                "classifications — an account is tagged because its name contains a matching term (e.g. "
                "\"CSR\", \"MSME\", \"suspense\", \"advance\"), and each should be verified individually "
                "rather than relied upon as a complete or precise population."
            )
            if susp.get("count"):
                add_body(
                    f"The suspense/control tag carries {safe_fmt(susp.get('total_balance', 0))} combined "
                    "exposure and is the category most worth reconciling first, since by nature these "
                    "accounts should clear to nil or a small residual, not carry a large standing balance."
                )
        else:
            add_body("No sensitive-account categories were flagged in this dataset.")

        # TB-R11: FX/translation-risk screen, gated on entity_profile.json's has_foreign_ops
        # flag by build_fx_exposure.py itself -- only rendered when it actually screened.
        if fx_exposure.get("screened"):
            doc.add_heading("Foreign-exchange / translation-risk exposure", level=2)
            monetary_items = fx_exposure.get("monetary_items", [])
            non_monetary_items = fx_exposure.get("non_monetary_items", [])
            translation_rows = fx_exposure.get("translation_reserve_movement", [])
            if monetary_items:
                doc.add_heading("Monetary items (retranslated each period — ongoing exposure)", level=3)
                add_table(
                    ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                    [(m["gl_code"], m["gl_name"], m["fs_head"], safe_fmt(m["closing_balance"])) for m in monetary_items[:_SECTION_CAP]],
                )
                add_body(
                    f"{len(monetary_items)} account(s) totalling "
                    f"{safe_fmt(fx_exposure.get('summary', {}).get('total_monetary_exposure', 0))} — confirm "
                    "the closing rate applied against an independent source (RBI reference/bank rate).",
                    style="Normal",
                )
            if non_monetary_items:
                doc.add_heading("Non-monetary items (historical rate — no ongoing translation exposure)", level=3)
                add_table(
                    ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                    [(n["gl_code"], n["gl_name"], n["fs_head"], safe_fmt(n["closing_balance"])) for n in non_monetary_items[:_SECTION_CAP]],
                )
            if translation_rows:
                doc.add_heading("Translation / exchange-fluctuation reserve movement", level=3)
                add_table(
                    ["GL Code", "GL Name", "Opening", "Movement", "Closing"],
                    [(t["gl_code"], t["gl_name"], safe_fmt(t["opening_balance"]), safe_fmt(t["movement"]), safe_fmt(t["closing_balance"])) for t in translation_rows[:_SECTION_CAP]],
                )

    # Narrated observations, read once. Sections 17 and 22 both consume this, so it is
    # assigned in the enclosing scope rather than inside either closure.
    observations = [o.get("observation", {}) for o in audit_reasoning.get("audit_observations", [])]
    observations.sort(key=lambda o: sev_rank.get(o.get("priority", "Low"), 0), reverse=True)

    def _render_focus_areas():
        doc.add_heading("Narrated focus areas", level=2)
        clusters_by_id = {c.get("cluster_id"): c for c in clusters}

        # Capped to the top 3 here specifically (independent of _SECTION_CAP, which governs
        # plain summary tables elsewhere) -- this is full multi-line narrative per item, the
        # most page-expensive content in the report, and the Exception Register sheet
        # already holds the complete, uncapped population with its own per-account detail.
        _FOCUS_AREA_CAP = 3
        if observations:
            eligible_cluster_count = len([c for c in clusters if str(c.get("cluster_severity", "Low")) in ("Critical", "High", "Medium")])
            shown = observations[:_FOCUS_AREA_CAP]
            if len(shown) < eligible_cluster_count:
                add_body(
                    f"Showing the top {len(shown)} of {eligible_cluster_count} Medium-or-higher-"
                    "severity clusters; see the Exception Register sheet for the complete list.",
                    style="Normal",
                )
            for i, obs in enumerate(shown, start=1):
                sev = str(obs.get("priority", "MEDIUM")).upper()
                title = obs.get("title", "Untitled observation")
                cluster = clusters_by_id.get(obs.get("cluster_id"), {})
                # TB-R07: render the observation's real cluster_id (the same ID the Exception
                # Summary table above uses), not a re-minted F01/F02 sequence.
                ref_id = obs.get("cluster_id") or f"F{i:02d}"
                _docx_add_severity_heading__build_docx_report(doc, ref_id, sev, title, level=2)
                add_body(_brief(obs.get("detailed_observation") or obs.get("executive_summary", ""), 220))
                assertions = ", ".join(obs.get("affected_assertions", []))
                evidence = obs.get("supporting_evidence") or ", ".join(cluster.get("risk_themes", []))
                add_body(
                    f"{assertions} · {cluster.get('member_count', 0)} ledger(s), "
                    f"{safe_fmt(cluster.get('total_balance', 0.0))} vs materiality {safe_fmt(om)} · "
                    f"Evidence: {_brief(evidence, 80)}",
                    style="Normal",
                )
        elif clusters:
            ranked_clusters = sorted(clusters, key=lambda c: sev_rank.get(c.get("cluster_severity", "Low"), 0), reverse=True)
            for i, c in enumerate(ranked_clusters[:_FOCUS_AREA_CAP], start=1):
                sev = str(c.get("cluster_severity", "LOW")).upper()
                ref_id = c.get("cluster_id") or f"F{i:02d}"
                _docx_add_severity_heading__build_docx_report(doc, ref_id, sev, c.get("cluster_name", "Unknown Cluster"), level=2)
                themes = ", ".join(c.get("risk_themes", []))
                add_body(
                    f"{c.get('member_count', 0)} ledger(s) flagged under {themes}, combined net "
                    f"{safe_fmt(c.get('total_balance', 0))} vs materiality {safe_fmt(om)}. TB cannot "
                    "establish conditions or reconciliation.",
                    style="Normal",
                )
            if len(ranked_clusters) > _FOCUS_AREA_CAP:
                add_body(
                    f"Showing the top {_FOCUS_AREA_CAP} of {len(ranked_clusters)} clusters; see the "
                    "Exception Register sheet for the complete list.",
                    style="Normal",
                )
        else:
            add_body("No exception clusters were generated for this dataset.")

    def _render_focus_sequence():

        # -------------------------------------------------------------------------
        # 11. Suggested Audit Focus Sequence
        # -------------------------------------------------------------------------
        doc.add_heading("Suggested focus sequence", level=2)
        add_note("Priority order for planning, driven by risk-ranked findings and statutory indicators.")
        seq_items = observations if observations else clusters
        eligible = [
            item for item in seq_items
            if str(item.get("priority") or item.get("cluster_severity", "LOW")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
        ]
        for item in eligible[:_SECTION_CAP]:
            sev = str(item.get("priority") or item.get("cluster_severity", "LOW")).upper()
            name = item.get("title") or item.get("cluster_name", "")
            reason = item.get("conclusion") or item.get("executive_summary") or ""
            if not reason and "cluster_name" in item:
                # TB-R04: the clusters fallback (no LLM-narrated observation available) has no
                # conclusion/executive_summary field -- backfill a real one-line summary from the
                # cluster's own fields instead of leaving the trailing ": " empty.
                themes = ", ".join(item.get("risk_themes", []))
                reason = f"{item.get('member_count', 0)} ledger(s) flagged under {themes}, combined {safe_fmt(item.get('total_balance', 0))}."
            line = f"{name} — {sev}-rated finding: {_brief(reason, 100)}" if reason else f"{name} — {sev}-rated finding."
            doc.add_paragraph(line, style="List Bullet")
        if not eligible:
            add_body("No High/Medium-rated findings to sequence for audit focus.")
        elif len(eligible) > _SECTION_CAP:
            add_note(f"Showing the top {_SECTION_CAP} of {len(eligible)} — see the Exception Register sheet for the complete, risk-ranked list.")


    # -------------------------------------------------------------------------
    # 12-23. Phase-2 analytical sections, and the schedule cross-reference.
    #
    # `manifest` records the sheets build_excel_report actually wrote. Every section
    # below closes with reference_line(), which resolves to a real sheet name and row
    # count -- or to "" when no sheet backs that section, so the document never cites a
    # schedule that does not exist and never omits one that does.
    # -------------------------------------------------------------------------
    _write_phase4_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                           add_bullets, safe_fmt,
                           render_sensitive=_render_sensitive_categories,
                           render_focus_areas=_render_focus_areas,
                           render_focus_sequence=_render_focus_sequence)

    # 24. Limitations & No-Opinion Statement
    _write_limitations_section(doc, source_file, comparative=False)

    # 25. Detailed appendices and full TB schedules -- the index that guarantees every
    # sheet in the workbook is named somewhere in this document.
    _write_schedule_appendix(doc, manifest, add_body, add_note, add_table)

    # -------------------------------------------------------------------------
    # Export DOCX
    # -------------------------------------------------------------------------
    docx_path = out_dir / "TB_Audit_Report.docx"
    try:
        with atomic_write(docx_path) as tmp:
            doc.save(tmp)
        artifacts.append(str(docx_path))
    except Exception as e:
        errors.append(log_and_redact_exception("build_docx_report", e, error_type="ExportError"))
        return {"execution_status": "FAILED", "errors": errors, "message": "Failed to save DOCX report."}

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": "Generated TB_Audit_Report.docx.",
        "artifacts": artifacts,
        "errors": errors,
    }


_FS_HEAD_ORDER = ["Assets", "Equity", "Expenses", "Liabilities", "Revenue", "Unmapped"]

def _compute_unmapped(df: pl.DataFrame) -> list:
    """Trivial single-column filter, not a duplicated computation -- nothing else in the
    pipeline computes "unmapped accounts" independently, so this stays local."""
    return df.filter(pl.col("report_head") == "Unmapped").to_dicts()

@pipeline_tool("build_excel_report", domain="reports")
def build_excel_report(
    canonical_tb_file: str = None,
    output_dir: str = None,
    entity_label: str = None,
    tb_metadata_file: str = None,
    materiality_file: str = None,
    consolidated_exceptions_file: str = None,
    sensitive_accounts_file: str = None,
    estimation_exposure_file: str = None,
    fx_exposure_file: str = None,
    mapping_quality_file: str = None,
    **kwargs,
) -> dict:
    """Creates TB_Audit.xlsx — the Single TB Analytical Review workbook — with 9 sheets
    (Data Quality, Materiality, Canonical TB, Exception Register, Movement (BS), Risk
    Indicators, Sensitive Accounts, Unmapped Accounts). Canonical TB carries every GL row
    plus native Excel SUBTOTAL() rows and row-group outlining at FS-Head/FSLI level, in
    place of a separate FSLI Summary sheet -- see _write_grouped_canonical_sheet. Every
    figure is read from the same persisted spine artifacts and `_shared.py` compute
    functions build_docx_report.py/build_report_markdown.py use — no number here is
    recomputed independently (see module docstring, TB-R06/R08/R16/R17/R20)."""
    errors = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = Path(canonical_tb_file) if canonical_tb_file else out_dir / "canonical_tb.parquet"
    if not canonical_path.exists():
        raise PipelineFileError(str(canonical_path), "canonical_tb.parquet")

    excel_path = out_dir / "TB_Audit.xlsx"

    def load_json(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            errors.append({"type": "MissingDataWarning", "message": f"File {p.name} not found."})
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return {}

    def load_parquet(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            return None
        try:
            return pl.read_parquet(p)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return None

    try:
        df = load_canonical_tb(canonical_path)
        if df is None or df.is_empty():
            errors.append({"type": "EmptyDataError", "message": "canonical_tb.parquet loaded but is empty."})
            return {"execution_status": "FAILED", "errors": errors, "message": "Canonical TB is empty."}

        materiality = load_json(materiality_file, "materiality.json")
        thr = materiality.get("thresholds", {})
        om = thr.get("overall", 0.0)
        perf_mat = thr.get("performance", 0.0)
        triv = thr.get("clearly_trivial", 0.0)
        summ = materiality.get("summary", {})
        selected = materiality.get("selected_materiality", {})
        benchmark_analysis = materiality.get("benchmark_analysis", [])
        chosen_candidate = next(
            (c for c in benchmark_analysis if c.get("benchmark") == summ.get("benchmark_used")), {}
        )

        exceptions_db = load_json(consolidated_exceptions_file, "consolidated_exceptions.json")
        clusters = exceptions_db.get("clusters", [])
        exception_records = exceptions_db.get("exceptions", [])

        ctrl = control_totals(df)
        # limit=500 here (vs the function's own default of 20, still used by the Word/
        # Markdown top-N narratives): the Excel sheet is this report's full-population
        # surface, so it should not silently truncate a materiality-flagged movement list.
        movement_rows = movement_rows_from_canonical(df, om, perf_mat, limit=500)
        conc_rows = concentration_rows_from_canonical(df, clearly_trivial=triv)
        dormant_rows = dormant_rows_from_canonical(df)
        # Same BS-only exclusion movement_rows_from_canonical applies by default, so the
        # Movement (BS) sheet's "no movement" section covers exactly the same population
        # its "in-year movement" section does -- not the wider all-heads dormant screen
        # the Risk Indicators sheet below still uses.
        dormant_bs_rows = dormant_rows_from_canonical(
            df, limit=500, exclude_heads=("Revenue", "Expenses", "Suspense", "Unmapped")
        )
        sign_stats = sign_convention_stats(df)
        unmapped_rows = _compute_unmapped(df)

        sensitive_df = load_parquet(sensitive_accounts_file, "sensitive_accounts.parquet")
        estimation_exposure = load_json(estimation_exposure_file, "estimation_exposure.json")
        fx_exposure = load_json(fx_exposure_file, "fx_exposure.json")
        mapping_quality = load_json(mapping_quality_file, "mapping_quality.json")
        # Optional: build_anomaly_scanner's TB-022 (LLM semantic-mismatch spot-check) status,
        # read directly rather than via load_json so its absence (e.g. anomaly scan not run
        # this session) doesn't register as a MissingDataWarning -- it's a nice-to-have here.
        _anomaly_path = out_dir / "anomaly_findings.json"
        anomaly_data = json.loads(_anomaly_path.read_text(encoding="utf-8")) if _anomaly_path.exists() else {}
        # Full Layer-1/Layer-2 rule population for the Rule Register (read the same way --
        # its absence just means an emptier register, not a report-breaking warning).
        _layer1_path = out_dir / "layer1_results.json"
        layer1_results = json.loads(_layer1_path.read_text(encoding="utf-8")) if _layer1_path.exists() else []

        meta_path = Path(tb_metadata_file) if tb_metadata_file else out_dir / "tb_metadata.json"
        tb_meta = load_json(tb_metadata_file, "tb_metadata.json") if meta_path.exists() else {}
        # Prefers tb_metadata.json's structured entity_name/financial_year (real MAIN
        # document data for DB-sourced runs) -- resolve_entity_and_fy falls back to
        # filename-regex parsing only when those structured fields are absent.
        resolved_entity, resolved_fy, _src = resolve_entity_and_fy(tb_meta)
        period_label = resolved_fy or "Single Period"
        entity_label = entity_label or resolved_entity or out_dir.name

        registry_problems = validate_registry()
        if registry_problems:
            errors.append({"type": "ScheduleRegistryError", "problems": registry_problems})
            return {
                "execution_status": "FAILED", "errors": errors,
                "message": f"Schedule registry invalid: {'; '.join(registry_problems)}",
            }

        wb = Workbook()
        wb.remove(wb.active)

        # Every sheet is created through this helper so the manifest cannot fall out
        # of step with the workbook. The Word writer cross-references the manifest, so
        # a sheet created directly with wb.create_sheet() would exist but never be
        # cited -- exactly the silent drift the registry is here to prevent.
        sheets_written = []

        def new_sheet(name: str, row_count: int = 0):
            if name not in SCHEDULES:
                raise KeyError(
                    f"Sheet {name!r} is not in backend/tools/_report_schedules.py::SCHEDULES. "
                    "Add it there (with the section it supports) so the Word document can "
                    "reference it."
                )
            sheets_written.append((name, row_count))
            return wb.create_sheet(name)

        # 0. Engagement -- identity first. Loaded from artifacts the chain already wrote;
        # each is optional, so a missing one degrades a row rather than the sheet.
        _ec_path = out_dir / "engagement_context.json"
        engagement_ctx = json.loads(_ec_path.read_text(encoding="utf-8")) if _ec_path.exists() else {}
        _nn_path = out_dir / "normalisation_note.json"
        normalisation = json.loads(_nn_path.read_text(encoding="utf-8")) if _nn_path.exists() else {}
        _ds_path = out_dir / "data_sufficiency.json"
        data_sufficiency = json.loads(_ds_path.read_text(encoding="utf-8")) if _ds_path.exists() else {}

        ws = new_sheet("Engagement")
        _write_engagement_sheet(
            ws, tb_meta, engagement_ctx, normalisation, data_sufficiency,
            # This builder IS the single-TB workbook (see the docstring); the
            # comparative workbook is built by build_comparison_report and labels itself.
            mode_label="Single TB Review",
            period_label=period_label,
            account_count=canonical_row_count(df),
        )
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 26, "B": 74})

        # 1. Data Quality
        ws = new_sheet("Data Quality")
        _write_sheet_title(ws, "Data Quality & Control Totals",
                            "Layer-1 style checks on the raw TB file, prior to any grouping join", 5)
        next_row = 4
        if ctrl:
            dq_rows = [
                ("Total Debit (closing-balance foot)", ctrl["total_debit"]),
                ("Total Credit (closing-balance foot)", ctrl["total_credit"]),
                ("Debit − Credit Difference", ctrl["difference"]),
                ("Sum of Closing Balances (tie-out)", ctrl["sum_closing"]),
                ("Turnover — Debit reporting period (Dr rept. period)", ctrl["turnover_total_debit"]),
                ("Turnover — Credit reporting period (Cr rept. period)", ctrl["turnover_total_credit"]),
                ("Duplicate GL Codes", ctrl["duplicate_gl_codes"]), ("Fully Zero Rows", ctrl["fully_zero_rows"]),
            ]
            next_row = _write_table(ws, 4, ["Check", "Value"], dq_rows, formats=[None, _ACC_FMT])
        _write_rule_register_table(ws, next_row + 2, layer1_results)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 12, "B": 40, "C": 12, "D": 16, "E": 60})

        # 2. Materiality
        ws = new_sheet("Materiality")
        _write_sheet_title(ws, "Provisional Planning Materiality",
                            "Mechanical benchmark-based calculation. Not a substitute for engagement-level judgment.", 3)
        mat_rows = [
            ("Materiality Basis Used", summ.get("benchmark_used", "Unknown")),
            ("Basis Amount", selected.get("base_amount", 0.0)),
            ("Basis Selection Rationale", chosen_candidate.get("reason", "")),
            ("Overall Materiality", om),
            ("Performance Materiality (75% of OM)", perf_mat),
            ("Clearly Trivial Threshold", triv),
        ]
        next_row = _write_table(ws, 4, ["Basis", "Value"], mat_rows, formats=[None, _ACC_FMT, None, _ACC_FMT, _ACC_FMT, _ACC_FMT])
        _write_materiality_basis_table(ws, next_row + 2, materiality, mapping_quality=mapping_quality, anomaly_data=anomaly_data)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 38, "B": 40, "C": 16, "D": 22, "E": 12, "F": 12, "G": 12, "H": 12})

        # 2b. Materiality Workings -- the arithmetic behind the sheet above, so the audit
        # team can re-perform it against signed accounts and locate any difference in the
        # inputs rather than attributing it to the tool.
        ws = new_sheet("Materiality Workings")
        _write_materiality_workings_sheet(ws, materiality)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 7, "B": 42, "C": 20, "D": 62, "E": 14, "F": 13, "G": 60})

        # 2c. Benchmark Build-Up -- which balances sum into each benchmark. Workings above
        # state the arithmetic; this states the inputs, so "agree the base to the signed
        # accounts" becomes an instruction the auditor can actually follow.
        ws = new_sheet("Benchmark Build-Up")
        _write_benchmark_buildup_sheet(ws, out_dir, materiality)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 46, "B": 24, "C": 66})

        # 3. Canonical TB -- FSLI Summary's replacement: every GL row plus native Excel
        # SUBTOTAL() rows and row grouping (+/- outline) at both FS-Head and FSLI level,
        # computed by Excel itself over these same visible rows. See
        # _write_grouped_canonical_sheet's docstring for why the old two-sheet, two-taxonomy
        # split made independent SUMIFS/Pivot verification impossible.
        ws = new_sheet("Canonical TB")
        _write_sheet_title(ws, f"Canonical Trial Balance — {period_label}",
                            "Every ledger account, joined to grouping ground truth. Collapse the "
                            "row-group outline (top-left +/- buttons) for FS-Head/FSLI subtotals.", 9)
        _write_grouped_canonical_sheet(ws, 4, df)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 10, "B": 34, "C": 14, "D": 20, "E": 12, "F": 14, "G": 14, "H": 14, "I": 14, "J": 30, "K": 16})

        # 5. Exception Register -- reads consolidated_exceptions.json's clusters AND its
        # exceptions array directly; the cluster_id rendered here is the SAME ID minted
        # once in build_exception_consolidator.py, never re-derived (TB-R07). Every
        # cluster's Accounts/Amount is now a native SUBTOTAL() over its own member-account
        # rows below it (collapsible via the row-group outline), each carrying its specific
        # risk theme (rule_id) and the plain-language reason it was raised -- see
        # _write_exception_register.
        ws = new_sheet("Exception Register")
        _write_sheet_title(ws, "Consolidated Exception Register",
                            "All findings across the analytics below, in one severity-ranked view. "
                            "Collapse the row-group outline (top-left +/- buttons) for cluster totals only.", 9)
        _write_exception_register(ws, 4, clusters, exception_records)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 10, "B": 40, "C": 12, "D": 12, "E": 18, "F": 26, "G": 60, "H": 16, "I": 40})

        # 6. Movement (BS) -- both sub-sections grouped by FS Head (sorted), rather than a
        # single flat list ordered only by movement size, so a reviewer working through one
        # FS Head at a time (as an audit file typically is organized) can find everything
        # relevant to it in one place.
        ws = new_sheet("Movement (BS)")
        _write_sheet_title(ws, "Balance Sheet In-Year Movement",
                            "Opening vs Closing, Assets/Liabilities/Equity only — P&L accounts excluded (see notes). "
                            "Grouped by FS Head; includes accounts with NO movement in the same population.", 7)
        r = _write_section_header(ws, 4, "In-Year Movement (materiality-flagged)")
        r = _write_rows_grouped_by_fs_head(
            ws, r, movement_rows,
            ["GL Code", "GL Name", "FS Head", "Opening", "Closing", "Movement", "Flag"],
            row_fn=lambda m: [m["gl_code"], m["gl_name"], m["fs_head"], m["opening"], m["closing"], m["movement"], m["flag"]],
            formats=[None, None, None, _ACC_FMT, _ACC_FMT, _ACC_FMT, None],
        )
        r += 1
        r = _write_section_header(ws, r, "No Movement (zero debit/credit activity all year)")
        r = _write_rows_grouped_by_fs_head(
            ws, r, dormant_bs_rows,
            ["GL Code", "GL Name", "FS Head", "Closing", "What This May Indicate"],
            row_fn=lambda d: [d["gl_code"], d["gl_name"], d["fs_head"], d["closing"], d["explanation"]],
            formats=[None, None, None, _ACC_FMT, None],
        )
        ws.cell(row=r + 1, column=1,
                value="Note: Revenue and Expense accounts are excluded from this sheet — they structurally move from a "
                      "nil opening to their full-year closing figure every year, which is not \"movement\" in the "
                      "single-period risk sense. See the Canonical TB sheet for the full P&L detail.").font = Font(size=9, italic=True)
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 10, "B": 30, "C": 12, "D": 16, "E": 16, "F": 16, "G": 60})

        # 7. Risk Indicators
        ws = new_sheet("Risk Indicators")
        _write_sheet_title(ws, "Risk Indicators", "Concentration / dormant-balance / sign-convention screens, "
                            "grouped by FS Head. See the Legend sheet for how Priority/Severity labels elsewhere "
                            "in this workbook are computed.", 6)
        r = _write_section_header(ws, 4, "Concentration (>5% of FS Head) — risky GL accounts by FS Head")
        r = _write_rows_grouped_by_fs_head(
            ws, r, conc_rows,
            ["GL Code", "GL Name", "FS Head", "Closing", "% of FS Head", "Note"],
            row_fn=lambda c: [c["gl_code"], c["gl_name"], c["fs_head"], c["closing"], c["pct"], c.get("note", "")],
            formats=[None, None, None, _ACC_FMT, _PCT_FMT, None],
        )
        r += 1
        r = _write_section_header(ws, r, "Dormant Accounts (no movement, material balance)")
        r = _write_rows_grouped_by_fs_head(
            ws, r, dormant_rows,
            ["GL Code", "GL Name", "FS Head", "Closing", "Flag", "What This May Indicate"],
            row_fn=lambda d: [d["gl_code"], d["gl_name"], d["fs_head"], d["closing"],
                               "ESTIMATION RISK" if d.get("is_estimation_risk") else "", d.get("explanation", "")],
            formats=[None, None, None, _ACC_FMT, None, None],
        )
        r += 1
        r = _write_section_header(ws, r, f"Sign Convention Consistency ({sign_stats.get('basis', SIGN_CONVENTION_STATS_BASIS_LABEL) if sign_stats else SIGN_CONVENTION_STATS_BASIS_LABEL})")
        if sign_stats:
            sign_table_rows = [
                ["Debit-side (Assets/Expenses) w/ credit balance", sign_stats["debit_flip"], sign_stats["debit_total"], f"{sign_stats['debit_flip_pct']}%"],
                ["Credit-side (Liabilities/Equity/Revenue) w/ debit balance", sign_stats["credit_flip"], sign_stats["credit_total"], f"{sign_stats['credit_flip_pct']}%"],
            ]
            _write_table(ws, r, ["Screen", "Flagged", "Total in Group", "% Flagged"], sign_table_rows,
                         formats=[None, _ACC_FMT, _ACC_FMT, None])
        _set_col_widths(ws, {"A": 40, "B": 32, "C": 14, "D": 16, "E": 16, "F": 50})

        # 8. Sensitive Accounts -- reads sensitive_accounts.parquet's real per-account
        # classification (10 categories incl. related_party/grant_subsidy/propriety/
        # foreign_currency) instead of a private 6-category keyword list.
        ws = new_sheet("Sensitive Accounts")
        _write_sheet_title(ws, "Sensitive Account Categories",
                            "Related party / grant-subsidy / MSME / CSR / statutory-dues / suspense / propriety / deposits / advances / FCY", 4)
        r = 4
        if sensitive_df is not None and not sensitive_df.is_empty() and "category" in sensitive_df.columns:
            for category in sensitive_df["category"].unique(maintain_order=True).sort().to_list():
                sub = sensitive_df.filter(pl.col("category") == category).sort("gl_code")
                r = _write_section_header(ws, r, str(category).replace("_", " ").title())
                cat_rows = [[a["gl_code"], a["gl_name"], a["balance"], a["priority"]] for a in sub.to_dicts()]
                r = _write_table(ws, r, ["GL Code", "GL Name", "Closing", "Priority"], cat_rows,
                                 formats=[None, None, _ACC_FMT, None])
                r += 1
        _set_col_widths(ws, {"A": 26, "B": 40, "C": 18, "D": 12})

        # 9. Unmapped Accounts
        ws = new_sheet("Unmapped Accounts")
        _write_sheet_title(ws, "Unmapped Accounts", "GL codes with no matching entry in the grouping workbook", 3)
        unmapped_table_rows = [[u["gl_code"], u["gl_name"], u["closing_balance"]] for u in unmapped_rows]
        _write_table(ws, 4, ["GL Code", "GL Name", "Closing Balance"], unmapped_table_rows, formats=[None, None, _ACC_FMT])
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 10, "B": 46, "C": 18})

        # 10. Estimation Exposure -- TB-R12, independent of the dormant screen above:
        # every acquisition-cost/CWIP/exploration/impairment account by size, whether or
        # not it moved in the year.
        ws = new_sheet("Estimation Exposure")
        _write_sheet_title(ws, "Estimation Exposure",
                            "Acquisition cost / CWIP / exploration / impairment -- ranked by size, independent of movement", 5)
        est_accounts = (estimation_exposure or {}).get("accounts", [])
        est_rows = [
            [a["gl_code"], a["gl_name"], a["fs_head"], a["closing_balance"], "Yes" if a.get("moved_in_year") else "No"]
            for a in est_accounts
        ]
        _write_table(ws, 4, ["GL Code", "GL Name", "FS Head", "Closing Balance", "Moved in Year"],
                     est_rows, formats=[None, None, None, _ACC_FMT, None])
        ws.freeze_panes = "A5"
        _set_col_widths(ws, {"A": 10, "B": 46, "C": 16, "D": 20, "E": 14})

        # 11. FX Exposure -- TB-R11, only written when build_fx_exposure.py actually
        # screened (gated on entity_profile.json's has_foreign_ops flag).
        if (fx_exposure or {}).get("screened"):
            ws = new_sheet("FX Exposure")
            _write_sheet_title(ws, "Foreign-Exchange / Translation-Risk Exposure",
                                "Monetary items retranslated each period; non-monetary carried at historical rate (Ind AS 21)", 4)
            r = _write_section_header(ws, 4, "Monetary Items (ongoing translation exposure)")
            mon_rows = [[m["gl_code"], m["gl_name"], m["fs_head"], m["closing_balance"]] for m in fx_exposure.get("monetary_items", [])]
            r = _write_table(ws, r, ["GL Code", "GL Name", "FS Head", "Closing"], mon_rows, formats=[None, None, None, _ACC_FMT])
            r += 2
            r = _write_section_header(ws, r, "Non-Monetary Items (historical rate)")
            nonmon_rows = [[n["gl_code"], n["gl_name"], n["fs_head"], n["closing_balance"]] for n in fx_exposure.get("non_monetary_items", [])]
            r = _write_table(ws, r, ["GL Code", "GL Name", "FS Head", "Closing"], nonmon_rows, formats=[None, None, None, _ACC_FMT])
            r += 2
            r = _write_section_header(ws, r, "Translation / Exchange-Fluctuation Reserve Movement")
            trans_rows = [[t["gl_code"], t["gl_name"], t["opening_balance"], t["movement"], t["closing_balance"]] for t in fx_exposure.get("translation_reserve_movement", [])]
            _write_table(ws, r, ["GL Code", "GL Name", "Opening", "Movement", "Closing"], trans_rows,
                         formats=[None, None, _ACC_FMT, _ACC_FMT, _ACC_FMT])
            _set_col_widths(ws, {"A": 10, "B": 40, "C": 16, "D": 18, "E": 18})

        # 12. Mapping Quality -- TB-R19, surfaces grouping-taxonomy inconsistencies without
        # correcting them.
        mq_flags = (mapping_quality or {}).get("flags", [])
        if mq_flags:
            ws = new_sheet("Mapping Quality")
            _write_sheet_title(ws, "Grouping-Taxonomy Quality Flags",
                                "Surfaces inconsistencies in the grouping workbook as supplied -- does not correct them", 2)
            mq_rows = [[f.get("type", ""), f.get("detail", "")] for f in mq_flags]
            _write_table(ws, 4, ["Type", "Detail"], mq_rows, formats=[None, None])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 28, "B": 80})

        # 13. Legend -- every High/Medium/Low(/Critical/Information Request) scale used
        # anywhere in this workbook, with its thresholds and how it is computed. See
        # _write_legend_sheet's own docstring for why this exists as one sheet rather than
        # a caption on each individual sheet that uses one of these scales.
        ws = new_sheet("Legend")
        _write_sheet_title(ws, "Legend — Priority, Severity & Confidence Scales",
                            "Every classification scale ('High'/'Medium'/'Low', etc.) used anywhere in this "
                            "workbook, with its thresholds and how it is computed", 6)
        _write_legend_sheet(ws, 4)
        _set_col_widths(ws, {"A": 26, "B": 34, "C": 22, "D": 60, "E": 14, "F": 14})

        # ── Phase-2 populations (sec 9, 11, 12, 14, 15, 16, 17, 19, 20) ─────
        # Sections 17, 19 and 20 in the Word document carry only the top findings; these
        # sheets hold the complete populations the document points at. Findings & Queries
        # Register / Evidence Request Register are written first and separately -- they
        # need bespoke cross-referencing logic the generic _PHASE2_SHEETS loop below
        # doesn't (and shouldn't) provide for every other population.
        _write_findings_and_evidence_sheets(new_sheet, out_dir, load_json)
        _phase2_sheets(new_sheet, out_dir, load_json)

        # Row counts recorded from the worksheets themselves rather than tracked by
        # each call site -- one source, and it cannot drift from what was written.
        # Minus the 3-row title block _write_sheet_title lays down.
        resolved = []
        for name, _ in sheets_written:
            ws_actual = wb[name]
            resolved.append((name, max(ws_actual.max_row - 4, 0)))

        with atomic_write(excel_path) as tmp:
            wb.save(tmp)
        artifacts.append(str(excel_path.resolve()))

        manifest_path = write_manifest(out_dir, resolved, excel_path.name)
        artifacts.append(str(manifest_path.resolve()))

    except Exception as e:
        errors.append(log_and_redact_exception("build_excel_report", e, error_type="ExcelError"))
        return {"execution_status": "FAILED", "errors": errors, "message": "Failed to create Excel report."}

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": f"Excel report generated at {excel_path.name} with {len(sheets_written)} schedule(s); "
                   f"manifest written for Word cross-referencing.",
        "artifacts": artifacts,
        "errors": errors,
    }

def _rows_from(records, columns):
    """Flatten dict records to rows in a fixed column order, joining list values so a
    multi-value cell stays readable in a spreadsheet filter."""
    out = []
    for r in records:
        row = []
        for key in columns:
            v = r.get(key)
            if isinstance(v, (list, tuple)):
                v = "; ".join(str(x) for x in v)
            elif isinstance(v, dict):
                v = json.dumps(v, default=str)
            row.append(v)
        out.append(row)
    return out

def _write_findings_and_evidence_sheets(new_sheet, out_dir, load_json):
    """Consolidates what used to be 3 sheets -- Findings Register, Evidence Requests,
    Management Queries -- into 2:

    - "Findings & Queries Register": one row per finding (same grain as the old Findings
      Register), with each finding's own management query rendered inline in that SAME
      row (via _build_management_query -- a pure function of the finding record, so this
      is not a re-derivation, it is the single source of truth rendered where it belongs).
      Evidence items are tagged [EV-nnn]/[IR-nnn] against the Evidence Request Register's
      own IDs, so a reader can jump straight to the de-duplicated request instead of
      re-reading free text across two sheets.
    - "Evidence Request Register": the de-duplicated, audit-area-grouped evidence list
      (same content as the old Evidence Requests sheet), plus the information-request list
      (chart-of-accounts/scale confirmations) that had NO sheet of its own before -- each
      row carries the same stable ID Sheet 1 cross-references.

    Management queries are not folded into the Evidence Request Register: sec 13.1 requires
    management explanation held separately from evidence, so they stay in their own
    clearly-labelled columns on Sheet 1, never mixed into the de-duplicated request list.
    """
    fr_path = out_dir / "finding_records.json"
    if not fr_path.exists():
        return
    fr = load_json(None, "finding_records.json")
    records = (fr or {}).get("finding_records") or []
    if not records:
        return

    ev_path = out_dir / "evidence_request_list.json"
    ev_data = load_json(None, "evidence_request_list.json") if ev_path.exists() else {}
    evidence_requests = list((ev_data or {}).get("evidence_request_list") or [])
    information_requests = list((ev_data or {}).get("information_request_list") or [])

    id_lookup = {}
    for i, e in enumerate(evidence_requests, start=1):
        e["_id"] = f"EV-{i:03d}"
        id_lookup[_normalise(e.get("record"))] = e["_id"]
    for i, e in enumerate(information_requests, start=1):
        e["_id"] = f"IR-{i:03d}"
        id_lookup[_normalise(e.get("record"))] = e["_id"]

    def _format_requested_for(accounts, cap=3):
        """Remark #16 fix: a joined "GL1; GL2; GL3; ..." string is hard to work
        row-by-row when a finding legitimately spans many accounts. Show up to `cap`
        accounts plainly; beyond that, cap and say how many more rather than running
        every account together in one unreadable cell."""
        accounts = list(accounts or [])
        if len(accounts) <= cap:
            return "; ".join(accounts)
        return "; ".join(accounts[:cap]) + f"; +{len(accounts) - cap} more"

    def _tag_evidence(items):
        if not isinstance(items, (list, tuple)):
            return str(items) if items else ""
        tagged = []
        for it in items:
            eid = id_lookup.get(_normalise(it)) if isinstance(it, str) else None
            tagged.append(f"[{eid}] {it}" if eid else str(it))
        return "; ".join(tagged)

    # ---- Sheet 1: Findings & Queries Register ----
    ws = new_sheet("Findings & Queries Register")
    _write_sheet_title(
        ws, "Findings & Queries Register",
        "Every finding in full, risk-ranked. Management Query columns are populated only "
        "where a plausible innocent explanation exists to put to management -- NOT audit "
        "evidence (sec 13.1). Evidence items are tagged [EV-nnn]/[IR-nnn]: see the Evidence "
        "Request Register sheet for the de-duplicated, ready-to-send request list.", 15,
    )
    headers = ["Screen", "Account", "FSLI", "Amount", "Risk", "Regularity", "Observation",
               "Expectation", "Gap", "Assertions", "Risk basis", "Proposed response",
               "Evidence Requested", "Management Query", "Candidate Explanations"]

    def _cellify(v):
        """Not every finding-record field is guaranteed a plain string across every
        source screen -- some carry a list or dict. _rows_from applied this same
        defensive join/dump uniformly; this function does the same for hand-picked
        columns, which don't get that safety net for free."""
        if isinstance(v, (list, tuple)):
            return "; ".join(str(x) for x in v)
        if isinstance(v, dict):
            return json.dumps(v, default=str)
        return v

    rows = []
    for r in records:
        q = _build_management_query(r)
        rows.append([
            _cellify(r.get("source_screen")), _cellify(r.get("account")), _cellify(r.get("fsli")),
            _cellify(r.get("amount")), str(r.get("risk_rating", "")).upper(), r.get("regularity_flag"),
            _cellify(r.get("observation")), _cellify(r.get("expectation")), _cellify(r.get("gap")),
            _cellify(r.get("assertion")), _cellify(r.get("risk_basis")), _cellify(r.get("proposed_response")),
            _tag_evidence(r.get("evidence_requested")),
            q["query"] if q else "",
            _cellify(q["candidate_explanations"]) if q else "",
        ])
    _write_table(ws, 4, headers, rows, formats=[None] * len(headers))
    for i, r in enumerate(records):
        fill = _SEVERITY_FILL_XLSX.get(str(r.get("risk_rating", "")).upper())
        if fill:
            for col in range(1, len(headers) + 1):
                ws.cell(row=5 + i, column=col).fill = fill
    ws.freeze_panes = "A5"
    widths = {chr(ord("A") + i): 55 if h in ("Observation", "Expectation", "Gap", "Risk basis",
                                              "Proposed response", "Evidence Requested",
                                              "Management Query", "Candidate Explanations") else 16
              for i, h in enumerate(headers)}
    _set_col_widths(ws, widths)

    # ---- Sheet 2: Evidence Request Register ----
    ws2 = new_sheet("Evidence Request Register")
    _write_sheet_title(
        ws2, "Evidence Request Register",
        "De-duplicated, risk-ordered, grouped by audit area -- the ready-to-send request "
        "list. IDs cross-reference the Findings & Queries Register's Evidence Requested column.", 6,
    )
    r = _write_section_header(ws2, 4, "Evidence Requests (substantive)")
    r = _write_rows_grouped_by_fs_head(
        ws2, r, evidence_requests,
        ["ID", "Record to Obtain", "Highest Risk", "Regularity", "Requested For", "Assertions Resolved"],
        row_fn=lambda e: [e["_id"], e.get("record"), str(e.get("highest_risk_rating", "")).upper(),
                           "Yes" if e.get("regularity_relevant") else "No",
                           _format_requested_for(e.get("requested_for")),
                           "; ".join(e.get("assertions_resolved") or [])],
        head_key="audit_area",
    )
    r += 1
    r = _write_section_header(ws2, r, "Information Requests (data/scope confirmations — not audit findings)")
    if information_requests:
        info_rows = [
            [e["_id"], e.get("record"), str(e.get("highest_risk_rating", "")).upper(),
             _format_requested_for(e.get("requested_for"))]
            for e in information_requests
        ]
        _write_table(ws2, r, ["ID", "Record to Obtain", "Highest Risk", "Requested For"], info_rows)
    else:
        c = ws2.cell(row=r, column=1, value="None for this run.")
        c.font = Font(size=9, italic=True)
    ws2.freeze_panes = "A5"
    _set_col_widths(ws2, {"A": 10, "B": 55, "C": 14, "D": 12, "E": 40, "F": 30})

_PHASE2_SHEETS = [
    ("Assertion Map", "assertion_evidence_map.json", "assertion_evidence_map",
     "Account area to assertion to named record, replacing generic source-engine evidence",
     ["account", "account_area", "sensitive_tag", "severity", "closing_balance",
      "assertions", "evidence_requested", "primary_objective", "previous_generic_evidence"],
     ["Account", "Account area", "Sensitive tag", "Severity", "Closing balance",
      "Assertions", "Evidence requested", "Primary objective", "Previously (generic)"]),

    ("Counterpart Gaps", "counterpart_screen.json", "finding_records",
     "Expected pairs whose counterpart is absent or immaterial",
     ["relationship_id", "source_head", "source_balance", "target_balance",
      "counterpart_ratio", "risk_rating", "gap", "evidence_requested", "valid_reasons"],
     ["Relationship", "Source head", "Source balance", "Counterpart balance",
      "Ratio", "Risk", "Gap", "Evidence requested", "Legitimate reasons"]),

    ("Relationship Expectations", "relationship_expectations.json", "results",
     "Observed ratio against the plausible band, with the quantified gap",
     ["id", "name", "status", "observed_ratio", "expected_band", "formula", "reason"],
     ["ID", "Relationship", "Status", "Observed ratio", "Expected band", "Formula", "If not computed"]),

    ("Abnormal Signs", "abnormal_sign_screen.json", "finding_records",
     "Accounts on the opposite side to their class; contra accounts listed separately",
     ["account", "report_head", "amount", "normal_balance_expectation",
      "actual_balance_side", "account_area", "risk_rating", "valid_reasons", "evidence_requested"],
     ["Account", "Class", "Closing balance", "Normal side", "Actual side",
      "Account area", "Risk", "Legitimate explanations", "Evidence requested"]),

    ("Statutory Screen", "statutory_screen.json", "results",
     "GST, TDS and PF/ESI against the bases they arise from — no compliance conclusion",
     ["id", "label", "status", "base_balance", "dues_balance", "observed_ratio", "expected_band"],
     ["ID", "Levy relationship", "Status", "Base", "Dues", "Observed ratio", "Expected band"]),

    ("Public Sector Lens", "public_sector_lens.json", "finding_records",
     "The four regularity and propriety questions, by account",
     ["account", "regularity_question", "amount", "matched_terms", "risk_rating",
      "evidence_requested", "valid_reasons"],
     ["Account", "Regularity question", "Balance", "Matched terms", "Risk",
      "Evidence requested", "Legitimate reasons"]),

    ("CARO Indicators", "caro_indicators.json", "finding_records",
     "Indicators only — CARO applicability is not derivable from a trial balance",
     ["fsli", "clause_hint", "amount", "risk_rating", "applicability_confirmed",
      "observation", "evidence_requested"],
     ["CARO / Act area", "Clause hint", "Balance", "Rating", "Applicability confirmed",
      "Observation", "Evidence to request"]),

    ("Override Indicators", "override_indicators.json", "finding_records",
     "Planning-stage fraud-risk indicators — not a statement that fraud occurred",
     ["indicator", "account", "amount", "risk_rating", "observation", "gap",
      "evidence_requested", "valid_reasons"],
     ["Indicator", "Account", "Amount", "Risk", "Observation", "Gap",
      "Evidence requested", "Legitimate reasons"]),

    ("Going Concern", "going_concern_screen.json", "indicators",
     "Indicators only — no conclusion on the going-concern basis is drawn",
     ["indicator", "present", "amount", "net_worth", "borrowings", "cash",
      "cash_cover_ratio", "statutory_dues", "idle_funds", "government_support"],
     ["Indicator", "Present", "Amount", "Net worth", "Borrowings", "Cash",
      "Cash cover", "Statutory dues", "Idle funds", "Govt support"]),

    ("Audit Ratios", "audit_ratio_pack.json", None,
     "Every ratio traceable to both its components; a missing component is stated, not approximated",
     None, None),

    ("Sample Selection", "sample_selection.json", None,
     "SA 530 sampling -- the one screen in this report that tests a sample, not the full population; "
     "every parameter is recorded so the sample is reproducible from this sheet alone",
     None, None),
]

def _phase2_sheets(new_sheet, out_dir, load_json):
    """Write one sheet per Phase-2 population that has a backing artifact.

    A screen that did not run contributes no sheet, and therefore no manifest entry,
    and therefore no dangling reference in the Word document.
    """
    for name, filename, key, subtitle, columns, headers in _PHASE2_SHEETS:
        if not (out_dir / filename).exists():
            continue
        data = load_json(None, filename)
        if not isinstance(data, dict):
            continue

        if name == "Audit Ratios":
            ratios = data.get("ratios") or {}
            if not ratios:
                continue
            rows = []
            for rk, r in ratios.items():
                num, den = r.get("numerator") or {}, r.get("denominator") or {}
                basis_note = "Fallback denominator used (see Formula)" if r.get("used_fallback_denominator") else ""
                if r.get("provisional"):
                    basis_note = (basis_note + "; " if basis_note else "") + "Ledger-name match, not FSLI hierarchy -- indicative"
                rows.append([
                    rk.replace("_", " ").title(),
                    r.get("value_pct") or "not computed",
                    r.get("formula"),
                    num.get("head"), num.get("balance"),
                    den.get("head"), den.get("balance"),
                    basis_note,
                    "; ".join(r.get("components_missing") or []) or "—",
                    r.get("audit_meaning"),
                ])
            ws = new_sheet(name)
            _write_sheet_title(ws, "Audit-Analytical Ratios", subtitle, 10)
            _write_table(ws, 4, ["Ratio", "Value", "Formula", "Numerator head",
                                 "Numerator balance", "Denominator head",
                                 "Denominator balance", "Basis Note", "Missing", "What it means"],
                         rows, formats=[None, None, None, None, _ACC_FMT, None, _ACC_FMT, None, None, None])
            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 24, "B": 14, "C": 55, "D": 28, "E": 18,
                                 "F": 28, "G": 18, "H": 36, "I": 20, "J": 70})
            continue

        if name == "Sample Selection":
            if not data.get("methods_run"):
                continue
            ws = new_sheet(name)
            _write_sheet_title(ws, "SA 530 Sample Selection", subtitle, 6)
            r = 4
            c = ws.cell(row=r, column=1, value=data.get("methodology", ""))
            c.font = Font(size=9, italic=True)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.row_dimensions[r].height = 45
            r += 2

            mus = data.get("mus")
            if mus:
                r = _write_section_header(ws, r, "Monetary Unit Sample (MUS / PPS)")
                param_note = (
                    f"Confidence {mus['confidence_level']:.0%} (reliability factor {mus['reliability_factor']}), "
                    f"Performance Materiality {mus['performance_materiality']:,.0f}, sampling interval "
                    f"{mus['sampling_interval']:,.0f}, random start {mus['random_start']:,.0f} (seed {mus['seed']}). "
                    f"Population: {mus['population_count']} account(s), value {mus['population_value']:,.0f}. "
                    f"{mus['sample_size']} item(s) selected, {mus['certainty_items']} with certainty "
                    "(own balance exceeds the sampling interval)."
                )
                if mus.get("reliability_factor_note"):
                    param_note += " " + mus["reliability_factor_note"]
                c = ws.cell(row=r, column=1, value=param_note)
                c.font = Font(size=9, italic=True)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
                ws.row_dimensions[r].height = 45
                r += 1
                mus_rows = [
                    [m["gl_code"], m["gl_name"], m["closing_balance"], m["cumulative_value_at_selection"],
                     "Yes" if m["selected_with_certainty"] else "No", m["hits"]]
                    for m in mus["sampled_items"]
                ]
                r = _write_table(ws, r, ["GL Code", "GL Name", "Closing Balance", "Cumulative Value at Selection",
                                          "Certainty Item", "Times Hit"],
                                  mus_rows, formats=[None, None, _ACC_FMT, _ACC_FMT, None, None])
                r += 2

            strat = data.get("stratified")
            if strat:
                r = _write_section_header(ws, r, "Stratified Random Sample")
                c = ws.cell(
                    row=r, column=1,
                    value=(
                        f"{strat['sample_pct_per_stratum']:.0%} of each stratum, seed {strat['seed']}. Strata are "
                        "the same Critical/High/Medium/Low/Below-Threshold bands the Materiality sheet's Priority "
                        "scale uses (see the Legend sheet)."
                    ),
                )
                c.font = Font(size=9, italic=True)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
                r += 1
                for stratum_name, s in strat["strata"].items():
                    r = _write_section_header(ws, r, f"{stratum_name} ({s['sample_size']} of {s['population_count']})")
                    strat_rows = [[i["gl_code"], i["gl_name"], i["closing_balance"]] for i in s["sampled_items"]]
                    r = _write_table(ws, r, ["GL Code", "GL Name", "Closing Balance"], strat_rows, formats=[None, None, _ACC_FMT])
                    r += 1

            combined_total = data.get("total_sampled_items_mus_plus_stratified")
            if combined_total is not None:
                c = ws.cell(
                    row=r, column=1,
                    value=(
                        f"Combined total sampled: {combined_total} item(s) (MUS + Stratified). "
                        "MUS and stratified are two independent SA 530 techniques with their own "
                        "valid sample sizes -- cite this combined figure, not either method's size "
                        "alone, when a single \"total sampled\" number is needed."
                    ),
                )
                c.font = Font(size=9, italic=True, bold=True)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
                ws.row_dimensions[r].height = 30
                r += 1

            ws.freeze_panes = "A5"
            _set_col_widths(ws, {"A": 14, "B": 40, "C": 20, "D": 26, "E": 14, "F": 12})
            continue

        records = data.get(key) or []
        if not records:
            continue
        rows = _rows_from(records, columns)
        ws = new_sheet(name)
        _write_sheet_title(ws, name, subtitle, len(headers))
        _write_table(ws, 4, headers, rows, formats=[None] * len(headers))
        ws.freeze_panes = "A5"
        widths = {}
        for i, h in enumerate(headers):
            col = chr(ord("A") + i) if i < 26 else "A" + chr(ord("A") + i - 26)
            widths[col] = 55 if h in ("Observation", "Expectation", "Gap", "Query",
                                      "Evidence requested", "Valid reasons",
                                      "Legitimate reasons", "Legitimate explanations",
                                      "Proposed response", "Candidate explanations",
                                      "Corroborating evidence required",
                                      "Record to obtain", "What it means",
                                      "Primary objective", "Evidence to request") else 20
        _set_col_widths(ws, widths)


def _safe_json(path):
    """Degrade-gracefully load. A summary must still render when an upstream artifact
    is absent -- the section it feeds simply does not appear."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None

def _md_table(headers, rows) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines) + "\n"

@pipeline_tool("build_report_markdown", domain="reports")
def build_report_markdown(
    output_dir: str = None,
    canonical_tb_file: str = None,
    tb_metadata_file: str = None,
    processing_manifest_file: str = None,
    mapping_summary_file: str = None,
    materiality_file: str = None,
    sensitive_accounts_file: str = None,
    consolidated_exceptions_file: str = None,
    audit_reasoning_file: str = None,
    fsli_summary_file: str = None,
    estimation_exposure_file: str = None,
    fx_exposure_file: str = None,
    mapping_quality_file: str = None,
    **kwargs,
) -> dict:
    """Renders the Single-TB report content as a markdown string in data.markdown."""
    errors = []

    out_dir = resolve_output_dir(output_dir)

    def load_json(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            errors.append({"type": "MissingDataWarning", "message": f"File {p.name} not found."})
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return None

    def load_parquet(explicit_path, default_filename):
        p = Path(explicit_path) if explicit_path else out_dir / default_filename
        if not p.exists():
            return None
        try:
            return pl.read_parquet(p)
        except Exception as e:
            errors.append({"type": "ParseError", "file": p.name, "error": str(e)})
            return None

    manifest = load_json(processing_manifest_file, "processing_manifest.json") or {}
    tb_meta = load_json(tb_metadata_file, "tb_metadata.json") or {}
    mapping_summary = load_json(mapping_summary_file, "mapping_summary.json") or {}
    materiality = load_json(materiality_file, "materiality.json") or {}
    sensitive_accounts = load_json(sensitive_accounts_file, "sensitive_accounts.json") or {}
    exceptions_db = load_json(consolidated_exceptions_file, "consolidated_exceptions.json") or {}
    audit_reasoning = load_json(audit_reasoning_file, "audit_reasoning.json") or {}
    fsli_df = load_parquet(fsli_summary_file, "fsli_summary.parquet")
    estimation_exposure = load_json(estimation_exposure_file, "estimation_exposure.json") or {}
    fx_exposure = load_json(fx_exposure_file, "fx_exposure.json") or {}
    mapping_quality = load_json(mapping_quality_file, "mapping_quality.json") or {}
    canonical_path = Path(canonical_tb_file) if canonical_tb_file else out_dir / "canonical_tb.parquet"
    canonical_df = load_canonical_tb(canonical_path)

    sev_rank = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Information Request": 0}

    thr = materiality.get("thresholds", {})
    om = thr.get("overall", 0.0)
    perf_mat = thr.get("performance", 0.0)
    triv = thr.get("clearly_trivial", 0.0)
    summ = materiality.get("summary", {})
    sens_cat = sensitive_accounts.get("category_summary", {})
    susp = sens_cat.get("suspense_control", {})

    entity_label, _fy_label, source_file = resolve_entity_and_fy(tb_meta, manifest)

    # TB-R01/R02: len(canonical_df) is the single source of truth for row/account count --
    # mapping_summary.json is absent for every DB-sourced run (upload-path only), which
    # previously silently defaulted every count to 0. See build_docx_report.py for the
    # identical fix.
    total_rows = canonical_row_count(canonical_df) if canonical_df is not None else mapping_summary.get("total_tb_rows", 0)
    # TB-R03: see the identical fix + note in build_docx_report — mapping_summary.json
    # is never written by the current pipeline, so derive mapped/unmapped directly
    # from canonical_df's own mapped_status column instead.
    if canonical_df is not None and canonical_df.height > 0 and "mapped_status" in canonical_df.columns:
        mapped_rows = int((canonical_df["mapped_status"] == MAPPED_STATUS_MAPPED).sum())
        unmapped_rows = total_rows - mapped_rows
    else:
        has_mapping_summary = bool(mapping_summary)
        unmapped_rows = (
            mapping_summary.get("unmapped_rows", max(total_rows - mapping_summary.get("mapped_rows", 0), 0))
            if has_mapping_summary else None
        )

    ctrl = control_totals(canonical_df)
    movement_rows = movement_rows_from_canonical(canonical_df, om, perf_mat)
    conc_rows = concentration_rows_from_canonical(canonical_df, clearly_trivial=triv)
    dormant_rows = dormant_rows_from_canonical(canonical_df)
    sign_stats = sign_convention_stats(canonical_df)
    clusters = exceptions_db.get("clusters", [])
    clusters_by_id = {c.get("cluster_id"): c for c in clusters}
    observations = [o.get("observation", {}) for o in audit_reasoning.get("audit_observations", [])]
    observations.sort(key=lambda o: sev_rank.get(o.get("priority", "Low"), 0), reverse=True)

    # Read by both the Executive Summary and Data Quality sections below.
    balanced = bool(ctrl) and abs(ctrl["difference"]) < 1.0

    lines = []
    a = lines.append
    def _render_title():
        a(f"# Trial Balance Analysis Report — {entity_label}")
        a("")
        a("> Mechanical, evidence-based analytical review derived solely from the trial balance and "
          "grouping workbook supplied. No audit opinion; risk-assessment and planning aid only.")
        a("")

    _render_title()
    def _render_executive_summary():
        a("## Executive Summary")
        if ctrl:
            a(
                f"The {entity_label} trial balance comprises {total_rows} GL account(s). "
                + ("It is internally self-balanced — debit and credit control totals match exactly, "
                   f"with {ctrl['duplicate_gl_codes']} duplicate GL code(s) and {ctrl['fully_zero_rows']} "
                   "fully-zero row(s) — so the classification and risk analytics below rest on a sound "
                   "base rather than a data-quality artefact." if balanced else
                   f"Control totals differ by {safe_fmt(ctrl['difference'])} — see Data Quality below.")
            )
        else:
            a(f"The {entity_label} trial balance comprises {total_rows} GL account(s).")
        a(
            "Because only one period is available, this review focuses on what the trial balance shows "
            "about itself: composition, concentration, within-year movement, and account-level risk "
            "flags, rather than year-on-year change."
        )
        a("")
        highlights = [movement_rows, conc_rows, dormant_rows, sign_stats, sens_cat]
        highlight_count = sum(1 for x in highlights if x)
        a(f"**{NUMBER_WORDS.get(highlight_count, str(highlight_count))} things stand out:**" if highlight_count else "**Key observations:**")
        if movement_rows:
            a(f"- **In-year movement** — {len(movement_rows)} Balance Sheet account(s) moved by more than "
              "performance materiality within the year, once Revenue/Expense accounts are set aside.")
        if conc_rows:
            top = conc_rows[0]
            a(f"- **Concentration** — {top['gl_name']} alone is {top['pct']:.1f}% of total {top['fs_head']} "
              f"— {len(conc_rows)} account(s) individually exceed 5% of their FS Head.")
        if dormant_rows:
            a(f"- **Dormant balances** — {len(dormant_rows)} account(s) show no debit/credit activity all "
              "year despite a non-trivial closing balance.")
        if sign_stats:
            a(f"- **Sign convention** (on a {sign_stats.get('basis', SIGN_CONVENTION_STATS_BASIS_LABEL)}) — "
              f"{sign_stats['debit_flip']} of {sign_stats['debit_total']} "
              f"Asset/Expense account(s) ({sign_stats['debit_flip_pct']}%) and {sign_stats['credit_flip']} "
              f"of {sign_stats['credit_total']} Liability/Equity/Revenue account(s) "
              f"({sign_stats['credit_flip_pct']}%) sit opposite their FS Head's normal balance side.")
        if susp.get("count"):
            a(f"- **Suspense/control exposure** — {safe_fmt(susp.get('total_balance', 0))} combined "
              f"balance across {susp['count']} account(s).")
        a("")

    _render_executive_summary()

    def _render_data_quality():
        a("## Data Quality & Control Totals")
        if ctrl:
            a(_md_table(
                ["Check", "Value"],
                [
                    ("Total Debit (closing-balance foot)", safe_fmt(ctrl["total_debit"])),
                    ("Total Credit (closing-balance foot)", safe_fmt(ctrl["total_credit"])),
                    ("Debit − Credit Difference", safe_fmt(ctrl["difference"])),
                    ("Sum of Closing Balances (tie-out)", f"{ctrl['sum_closing']:,.6f}"),
                    ("Turnover — Debit reporting period (Dr rept. period)", safe_fmt(ctrl["turnover_total_debit"])),
                    ("Turnover — Credit reporting period (Cr rept. period)", safe_fmt(ctrl["turnover_total_credit"])),
                    ("Duplicate GL Codes", str(ctrl["duplicate_gl_codes"])),
                    ("Fully Zero Rows", str(ctrl["fully_zero_rows"])),
                ],
            ))
            a(
                "Implication: the underlying trial balance is internally consistent before any grouping "
                "logic is applied." if balanced else
                "Implication: the control-total mismatch should be resolved before the downstream "
                "analytics in this report are relied upon."
            )
        else:
            a("Control totals could not be computed — the canonical trial balance was not available.")
        if unmapped_rows:
            a(f"\nUnmapped accounts: {unmapped_rows} account(s) did not match a distinct entry in the "
              "grouping workbook.")
        a("")

    _render_data_quality()

    def _render_materiality():
        a("## Provisional Planning Materiality")
        if materiality:
            a(_md_table(
                ["Basis", "Value"],
                [
                    ("Materiality Basis Used", summ.get("benchmark_used", "Unknown")),
                    ("Overall Materiality", safe_fmt(om)),
                    ("Performance Materiality (75% of OM)", safe_fmt(perf_mat)),
                    ("Clearly Trivial Threshold", safe_fmt(triv)),
                ],
            ))
            # TB-R09: render the winning candidate's selection rationale, already in the spine
            # file but previously never read.
            benchmark_analysis = materiality.get("benchmark_analysis", [])
            chosen_candidate = next(
                (c for c in benchmark_analysis if c.get("benchmark") == summ.get("benchmark_used")), {}
            )
            rationale = chosen_candidate.get("reason")
            if rationale:
                a(f"*Basis rationale: {rationale}*")
            a("*Provisional only — a mechanical benchmark-based calculation, not a substitute for "
              "engagement-level materiality judgment.*")
        else:
            a("Materiality could not be computed — materiality.json was not available.")
        a("")

    _render_materiality()

    def _render_fsli_summary():
        a("## FSLI Summary")
        if fsli_df is not None and not fsli_df.is_empty() and "main_head" in fsli_df.columns:
            lvl1 = fsli_df
            if "hierarchy_level" in lvl1.columns:
                lvl1 = lvl1.filter(pl.col("hierarchy_level") == 1)
            if "closing_balance" in lvl1.columns:
                lvl1 = lvl1.with_columns(pl.col("closing_balance").abs().alias("_abs")).sort("_abs", descending=True)
            a(_md_table(
                ["FS Head", "GL Accounts", "Closing Balance"],
                [
                    (
                        r.get("main_head") or r.get("sub_head_1") or "",
                        int(r.get("gl_accounts") or r.get("descendant_gl_count") or r.get("leaf_gl_count") or 0),
                        safe_fmt(r.get("closing_balance", 0.0)),
                    )
                    for r in lvl1.iter_rows(named=True)
                ],
            ))
        else:
            a("FSLI summary not available.")
        a("")

        # TB-R19: surfaces grouping-taxonomy inconsistencies -- does not correct the grouping.
        mq_flags = mapping_quality.get("flags", [])
        if mq_flags:
            a("**Grouping-taxonomy quality flags**")
            a(_md_table(["Type", "Detail"], [(f.get("type", ""), f.get("detail", "")) for f in mq_flags]))
            a(
                "These flag possible inconsistencies in the grouping workbook as supplied — the "
                "grouping itself is not altered; confirm with the entity whether each flagged item "
                "is intentional."
            )
            a("")

    _render_fsli_summary()

    def _render_exception_summary():
        a("## Exception Summary")
        if clusters:
            ranked_clusters = sorted(clusters, key=lambda c: sev_rank.get(c.get("cluster_severity", "Low"), 0), reverse=True)
            # TB-R07: render the real cluster_id, never a re-minted EXC-xxx sequence.
            a(_md_table(
                ["ID", "Cluster", "Severity", "Accounts", "Combined Amount"],
                [
                    (c.get("cluster_id", f"CL-{i:03d}"), c.get("cluster_name", "Unnamed"), str(c.get("cluster_severity", "Low")).upper(), str(c.get("member_count", 0)), safe_fmt(c.get("total_balance", 0.0)))
                    for i, c in enumerate(ranked_clusters, start=1)
                ],
            ))
        else:
            a("No exception clusters were generated deterministically for this dataset.")
        a("")

    _render_exception_summary()

    def _render_balance_sheet_movement():
        a("## Balance Sheet In-Year Movement (Opening → Closing)")
        if movement_rows:
            a(f"{len(movement_rows)} Balance Sheet account(s) moved materially within the year.")
            a(_md_table(
                ["GL Code", "GL Name", "FS Head", "Opening", "Closing", "Movement", "Flag"],
                [(m["gl_code"], m["gl_name"], m["fs_head"], safe_fmt(m["opening"]), safe_fmt(m["closing"]), safe_fmt(m["movement"]), m["flag"]) for m in movement_rows],
            ))
            a("Recommended procedure: obtain management's explanation and the supporting schedule for "
              "each flagged account above, prioritising CRITICAL-flagged balances first.")
        else:
            a("No Balance Sheet accounts moved by more than performance materiality within the year.")
        a("")

    _render_balance_sheet_movement()

    def _render_concentration_and_dormant():
        a("## Concentration & Dormant Balances")
        a("**Concentration — accounts exceeding 5% of their FS Head**")
        if conc_rows:
            a(_md_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "% of FS Head", "Note"],
                [(c["gl_code"], c["gl_name"], c["fs_head"], safe_fmt(c["closing"]), f"{c['pct']:.1f}%", c.get("note", "")) for c in conc_rows],
            ))
        else:
            a("No account individually exceeds 5% of its FS Head in this dataset.")
        a("")
        a("**Dormant accounts (no debit/credit movement in year) with material closing balance**")
        if dormant_rows:
            a(_md_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                [(d["gl_code"], d["gl_name"], d["fs_head"], safe_fmt(d["closing"])) for d in dormant_rows],
            ))
            # TB-R15: estimation-prone balances never get the "structurally expected" framing.
            estimation_risk = [d for d in dormant_rows if d.get("is_estimation_risk")]
            if estimation_risk:
                a(
                    "**Audit concern (estimation-prone accounts — do not treat as structurally "
                    "expected):** " + ", ".join(f"{d['gl_name']} ({safe_fmt(d['closing'])})" for d in estimation_risk)
                    + ". These rest on management estimate/judgment (acquisition cost, CWIP, "
                    "exploration, or impairment) rather than a transactable price -- obtain the "
                    "impairment assessment or capitalisation basis regardless of size or tenure."
                )
        else:
            a("No dormant accounts with a material closing balance were identified in this dataset.")
        a("")

        a("**Estimation exposure — acquisition cost / CWIP / exploration / impairment**")
        est_accounts = estimation_exposure.get("accounts", [])
        if est_accounts:
            a(_md_table(
                ["GL Code", "GL Name", "FS Head", "Closing Balance", "Moved in Year"],
                [(a_["gl_code"], a_["gl_name"], a_["fs_head"], safe_fmt(a_["closing_balance"]), "Yes" if a_.get("moved_in_year") else "No") for a_ in est_accounts],
            ))
            a(
                f"{len(est_accounts)} account(s) carry a balance resting on management estimate/judgment, "
                f"totalling {safe_fmt(estimation_exposure.get('summary', {}).get('total_exposure', 0))}. "
                "None of these can be corroborated from the trial balance alone regardless of whether "
                "they moved this year."
            )
        else:
            a("No acquisition-cost/CWIP/exploration/impairment accounts were identified in this dataset.")
        a("")

    _render_concentration_and_dormant()

    def _render_sign_convention():
        a("## Sign Convention Consistency")
        if sign_stats:
            a(
                f"Basis: {sign_stats.get('basis', SIGN_CONVENTION_STATS_BASIS_LABEL)} "
                "(see the separate TB-000/anchor-keyword screen elsewhere in this report for a "
                "narrower, name-based check).\n\n"
                f"Observed: {sign_stats['debit_flip']} of {sign_stats['debit_total']} Asset/Expense "
                f"account(s) ({sign_stats['debit_flip_pct']}%) carry a credit closing balance, and "
                f"{sign_stats['credit_flip']} of {sign_stats['credit_total']} Liability/Equity/Revenue "
                f"account(s) ({sign_stats['credit_flip_pct']}%) carry a debit closing balance. This is "
                "more consistent with a structural chart-of-accounts feature (contra-asset, provision, "
                "or clearing accounts) than a systemic classification problem."
            )
        else:
            a("Sign convention could not be assessed — FS Head classification was not available.")
        a("")

    _render_sign_convention()

    def _render_sensitive_accounts():
        a("## Sensitive Account Categories")
        if sens_cat:
            a(_md_table(
                ["Category", "Accounts", "Combined Closing Balance"],
                [(k.replace("_", " "), str(v.get("count", 0)), safe_fmt(v.get("total_balance", 0))) for k, v in sens_cat.items()],
            ))
            a("These are name-pattern screens, not confirmed classifications, and should be verified "
              "individually.")
        else:
            a("No sensitive-account categories were flagged in this dataset.")
        a("")

    _render_sensitive_accounts()

    def _render_fx_exposure():
        if fx_exposure.get("screened"):
            a("## Foreign-Exchange / Translation-Risk Exposure")
            monetary_items = fx_exposure.get("monetary_items", [])
            non_monetary_items = fx_exposure.get("non_monetary_items", [])
            translation_rows = fx_exposure.get("translation_reserve_movement", [])
            if monetary_items:
                a("**Monetary items (retranslated each period — ongoing exposure)**")
                a(_md_table(
                    ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                    [(m["gl_code"], m["gl_name"], m["fs_head"], safe_fmt(m["closing_balance"])) for m in monetary_items],
                ))
                a(
                    f"{len(monetary_items)} monetary FCY account(s) totalling "
                    f"{safe_fmt(fx_exposure.get('summary', {}).get('total_monetary_exposure', 0))} combined exposure."
                )
            if non_monetary_items:
                a("**Non-monetary items (historical rate — no ongoing translation exposure)**")
                a(_md_table(
                    ["GL Code", "GL Name", "FS Head", "Closing Balance"],
                    [(n["gl_code"], n["gl_name"], n["fs_head"], safe_fmt(n["closing_balance"])) for n in non_monetary_items],
                ))
            if translation_rows:
                a("**Translation / exchange-fluctuation reserve movement**")
                a(_md_table(
                    ["GL Code", "GL Name", "Opening", "Movement", "Closing"],
                    [(t["gl_code"], t["gl_name"], safe_fmt(t["opening_balance"]), safe_fmt(t["movement"]), safe_fmt(t["closing_balance"])) for t in translation_rows],
                ))
            a("")

    _render_fx_exposure()

    def _render_focus_areas():
        a("## Focus Areas")
        if observations:
            # TB-QA-Issue-C: disclose it if build_audit_reasoning.py's max_observations cap
            # was ever hit -- the Exception Summary section above always shows the full,
            # uncapped cluster list.
            eligible_cluster_count = len([c for c in clusters if str(c.get("cluster_severity", "Low")) in ("Critical", "High", "Medium")])
            if len(observations) < eligible_cluster_count:
                a(
                    f"*Showing the top {len(observations)} of {eligible_cluster_count} "
                    "Medium-or-higher-severity clusters by severity; see Exception Summary "
                    "above for the complete list.*"
                )
                a("")
            for i, obs in enumerate(observations, start=1):
                sev = str(obs.get("priority", "MEDIUM")).upper()
                title = obs.get("title", "Untitled observation")
                cluster = clusters_by_id.get(obs.get("cluster_id"), {})
                # TB-R07: real cluster_id, not a re-minted F01/F02 sequence.
                ref_id = obs.get("cluster_id") or f"F{i:02d}"
                a(f"### {ref_id} [{sev}] {title}")
                a(f"- **Observation and Gap:** {obs.get('detailed_observation') or obs.get('executive_summary', '')}")
                assertions = ", ".join(obs.get("affected_assertions", []))
                a(f"- **Assertions/Risk Basis:** {assertions}. Risk Rating: {sev}")
                evidence = obs.get("supporting_evidence") or ", ".join(cluster.get("risk_themes", []))
                a(f"- **Evidence Requested:** {evidence}")
                a(f"- **Calculation basis:** {cluster.get('member_count', 0)} ledger(s), combined "
                  f"{safe_fmt(cluster.get('total_balance', 0.0))} vs provisional materiality {safe_fmt(om)}")
                # TB-R03: conditions/rule-names as their own line, never folded into the
                # Observation/Gap narrative sentence above.
                conditions = obs.get("conditions_evaluated") or []
                if conditions:
                    a(f"- **Conditions Evaluated:** {', '.join(conditions)}")
        elif clusters:
            ranked_clusters = sorted(clusters, key=lambda c: sev_rank.get(c.get("cluster_severity", "Low"), 0), reverse=True)
            for i, c in enumerate(ranked_clusters, start=1):
                sev = str(c.get("cluster_severity", "LOW")).upper()
                themes = ", ".join(c.get("risk_themes", []))
                ref_id = c.get("cluster_id") or f"F{i:02d}"
                a(f"### {ref_id} [{sev}] {c.get('cluster_name', 'Unknown Cluster')}")
                a(f"- **Observation and Gap:** {c.get('member_count', 0)} ledger(s) flagged under {themes}, "
                  f"combined net {safe_fmt(c.get('total_balance', 0))}. TB cannot establish conditions or "
                  "reconciliation.")
                a(f"- **Assertions/Risk Basis:** {themes}. Risk Rating: {sev}")
                a("- **Evidence Requested:** account-wise reconciliation, ledger dump, supporting schedule")
                a(f"- **Calculation basis:** {c.get('member_count', 0)} ledger(s), combined "
                  f"{safe_fmt(c.get('total_balance', 0))} vs provisional materiality {safe_fmt(om)}")
        else:
            a("No exception clusters were generated for this dataset.")
        a("")

    _render_focus_areas()

    def _render_focus_sequence():
        a("## Suggested Audit Focus Sequence")
        seq_items = observations if observations else clusters
        seq_lines = []
        for item in seq_items:
            sev = str(item.get("priority") or item.get("cluster_severity", "LOW")).upper()
            if sev not in ("CRITICAL", "HIGH", "MEDIUM"):
                continue
            name = item.get("title") or item.get("cluster_name", "")
            reason = item.get("conclusion") or item.get("executive_summary") or ""
            if not reason and "cluster_name" in item:
                # TB-R04: clusters fallback has no conclusion/executive_summary field --
                # backfill a real summary instead of leaving the trailing ": " empty.
                themes = ", ".join(item.get("risk_themes", []))
                reason = f"{item.get('member_count', 0)} ledger(s) flagged under {themes}, combined {safe_fmt(item.get('total_balance', 0))}."
            seq_lines.append(f"- {name} — {sev}-rated finding: {reason}" if reason else f"- {name} — {sev}-rated finding.")
        a("\n".join(seq_lines) if seq_lines else "No High/Medium-rated findings to sequence for audit focus.")
        a("")

    _render_focus_sequence()

    def _render_full_deliverables():
        # ── where the full deliverables are ──────────────────────────────────────
        # This digest is relayed verbatim into the chat window; the Word document carries
        # all 25 sections and the workbook carries the complete populations. Naming them
        # here is what stops a reader treating the digest as the whole report.
        fr = _safe_json(out_dir / "finding_records.json")
        ev = _safe_json(out_dir / "evidence_request_list.json")
        manifest = _safe_json(out_dir / "excel_schedule_manifest.json")
        if fr or ev or manifest:
            a("## Full Deliverables")
            if fr:
                summ = fr.get("summary", {})
                by = summ.get("by_risk_rating", {})
                a(f"- **{summ.get('total_records', 0)} finding(s)** across "
                  f"{len(summ.get('contributing_screens', []))} screen(s): "
                  f"{by.get('high', 0)} high, {by.get('medium', 0)} medium, "
                  f"{by.get('information_request', 0)} information request; "
                  f"{summ.get('regularity_flagged', 0)} regularity-flagged.")
            if ev:
                esum = ev.get("summary", {})
                a(f"- **{esum.get('evidence_requests', 0)} de-duplicated evidence request(s)** "
                  f"across {esum.get('audit_areas', 0)} audit area(s).")
            if manifest and manifest.get("sheets"):
                a(f"- Word report: all 25 sections. Workbook `{manifest.get('workbook')}`: "
                  f"{len(manifest['sheets'])} schedule(s) holding the complete populations "
                  "this summary caps.")
            a("")

    _render_full_deliverables()

    def _render_limitations():
        a("## Limitations & No-Opinion Statement")
        a("This review is mechanical and confined entirely to the trial balance file and the grouping "
          "workbook supplied:")
        a("- No assurance opinion — it does not constitute an audit, review, or any other assurance "
          "engagement under any professional standard.")
        a("- No corroboration performed — no vouching, confirmation, analytical corroboration against "
          "external data, or management inquiry was performed.")
        a("- Grouping not independently verified — classifications rely on the GL Grouping workbook as "
          "supplied and have not been independently verified against statutory financial statements.")
        a("- Single-period scope — with only one period available, this review cannot distinguish "
          "ordinary year-on-year change from a genuine anomaly.")
        a("- No rescaling — figures are presented exactly as extracted from source, unscaled.")
        a("")
        a("*This document should be read alongside, not instead of, full substantive audit procedures.*")

    _render_limitations()

    markdown = "\n".join(lines)
    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": "Rendered Single-TB report content as markdown.",
        "data": {"markdown": markdown},
        "artifacts": [],
        "errors": errors,
    }




