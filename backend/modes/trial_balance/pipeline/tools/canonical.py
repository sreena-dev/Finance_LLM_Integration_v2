import datetime
import json
import os
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

# NOTE: build_canonical_tb / _apply_native_engine_classification / _detect_grouping_shape
# (the old manifest + grouping-ground-truth-parquet join path) were retired -- both live
# and uploaded documents now go through the single ingest_tb_to_live entry point
# (backend/tools/db_bridge.py), which parses (input_dispatch.parse_tb_input) and
# classifies (classify_document.classify_tb_document) TB+Grouping files directly and
# writes canonical_tb.parquet + LIVE tables in one call. See db_bridge.py's own
# docstring for why: TB-v2-git's own upload flow was outdated/stale relative to
# TB_normalization_v1's parsing/classification standard, and having two separate code
# paths reach the same engine through different (and non-equivalent) evidence shapes
# was the root cause of a real bug (grouping hint_text silently dropped on the upload
# path). validate_layer1_tb / build_data_sufficiency_grade / build_normalisation_note
# below are unaffected -- they consume canonical_tb_file, produced by either
# ingest_tb_to_live or load_tb_from_db, unchanged.


GRADE_INFO_REQUEST = "Information request only"

GRADE_LOW = "Low"

GRADE_MEDIUM = "Medium"

GRADE_HIGH = "High"

@pipeline_tool("build_data_sufficiency_grade", domain="canonical")
def build_data_sufficiency_grade(
    layer1_results_file: str,
    canonical_tb_file: str = None,
    comparative_data_present: bool = False,
    output_dir: str = None,
) -> dict:
    """Grade Trial Balance data sufficiency (Low/Medium/High/Information request only) and list weakened/unavailable checks."""
    results_path = Path(layer1_results_file)
    if not results_path.exists():
        raise PipelineFileError(str(results_path), "Layer-1 validation results")

    with open(results_path, "r", encoding="utf-8") as f:
        layer1_results = json.load(f)

    halted_blocking = [r for r in layer1_results if r.get("status") == "HALTED" and r.get("severity") == "Blocking"]
    skipped = [r for r in layer1_results if r.get("status") == "SKIPPED"]
    # NOT_IMPLEMENTED rules (e.g. TB-006/007/008/014, which need an approved chart-of-accounts
    # master this pipeline doesn't ingest from any source) are deliberately excluded from
    # `skipped`/`disabled_checks` -- they are never fixable by supplying more data for THIS
    # dataset, unlike a genuine per-session SKIP, so listing them the same way would mislead
    # a reader into thinking another upload would resolve them. Counted separately instead;
    # the full detail lives in the Data Quality sheet's Rule Register.
    not_implemented = [r for r in layer1_results if r.get("status") == "NOT_IMPLEMENTED"]

    mapped_pct = None
    if canonical_tb_file:
        canon_path = Path(canonical_tb_file)
        if canon_path.exists():
            canon_df = pl.read_parquet(canon_path)
            if not canon_df.is_empty() and "mapped_status" in canon_df.columns:
                mapped_pct = float((canon_df["mapped_status"] == MAPPED_STATUS_MAPPED).sum()) / canon_df.height * 100

    _MAPPED_PCT_HIGH, _MAPPED_PCT_MEDIUM = mapping_confidence_thresholds()

    if halted_blocking:
        grade = GRADE_INFO_REQUEST
        rationale = (
            f"{len(halted_blocking)} Blocking rule(s) HALTED "
            f"({', '.join(r['rule'] for r in halted_blocking)}) -- the trial balance does not "
            "foot internally; substantive analysis should not proceed until corrected."
        )
    elif mapped_pct is None:
        grade = GRADE_LOW
        rationale = "No canonical/grouping-mapped Trial Balance supplied -- only raw Layer-1 screens can run."
    elif mapped_pct >= _MAPPED_PCT_HIGH and comparative_data_present:
        grade = GRADE_HIGH
        rationale = f"{mapped_pct:.0f}% of GL accounts mapped to a chart of accounts, plus comparative-period data supplied."
    elif mapped_pct >= _MAPPED_PCT_MEDIUM:
        grade = GRADE_MEDIUM
        rationale = f"{mapped_pct:.0f}% of GL accounts mapped to a chart of accounts."
    else:
        grade = GRADE_LOW
        rationale = (
            f"Only {mapped_pct:.0f}% of GL accounts mapped to a chart of accounts -- most "
            "group/relationship checks cannot run reliably."
        )

    disabled_checks = [f"{r['rule']} ({r['rule_name']}): {r['message']}" for r in skipped]
    if not comparative_data_present:
        disabled_checks.append(
            "Movement/variance year-on-year checks -- no comparative-period data supplied."
        )

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data_sufficiency.json"
    payload = {
        "grade": grade,
        "rationale": rationale,
        "mapped_percentage": mapped_pct,
        "disabled_checks": disabled_checks,
        "halted_blocking_rules": [r["rule"] for r in halted_blocking],
        "not_implemented_count": len(not_implemented),
    }
    write_json_atomic(payload, out_path, indent=4)

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Data sufficiency: {grade}. {len(disabled_checks)} check(s) unavailable or weakened.",
        "artifacts": [str(out_path)],
        "errors": [],
        "grade": grade,
    }


_NOT_EVIDENCED = "Not evidenced by any artifact in this run -- confirm against the original export."

def _rule(layer1, rule_id: str) -> dict:
    if not isinstance(layer1, list):
        return {}
    return next((r for r in layer1 if r.get("rule") == rule_id), {})

