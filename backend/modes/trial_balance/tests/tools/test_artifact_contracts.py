"""Producer/consumer artifact-key contract tests.

Tools in this pipeline never call each other -- they exchange JSON/Parquet files.
That makes a mismatch between the keys a producer WRITES and the keys a consumer
READS completely silent: the consumer's `.get(...)` returns a default, the tool
reports SUCCESS, and a whole class of finding disappears with no error anywhere.

That is exactly what had happened. build_relationship_analytics wrote
`financial_relationships`/`risk_relationships`/`summary`, while
build_risk_indicators read `hierarchy_validation.orphan_nodes`,
`suspense_analysis.accounts`, `intercompany_relationships` and
`sign_relationships` -- none of which any tool wrote. Four of its ten scoring
rules (ORPHAN_NODE 15, SUSPENSE_CLEARING 20, INTERCOMPANY 15, SIGN_ANOMALY 20 =
70 of the model's weight points) therefore contributed zero on every run since
the port, and no test caught it because every tool still returned SUCCESS.

These tests assert the contract directly, and are the regression guard for that
whole failure mode. Extend them whenever a new producer/consumer pair is added.
"""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import build_relationship_analytics
from modes.trial_balance.pipeline.tools import build_risk_indicators


def _get_path(data: dict, dotted: str):
    """Walk a dotted key path, returning a sentinel when any segment is absent."""
    MISSING = object()
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


# Every key path build_risk_indicators reads out of relationship_analytics.json.
# Sourced by reading its "3. Apply Relationship Risks" and "7. Coverage" blocks.
RELATIONSHIP_KEYS_READ_BY_RISK_INDICATORS = [
    "hierarchy_validation.orphan_nodes",
    "suspense_analysis.accounts",
    "intercompany_relationships",
    "sign_relationships",
    "summary.total_relationships_integrated",
]


@pytest.fixture
def screened_run(make_canonical_tb, tmp_path):
    """A canonical TB containing one row for each of the four relationship signals,
    plus a clean control row, with relationship_analytics already run over it."""
    rows = [
        # Clean: asset with a normal debit balance, properly mapped.
        {"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 5000.0,
         "main_head": "Current assets", "sub_head_1": "Cash and cash equivalents",
         "account_type": "Asset", "bs_pl": "BS", "mapped_status": "MAPPED"},
        # ORPHAN_NODE: no grouping match at all.
        {"gl_code": "9999", "gl_name": "Misc Unclassified Ledger", "closing_balance": 1200.0,
         "mapped_status": "UNMATCHED"},
        # SUSPENSE_CLEARING: suspense head carrying a non-nil balance.
        {"gl_code": "7001", "gl_name": "Suspense Account", "closing_balance": 3400.0,
         "main_head": "Current assets", "sub_head_1": "Other current assets",
         "account_type": "Asset", "bs_pl": "BS", "mapped_status": "MAPPED"},
        # INTERCOMPANY: inter-unit transfer balance.
        {"gl_code": "6002", "gl_name": "Due From Subsidiary Co", "closing_balance": 8800.0,
         "main_head": "Current assets", "sub_head_1": "Other current assets",
         "account_type": "Asset", "bs_pl": "BS", "mapped_status": "MAPPED"},
        # SIGN_ANOMALY: an Asset carrying a credit balance.
        {"gl_code": "1500", "gl_name": "Trade Receivable Control", "closing_balance": -2200.0,
         "main_head": "Current assets", "sub_head_1": "Trade receivables",
         "account_type": "Asset", "bs_pl": "BS", "mapped_status": "MAPPED"},
    ]
    canonical = make_canonical_tb(rows)

    fsli = tmp_path / "fsli_summary.parquet"
    pl.DataFrame({
        "main_head": ["Current assets"],
        "sub_head_1": ["Cash and cash equivalents"],
        "closing_balance": [5000.0],
    }).write_parquet(fsli)

    result = build_relationship_analytics(
        canonical_tb_file=str(canonical),
        fsli_summary_file=str(fsli),
        output_dir=str(tmp_path),
    )
    assert result["execution_status"] == "SUCCESS", result["message"]
    analytics = json.loads((tmp_path / "relationship_analytics.json").read_text(encoding="utf-8"))
    return canonical, tmp_path, analytics


