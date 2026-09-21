"""Classifies a grouping file's content shape from its already-detected
column map (input_header_detect.locate_header's `cols` dict), so hint-text
extraction dispatches to exactly one strategy per file instead of running
every extractor unconditionally and merging results.

Three real shapes, confirmed against real client exports:
- STRUCTURED_TAXONOMY: fully structured taxonomy columns (SUB HEAD 1, SUB
  HEAD 2, MAIN HEAD, BS/PL).
- FLAT_CATEGORY: a single flat "Grouping" category column (e.g. "Property
  plant and Equipment", "CWIP"), no discrete sub_head_1/sub_head_2/
  main_head columns.
- HIERARCHICAL_TRAIL: neither taxonomy nor category columns -- section-
  label rows above blocks of coded account rows, closed by "Total" rows.
  Also the fallback for a file with no real section trail either (its own
  algorithm degrades gracefully rather than crashing).

Ported from TB_normalization_v1's input/grouping_shape.py.
"""

from __future__ import annotations

from enum import Enum


class GroupingShape(Enum):
    STRUCTURED_TAXONOMY = "structured_taxonomy"
    FLAT_CATEGORY = "flat_category"
    HIERARCHICAL_TRAIL = "hierarchical_trail"


_TAXONOMY_FIELDS = ("sub_head_1", "sub_head_2", "main_head")


def classify_grouping_shape(cols: dict) -> GroupingShape:
    """Decides shape from the SAME cols map locate_header() already
    produces -- no separate column-scanning pass. Priority: discrete
    taxonomy columns beat a flat category column beat the hierarchical-
    trail fallback (a file could in principle carry both a taxonomy column
    and a generic "Grouping" column -- the taxonomy column is the
    stronger, already-resolved signal, so it wins)."""
    if any(field in cols for field in _TAXONOMY_FIELDS):
        return GroupingShape.STRUCTURED_TAXONOMY
    if "group_label" in cols:
        return GroupingShape.FLAT_CATEGORY
    return GroupingShape.HIERARCHICAL_TRAIL


__all__ = ["GroupingShape", "classify_grouping_shape"]
