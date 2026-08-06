"""Standalone tests for the 4-gap closure pass (reference-doc comparison fix —
no pytest dependency, run directly: `python backend/tests/test_gap_closure_fixes.py`,
or inside the backend container:
`docker compose exec backend python /app/tests/test_gap_closure_fixes.py`).

Covers:
  - Item 1: find_clearing_series() no longer requires the full sign-validated
    COA scheme (_coa_scheme_detected) — only that codes are numeric enough to
    group (_numeric_codes_present). New structural (name-independent)
    candidate signal catches a GL-series that behaves like a wash OR is
    entirely unmapped, even with no clearing/inter-unit keyword in its name
    (the real GAIL gap — "ICT-", "IUT-", "Stat A/C" etc. match no keyword).
  - Item 1 false-positive guards: a small (below the structural minimum) non-
    keyword group is not flagged; a real, mapped FSLI group sharing a prefix
    is not flagged just because it doesn't net to nil.
  - Item 3: analyze_relationships() now also emits a "none"-severity
    (checked, no exception) entry when a relationship is consistent, and
    build_findings() does not turn those into Focus Areas.
  - Item 4: the "Overall data sufficiency" line is rendered in the report
    from the already-computed iq["data_sufficiency"] field.
"""
import pathlib
import sys

# PATCHED for this integration: the vendored `yukta_rag` package lives under
# this mode's pipeline/ dir, not at a repo-root "backend". Resolved from
# __file__ so the file runs directly from any cwd and under pytest alike.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pipeline"))

from yukta_rag.audit.audit_screen import find_clearing_series
from yukta_rag.audit.audit_relationships import analyze_relationships
from yukta_rag.audit.audit_findings import build_findings
from yukta_rag.audit.audit_pipeline import build_audit_report


def _tb_account(code, net):
    return {"code": code, "name": f"placeholder-{code}", "p1_net": net}


def _mapping_account(name, fsli=None, sensitive_tags=None, code=None, net=0.0,
                      mapping_confidence="medium", category=None):
    return {"name": name, "code": code, "net": net, "fsli": fsli, "category": category,
            "sensitive_tags": sensitive_tags or [], "mapping_confidence": mapping_confidence,
            "mapping_source": "keyword_inference"}


def _tb_and_mapping(entries):
    """entries: list of (code, name, net, fsli_or_None) tuples."""
    tb_accounts = [_tb_account(code, net) for code, name, net, fsli in entries]
    # rename tb accounts' "name" to match the mapping accounts (name_by_key join key)
    for (code, name, net, fsli), a in zip(entries, tb_accounts):
        a["name"] = name
    mapping_accounts = [_mapping_account(name, fsli=fsli, code=code, net=net)
                        for code, name, net, fsli in entries]
    tb = {"accounts": tb_accounts}
    mapping = {"accounts": mapping_accounts}
    return tb, mapping


def test_clearing_series_works_without_full_coa_scheme():
    """Item 1 — the real GAIL gap: digits 1/2/3 fail sign validation (so the
    full COA scheme is NOT detected), but a distinct, unmapped, non-keyword,
    near-nil-net GL-series (mirroring GAIL's 80-series) must still be caught."""
    entries = []
    # digits 1/2/3 deliberately fail sign validation: asset-class digit "1" made to
    # net NEGATIVE (wrong side) across enough accounts to break _coa_scheme_detected.
    for i in range(4):
        entries.append((f"10{i:04d}", f"Asset acct {i}", -50_000.0, "Cash and Cash Equivalents"))
    # padding: _numeric_codes_present() requires >= 10 numeric-coded accounts;
    # digit "7" isn't in the sign-validated scheme at all, so this is inert padding.
    for i in range(3):
        entries.append((f"70{i:04d}", f"Padding acct {i}", 10_000.0, "Other Assets (advances/deposits/tax)"))
    # the structural clearing series: prefix "80" + 2-digit suffix, names use GAIL's
    # real (non-keyword) abbreviation style, unmapped (fsli=None), nets to ~nil.
    entries.append(("8000001", "ICT-Oth-Corp Service", 1_000_000.0, None))
    entries.append(("8000002", "ICT-Oth-NG Trad", -998_000.0, None))
    entries.append(("8000003", "ICT-Misc Transfer", -2_000.0, None))
    tb, mapping = _tb_and_mapping(entries)

    result = find_clearing_series(tb, mapping)
    assert result["computable"] is True, (
        "clearing-series detection must not be blocked by unrelated digit classes "
        "failing full COA-scheme sign validation")
    assert len(result["groups"]) == 1, f"expected exactly 1 structural series, got {result}"
    grp = result["groups"][0]
    assert grp["match_basis"] == "structural"
    assert grp["resolved"] is True
    assert "ICT-Oth-Corp Service" in [a["name"] for a in grp["accounts"]]
    print("PASS: structural clearing-series signal fires without the full COA scheme, "
          "even with GAIL-style non-keyword names")


