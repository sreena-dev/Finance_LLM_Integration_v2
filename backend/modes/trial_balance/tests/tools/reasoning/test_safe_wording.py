"""Tests for backend/tools/reasoning/_safe_wording.py -- the deterministic
guardrail filter behind Requirement #8. Pure function, no I/O: every banned
phrase must be substituted, the mandatory disclaimer must be attachable (or
not) on demand, and -- the regression this suite exists to pin down -- the
filter must never mangle its own safe-replacement text."""

import pytest

from modes.trial_balance.pipeline.tools import SAFE_WORDING_DISCLAIMER, apply_safe_wording


@pytest.mark.parametrize(
    "banned_text,expected_substring",
    [
        ("The suspense balance is fraud.", "risk indicator requiring corroboration"),
        ("The suspense balance is an irregularity.", "risk indicator requiring corroboration"),
        ("This is a misstatement.", "risk indicator requiring corroboration"),
        ("This balance is not recoverable.", "recoverability cannot be concluded from TB alone"),
        ("This balance is recoverable.", "recoverability cannot be concluded from TB alone"),
        ("The company did not comply with GST rules.", "requiring compliance reconciliation and evidence"),
        ("The company complied with statutory dues.", "requiring compliance reconciliation and evidence"),
        ("The accounts are true and fair.", "no opinion or certification is expressed"),
        ("CARO clause is applicable here.", "possible CARO-related indicator"),
        ("CARO clause is violated here.", "possible CARO-related indicator"),
        ("Related party identified in the ledger.", "possible related-party indicator"),
    ],
)
def test_banned_phrase_is_substituted(banned_text, expected_substring):
    filtered = apply_safe_wording(banned_text, append_disclaimer=False)
    assert expected_substring in filtered
    # The banned phrase itself must not survive verbatim.
    assert banned_text.strip(".") not in filtered


@pytest.mark.parametrize(
    "text,banned_word",
    [
        ("Management confirms this balance.", "confirms"),
        ("The balance is verified against the bank statement.", "verified"),
        ("This is our opinion on the matter.", "opinion"),
        ("Our review certifies the accounts.", "certifies"),
        ("The reconciliation proves the balance is correct.", "proves"),
    ],
)
def test_assurance_words_are_stripped(text, banned_word):
    filtered = apply_safe_wording(text, append_disclaimer=False)
    assert banned_word not in filtered.lower()


def test_safe_replacement_text_is_not_self_mangled():
    """Regression test: an earlier version ran the assurance-word-stripping
    pass AFTER phrase substitution, so the filter's own safe replacement for
    'true and fair' ("no opinion or certification is expressed") had
    'opinion'/'certification' stripped right back out, producing the mangled
    'no  or  is expressed'. Order must be: strip assurance words from the
    ORIGINAL text first, then substitute banned phrases."""
    filtered = apply_safe_wording("The accounts are true and fair.", append_disclaimer=False)
    assert filtered == "The no opinion or certification is expressed."
    # The critical assertion: the safe replacement's own "opinion"/
    # "certification" words must survive, not get stripped back out.
    assert "opinion" in filtered
    assert "certification" in filtered


def test_clean_text_passes_through_unchanged():
    text = "Trade receivables are 42% of revenue."
    assert apply_safe_wording(text, append_disclaimer=False) == text


def test_disclaimer_appended_when_requested():
    filtered = apply_safe_wording("Some finding.", append_disclaimer=True)
    assert filtered.startswith("Some finding.")
    assert filtered.endswith(SAFE_WORDING_DISCLAIMER)


def test_disclaimer_not_appended_by_default_false():
    filtered = apply_safe_wording("Some finding.", append_disclaimer=False)
    assert SAFE_WORDING_DISCLAIMER not in filtered


def test_empty_text_with_disclaimer_returns_disclaimer_only():
    assert apply_safe_wording("", append_disclaimer=True) == SAFE_WORDING_DISCLAIMER


def test_empty_text_without_disclaimer_returns_empty():
    assert apply_safe_wording("", append_disclaimer=False) == ""


def test_disclaimer_wording_is_exact():
    # The disclaimer's exact wording is a compliance requirement (sourced
    # verbatim from SKILL_TB_consolidated.md) -- pin it so an accidental
    # paraphrase during a future edit is caught immediately.
    assert SAFE_WORDING_DISCLAIMER == (
        "This observation is a risk indicator from the trial balance only. It is not a conclusion "
        "on misstatement, fraud, non-compliance, irregularity, recoverability or going concern. The "
        "matter requires corroboration through the audit procedures and evidence listed."
    )
