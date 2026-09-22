"""ParsedInput: the common output shape every Scenario (A/B/C/D) parser
produces -- one TB row list, one grouping-hint map, resolved metadata, and
the grouping shape classify_document.py's quality tier needs.

Ported from TB_normalization_v1's input/normalized_template.py (where
ParsedInput was originally defined).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from modes.trial_balance.pipeline.tools.document_metadata import DocumentMetadata
from modes.trial_balance.pipeline.tools.input_grouping_shape import GroupingShape


@dataclass(frozen=True)
class ParsedInput:
    metadata: DocumentMetadata
    tb_rows: list
    grouping_hints: dict
    warnings: list = field(default_factory=list)
    # Scenario A's own "Account type" column, read for informational
    # comparison only -- never written into a GroupingHint's known_fields
    # (account_type is always derived, never taken from a hint).
    template_account_type: dict = field(default_factory=dict)
    grouping_shape: Optional[GroupingShape] = None
