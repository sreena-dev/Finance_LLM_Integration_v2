"""Tests for cag_directions_engine.py — Gap-closure Phase 5, Gap #2.

Uses a fake `tables` object (not a real REFERENCE_DSN connection) so this
suite runs without a live database — see conftest.py's note on keeping
this layer DB/LLM-free and testable in isolation.
"""

from sar_prod_v3.cag_directions_engine import (
    _DIRECTION_THEMES_BY_DOC,
    check_direction_addressed,
    resolve_applicable_directions,
    run_cag_directions_checks,
)


class _FakeTables:
    """Stands in for ReferenceTools — returns canned rows or none, without
    touching a real database."""

    def __init__(self, rows):
        self._rows = rows

    def fetch_cag_directions_for_date(self, report_date_str):
        return self._rows


# doc_name must match a real key in _DIRECTION_THEMES_BY_DOC — run_cag_
# directions_checks looks themes up per-document now (Gap-closure: a
# document that date-matches but isn't this one must not be tested against
# this one's themes), so a made-up name like the old "x" placeholder would
# silently match nothing and every "run_..." test below would go quiet.
_REAL_DOC_NAME = "CAG's Revised Directions for Statutory Auditors"
_DIRECTION_THEMES = _DIRECTION_THEMES_BY_DOC[_REAL_DOC_NAME]
ONE_ROW = [{"chunk_id": 1, "doc_name": _REAL_DOC_NAME, "page_no": 1, "txt": "...", "effective_from": "2025-05-23", "effective_to": None}]


def test_resolve_applicable_directions_empty_without_report_date():
    assert resolve_applicable_directions(None, tables=_FakeTables(ONE_ROW)) == []
    assert resolve_applicable_directions("", tables=_FakeTables(ONE_ROW)) == []


def test_resolve_applicable_directions_delegates_to_tables():
    assert resolve_applicable_directions("2025-06-01", tables=_FakeTables(ONE_ROW)) == ONE_ROW
    assert resolve_applicable_directions("2025-06-01", tables=_FakeTables([])) == []


# ---------------------------------------------------------------------------
# check_direction_addressed
# ---------------------------------------------------------------------------

def test_no_cag_directions_text_at_all_is_a_high_finding():
    theme = _DIRECTION_THEMES[0]  # "I" — fair valuation of investments
    obs = check_direction_addressed(theme, "")
    assert obs.tag == "FINDING" and obs.risk_rating == "High"


def test_theme_evidenced_in_text_does_not_fire():
    theme = next(t for t in _DIRECTION_THEMES if t["roman"] == "III")  # grants/subsidy
    text = "The Company received grants from the State Government which were utilised per the terms of the scheme."
    assert check_direction_addressed(theme, text) is None


def test_theme_not_evidenced_raises_medium_risk_flag():
    theme = next(t for t in _DIRECTION_THEMES if t["roman"] == "IV")  # risk management / data assets
    text = "The Company complied with SEBI listing regulations during the year."  # theme V's text, not IV's
    obs = check_direction_addressed(theme, text)
    assert obs.tag == "RISK_FLAG" and obs.risk_rating == "Medium"


def test_all_five_real_themes_have_distinct_keywords_and_check_ids():
    """Sanity check on the curated table itself — every theme must produce
    a distinct check_id, and none should accidentally share a keyword set
    with another (which would make them indistinguishable in practice)."""
    check_ids = [f"DIR-{t['roman']}-01" for t in _DIRECTION_THEMES]
    assert len(check_ids) == len(set(check_ids)) == 5
    keyword_sets = [frozenset(t["keywords"]) for t in _DIRECTION_THEMES]
    assert len(keyword_sets) == len(set(keyword_sets))


# ---------------------------------------------------------------------------
# run_cag_directions_checks
# ---------------------------------------------------------------------------

def test_run_returns_empty_when_nothing_covers_the_report_date():
    merged = {"cag_directions": {"text": ""}}
    obs = run_cag_directions_checks(merged, "2020-01-01", tables=_FakeTables([]))
    assert obs == []


def test_run_returns_empty_without_a_report_date():
    merged = {"cag_directions": {"text": ""}}
    obs = run_cag_directions_checks(merged, None, tables=_FakeTables(ONE_ROW))
    assert obs == []


def test_run_raises_one_observation_per_unaddressed_theme():
    merged = {"cag_directions": {"text": "The Company received grants which were utilised per scheme terms."}}
    obs = run_cag_directions_checks(merged, "2025-06-01", tables=_FakeTables(ONE_ROW))
    check_ids = {o.check_id for o in obs}
    # III (grants) is evidenced -> not in the list; the other 4 themes are not.
    assert "DIR-III-01" not in check_ids
    assert check_ids == {"DIR-I-01", "DIR-II-01", "DIR-IV-01", "DIR-V-01"}


def test_run_raises_high_findings_for_all_themes_when_text_is_empty():
    merged = {"cag_directions": {"text": ""}}
    obs = run_cag_directions_checks(merged, "2025-06-01", tables=_FakeTables(ONE_ROW))
    assert len(obs) == 5
    assert all(o.tag == "FINDING" and o.risk_rating == "High" for o in obs)


def test_run_skips_a_date_matched_document_with_no_curated_theme_set():
    """A document that date-matches (resolve_applicable_directions found it)
    but has no entry in _DIRECTION_THEMES_BY_DOC — e.g. an older standing-
    directions document ingested for its date coverage before its own
    themes were curated — must be skipped, not silently tested against a
    different, unrelated document's themes. This is the exact bug found in
    production: a report whose applicable directions were an un-curated
    document was still being checked against this module's one hardcoded
    5-point theme set."""
    uncurated_row = [{
        "chunk_id": 99, "doc_name": "Some Other Standing Directions Document",
        "page_no": 1, "txt": "...", "effective_from": "2015-04-01", "effective_to": "2025-05-22",
    }]
    merged = {"cag_directions": {"text": ""}}
    obs = run_cag_directions_checks(merged, "2020-01-01", tables=_FakeTables(uncurated_row))
    assert obs == []
