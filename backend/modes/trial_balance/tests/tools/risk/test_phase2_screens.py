"""Tests for the Phase-2 risk screens.

Grouped in one module per domain rather than one per tool: these six screens share
the same fixture, the same contract and the same degradation behaviour, and splitting
them into six near-identical files would obscure the parts that actually differ.

Every screen is checked for three things:
  1. it detects what it exists to detect (the positive case, on a known TB);
  2. it does NOT fire on the control case planted in the same fixture;
  3. it degrades rather than fails when its optional upstream artifacts are absent.

(3) matters most in practice. Every one of these tools runs inside routes.py's
log-and-continue chain, so a tool that raises instead of degrading silently removes a
whole audit area from the report with only a log line to show for it.
"""

import json
from pathlib import Path

import pytest

from modes.trial_balance.pipeline.tools import (
    build_abnormal_sign_screen,
    build_caro_indicators,
    build_counterpart_screen,
    build_going_concern_screen,
    build_override_indicators,
    build_public_sector_lens,
    build_relationship_expectations,
    build_statutory_screen,
)
from modes.trial_balance.tests.conftest_phase2 import write_canonical

SCREENS = [
    (build_counterpart_screen, "counterpart_screen.json"),
    (build_relationship_expectations, "relationship_expectations.json"),
    (build_abnormal_sign_screen, "abnormal_sign_screen.json"),
    (build_statutory_screen, "statutory_screen.json"),
    (build_public_sector_lens, "public_sector_lens.json"),
    (build_going_concern_screen, "going_concern_screen.json"),
    (build_caro_indicators, "caro_indicators.json"),
    (build_override_indicators, "override_indicators.json"),
]


def _load(run_dir, name):
    return json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))


def _run(fn, tb, run_dir):
    result = fn(canonical_tb_file=tb, output_dir=str(run_dir))
    assert result["execution_status"] == "SUCCESS", result.get("message")
    return result


class TestContract:
    """Every screen honours the pipeline tool contract."""

    @pytest.mark.parametrize("fn,artifact", SCREENS, ids=[f.__name__ for f, _ in SCREENS])
    def test_writes_its_artifact_and_can_continue(self, fn, artifact, phase2_run):
        tb, run_dir = phase2_run
        result = _run(fn, tb, run_dir)
        assert result["can_continue"] is True
        assert (Path(run_dir) / artifact).exists()
        assert any(artifact in a for a in result["artifacts"])

    @pytest.mark.parametrize("fn,artifact", SCREENS, ids=[f.__name__ for f, _ in SCREENS])
    def test_degrades_without_upstream_artifacts(self, fn, artifact, minimal_tb, tmp_path):
        """No materiality, no sensitive accounts, no engagement context, no anomaly
        findings -- the screen must still succeed and say what it lost."""
        out = tmp_path / "bare"
        out.mkdir()
        result = fn(canonical_tb_file=minimal_tb, output_dir=str(out))
        assert result["execution_status"] == "SUCCESS", result.get("message")
        assert result["can_continue"] is True

    @pytest.mark.parametrize("fn,artifact", SCREENS, ids=[f.__name__ for f, _ in SCREENS])
    def test_every_finding_carries_the_sec14_shape(self, fn, artifact, phase2_run):
        tb, run_dir = phase2_run
        _run(fn, tb, run_dir)
        for record in _load(run_dir, artifact).get("finding_records", []):
            # sec 13's triad plus the fields that make a finding actionable.
            assert record["observation"], record
            assert record["expectation"], record
            assert record["evidence_requested"], f"sec 13.1 requires a named record: {record}"
            assert record["safe_limitation"], record
            assert record["risk_rating"] in ("high", "medium", "low", "information_request")
            assert record["risk_basis"], record


class TestCounterpartScreen:
    def test_flags_borrowings_without_finance_cost(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_counterpart_screen, tb, run_dir)
        ids = {f["relationship_id"] for f in _load(run_dir, "counterpart_screen.json")["finding_records"]}
        assert "REL-07" in ids, "borrowings with no finance cost was not flagged"

    def test_does_not_flag_ppe_when_depreciation_is_present(self, phase2_run):
        """The control case. Firing here would mean the screen reports on the
        PRESENCE of a pair, which is the inverted logic this tool exists to fix."""
        tb, run_dir = phase2_run
        _run(build_counterpart_screen, tb, run_dir)
        ids = {f["relationship_id"] for f in _load(run_dir, "counterpart_screen.json")["finding_records"]}
        assert "REL-05" not in ids, "PPE/depreciation flagged despite depreciation being present"

    def test_findings_carry_the_legitimate_explanations(self, phase2_run):
        """sec 7.2 -- valid reasons must travel with the observation so it is never
        read as a conclusion."""
        tb, run_dir = phase2_run
        _run(build_counterpart_screen, tb, run_dir)
        for f in _load(run_dir, "counterpart_screen.json")["finding_records"]:
            assert f["valid_reasons"], f"no valid_reasons on {f['relationship_id']}"