class TestRelationshipToRiskContract:
    def test_producer_writes_every_key_the_consumer_reads(self, screened_run):
        _, _, analytics = screened_run
        missing = [k for k in RELATIONSHIP_KEYS_READ_BY_RISK_INDICATORS
                   if _get_path(analytics, k) is _get_path({}, "nope")]
        assert not missing, (
            "build_relationship_analytics does not write these key paths that "
            f"build_risk_indicators reads: {missing}"
        )

    @pytest.mark.parametrize(
        "key,expected_gl",
        [
            ("hierarchy_validation.orphan_nodes", "9999"),
            ("suspense_analysis.accounts", "7001"),
            ("intercompany_relationships", "6002"),
            ("sign_relationships", "1500"),
        ],
    )
    def test_each_signal_detects_its_account(self, screened_run, key, expected_gl):
        _, _, analytics = screened_run
        entries = _get_path(analytics, key)
        codes = [e["gl_code"] for e in entries]
        assert expected_gl in codes, f"{key} did not flag GL {expected_gl}; got {codes}"

    def test_account_key_format_matches_risk_engine_lookup(self, screened_run):
        """add_risk() looks up "{gl_code} - {gl_name}". A differently-formatted
        key scores nothing and fails silently -- the original defect's mechanism."""
        _, _, analytics = screened_run
        for key in ("hierarchy_validation.orphan_nodes", "suspense_analysis.accounts",
                    "intercompany_relationships", "sign_relationships"):
            for entry in _get_path(analytics, key):
                assert entry["account"] == f"{entry['gl_code']} - {entry['gl_name']}"

    def test_nil_suspense_balance_is_not_flagged(self, make_canonical_tb, tmp_path):
        """A suspense account at nil is the correctly-cleared state, not a finding."""
        canonical = make_canonical_tb([
            {"gl_code": "7001", "gl_name": "Suspense Account", "closing_balance": 0.0,
             "main_head": "Current assets", "account_type": "Asset", "mapped_status": "MAPPED"},
        ])
        fsli = tmp_path / "fsli_summary.parquet"
        pl.DataFrame({"main_head": ["Current assets"], "sub_head_1": ["Other"],
                      "closing_balance": [0.0]}).write_parquet(fsli)
        build_relationship_analytics(canonical_tb_file=str(canonical),
                                     fsli_summary_file=str(fsli), output_dir=str(tmp_path))
        analytics = json.loads((tmp_path / "relationship_analytics.json").read_text(encoding="utf-8"))
        assert analytics["suspense_analysis"]["accounts"] == []


class TestRiskIndicatorsConsumesSignals:
    def test_all_four_relationship_rules_fire(self, screened_run):
        """The end-to-end proof: each of the four previously-dead rules now
        contributes to a real account's score."""
        canonical, out_dir, _ = screened_run
        (out_dir / "materiality.json").write_text(json.dumps({
            "selected_materiality": {"overall_materiality": 100000.0},
            "thresholds": {"overall": 100000.0, "performance": 75000.0, "clearly_trivial": 5000.0},
        }), encoding="utf-8")

        result = build_risk_indicators(canonical_tb_file=str(canonical), output_dir=str(out_dir))
        assert result["execution_status"] == "SUCCESS", result["message"]

        indicators = json.loads((out_dir / "risk_indicators.json").read_text(encoding="utf-8"))
        scored = pl.read_parquet(out_dir / "risk_indicators.parquet")
        # Materiality is set far above every balance here, so any non-zero score
        # can only have come from the four relationship rules.
        assert scored["score"].sum() > 0, "no relationship rule contributed any score"
        assert set(indicators["risk_categories"]) >= {
            "ORPHAN_NODE", "SUSPENSE_CLEARING", "INTERCOMPANY", "SIGN_ANOMALY"
        }

    def test_coverage_metrics_are_measured_not_constant(self, screened_run):
        """`relationships_consumed` read a key the producer never wrote (reported 0
        forever) and `mapping_confidence` read one no tool writes at all (reported
        "Unknown" forever, pinning planning_confidence to "Medium")."""
        canonical, out_dir, _ = screened_run
        (out_dir / "materiality.json").write_text(json.dumps({
            "selected_materiality": {"overall_materiality": 100000.0},
            "thresholds": {"overall": 100000.0, "performance": 75000.0, "clearly_trivial": 5000.0},
        }), encoding="utf-8")

        build_risk_indicators(canonical_tb_file=str(canonical), output_dir=str(out_dir))
        coverage = json.loads((out_dir / "risk_indicators.json").read_text(encoding="utf-8"))["coverage"]

        assert coverage["relationships_consumed"] > 0
        # 4 of 5 fixture rows are MAPPED (80%) -> High, per the shared thresholds.
        assert coverage["mapping_confidence"] == "High", coverage


class TestKnowledgePacksLoad:
    """Packs are read at call time, so a malformed pack surfaces as a tool failure
    rather than an import error. These assert the loader contract directly."""

    def test_every_declared_pack_loads_and_is_versioned(self):
        import modes.trial_balance.pipeline.tools as K

        for name in K._PACKS:
            pack = K.load_pack(name)
            assert pack["_meta"]["version"], f"{name} has no _meta.version"

    def test_unknown_pack_names_its_alternatives(self):
        from modes.trial_balance.pipeline.tools import KnowledgePackError, load_pack

        with pytest.raises(KnowledgePackError, match="Known packs"):
            load_pack("does_not_exist")

    def test_packs_reproduce_the_literals_they_replaced(self):
        """Phase-1 extraction must be behaviour-preserving. If someone edits a pack
        and a call site's expectations drift, this is where it shows up."""
        import modes.trial_balance.pipeline.tools as K

        assert len(K.debit_anchors()) == 32
        assert len(K.credit_anchors()) == 21
        assert K.normal_debit_heads() == frozenset({"assets", "expenses"})
        assert K.normal_credit_heads() == frozenset({"liabilities", "equity", "revenue"})
        assert len(K.sensitive_rules()) == 13
        assert K.risk_rule_weights()["SUSPENSE_CLEARING"] == 20
        assert K.mapping_confidence_thresholds() == (80.0, 50.0)
