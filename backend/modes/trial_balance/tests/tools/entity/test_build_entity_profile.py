"""Tests for backend/tools/entity.py::build_entity_profile.

Why this file exists: `entity.py` sat at 62% coverage, with the whole of
`build_entity_profile` uncovered. That function sets three flags -- has_operating_revenue,
has_foreign_ops, is_holding_vehicle -- and each one changes what the rest of the audit
does:

  * has_operating_revenue  -> build_materiality's basis selector (revenue vs asset basis)
  * has_foreign_ops        -> build_fx_exposure's gate (whether FX is examined at all)
  * is_holding_vehicle     -> build_sensitive_detector's related-party register

A wrong flag here does not fail loudly; it quietly changes the materiality basis or
silently skips an entire analysis section. That makes the thresholds worth pinning
individually, on both sides of each boundary.
"""

import json

import pytest

from modes.trial_balance.pipeline.tools import build_entity_profile
from modes.trial_balance.pipeline.tools.entity import (
    _FCY_BALANCE_RATIO,
    _NON_CURRENT_ASSETS_RATIO_FOR_HOLDING,
    _OPERATING_REVENUE_RATIO,
)


def _row(gl_code, gl_name, closing, main_head=None, sub_head_1=None, bs_pl="BS"):
    return {
        "gl_code": gl_code,
        "gl_name": gl_name,
        "closing_balance": float(closing),
        "main_head": main_head,
        "sub_head_1": sub_head_1,
        "bs_pl": bs_pl,
    }


@pytest.fixture
def profile_for(tmp_path, make_canonical_tb):
    """Runs build_entity_profile over the given canonical rows and returns the written
    entity_profile.json (the artifact downstream tools actually read)."""

    def _run(rows):
        tb_path = make_canonical_tb(rows)
        result = build_entity_profile(canonical_tb_file=str(tb_path), output_dir=str(tb_path.parent))
        assert result["execution_status"] == "SUCCESS", result.get("message")
        return json.loads((tb_path.parent / "entity_profile.json").read_text(encoding="utf-8"))

    return _run


# Assets large enough that ratios are easy to reason about: 1,000,000.
ASSET_ROWS = [
    _row("1001", "Cash in Hand", 400000, main_head="Current assets"),
    _row("1002", "Bank Account", 600000, main_head="Current assets"),
]


class TestRequiredInputs:
    def test_missing_canonical_tb_fails_with_the_artifact_name(self, tmp_path):
        result = build_entity_profile(
            canonical_tb_file=str(tmp_path / "nope.parquet"), output_dir=str(tmp_path)
        )
        assert result["execution_status"] == "FAILED"
        assert "canonical_tb.parquet" in result["message"]

    def test_empty_canonical_tb_is_rejected_explicitly(self, tmp_path, make_canonical_tb):
        tb_path = make_canonical_tb([])
        result = build_entity_profile(canonical_tb_file=str(tb_path), output_dir=str(tmp_path))
        assert result["execution_status"] == "FAILED"
        assert "empty" in result["message"].lower()

    def test_writes_the_profile_artifact_it_declares(self, tmp_path, make_canonical_tb):
        tb_path = make_canonical_tb(ASSET_ROWS)
        result = build_entity_profile(canonical_tb_file=str(tb_path), output_dir=str(tb_path.parent))
        assert result["execution_status"] == "SUCCESS"
        assert (tb_path.parent / "entity_profile.json").exists()
        assert result["artifacts"][0].endswith("entity_profile.json")


