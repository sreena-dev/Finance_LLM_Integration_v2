"""Phase-3 wiring tests: the deterministic /audit chain in routes.py.

The tools themselves are tested in tests/tools/. What is tested here is the ORDER
they run in, which is where this pipeline's real failure mode lives.

Two of the three Phase-0 defects were ordering or contract problems that every
individual tool's own tests passed straight through: build_relationship_analytics ran
before the analytics it read, and build_risk_indicators read keys nobody wrote. Both
reported SUCCESS throughout. A chain whose steps each pass in isolation can still be
wired wrong, and only a test that runs the chain catches it.

Every dependency asserted below is one a tool would silently degrade on rather than
fail: materiality_lens with no sensitive population elevates nothing, override
indicators with no anomaly findings drops two of its six signals, CARO with no
engagement context downgrades everything to information_request. Silent degradation
is why these are asserted rather than left to the tools' own error handling.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from modes.trial_balance.router import _run_core_analytics_chain


@pytest.fixture
def chain_run(phase2_run):
    """Run the real deterministic chain over the screen-exercising TB.

    get_agent() is patched out: two steps pass an llm_client through, and neither
    the anomaly scanner's semantic check nor audit reasoning's narrative needs a live
    model for the wiring to be exercised -- both degrade to their deterministic paths,
    which is exactly the behaviour a CI box without an LLM endpoint should get.
    """
    tb, run_dir = phase2_run
    # Remove the pre-seeded artifacts the chain is supposed to produce itself, so the
    # test proves the CHAIN wrote them rather than the fixture.
    for stale in ("anomaly_findings.json", "consolidated_exceptions.json", "data_sufficiency.json"):
        p = Path(run_dir) / stale
        if p.exists():
            p.unlink()

    with patch("modes.trial_balance.router.get_agent") as mock_agent:
        mock_agent.return_value.llm_client = None
        _run_core_analytics_chain(tb, str(run_dir))
    return tb, Path(run_dir)


def _load(run_dir, name):
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


class TestChainProducesPhase2Artifacts:
    @pytest.mark.parametrize("artifact", [
        "engagement_context.json",
        "normalisation_note.json",
        "audit_ratio_pack.json",
        "counterpart_screen.json",
        "relationship_expectations.json",
        "abnormal_sign_screen.json",
        "materiality_lens.json",
        "statutory_screen.json",
        "public_sector_lens.json",
        "going_concern_screen.json",
        "caro_indicators.json",
        "override_indicators.json",
        "assertion_evidence_map.json",
        "finding_records.json",
        "evidence_request_list.json",
        "management_query_list.json",
        "run_log.json",
    ])
    def test_artifact_written(self, chain_run, artifact):
        _, run_dir = chain_run
        assert (run_dir / artifact).exists(), f"{artifact} not produced by the chain"


class TestOrderingDependencies:
    """Each of these would degrade silently, not fail, if the order were wrong."""

    def test_materiality_lens_saw_the_sensitive_population(self, chain_run):
        """Runs after build_sensitive_detector, not after build_materiality. With no
        sensitive population it elevates nothing and the tool still reports SUCCESS."""
        _, run_dir = chain_run
        lens = _load(run_dir, "materiality_lens.json")
        assert lens["summary"]["sensitive_accounts_assessed"] > 0, (
            "materiality_lens ran before build_sensitive_detector -- it had no population "
            "to elevate, which is the whole purpose of the tool"
        )

    def test_override_indicators_consumed_the_anomaly_scanner(self, chain_run):
        """Reads anomaly_findings.json to reframe round-sum/Benford rather than
        recomputing them, so it must follow build_anomaly_scanner."""
        _, run_dir = chain_run
        data = _load(run_dir, "override_indicators.json")
        assert (run_dir / "anomaly_findings.json").exists()
        indicators = {i["indicator"] for i in data["indicators"]}
        assert {"round_sum_balances", "benford_deviation"} <= indicators

    def test_caro_read_the_engagement_context_gate(self, chain_run):
        """build_engagement_context runs first precisely so the applicability gates
        exist for this screen to read."""
        _, run_dir = chain_run
        caro = _load(run_dir, "caro_indicators.json")
        assert caro["applicability_gate"]["basis"] in ("unconfirmed", "supplied")
        assert (run_dir / "engagement_context.json").exists()

    def test_assertion_map_saw_consolidated_exceptions(self, chain_run):
        _, run_dir = chain_run
        assert (run_dir / "consolidated_exceptions.json").exists()
        aem = _load(run_dir, "assertion_evidence_map.json")
        assert "summary" in aem

    def test_normalisation_note_read_layer1_results(self, chain_run):
        """The note is only re-performable if it evidences the sign/scale rules."""
        _, run_dir = chain_run
        note = _load(run_dir, "normalisation_note.json")
        assert note["sign_convention"]["status"] != "NOT_RUN"
        assert note["currency_and_scale"]["status"] != "NOT_RUN"

    def test_finding_records_collected_from_multiple_screens(self, chain_run):
        """Runs after every screen. If it ran early it would find nothing and still
        report SUCCESS."""
        _, run_dir = chain_run
        fr = _load(run_dir, "finding_records.json")
        screens = {s["artifact"] for s in fr["summary"]["contributing_screens"]}
        assert len(screens) >= 4, f"only {len(screens)} screen(s) contributed: {screens}"
        assert fr["summary"]["total_records"] > 0

    def test_request_lists_built_from_the_consolidated_records(self, chain_run):
        _, run_dir = chain_run
        ev = _load(run_dir, "evidence_request_list.json")
        assert ev["summary"]["findings_covered"] > 0
        assert ev["summary"]["evidence_requests"] > 0

    def test_run_log_ran_last_and_saw_the_whole_run(self, chain_run):
        """Infers the tool list from artifacts on disk, so running it early would
        under-report what executed."""
        _, run_dir = chain_run
        log = _load(run_dir, "run_log.json")
        for tool in ("build_engagement_context", "build_counterpart_screen",
                     "build_finding_records", "build_exception_consolidator"):
            assert tool in log["tools_run"], f"{tool} missing from run log"
        assert len(log["artifacts"]) > 15


class TestChainRemainsResilient:
    def test_phase0_relationship_signals_still_reach_the_risk_engine(self, chain_run):
        """The Phase-0 defect regression, asserted through the real chain rather than
        through the two tools in isolation."""
        _, run_dir = chain_run
        rel = _load(run_dir, "relationship_analytics.json")
        for key in ("hierarchy_validation", "suspense_analysis",
                    "intercompany_relationships", "sign_relationships"):
            assert key in rel, f"{key} missing -- build_risk_indicators reads it"
        risk = _load(run_dir, "risk_indicators.json")
        assert risk["coverage"]["mapping_confidence"] != "Unknown"
        assert risk["coverage"]["relationships_consumed"] > 0

    def test_chain_does_not_raise_when_a_tool_is_unavailable(self, phase2_run):
        """Every step goes through run(), which logs and continues. A raising step
        would remove every later audit area from the report."""
        tb, run_dir = phase2_run
        from modes.trial_balance.pipeline.agent import ToolNotAvailableError

        real_call = None
        from modes.trial_balance import router as routes_mod
        real_call = routes_mod.call_tool

        def flaky(name, **kwargs):
            if name == "build_counterpart_screen":
                raise ToolNotAvailableError(name)
            if name == "build_statutory_screen":
                raise RuntimeError("simulated tool crash")
            return real_call(name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=flaky):
            mock_agent.return_value.llm_client = None
            _run_core_analytics_chain(tb, str(run_dir))  # must not raise

        # Later steps still ran despite the two failures above.
        assert (Path(run_dir) / "finding_records.json").exists()
        assert (Path(run_dir) / "run_log.json").exists()