class TestRelationshipExpectations:
    def test_computes_debtor_intensity_outside_band(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_relationship_expectations, tb, run_dir)
        data = _load(run_dir, "relationship_expectations.json")
        rel01 = next(r for r in data["results"] if r["id"] == "REL-01")
        assert rel01["status"] == "outside_expectation"
        assert rel01["observed_ratio"] == pytest.approx(0.8, abs=0.01)

    def test_never_computes_a_ratio_without_a_denominator(self, phase2_run):
        """sec 6.2 -- an absent denominator is reported, never approximated."""
        tb, run_dir = phase2_run
        _run(build_relationship_expectations, tb, run_dir)
        for r in _load(run_dir, "relationship_expectations.json")["results"]:
            if r["status"] == "not_computed":
                assert "reason" in r and r.get("missing_component")
                assert "observed_ratio" not in r

    def test_rel09_inventory_not_computed_agrees_with_audit_ratio_pack(self, phase2_run):
        # Wave 2 Fix 1c: cross-engine consistency check. build_audit_ratio_pack and
        # build_relationship_expectations resolve "inventory" independently -- neither
        # this fixture nor a real services-sector TB carries an inventory account, so
        # BOTH engines must agree it is not computed, not have one silently read a false
        # positive off an unrelated keyword match (the client's "Inventory Intensity nil
        # though the relationship reads 100%" symptom).
        from modes.trial_balance.pipeline.tools import build_audit_ratio_pack

        tb, run_dir = phase2_run
        _run(build_relationship_expectations, tb, run_dir)
        rel09 = next(r for r in _load(run_dir, "relationship_expectations.json")["results"] if r["id"] == "REL-09")

        _run(build_audit_ratio_pack, tb, run_dir)
        inventory_intensity = _load(run_dir, "audit_ratio_pack.json")["ratios"]["inventory_intensity"]

        assert rel09["status"] == "not_computed"
        assert inventory_intensity["value"] is None
        assert inventory_intensity["status"] == "not_computed"


class TestAbnormalSignScreen:
    def test_flags_debit_balance_on_a_liability(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_abnormal_sign_screen, tb, run_dir)
        accounts = {f["account"] for f in _load(run_dir, "abnormal_sign_screen.json")["finding_records"]}
        assert any("Trade Payable" in a for a in accounts)

    def test_excludes_contra_accounts_from_findings(self, phase2_run):
        """Accumulated depreciation is a credit inside the asset block by
        construction. Flagging it buries the real signal under structural noise."""
        tb, run_dir = phase2_run
        _run(build_abnormal_sign_screen, tb, run_dir)
        data = _load(run_dir, "abnormal_sign_screen.json")
        flagged = {f["account"] for f in data["finding_records"]}
        assert not any("Accumulated Depreciation" in a for a in flagged)
        assert any("Accumulated Depreciation" in c["account"] for c in data["contra_accounts"])

    def test_skips_unmapped_accounts(self, phase2_run):
        """An unmapped account has no known normal side, so it is an information
        request rather than a sign finding."""
        tb, run_dir = phase2_run
        _run(build_abnormal_sign_screen, tb, run_dir)
        data = _load(run_dir, "abnormal_sign_screen.json")
        assert data["summary"]["unmapped_accounts_skipped"] >= 1
        assert not any("Misc Unclassified" in f["account"] for f in data["finding_records"])

    def test_names_the_class_specific_explanations(self, phase2_run):
        """sec 5.3 -- a debit payable may be a vendor advance. The alternatives must
        be stated, not left for the reader to supply."""
        tb, run_dir = phase2_run
        _run(build_abnormal_sign_screen, tb, run_dir)
        payable = next(f for f in _load(run_dir, "abnormal_sign_screen.json")["finding_records"]
                       if "Trade Payable" in f["account"])
        assert any("advance" in r.lower() for r in payable["valid_reasons"])