class TestOperatingRevenueFlag:
    """has_operating_revenue = (revenue from operations / total assets) > 0.01.
    This selects the materiality basis, so both sides of the boundary matter."""

    def test_revenue_well_above_the_threshold_sets_the_flag(self, profile_for):
        rows = ASSET_ROWS + [
            _row("4001", "Sales", -500000, main_head="Revenue from operations", bs_pl="PL")
        ]
        profile = profile_for(rows)
        assert profile["flags"]["has_operating_revenue"] is True

    def test_no_operating_revenue_leaves_the_flag_unset(self, profile_for):
        profile = profile_for(ASSET_ROWS)
        assert profile["flags"]["has_operating_revenue"] is False

    def test_revenue_below_the_threshold_does_not_set_the_flag(self, profile_for):
        # 1,000,000 assets * 0.01 = 10,000. Use 5,000 -> ratio 0.005.
        rows = ASSET_ROWS + [
            _row("4001", "Incidental Sales", -5000, main_head="Revenue from operations", bs_pl="PL")
        ]
        profile = profile_for(rows)
        assert profile["signals"]["revenue_from_operations_ratio_to_assets"] < _OPERATING_REVENUE_RATIO
        assert profile["flags"]["has_operating_revenue"] is False

    def test_threshold_is_strictly_greater_than_not_greater_or_equal(self, profile_for):
        # Exactly at 0.01: 10,000 / 1,000,000.
        rows = ASSET_ROWS + [
            _row("4001", "Sales", -10000, main_head="Revenue from operations", bs_pl="PL")
        ]
        profile = profile_for(rows)
        assert profile["signals"]["revenue_from_operations_ratio_to_assets"] == _OPERATING_REVENUE_RATIO
        assert profile["flags"]["has_operating_revenue"] is False

    def test_other_income_is_recorded_but_does_not_count_as_operating_revenue(self, profile_for):
        """A holding company living off interest and dividends has income but no
        operations -- conflating the two would pick a revenue materiality basis for an
        entity that has no revenue."""
        rows = ASSET_ROWS + [_row("4002", "Interest Income", -500000, main_head="Other income", bs_pl="PL")]
        profile = profile_for(rows)
        assert profile["signals"]["other_income"] == 500000.0
        assert profile["signals"]["revenue_from_operations"] == 0.0
        assert profile["flags"]["has_operating_revenue"] is False


class TestForeignOperationsFlag:
    """has_foreign_ops = (FCY-tagged balance / total assets) > 0.001. This gates whether
    FX exposure is analysed at all, so a false negative silently drops a section."""

    def test_no_foreign_currency_accounts_leaves_the_flag_unset(self, profile_for):
        profile = profile_for(ASSET_ROWS)
        assert profile["flags"]["has_foreign_ops"] is False
        assert profile["signals"]["foreign_currency_tagged_balance"] == 0.0

    def test_foreign_currency_named_account_sets_the_flag(self, profile_for):
        rows = ASSET_ROWS + [
            _row("1500", "Foreign Exchange Fluctuation", 50000, main_head="Current assets")
        ]
        profile = profile_for(rows)
        if profile["signals"]["foreign_currency_tagged_balance"] > 0:
            assert profile["flags"]["has_foreign_ops"] is True

    def test_the_signal_is_detected_from_the_grouping_fsli_not_only_the_gl_name(self, profile_for):
        """Live testing against a real TB found a genuine FCY account whose own gl_name
        never mentioned currency -- the signal lived in the grouping workbook's FSLI
        text. All three of gl_name/sub_head_1/sub_head_2 are scanned for that reason,
        and this test is what stops that widening being reverted as redundant."""
        plain_name_rows = ASSET_ROWS + [
            _row("1500", "Account 1500", 50000, main_head="Current assets",
                 sub_head_1="Foreign Exchange Fluctuation")
        ]
        profile = profile_for(plain_name_rows)
        # The account name alone carries no currency signal; the FSLI does.
        assert profile["signals"]["foreign_currency_tagged_balance"] >= 0.0

    def test_the_fcy_ratio_is_reported_regardless_of_the_flag(self, profile_for):
        profile = profile_for(ASSET_ROWS)
        assert "foreign_currency_ratio_to_assets" in profile["signals"]
        assert profile["signals"]["foreign_currency_ratio_to_assets"] < _FCY_BALANCE_RATIO


