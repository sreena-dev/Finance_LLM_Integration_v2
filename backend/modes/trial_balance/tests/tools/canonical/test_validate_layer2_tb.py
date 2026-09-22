"""Tests for validate_layer2_tb -- the six rules validate_layer1_tb can only mark
SKIPPED (TB-012/013/016/017/018/020) because they need the FSLI classification and
grouping join that only exist once build_canonical_tb has run. This patches those
SKIPPED stubs in layer1_results.json with real PASS/WARNING/HALTED results."""

import json

import pytest

from modes.trial_balance.pipeline.tools import validate_layer2_tb

pl = pytest.importorskip("polars")


def _write_layer1_stub(tmp_path, rules=("TB-012", "TB-013", "TB-016", "TB-017", "TB-018", "TB-020")):
    results = [
        {"rule": r, "rule_name": r, "status": "SKIPPED", "severity": "Warning",
         "message": "stub", "evidence": None, "affected_rows": []}
        for r in rules
    ]
    path = tmp_path / "layer1_results.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f)
    return path


def _write_canonical_tb(tmp_path, rows):
    base = {"tb_doc_id": "doc1", "bs_pl": None, "sub_head_2": None, "sub_head_1": None,
            "account_type": None, "mapped_status": "MAPPED", "opening_balance": 0.0,
            "debit": 0.0, "credit": 0.0}
    full_rows = [{**base, **r} for r in rows]
    df = pl.DataFrame(full_rows)
    path = tmp_path / "canonical_tb.parquet"
    df.write_parquet(path)
    return path


def _results_by_rule(path):
    with open(path) as f:
        return {r["rule"]: r for r in json.load(f)}


class TestBalancedIdentityAndCleanMapping:
    def _rows(self):
        return [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 1000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            {"gl_code": "3001", "gl_name": "Share Capital", "main_head": "Equity", "closing_balance": -500.0},
            {"gl_code": "4001", "gl_name": "Sales Revenue", "main_head": "Revenue", "closing_balance": -300.0},
            {"gl_code": "5001", "gl_name": "Office Expenses", "main_head": "Expenses", "closing_balance": 200.0},
        ]

    def test_all_six_rules_patched_to_real_statuses(self, tmp_path):
        canon_path = _write_canonical_tb(tmp_path, self._rows())
        l1_path = _write_layer1_stub(tmp_path)

        result = validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"

        results = _results_by_rule(l1_path)
        for rule in ("TB-012", "TB-013", "TB-016", "TB-017", "TB-018", "TB-020"):
            assert results[rule]["status"] != "SKIPPED", f"{rule} was not patched"

        # Signed sum nets to zero: 1000 + (-400) + (-500) + (-300) + 200 == 0 -> PASS
        assert results["TB-012"]["status"] == "PASS"
        assert results["TB-013"]["status"] == "PASS"
        assert results["TB-016"]["status"] == "PASS"
        assert results["TB-017"]["status"] == "PASS"
        assert results["TB-018"]["status"] == "PASS"
        assert results["TB-020"]["status"] == "PASS"