class TestStatutoryScreen:
    def test_flags_payroll_without_pf_or_esi(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_statutory_screen, tb, run_dir)
        ids = {f["screen_id"] for f in _load(run_dir, "statutory_screen.json")["finding_records"]}
        assert "STAT-PF-ESI" in ids

    def test_carries_exemption_caveats_and_named_returns(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_statutory_screen, tb, run_dir)
        for f in _load(run_dir, "statutory_screen.json")["finding_records"]:
            assert f["valid_reasons"], "exemption caveats missing"
            joined = " ".join(f["evidence_requested"]).lower()
            assert any(t in joined for t in ("return", "challan", "reconciliation")), joined

    def test_every_finding_is_regularity_flagged(self, phase2_run):
        """Statutory dues are material by context under sec 2.2 regardless of amount."""
        tb, run_dir = phase2_run
        _run(build_statutory_screen, tb, run_dir)
        assert all(f["regularity_flag"] for f in _load(run_dir, "statutory_screen.json")["finding_records"])


class TestPublicSectorLens:
    def test_raises_the_grant_purpose_question(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_public_sector_lens, tb, run_dir)
        ids = {f["question_id"] for f in _load(run_dir, "public_sector_lens.json")["finding_records"]}
        assert "PS-PURPOSE" in ids

    def test_elevates_by_context_not_value(self, phase2_run):
        """A small write-off still requires competent sanction. Only a nil balance
        is excluded."""
        tb, run_dir = phase2_run
        _run(build_public_sector_lens, tb, run_dir)
        findings = _load(run_dir, "public_sector_lens.json")["finding_records"]
        small = [f for f in findings if abs(f["amount"] or 0) < 750_000]
        assert small, "no sub-materiality account was raised by context"
        assert all("context" in f["risk_basis"] for f in findings)

    def test_never_asserts_government_company_status(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_public_sector_lens, tb, run_dir)
        data = _load(run_dir, "public_sector_lens.json")
        assert data["applicability"]["government_company_confirmed"] is None
        assert "does NOT establish" in data["applicability"]["note"]


class TestGoingConcernScreen:
    def test_detects_eroded_net_worth(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_going_concern_screen, tb, run_dir)
        data = _load(run_dir, "going_concern_screen.json")
        # capital -10m, accumulated losses +45m -> net worth -35m under debit-positive
        assert data["computed"]["net_worth"] == pytest.approx(-35_000_000.0)
        present = {i["indicator"] for i in data["indicators"] if i["present"]}
        assert "negative_or_eroded_net_worth" in present

    def test_states_it_draws_no_conclusion(self, phase2_run):
        """sec 1.3's worked example: 'The company is not a going concern' is the bad
        behaviour this screen must never produce."""
        tb, run_dir = phase2_run
        _run(build_going_concern_screen, tb, run_dir)
        data = _load(run_dir, "going_concern_screen.json")
        assert "not a conclusion" in data["non_conclusion"]
        blob = json.dumps(data["finding_records"]).lower()
        for phrase in ("is not a going concern", "material uncertainty exists", "will cease"):
            assert phrase not in blob, f"conclusion-shaped phrase leaked: {phrase}"

    def test_cash_figure_uses_netted_balance_when_netting_screen_has_run(self, tmp_path):
        # Wave 2 Fix 3b: EPIL's GL 20950021/20950022 ("SBI- MUSCAT (US$) -R"/"-P")
        # inflated this screen's cash-and-bank figure to gross (~Rs 52,629cr on the live
        # run) instead of the true net (~Rs 700cr) exposure. Once build_netting_screen has
        # already produced netted_balances.parquet for this output_dir, the cash figure
        # here must use it.
        from modes.trial_balance.pipeline.tools import build_netting_screen

        rows = [
            {"gl_code": "20950021", "gl_name": "SBI- MUSCAT (US$) -R", "closing_balance": 6_396_870_000.0,
             "main_head": "Current assets", "sub_head_1": "Cash and Bank Balances", "mapped_status": "MAPPED"},
            {"gl_code": "20950022", "gl_name": "SBI- MUSCAT (US$) -P", "closing_balance": -6_389_870_000.0,
             "main_head": "Current assets", "sub_head_1": "Cash and Bank Balances", "mapped_status": "MAPPED"},
        ]
        tb = write_canonical(tmp_path / "canonical_tb.parquet", rows)

        gross_run = tmp_path / "gross"
        gross_run.mkdir()
        _run(build_going_concern_screen, str(tb), gross_run)
        gross_cash = _load(gross_run, "going_concern_screen.json")["computed"]["cash_and_bank"]
        assert gross_cash == pytest.approx(6_396_870_000.0 + 6_389_870_000.0)

        netted_run = tmp_path / "netted"
        netted_run.mkdir()
        build_netting_screen(canonical_tb_file=str(tb), output_dir=str(netted_run))
        _run(build_going_concern_screen, str(tb), netted_run)
        net_cash = _load(netted_run, "going_concern_screen.json")["computed"]["cash_and_bank"]
        assert net_cash == pytest.approx(7_000_000.0)


