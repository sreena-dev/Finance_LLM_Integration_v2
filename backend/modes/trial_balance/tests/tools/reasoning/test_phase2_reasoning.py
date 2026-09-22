"""Tests for the Phase-2 reasoning tools: the finding-record shape, the account-area
assertion/evidence map, the consolidated request lists, and the run log.

These four are where the specification's output contract actually lands, so the tests
below check the CONTRACT rather than just that the tools run: that a finding carries
observation/expectation/gap, that evidence names a real record instead of a category,
that management explanation stays separate from audit evidence, and that a run can be
traced back to the rule-set versions that produced it.
"""

import json
from pathlib import Path

import pytest

from modes.trial_balance.pipeline.tools import (
    VALID_ASSERTIONS,
    VALID_RISK_BASIS,
    build_assertion_evidence_map,
    build_counterpart_screen,
    build_finding_records,
    build_public_sector_lens,
    build_request_lists,
    build_run_log,
    make_record,
    match_area,
    validate_record,
)


def _load(run_dir, name):
    return json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))


@pytest.fixture
def populated_run(phase2_run):
    """A run with two screens' findings already on disk, so the consolidating tools
    have something real to work with."""
    tb, run_dir = phase2_run
    build_counterpart_screen(canonical_tb_file=tb, output_dir=str(run_dir))
    build_public_sector_lens(canonical_tb_file=tb, output_dir=str(run_dir))
    return tb, run_dir


class TestFindingRecordShape:
    def test_make_record_produces_the_sec14_triad(self):
        r = make_record(
            source_screen="test",
            observation="Receivables are 80% of revenue.",
            expectation="Receivables reflect credit terms.",
            gap="20 points above the plausible band.",
            assertions=["Existence", "Valuation"],
            risk_basis=["relationship", "value"],
            evidence_requested=["Party-wise debtors ageing"],
        )
        assert r["observation"] and r["expectation"] and r["gap"]
        assert r["safe_limitation"]
        assert set(r["assertion"]) <= VALID_ASSERTIONS
        assert set(r["risk_basis"]) <= VALID_RISK_BASIS

    def test_narrative_fields_pass_through_the_safe_wording_filter(self):
        """A screen author must not be able to emit assurance language, even by
        accident -- the filter runs inside the constructor, not at the call site."""
        r = make_record(
            source_screen="test",
            observation="This balance is a fraud and the accounts are true and fair.",
            expectation="Nothing.",
        )
        assert "is a fraud" not in r["observation"]
        assert "true and fair" not in r["observation"]

    def test_unknown_risk_rating_falls_back_rather_than_crashing(self):
        assert make_record(source_screen="t", observation="o", expectation="e",
                           risk_rating="catastrophic")["risk_rating"] == "medium"

    def test_validate_flags_a_finding_with_no_evidence(self):
        """sec 13.1 -- a finding that names no record cannot be acted on."""
        issues = validate_record(make_record(
            source_screen="t", observation="o", expectation="e", account="1001 - Cash"))
        assert any("no evidence requested" in i for i in issues)

    def test_validate_flags_vocabulary_outside_the_spec(self):
        issues = validate_record(make_record(
            source_screen="t", observation="o", expectation="e",
            assertions=["Vibes"], risk_basis=["hunch"], evidence_requested=["Ledger"]))
        assert any("sec-14 vocabulary" in i for i in issues)
        assert any("sec-8.2 vocabulary" in i for i in issues)


