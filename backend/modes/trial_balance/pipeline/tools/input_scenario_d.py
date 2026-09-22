"""Content-based multi-year / role detection ("Scenario D").

Scenario A/B/C dispatch assumes one fiscal year per file/folder. Real
client data breaks that assumption: a single workbook can pack multiple
fiscal years as separate sheets, in many different sheet-naming
conventions, with no reliable filename-level year signal to fall back on.
This module is the front door that discovers years and their TB/Grouping
sheet pairing purely from sheet-name (and, when a sheet name yields
nothing, file-name) content, then hands off one ParsedInput per detected
year to the SAME row/hint-extraction logic every other Scenario uses.

Never guesses: an unresolvable year, an ambiguous shared-grouping source,
or genuinely conflicting TB candidates all raise rather than pick
arbitrarily -- same discipline as every other input parser here.

Ported from TB_normalization_v1's input/multi_year_detect.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from modes.trial_balance.pipeline.tools.document_metadata import (
    DocumentMetadata,
    blank_metadata,
    with_resolved_framework,
)
from modes.trial_balance.pipeline.tools.input_grouping_shape import GroupingShape, classify_grouping_shape
from modes.trial_balance.pipeline.tools.input_header_detect import build_tb_rows_from_grid, locate_header, qualifies_as_tb_sheet
from modes.trial_balance.pipeline.tools.input_legacy_formats import load_all_sheets_any_format
from modes.trial_balance.pipeline.tools.input_scenario_b import read_grouping_hints
from modes.trial_balance.pipeline.tools.parsed_input import ParsedInput

logger = logging.getLogger(__name__)


# ---- 1. Year extraction -----------------------------------------------


@dataclass(frozen=True)
class DetectedYear:
    """A fiscal year identified from sheet/file-name content. `fy_label`
    is the canonical "FY2024-25" form regardless of source spelling."""

    fy_label: str
    fy_start_year: int
    fy_end_year: int
    source_text: str


def normalize_fy_label(start_year: int, end_year: int) -> str:
    return f"FY{start_year}-{str(end_year)[-2:]}"


# Most-specific first: a later pattern only gets a chance if an earlier,
# more specific one didn't match. India Schedule III's April-March fiscal
# year is assumed throughout.
_FY_4DIGIT_RANGE = re.compile(r"\bF\.?\s?Y[._ ]?\s?(\d{4})\s*[-–]\s*(\d{2,4})\b", re.IGNORECASE)
_FY_APOSTROPHE_SINGLE = re.compile(r"\bF\.?\s?Y[._ ]?\s?['’](\d{2})\b", re.IGNORECASE)
_FY_2DIGIT_RANGE = re.compile(r"\bF\.?\s?Y[._ ]?\s?(\d{2})\s*[-–]\s*(\d{2})\b", re.IGNORECASE)
_BARE_4DIGIT_RANGE = re.compile(r"(?<!\d)(\d{4})\s*[-–]\s*(\d{2,4})(?!\d)")
_TB_DATE = re.compile(r"\bTB\s+(\d{1,2})[ /](\d{1,2})[ /](\d{4})\b", re.IGNORECASE)
# A bare 2-digit range with no FY/TB anchor at all -- the highest false-
# positive risk of every pattern here, so tried dead last, and anchored to
# the END of the text, since a real fiscal-year range in these filenames
# is consistently the trailing token.
_BARE_2DIGIT_RANGE = re.compile(r"(?<!\d)(\d{2})\s*[-–]\s*(\d{2})\s*$")


def _distinct_fy_labels_in_text(text: str) -> set:
    """All distinct fiscal-year labels the 4-digit-range patterns find in
    `text` -- used only to detect a filename that names MORE THAN ONE
    fiscal year, so inventory_workbook() doesn't silently pin a year-less
    GROUPING sheet to whichever one extract_year_from_text's leftmost-
    match happens to find first."""
    labels: set = set()
    for pattern in (_FY_4DIGIT_RANGE, _BARE_4DIGIT_RANGE):
        for start_raw, end_raw in pattern.findall(text):
            start_year = int(start_raw)
            end_year = int(end_raw) if len(end_raw) == 4 else int(str(start_year)[:2] + end_raw)
            labels.add(normalize_fy_label(start_year, end_year))
    return labels


def _fy_from_end_year_2digit(end_2digit: int) -> tuple:
    """FY'24 means "the fiscal year ending in 2024" under the April-March
    convention -- i.e. FY2023-24, not a fiscal year that starts in 2024."""
    end_year = 2000 + end_2digit
    return end_year - 1, end_year


def _fy_from_date(day: int, month: int, year: int) -> tuple:
    """A fiscal-year-END date (e.g. 'TB 31 03 2026', no 'FY' token at
    all). April-March convention: a date in Jan-Mar closes out the fiscal
    year that started the previous calendar year; a date in Apr-Dec
    closes out the fiscal year ending the following calendar year."""
    if month >= 4:
        return year, year + 1
    return year - 1, year


def extract_year_from_text(text: str) -> Optional[DetectedYear]:
    """Tries each pattern in priority order; returns None (never a guess)
    if nothing matches."""
    if not text:
        return None

    m = _FY_4DIGIT_RANGE.search(text)
    if m:
        start_year = int(m.group(1))
        end_raw = m.group(2)
        end_year = int(end_raw) if len(end_raw) == 4 else int(str(start_year)[:2] + end_raw)
        return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    m = _FY_APOSTROPHE_SINGLE.search(text)
    if m:
        start_year, end_year = _fy_from_end_year_2digit(int(m.group(1)))
        return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    m = _FY_2DIGIT_RANGE.search(text)
    if m:
        start_year = 2000 + int(m.group(1))
        end_year = 2000 + int(m.group(2))
        return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    m = _TB_DATE.search(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start_year, end_year = _fy_from_date(day, month, year)
        return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    m = _BARE_4DIGIT_RANGE.search(text)
    if m:
        start_year = int(m.group(1))
        end_raw = m.group(2)
        end_year = int(end_raw) if len(end_raw) == 4 else int(str(start_year)[:2] + end_raw)
        return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    m = _BARE_2DIGIT_RANGE.search(text)
    if m:
        start_2digit, end_2digit = int(m.group(1)), int(m.group(2))
        if end_2digit == (start_2digit + 1) % 100:
            start_year, end_year = 2000 + start_2digit, 2000 + end_2digit
            return DetectedYear(normalize_fy_label(start_year, end_year), start_year, end_year, m.group(0))

    return None


# ---- 2. Sheet role classification ---------------------------------------


class SheetRole(Enum):
    TB = "tb"
    GROUPING = "grouping"
    UNKNOWN = "unknown"


def classify_sheet_role(grid: list) -> SheetRole:
    """Content signal only -- name-text is never authoritative. Reuses
    locate_header/REQUIRED_TB_FIELDS (TB signal) and
    classify_grouping_shape's discriminating columns (GROUPING signal)
    directly -- no new parsing logic duplicated."""
    header_row, cols = locate_header(grid, max_scan=min(15, len(grid)))
    if qualifies_as_tb_sheet(grid, header_row, cols):
        return SheetRole.TB
    if "gl_code" in cols and (
        any(f in cols for f in ("sub_head_1", "sub_head_2", "main_head", "bs_pl", "account_type"))
        or "group_label" in cols
    ):
        return SheetRole.GROUPING
    return SheetRole.UNKNOWN


# ---- 3. Per-workbook year+role inventory --------------------------------


@dataclass(frozen=True)
class YearRoleEntry:
    """One (year, role) finding for one sheet. `year=None` means no year
    could be extracted from either the sheet name or the file name -- a
    candidate for the shared/undated-grouping fallback below, never
    simply dropped."""

    year: Optional[DetectedYear]
    role: SheetRole
    source_path: Path
    sheet_name: Optional[str]
    header_row: int
    cols: dict


def inventory_workbook(path: Path) -> list:
    """Loads every sheet in `path` (format-agnostic, long-path-safe),
    classifies each sheet's role, and extracts a year from the sheet name
    first, falling back to the file name (handles single-sheet-per-file
    layouts with no year-bearing sheet name at all)."""
    sheets = load_all_sheets_any_format(path)
    entries: list = []
    file_year = extract_year_from_text(path.stem)
    file_names_multiple_years = len(_distinct_fy_labels_in_text(path.stem)) > 1
    for sheet_name, grid in sheets.items():
        if not grid:
            continue
        role = classify_sheet_role(grid)
        if role is SheetRole.UNKNOWN:
            continue
        sheet_year = extract_year_from_text(sheet_name)
        if sheet_year is not None:
            year = sheet_year
        elif role is SheetRole.GROUPING and file_names_multiple_years:
            # The sheet itself carries no year, and the filename
            # ambiguously names more than one fiscal year -- treat this as
            # a shared grouping source instead of silently pinning it to
            # whichever year extract_year_from_text's leftmost-match
            # happens to find.
            year = None
        else:
            year = file_year
        header_row, cols = locate_header(grid, max_scan=min(15, len(grid)))
        entries.append(YearRoleEntry(year, role, path, sheet_name, header_row, cols))
    return entries


def inventory_files(files: list) -> list:
    """Flattens inventory_workbook() across all supplied files -- what a
    caller with N files in one upload batch hands to pair_years()."""
    entries: list = []
    for f in files:
        entries.extend(inventory_workbook(f))
    return entries


# ---- 4. Year-pairing algorithm -------------------------------------------


@dataclass(frozen=True)
class YearPairing:
    year: DetectedYear
    tb_entry: YearRoleEntry
    grouping_entry: Optional[YearRoleEntry]
    grouping_is_shared: bool


class AmbiguousGroupingSourceError(Exception):
    """More than one undated/shared grouping source exists and no year-
    specific match was found for a given year -- refuses to guess which
    applies."""


class AmbiguousTbYearError(Exception):
    """>=2 TB entries share the same detected year and their parsed rows
    genuinely differ -- refuses to guess which is authoritative."""


def _entry_rows_for_comparison(entry: YearRoleEntry) -> list:
    """Re-loads just this entry's own sheet and builds full TBRows via the
    same shared helper build_parsed_input_for_year uses below."""
    sheets = load_all_sheets_any_format(entry.source_path, sheet_name=entry.sheet_name)
    grid = sheets[entry.sheet_name]
    return build_tb_rows_from_grid(grid, entry.header_row, entry.cols)


def _select_primary_tb_entry(tb_entries: list, year: DetectedYear) -> YearRoleEntry:
    first_rows = _entry_rows_for_comparison(tb_entries[0])
    for other in tb_entries[1:]:
        if _entry_rows_for_comparison(other) != first_rows:
            raise AmbiguousTbYearError(
                f"{len(tb_entries)} TB sheets/files all detected as {year.fy_label} but their parsed "
                f"rows differ -- refusing to guess which is authoritative: "
                f"{[(e.source_path.name, e.sheet_name) for e in tb_entries]}"
            )
    return min(tb_entries, key=lambda e: (len(e.source_path.name), e.source_path.name, e.sheet_name or ""))


def pair_years(entries: list) -> list:
    """1. Group TB entries by year (entries with year=None are excluded).
       2. >1 TB entry for the same year -> resolve via
          _select_primary_tb_entry (duplicate content: safe pick; genuine
          difference: AmbiguousTbYearError, never a guess).
       3. Same-year GROUPING entry exists -> use it, grouping_is_shared=False.
       4. No year-specific match -> look at ALL year=None GROUPING entries
          across the whole input set: exactly one -> use it for every
          unmatched year, shared=True; zero -> grouping_entry=None, warn;
          more than one -> AmbiguousGroupingSourceError.
       Returns one YearPairing per distinct TB year found."""
    tb_by_year: dict = {}
    for e in entries:
        if e.role is not SheetRole.TB:
            continue
        if e.year is None:
            logger.warning(
                "TB sheet/file %s#%s has no determinable fiscal year (checked sheet name and file name) -- excluded.",
                e.source_path.name, e.sheet_name,
            )
            continue
        tb_by_year.setdefault(e.year.fy_label, []).append(e)

    grouping_entries = [e for e in entries if e.role is SheetRole.GROUPING]
    grouping_by_year: dict = {}
    shared_grouping_candidates: list = []
    for e in grouping_entries:
        if e.year is not None:
            grouping_by_year.setdefault(e.year.fy_label, []).append(e)
        else:
            shared_grouping_candidates.append(e)

    pairings: list = []
    for fy_label, tb_entries in tb_by_year.items():
        year = tb_entries[0].year
        tb_entry = _select_primary_tb_entry(tb_entries, year) if len(tb_entries) > 1 else tb_entries[0]

        year_specific = grouping_by_year.get(fy_label)
        if year_specific:
            grouping_entry = year_specific[0]
            grouping_is_shared = False
        elif len(shared_grouping_candidates) == 1:
            grouping_entry = shared_grouping_candidates[0]
            grouping_is_shared = True
        elif len(shared_grouping_candidates) == 0:
            grouping_entry = None
            grouping_is_shared = False
            logger.warning("No grouping source found for %s (checked year-specific and shared sources).", fy_label)
        else:
            raise AmbiguousGroupingSourceError(
                f"No year-specific grouping match for {fy_label}, and {len(shared_grouping_candidates)} "
                f"undated/shared grouping sources exist -- refusing to guess which applies: "
                f"{[(e.source_path.name, e.sheet_name) for e in shared_grouping_candidates]}"
            )

        pairings.append(YearPairing(year, tb_entry, grouping_entry, grouping_is_shared))

    return pairings


# ---- 5. Metadata construction per year -----------------------------------


def build_parsed_input_for_year(pairing: YearPairing, *, base_metadata: Optional[DocumentMetadata] = None) -> ParsedInput:
    """Row/hint extraction reuses the SAME shared helpers every other
    Scenario already uses -- no third copy of either.

    fy_period_start/fy_period_end/financial_year are ALWAYS derived from
    pairing.year, never taken from base_metadata even if it has its own
    values there -- this is the one thing that MUST vary per year. Every
    other field is inherited from base_metadata as-is."""
    tb_sheets = load_all_sheets_any_format(pairing.tb_entry.source_path, sheet_name=pairing.tb_entry.sheet_name)
    tb_grid = tb_sheets[pairing.tb_entry.sheet_name]
    tb_rows = build_tb_rows_from_grid(tb_grid, pairing.tb_entry.header_row, pairing.tb_entry.cols)

    if pairing.grouping_entry is not None:
        grouping_sheets = load_all_sheets_any_format(
            pairing.grouping_entry.source_path, sheet_name=pairing.grouping_entry.sheet_name,
        )
        grouping_hints = read_grouping_hints(grouping_sheets[pairing.grouping_entry.sheet_name])
        grouping_shape = classify_grouping_shape(pairing.grouping_entry.cols)
    elif classify_grouping_shape(pairing.tb_entry.cols) is not GroupingShape.HIERARCHICAL_TRAIL:
        # No separate year-specific or shared grouping SHEET/FILE was
        # paired for this year -- but the TB sheet itself may embed a full
        # grouping block side-by-side with the TB columns, sharing the
        # same rows. classify_sheet_role() always classifies such a sheet
        # as TB (checked first), so it never becomes a grouping_entry on
        # its own -- mirrors Scenario C's re-read-the-same-grid pattern,
        # applied here for Scenario D's per-year sheets too.
        #
        # Gated on classify_grouping_shape(pairing.tb_entry.cols)
        # explicitly -- NOT applied unconditionally -- because
        # extract_hint_text's fallback for a HIERARCHICAL_TRAIL shape (no
        # taxonomy/category columns present at all) is a blind section-
        # label trail-scan designed for genuine standalone grouping files;
        # running it against a plain TB-only sheet's own grid produces
        # spurious "hint text" from whatever stray non-numeric cells
        # happen to appear. Only engage when the TB sheet's own cols
        # already show a genuine STRUCTURED_TAXONOMY or FLAT_CATEGORY
        # shape.
        grouping_hints = read_grouping_hints(tb_grid)
        grouping_shape = classify_grouping_shape(pairing.tb_entry.cols)
    else:
        grouping_hints = {}
        grouping_shape = GroupingShape.HIERARCHICAL_TRAIL

    if base_metadata is None:
        base_metadata = blank_metadata()

    metadata = replace(
        base_metadata,
        fy_period_start=datetime(pairing.year.fy_start_year, 4, 1),
        fy_period_end=datetime(pairing.year.fy_end_year, 3, 31),
        financial_year=pairing.year.fy_label,
        # Reflects whether grouping evidence was ACTUALLY found, not just
        # whether a separate grouping_entry object was paired -- the
        # embedded-in-the-TB-sheet fallback above can populate
        # grouping_hints with pairing.grouping_entry still None, and that
        # must count as "has grouping" too.
        has_grouping=bool(grouping_hints),
        tb_doc_name=_doc_label(pairing.tb_entry),
        grouping_doc_name=_doc_label(pairing.grouping_entry) if pairing.grouping_entry else None,
    )
    metadata = with_resolved_framework(metadata, explicit=metadata.framework, heuristic_grid=tb_grid)

    return ParsedInput(metadata=metadata, tb_rows=tb_rows, grouping_hints=grouping_hints, grouping_shape=grouping_shape)


def _doc_label(entry: YearRoleEntry) -> str:
    """'<file>.xlsx#<sheet>' for a multi-sheet source, plain filename for
    a single-sheet-per-file source."""
    if entry.sheet_name and entry.sheet_name != entry.source_path.stem:
        return f"{entry.source_path.name}#{entry.sheet_name}"
    return entry.source_path.name


def parse_multi_year(files: list, *, base_metadata: Optional[DocumentMetadata] = None) -> list:
    """Top-level Scenario D entry point: inventory_files -> pair_years ->
    build_parsed_input_for_year per pairing. Returns one ParsedInput per
    detected year. Raises AmbiguousGroupingSourceError/AmbiguousTbYearError
    rather than guessing when the input set is genuinely ambiguous."""
    entries = inventory_files(files)
    pairings = pair_years(entries)
    return [build_parsed_input_for_year(p, base_metadata=base_metadata) for p in pairings]
