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

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "backend.agent, which needs it.",
)

from modes.trial_balance.pipeline.agent import call_tool
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

    def test_layer2_patch_runs_before_exception_consolidation(self, phase2_run):
        """TB-R19/R21: validate_layer2_tb (which patches TB-012/013/016/017/018/020 from
        SKIPPED to a real PASS/WARNING/HALTED status) must run BEFORE
        build_exception_consolidator, build_assertion_evidence_map and
        validate_tb_pipeline -- all three used to consume layer1_results.json while those
        six rules were still SKIPPED stubs, so a genuinely HALTED TB-012 never reached
        exception consolidation or pipeline validation at all. Asserted on call ORDER
        (not artifact content, which phase2_run's stub layer1_results.json doesn't carry
        TB-012 in) since that's the actual defect: a wiring/sequencing bug, not a content
        bug any individual tool's own tests would catch."""
        tb, run_dir = phase2_run
        call_order = []
        real_call_tool = call_tool

        def _recording_call_tool(tool_name, **kwargs):
            call_order.append(tool_name)
            return real_call_tool(tool_name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_recording_call_tool):
            mock_agent.return_value.llm_client = None
            _run_core_analytics_chain(tb, str(run_dir))

        assert "validate_layer2_tb" in call_order, "validate_layer2_tb did not run"
        assert "build_exception_consolidator" in call_order, "build_exception_consolidator did not run"
        assert call_order.index("validate_layer2_tb") < call_order.index("build_exception_consolidator"), (
            f"validate_layer2_tb must run before build_exception_consolidator; got order {call_order}"
        )
        assert call_order.index("validate_layer2_tb") < call_order.index("validate_tb_pipeline"), (
            f"validate_layer2_tb must run before validate_tb_pipeline; got order {call_order}"
        )

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


class TestNettingScreenWiring:
    """Wave 2 Fix 3: build_netting_screen was implemented and unit-tested but never
    actually wired into _run_core_analytics_chain -- confirmed live: a real EPIL
    COMPARISON re-run showed going_concern_screen.json's cash_and_bank still at the full
    gross ~Rs 52,629cr figure, and no netting_screen.json in the session at all, because
    the chain never called it. Must run, and must run BEFORE build_going_concern_screen
    so netted_balances.parquet exists for it to read."""

    def test_netting_screen_runs_and_precedes_going_concern_screen(self, phase2_run):
        tb, run_dir = phase2_run
        call_order = []
        real_call_tool = call_tool

        def _recording_call_tool(tool_name, **kwargs):
            call_order.append(tool_name)
            return real_call_tool(tool_name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_recording_call_tool):
            mock_agent.return_value.llm_client = None
            _run_core_analytics_chain(tb, str(run_dir))

        assert "build_netting_screen" in call_order, "build_netting_screen did not run"
        assert (Path(run_dir) / "netting_screen.json").exists()
        assert call_order.index("build_netting_screen") < call_order.index("build_going_concern_screen"), (
            f"build_netting_screen must run before build_going_concern_screen; got order {call_order}"
        )


class TestWave3ScreensWiring:
    """Wave 3: every new screen (build_contract_exposure_lens, build_foreign_operations_
    lens, build_deposit_margin_money_screen, build_provisions_writeoff_screen,
    build_msme_interest_screen) must actually run in the deterministic chain -- Wave 2's
    build_netting_screen was built and unit-tested but never wired in, and only a live
    re-triage caught it; this test exists so the same class of gap can't ship silently
    for Wave 3's screens too."""

    def test_all_wave3_screens_run_and_contract_exposure_precedes_deposit_screen(self, phase2_run):
        tb, run_dir = phase2_run
        call_order = []
        real_call_tool = call_tool

        def _recording_call_tool(tool_name, **kwargs):
            call_order.append(tool_name)
            return real_call_tool(tool_name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_recording_call_tool):
            mock_agent.return_value.llm_client = None
            _run_core_analytics_chain(tb, str(run_dir))

        for tool_name, artifact in (
            ("build_contract_exposure_lens", "contract_exposure.json"),
            ("build_foreign_operations_lens", "foreign_operations.json"),
            ("build_deposit_margin_money_screen", "deposit_margin_money_screen.json"),
            ("build_provisions_writeoff_screen", "provisions_writeoff_screen.json"),
            ("build_msme_interest_screen", "msme_interest_screen.json"),
        ):
            assert tool_name in call_order, f"{tool_name} did not run"
            assert (Path(run_dir) / artifact).exists(), f"{artifact} was not written"

        assert call_order.index("build_contract_exposure_lens") < call_order.index("build_deposit_margin_money_screen"), (
            f"build_contract_exposure_lens must run before build_deposit_margin_money_screen; got order {call_order}"
        )


