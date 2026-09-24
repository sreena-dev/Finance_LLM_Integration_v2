"""
Hermetic test suite for Block 3: Business Profile.
Runs 100% offline with zero network and zero database dependencies.
"""
from __future__ import annotations
import pytest

from . import xbrl_profile_concepts as C
from . import xbrl_profile_prompt as P
from .xbrl_business_profile import (
    extract_grounded_figures,
    assemble_evidence_passages,
    lint_business_profile,
    build_business_profile,
    GroundedFigure,
)


def _fact(concept: str, val: float, fy_start=None, fy_end="2023-03-31") -> dict:
    return {
        "concept_name": concept,
        "value_numeric": val,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "unit": "INR",
    }


def _disc(concept: str, text: str, ctx="D2023") -> dict:
    return {
        "concept_name": concept,
        "context_ref": ctx,
        "fy_start": "2022-04-01",
        "fy_end": "2023-03-31",
        "text": text,
        "disclosure_category": "Notes",
    }


def test_profile_concepts_declarations():
    num_concepts = C.all_profile_numeric_concepts()
    assert len(num_concepts) == len(set(num_concepts)), "Duplicate concepts in numeric registry"
    assert "Equity" in num_concepts
    assert "BorrowingsCurrent" in num_concepts
    assert "BorrowingsNoncurrent" in num_concepts
    assert "RevenueFromOperations" in num_concepts
    assert "ContingentLiabilities" in num_concepts

    disc_concepts = C.all_profile_disclosure_concepts()
    assert len(disc_concepts) == len(set(disc_concepts)), "Duplicate concepts in disclosure registry"
    # Every concept below is verified present in as_db (see xbrl_profile_concepts.py's
    # module docstring) — this replaces an earlier version of this test that asserted
    # two concepts ("DescriptionOfNatureOfOperations",
    # "DescriptionOfAccountingPolicyForRevenueRecognitionTextBlock") which do not
    # exist anywhere in the corpus under those names.
    assert "DisclosureInBoardOfDirectorsReportExplanatory" in disc_concepts
    assert "DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory" in disc_concepts
    assert "ContingentLiabilities" not in disc_concepts, (
        "ContingentLiabilities is a financial_facts concept, not a disclosures one")


def test_extract_grounded_figures_golden():
    # Golden figures mimicking petroleum / energy PSU (in INR millions/units)
    rows = [
        _fact("Equity", 3317704400000.0),
        _fact("BorrowingsCurrent", 28234000000.0),
        _fact("BorrowingsNoncurrent", 50000000000.0),
        _fact("DepreciationDepletionAndAmortisationExpense", 251338230000.0, "2022-04-01"),
        _fact("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", 4451000.0, "2022-04-01"),
        _fact("CurrentInvestments", 333786380000.0),
        _fact("NoncurrentInvestments", 800000000000.0),
        _fact("RevenueFromOperations", 6848290000000.0, "2022-04-01"),
        _fact("ContingentLiabilities", 12000000000.0),
    ]

    figures, lookup = extract_grounded_figures(rows)
    assert lookup["equity"] == 3317704400000.0
    assert lookup["borrowings"] == 78234000000.0
    assert abs(lookup["debt_equity"] - (78234000000.0 / 3317704400000.0)) < 1e-4
    assert lookup["depreciation"] == 251338230000.0
    assert lookup["impairment"] == 4451000.0
    assert lookup["investments"] == 1133786380000.0
    assert lookup["revenue"] == 6848290000000.0
    assert lookup["contingent_liabilities"] == 12000000000.0

    labels = {f.label: f for f in figures}
    assert "Total equity" in labels
    assert "Total borrowings" in labels
    assert "Debt-equity ratio" in labels
    assert "Depreciation, depletion and amortisation" in labels
    assert "Investment book (current + non-current)" in labels
    assert "Contingent liabilities" in labels


def test_impairment_reversal_worded_as_reversal_not_a_fresh_loss():
    """A negative impairment value is a net REVERSAL of a prior charge, not a new
    loss — the deterministic text must say so rather than presenting a credit as
    if it were a debit."""
    metric_rows = [
        _fact("Equity", 1000.0),
        _fact("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", -50.0, "2022-04-01"),
    ]
    payload = build_business_profile(
        doc_id="d", company_name="X", cin="Y", fy_label="FY2024-25",
        metric_rows=metric_rows, disclosure_rows=[], use_llm=False,
    )
    cost_text = next(f["text"] for f in payload["fields"] if f["key"] == "cost")
    assert "reversal" in cost_text.lower()


def test_assemble_evidence_passages():
    disclosures = [
        _disc(
            "DescriptionOfNatureOfOperations",
            "The Company is engaged in exploration and production of crude oil and natural gas onshore and offshore."
        ),
        _disc(
            "DescriptionOfAccountingPolicyForRevenueRecognitionTextBlock",
            "Crude oil and gas sales under administered pricing. Gas is subject to floor/ceiling prices (Note 30)."
        ),
    ]

    passages_block, citations = assemble_evidence_passages(disclosures)
    assert "exploration and production of crude oil" in passages_block
    assert "(Note 30)" in passages_block
    assert len(citations) == 2
    assert citations[0]["concept"] == "DescriptionOfNatureOfOperations"