def test_clearing_series_non_washing_structural_series_not_flagged():
    """Item 1, deliberate scope limit — an early version of the structural
    signal also treated "entirely unmapped" as sufficient on its own (no wash
    required), to catch a non-netting series like GAIL's real 90-series. Live
    testing against the actual GAIL file showed this was NOT safe: many
    genuine, legitimately-unmapped FSLI categories (e.g. a 425-account current
    assets bucket, 87% unmapped) would be misflagged as clearing series too.
    So the structural (name-independent, no keyword) path now REQUIRES an
    actual wash (resolved=True) — a coherent non-keyword series that doesn't
    net to nil is intentionally left undetected by this function; catching
    genuinely mirrored-but-unresolved postings against specific other
    accounts (this shape) is better suited to find_mirrored_pairs(), not this
    prefix-grouping mechanism. This test documents that scope limit."""
    entries = [
        ("9000001", "Stat A/C-P/Clms Cust", 20_000_000.0, None),
        ("9000002", "ASSMT-Oth Item", 15_000_000.0, None),
        ("9000003", "ACTY-Misc", 8_000_000.0, None),
    ]
    # padding: _numeric_codes_present() requires >= 10 numeric-coded accounts total.
    for i in range(7):
        entries.append((f"70{i:04d}", f"Padding acct {i}", 10_000.0, "Other Assets (advances/deposits/tax)"))
    tb, mapping = _tb_and_mapping(entries)
    result = find_clearing_series(tb, mapping)
    assert result["computable"] is True
    assert result["groups"] == [], (
        f"a non-washing, unmapped-but-not-keyword series must NOT be flagged "
        f"(this is the exact false-positive shape found live on GAIL): {result}")
    print("PASS: a non-washing structural series is correctly left undetected "
          "(documented scope limit, not a silent miss)")


def test_clearing_series_small_group_not_flagged():
    """Item 1 false-positive guard — a 2-member, non-keyword, unmapped, near-nil
    group stays below the structural minimum size and must NOT be flagged."""
    entries = [
        ("7700001", "Random Wash A", 500_000.0, None),
        ("7700002", "Random Wash B", -498_000.0, None),
    ]
    tb, mapping = _tb_and_mapping(entries)
    result = find_clearing_series(tb, mapping)
    assert result["groups"] == [], f"a 2-member non-keyword group must not be flagged: {result}"
    print("PASS: small (below structural minimum) non-keyword group is not flagged")


def test_clearing_series_real_mapped_fsli_group_not_flagged():
    """Item 1 false-positive guard — a real, mapped FSLI group sharing a GL
    prefix (e.g. genuine Trade Payables sub-accounts) must not be flagged just
    because it happens to share a numeric series and isn't keyword-tagged."""
    entries = [
        ("2000001", "Sundry Creditors - Local", -3_000_000.0, "Trade Payables"),
        ("2000002", "Sundry Creditors - Import", -4_000_000.0, "Trade Payables"),
        ("2000003", "Sundry Creditors - MSME", -1_000_000.0, "Trade Payables"),
    ]
    tb, mapping = _tb_and_mapping(entries)
    result = find_clearing_series(tb, mapping)
    assert result["groups"] == [], (
        f"a real, mapped FSLI group must not be flagged as a clearing series: {result}")
    print("PASS: a real, mapped FSLI group sharing a GL prefix is not misflagged as clearing")


def _account(name, code, net, category=None, fsli=None):
    return {"name": name, "code": code, "net": net, "fsli": fsli, "category": category,
            "sensitive_tags": [], "mapping_confidence": "medium", "mapping_source": "keyword_inference"}


