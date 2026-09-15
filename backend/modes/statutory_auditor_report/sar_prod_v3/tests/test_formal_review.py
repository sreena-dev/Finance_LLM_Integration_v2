"""Tests for formal_review.py — Gap-closure Phase 1, Gap #5.

Covers: UDIN wiring (single + joint-auditor), EoM-closing-sentence gating,
report-date-sequence gating, and the review_status derivation (§47).
"""

from sar_prod_v3.formal_review import compute_review_status, run_formal_checks

VALID_UDIN = "24123456ABCDEFGHIJ"

FS_TABLES_OK = {"balance_sheet": {"table_md": "x"}, "profit_loss": {"table_md": "x"}}


def _merged(**overrides):
    base = {
        "formal_checks": {
            "auditors": [{"udin": VALID_UDIN}],
            "report_date": "2024-05-20",
            "fs_approval_date": "2024-05-10",
        },
        "emphasis_of_matter": {"present": False},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# run_formal_checks
# ---------------------------------------------------------------------------

def test_all_checks_pass_raises_no_observations():
    res = run_formal_checks(_merged())
    assert res["observations"] == []
    assert res["summary"]["udin"]["all_valid"] is True
    assert res["summary"]["eom_closing_sentence"] is None  # not applicable, EoM absent
    assert res["summary"]["report_date_sequence"]["passed"] is True


def test_invalid_udin_raises_one_audit_pointer_observation():
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": "NOT-VALID"}],
        "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
    }))
    ids = [o.check_id for o in res["observations"]]
    assert "CHK-UDIN-01" in ids
    assert res["summary"]["udin"]["all_valid"] is False


def test_joint_auditors_each_get_a_distinct_check_id():
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": VALID_UDIN}, {"udin": "BAD"}],
        "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
    }))
    ids = [o.check_id for o in res["observations"]]
    assert ids == ["CHK-UDIN-01-2"]  # only the second (invalid) one raises
    assert res["summary"]["udin"]["all_valid"] is False
    assert len(res["summary"]["udin"]["checks"]) == 2


def test_eom_present_with_closing_sentence_passes_and_raises_nothing():
    res = run_formal_checks(_merged(emphasis_of_matter={
        "present": True,
        "items": [{"quote": "Note 12 discloses..."}],
        "closing_sentence_present": True,
        "closing_sentence_quote": (
            "Our opinion on the Financial Statements is not modified in respect of the above matters."
        ),
    }))
    assert res["summary"]["eom_closing_sentence"]["passed"] is True
    assert not any(o.check_id == "CHK-EOM-01" for o in res["observations"])


def test_eom_present_without_closing_sentence_raises_high_finding():
    res = run_formal_checks(_merged(emphasis_of_matter={
        "present": True,
        "items": [{"quote": "We draw attention to Note 5."}],
        "closing_sentence_present": False,
    }))
    eom_obs = [o for o in res["observations"] if o.check_id == "CHK-EOM-01"]
    assert len(eom_obs) == 1
    assert eom_obs[0].tag == "FINDING"
    assert eom_obs[0].risk_rating == "High"


def test_report_date_before_approval_raises_high_finding():
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": VALID_UDIN}],
        "report_date": "2024-05-01", "fs_approval_date": "2024-05-10",
    }))
    date_obs = [o for o in res["observations"] if o.check_id == "CHK-DATE-01"]
    assert len(date_obs) == 1
    assert date_obs[0].tag == "FINDING"
    assert date_obs[0].risk_rating == "High"


def test_report_date_check_skipped_when_either_date_missing():
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": VALID_UDIN}], "report_date": "", "fs_approval_date": "2024-05-10",
    }))
    assert res["summary"]["report_date_sequence"] is None
    assert not any(o.check_id == "CHK-DATE-01" for o in res["observations"])


# ---------------------------------------------------------------------------
# compute_review_status
# ---------------------------------------------------------------------------

def test_status_complete_when_everything_present_and_valid():
    res = run_formal_checks(_merged())
    status, reasons = compute_review_status(
        main_text_usable=True, fs_tables=FS_TABLES_OK, formal_summary=res["summary"],
    )
    assert status == "complete"
    assert reasons == []


def test_status_provisional_when_udin_invalid():
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": "BAD"}], "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
    }))
    status, reasons = compute_review_status(
        main_text_usable=True, fs_tables=FS_TABLES_OK, formal_summary=res["summary"],
    )
    assert status == "provisional"
    assert reasons


def test_status_blocked_when_main_report_unusable():
    res = run_formal_checks(_merged())
    status, reasons = compute_review_status(
        main_text_usable=False, fs_tables=FS_TABLES_OK, formal_summary=res["summary"],
    )
    assert status == "blocked"
    assert reasons


def test_status_blocked_when_mandatory_fs_missing():
    res = run_formal_checks(_merged())
    status, reasons = compute_review_status(
        main_text_usable=True, fs_tables={"balance_sheet": {"table_md": "x"}},  # profit_loss missing
        formal_summary=res["summary"],
    )
    assert status == "blocked"
    assert any("profit_loss" in r for r in reasons)


def test_blocked_takes_priority_over_provisional():
    """Both a blocking condition and a provisional-only condition present at
    once must report as blocked — a caller should never see 'provisional'
    when a mandatory input is actually missing."""
    res = run_formal_checks(_merged(formal_checks={
        "auditors": [{"udin": "BAD"}], "report_date": "2024-05-20", "fs_approval_date": "2024-05-10",
    }))
    status, reasons = compute_review_status(
        main_text_usable=False, fs_tables=FS_TABLES_OK, formal_summary=res["summary"],
    )
    assert status == "blocked"
