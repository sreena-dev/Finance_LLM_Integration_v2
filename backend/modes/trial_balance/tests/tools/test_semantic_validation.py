"""Tests for semantic_validation.py's contradiction rules -- the gate that blocks a
structurally-valid but semantically-wrong taxonomy match. TB-R23 adds
_bank_cash_vs_income_conflict: the rule that would have caught the EPIL GL 20950021
defect (a bank ledger, "SBI- MUSCAT (US$) -R", resolving to an Income node) even if
taxonomy_resolver.py's own query-construction fix were ever imperfect for some other
account -- none of the four pre-existing rules cover a bank/cash-named account
resolving to Income/Expense."""

from modes.trial_balance.pipeline.tools.semantic_validation import validate_semantic_evidence


def _node(account_type, main_head="Revenue from operations", sub_head_1="Other operating revenues", sub_head_2=""):
    return {"main_head": main_head, "sub_head_1": sub_head_1, "sub_head_2": sub_head_2, "account_type": account_type}


def test_bank_named_account_resolving_to_income_is_rejected():
    assert validate_semantic_evidence("SBI- MUSCAT (US$) -R", _node("Income")) is False


def test_bank_abbreviation_resolving_to_income_is_rejected():
    # "IOB" (Indian Overseas Bank) does not literally contain "bank" -- confirms the
    # abbreviation match, not just the literal "cash"/"bank" substring check, is wired in.
    assert validate_semantic_evidence("IOB 040802000002289", _node("Income")) is False


def test_cash_named_account_resolving_to_expense_is_rejected():
    assert validate_semantic_evidence("Petty Cash Imprest", _node("Expense", main_head="Other expenses")) is False


def test_bank_named_account_resolving_to_asset_is_accepted():
    # The correct classification for a bank ledger -- must not be rejected.
    assert validate_semantic_evidence("SBI- MUSCAT (US$) -R", _node("Asset", main_head="Cash and cash equivalents")) is True


def test_non_bank_account_resolving_to_income_is_accepted():
    assert validate_semantic_evidence("Sales - Domestic", _node("Income")) is True


def test_sibling_r_and_p_legs_both_rejected_against_income_identically():
    # The exact pair behind the EPIL defect: both legs must be treated identically by
    # this gate -- neither should be waved through just because of the -R/-P suffix.
    assert validate_semantic_evidence("SBI- MUSCAT (US$) -R", _node("Income")) is False
    assert validate_semantic_evidence("SBI- MUSCAT (US$) -P", _node("Income")) is False
