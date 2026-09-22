"""Tests for the native classification engine (normalize.py + friends) --
the port of TB_normalization_v1's resolution order and Validation Gate
invariant into TB-v2-git's own codebase.

Uses use_candidates=False throughout to avoid the DB-backed candidate
retrieval path (taxonomy_node/taxonomy_alias/taxonomy_embedding, populated
separately by backend/scripts/compile_taxonomy.py) -- these tests exercise
the LLM-call/validation-gate machinery and the pure-JSON-taxonomy paths
(EXACT/Step 2, single-shot classify, the Validation Gate itself), which
have no DB dependency.
"""

import json

from modes.trial_balance.pipeline.tools.normalize import normalize
from modes.trial_balance.pipeline.tools.tb_models import GroupingHint, ProvisionalMappedRow, TBRow
from modes.trial_balance.pipeline.tools.validation_gate import run_validation_gate


class ScriptedLLM:
    """A fake LLM client matching backend.agent.SafeVLLMClient's call
    shape (generate(messages, max_tokens=..., temperature=...) -> object
    with .content), returning pre-scripted JSON responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate(self, messages, max_tokens=None, temperature=None, tools=None, **kwargs):
        self.calls += 1
        content = self._responses.pop(0) if self._responses else "[]"

        class _Resp:
            pass

        resp = _Resp()
        resp.content = content
        return resp


def _tb_row(gl_code, gl_name, opening=0.0, debit=0.0, credit=0.0, closing=0.0):
    return TBRow(gl_code=gl_code, gl_name=gl_name, opening=opening, debit=debit, credit=credit, closing=closing)


def test_step1_no_grouping_hint_is_terminal_unmapped_no_llm():
    tb_rows = [_tb_row("1000", "Freehold Land")]
    result = normalize(tb_rows, {}, "IND_AS", llm_client=ScriptedLLM([]))
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.mapped_status == "UNMAPPED"
    assert row.trace.resolved_at_step == "no_grouping_match"


def test_step2_direct_resolution_disambiguates_ambiguous_pair_by_main_head():
    """"Trade payables" / "Total outstanding dues of micro enterprises and
    small enterprises" is one of the 17 pairs shared between Non-current
    and Current liabilities in the real IND_AS taxonomy -- Step 2 must use
    the caller-supplied main_head to pick the right one, not silently
    guess."""
    tb_rows = [_tb_row("2000", "Sundry Creditors (MSME)")]
    hints = {
        "2000": GroupingHint(
            gl_code="2000",
            known_fields={
                "main_head": "Current liabilities",
                "sub_head_1": "Trade payables",
                "sub_head_2": "Total outstanding dues of micro enterprises and small enterprises",
            },
        )
    }
    result = normalize(tb_rows, hints, "IND_AS", llm_client=ScriptedLLM([]))
    row = result.rows[0]
    assert row.mapped_status == "MAPPED"
    assert row.main_head == "Current liabilities"
    assert row.trace.resolved_at_step == "step2_direct"
    assert row.wording_source == "client_original"


def test_step3_single_shot_llm_classification_mapped():
    tb_rows = [_tb_row("3000", "Freehold Land")]
    hints = {"3000": GroupingHint(gl_code="3000", hint_text="Fixed Assets")}
    llm_response = json.dumps([{
        "gl_code": "3000", "bs_pl": "BS", "main_head": "Non-current assets",
        "sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land",
    }])
    result = normalize(tb_rows, hints, "IND_AS", llm_client=ScriptedLLM([llm_response]), use_candidates=False)
    row = result.rows[0]
    assert row.mapped_status == "MAPPED"
    assert row.sub_head_2 == "Land"
    assert row.trace.resolved_at_step == "step3_single_shot"
    assert row.wording_source == "system_canonical"


def test_step3_llm_explicit_null_is_terminal_unmatched_not_unmapped():
    """Grouping data existed (a hint was present) but classification found
    nothing plausible -- UNMATCHED, distinct from UNMAPPED (no grouping
    data at all)."""
    tb_rows = [_tb_row("3001", "Something Genuinely Unclassifiable")]
    hints = {"3001": GroupingHint(gl_code="3001", hint_text="???")}
    llm_response = json.dumps([{"gl_code": "3001", "bs_pl": None, "main_head": None, "sub_head_1": None, "sub_head_2": None}])
    result = normalize(tb_rows, hints, "IND_AS", llm_client=ScriptedLLM([llm_response]), use_candidates=False)
    row = result.rows[0]
    assert row.mapped_status == "UNMATCHED"


def test_validation_gate_never_produces_mapped_without_a_real_snap():
    """The core invariant this whole port exists to enforce: a provisional
    row whose (sub_head_1, sub_head_2) does NOT resolve to any real
    taxonomy entry must never reach mapped_status=MAPPED, even if it
    arrived "provisionally mapped" from an upstream step. Exactly one
    repair attempt is made; when that also fails, the row is UNMATCHED."""
    bogus_row = ProvisionalMappedRow(
        tb_row=_tb_row("9999", "Totally Invented Account"),
        bs_pl="BS", main_head="Not A Real Main Head", sub_head_1="Not A Real Sub Head 1",
        sub_head_2="Not A Real Sub Head 2", resolved_at_step="step2_direct",
    )
    # Repair attempt's own classify_all call also returns nothing plausible.
    repair_response = json.dumps([{"gl_code": "9999", "bs_pl": None, "main_head": None, "sub_head_1": None, "sub_head_2": None}])
    resolved = run_validation_gate([bogus_row], "IND_AS", ScriptedLLM([repair_response]), use_candidates=False)

    assert len(resolved) == 1
    assert resolved[0].mapped_status == "UNMATCHED"
    assert resolved[0].trace.repair_attempted is True
    assert resolved[0].trace.repair_succeeded is False


def test_validation_gate_repairs_a_row_via_single_shot_reclassification():
    """A provisional row that fails its own snap re-validation gets exactly
    one repair attempt -- if THAT resolves to a real taxonomy entry, the
    row IS allowed to reach MAPPED, but only through this repaired path,
    never by trusting the original (invalid) provisional fields."""
    bogus_row = ProvisionalMappedRow(
        tb_row=_tb_row("9998", "Freehold Land Mislabeled"),
        bs_pl="BS", main_head="Not Real", sub_head_1="Not Real Either", sub_head_2="Still Not Real",
        resolved_at_step="step2_direct",
    )
    repair_response = json.dumps([{
        "gl_code": "9998", "bs_pl": "BS", "main_head": "Non-current assets",
        "sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land",
    }])
    resolved = run_validation_gate([bogus_row], "IND_AS", ScriptedLLM([repair_response]), use_candidates=False)

    assert len(resolved) == 1
    assert resolved[0].mapped_status == "MAPPED"
    assert resolved[0].sub_head_2 == "Land"
    assert resolved[0].trace.repair_attempted is True
    assert resolved[0].trace.repair_succeeded is True
    assert resolved[0].trace.demoted_from_provisional_mapped is True
