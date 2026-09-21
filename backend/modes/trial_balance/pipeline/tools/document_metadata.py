"""Parses the `COMPANY_DETAILS` key/value sheet (a 2-column sheet with a
title row, then one label/value pair per row -- NOT a header-row table)
into `DocumentMetadata`, and resolves the accounting framework (AS vs
IND_AS) per an explicit precedence rule.

Missing metadata stays missing/explicit -- never invented. Framework
resolution is made auditable via `FrameworkResolution` rather than
silently picked.

Ported from TB_normalization_v1's input/metadata.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from modes.trial_balance.pipeline.tools._shared import Standard, detect_standard

logger = logging.getLogger(__name__)


class FrameworkResolution(str, Enum):
    """How `DocumentMetadata.framework` was decided -- recorded for audit,
    not just the final framework value."""

    EXPLICIT = "explicit"
    COMPANY_DETAILS = "company_details"
    HEURISTIC = "heuristic"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class DocumentMetadata:
    entity_id: Optional[str]
    entity_name: Optional[str]
    cin: Optional[str]
    company_name: Optional[str]
    fy_period_start: Optional[datetime]
    fy_period_end: Optional[datetime]
    financial_year: Optional[str]
    statement_type: Optional[str]
    company_standards_raw: Optional[str]
    framework: Optional[Standard]
    framework_resolution: FrameworkResolution
    tb_doc_name: Optional[str] = None
    grouping_doc_name: Optional[str] = None
    has_grouping: bool = False


def blank_metadata() -> DocumentMetadata:
    """The zero-evidence starting point Scenario B/C/D use when the caller
    supplies no metadata of their own -- there is no COMPANY_DETAILS sheet
    in those scenarios."""
    return DocumentMetadata(
        entity_id=None, entity_name=None, cin=None, company_name=None,
        fy_period_start=None, fy_period_end=None, financial_year=None, statement_type=None,
        company_standards_raw=None, framework=None, framework_resolution=FrameworkResolution.UNRESOLVED,
    )


_JS_DATE_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _parse_js_date_string(value) -> Optional[datetime]:
    """Best-effort parse of a JS `Date.prototype.toString()` value (a real
    template export format, e.g. "Sun Mar 30 2025 05:30:00 GMT+0530 (India
    Standard Time)"). Returns a naive UTC datetime -- never raises; an
    unparseable value logs a warning and returns None rather than
    inventing a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    text = _JS_DATE_RE.sub("", str(value).strip())
    try:
        dt = datetime.strptime(text, "%a %b %d %Y %H:%M:%S GMT%z")
    except ValueError:
        logger.warning("Could not parse date value %r -- leaving field as None, not guessing.", value)
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_BARE_YEAR_RE = re.compile(r"^\d{4}(\.0)?$")


def _parse_period_boundary(value, month_day: str) -> Optional[datetime]:
    """PERIOD_START/PERIOD_END (TB_normalization_v1's own template
    convention) carry a full JS-date-string value; FY_START/FY_END
    (TB-v2-git's existing template convention, checked as a fallback
    below when PERIOD_START/PERIOD_END is absent) carries a bare year
    instead -- expanded here to a full date using the same April-to-March
    fiscal year convention this codebase assumes throughout. Checked
    before attempting the JS-date parse so a legitimate bare year doesn't
    spuriously trigger _parse_js_date_string's own "could not parse"
    warning log."""
    if value is None:
        return None
    text = str(value).strip()
    if _BARE_YEAR_RE.match(text):
        month, day = (int(p) for p in month_day.split("-"))
        return datetime(int(float(text)), month, day)
    return _parse_js_date_string(value)


