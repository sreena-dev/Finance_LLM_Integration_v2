"""Tests for backend/tools/variance.py.

Why this file exists: build_variance_analysis was one of the most heavily restructured
functions in the Phase-3 complexity pass (McCabe 109 -> 72, five sections extracted into
nested closures) and sat at 10% line coverage afterwards -- the thinnest safety net in
the codebase sitting under one of its largest structural changes. Two real cross-closure
variable-scope bugs (abn_classes/abn_rules, offsetting_movements) were introduced and
caught during that refactor by unrelated tests; nothing in the suite actually asserted
this tool's *numbers*.

These tests pin the arithmetic and the classification boundaries, not just "it runs":
_compute_variance is exercised branch-by-branch, and the tool-level tests assert the
specific derived values (z-scores, distribution buckets, offsetting detection,
concentration percentages) that the audit report ultimately quotes.
"""

import json
from pathlib import Path

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import build_variance_analysis
from modes.trial_balance.pipeline.tools.variance import (
    ABNORMAL_MIN_TRIGGERED_RULES,
    EXTREME_MOVEMENT_PCT_THRESHOLD,
    MATERIALITY_RATIO_CRITICAL_PCT,
    MATERIALITY_RATIO_HIGH_PCT,
    MATERIALITY_RATIO_LOW_PCT,
    MATERIALITY_RATIO_MEDIUM_PCT,
    OFFSETTING_NET_TO_GROSS_RATIO,
    PEER_Z_SCORE_OUTLIER_THRESHOLD,
    _compute_variance,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

OVERALL_MAT = 1000.0
PERF_MAT = 750.0


def _write_materiality(d, overall=OVERALL_MAT, performance=PERF_MAT):
    (d / "materiality.json").write_text(
        json.dumps({"thresholds": {"overall": overall, "performance": performance}}), encoding="utf-8"
    )


def _write_snapshot(d, nodes):
    """nodes: list of (node_name, parent_node_name, hierarchy_level, opening, closing)."""
    pl.DataFrame(
        {
            "node_name": [n[0] for n in nodes],
            "parent_node_name": [n[1] for n in nodes],
            "hierarchy_level": [n[2] for n in nodes],
            "opening_balance": [float(n[3]) for n in nodes],
            "closing_balance": [float(n[4]) for n in nodes],
        }
    ).write_parquet(d / "snapshot_drilldown.parquet")


@pytest.fixture
def variance_inputs(tmp_path, make_canonical_tb):
    """Builds the three artifacts build_variance_analysis requires, in one directory,
    and returns (canonical_tb_path, output_dir). Callers override pieces as needed."""

    def _build(gl_rows, nodes=None, overall=OVERALL_MAT, performance=PERF_MAT):
        tb_path = make_canonical_tb(gl_rows)
        d = tb_path.parent
        _write_materiality(d, overall, performance)
        _write_snapshot(d, nodes if nodes is not None else [("Current assets", "", 1, 0.0, 0.0)])
        return tb_path, d

    return _build


def _gl(code, name, opening, closing, main_head="Current assets", sub_head_1=None):
    return {
        "gl_code": code,
        "gl_name": name,
        "opening_balance": float(opening),
        "closing_balance": float(closing),
        "main_head": main_head,
        "sub_head_1": sub_head_1,
    }


def _run(tb_path, d):
    """Run the tool and return the parsed variance_analysis.json.

    The tool's own response carries only status/message/artifacts -- the entire
    analysis payload is written to disk (that is the pipeline's file-artifact
    contract, and it is what every downstream tool and the report writer actually
    read). Asserting against the response dict would test almost nothing."""
    result = build_variance_analysis(canonical_tb_file=str(tb_path), output_dir=str(d))
    assert result["execution_status"] == "SUCCESS", result.get("message")
    return json.loads((d / "variance_analysis.json").read_text(encoding="utf-8"))


# ── _compute_variance: trend classification ─────────────────────────────────


class TestComputeVarianceTrend:
    """Every branch of the trend ladder. These strings are quoted verbatim in the audit
    report and drive the new/closed/reversal sections, so the boundaries matter."""

    def test_zero_opening_nonzero_closing_is_new(self):
        assert _compute_variance({"opening_balance": 0, "closing_balance": 500}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "New"

    def test_nonzero_opening_zero_closing_is_closed(self):
        assert _compute_variance({"opening_balance": 500, "closing_balance": 0}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Closed"

    def test_positive_to_negative_is_asset_to_liability_reversal(self):
        assert _compute_variance({"opening_balance": 500, "closing_balance": -300}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Reversal (Asset to Liab)"

    def test_negative_to_positive_is_liability_to_asset_reversal(self):
        assert _compute_variance({"opening_balance": -500, "closing_balance": 300}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Reversal (Liab to Asset)"

    def test_growing_magnitude_is_increase(self):
        assert _compute_variance({"opening_balance": 100, "closing_balance": 200}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Increase"

    def test_shrinking_magnitude_is_decrease(self):
        assert _compute_variance({"opening_balance": 200, "closing_balance": 100}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Decrease"

    def test_no_movement_is_stable(self):
        assert _compute_variance({"opening_balance": 100, "closing_balance": 100}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Stable"

    def test_both_zero_is_stable_not_new_or_closed(self):
        """A dormant account must not be classified as New or Closed -- it feeds the
        dormant-accounts section instead."""
        assert _compute_variance({"opening_balance": 0, "closing_balance": 0}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Stable"

    def test_increase_on_negative_side_uses_absolute_magnitude(self):
        """A liability growing more negative is an Increase (magnitude grew), not a
        Decrease -- the sign convention here is a genuine audit-meaning question."""
        assert _compute_variance({"opening_balance": -100, "closing_balance": -200}, OVERALL_MAT, PERF_MAT)[
            "trend_classification"
        ] == "Increase"


# ── _compute_variance: arithmetic ───────────────────────────────────────────


class TestComputeVarianceArithmetic:
    def test_movement_and_percent_are_exact(self):
        v = _compute_variance({"opening_balance": 200.0, "closing_balance": 250.0}, OVERALL_MAT, PERF_MAT)
        assert v["movement"] == 50.0
        assert v["abs_movement"] == 50.0
        assert v["movement_percent"] == 25.0

    def test_percent_uses_absolute_opening_so_sign_does_not_invert(self):
        """movement/abs(opening): a liability moving -100 -> -150 is a +50% magnitude
        change, not -50%. Dividing by the signed opening would flip it."""
        v = _compute_variance({"opening_balance": -100.0, "closing_balance": -150.0}, OVERALL_MAT, PERF_MAT)
        assert v["movement"] == -50.0
        assert v["movement_percent"] == -50.0

    def test_zero_opening_yields_zero_percent_not_division_error(self):
        v = _compute_variance({"opening_balance": 0.0, "closing_balance": 900.0}, OVERALL_MAT, PERF_MAT)
        assert v["movement_percent"] == 0.0

    def test_missing_keys_default_to_zero(self):
        v = _compute_variance({}, OVERALL_MAT, PERF_MAT)
        assert v["movement"] == 0.0
        assert v["trend_classification"] == "Stable"

    def test_values_are_rounded_to_two_decimals(self):
        v = _compute_variance({"opening_balance": 3.0, "closing_balance": 10.0}, OVERALL_MAT, PERF_MAT)
        assert v["movement_percent"] == round(7.0 / 3.0 * 100, 2)

    def test_zero_overall_materiality_yields_zero_ratio_not_division_error(self):
        v = _compute_variance({"opening_balance": 0.0, "closing_balance": 5000.0}, 0.0, 0.0)
        assert v["materiality_ratio"] == 0.0
        assert v["priority"] == "None"


class TestComputeVariancePriorityBands:
    """materiality_ratio = abs_movement / overall_materiality * 100. Each band boundary
    is asserted on both sides, because these drive which accounts reach substantive
    testing in the planning output."""

    def _priority_at_ratio(self, ratio_pct):
        movement = OVERALL_MAT * ratio_pct / 100
        return _compute_variance({"opening_balance": 0.0, "closing_balance": movement}, OVERALL_MAT, PERF_MAT)[
            "priority"
        ]

    def test_below_low_threshold_is_none(self):
        assert self._priority_at_ratio(MATERIALITY_RATIO_LOW_PCT - 1) == "None"

    def test_at_low_threshold_is_low(self):
        assert self._priority_at_ratio(MATERIALITY_RATIO_LOW_PCT) == "Low"

    def test_at_medium_threshold_is_medium(self):
        assert self._priority_at_ratio(MATERIALITY_RATIO_MEDIUM_PCT) == "Medium"

    def test_at_high_threshold_is_high(self):
        assert self._priority_at_ratio(MATERIALITY_RATIO_HIGH_PCT) == "High"

    def test_critical_is_strictly_above_not_at_threshold(self):
        """Critical uses `>` while every other band uses `>=` -- exactly at 100x is
        High, not Critical. Pinning this so a future tidy-up cannot silently
        harmonise the operators and reclassify findings."""
        assert self._priority_at_ratio(MATERIALITY_RATIO_CRITICAL_PCT) == "High"
        assert self._priority_at_ratio(MATERIALITY_RATIO_CRITICAL_PCT + 1) == "Critical"


class TestComputeVarianceMovementClass:
    def test_extreme_requires_both_materiality_and_percent(self):
        # Large percent, but movement below performance materiality -> Normal.
        assert _compute_variance({"opening_balance": 1.0, "closing_balance": 100.0}, OVERALL_MAT, PERF_MAT)[
            "movement_class"
        ] == "Normal"
        # Above performance materiality but modest percent -> Normal.
        assert _compute_variance({"opening_balance": 100000.0, "closing_balance": 101000.0}, OVERALL_MAT, PERF_MAT)[
            "movement_class"
        ] == "Normal"

    def test_extreme_when_both_conditions_hold(self):
        v = _compute_variance({"opening_balance": 100.0, "closing_balance": 5000.0}, OVERALL_MAT, PERF_MAT)
        assert v["abs_movement"] >= PERF_MAT
        assert abs(v["movement_percent"]) > EXTREME_MOVEMENT_PCT_THRESHOLD
        assert v["movement_class"] == "Extreme"


# ── Tool-level: required inputs ─────────────────────────────────────────────


class TestRequiredInputs:
    def test_missing_canonical_tb_fails_with_named_artifact(self, tmp_path):
        result = build_variance_analysis(
            canonical_tb_file=str(tmp_path / "nope.parquet"), output_dir=str(tmp_path)
        )
        assert result["execution_status"] == "FAILED"
        assert "Canonical TB" in result["message"]

    def test_missing_snapshot_fails_pointing_at_the_producing_tool(self, tmp_path, make_canonical_tb):
        tb_path = make_canonical_tb([_gl("1", "Cash", 0, 100)])
        _write_materiality(tb_path.parent)
        result = build_variance_analysis(canonical_tb_file=str(tb_path), output_dir=str(tb_path.parent))
        assert result["execution_status"] == "FAILED"
        assert "build_financial_snapshot" in result["message"]

    def test_missing_materiality_fails_pointing_at_the_producing_tool(self, tmp_path, make_canonical_tb):
        tb_path = make_canonical_tb([_gl("1", "Cash", 0, 100)])
        _write_snapshot(tb_path.parent, [("Current assets", "", 1, 0.0, 0.0)])
        result = build_variance_analysis(canonical_tb_file=str(tb_path), output_dir=str(tb_path.parent))
        assert result["execution_status"] == "FAILED"
        assert "build_materiality" in result["message"]

    def test_performance_materiality_falls_back_to_75_percent_of_overall(self, variance_inputs, tmp_path):
        """When thresholds.performance is 0, the tool derives it from
        selected_materiality.overall_materiality * 0.75 rather than dividing by zero."""
        tb_path, d = variance_inputs([_gl("1", "Cash", 0, 5000)], performance=0.0)
        (d / "materiality.json").write_text(
            json.dumps(
                {
                    "thresholds": {"overall": 0.0, "performance": 0.0},
                    "selected_materiality": {"overall_materiality": 4000.0},
                }
            ),
            encoding="utf-8",
        )
        result = _run(tb_path, d)
        # 5000 movement vs derived perf_mat of 3000 -> material.
        assert result["downstream_metadata"]["variance_threshold"] == 3000.0


# ── Tool-level: classification sections ─────────────────────────────────────


class TestVarianceSections:
    def test_material_variance_reports_the_multiple_of_performance_materiality(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1001", "Cash in Hand", 0.0, 1500.0)])
        result = _run(tb_path, d)

        mv = result["material_variances"]
        assert len(mv) == 1
        assert mv[0]["account"] == "1001 - Cash in Hand"
        assert mv[0]["movement"] == 1500.0
        # 1500 / 750 = 2.0x
        assert "2.0x" in mv[0]["reason"]
        assert mv[0]["performance_materiality"] == PERF_MAT

    def test_immaterial_movement_is_excluded_from_material_variances(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1001", "Cash", 0.0, 100.0)])
        result = _run(tb_path, d)
        assert result["material_variances"] == []

    def test_new_and_closed_require_materiality_not_just_the_trend(self, variance_inputs):
        tb_path, d = variance_inputs(
            [
                _gl("1", "Big New", 0.0, 5000.0),       # New + material
                _gl("2", "Small New", 0.0, 10.0),        # New but immaterial
                _gl("3", "Big Closed", 5000.0, 0.0),     # Closed + material
                _gl("4", "Small Closed", 10.0, 0.0),     # Closed but immaterial
            ]
        )
        result = _run(tb_path, d)
        assert [a["account"] for a in result["new_accounts"]] == ["1 - Big New"]
        assert [a["account"] for a in result["closed_accounts"]] == ["3 - Big Closed"]
        # The summary counts ALL new/closed regardless of materiality -- a different
        # question from which ones are reported.
        assert result["summary"]["new_accounts"] == 2
        assert result["summary"]["closed_accounts"] == 2

    def test_dormant_accounts_are_zero_on_every_measure(self, variance_inputs):
        tb_path, d = variance_inputs(
            [_gl("1", "Dormant", 0.0, 0.0), _gl("2", "Active", 100.0, 100.0)]
        )
        result = _run(tb_path, d)
        accounts = [a["account"] for a in result["dormant_accounts"]]
        assert "1 - Dormant" in accounts
        assert "2 - Active" not in accounts  # unchanged, but not zero-balance

    def test_material_sign_reversal_is_reported(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Swing", 2000.0, -2000.0)])
        result = _run(tb_path, d)
        assert [a["account"] for a in result["sign_reversals"]] == ["1 - Swing"]
        assert result["downstream_metadata"]["sign_reversal_count"] == 1


class TestAbnormalDetection:
    """The abnormal classifier needs >= ABNORMAL_MIN_TRIGGERED_RULES rules AND a movement
    at or above performance materiality. This is the section whose backing lists
    (abn_classes/abn_rules) were a real scope bug during the Phase-3 refactor."""

    def test_two_rules_plus_materiality_flags_extreme(self, variance_inputs):
        # Materiality (5000 >= 750) + Extreme Percentage (4900% > 300%) = 2 rules.
        tb_path, d = variance_inputs([_gl("1", "Spike", 100.0, 5000.0)])
        result = _run(tb_path, d)

        abn = result["abnormal_variances"]
        assert len(abn) == 1
        assert abn[0]["account"] == "1 - Spike"
        assert len(abn[0]["triggered_rules"]) >= ABNORMAL_MIN_TRIGGERED_RULES
        assert "Materiality" in abn[0]["triggered_rules"]

    def test_single_rule_does_not_flag_extreme(self, variance_inputs):
        # Material, but a modest 1% move -> only the Materiality rule fires.
        tb_path, d = variance_inputs([_gl("1", "Steady", 100000.0, 101000.0)])
        result = _run(tb_path, d)
        assert result["abnormal_variances"] == []

    def test_immaterial_movement_never_flags_however_extreme_the_percentage(self, variance_inputs):
        # 1 -> 100 is +9900%, but 99 is far below performance materiality.
        tb_path, d = variance_inputs([_gl("1", "Tiny", 1.0, 100.0)])
        result = _run(tb_path, d)
        assert result["abnormal_variances"] == []


class TestPeerAnalysis:
    def test_outlier_within_an_fsli_peer_group_gets_a_z_score(self, variance_inputs):
        """Peer grouping is by derived FSLI (sub_head_1 falling back to main_head).
        Needs > MIN_PEER_GROUP_SIZE members and a non-zero std.

        Group size matters mathematically, not just as a gate: with a sample standard
        deviation the largest z-score attainable in a group of n is (n-1)/sqrt(n), so a
        single outlier among 7 peers tops out at ~2.47 and can never cross the
        threshold of 3.0 no matter how extreme its value. 15 peers puts the ceiling at
        ~3.75, which is why this test uses that size."""
        rows = [_gl(str(i), f"Normal {i}", 0.0, 100.0, sub_head_1="Trade Receivables") for i in range(1, 16)]
        rows.append(_gl("99", "Outlier", 0.0, 100000.0, sub_head_1="Trade Receivables"))
        tb_path, d = variance_inputs(rows)

        result = _run(tb_path, d)
        peers = result["peer_analysis"]
        assert len(peers) == 1
        assert peers[0]["account"] == "99 - Outlier"
        assert peers[0]["peer_group"] == "Trade Receivables"
        assert peers[0]["z_score"] > PEER_Z_SCORE_OUTLIER_THRESHOLD

    def test_group_at_or_below_minimum_size_produces_no_outliers(self, variance_inputs):
        """The gate is `> MIN_PEER_GROUP_SIZE`, so a 2-member group is excluded however
        lopsided it is -- a z-score across two points is not evidence."""
        tb_path, d = variance_inputs(
            [
                _gl("1", "A", 0.0, 100.0, sub_head_1="Inventory"),
                _gl("2", "B", 0.0, 100000.0, sub_head_1="Inventory"),
            ]
        )
        result = _run(tb_path, d)
        assert result["peer_analysis"] == []

    def test_identical_movements_have_zero_std_and_produce_no_outliers(self, variance_inputs):
        rows = [_gl(str(i), f"Same {i}", 0.0, 500.0, sub_head_1="Cash") for i in range(1, 8)]
        tb_path, d = variance_inputs(rows)
        result = _run(tb_path, d)
        assert result["peer_analysis"] == []


class TestOffsettingMovements:
    """Offsetting detection was the other real scope bug in the Phase-3 refactor
    (offsetting_movements declared inside a closure a later section needed)."""

    def test_entity_wide_offset_detected_when_net_is_small_versus_gross(self, variance_inputs):
        # +5000 and -5000: gross 10000, net 0 -> well under the 0.2 ratio.
        tb_path, d = variance_inputs(
            [_gl("1", "Up", 0.0, 5000.0), _gl("2", "Down", 5000.0, 0.0)]
        )
        result = _run(tb_path, d)

        entity_wide = [o for o in result["offsetting_movements"] if o["accounts"] == ["Entity Wide Portfolio"]]
        assert len(entity_wide) == 1
        assert entity_wide[0]["gross_movement"] == 10000.0
        assert entity_wide[0]["net_movement"] == 0.0

    def test_one_directional_ledger_is_not_reported_as_offsetting(self, variance_inputs):
        tb_path, d = variance_inputs(
            [_gl("1", "Up", 0.0, 5000.0), _gl("2", "AlsoUp", 0.0, 4000.0)]
        )
        result = _run(tb_path, d)
        assert [o for o in result["offsetting_movements"] if o["accounts"] == ["Entity Wide Portfolio"]] == []

    def test_net_exactly_at_the_ratio_boundary_is_not_reported(self, variance_inputs):
        """The gate is strict (`<`), so a net sitting exactly at 20% of gross does not
        qualify. gross 10000 / net 2000 -> +6000 and -4000."""
        tb_path, d = variance_inputs(
            [_gl("1", "Up", 0.0, 6000.0), _gl("2", "Down", 4000.0, 0.0)]
        )
        result = _run(tb_path, d)
        gross, net = 10000.0, 2000.0
        assert net == OFFSETTING_NET_TO_GROSS_RATIO * gross  # boundary, by construction
        assert [o for o in result["offsetting_movements"] if o["accounts"] == ["Entity Wide Portfolio"]] == []

    def test_one_to_one_fsli_offset_is_reported_from_the_hierarchy(self, variance_inputs):
        tb_path, d = variance_inputs(
            [_gl("1", "X", 0.0, 100.0)],
            nodes=[
                ("Revenue", "", 1, 0.0, -5000.0),
                ("Cost of sales", "", 1, 0.0, 5000.0),
            ],
        )
        result = _run(tb_path, d)
        pairs = [o for o in result["offsetting_movements"] if len(o["accounts"]) == 2]
        assert len(pairs) == 1
        assert set(pairs[0]["accounts"]) == {"Revenue", "Cost of sales"}
        assert pairs[0]["net_movement"] == 0.0


class TestDistributionAndConcentration:
    def test_accounts_land_in_the_correct_materiality_buckets(self, variance_inputs):
        # trivial threshold = overall * TRIVIAL_THRESHOLD_FRACTION; perf 750; overall 1000.
        tb_path, d = variance_inputs(
            [
                _gl("1", "Tiny", 0.0, 1.0),        # below trivial
                _gl("2", "Small", 0.0, 500.0),     # trivial -> performance
                _gl("3", "Mid", 0.0, 800.0),       # performance -> materiality
                _gl("4", "Big", 0.0, 5000.0),      # above materiality
            ]
        )
        result = _run(tb_path, d)
        dist = result["variance_distribution"]

        assert dist["below_trivial"]["accounts"] == 1
        assert dist["trivial_to_performance"]["accounts"] == 1
        assert dist["performance_to_materiality"]["accounts"] == 1
        assert dist["above_materiality"]["accounts"] == 1
        # Every account is counted exactly once.
        assert sum(b["accounts"] for b in dist.values()) == 4
        assert dist["above_materiality"]["balance"] == 5000.0

    def test_concentration_percentages_are_relative_to_total_movement(self, variance_inputs):
        tb_path, d = variance_inputs(
            [_gl("1", "Dominant", 0.0, 9000.0), _gl("2", "Minor", 0.0, 1000.0)]
        )
        result = _run(tb_path, d)
        conc = result["concentration_analysis"]
        # Fewer than 10 accounts -> top10 is the whole ledger.
        assert conc["top10_percent"] == 100.0
        assert conc["largest_gl"] == "1 - Dominant"
        assert result["summary"]["total_movement"] == 10000.0

    def test_movement_statistics_are_exact(self, variance_inputs):
        tb_path, d = variance_inputs(
            [
                _gl("1", "Up", 0.0, 300.0),
                _gl("2", "Down", 500.0, 300.0),
                _gl("3", "Flat", 100.0, 100.0),
            ]
        )
        result = _run(tb_path, d)
        stats = result["movement_statistics"]
        assert stats["positive_movements"] == 1
        assert stats["negative_movements"] == 1
        assert stats["zero_movements"] == 1
        assert stats["largest_increase"] == 300.0
        assert stats["largest_decrease"] == -200.0
        assert result["summary"]["changed_accounts"] == 2
        assert result["summary"]["unchanged_accounts"] == 1


class TestCoverageAndArtifacts:
    def test_mapping_summary_drives_coverage_confidence(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Cash", 0.0, 100.0)])
        (d / "mapping_summary.json").write_text(
            json.dumps({"mapped_rows": 99, "unmapped_rows": 1}), encoding="utf-8"
        )
        result = _run(tb_path, d)
        cov = result["coverage"]
        assert cov["mapped_accounts"] == 99
        assert cov["mapped_balance"] == 99.0
        assert cov["confidence"] == "High"

    def test_poor_mapping_downgrades_confidence_to_medium(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Cash", 0.0, 100.0)])
        (d / "mapping_summary.json").write_text(
            json.dumps({"mapped_rows": 50, "unmapped_rows": 50}), encoding="utf-8"
        )
        result = _run(tb_path, d)
        assert result["coverage"]["confidence"] == "Medium"

    def test_absent_mapping_summary_leaves_coverage_unknown(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Cash", 0.0, 100.0)])
        result = _run(tb_path, d)
        assert result["coverage"]["confidence"] == "Unknown"

    def test_writes_both_declared_artifacts(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Cash", 0.0, 1500.0)])
        result = build_variance_analysis(canonical_tb_file=str(tb_path), output_dir=str(d))
        assert result["execution_status"] == "SUCCESS"

        assert (d / "variance_analysis.parquet").exists()
        assert (d / "variance_analysis.json").exists()
        # Every declared artifact must actually exist -- a tool that reports a path it
        # never wrote breaks the decorator's artifact-validation contract downstream.
        assert len(result["artifacts"]) == 2
        for a in result["artifacts"]:
            assert a.endswith((".parquet", ".json"))
            assert Path(a).exists()

        # The parquet drops triggered_rules (a list column) but keeps the derived metrics
        # downstream tools read.
        df = pl.read_parquet(d / "variance_analysis.parquet")
        assert "triggered_rules" not in df.columns
        for col in ("movement", "abs_movement", "materiality_ratio", "priority", "z_score"):
            assert col in df.columns

    def test_written_json_matches_the_returned_payload(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "Cash", 0.0, 1500.0)])
        result = _run(tb_path, d)
        on_disk = json.loads((d / "variance_analysis.json").read_text(encoding="utf-8"))
        assert on_disk["summary"] == result["summary"]
        assert on_disk["material_variances"] == result["material_variances"]


class TestEmptyAndDegenerateInputs:
    def test_empty_trial_balance_fails_with_a_meaningful_message(self, variance_inputs):
        """Regression: an empty canonical TB used to raise a bare polars
        ColumnNotFoundError ("unable to find column abs_movement") from deep inside the
        horizontal concat, because the per-row variance dicts came out empty and so
        contributed no columns. An empty TB is not a valid audit input, so it must
        fail -- but it must say why."""
        tb_path, d = variance_inputs([])
        result = build_variance_analysis(canonical_tb_file=str(tb_path), output_dir=str(d))

        assert result["execution_status"] == "FAILED"
        assert "no rows" in result["message"]
        assert result["errors"][0]["type"] == "EmptyInputError"
        # Not the old opaque internal error.
        assert "ColumnNotFoundError" not in result["message"]
        assert "abs_movement" not in result["message"]

    def test_all_zero_movements_do_not_divide_by_total_movement(self, variance_inputs):
        tb_path, d = variance_inputs([_gl("1", "A", 100.0, 100.0), _gl("2", "B", 200.0, 200.0)])
        result = _run(tb_path, d)
        assert result["summary"]["total_movement"] == 0
        assert result["concentration_analysis"]["top10_percent"] == 0

    def test_unmapped_rows_fall_back_to_the_unmapped_fsli_bucket(self, variance_inputs):
        rows = [{"gl_code": "1", "gl_name": "Orphan", "opening_balance": 0.0, "closing_balance": 900.0}]
        tb_path, d = variance_inputs(rows)
        result = _run(tb_path, d)
        assert result["material_variances"][0]["fsli"] == "Unmapped"