def test_relationship_analytics_symmetric_consistent_entry():
    """Item 3 — when PPE and depreciation are BOTH present (consistent case),
    analyze_relationships() must emit a severity="none" entry, and
    build_findings() must NOT turn it into a Focus Area."""
    mapping = {"accounts": [
        _account("GB-Plant & Machinery", "3010210", 500_000.0, category="asset", fsli="Property, Plant and Equipment"),
        _account("Dep-Plant & Machinery", "6300010", -50_000.0, category="expense", fsli="Depreciation and Amortisation"),
    ]}
    result = analyze_relationships(mapping)
    ppe_entries = [r for r in result["relationships"] if r["relationship"] == "PPE / CWIP vs Depreciation"]
    assert len(ppe_entries) == 1, f"expected exactly one PPE/dep entry, got {ppe_entries}"
    assert ppe_entries[0]["severity"] == "none", f"expected a consistent (none) entry, got {ppe_entries[0]}"

    screen = {"abnormal_signs": [], "concentration": [], "round_sums": []}
    materiality = {"provisional_overall_materiality": 100_000, "chosen_base": "revenue"}
    input_quality = {"balanced": True, "data_sufficiency": "medium", "n_unmapped": 0,
                     "mapping_confidence_summary": {}, "n_accounts": len(mapping["accounts"]),
                     "unmapped_accounts": []}
    findings = build_findings(mapping, screen, result, materiality, input_quality)
    focus_names = [f["account"] for f in findings["findings"]]
    assert "PPE / CWIP vs Depreciation" not in focus_names, (
        "a 'none'-severity consistent relationship must not become a Focus Area")
    print("PASS: consistent PPE/depreciation relationship recorded as severity=none, "
          "and correctly excluded from Focus Areas")


def test_relationship_analytics_flagged_entry_still_becomes_focus_area():
    """Regression — the existing flagged (anomalous) case must still work and
    still become a Focus Area, unchanged."""
    mapping = {"accounts": [
        _account("GB-Plant & Machinery", "3010210", 500_000.0, category="asset", fsli="Property, Plant and Equipment"),
    ]}
    result = analyze_relationships(mapping)
    ppe_entries = [r for r in result["relationships"] if r["relationship"] == "PPE / CWIP vs Depreciation"]
    assert len(ppe_entries) == 1
    assert ppe_entries[0]["severity"] == "high"

    screen = {"abnormal_signs": [], "concentration": [], "round_sums": []}
    materiality = {"provisional_overall_materiality": 100_000, "chosen_base": "revenue"}
    input_quality = {"balanced": True, "data_sufficiency": "medium", "n_unmapped": 0,
                     "mapping_confidence_summary": {}, "n_accounts": len(mapping["accounts"]),
                     "unmapped_accounts": []}
    findings = build_findings(mapping, screen, result, materiality, input_quality)
    focus_names = [f["account"] for f in findings["findings"]]
    assert "PPE / CWIP vs Depreciation" in focus_names
    print("PASS: flagged PPE/depreciation gap is still raised as a Focus Area (no regression)")


def test_data_sufficiency_line_not_rendered():
    """The 'Overall data sufficiency' line was removed from the rendered report
    at the user's request — the underlying iq['data_sufficiency'] value is still
    computed and used elsewhere, but the phrase must no longer appear in the
    report markdown."""
    mapping = {"accounts": [], "fsli_groups": {}, "sensitive_summary": {}, "grouping_source": "keyword_inference"}
    iq = {"source_system": "unknown", "period_end": "Balance", "currency": "INR",
         "currency_assumed": True, "scale": "unknown", "balanced": True,
         "trial_balance_residual": 0, "comparative_present": False,
         "movement_columns_present": False, "n_unmapped": 0, "n_accounts": 0,
         "data_quality_flags": [], "engagement_context": "unknown",
         "value_coverage_pct": 100.0, "n_contra_pairs_excluded": 0,
         "low_value_coverage": False, "opening_present": False,
         "data_sufficiency": "high"}
    screen = {"abnormal_signs": [], "n_abnormal": 0, "concentration": [], "round_sums": [],
             "sensitive_heads": [], "contra_pairs": [], "n_contra_pairs": 0, "data_quality": {}}
    materiality = {"provisional_overall_materiality": 1000, "chosen_base": "revenue",
                  "note": "", "base_reason": ""}
    findings = {"findings": [], "summary": {}, "evidence_request_list": [], "management_query_list": []}

    report = build_audit_report("Test Co", iq, mapping, screen, {"relationships": []},
                                materiality, {}, findings, filename="test.xlsx", sheet="TB")
    assert "Overall data sufficiency" not in report, "data-sufficiency line should be removed from report"
    print("PASS: 'Overall data sufficiency' line is no longer rendered in the report")


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