def parse_company_details(grid: list) -> DocumentMetadata:
    """`grid` is the raw COMPANY_DETAILS sheet grid (row-major cell
    values). Every row with a non-None value in column B is treated as a
    label/value pair -- this naturally skips the sheet's own title row
    (label with no value) without hardcoding a specific title string.

    Returns framework=None / framework_resolution=UNRESOLVED as a
    placeholder -- callers must call resolve_framework themselves once
    they can supply a heuristic-fallback grid, since COMPANY_DETAILS alone
    cannot always disambiguate."""
    fields: dict = {}
    for row in grid:
        if not row:
            continue
        label = _clean(row[0])
        value = row[1] if len(row) > 1 else None
        if label is None or value is None:
            continue
        # Space-normalized to underscores -- TB_normalization_v1's own
        # template already uses underscored labels ("COMPANY_NAME") and
        # needs no folding, but TB-v2-git's existing TB_GROUPING_TEMPLATE.xlsx
        # sample data uses human-readable spaced labels ("Company Name",
        # "FY Start") for the same fields. Folding both to one lookup key
        # is a strict superset (no real label needs internal spaces
        # preserved as distinct from underscores), so this accepts either
        # convention rather than silently dropping company/entity data
        # from templates authored the TB-v2-git way.
        key = re.sub(r"\s+", "_", label.strip().upper())
        fields[key] = value

    return DocumentMetadata(
        entity_id=_clean(fields.get("ENTITY_ID")),
        entity_name=_clean(fields.get("ENTITY_NAME")),
        cin=_clean(fields.get("CIN")),
        company_name=_clean(fields.get("COMPANY_NAME")),
        fy_period_start=_parse_period_boundary(fields.get("PERIOD_START") or fields.get("FY_START"), "04-01"),
        fy_period_end=_parse_period_boundary(fields.get("PERIOD_END") or fields.get("FY_END"), "03-31"),
        financial_year=_clean(fields.get("FINANCIAL_YEAR")),
        statement_type=_clean(fields.get("STATEMENT_TYPE")),
        company_standards_raw=_clean(fields.get("COMPANY_STANDARDS")),
        framework=None,
        framework_resolution=FrameworkResolution.UNRESOLVED,
    )


_STANDARD_ALIASES: dict = {
    "as": "AS",
    "ind as": "IND_AS",
    "ind_as": "IND_AS",
    "indas": "IND_AS",
}


def _parse_single_standard(raw: Optional[str]) -> Optional[Standard]:
    """A `/`-joined value (e.g. "IND AS/ AS") is explicitly ambiguous --
    never parsed positionally, never guessed at."""
    if not raw or "/" in raw:
        return None
    return _STANDARD_ALIASES.get(re.sub(r"\s+", " ", raw.strip().lower()))


def resolve_framework(
    *,
    explicit: Optional[Standard] = None,
    company_standards_raw: Optional[str] = None,
    heuristic_grid: Optional[list] = None,
) -> tuple:
    """Precedence, in order: (1) explicit caller override, (2) an
    unambiguous single value parsed from COMPANY_STANDARDS, (3) a
    vocabulary heuristic over `heuristic_grid` if supplied, (4)
    unresolved. Never silently defaults -- callers must handle AMBIGUOUS/
    UNRESOLVED explicitly."""
    if explicit is not None:
        return explicit, FrameworkResolution.EXPLICIT

    parsed = _parse_single_standard(company_standards_raw)
    if parsed is not None:
        return parsed, FrameworkResolution.COMPANY_DETAILS

    if heuristic_grid is not None:
        return detect_standard(heuristic_grid), FrameworkResolution.HEURISTIC

    if company_standards_raw and "/" in company_standards_raw:
        return None, FrameworkResolution.AMBIGUOUS

    return None, FrameworkResolution.UNRESOLVED


def with_resolved_framework(
    metadata: DocumentMetadata, *, explicit: Optional[Standard] = None, heuristic_grid: Optional[list] = None,
) -> DocumentMetadata:
    framework, resolution = resolve_framework(
        explicit=explicit, company_standards_raw=metadata.company_standards_raw, heuristic_grid=heuristic_grid,
    )
    return replace(metadata, framework=framework, framework_resolution=resolution)
