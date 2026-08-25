"""Standalone tests for the TB SFS_IOCL bug-fix pass (no pytest dependency —
run directly: `python backend/tests/test_audit_report_fixes.py`, or inside the
backend container: `docker compose exec backend python /app/tests/test_audit_report_fixes.py`).

Covers:
  - Bug 1: FSLI "of which" sub-lines never break table syntax; residual
    reconciliation (item 11).
  - Bug 3 / item 7: find_mirrored_pairs() false-positive guard — two unrelated,
    non-keyword accounts with coincidental magnitude/sign are never flagged.
  - Bug 3: find_mirrored_pairs() correctly matches the two real IOCL patterns
    (explicit code cross-reference; near-identical name differing by a
    trailing number) and classifies resolved vs. unresolved correctly.
  - Requirement 2: multi-year-in-one-file period detection (3 periods, base
    year selection, no IndexError past 2 periods).
"""
import pathlib
import re
import sys

# PATCHED for this integration: the vendored `yukta_rag` package lives under
# this mode's pipeline/ dir, not at a repo-root "backend". Resolved from
# __file__ so the file runs directly from any cwd and under pytest alike.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pipeline"))

from yukta_rag.audit.audit_pipeline import _has_malformed_table_rows, _structurally_compliant
from yukta_rag.audit.audit_screen import find_mirrored_pairs
from yukta_rag.trial_balance.trial_balance import _period_labels


def _account(name, code, net):
    return {"name": name, "code": code, "net": net, "fsli": None, "category": None,
            "sensitive_tags": [], "mapping_confidence": "medium", "mapping_source": "keyword_inference"}


def test_fsli_table_of_which_stays_valid_table():
    """Bug 1 — build a >15-row FSLI table (via the real report-building code
    path, using a minimal synthetic mapping) and assert no line inside the
    table block fails to start with '|'."""
    from yukta_rag.audit.audit_pipeline import build_audit_report

    fsli_groups = {}
    accounts = []
    for i in range(18):
        name_suffix = " Non-Current" if i % 3 == 0 else (" Current" if i % 3 == 1 else "")
        name = f"Account {i}{name_suffix}"
        a = _account(name, str(1000 + i), float(1000 * (i + 1)))
        a["fsli"] = "Other Assets"
        accounts.append(a)
        fsli_groups.setdefault("Other Assets", []).append(name)
    mapping = {"accounts": accounts, "fsli_groups": fsli_groups,
              "sensitive_summary": {}, "grouping_source": "keyword_inference"}
    iq = {"source_system": "unknown", "period_end": "Balance", "currency": "INR",
         "currency_assumed": True, "scale": "unknown", "balanced": True,
         "trial_balance_residual": 0, "comparative_present": False,
         "movement_columns_present": False, "n_unmapped": 0, "n_accounts": 18,
         "data_quality_flags": [], "engagement_context": "unknown",
         "value_coverage_pct": 100.0, "n_contra_pairs_excluded": 0,
         "low_value_coverage": False, "opening_present": False}
    screen = {"abnormal_signs": [], "n_abnormal": 0, "concentration": [], "round_sums": [],
             "sensitive_heads": [], "contra_pairs": [], "n_contra_pairs": 0, "data_quality": {}}
    materiality = {"provisional_overall_materiality": 1000, "chosen_base": "revenue",
                  "note": "", "base_reason": ""}
    findings = {"findings": [], "summary": {}, "evidence_request_list": [], "management_query_list": []}

    report = build_audit_report("Test Co", iq, mapping, screen, {"relationships": []},
                                materiality, {}, findings, filename="test.xlsx", sheet="TB")

    # every line strictly inside a table block must start with '|'
    in_table = False
    for line in report.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
            in_table = True
            continue
        if in_table and stripped == "":
            in_table = False
            continue
        if in_table:
            raise AssertionError(f"non-table line found inside a table block: {line!r}")
    assert "## FSLI Summary" in report
    assert "of which" in report.lower()
    print("PASS: FSLI table stays valid table syntax throughout, incl. 'of which' rows")


def test_malformed_table_row_gate():
    """Bug 2 — a spliced row (more '|' than its table's own header) is caught."""
    clean = "| FSLI | Opening | Closing |\n|---|---:|---:|\n| Borrowings | -681,942 | -825,512 |"
    spliced = ("| FSLI | Opening | Closing |\n|---|---:|---:|\n"
              "| Borrowings | -681,942 | -825| Borrowings | -681,942 | -825,512 |")
    assert not _has_malformed_table_rows(clean), "clean table incorrectly flagged"
    assert _has_malformed_table_rows(spliced), "spliced row NOT detected"
    print("PASS: _has_malformed_table_rows detects a spliced row, and clean tables pass")