def test_safe_language_lint():
    bad_fields = {
        "model": "This proves the company operates in oil and gas.",
        "inherent_risk_map": "The entity is fraudulent and will fail in debt repayment.",
    }
    dummy_figures = []
    cleaned = lint_business_profile(bad_fields, dummy_figures)

    assert "proves" not in cleaned["model"].lower()
    assert "is fraudulent" not in cleaned["inherent_risk_map"].lower()
    assert "will fail" not in cleaned["inherent_risk_map"].lower()


def test_build_business_profile_hermetic_pipeline():
    metric_rows = [
        _fact("Equity", 3317704400000.0),
        _fact("BorrowingsCurrent", 28234000000.0),
        _fact("BorrowingsNoncurrent", 50000000000.0),
        _fact("RevenueFromOperations", 6848290000000.0, "2022-04-01"),
        _fact("DepreciationDepletionAndAmortisationExpense", 251338230000.0, "2022-04-01"),
        _fact("CurrentInvestments", 1133786380000.0),
    ]
    disc_rows = [
        _disc(
            "DescriptionOfNatureOfOperations",
            "Integrated petroleum upstream exploration and production company."
        ),
        _disc(
            "DescriptionOfAccountingPolicyForRevenueRecognitionTextBlock",
            "Revenue recognized based on notified gas prices under Note 30."
        ),
    ]

    payload = build_business_profile(
        doc_id="test_doc_001.xml",
        company_name="OIL & NATURAL GAS CORP LTD",
        cin="L74899DL1993GOI054155",
        fy_label="FY2022-23",
        metric_rows=metric_rows,
        disclosure_rows=disc_rows,
        use_llm=False,  # Test deterministic pipeline
    )

    assert payload["formed"] is True
    assert payload["doc_id"] == "test_doc_001.xml"
    assert payload["company_name"] == "OIL & NATURAL GAS CORP LTD"
    assert len(payload["fields"]) == 6

    keys = [f["key"] for f in payload["fields"]]
    assert keys == ["model", "revenue", "cost", "financing", "value_drivers", "inherent_risk_map"]

    for f in payload["fields"]:
        assert len(f["text"]) > 20, f"Field {f['key']} text too short"

    # Verify figures are in grounded payload
    grounded_labels = [g["label"] for g in payload["grounded_figures"]]
    assert "Total equity" in grounded_labels
    assert "Total borrowings" in grounded_labels


def test_use_llm_true_with_no_chat_fn_degrades_honestly_not_a_crash():
    """use_llm=True with no chat_fn supplied must not raise, must still return all
    6 fields from the deterministic path, and must explain why in plain language —
    not a bare Python exception class name (the bug this replaced)."""
    metric_rows = [_fact("Equity", 1000.0), _fact("RevenueFromOperations", 500.0, "2022-04-01")]
    payload = build_business_profile(
        doc_id="d", company_name="X", cin="Y", fy_label="FY2024-25",
        metric_rows=metric_rows, disclosure_rows=[], use_llm=True, chat_fn=None,
    )
    assert payload["formed"] is True
    assert len(payload["fields"]) == 6
    assert "Error" not in payload["reason"]
    assert payload["reason"]


def test_use_llm_true_with_failing_chat_fn_degrades_honestly():
    """A chat_fn that raises must be caught, must not surface its exception class
    name to the reader, and must still return a complete deterministic profile."""
    def _boom(*a, **k):
        raise RuntimeError("endpoint unreachable")

    metric_rows = [_fact("Equity", 1000.0), _fact("RevenueFromOperations", 500.0, "2022-04-01")]
    payload = build_business_profile(
        doc_id="d", company_name="X", cin="Y", fy_label="FY2024-25",
        metric_rows=metric_rows, disclosure_rows=[], use_llm=True, chat_fn=_boom,
    )
    assert payload["formed"] is True
    assert len(payload["fields"]) == 6
    assert "RuntimeError" not in payload["reason"]


def test_format_inr_adaptive_scaling():
    from .xbrl_business_profile import _format_inr
    assert _format_inr(None) == "—"
    assert _format_inr(0) == "₹0.00"
    assert _format_inr(50_000) == "₹0.50 lakh"
    assert _format_inr(4_500_000) == "₹45.00 lakh"
    assert _format_inr(9_900_000) == "₹99.00 lakh"
    assert _format_inr(10_000_000) == "₹1.00 cr"
    assert _format_inr(512_681_000) == "₹51.27 cr"
    assert _format_inr(-150_000) == "-₹1.50 lakh"
    assert _format_inr(-25_000_000) == "-₹2.50 cr"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

