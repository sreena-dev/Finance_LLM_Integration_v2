"""Input Quality Gate, data-quality tiering, and the confirmation gate --
pre-classification validation over raw parsed input, run once per
ingestion, BEFORE taxonomy resolution/classification ever runs.

Ported from TB_normalization_v1's input/quality_gate.py, input/quality_tier.py,
and input/confirmation_gate.py, combined into one module (this codebase
doesn't need them split across separate files -- there's exactly one
caller sequence, not three independent ones).

Nothing here mutates tb_rows or grouping_hints -- purely additive
diagnostics. Only genuinely unusable input (zero TB rows) is FAIL;
everything else is WARN -- warnings must never automatically block
ingestion. Confirmation is a separate, later decision: a CASE_3 document,
or a CASE_2 document whose incomplete-row fraction exceeds a threshold,
must not spend resolver/LLM cost until a human explicitly accepts
responsibility for the input's data quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from modes.trial_balance.pipeline.tools.input_grouping_shape import GroupingShape  # noqa: F401 -- re-exported for callers

QualityGateStatus = Literal["PASS", "WARN", "FAIL"]
DataQualityTier = Literal["CASE_1", "CASE_2", "CASE_3"]

DEFAULT_ARITHMETIC_TOLERANCE = 0.01

_REQUIRED_HINT_FIELDS = ("sub_head_1", "sub_head_2")

# Ingestion error catalog (TB-v2-git/Trial_Balance_ingestion_error.md) additions.
# All WARN-only, per this module's own "only zero rows is FAIL" philosophy --
# see run_input_quality_gate's docstring.
_VARCHAR_255_MAX_LEN = 255  # matches gl_code/gl_name's real VARCHAR(255) columns (database/schema.sql)
_VALUE_OUT_OF_RANGE_THRESHOLD = 10 ** 15
_SUSPICIOUSLY_FEW_ROWS_THRESHOLD = 5

# Case 2's incomplete-row fraction beyond which auto-processing requires
# accept_data_quality_risk=True.
CASE_2_INCOMPLETE_FRACTION_THRESHOLD = 0.25

# CASE_3 (grouping data lacks the taxonomy columns entirely) requires
# confirmation unconditionally, regardless of percentage.
CASE_3_REQUIRES_CONFIRMATION = True


def hint_is_complete(hint) -> bool:
    """True iff `hint` supplies every field Step 2 direct-resolution needs
    to treat this row as fully specified -- both sub_head_1 and sub_head_2
    present and non-empty. Presence only, never taxonomy validity."""
    if hint is None:
        return False
    return all(hint.known_fields.get(field_name) for field_name in _REQUIRED_HINT_FIELDS)


@dataclass(frozen=True)
class ArithmeticViolation:
    gl_code: str
    expected_closing: float
    actual_closing: float
    diff: float


@dataclass(frozen=True)
class QualityGateReport:
    status: QualityGateStatus
    tb_row_count: int
    unique_gl_count: int
    duplicate_gl_codes: list = field(default_factory=list)
    arithmetic_violations: list = field(default_factory=list)
    grouping_coverage_pct: float = 0.0
    unhinted_tb_row_count: int = 0
    orphan_grouping_gl_codes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # Ingestion error catalog additions -- all WARN-only (see module docstring).
    missing_gl_name_codes: list = field(default_factory=list)
    value_too_long: list = field(default_factory=list)  # [{gl_code, field, length}]
    value_out_of_range_gl_codes: list = field(default_factory=list)
    suspiciously_few_rows: bool = False
    blank_row_ratio: Optional[float] = None  # None when scanned_row_count wasn't supplied


def run_input_quality_gate(
    tb_rows: list, grouping_hints: dict, *, tolerance: float = DEFAULT_ARITHMETIC_TOLERANCE,
    scanned_row_count: Optional[int] = None,
) -> QualityGateReport:
    """Checks, in order: arithmetic (opening+debit-credit~=closing per
    row), duplicate GL codes, orphan grouping entries (a hint whose
    gl_code has no matching TB row), grouping coverage %, and the
    ingestion-error-catalog additions below (missing GL name, oversized
    values, too-few-rows, blank-row ratio). Only a completely empty TB
    (zero rows) is FAIL -- everything else, including every new check
    here, is WARN and never blocks ingestion on its own.

    `scanned_row_count` is the number of rows the caller scanned before
    filtering blanks/totals down to `tb_rows` (e.g. input_header_detect.py's
    build_tb_rows_from_grid) -- optional, since not every caller (e.g. a
    DB-sourced reload) has a meaningful "raw rows scanned" figure. Blank-row-
    ratio is skipped (blank_row_ratio stays None) when not supplied."""
    warnings: list = []
    total = len(tb_rows)

    if total == 0:
        return QualityGateReport(
            status="FAIL", tb_row_count=0, unique_gl_count=0, warnings=["TB has zero rows -- nothing to classify."],
        )

    gl_codes = [r.gl_code for r in tb_rows]
    seen: set = set()
    duplicates: list = []
    for gc in gl_codes:
        if gc in seen:
            duplicates.append(gc)
        else:
            seen.add(gc)
    if duplicates:
        warnings.append(f"{len(duplicates)} duplicate GL code(s) in TB: {duplicates[:10]}")

    arithmetic_violations: list = []
    for r in tb_rows:
        expected_closing = r.opening + r.debit - r.credit
        diff = abs(expected_closing - r.closing)
        if diff > tolerance:
            arithmetic_violations.append(ArithmeticViolation(gl_code=r.gl_code, expected_closing=expected_closing, actual_closing=r.closing, diff=diff))
    if arithmetic_violations:
        warnings.append(
            f"{len(arithmetic_violations)} row(s) fail opening+debit-credit≈closing "
            f"beyond tolerance={tolerance}: {[v.gl_code for v in arithmetic_violations[:10]]}"
        )

    tb_gl_set = set(gl_codes)
    orphan_grouping = sorted(gc for gc in grouping_hints if gc not in tb_gl_set)
    if orphan_grouping:
        warnings.append(f"{len(orphan_grouping)} grouping hint(s) have no matching TB row: {orphan_grouping[:10]}")

    hinted_count = sum(1 for gc in gl_codes if gc in grouping_hints)
    unhinted_count = total - hinted_count
    coverage_pct = round(100.0 * hinted_count / total, 2)
    if unhinted_count:
        warnings.append(f"grouping coverage {coverage_pct:.1f}% -- {unhinted_count} TB row(s) have no grouping hint at all")

    # MISSING_GL_NAME
    missing_gl_name = [r.gl_code for r in tb_rows if not r.gl_name or not r.gl_name.strip()]
    if missing_gl_name:
        warnings.append(f"{len(missing_gl_name)} row(s) have a GL Code but no GL Name: {missing_gl_name[:10]}")

    # VALUE_TOO_LONG -- only gl_code/gl_name exist on TBRow at this pre-classification
    # stage (main_head/sub_head_* are assigned later, by validation_gate.py).
    value_too_long: list = []
    for r in tb_rows:
        for field_name, value in (("gl_code", r.gl_code), ("gl_name", r.gl_name)):
            if value and len(value) > _VARCHAR_255_MAX_LEN:
                value_too_long.append({"gl_code": r.gl_code, "field": field_name, "length": len(value)})
    if value_too_long:
        warnings.append(f"{len(value_too_long)} value(s) exceed {_VARCHAR_255_MAX_LEN} characters: {value_too_long[:10]}")

    # VALUE_OUT_OF_RANGE
    value_out_of_range = sorted({
        r.gl_code for r in tb_rows
        if any(abs(v) >= _VALUE_OUT_OF_RANGE_THRESHOLD for v in (r.opening, r.debit, r.credit, r.closing))
    })
    if value_out_of_range:
        warnings.append(
            f"{len(value_out_of_range)} row(s) have a balance >= 10^15 -- please confirm units/decimal "
            f"placement: {value_out_of_range[:10]}"
        )

    # SUSPICIOUSLY_FEW_ROWS
    suspiciously_few_rows = total < _SUSPICIOUSLY_FEW_ROWS_THRESHOLD
    if suspiciously_few_rows:
        warnings.append(f"Only {total} data row(s) found -- unusually low for a trial balance.")

    # HIGH_BLANK_ROW_RATIO -- only computed when the caller supplied how many
    # raw rows it scanned before filtering down to `tb_rows`.
    blank_row_ratio = None
    if scanned_row_count is not None and scanned_row_count > 0:
        blank_row_ratio = round((scanned_row_count - total) / scanned_row_count, 4)
        if blank_row_ratio > 0.5:
            warnings.append(f"{blank_row_ratio:.0%} of scanned rows had no usable data.")

    return QualityGateReport(
        status="WARN" if warnings else "PASS", tb_row_count=total, unique_gl_count=len(seen),
        duplicate_gl_codes=duplicates, arithmetic_violations=arithmetic_violations, grouping_coverage_pct=coverage_pct,
        unhinted_tb_row_count=unhinted_count, orphan_grouping_gl_codes=orphan_grouping, warnings=warnings,
        missing_gl_name_codes=missing_gl_name, value_too_long=value_too_long,
        value_out_of_range_gl_codes=value_out_of_range, suspiciously_few_rows=suspiciously_few_rows,
        blank_row_ratio=blank_row_ratio,
    )


@dataclass(frozen=True)
class QualityTierAssessment:
    tier: DataQualityTier
    explanation: str
    reasons: list = field(default_factory=list)


def _case3_explanation(total: int) -> str:
    return (
        f"Grouping data does not have the taxonomy columns (BS/PL, Main Head, Sub Head 1, "
        f"Sub Head 2) structurally present -- all {total} TB row(s) require taxonomy resolution "
        "and/or LLM classification to map. Please verify the grouping data's own structure; the "
        "system's output reflects its best classification of the data provided, not a "
        "confirmation of its accuracy. Data authority remains with the source file."
    )


def _case2_explanation(total: int, incomplete: int) -> str:
    return (
        f"Grouping data has the required taxonomy columns, but {incomplete} of {total} TB row(s) "
        "are missing cell values -- those specific rows are routed through taxonomy matching "
        "and/or LLM classification; the remaining rows already had complete data supplied and are "
        "shown exactly as given, with no taxonomy validation applied to them. Please verify all "
        "rows against your own records -- data authority remains with the source file, not the "
        "system."
    )


def _case1_explanation(total: int) -> str:
    return (
        f"{total}/{total} TB row(s) had complete grouping data supplied -- output reflects a "
        "direct reshape of your own data, with no taxonomy validation, resolver, or LLM "
        "classification applied to any row. Please verify against your own records -- data "
        "authority remains with the source file, not the system."
    )


def classify_quality_tier(tb_rows: list, grouping_hints: dict, grouping_shape: Optional[GroupingShape]) -> QualityTierAssessment:
    """The single, pre-resolution routing decision: CASE_3 (grouping data
    doesn't have the taxonomy columns structurally present at all) / CASE_2
    (columns exist, but at least one TB row's hint is missing or
    incomplete) / CASE_1 (columns exist and every TB row already has a
    complete hint). `grouping_shape is None` is treated the same as "not
    structured" -- this function never routes a document to the
    zero-touch path from missing information."""
    total = len(tb_rows)

    if grouping_shape is not GroupingShape.STRUCTURED_TAXONOMY:
        return QualityTierAssessment(tier="CASE_3", explanation=_case3_explanation(total))

    incomplete = sum(1 for row in tb_rows if not hint_is_complete(grouping_hints.get(row.gl_code)))
    if incomplete > 0:
        return QualityTierAssessment(tier="CASE_2", explanation=_case2_explanation(total, incomplete))

    return QualityTierAssessment(tier="CASE_1", explanation=_case1_explanation(total))


@dataclass(frozen=True)
class ConfirmationRequirement:
    reason: str
    incomplete_count: Optional[int] = None
    total_count: Optional[int] = None
    incomplete_fraction: Optional[float] = None


def _case3_reason() -> str:
    return (
        "Grouping data lacks structured taxonomy columns (Sub Head 1/2, Main Head) entirely -- "
        "the whole document requires taxonomy resolution and/or LLM classification with no "
        "safely-skippable subset. Confirm to proceed, or improve the grouping data's structure."
    )


def _case2_overflow_reason(incomplete: int, total: int, fraction: float) -> str:
    pct = round(100 * fraction)
    threshold_pct = round(100 * CASE_2_INCOMPLETE_FRACTION_THRESHOLD)
    return (
        f"{pct}% of rows ({incomplete}/{total}) are missing Sub Head 1/2 -- exceeds the "
        f"{threshold_pct}% auto-processing threshold. Confirm to proceed at your own risk, or "
        "improve the source file's grouping coverage."
    )


def check_confirmation_requirement(tier_assessment: QualityTierAssessment, tb_rows: list, grouping_hints: dict) -> Optional[ConfirmationRequirement]:
    """None means proceed automatically (CASE_1, or CASE_2 at/under the
    threshold). Otherwise returns the reason a human must explicitly
    accept before the resolver/normalize/LLM pipeline may run at all."""
    if tier_assessment.tier == "CASE_3":
        if CASE_3_REQUIRES_CONFIRMATION:
            return ConfirmationRequirement(reason=_case3_reason())
        return None

    if tier_assessment.tier == "CASE_2":
        total = len(tb_rows)
        incomplete = sum(1 for row in tb_rows if not hint_is_complete(grouping_hints.get(row.gl_code)))
        fraction = incomplete / total if total else 0.0
        if fraction > CASE_2_INCOMPLETE_FRACTION_THRESHOLD:
            return ConfirmationRequirement(
                reason=_case2_overflow_reason(incomplete, total, fraction), incomplete_count=incomplete,
                total_count=total, incomplete_fraction=fraction,
            )
        return None

    return None  # CASE_1
