"""Ingestion error catalog (TB-v2-git/Trial_Balance_ingestion_error.md)
additions that need raw workbook/cell metadata `TBRow`/`quality_gate.py`
don't carry -- header-row ambiguity, merged cells, duplicate header names,
hidden/protected sheets, and unresolved Excel formula errors.

Purely additive WARNING findings. Never raises, never blocks ingestion --
matches quality_gate.py's own "only zero rows is FAIL" philosophy. Called
once per input file from ingest_tb_to_live (db_bridge.py), after
parse_tb_input has already succeeded (so the file is known-readable) --
this re-scans the same file independently rather than threading new
parameters through every input_scenario_*.py adapter, to keep this
feature's blast radius to one new call site.

Reuses input_header_detect.py's own scoring (`locate_header`,
`header_confidence`, `_norm`) and input_legacy_formats.py's
`load_all_sheets_any_format` rather than re-implementing header detection;
only the openpyxl-specific metadata (merge ranges, sheet visibility/
protection, cached formula-error values) needs a dedicated read, since
none of the existing helpers expose it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from modes.trial_balance.pipeline.tools.input_header_detect import _norm, header_confidence, locate_header, qualifies_as_tb_sheet
from modes.trial_balance.pipeline.tools.input_legacy_formats import load_all_sheets_any_format

_EXCEL_ERROR_VALUES = {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}


def _add(findings: list, code: str, message: str, sheet: Optional[str] = None) -> None:
    entry = {"code": code, "severity": "WARNING", "message": message}
    if sheet is not None:
        entry["sheet"] = sheet
    findings.append(entry)


def _check_header_ambiguity(grid: list, header_row: int, sheet_name: str, findings: list) -> None:
    """MULTIPLE_HEADER_CANDIDATES: a second row also clears locate_header's
    own scoring bar (re-scans the same window locate_header already
    scanned, using its own header_confidence, rather than inventing a
    separate threshold)."""
    limit = min(15, len(grid))
    from modes.trial_balance.pipeline.tools.input_header_detect import FIELD_ALIASES, _ALL_ALIASES, _PREFIX_FIELDS, _matches

    for r in range(limit):
        if r == header_row:
            continue
        row = grid[r]
        cols: dict = {}
        for c, cell in enumerate(row):
            norm = _norm(cell)
            if not norm:
                continue
            is_alias = norm in _ALL_ALIASES or any(norm.startswith(f) for f in _PREFIX_FIELDS)
            if not is_alias:
                continue
            for field_name in FIELD_ALIASES:
                if field_name not in cols and _matches(norm, field_name):
                    cols[field_name] = c
        if not cols:
            continue
        score = header_confidence(grid, r, cols)
        if score > 0:
            _add(
                findings, "MULTIPLE_HEADER_CANDIDATES",
                f"Row {r} also looks like a plausible header (row {header_row} was used) -- "
                "please confirm the correct row was read.",
                sheet=sheet_name,
            )
            return  # one finding per sheet is enough signal


def _check_duplicate_header_names(grid: list, header_row: int, sheet_name: str, findings: list) -> None:
    if header_row >= len(grid):
        return
    names = [_norm(v) for v in grid[header_row]]
    seen: set = set()
    dupes: set = set()
    for n in names:
        if not n:
            continue
        if n in seen:
            dupes.add(n)
        seen.add(n)
    if dupes:
        _add(
            findings, "DUPLICATE_HEADER_NAME",
            f"Header row has repeated column name(s) {sorted(dupes)} -- only the first occurrence is used.",
            sheet=sheet_name,
        )


def _check_openpyxl_metadata(path: Path, sheets: dict, findings: list) -> None:
    """MERGED_HEADER_CELLS / HIDDEN_SHEET_WITH_DATA / PROTECTED_SHEET /
    FORMULA_ERROR_VALUE -- needs a dedicated openpyxl read for merge-range
    topology, sheet visibility/protection flags, and cached error-cell
    values, none of which load_all_sheets_any_format's plain grid exposes.
    Only meaningful for .xlsx/.xlsm (openpyxl); no-ops for .xls/.xlsb, same
    scope restriction as the old Layer-4 Formula Flag this is modeled on.
    Never raises -- best-effort informational scan only."""
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except Exception:
        return

    try:
        for sheet_name, grid in sheets.items():
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]

            header_row, cols = locate_header(grid, max_scan=15)
            if cols and ws.merged_cells.ranges:
                for merged_range in ws.merged_cells.ranges:
                    if merged_range.min_row - 1 <= header_row <= merged_range.max_row - 1:
                        _add(
                            findings, "MERGED_HEADER_CELLS",
                            f"Header row on sheet '{sheet_name}' has merged cell(s) -- please verify "
                            "the mapped columns.",
                            sheet=sheet_name,
                        )
                        break

            if ws.sheet_state != "visible" and cols and qualifies_as_tb_sheet(grid, header_row, cols):
                _add(
                    findings, "HIDDEN_SHEET_WITH_DATA",
                    f"Hidden sheet '{sheet_name}' looks like Trial Balance data -- please confirm the "
                    "correct sheet is visible before uploading.",
                    sheet=sheet_name,
                )

            if ws.protection.sheet:
                _add(
                    findings, "PROTECTED_SHEET",
                    f"Sheet '{sheet_name}' is protected -- please verify its contents were read correctly.",
                    sheet=sheet_name,
                )

            error_cells = [
                cell.coordinate
                for row in ws.iter_rows()
                for cell in row
                if isinstance(cell.value, str) and cell.value.strip().upper() in _EXCEL_ERROR_VALUES
            ]
            if error_cells:
                _add(
                    findings, "FORMULA_ERROR_VALUE",
                    f"Sheet '{sheet_name}' has {len(error_cells)} unresolved formula error cell(s) "
                    f"({error_cells[:10]}) -- please fix the source formula before uploading.",
                    sheet=sheet_name,
                )
    finally:
        wb.close()


def assess_ingestion_intake(file_path: str) -> list:
    """Runs every WARNING-only intake check against `file_path` and returns
    a flat list of {code, severity, message, sheet?} findings. Never
    raises -- any failure to even open the file for this secondary scan is
    swallowed (the primary parse via parse_tb_input already proved the
    file is readable; this is a best-effort quality pass on top of that,
    not a second gate)."""
    path = Path(file_path)
    findings: list = []
    try:
        sheets = load_all_sheets_any_format(path)
    except Exception:
        return findings

    for sheet_name, grid in sheets.items():
        if not grid:
            continue
        header_row, cols = locate_header(grid, max_scan=min(15, len(grid)))
        if not cols:
            continue
        _check_header_ambiguity(grid, header_row, sheet_name, findings)
        _check_duplicate_header_names(grid, header_row, sheet_name, findings)

    _check_openpyxl_metadata(path, sheets, findings)
    return findings