class TestTB012IdentityBreach:
    def test_halts_when_assets_do_not_equal_liabilities_plus_equity_plus_net_pl(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 5000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            {"gl_code": "3001", "gl_name": "Share Capital", "main_head": "Equity", "closing_balance": -500.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-012"]["status"] == "HALTED"


class TestTB012IncludesUnmapped:
    # TB-R21: a TB that genuinely foots once Unmapped is accounted for must not be
    # mechanically HALTED just because some of its balance sits in Unmapped accounts --
    # Unmapped is a coverage gap, not a footing failure, and must be counted IN the
    # identity, not excluded from it.
    def test_passes_when_unmapped_balance_makes_the_full_identity_net_to_zero(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 1000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            # No FS-head text ("main_head" doesn't contain asset/liab/equity/revenue/
            # expense) and mapped_status is UNMAPPED -- classify_row resolves this to
            # report_head "Unmapped". The classified heads alone (1000 - 400 = 600) do
            # NOT net to zero; only including this Unmapped balance (-600) does.
            {"gl_code": "9999", "gl_name": "Unclassified Ledger", "main_head": "??", "closing_balance": -600.0,
             "mapped_status": "UNMAPPED"},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-012"]["status"] == "PASS"
        assert "Unmapped accounts" in results["TB-012"]["message"]

    def test_still_halts_when_gap_is_not_explained_by_unmapped_at_all(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 5000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            {"gl_code": "3001", "gl_name": "Share Capital", "main_head": "Equity", "closing_balance": -500.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-012"]["status"] == "HALTED"
        assert results["TB-012"]["evidence"]["unmapped"] == 0.0


class TestLayer2ReturnReflectsHaltedRule:
    # TB-R19: validate_layer2_tb must not unconditionally return SUCCESS/can_continue=True
    # when it has just patched a rule (typically TB-012) to HALTED -- the caller needs a
    # real signal to stop the chain instead of proceeding on a TB that doesn't foot.
    def test_return_is_halted_when_tb012_breaches(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 5000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            {"gl_code": "3001", "gl_name": "Share Capital", "main_head": "Equity", "closing_balance": -500.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        result = validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        assert result["pipeline_status"] == "HALTED"
        assert result["can_continue"] is False
        assert "TB-012" in result["halted_rules"]

    def test_return_is_success_when_all_six_rules_pass(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 1000.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -400.0},
            {"gl_code": "3001", "gl_name": "Share Capital", "main_head": "Equity", "closing_balance": -500.0},
            {"gl_code": "4001", "gl_name": "Sales Revenue", "main_head": "Revenue", "closing_balance": -300.0},
            {"gl_code": "5001", "gl_name": "Office Expenses", "main_head": "Expenses", "closing_balance": 200.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        result = validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        assert result["pipeline_status"] == "SUCCESS"
        assert result["can_continue"] is True
        assert result["halted_rules"] == []


class TestTB013DuplicateGroup:
    def test_halts_when_same_gl_code_maps_to_two_groups(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash A", "main_head": "Current Assets", "closing_balance": 100.0},
            {"gl_code": "1001", "gl_name": "Cash A", "main_head": "Non-Current Assets", "closing_balance": 100.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-013"]["status"] == "HALTED"
        assert "1001" in results["TB-013"]["message"]


class TestTB017MissingHead:
    def test_warns_when_a_mandatory_head_has_no_accounts(self, tmp_path):
        rows = [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 100.0},
            {"gl_code": "2001", "gl_name": "Trade Payable", "main_head": "Current Liabilities", "closing_balance": -100.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-017"]["status"] == "WARNING"
        assert "Equity" in results["TB-017"]["message"]
        assert "Revenue" in results["TB-017"]["message"]
        assert "Expenses" in results["TB-017"]["message"]


class TestTB018SuspenseNonZero:
    def test_warns_when_suspense_account_is_non_zero(self, tmp_path):
        rows = [
            {"gl_code": "9001", "gl_name": "Suspense Account", "main_head": "Current Assets", "closing_balance": 500.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-018"]["status"] == "WARNING"
        assert "9001" in json.dumps(results["TB-018"]["evidence"])


class TestTB020TaxSignFlip:
    def test_warns_when_tax_account_sits_opposite_its_normal_side(self, tmp_path):
        # An Asset-head "Advance Tax" account should normally carry a debit (positive)
        # balance; a negative balance here is the sign-convention red flag TB-020 checks for.
        rows = [
            {"gl_code": "1050", "gl_name": "Advance Tax", "main_head": "Current Assets", "closing_balance": -300.0},
        ]
        canon_path = _write_canonical_tb(tmp_path, rows)
        l1_path = _write_layer1_stub(tmp_path)

        validate_layer2_tb(canonical_tb_file=str(canon_path), layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        results = _results_by_rule(l1_path)
        assert results["TB-020"]["status"] == "WARNING"


class TestMissingInputs:
    # @pipeline_tool wraps every tool's exceptions into a FAILED result dict rather than
    # letting them propagate -- consistent with how every other tool in this pipeline
    # degrades on a missing prerequisite file.
    def test_missing_canonical_tb_fails_gracefully(self, tmp_path):
        l1_path = _write_layer1_stub(tmp_path)
        result = validate_layer2_tb(canonical_tb_file=str(tmp_path / "nope.parquet"),
                                     layer1_results_file=str(l1_path), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"

    def test_missing_layer1_results_fails_gracefully(self, tmp_path):
        canon_path = _write_canonical_tb(tmp_path, [
            {"gl_code": "1001", "gl_name": "Cash", "main_head": "Current Assets", "closing_balance": 100.0},
        ])
        result = validate_layer2_tb(canonical_tb_file=str(canon_path),
                                     layer1_results_file=str(tmp_path / "nope.json"), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"
