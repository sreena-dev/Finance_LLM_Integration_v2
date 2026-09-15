"""Tests for pervasiveness.py — Gap-closure Phase 3, Gap #8.

Every test constructs `modification` blocks by hand — this module has no
live-model validation in this sandbox (see pervasiveness.py's module
docstring); these tests only prove the deterministic reasoning over
whatever the extractor happens to hand it, not that the extractor
populates it well.
"""

from sar_prod_v3.pervasiveness import (
    check_modification_amount_reconciles,
    check_opinion_type_vs_pervasiveness,
    count_pervasiveness_cues,
    run_pervasiveness_checks,
)

CUES_HIGH = {
    "affects_multiple_elements": True, "affects_fundamental_balance": True,
    "large_relative_to_key_bases": True, "cannot_determine_effect": False,
    "multiple_modifications_same_direction": False,
}
CUES_NONE = {k: False for k in CUES_HIGH}


def test_count_pervasiveness_cues():
    assert count_pervasiveness_cues({"pervasiveness_cues": CUES_HIGH}) == 3
    assert count_pervasiveness_cues({"pervasiveness_cues": CUES_NONE}) == 0
    assert count_pervasiveness_cues({}) == 0
    assert count_pervasiveness_cues(None) == 0


def test_qualified_opinion_with_high_cue_count_raises_risk_flag():
    merged = {"opinion": {"type": "qualified"}, "modification": {"pervasiveness_cues": CUES_HIGH}}
    obs = check_opinion_type_vs_pervasiveness(merged)
    assert obs is not None and obs.tag == "RISK_FLAG" and obs.risk_rating == "High"


def test_qualified_opinion_with_low_cue_count_does_not_fire():
    merged = {"opinion": {"type": "qualified"}, "modification": {"pervasiveness_cues": {**CUES_NONE, "affects_multiple_elements": True}}}
    assert check_opinion_type_vs_pervasiveness(merged) is None


def test_unmodified_opinion_never_fires_regardless_of_cues():
    merged = {"opinion": {"type": "unmodified"}, "modification": {"pervasiveness_cues": CUES_HIGH}}
    assert check_opinion_type_vs_pervasiveness(merged) is None


def test_adverse_opinion_with_cues_does_not_fire_this_specific_check():
    """This check only cross-examines Qualified (a pervasive Adverse is
    already the 'expected' pairing per SA 705 — nothing to flag)."""
    merged = {"opinion": {"type": "adverse"}, "modification": {"pervasiveness_cues": CUES_HIGH}}
    assert check_opinion_type_vs_pervasiveness(merged) is None


def test_missing_modification_block_degrades_to_none_not_error():
    """Old-schema package, or a model that ignored the new instruction —
    must not crash."""
    assert check_opinion_type_vs_pervasiveness({"opinion": {"type": "qualified"}}) is None
    assert check_opinion_type_vs_pervasiveness({}) is None


def test_amount_reconciles_when_found_in_fs_tables():
    merged = {"modification": {"quantified_amount": "500", "affected_line_items": ["Trade Receivables"]}}
    fs_tables = {"balance_sheet": {"table_md": "Trade Receivables | 500"}}
    assert check_modification_amount_reconciles(merged, fs_tables) is None


def test_amount_does_not_reconcile_raises_audit_pointer():
    merged = {"modification": {"quantified_amount": "1234", "affected_line_items": ["Trade Receivables"]}}
    fs_tables = {"balance_sheet": {"table_md": "Trade Receivables | 999999"}}
    obs = check_modification_amount_reconciles(merged, fs_tables)
    assert obs is not None and obs.tag == "AUDIT_POINTER"


def test_amount_reconciliation_skipped_without_amount_or_line_items():
    assert check_modification_amount_reconciles({"modification": {}}, {}) is None
    assert check_modification_amount_reconciles(
        {"modification": {"quantified_amount": "500"}}, {},
    ) is None  # no affected_line_items


def test_run_pervasiveness_checks_returns_both_when_both_fire():
    merged = {
        "opinion": {"type": "qualified"},
        "modification": {
            "quantified_amount": "1234", "affected_line_items": ["Trade Receivables"],
            "pervasiveness_cues": CUES_HIGH,
        },
    }
    obs = run_pervasiveness_checks(merged, {"balance_sheet": {"table_md": "nothing matching here"}})
    ids = {o.check_id for o in obs}
    assert ids == {"PERV-01", "PERV-02"}


def test_run_pervasiveness_checks_empty_on_clean_package():
    assert run_pervasiveness_checks({}, {}) == []