class TestWave8ScreensWiring:
    """Wave 8: every new screen (build_unbilled_revenue_screen, build_dta_recoverability_
    screen, build_wip_contract_asset_screen) must actually run in the deterministic chain
    -- same lesson as Wave 2's netting-screen miss and Wave 3's own wiring test."""

    def test_all_wave8_screens_run(self, phase2_run):
        tb, run_dir = phase2_run
        call_order = []
        real_call_tool = call_tool

        def _recording_call_tool(tool_name, **kwargs):
            call_order.append(tool_name)
            return real_call_tool(tool_name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_recording_call_tool):
            mock_agent.return_value.llm_client = None
            _run_core_analytics_chain(tb, str(run_dir))

        for tool_name, artifact in (
            ("build_unbilled_revenue_screen", "unbilled_revenue_screen.json"),
            ("build_dta_recoverability_screen", "dta_recoverability_screen.json"),
            ("build_wip_contract_asset_screen", "wip_contract_asset_screen.json"),
        ):
            assert tool_name in call_order, f"{tool_name} did not run"
            assert (Path(run_dir) / artifact).exists(), f"{artifact} was not written"


class TestPYLegLayer2Register:
    """Wave 8 remark #30: validate_layer2_tb (TB-012/013/016/017/018/020 -- 6 rules,
    including TB-012's Assets=Liabilities+Equity identity check) previously only ran
    inside _run_core_analytics_chain, which the PY leg (run_full_analytics=False) never
    invokes -- so PY only ever got layer1's rules, never layer2's. It only needs
    canonical_tb_file + the layer1_results.json validate_layer1_tb already wrote in this
    same branch, so this is purely an invocation gap."""

    def test_py_leg_patches_layer2_rules_into_its_own_layer1_results(self, phase2_run):
        from modes.trial_balance.router import _run_layer1_precheck

        tb, run_dir = phase2_run
        real_call = call_tool

        def _redirect_load_tb_from_db(name, **kwargs):
            if name == "load_tb_from_db":
                return {"execution_status": "SUCCESS", "artifacts": [tb]}
            return real_call(name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_redirect_load_tb_from_db):
            mock_agent.return_value.llm_client = None
            _run_layer1_precheck("sess-1", "PY_DOC", str(run_dir), run_full_analytics=False)

        results = json.loads((Path(run_dir) / "layer1_results.json").read_text(encoding="utf-8"))
        by_rule = {r["rule"]: r for r in results}
        assert "TB-012" in by_rule, "PY leg's layer1_results.json has no TB-012 entry at all"
        assert by_rule["TB-012"]["status"] != "SKIPPED", (
            "PY leg's TB-012 is still SKIPPED -- validate_layer2_tb never patched it"
        )


class TestPYLegRatioAndRiskTools:
    """Wave 9 remark #21 (Part A): the comparative report's Sections 6/8/10 and the
    ratio-trend sheet were CY-only because build_financial_ratios/build_audit_ratio_
    pack/build_relationship_analytics/build_risk_indicators never ran for the PY leg,
    even though every prerequisite they need (fsli_summary.parquet, financial_
    snapshot_statistics.json) was already produced by the calls that DO run in this
    branch. Same wiring-gap lesson as TestWave3ScreensWiring and TestPYLegLayer2Register."""

    def test_py_leg_writes_ratio_and_risk_artifacts(self, phase2_run):
        from modes.trial_balance.router import _run_layer1_precheck

        tb, run_dir = phase2_run
        real_call = call_tool

        def _redirect_load_tb_from_db(name, **kwargs):
            if name == "load_tb_from_db":
                return {"execution_status": "SUCCESS", "artifacts": [tb]}
            return real_call(name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_redirect_load_tb_from_db):
            mock_agent.return_value.llm_client = None
            _run_layer1_precheck("sess-1", "PY_DOC", str(run_dir), run_full_analytics=False)

        for artifact in (
            "financial_ratios.json",
            "audit_ratio_pack.json",
            "relationship_analytics.json",
            "risk_indicators.json",
        ):
            assert (Path(run_dir) / artifact).exists(), f"PY leg did not write {artifact}"


class TestPYLegMateriality:
    """Wave 2 Fix 4a: the PY leg of a COMPARISON run must build its own materiality.json,
    not leave PY-side movements graded against a threshold from a year they don't belong
    to. build_materiality's inputs (financial_snapshot_statistics.json, fsli_summary.
    parquet, canonical_tb.parquet, snapshot_drilldown.parquet) are already produced by the
    build_fsli_summary/build_financial_snapshot calls in the same run_full_analytics=False
    branch, so this is purely an invocation gap, not a missing dependency."""

    def test_py_leg_writes_materiality_json(self, phase2_run):
        from modes.trial_balance.router import _run_layer1_precheck

        tb, run_dir = phase2_run
        real_call = call_tool

        def _redirect_load_tb_from_db(name, **kwargs):
            if name == "load_tb_from_db":
                return {"execution_status": "SUCCESS", "artifacts": [tb]}
            return real_call(name, **kwargs)

        with patch("modes.trial_balance.router.get_agent") as mock_agent, \
             patch("modes.trial_balance.router.call_tool", side_effect=_redirect_load_tb_from_db):
            mock_agent.return_value.llm_client = None
            _run_layer1_precheck("sess-1", "PY_DOC", str(run_dir), run_full_analytics=False)

        assert (Path(run_dir) / "materiality.json").exists(), (
            "PY leg did not build materiality.json -- PY-side movements have no PY threshold"
        )