class TestBuildFindingRecords:
    def test_consolidates_from_every_screen_artifact(self, populated_run):
        _, run_dir = populated_run
        result = build_finding_records(output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        data = _load(run_dir, "finding_records.json")
        artifacts = {s["artifact"] for s in data["summary"]["contributing_screens"]}
        assert {"counterpart_screen.json", "public_sector_lens.json"} <= artifacts

    def test_needs_no_registration_of_screens(self, phase2_run):
        """A new screen contributes just by writing a finding_records array -- this
        tool never enumerates which screens exist."""
        _, run_dir = phase2_run
        (Path(run_dir) / "some_future_screen.json").write_text(json.dumps({
            "finding_records": [make_record(
                source_screen="some_future_screen", observation="o", expectation="e",
                evidence_requested=["A named record"])]
        }), encoding="utf-8")
        build_finding_records(output_dir=str(run_dir))
        data = _load(run_dir, "finding_records.json")
        assert any(s["artifact"] == "some_future_screen.json"
                   for s in data["summary"]["contributing_screens"])

    def test_ranks_high_before_medium(self, populated_run):
        _, run_dir = populated_run
        build_finding_records(output_dir=str(run_dir))
        ratings = [r["risk_rating"] for r in _load(run_dir, "finding_records.json")["finding_records"]]
        rank = {"high": 3, "medium": 2, "low": 1, "information_request": 0}
        assert ratings == sorted(ratings, key=lambda r: rank[r], reverse=True)

    def test_succeeds_with_nothing_to_consolidate(self, tmp_path):
        result = build_finding_records(output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        assert "No screen findings" in result["message"]

    def test_inherits_the_run_data_sufficiency_grade(self, populated_run):
        _, run_dir = populated_run
        build_finding_records(output_dir=str(run_dir))
        assert _load(run_dir, "finding_records.json")["data_sufficiency"] == "Medium"


class TestAssertionEvidenceMap:
    def test_replaces_generic_source_engine_evidence(self, phase2_run):
        """The whole point: 'Ledgers' becomes the actual records that resolve a
        receivables assertion."""
        tb, run_dir = phase2_run
        result = build_assertion_evidence_map(output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        data = _load(run_dir, "assertion_evidence_map.json")
        assert data["summary"]["generic_evidence_replaced"] == 2

        receivable = next(m for m in data["assertion_evidence_map"] if m["gl_code"] == "1500")
        assert receivable["account_area"] == "trade_receivables"
        assert receivable["previous_generic_evidence"] == ["Ledgers"]
        joined = " ".join(receivable["evidence_requested"]).lower()
        assert "ageing" in joined and "confirmation" in joined
        assert "Ledgers" not in receivable["evidence_requested"]

    def test_grant_maps_to_sanction_and_utilisation_records(self, phase2_run):
        tb, run_dir = phase2_run
        build_assertion_evidence_map(output_dir=str(run_dir))
        grant = next(m for m in _load(run_dir, "assertion_evidence_map.json")["assertion_evidence_map"]
                     if m["gl_code"] == "5100")
        assert grant["account_area"] == "grants_subsidies"
        assert grant["regularity_flag"] is True
        joined = " ".join(grant["evidence_requested"]).lower()
        assert "sanction order" in joined and "utilisation certificate" in joined

    def test_longest_keyword_wins(self):
        """'trade receivable' must beat a bare 'receivable', or a specific area loses
        to a generic one purely on dict ordering."""
        from modes.trial_balance.pipeline.tools import load_pack
        areas = load_pack("assertions")["areas"]
        name, _ = match_area("Trade Receivable - Domestic", areas)
        assert name == "trade_receivables"

    def test_unmatched_account_is_an_information_request_not_a_forced_mapping(self):
        """sec 1.2 -- do not force a mapping for an unclear account."""
        from modes.trial_balance.pipeline.tools import load_pack
        areas = load_pack("assertions")["areas"]
        name, spec = match_area("Zzz Unknown Ledger 4471", areas)
        assert name == "unclassified"
        assert "chart of accounts" in " ".join(spec["evidence"]).lower()


class TestRequestLists:
    def test_deduplicates_and_groups_by_audit_area(self, populated_run):
        _, run_dir = populated_run
        build_finding_records(output_dir=str(run_dir))
        result = build_request_lists(output_dir=str(run_dir))
        assert result["execution_status"] == "SUCCESS"
        ev = _load(run_dir, "evidence_request_list.json")
        records = [e["record"] for e in ev["evidence_request_list"]]
        assert len(records) == len(set(records)), "same record requested twice"
        assert ev["summary"]["audit_areas"] > 1

    def test_keeps_management_queries_out_of_the_evidence_list(self, populated_run):
        """sec 13.1 -- mixing the two is how an explanation quietly becomes evidence."""
        _, run_dir = populated_run
        build_finding_records(output_dir=str(run_dir))
        build_request_lists(output_dir=str(run_dir))
        mq = _load(run_dir, "management_query_list.json")
        assert "NOT audit evidence" in mq["important"]
        assert mq["management_query_list"]
        for q in mq["management_query_list"]:
            assert q["candidate_explanations"]
            assert q["corroborating_evidence_required"]

    def test_orders_by_the_highest_risk_each_record_supports(self, populated_run):
        _, run_dir = populated_run
        build_finding_records(output_dir=str(run_dir))
        build_request_lists(output_dir=str(run_dir))
        ratings = [e["highest_risk_rating"]
                   for e in _load(run_dir, "evidence_request_list.json")["evidence_request_list"]]
        rank = {"high": 3, "medium": 2, "low": 1, "information_request": 0}
        assert ratings == sorted(ratings, key=lambda r: rank.get(r, 0), reverse=True)

    def test_warns_rather_than_failing_with_no_findings(self, tmp_path):
        result = build_request_lists(output_dir=str(tmp_path))
        assert result["execution_status"] == "SUCCESS"
        assert any("no records" in w for w in result["warnings"])


class TestRunLog:
    def test_hashes_every_artifact_and_records_rule_set_versions(self, populated_run):
        _, run_dir = populated_run
        result = build_run_log(output_dir=str(run_dir), tb_doc_id="TEST_DOC")
        assert result["execution_status"] == "SUCCESS"
        log = _load(run_dir, "run_log.json")
        assert log["artifacts"] and all(len(a["sha256"]) == 64 for a in log["artifacts"])
        packs = log["versions"]["knowledge_packs"]
        assert packs["relationships"] and not str(packs["relationships"]).startswith("UNAVAILABLE")
        assert log["versions"]["prompt_version"]

    def test_records_which_tools_actually_ran(self, populated_run):
        """Inferred from artifacts on disk, so it reports what executed rather than
        what was planned."""
        _, run_dir = populated_run
        build_run_log(output_dir=str(run_dir))
        log = _load(run_dir, "run_log.json")
        assert "build_counterpart_screen" in log["tools_run"]
        assert "build_audit_reasoning" in log["tools_not_run"]

    def test_does_not_hash_itself(self, populated_run):
        _, run_dir = populated_run
        build_run_log(output_dir=str(run_dir))
        log = _load(run_dir, "run_log.json")
        assert not any(a["file_name"] == "run_log.json" for a in log["artifacts"])

    def test_warns_when_no_source_file_hash_can_be_recorded(self, populated_run):
        """sec 16.2 requires the input file's hash; a DB-sourced run has none."""
        _, run_dir = populated_run
        result = build_run_log(output_dir=str(run_dir), tb_doc_id="DB_DOC")
        assert any("source_file_path" in w for w in result["warnings"])