@pipeline_tool("build_normalisation_note", domain="canonical")
def build_normalisation_note(
    canonical_tb_file: str,
    layer1_results_file: str = None,
    engagement_context_file: str = None,
    grouping_statistics_file: str = None,
    source_file_name: str = None,
    output_dir: str = None,
    **kwargs,
) -> dict:
    """Assemble the normalisation note the specification requires before any audit
    finding: source system and file, sign convention and how it was resolved, currency
    and scale, period, arithmetic residual, rows removed as subtotals, mapped versus
    unmapped account counts, and every caveat that qualifies the figures downstream.

    Built from artifacts the run actually produced, so it records what happened rather
    than what was intended; anything not evidenced is stated as not evidenced. Writes
    normalisation_note.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    df = load_canonical_tb(tb_path)
    if df is None:
        raise PipelineFileError(str(tb_path), "Canonical TB could not be parsed")

    warnings, caveats = [], []

    l1_path = Path(layer1_results_file) if layer1_results_file else out_dir / "layer1_results.json"
    layer1 = safe_load_json(l1_path) if l1_path.exists() else None
    if layer1 is None:
        warnings.append(
            "layer1_results.json not available -- sign convention, scale and period could not be "
            "evidenced. Run validate_layer1_tb for a re-performable note."
        )

    ctx_path = (Path(engagement_context_file) if engagement_context_file
                else out_dir / "engagement_context.json")
    ctx = safe_load_json(ctx_path) if ctx_path.exists() else {}

    # ── sign convention (TB-000) ──────────────────────────────────────────────
    tb000 = _rule(layer1, "TB-000")
    sign = {
        "assumed_convention": "closing_balance is debit-positive",
        "verification_rule": "TB-000 (normal-side keyword anchors)",
        "status": tb000.get("status", "NOT_RUN"),
        "finding": tb000.get("message", _NOT_EVIDENCED),
    }
    if tb000.get("status") == "WARNING":
        caveats.append(
            "Sign convention was not confirmed by TB-000. Every downstream figure assumes "
            "debit-positive; if the export uses the opposite convention, variance direction and "
            "abnormal-sign findings are inverted."
        )

    # ── currency and scale (TB-026) ───────────────────────────────────────────
    tb026 = _rule(layer1, "TB-026")
    scale = {
        "currency": ctx.get("currency", "unknown"),
        "scale": ctx.get("scale", "unknown"),
        "verification_rule": "TB-026 (currency and unit declared once and consistent)",
        "status": tb026.get("status", "NOT_RUN"),
        "finding": tb026.get("message", _NOT_EVIDENCED),
        "rescaling_applied": "none -- figures are used exactly as supplied (sec 4.1)",
    }
    if tb026.get("status") != "PASS":
        caveats.append(
            "Currency and unit were not confirmed. Materiality, ratios and risk ranking are all "
            "scale-sensitive; a figure stated in thousands but read as units is wrong by 1,000x. "
            "sec 3.2 says final materiality should not be computed until scale is confirmed."
        )

    # ── period and source system ──────────────────────────────────────────────
    tb025, tb029 = _rule(layer1, "TB-025"), _rule(layer1, "TB-029")
    provenance = {
        "source_file": source_file_name or ctx.get("source_system") or _NOT_EVIDENCED,
        "source_system": ctx.get("source_system", "unknown"),
        "period_end": ctx.get("period_end") or _NOT_EVIDENCED,
        "period_rule_status": tb025.get("status", "NOT_RUN"),
        "period_finding": tb025.get("message", _NOT_EVIDENCED),
        "extraction_trail_rule_status": tb029.get("status", "NOT_RUN"),
        "extraction_trail_finding": tb029.get("message", _NOT_EVIDENCED),
        "consolidation_basis": ctx.get("consolidation_basis", "unknown"),
    }
    if tb029.get("status") != "PASS":
        caveats.append(
            "Source system and extraction trail are not identifiable, so this note cannot be "
            "fully re-performed against the original export -- the test sec 4.6 sets for it."
        )

    # ── arithmetic integrity ──────────────────────────────────────────────────
    totals = control_totals(df) or {}
    integrity_rules = {
        rid: {"status": _rule(layer1, rid).get("status", "NOT_RUN"),
              "message": _rule(layer1, rid).get("message", _NOT_EVIDENCED)}
        for rid in ("TB-005", "TB-009", "TB-010", "TB-011")
    }
    halted = [rid for rid, r in integrity_rules.items() if r["status"] == "HALTED"]
    if halted:
        caveats.append(
            f"Arithmetic integrity rule(s) {', '.join(halted)} HALTED -- the trial balance does "
            "not foot internally. Substantive analysis should not be relied on until a corrected "
            "export is obtained."
        )

    # ── rows excluded and mapping coverage ────────────────────────────────────
    grp_path = (Path(grouping_statistics_file) if grouping_statistics_file
                else out_dir / "grouping_statistics.json")
    grp = safe_load_json(grp_path) if grp_path.exists() else {}

    status_col = df["mapped_status"].to_list() if "mapped_status" in df.columns else []
    mapped = sum(1 for s in status_col if str(s).upper() == MAPPED_STATUS_MAPPED)
    unmapped = sum(1 for s in status_col if str(s).upper() in (MAPPED_STATUS_UNMAPPED, MAPPED_STATUS_UNMATCHED))
    total_rows = df.height
    mapped_pct = (mapped / total_rows * 100) if total_rows else 0.0

    # remark #13 fix: coverage measured by account COUNT alone can read very differently
    # from coverage measured by VALUE (a handful of large unmapped balances vs many small
    # ones) -- add the by-value figure alongside, so neither reading is mistaken for the
    # complete picture.
    mapped_pct_by_value = 0.0
    if "closing_balance" in df.columns and total_rows:
        abs_bal = df["closing_balance"].cast(pl.Float64, strict=False).fill_null(0.0).abs()
        total_value = float(abs_bal.sum())
        if total_value:
            mapped_mask = pl.Series(status_col).cast(pl.Utf8).str.to_uppercase() == MAPPED_STATUS_MAPPED
            mapped_pct_by_value = float(abs_bal.filter(mapped_mask).sum()) / total_value * 100

    if mapped_pct < 80:
        caveats.append(
            f"Only {mapped_pct:.0f}% of accounts carry a confirmed FSLI mapping. Relationship, "
            "ratio and counterpart analytics degrade in proportion, and sec 1.2 forbids forcing a "
            "mapping to close the gap -- request the chart of accounts instead."
        )

    transformations = [
        {"step": "Subtotal and total rows removed",
         "detail": grp.get("total_rows_removed", _NOT_EVIDENCED),
         "rationale": "Retaining a subtotal alongside its children double-counts (sec 4.6 step 2)."},
        {"step": "Sign standardisation",
         "detail": sign["assumed_convention"],
         "rationale": "Every downstream rule assumes this convention; TB-000 sanity-checks it."},
        {"step": "Numeric coercion",
         "detail": "opening_balance / debit / credit / closing_balance cast to float, nulls to 0.0",
         "rationale": "Raw exports carry text, blanks and formatted numbers in numeric columns."},
        {"step": "Rescaling",
         "detail": "None applied",
         "rationale": "sec 4.1 -- never silently rescale."},
        {"step": "Ledger separation",
         "detail": ctx.get("consolidation_basis", "unknown"),
         "rationale": "Mixing Ind AS, tax and consolidation ledgers compares unlike balances (sec 4.6 step 5)."},
    ]

    note = {
        "purpose": (
            "Records every transformation applied to the trial balance before analysis, so a "
            "team member can independently re-perform it from the original ERP export "
            "(spec sec 4.6). This note precedes all audit findings (sec 4.2 step 10)."
        ),
        "generated_at": datetime.datetime.now().isoformat(),
        "provenance": provenance,
        "sign_convention": sign,
        "currency_and_scale": scale,
        "arithmetic_integrity": {
            "control_totals": totals,
            "rules": integrity_rules,
            "halted_rules": halted,
        },
        "row_population": {
            "rows_in_canonical_tb": total_rows,
            "mapped_accounts": mapped,
            "unmapped_or_unmatched_accounts": unmapped,
            "mapped_percentage": round(mapped_pct, 2),
            "mapped_percentage_by_value": round(mapped_pct_by_value, 2),
            "grouping_statistics": grp or _NOT_EVIDENCED,
        },
        "transformations_applied": transformations,
        "caveats": caveats or ["No qualifying caveats identified from the artifacts available."],
        "knowledge_pack_versions": pack_versions(),
        "re_performance_test": (
            "A reviewer holding the original export should be able to reproduce the canonical TB "
            "row count, control totals and mapped percentage above. If they cannot, this note is "
            "incomplete and the difference must be investigated before relying on any finding."
        ),
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "normalisation_note.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(note, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "WARNING" if (caveats and caveats[0] != note["caveats"][0]) or warnings else "SUCCESS",
        "can_continue": True,
        "message": (
            f"Normalisation note: {total_rows} row(s), {mapped_pct:.0f}% mapped, "
            f"{len(halted)} halted integrity rule(s), {len(caveats)} caveat(s) qualifying "
            "downstream figures."
        ),
        "artifacts": [str(out_path.resolve())],
        "errors": [],
        "warnings": warnings,
    }


_SIGN_CONVENTION_MIN_ANCHORS = 3

_SIGN_CONVENTION_AGREEMENT_THRESHOLD = 0.7

# TB-R31: this heuristic, run_comparison_sign_check's per-period anchor screen, and
# sign_convention_stats()'s population-wide report_head basis each compute a genuinely
# different "sign convention agreement" percentage for the same TB (different mechanism
# or different denominator scope) -- every render site must say which one a number came
# from, so a reader never mistakes one screen's figure for the only one.
_SIGN_CONVENTION_BASIS_LABEL = "anchor-keyword screen (single-TB, whole-file basis)"

def _check_sign_convention(df: pl.DataFrame) -> tuple:
    """TB-000 fallback heuristic: infer whether this file's positive
    closing_balance means debit or credit, via normal-side keyword anchors,
    and sanity-check internal consistency. Never itself a HALT -- this is a
    sanity check on the sign convention already assumed by every other rule
    in this file (closing_balance is debit-positive by construction), not an
    override of it. Returns (status, message)."""
    debit_normal_anchors = debit_anchors()
    credit_normal_anchors = credit_anchors()
    debit_anchored = 0
    debit_positive = 0
    credit_anchored = 0
    credit_negative = 0

    for row in df.iter_rows(named=True):
        name = str(row.get("gl_name") or "").lower()
        if not name:
            continue
        # closing_balance may still be raw/string here -- TB-000 runs before
        # TB-004's numeric coercion (this heuristic only needs sign, not the
        # coerced-with-tolerance value TB-004 produces), so parse defensively
        # rather than assume a numeric type.
        try:
            closing = float(row.get("closing_balance") or 0)
        except (TypeError, ValueError):
            continue
        is_debit = any(kw in name for kw in debit_normal_anchors)
        is_credit = any(kw in name for kw in credit_normal_anchors)
        if is_debit and not is_credit:
            debit_anchored += 1
            if closing > 0:
                debit_positive += 1
        elif is_credit and not is_debit:
            credit_anchored += 1
            if closing < 0:
                credit_negative += 1

    if debit_anchored < _SIGN_CONVENTION_MIN_ANCHORS or credit_anchored < _SIGN_CONVENTION_MIN_ANCHORS:
        return "WARNING", (
            f"[{_SIGN_CONVENTION_BASIS_LABEL}] Not enough recognizable ledger names to confirm sign "
            f"convention (debit anchors={debit_anchored}, credit anchors={credit_anchored}, "
            f"need >= {_SIGN_CONVENTION_MIN_ANCHORS} each)."
        )

    debit_agreement = debit_positive / debit_anchored
    credit_agreement = credit_negative / credit_anchored

    if debit_agreement >= _SIGN_CONVENTION_AGREEMENT_THRESHOLD and credit_agreement >= _SIGN_CONVENTION_AGREEMENT_THRESHOLD:
        return "PASS", (
            f"[{_SIGN_CONVENTION_BASIS_LABEL}] Sign convention: positive = debit (confirmed by "
            f"{debit_anchored} debit-anchor and {credit_anchored} credit-anchor accounts, "
            f"agreement {debit_agreement:.0%}/{credit_agreement:.0%})."
        )

    inverse_debit_agreement = 1 - debit_agreement
    inverse_credit_agreement = 1 - credit_agreement
    if (
        inverse_debit_agreement >= _SIGN_CONVENTION_AGREEMENT_THRESHOLD
        and inverse_credit_agreement >= _SIGN_CONVENTION_AGREEMENT_THRESHOLD
    ):
        return "WARNING", (
            f"[{_SIGN_CONVENTION_BASIS_LABEL}] Sign convention: this file appears to use the opposite "
            "convention (credit-positive/debit-negative) from what downstream tools assume "
            "(debit-positive) -- variance direction and zero-sum results may be misleading."
        )

    return "WARNING", (
        f"[{_SIGN_CONVENTION_BASIS_LABEL}] Sign convention could not be confirmed -- debit-anchor "
        f"agreement {debit_agreement:.0%}, credit-anchor agreement {credit_agreement:.0%} -- "
        "signs look inconsistent/mixed."
    )

def _extract_tb_sheets_raw(tb_excel_path: str, tb_column_mapping: dict = None) -> dict:
    """
    Loads a TB workbook and returns every TRIAL_BALANCE-classified sheet as one
    concatenated dataframe with standardized columns, before any parquet conversion
    or grouping join happens. Uses the WorksheetClassifier/header-detection/column-
    mapping primitives in backend/tools/_shared.py (this is now their only caller --
    validate_layer1_tb's own raw-tb_excel_path mode -- since the input pipeline
    proper moved to the native auto-detecting parser in input_header_detect.py).

    Per-sheet Excel reading/header-detection and the returned working dataframe
    both run on polars.
    """
    path = Path(tb_excel_path)
    ext = _validate_input_file(path)
    workbook = _load_workbook(path, ext)

    sheets_info = _enumerate_sheets(workbook, ext, path)
    if not sheets_info:
        raise ValueError("EMPTY_WORKBOOK")

    classifier = WorksheetClassifier()
    tb_frames = []
    mapping_issues = []

    for sheet_info in sheets_info:
        try:
            df = _extract_sheet_dataframe(workbook, sheet_info["name"], ext, path)
        except Exception:
            continue  # one unreadable sheet shouldn't abort classification of the rest

        if df.is_empty():
            continue
        # Bail-out check only -- deliberately NOT filtered into `df` itself: a
        # leading blank row has to stay in place for header-row-index detection to
        # count positions the same way the old pandas path did.
        if df.filter(~pl.all_horizontal([pl.col(c).is_null() for c in df.columns])).is_empty():
            continue

        non_null_cols = [c for c in df.columns if df[c].null_count() < df.height]
        if not non_null_cols:
            continue
        df = df.select(non_null_cols)

        data_start = 0
        found = _find_header(df)
        if found:
            header_idx, two_row = found
            header = [_norm___ingest_shared(v) for v in df.row(header_idx)]
            data_start = header_idx + (2 if two_row else 1)

            if two_row:
                sub = [_norm___ingest_shared(v) for v in df.row(header_idx + 1)]
                top = _ffill(header)
                width = max(len(header), len(sub))
                merged = []
                for i in range(width):
                    raw_top = header[i] if i < len(header) else ""
                    sub_i = sub[i] if i < len(sub) else ""
                    if not raw_top and not sub_i:
                        merged.append("")
                        continue
                    top_i = top[i] if i < len(top) else ""
                    merged.append(" ".join(x for x in (top_i, sub_i) if x).strip())
                df = df.rename(dict(zip(df.columns, _dedupe_names(merged))))
            else:
                df = df.rename(dict(zip(df.columns, _dedupe_names(header))))

            df = df.slice(data_start, df.height - data_start)
        else:
            df, _generated = _generate_headers_if_missing(df)

        df = _normalize_duplicate_columns(df)
        df = df.with_row_index("_rownum").with_columns(
            [
                pl.lit(sheet_info["name"]).alias("source_sheet"),
                (pl.col("_rownum") + data_start).alias("source_row"),
            ]
        ).drop("_rownum")

        classification = classifier.classify_worksheet(df)
        if classification["role"] != "TRIAL_BALANCE":
            continue

        mapping = _generate_column_mapping(df, agent_provided_mapping=tb_column_mapping)

        missing_fields = [
            field for field in ("gl_code", "closing_balance")
            if not mapping.get(field) or mapping.get(field) not in df.columns
        ]
        if missing_fields:
            mapping_issues.append({"sheet": sheet_info["name"], "missing_fields": missing_fields})

        def _col(field, default=None):
            col_name = mapping.get(field)
            if col_name and col_name in df.columns:
                return df[col_name].to_list()
            return [default] * df.height

        tb_frames.append(
            pl.DataFrame(
                {
                    "gl_code": _col("gl_code", default=None),
                    "gl_name": _col("gl_name", default=None),
                    "opening_balance": _col("opening_balance", default=0.0),
                    "debit": _col("debit", default=0.0),
                    "credit": _col("credit", default=0.0),
                    "closing_balance": _col("closing_balance", default=0.0),
                    "source_sheet": df["source_sheet"].to_list(),
                    "source_row": df["source_row"].to_list(),
                }
            )
        )

    if not tb_frames:
        raise ValueError("NO_TRIAL_BALANCE_SHEET")

    raw_df = pl.concat(tb_frames, how="vertical_relaxed")

    # Drop trailing total/empty rows: rows with both GL Code and GL Name blank,
    # or blank GL Code paired with a GL Name that reads as a "total" row.
    keep = []
    for row in raw_df.iter_rows(named=True):
        c = str(row.get("gl_code", "")).strip() if row.get("gl_code") is not None else ""
        n = str(row.get("gl_name", "")).strip() if row.get("gl_name") is not None else ""

        is_blank_code = c == "" or c.lower() == "nan"
        is_blank_name = n == "" or n.lower() == "nan"
        is_total_name = "total" in n.lower()

        keep.append(not ((is_blank_code and is_blank_name) or (is_blank_code and is_total_name)))

    filtered_df = raw_df.filter(pl.Series(keep))

    return {"dataframe": filtered_df, "mapping_issues": mapping_issues}

def _extract_tb_rows_from_canonical(canonical_tb_file: str) -> dict:
    """Builds the same working dataframe shape _extract_tb_sheets_raw produces
    (gl_code, gl_name, opening_balance, debit, credit, closing_balance,
    source_sheet, source_row), sourced from an already-materialized canonical
    Parquet instead of a raw workbook. Required columns are always present by
    construction (canonical_schema.CANONICAL_TB_COLUMNS), so mapping_issues is
    always empty here -- TB-001 always PASSes for this input mode."""
    canon_path = Path(canonical_tb_file)
    if not canon_path.exists():
        raise PipelineFileError(str(canon_path), "Canonical Trial Balance (supply canonical_tb_file from load_tb_from_db/ingest_tb_to_live)")

    canon_df = pl.read_parquet(canon_path)
    missing = [c for c in CANONICAL_TB_COLUMNS if c not in canon_df.columns]
    if missing:
        raise ValueError(f"CANONICAL_SCHEMA_MISMATCH: missing columns {missing}")

    if canon_df.is_empty():
        raise ValueError("NO_TRIAL_BALANCE_SHEET")

    working_df = pl.DataFrame(
        {
            "gl_code": canon_df["gl_code"].to_list(),
            "gl_name": canon_df["gl_name"].to_list(),
            "opening_balance": canon_df["opening_balance"].to_list(),
            "debit": canon_df["debit"].to_list(),
            "credit": canon_df["credit"].to_list(),
            "closing_balance": canon_df["closing_balance"].to_list(),
            "source_sheet": ["canonical_tb"] * canon_df.height,
            "source_row": list(range(canon_df.height)),
        }
    )
    return {"dataframe": working_df, "mapping_issues": []}

# TB-R22: an absolute-currency-unit floor for TB-009/010/011/019's rounding checks. Each
# of those rules previously compared only a RELATIVE (percentage) residual, so any
# nonzero residual at all -- including floating-point noise, e.g. 0.000122 against a
# Rs 95,219.69 crore control total -- escalated straight past PASS into WARNING. A
# residual has to clear BOTH this absolute floor AND the existing 5% relative threshold
# before it's treated as a genuine exception rather than noise.
_TB_ROUNDING_ABS_EPSILON = 1.0


@pipeline_tool("validate_layer1_tb", domain="canonical")
def validate_layer1_tb(
    tb_excel_path: str = None,
    tb_column_mapping: dict = None,
    canonical_tb_file: str = None,
    output_dir: str = None,
    expected_financial_year: str = None,
    source_system: str = None,
    extraction_date: str = None,
):
    """
    Executes the complete Layer 1 Trial Balance validation, sourced either from
    a raw TB Excel/CSV file (tb_excel_path, before any parquet conversion or
    grouping join happens) or from an already-materialized canonical
    Parquet (canonical_tb_file -- the DB-sourced / live-template-sourced path,
    which has no raw workbook). Exactly one of the two must be provided.
    expected_financial_year/source_system/extraction_date are optional
    caller-supplied context (TB-025/TB-029) -- this tool never extracts them
    from the file itself; absent, those rules report WARNING, not HALTED.
    """
    errors = []
    artifacts = []

    if not tb_excel_path and not canonical_tb_file:
        errors.append({"type": "InvalidArguments", "error": "Provide either tb_excel_path or canonical_tb_file."})
        return {"execution_status": "FAILED", "errors": errors, "message": "No Trial Balance input provided."}
    if tb_excel_path and canonical_tb_file:
        errors.append({"type": "InvalidArguments", "error": "Provide only one of tb_excel_path or canonical_tb_file, not both."})
        return {"execution_status": "FAILED", "errors": errors, "message": "Ambiguous Trial Balance input."}

    if tb_excel_path:
        tb_path = Path(tb_excel_path)
        if not tb_path.exists():
            errors.append({"type": "FileNotFoundError", "file": str(tb_path)})
            return {"execution_status": "FAILED", "errors": errors, "message": "TB Excel file not found."}
        try:
            extraction = _extract_tb_sheets_raw(tb_excel_path, tb_column_mapping)
        except ValueError as e:
            errors.append({"type": "TBExtractionError", "error": str(e)})
            return {"execution_status": "FAILED", "errors": errors, "message": f"Failed to read TB Excel: {e}"}
    else:
        try:
            extraction = _extract_tb_rows_from_canonical(canonical_tb_file)
        except ValueError as e:
            errors.append({"type": "TBExtractionError", "error": str(e)})
            return {"execution_status": "FAILED", "errors": errors, "message": f"Failed to read canonical Trial Balance: {e}"}

    df = extraction["dataframe"]
    mapping_issues = extraction["mapping_issues"]

    if df.is_empty():
        errors.append({"type": "DataError", "error": "Empty Trial Balance"})
        return {"execution_status": "FAILED", "errors": errors, "message": "Trial Balance is empty."}

    df = df.with_row_index("_rownum")

    results = []
    findings = []

    def add_result(rule_id, rule_name, status, severity, message, evidence=None, affected_rows=None):
        if affected_rows is None:
            affected_rows = []
        results.append(
            {
                "rule": rule_id,
                "rule_name": rule_name,
                "status": status,
                "severity": severity,
                "message": message,
                "evidence": evidence,
                "affected_rows": affected_rows,
            }
        )

    def add_findings(rule_id, rule_name, severity, status, gl_code, gl_name, source_sheet, source_row, message):
        findings.append(
            {
                "rule_id": rule_id,
                "rule_name": rule_name,
                "severity": severity,
                "status": status,
                "gl_code": gl_code,
                "gl_name": gl_name,
                "source_sheet": source_sheet,
                "source_row": source_row,
                "message": message,
            }
        )

    # Read at the very end (summary/return); overwritten below by the Layer 4 rule when
    # a raw workbook is available to re-scan for formulas.
    layer4_result = {"has_formulas": False, "total_formula_cells": 0, "cells_by_sheet": {}}

    def _rule_tb000_sign_convention():
        # TB-000: Sign convention -- real anchor-keyword heuristic (see
        # _check_sign_convention above), replacing the old always-PASS stub.
        # Never HALTED: this sanity-checks the debit-positive convention every
        # other rule in this file already assumes, it doesn't gate on it.
        sign_status, sign_message = _check_sign_convention(df)
        add_result("TB-000", "Sign convention documented and consistent", sign_status, "Blocking", sign_message)

    _rule_tb000_sign_convention()

    def _rule_tb001_required_columns():
        # TB-001: Required columns identified
        if mapping_issues:
            add_result("TB-001", "All required columns present", "HALTED", "Blocking", f"Could not identify required columns on sheet(s): {mapping_issues}")
        else:
            add_result("TB-001", "All required columns present", "PASS", "Blocking", "All columns identified.")

    _rule_tb001_required_columns()

    def _rule_tb002_gl_code_mandatory():
        # TB-002: GL Code mandatory
        blanks = df.filter(pl.col("gl_code").is_null() | (pl.col("gl_code") == ""))
        if not blanks.is_empty():
            rows = blanks["_rownum"].to_list()
            add_result("TB-002", "GL Code mandatory and non-blank", "HALTED", "Blocking", "Blank GL codes found.", affected_rows=rows)
            for row in blanks.iter_rows(named=True):
                add_findings("TB-002", "GL Code mandatory", "Blocking", "HALTED", None, row.get("gl_name"), row.get("source_sheet"), row.get("source_row"), "Blank GL code")
        else:
            add_result("TB-002", "GL Code mandatory and non-blank", "PASS", "Blocking", "No blank GL codes.")

    _rule_tb002_gl_code_mandatory()

    def _rule_tb003_gl_code_unique():
        # TB-003: GL Code unique
        counts = df.group_by("gl_code").agg(pl.len().alias("_count"))
        dupes = counts.filter(pl.col("_count") > 1)["gl_code"].to_list()
        if dupes:
            affected = df.filter(pl.col("gl_code").is_in(dupes))
            add_result("TB-003", "GL Code unique", "HALTED", "Blocking", "Duplicate GL codes found.", affected_rows=affected["_rownum"].to_list())
            for row in affected.iter_rows(named=True):
                add_findings("TB-003", "GL Code unique", "Blocking", "HALTED", row.get("gl_code"), row.get("gl_name"), row.get("source_sheet"), row.get("source_row"), "Duplicate GL code")
        else:
            add_result("TB-003", "GL Code unique", "PASS", "Blocking", "GL codes are unique.")

    _rule_tb003_gl_code_unique()

    def _rule_tb004_numeric_columns_valid():
        nonlocal df
        # TB-004: Numeric columns valid
        num_cols = ["opening_balance", "debit", "credit", "closing_balance"]
        df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0) for c in num_cols])
        add_result("TB-004", "Numeric columns valid", "PASS", "Blocking", "Numeric columns coerced from raw Excel.")

    _rule_tb004_numeric_columns_valid()

    def _rule_tb005_closing_equals_opening_plus_dr_minus_cr():
        nonlocal df
        # TB-005: Closing = Opening + Dr - Cr
        tolerance = 5.0
        df = df.with_columns(
            (pl.col("opening_balance") + pl.col("debit") - pl.col("credit")).alias("_expected")
        ).with_columns(
            (pl.col("closing_balance") - pl.col("_expected")).alias("_diff")
        ).with_columns(
            pl.max_horizontal(
                pl.col("opening_balance").abs() + pl.col("debit").abs() + pl.col("credit").abs(),
                pl.col("closing_balance").abs(),
                pl.col("_expected").abs(),
            ).alias("_activity_base")
        ).with_columns(
            pl.when(pl.col("_activity_base") > 0)
            .then((pl.col("_diff").abs() / pl.col("_activity_base")) * 100)
            .otherwise(0.0)
            .alias("_diff_pct")
        )

        mismatches = df.filter(pl.col("_diff_pct") > tolerance)
        warnings = df.filter((pl.col("_diff_pct") > 0) & (pl.col("_diff_pct") <= tolerance))

        if not mismatches.is_empty():
            add_result("TB-005", "Closing Balance = Opening Balance + Debit - Credit", "WARNING", "Warning", "Real mismatches outside tolerance.", affected_rows=mismatches["_rownum"].to_list())
            for row in mismatches.iter_rows(named=True):
                add_findings("TB-005", "Closing Balance = Opening Balance + Debit - Credit", "Warning", "WARNING", row.get("gl_code"), row.get("gl_name"), row.get("source_sheet"), row.get("source_row"), "Mismatch outside tolerance")
        elif not warnings.is_empty():
            add_result("TB-005", "Closing Balance = Opening Balance + Debit - Credit", "WARNING", "Warning", "Rounding drift within tolerance.", affected_rows=warnings["_rownum"].to_list())
        else:
            # TB-R22: severity must be consistent regardless of pass/fail status -- this
            # PASS branch previously recorded "Blocking" while both WARNING branches above
            # record "Warning" for the identical rule, which is inconsistent by construction.
            add_result("TB-005", "Closing Balance = Opening Balance + Debit - Credit", "PASS", "Warning", "All rows tie out.")

        df = df.drop(["_expected", "_diff", "_activity_base", "_diff_pct"])

    _rule_tb005_closing_equals_opening_plus_dr_minus_cr()

    def _rule_tb009_total_dr_equals_total_cr():
        # TB-009: Total Dr = Total Cr
        tot_dr = float(df["debit"].sum())
        tot_cr = float(df["credit"].sum())
        diff_tot = abs(tot_dr - tot_cr)
        base = max(abs(tot_dr), abs(tot_cr))
        diff_pct_tot = (diff_tot / base * 100) if base > 0 else 0

        # TB-R22: a residual must clear the absolute floor as well as the relative one --
        # otherwise a floating-point-noise-level diff (diff_pct_tot > 0 but astronomically
        # small) escalates straight past PASS.
        if diff_tot <= _TB_ROUNDING_ABS_EPSILON:
            add_result("TB-009", "Total Debit equals Total Credit", "PASS", "Blocking", "Debits equal Credits.")
        elif diff_pct_tot > 5.0:
            add_result("TB-009", "Total Debit equals Total Credit", "HALTED", "Blocking", f"Dr={tot_dr}, Cr={tot_cr}, diff={diff_tot}")
        else:
            add_result("TB-009", "Total Debit equals Total Credit", "WARNING", "Warning", f"Dr={tot_dr}, Cr={tot_cr}, diff={diff_tot}")

    _rule_tb009_total_dr_equals_total_cr()

    def _rule_tb019_rounding_stays_within_tolerance():
        # TB-019: Rounding stays within tolerance -- was permanently SKIPPED ("Missing
        # threshold"), unlike TB-005/009/010/011, which never had a threshold wired in for
        # rounding tolerance either (see _TB_ROUNDING_ABS_EPSILON, TB-R22). This rule is
        # the aggregate, control-total-level counterpart to TB-005's per-row check: it
        # validates control_totals()'s own closing-balance foot (the number rendered as
        # "Total Debit"/"Total Credit"/"Debit - Credit Difference" in every report) against
        # the same absolute-plus-relative tolerance.
        ctrl = control_totals(df) or {}
        diff = abs(ctrl.get("difference", 0.0))
        base = max(abs(ctrl.get("total_debit", 0.0)), abs(ctrl.get("total_credit", 0.0)))
        diff_pct = (diff / base * 100) if base > 0 else 0
        if diff <= _TB_ROUNDING_ABS_EPSILON:
            add_result("TB-019", "Rounding stays within tolerance", "PASS", "Info",
                       f"Control-total residual {diff:,.6f} is within the absolute tolerance.")
        elif diff_pct > 5.0:
            add_result("TB-019", "Rounding stays within tolerance", "HALTED", "Info",
                       f"Control-total residual {diff:,.2f} ({diff_pct:.2f}%) exceeds tolerance.")
        else:
            add_result("TB-019", "Rounding stays within tolerance", "WARNING", "Info",
                       f"Control-total residual {diff:,.2f} ({diff_pct:.2f}%) outside the absolute "
                       "tolerance but within the 5% relative band.")

    _rule_tb019_rounding_stays_within_tolerance()

    def _rule_tb010_sum_opening_equals_zero():
        # TB-010: Sum Opening = 0
        tot_open = float(df["opening_balance"].sum())
        base_open = float(df["opening_balance"].abs().sum())
        diff_pct_open = (abs(tot_open) / base_open * 100) if base_open > 0 else 0
        # TB-R22: absolute floor first -- see _TB_ROUNDING_ABS_EPSILON.
        if abs(tot_open) <= _TB_ROUNDING_ABS_EPSILON:
            add_result("TB-010", "Sum of Opening Balances equals zero", "PASS", "Blocking", "Sum is 0.")
        elif diff_pct_open > 5.0:
            add_result("TB-010", "Sum of Opening Balances equals zero", "HALTED", "Blocking", f"Sum={tot_open}")
        else:
            add_result("TB-010", "Sum of Opening Balances equals zero", "WARNING", "Warning", f"Sum={tot_open}")

    _rule_tb010_sum_opening_equals_zero()

    def _rule_tb011_sum_closing_equals_zero():
        # TB-011: Sum Closing = 0
        tot_close = float(df["closing_balance"].sum())
        base_close = float(df["closing_balance"].abs().sum())
        diff_pct_close = (abs(tot_close) / base_close * 100) if base_close > 0 else 0
        # TB-R22: absolute floor first -- see _TB_ROUNDING_ABS_EPSILON. This is the exact
        # class of defect a client review flagged directly: a residual of 0.000122 against
        # a multi-thousand-crore sum was reported as a WARNING exception with false
        # precision, purely because diff_pct was nonzero however microscopically.
        if abs(tot_close) <= _TB_ROUNDING_ABS_EPSILON:
            add_result("TB-011", "Sum of Closing Balances equals zero", "PASS", "Blocking", "Sum is 0.")
        elif diff_pct_close > 5.0:
            add_result("TB-011", "Sum of Closing Balances equals zero", "HALTED", "Blocking", f"Sum={tot_close}")
        else:
            add_result("TB-011", "Sum of Closing Balances equals zero", "WARNING", "Warning", f"Sum={tot_close}")

    _rule_tb011_sum_closing_equals_zero()

    def _rule_tb015_activity_but_closes_at_zero():
        # TB-015: Activity but closes at zero
        act_zero = df.filter(
            ((pl.col("debit") != 0) | (pl.col("credit") != 0)) & (pl.col("closing_balance") == 0)
        )
        if not act_zero.is_empty():
            add_result("TB-015", "Activity during period but closes at zero", "WARNING", "Info", "Some accounts close at zero.", affected_rows=act_zero["_rownum"].to_list())
            for row in act_zero.iter_rows(named=True):
                add_findings("TB-015", "Activity but closes at zero", "Info", "WARNING", row.get("gl_code"), row.get("gl_name"), row.get("source_sheet"), row.get("source_row"), "Closes at zero")
        else:
            add_result("TB-015", "Activity during period but closes at zero", "PASS", "Info", "None found.")

    _rule_tb015_activity_but_closes_at_zero()

    def _rule_tb025_period_matches_engagement():
        # TB-025: Period matches the audit/comparison engagement period. This tool
        # has no title-block/period-label extraction of its own -- if the caller
        # supplies expected_financial_year, the period is recorded as
        # caller-asserted (not independently verified from file content, stated
        # plainly rather than pretending to be verified); otherwise WARNING.
        if expected_financial_year:
            add_result(
                "TB-025", "Period matches the audit/comparison engagement period", "PASS", "Blocking",
                f"Engagement period supplied by caller: {expected_financial_year} (not independently verified from file content).",
            )
        else:
            add_result(
                "TB-025", "Period matches the audit/comparison engagement period", "WARNING", "Blocking",
                "Engagement period not supplied -- cannot confirm period match.",
            )

    _rule_tb025_period_matches_engagement()

    def _rule_tb026_currency_and_scale():
        # TB-026: Currency and unit of measure declared once and consistent. This
        # pipeline has no currency column anywhere in its canonical schema (single
        # target-currency assumption baked into ingestion) -- always WARNING per
        # the spec's own "no currency column mapped -> WARNING, assume target,
        # continue" step. Scale-outlier sub-check: any single row's magnitude far
        # outside the file's own scale suggests a wrong-scale entry.
        nonzero_closing = df.filter(pl.col("closing_balance") != 0)["closing_balance"].abs()
        scale_outliers = []
        if nonzero_closing.len() >= 5:
            median_mag = float(nonzero_closing.median())
            if median_mag > 0:
                outlier_rows = df.filter((pl.col("closing_balance").abs() > median_mag * 1000) & (pl.col("closing_balance") != 0))
                scale_outliers = outlier_rows["_rownum"].to_list()
        if scale_outliers:
            add_result(
                "TB-026", "Currency and unit of measure declared once and consistent", "WARNING", "Blocking",
                "No currency column tracked -- assuming target currency throughout. "
                f"{len(scale_outliers)} row(s) are >1000x the file's median closing-balance magnitude -- possible wrong-scale entry.",
                affected_rows=scale_outliers,
            )
        else:
            add_result(
                "TB-026", "Currency and unit of measure declared once and consistent", "WARNING", "Blocking",
                "No currency column tracked -- assuming target currency throughout. No scale outliers detected.",
            )

    _rule_tb026_currency_and_scale()

    def _rule_tb029_source_system_identifiable():
        # TB-029: Source system and extraction trail identifiable.
        if source_system or extraction_date:
            add_result(
                "TB-029", "TB source system and extraction trail identifiable", "PASS", "Warning",
                f"Source system: {source_system or 'not supplied'}. Extraction date: {extraction_date or 'not supplied'}.",
            )
        else:
            add_result(
                "TB-029", "TB source system and extraction trail identifiable", "WARNING", "Warning",
                "No source system or extraction date supplied.",
            )

    _rule_tb029_source_system_identifiable()

    def _rule_layer4_formula_flag():
        nonlocal layer4_result
        # Layer 4 -- Formula Flag (presence-only, informational). Only meaningful
        # against the raw workbook (a canonical Parquet has no formulas by
        # construction); requires a second, separate raw read of the workbook
        # (see _detect_formula_cells) since every value-loading path elsewhere
        # uses data_only=True and can never see formula text. Never counts toward
        # "flagged rows" in any summary metric -- formula presence alone is
        # informational, not a validation failure.
        if tb_excel_path:
            formula_cells = _detect_formula_cells(Path(tb_excel_path))
            total_formula_cells = sum(len(cells) for cells in formula_cells.values())
            layer4_result = {
                "has_formulas": total_formula_cells > 0,
                "total_formula_cells": total_formula_cells,
                "cells_by_sheet": formula_cells,
            }

    _rule_layer4_formula_flag()

    # SKIPPED rules — genuinely fixable by a later stage of THIS SAME pipeline run, not by
    # anything external. TB-012/013/016/017/018/020 all need the FSLI classification and/or
    # grouping join that only exists once the canonical TB is built (ingest_tb_to_live for
    # live/uploaded documents, load_tb_from_db for MAIN) -- validate_layer2_tb (run right
    # after that, before build_data_sufficiency_grade) re-evaluates each of these for real
    # against the canonical TB and patches its SKIPPED stub below with the actual
    # PASS/WARNING/HALTED result, in place, in layer1_results.json.
    skipped_rules = [
        ("TB-012", "Assets = Liabilities + Equity", "Blocking",
         "Requires FSLI classification -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-013", "Each GL Code maps to exactly one Group", "Blocking",
         "Requires the grouping join -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-016", "No duplicate GL Code + Group", "Blocking",
         "Requires the grouping join -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-017", "Expected mandatory heads present", "Warning",
         "Requires FSLI classification -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-018", "Suspense carries non-zero", "Warning",
         "Requires FSLI classification -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-020", "Tax-related heads sign", "Warning",
         "Requires FSLI classification -- evaluated by validate_layer2_tb once the canonical TB is built"),
        ("TB-024", "Total Income - Total Expense = Net P/L", "Blocking", "Missing P/L figure"),
        ("TB-027", "Numeric columns contain static values", "Warning", "Formula data lost in canonical parquet"),
        ("TB-031", "Control/branch flagged", "Warning", "Missing sub-ledger"),
        ("TB-032", "P&L accounts carry no Opening Balance", "Warning", "Missing P&L tagging"),
    ]

    for rule_id, rule_name, sev, reason in skipped_rules:
        add_result(rule_id, rule_name, "SKIPPED", sev, reason)

    # NOT_IMPLEMENTED rules — distinct from SKIPPED on purpose. These need an approved
    # chart-of-accounts/Group master reference to check sign/value against; no such
    # ingestion path (upload field, schema, or join) exists anywhere in this pipeline for
    # any client, so no amount of re-running or re-uploading resolves them. Labeling them
    # SKIPPED alongside genuinely session-fixable rules wrongly implies otherwise.
    not_implemented_rules = [
        ("TB-006", "Opening Balance sign matches Group", "Blocking"),
        ("TB-007", "Closing Balance sign matches Group", "Warning"),
        ("TB-008", "Balance direction that should never occur", "Warning"),
        ("TB-014", "Group values restricted to approved master", "Blocking"),
    ]
    for rule_id, rule_name, sev in not_implemented_rules:
        add_result(
            rule_id, rule_name, "NOT_IMPLEMENTED", sev,
            "Not implemented in this product build -- requires an approved chart-of-accounts/"
            "Group master reference that this pipeline does not currently ingest from any source.",
        )

    status_counts = {"PASS": 0, "WARNING": 0, "HALTED": 0, "SKIPPED": 0}  # nosec B105 -- tally initializer, not a credential

    for r in results:
        st = r["status"]
        status_counts[st] = status_counts.get(st, 0) + 1

    if status_counts["HALTED"] > 0:
        overall = "HALTED"
    elif status_counts["WARNING"] > 0:
        overall = "WARNING"
    else:
        overall = "PASS"

    out_dir = resolve_output_dir(output_dir)
    os.makedirs(out_dir, exist_ok=True)

    results_file = out_dir / "layer1_results.json"
    findings_file = out_dir / "layer1_findings.parquet"
    summary_file = out_dir / "layer1_summary.json"

    # Wave 2 Fix 6: a live COMPARISON run hit a UnicodeDecodeError in validate_layer2_tb
    # reading this exact file -- the agent's own independent tool-call raced this
    # in-place write and caught it mid-flush (a truncated multi-byte UTF-8 sequence).
    # atomic_write's write-to-temp-then-os.replace() means any concurrent reader sees
    # either the complete old file or the complete new one, never a partial write.
    with atomic_write(results_file) as tmp_path:
        Path(tmp_path).write_text(json.dumps(results, indent=4), encoding="utf-8")
    artifacts.append(str(results_file.resolve()))

    summary = {
        "rules_executed": len(results),
        "pass": status_counts["PASS"],
        "warning": status_counts["WARNING"],
        "halted": status_counts["HALTED"],
        "skipped": status_counts["SKIPPED"],
        "overall_status": overall,
        "layer4_result": layer4_result,
    }
    write_json_atomic(summary, summary_file, indent=4)
    artifacts.append(str(summary_file.resolve()))

    findings_cols = ["rule_id", "rule_name", "severity", "status", "gl_code", "gl_name", "source_sheet", "source_row", "message"]
    if findings:
        findings_df = pl.DataFrame(findings)
    else:
        findings_df = pl.DataFrame(schema={c: pl.Utf8 for c in findings_cols})

    schema_errors, findings_df = validate_dataframe_schema(findings_df, "validate_layer1_tb")
    if schema_errors:
        errors.extend(schema_errors)

    write_parquet_atomic(findings_df, findings_file)
    artifacts.append(str(findings_file.resolve()))

    passed = status_counts["PASS"]
    halted = status_counts["HALTED"]
    warn_count = status_counts["WARNING"]

    msg_parts = [f"Layer 1 Validation completed: {passed} passed, {halted} halted, {warn_count} warnings."]

    if halted > 0 or warn_count > 0:
        failed_details = []
        for r in results:
            if r["status"] in ("HALTED", "WARNING"):
                failed_details.append(f"{r['rule_name']} ({r['status']})")
        if failed_details:
            msg_parts.append("Issues found in: " + ", ".join(failed_details))

    final_message = " ".join(msg_parts)

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": overall,
        "can_continue": overall != "HALTED",
        "message": final_message,
        "artifacts": artifacts,
        "errors": errors,
        "layer4_result": layer4_result,
    }


@pipeline_tool("validate_layer2_tb", domain="canonical")
def validate_layer2_tb(canonical_tb_file: str, layer1_results_file: str = None, output_dir: str = None, **kwargs) -> dict:
    """Evaluates the six rules validate_layer1_tb can only mark SKIPPED (TB-012/013/016/017/
    018/020) because they need the FSLI classification and grouping join that only exist once
    the canonical TB has been built (ingest_tb_to_live for live/uploaded documents,
    load_tb_from_db for MAIN). Patches each of those six SKIPPED stubs in layer1_results.json
    with a real PASS/WARNING/HALTED result, in place. Run this immediately after the canonical
    TB is built and before build_data_sufficiency_grade."""
    errors = []
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "canonical_tb.parquet")

    df = load_canonical_tb(tb_path)
    if df is None or df.is_empty():
        errors.append({"type": "EmptyDataError", "message": "canonical_tb.parquet loaded but is empty."})
        return {"execution_status": "FAILED", "errors": errors, "message": "Canonical TB is empty."}

    l1_path = Path(layer1_results_file) if layer1_results_file else out_dir / "layer1_results.json"
    if not l1_path.exists():
        raise PipelineFileError(str(l1_path), "Layer-1 results (run validate_layer1_tb first)")
    with open(l1_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    by_rule = {r["rule"]: r for r in results}
    patched = []

    def patch(rule_id, status, message, evidence=None, affected_rows=None):
        if rule_id not in by_rule:
            return
        r = by_rule[rule_id]
        r["status"] = status
        r["message"] = message
        r["evidence"] = evidence
        r["affected_rows"] = affected_rows or []
        patched.append(rule_id)

    # TB-013: each GL Code maps to exactly one Group (main_head) -- a code grouped two
    # different ways across rows signals an upstream mapping inconsistency.
    dup_group = (
        df.group_by("gl_code").agg(pl.col("main_head").n_unique().alias("n_groups"))
        .filter(pl.col("n_groups") > 1)
    )
    if dup_group.is_empty():
        patch("TB-013", "PASS", "Every GL Code maps to exactly one Group.")
    else:
        codes = dup_group["gl_code"].to_list()
        shown = ", ".join(str(c) for c in codes[:10])
        more = f" (+{len(codes) - 10} more)" if len(codes) > 10 else ""
        patch("TB-013", "HALTED", f"{len(codes)} GL Code(s) map to more than one Group: {shown}{more}.",
              evidence={"gl_codes": codes})

    # TB-016: no duplicate (GL Code, Group) pair -- each account should appear once.
    dup_pair = (
        df.group_by(["gl_code", "main_head"]).agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
    )
    if dup_pair.is_empty():
        patch("TB-016", "PASS", "No duplicate GL Code + Group combinations.")
    else:
        pairs = dup_pair.select(["gl_code", "main_head"]).to_dicts()
        patch("TB-016", "HALTED", f"{len(pairs)} duplicate GL Code + Group combination(s) found.",
              evidence={"pairs": pairs[:10]})

    # TB-012: Assets = Liabilities + Equity + Net P/L -- the fundamental accounting identity
    # restated by FS head. This canonical schema stores closing_balance SIGNED (debit-normal
    # heads -- Assets, Expenses -- positive; credit-normal heads -- Liabilities, Equity,
    # Revenue -- negative; see normal_debit_heads()/normal_credit_heads() and
    # sign_convention_stats()), so the identity is simply that Assets + Liabilities + Equity +
    # Revenue + Expenses nets to zero once every row sits under one of the five core heads.
    # A breach beyond rounding tolerance is arithmetically just Total Debit = Total Credit
    # reshuffled by classification, so it means a row was dropped, double-counted, or sits
    # outside the five core heads (Unmapped) -- not that the entity's real balance sheet fails
    # to balance.
    totals = {}
    for row in df.iter_rows(named=True):
        head = row.get("report_head")
        totals[head] = totals.get(head, 0.0) + float(row.get("closing_balance") or 0.0)
    assets = totals.get("Assets", 0.0)
    liabilities = totals.get("Liabilities", 0.0)
    equity = totals.get("Equity", 0.0)
    revenue = totals.get("Revenue", 0.0)
    expenses = totals.get("Expenses", 0.0)
    unmapped = totals.get("Unmapped", 0.0)
    # TB-R21 correction: the identity must include Unmapped (and any other non-core
    # report_head bucket classify_row can emit, e.g. a "Balance Sheet"/"Profit & Loss"
    # bs_pl-only fallback label) -- excluding Unmapped mechanically halted any TB carrying
    # an unmapped balance, even one that genuinely foots once every row (mapped or not) is
    # summed. identity_gap is therefore the exhaustive sum across ALL report_head buckets,
    # equal to the TB's true double-entry closing-balance total (control_totals()'s
    # sum_closing). Unmapped is still surfaced separately below as its own, distinctly
    # labelled coverage gap rather than folded silently into a footing failure.
    identity_gap = sum(totals.values())
    tolerance = max(1.0, abs(assets) * 0.001)
    if abs(identity_gap) <= tolerance:
        unmapped_note = (
            f" {unmapped:,.2f} of this sits in Unmapped accounts -- a coverage gap, not a footing failure."
            if abs(unmapped) > tolerance else ""
        )
        patch("TB-012", "PASS",
              f"Assets + Liabilities + Equity + Revenue + Expenses + Unmapped nets to {identity_gap:,.2f}, "
              f"within tolerance.{unmapped_note}")
    else:
        msg = (
            f"Assets ({assets:,.2f}) + Liabilities ({liabilities:,.2f}) + Equity ({equity:,.2f}) + "
            f"Revenue ({revenue:,.2f}) + Expenses ({expenses:,.2f}) + Unmapped ({unmapped:,.2f}) leaves "
            f"a residual of {identity_gap:,.2f} instead of netting to zero -- this cannot be explained "
            "by unmapped coverage alone; a row was likely dropped, double-counted, or sits under a "
            "non-standard classification outside the five core heads and Unmapped."
        )
        patch("TB-012", "HALTED", msg,
              evidence={"assets": assets, "liabilities": liabilities, "equity": equity,
                        "revenue": revenue, "expenses": expenses, "unmapped": unmapped,
                        "identity_gap": identity_gap})

    # TB-017: expected mandatory heads present -- a core FS head with zero mapped GL accounts
    # usually means the grouping workbook never populated that category, not that the entity
    # genuinely has none of it.
    head_n = {h["report_head"]: h["n"] for h in df.group_by("report_head").agg(pl.len().alias("n")).to_dicts()}
    missing_heads = [h for h in ("Assets", "Liabilities", "Equity", "Revenue", "Expenses") if head_n.get(h, 0) == 0]
    if not missing_heads:
        patch("TB-017", "PASS", "All five mandatory FS heads have at least one mapped GL account.")
    else:
        patch("TB-017", "WARNING",
              f"No GL accounts classified under: {', '.join(missing_heads)}. Confirm this entity genuinely "
              "has none of this head, rather than a gap in the grouping workbook.",
              evidence={"missing_heads": missing_heads})

    # TB-018: suspense/clearing accounts carrying a non-zero closing balance -- unresolved
    # classification that can mask offsetting errors larger than the net balance shown.
    susp_mask = (
        pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().str.contains("suspense", literal=True).fill_null(False)
        | pl.col("main_head").cast(pl.Utf8).str.to_lowercase().str.contains("suspense", literal=True).fill_null(False)
    )
    susp_df = df.filter(susp_mask & (pl.col("closing_balance") != 0))
    if susp_df.is_empty():
        patch("TB-018", "PASS", "No suspense/clearing account carries a non-zero closing balance.")
    else:
        rows = susp_df.select(["gl_code", "gl_name", "closing_balance"]).to_dicts()
        total_susp = float(susp_df["closing_balance"].abs().sum())
        patch("TB-018", "WARNING",
              f"{len(rows)} suspense/clearing account(s) carry a non-zero closing balance "
              f"(combined {total_susp:,.2f}).",
              evidence={"accounts": rows[:20]})

    # TB-020: tax-related accounts should sit on their FS head's normal balance side (e.g. an
    # Asset-side advance-tax account with a credit balance is a sign-convention red flag).
    tax_mask = (
        pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().str.contains("tax", literal=True).fill_null(False)
        | pl.col("main_head").cast(pl.Utf8).str.to_lowercase().str.contains("tax", literal=True).fill_null(False)
    )
    tax_df = df.filter(tax_mask)
    debit_heads = normal_debit_heads()
    credit_heads = normal_credit_heads()
    flips = []
    for row in tax_df.iter_rows(named=True):
        head_l = str(row.get("report_head") or "").lower()
        bal = float(row.get("closing_balance") or 0.0)
        if (head_l in debit_heads and bal < 0) or (head_l in credit_heads and bal > 0):
            flips.append(row)
    if tax_df.is_empty():
        patch("TB-020", "PASS", "No tax-related accounts identified in this TB.")
    elif not flips:
        patch("TB-020", "PASS", f"All {tax_df.height} tax-related account(s) sit on their FS head's normal balance side.")
    else:
        rows = [{"gl_code": r.get("gl_code"), "gl_name": r.get("gl_name"),
                 "report_head": r.get("report_head"), "closing_balance": r.get("closing_balance")} for r in flips]
        patch("TB-020", "WARNING",
              f"{len(flips)} tax-related account(s) carry a balance opposite their FS head's normal side.",
              evidence={"accounts": rows[:20]})

    write_json_atomic(results, l1_path, indent=4)

    # TB-R19 correction: a rule patched HALTED above (most commonly TB-012) must actually
    # surface as a HALTED pipeline_status/can_continue=False here -- this function used to
    # return SUCCESS/True unconditionally regardless of what it had just patched, so a
    # genuinely-halted TB-012 never stopped anything downstream.
    halted_rules = [rid for rid in patched if by_rule[rid]["status"] == "HALTED"]
    pipeline_status = "HALTED" if halted_rules else "SUCCESS"
    halted_note = f" HALTED: {', '.join(halted_rules)}." if halted_rules else ""

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": pipeline_status,
        "can_continue": not halted_rules,
        "halted_rules": halted_rules,
        "message": f"Layer 2 validation patched {len(patched)}/6 rule(s) in layer1_results.json.{halted_note}",
        "artifacts": [str(l1_path)],
        "errors": errors,
    }