class TestCaroIndicators:
    def test_downgrades_to_information_request_when_applicability_unconfirmed(self, phase2_run):
        """sec 10.2's safe rule -- CARO applicability is not derivable from a TB."""
        tb, run_dir = phase2_run
        _run(build_caro_indicators, tb, run_dir)
        data = _load(run_dir, "caro_indicators.json")
        assert data["applicability_gate"]["basis"] == "unconfirmed"
        ratings = {f["risk_rating"] for f in data["finding_records"]}
        assert ratings <= {"information_request"}, ratings

    def test_rates_medium_once_applicability_is_confirmed(self, phase2_run):
        tb, run_dir = phase2_run
        (Path(run_dir) / "engagement_context.json").write_text(json.dumps({
            "applicability_gates": {"caro": {"applicable": True, "basis": "supplied"}}
        }), encoding="utf-8")
        _run(build_caro_indicators, tb, run_dir)
        data = _load(run_dir, "caro_indicators.json")
        assert data["applicability_gate"]["caro_applicable"] is True
        assert {f["risk_rating"] for f in data["finding_records"]} <= {"medium"}

    def test_clause_reference_is_a_hint_not_a_determination(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_caro_indicators, tb, run_dir)
        data = _load(run_dir, "caro_indicators.json")
        assert "MUST NOT conclude" in data["safe_rule"]
        for f in data["finding_records"]:
            assert "clause_hint" in f


class TestOverrideIndicators:
    def test_flags_non_nil_suspense(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_override_indicators, tb, run_dir)
        indicators = {f["indicator"] for f in _load(run_dir, "override_indicators.json")["finding_records"]}
        assert "non_nil_suspense" in indicators

    def test_reads_round_sums_from_the_anomaly_scanner(self, phase2_run):
        """Round-number and Benford detection already exist; recomputing them here
        would let the two tools disagree."""
        tb, run_dir = phase2_run
        _run(build_override_indicators, tb, run_dir)
        rounds = [f for f in _load(run_dir, "override_indicators.json")["finding_records"]
                  if f["indicator"] == "round_sum_balances"]
        assert rounds and rounds[0]["source_rule"] == "TB-023"

    def test_never_states_fraud_occurred(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_override_indicators, tb, run_dir)
        data = _load(run_dir, "override_indicators.json")
        assert "not a statement that fraud" in data["planning_only"]
        blob = json.dumps(data["finding_records"]).lower()
        for phrase in ("fraud has occurred", "is fraudulent", "management committed"):
            assert phrase not in blob, f"conclusion-shaped phrase leaked: {phrase}"

    def test_degrades_when_anomaly_findings_absent(self, phase2_run, tmp_path):
        tb, run_dir = phase2_run
        (Path(run_dir) / "anomaly_findings.json").unlink()
        result = _run(build_override_indicators, tb, run_dir)
        assert any("anomaly_findings.json not available" in w for w in result.get("warnings", []))


class TestStatutoryDuesPopulationLabeling:
    """Wave 2 Fix 2b: statutory dues are independently aggregated in 5 places across
    this codebase -- every population must name the other four so a reviewer never
    mistakes one screen's total for the only "statutory dues" figure."""

    def test_statutory_screen_results_carry_population_basis(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_statutory_screen, tb, run_dir)
        data = _load(run_dir, "statutory_screen.json")
        assert all(r.get("population_basis") for r in data["results"])

    def test_going_concern_screen_carries_population_basis(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_going_concern_screen, tb, run_dir)
        data = _load(run_dir, "going_concern_screen.json")
        assert data.get("statutory_dues_population_basis")

    def test_caro_indicators_carries_population_basis(self, phase2_run):
        tb, run_dir = phase2_run
        _run(build_caro_indicators, tb, run_dir)
        data = _load(run_dir, "caro_indicators.json")
        assert data.get("statutory_dues_population_basis")
