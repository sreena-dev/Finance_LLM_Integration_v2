"""Scenario A adapter: the normalized `TB_GROUPING_TEMPLATE.xlsx` (one
workbook, `COMPANY_DETAILS` + `TB&GROUPING-TEMPLATE` sheets) -> ParsedInput.

Not a separate classification code path -- the template's own 5 taxonomy
columns (BS/PL, SUB HEAD 2, SUB HEAD 1, MAIN HEAD, Account type) simply
become that GL's GroupingHint.known_fields, so normalize()'s existing Step
2 direct-resolution handles them with no special-casing. The template's
own "Account type" column is read into `template_account_type` for
informational comparison only -- it is NEVER written into
known_fields["account_type"] (which the engine never reads from
GroupingHint in the first place), since account_type is always derived.

Ported from TB_normalization_v1's input/normalized_template.py.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from modes.trial_balance.pipeline.tools.document_metadata import parse_company_details, with_resolved_framework
from modes.trial_balance.pipeline.tools.input_grouping_shape import classify_grouping_shape
from modes.trial_balance.pipeline.tools.input_grouping_source import normalize_grouping_cell_value
from modes.trial_balance.pipeline.tools.input_header_detect import (
    REQUIRED_TB_FIELDS,
    is_total_row_label,
    locate_header,
    parse_amount,
    parse_gl_code,
)
from modes.trial_balance.pipeline.tools.input_legacy_formats import load_all_sheets_any_format
from modes.trial_balance.pipeline.tools.input_long_path import to_long_path
from modes.trial_balance.pipeline.tools.parsed_input import ParsedInput
from modes.trial_balance.pipeline.tools.tb_models import GroupingHint, TBRow

COMPANY_DETAILS_SHEET = "COMPANY_DETAILS"
TB_GROUPING_SHEET = "TB&GROUPING-TEMPLATE"

_GROUPING_FIELDS = ("bs_pl", "sub_head_1", "sub_head_2", "main_head")


class TemplateStructureError(Exception):
    """The workbook does not satisfy the normalized-template contract (a
    required sheet, or a required TB column, is missing)."""


def _load_grid(ws) -> list:
    return [[cell.value for cell in row] for row in ws.iter_rows()]


def _cell(row: list, cols: dict, field_name: str):
    idx = cols.get(field_name)
    return row[idx] if idx is not None and idx < len(row) else None


def parse_template(path: Path) -> ParsedInput:
    try:
        wb = openpyxl.load_workbook(to_long_path(path), data_only=True, read_only=True)
    except Exception:
        # Scenario A's template contract is .xlsx-only in practice, but
        # route through the same content-sniffing loader for consistency
        # rather than leaving a silent gap.
        sheets = load_all_sheets_any_format(path)
        missing = {COMPANY_DETAILS_SHEET, TB_GROUPING_SHEET} - set(sheets.keys())
        if missing:
            raise TemplateStructureError(
                f"{path.name}: missing required sheet(s) {sorted(missing)} for the normalized template contract"
            )
        company_grid = sheets[COMPANY_DETAILS_SHEET]
        tb_grid = sheets[TB_GROUPING_SHEET]
    else:
        missing = {COMPANY_DETAILS_SHEET, TB_GROUPING_SHEET} - set(wb.sheetnames)
        if missing:
            raise TemplateStructureError(
                f"{path.name}: missing required sheet(s) {sorted(missing)} for the normalized template contract"
            )
        company_grid = _load_grid(wb[COMPANY_DETAILS_SHEET])
        tb_grid = _load_grid(wb[TB_GROUPING_SHEET])

    metadata = parse_company_details(company_grid)

    header_row, cols = locate_header(tb_grid, max_scan=min(15, len(tb_grid)))
    if not REQUIRED_TB_FIELDS.issubset(cols.keys()):
        missing_fields = REQUIRED_TB_FIELDS - cols.keys()
        raise TemplateStructureError(
            f"{path.name}: {TB_GROUPING_SHEET!r} is missing required TB column(s) {sorted(missing_fields)}"
        )

    tb_rows: list = []
    grouping_hints: dict = {}
    template_account_type: dict = {}
    warnings: list = []
    seen_gl_codes: set = set()

    for row in tb_grid[header_row + 1:]:
        gl_code_raw = row[cols["gl_code"]] if cols.get("gl_code") is not None and cols["gl_code"] < len(row) else None
        if gl_code_raw in (None, ""):
            continue
        gl_code = parse_gl_code(gl_code_raw)
        if gl_code in seen_gl_codes:
            warnings.append(f"Duplicate GL code {gl_code!r} in {TB_GROUPING_SHEET!r} -- later row wins")
        seen_gl_codes.add(gl_code)

        gl_name_value = _cell(row, cols, "gl_name")
        gl_name = str(gl_name_value).strip() if gl_name_value not in (None, "") else ""
        if is_total_row_label(gl_name):
            continue
        tb_rows.append(TBRow(
            gl_code=gl_code, gl_name=gl_name, opening=parse_amount(_cell(row, cols, "opening")),
            debit=parse_amount(_cell(row, cols, "debit")), credit=parse_amount(_cell(row, cols, "credit")),
            closing=parse_amount(_cell(row, cols, "closing")),
        ))

        known_fields: dict = {}
        for grouping_field in _GROUPING_FIELDS:
            value = _cell(row, cols, grouping_field)
            if value not in (None, ""):
                cleaned = normalize_grouping_cell_value(value)
                if cleaned is not None:
                    known_fields[grouping_field] = cleaned
        if known_fields:
            grouping_hints[gl_code] = GroupingHint(gl_code=gl_code, known_fields=known_fields, hint_text="")

        account_type_value = _cell(row, cols, "account_type")
        if account_type_value not in (None, ""):
            template_account_type[gl_code] = str(account_type_value).strip()

    metadata = with_resolved_framework(metadata, heuristic_grid=tb_grid)
    metadata = _with_source_names(metadata, path)

    return ParsedInput(
        metadata=metadata, tb_rows=tb_rows, grouping_hints=grouping_hints, warnings=warnings,
        template_account_type=template_account_type, grouping_shape=classify_grouping_shape(cols),
    )


def _with_source_names(metadata, path: Path):
    from dataclasses import replace

    return replace(metadata, tb_doc_name=path.name, grouping_doc_name=path.name, has_grouping=True)