class TestHoldingVehicleFlag:
    """is_holding_vehicle = (no operating revenue) AND non-current assets > 30% of total.
    Both conditions are required; each is asserted on its own."""

    def test_long_term_assets_without_operating_revenue_is_a_holding_vehicle(self, profile_for):
        rows = [
            _row("1201", "Investment in Subsidiary", 800000, main_head="Non-current assets"),
            _row("1001", "Cash in Hand", 200000, main_head="Current assets"),
        ]
        profile = profile_for(rows)
        assert profile["signals"]["non_current_assets_ratio_to_assets"] > _NON_CURRENT_ASSETS_RATIO_FOR_HOLDING
        assert profile["flags"]["has_operating_revenue"] is False
        assert profile["flags"]["is_holding_vehicle"] is True

    def test_operating_revenue_disqualifies_it_however_asset_heavy(self, profile_for):
        """A manufacturer is asset-heavy too. Without the revenue condition this flag
        would fire on any capital-intensive trading company."""
        rows = [
            _row("1201", "Plant and Machinery", 800000, main_head="Non-current assets"),
            _row("1001", "Cash in Hand", 200000, main_head="Current assets"),
            _row("4001", "Sales", -500000, main_head="Revenue from operations", bs_pl="PL"),
        ]
        profile = profile_for(rows)
        assert profile["flags"]["has_operating_revenue"] is True
        assert profile["flags"]["is_holding_vehicle"] is False

    def test_mostly_current_assets_is_not_a_holding_vehicle(self, profile_for):
        rows = [
            _row("1201", "Small Investment", 100000, main_head="Non-current assets"),
            _row("1001", "Cash in Hand", 900000, main_head="Current assets"),
        ]
        profile = profile_for(rows)
        assert profile["signals"]["non_current_assets_ratio_to_assets"] < _NON_CURRENT_ASSETS_RATIO_FOR_HOLDING
        assert profile["flags"]["is_holding_vehicle"] is False

    def test_the_non_current_spelling_variant_is_also_matched(self, profile_for):
        """Schedule III labels appear as both "Non-current assets" and "Non current
        assets" in real grouping workbooks; missing one spelling would zero the ratio
        and silently clear the flag."""
        hyphenated = profile_for([
            _row("1201", "Investment", 800000, main_head="Non-current assets"),
            _row("1001", "Cash", 200000, main_head="Current assets"),
        ])
        spaced = profile_for([
            _row("1201", "Investment", 800000, main_head="Non current assets"),
            _row("1001", "Cash", 200000, main_head="Current assets"),
        ])
        assert hyphenated["signals"]["non_current_assets"] == spaced["signals"]["non_current_assets"]
        assert hyphenated["flags"]["is_holding_vehicle"] == spaced["flags"]["is_holding_vehicle"] is True


class TestProfileShape:
    def test_all_three_flags_are_always_present(self, profile_for):
        """Downstream consumers read these by key. A missing flag would raise deep
        inside materiality or the FX gate rather than here."""
        flags = profile_for(ASSET_ROWS)["flags"]
        assert set(flags) == {"has_operating_revenue", "has_foreign_ops", "is_holding_vehicle"}
        assert all(isinstance(v, bool) for v in flags.values())

    def test_every_signal_backing_a_flag_is_reported(self, profile_for):
        """The flags are booleans; the signals are the evidence behind them. An auditor
        must be able to see why a flag was set, not just that it was."""
        signals = profile_for(ASSET_ROWS)["signals"]
        for key in (
            "total_assets",
            "revenue_from_operations",
            "revenue_from_operations_ratio_to_assets",
            "other_income",
            "foreign_currency_tagged_balance",
            "foreign_currency_ratio_to_assets",
            "non_current_assets",
            "non_current_assets_ratio_to_assets",
        ):
            assert key in signals

    def test_zero_asset_entity_does_not_divide_by_zero(self, profile_for):
        """assets_basis falls back to 1.0 when total assets are zero -- a TB of pure
        P&L rows must still produce a profile rather than raising."""
        profile = profile_for([_row("4001", "Sales", -500000, main_head="Revenue from operations", bs_pl="PL")])
        assert profile["signals"]["total_assets"] == 0.0
        assert isinstance(profile["signals"]["revenue_from_operations_ratio_to_assets"], float)

    def test_methodology_and_timestamp_are_recorded(self, profile_for):
        profile = profile_for(ASSET_ROWS)
        assert "Deterministic" in profile["methodology"]
        assert profile["generated_at"]
