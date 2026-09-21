"""Top-level input dispatcher: given a caller-supplied list of file paths,
auto-detects which Scenario (A/B/C/D) they are and returns one ParsedInput
per detected fiscal year.

Ported from TB_normalization_v1's modes.trial_balance.pipeline.py (_parse_single_file,
_parse_scenario_b, ingest_tb_backend/ingest_tb_backend_auto's dispatch
logic) -- the classification/DB-write portions of modes.trial_balance.pipeline.py are NOT
ported here; this module only parses. The caller (ingest_tb_to_live, the
single entry point for both live and uploaded documents) runs
classify_document.classify_tb_document on the result itself.
"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import Optional

from modes.trial_balance.pipeline.tools._shared import Standard
from modes.trial_balance.pipeline.tools.document_metadata import DocumentMetadata
from modes.trial_balance.pipeline.tools.input_scenario_a import TemplateStructureError, parse_template
from modes.trial_balance.pipeline.tools.input_scenario_b import parse_tb_grouping_pair
from modes.trial_balance.pipeline.tools.input_scenario_c import parse_combined_workbook
from modes.trial_balance.pipeline.tools.input_scenario_d import inventory_files, parse_multi_year
from modes.trial_balance.pipeline.tools.parsed_input import ParsedInput


class UnsupportedInputError(Exception):
    """The input file count doesn't match any supported scenario contract
    (exactly 1 or 2 files for single-year; any count for multi-year, which
    is detected content-first, not by count)."""


def _apply_metadata_overrides(parsed_metadata: DocumentMetadata, override: DocumentMetadata) -> DocumentMetadata:
    """Caller-supplied metadata fields win over parsed ones where the
    caller actually set them (explicit > inferred); fields the caller left
    None fall back to whatever the input adapter already resolved."""
    overrides = {f.name: getattr(override, f.name) for f in fields(override) if getattr(override, f.name) is not None}
    return replace(parsed_metadata, **overrides)


def _parse_single_file(input_files: list, metadata: Optional[DocumentMetadata]) -> ParsedInput:
    """Scenario A (normalized template) if the file has the expected
    COMPANY_DETAILS/TB&GROUPING-TEMPLATE sheets, else Scenario C (single
    combined sheet).

    Scenario A merges caller-supplied `metadata` on top of what
    COMPANY_DETAILS provided (explicit > inferred). Scenario C does NOT do
    this merge -- like Scenario B, there is no COMPANY_DETAILS sheet to
    derive a base from, so `metadata` is passed straight into
    parse_combined_workbook as the base object itself."""
    try:
        parsed = parse_template(input_files[0])
        if metadata is not None:
            parsed = replace(parsed, metadata=_apply_metadata_overrides(parsed.metadata, metadata))
    except TemplateStructureError:
        parsed = parse_combined_workbook(input_files[0], metadata=metadata)
    return parsed


def _parse_scenario_b(input_files: list, metadata: Optional[DocumentMetadata]) -> ParsedInput:
    from modes.trial_balance.pipeline.tools.input_scenario_b import classify_workbooks

    tb_path, grouping_path = classify_workbooks(input_files)
    return parse_tb_grouping_pair(tb_path, grouping_path, metadata=metadata)


def parse_tb_input_single_year(
    input_files: list, metadata: Optional[DocumentMetadata] = None, framework: Optional[Standard] = None,
) -> ParsedInput:
    """Single-year dispatch (Scenario A/B/C), never runs the multi-year
    inventory check itself -- use parse_tb_input for automatic multi-year
    routing."""
    if len(input_files) == 1:
        parsed = _parse_single_file(input_files, metadata)
    elif len(input_files) == 2:
        parsed = _parse_scenario_b(input_files, metadata)
    else:
        raise UnsupportedInputError(
            f"Expected either one normalized-template/combined file, or exactly one TB + one "
            f"Grouping file; got {len(input_files)} files."
        )
    if framework is not None:
        from modes.trial_balance.pipeline.tools.document_metadata import with_resolved_framework

        parsed = replace(parsed, metadata=with_resolved_framework(parsed.metadata, explicit=framework))
    return parsed


def parse_tb_input(
    input_files: list, metadata: Optional[DocumentMetadata] = None, framework: Optional[Standard] = None,
) -> list:
    """Automatic entry point: runs a cheap content-based inventory check
    over `input_files` and routes to Scenario D (parse_multi_year) if it
    finds more than one distinct TB-role fiscal year, else falls through
    to the single-year dispatch (wrapped in a single-element list for a
    uniform return type). Returns a list of ParsedInput, one per detected
    fiscal year."""
    input_files = [Path(f) for f in input_files]
    entries = inventory_files(input_files)
    distinct_years = {e.year.fy_label for e in entries if e.role.value == "tb" and e.year is not None}
    if len(distinct_years) > 1:
        results = parse_multi_year(input_files, base_metadata=metadata)
        if framework is not None:
            from modes.trial_balance.pipeline.tools.document_metadata import with_resolved_framework

            results = [replace(r, metadata=with_resolved_framework(r.metadata, explicit=framework)) for r in results]
        return results
    return [parse_tb_input_single_year(input_files, metadata, framework)]