def test_structural_compliance_gate():
    old_format = "## 4. Prioritised findings\nsome text"
    new_format = ("Notice to the Reader ... Engagement Context and Assumptions ... "
                 "FSLI Summary ... Focus Areas ...")
    assert not _structurally_compliant(old_format)
    assert _structurally_compliant(new_format)
    print("PASS: structural-compliance gate rejects old format, accepts new format")


def test_mirror_pairs_false_positive_guard():
    """Item 7 — two unrelated, non-keyword accounts with coincidental
    magnitude/sign must NOT be flagged."""
    mapping = {"accounts": [
        _account("Salaries Payable - Region X", "9001", 5_432_100.00),
        _account("Prepaid Insurance - Region Y", "9002", -5_432_100.00),
    ]}
    result = find_mirrored_pairs(mapping)
    assert result["pairs"] == [], f"false positive: unrelated accounts flagged: {result}"
    print("PASS: no false positive on unrelated, non-keyword, coincidental-magnitude accounts")


def test_mirror_pairs_code_cross_reference():
    """Real IOCL pattern 1 — exact mirror via explicit code cross-reference."""
    mapping = {"accounts": [
        _account("TRANSFER OF PRODUCT WITHIN MD-4600005022(C)", "4600005021", -10_009_694_143_844.10),
        _account("TRANSFER OF PRODUCT WITHIN MD-4600005021(C)", "4600005022", 10_009_694_143_844.10),
    ]}
    result = find_mirrored_pairs(mapping)
    assert len(result["pairs"]) == 1, f"expected 1 pair, got {result}"
    assert result["pairs"][0]["match_basis"] == "code_reference"
    assert result["pairs"][0]["resolved"] is True
    assert result["unresolved"] == []
    print("PASS: exact code-cross-reference mirror pair matched and correctly resolved")


def test_mirror_pairs_name_match_unresolved():
    """Real IOCL pattern 3 — 'Divisional Consolidated - Contra Mktg'/'Mktg2',
    ~9% apart, must be matched (by name) but classified unresolved."""
    mapping = {"accounts": [
        _account("Divisional Consolidated - Contra Mktg2", "2491000080", 4_965_352_225_953.12),
        _account("Divisional Consolidated - Contra Mktg", "2491000010", -4_540_512_642_802.79),
    ]}
    result = find_mirrored_pairs(mapping)
    assert len(result["pairs"]) == 1, f"expected 1 pair, got {result}"
    assert result["pairs"][0]["match_basis"] == "name_match"
    assert result["pairs"][0]["resolved"] is False
    assert len(result["unresolved"]) == 1
    print("PASS: near-identical-name pair matched and correctly classified unresolved")


def test_period_labels_beyond_two_no_indexerror():
    """Requirement 2 — _period_labels() must not IndexError past 2 unlabeled periods."""
    header = ["Account", "", "", ""]
    above = ["", "", "", ""]
    labels = _period_labels(header, above, [1, 2, 3])
    assert len(labels) == 3
    assert labels == ["Current", "Prior", "Period 3"], labels
    print("PASS: _period_labels() handles 3+ unlabeled periods without IndexError:", labels)


def test_period_labels_year_detection_three_periods():
    header = ["FY2022-23 Closing", "FY2023-24 Closing", "FY2024-25 Closing"]
    above = ["", "", ""]
    labels = _period_labels(header, above, [0, 1, 2])
    assert len(labels) == 3
    assert "2022" in labels[0] and "2023" in labels[1] and "2024" in labels[2], labels
    print("PASS: 3 year-labeled periods parsed:", labels)


def test_base_period_selection():
    from yukta_rag.audit.audit_pipeline import _select_base_period_index, _slice_period
    periods = ["FY 2022-23", "FY 2024-25", "FY 2023-24"]
    idx = _select_base_period_index(periods)
    assert idx == 1, f"expected index 1 (FY2024-25, the max year), got {idx}"

    tb = {"filename": "x.xlsx", "sheet": "TB", "periods": periods,
         "accounts": [
             {"name": "Cash", "code": "1", "p1_debit": 10.0, "p1_credit": 0.0, "p1_net": 10.0,
              "p2_debit": 30.0, "p2_credit": 0.0, "p2_net": 30.0,
              "p3_debit": 20.0, "p3_credit": 0.0, "p3_net": 20.0},
         ]}
    sliced = _slice_period(tb, idx)
    assert sliced["periods"] == ["FY 2024-25"]
    assert sliced["accounts"][0]["p1_net"] == 30.0, "slice did not pick the base period's own values"
    print("PASS: base-year auto-selection picks the max parsed year and slices correctly")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
