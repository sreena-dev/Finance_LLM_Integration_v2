"""
xbrl_health_concepts.py — Concept registry for Block 4: Financial Health Summary.

Declares all canonical XBRL concepts required for:
1. Business Type & Principal Activities (NameOfMainProductOrService, NIC codes, Turnover %)
2. Financial Structure (Asset mix, CWIP components, investment book, working capital, funding)
3. Performance Decomposition (Profit movement, revenue drivers, depletion, provisions, taxes, OCF)

Verified against live as_db filings.
"""
from __future__ import annotations
from dataclasses import dataclass

# --- 1. BUSINESS TYPE & ACTIVITY CONCEPTS ------------------------------------
BUSINESS_DISCLOSURE_CONCEPTS: tuple[str, ...] = (
    "NameOfMainProductOrService",
    "NICCodeOfProductOrService",
    "DescriptionOfProductOrService",
    "DescriptionOfPrincipalBusinessActivitiesOfCompany",
    "DisclosureOfChangeInNatureOfBusinessExplanatory",
    "DisclosureInBoardOfDirectorsReportExplanatory",
    "LevelOfRoundingUsedInFinancialStatements",
)

BUSINESS_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "PercentageToTotalTurnoverOfCompany",
)


# --- 2. STRUCTURE CONCEPTS (Balance Sheet instant context) --------------------
ASSET_COMPOSITION_CONCEPTS: tuple[str, ...] = (
    "Assets",
    "PropertyPlantAndEquipment",
    "ProducingProperties",
    "CapitalWorkInProgress",
    "FacilitiesInProgress",
    "ExploratoryWellsInProgress",
    "DevelopmentWellsInProgress",
    "Investments",
    "CurrentInvestments",
    "NoncurrentInvestments",
    "OtherNoncurrentAssets",
)

LIQUIDITY_CONCEPTS: tuple[str, ...] = (
    "CurrentAssets",
    "CurrentLiabilities",
    "CashAndCashEquivalents",
    "BankBalanceOtherThanCashAndCashEquivalents",
    "TradeReceivablesCurrent",
    "TradePayablesCurrent",
)

FUNDING_CONCEPTS: tuple[str, ...] = (
    "Equity",
    "EquityShareCapital",
    "OtherEquity",
    "BorrowingsCurrent",
    "BorrowingsNoncurrent",
)


# --- 3. PERFORMANCE DECOMPOSITION CONCEPTS (P&L & Cash Flow duration) ----------
PERFORMANCE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromOperations",
    "ProfitLossForPeriod",
    "ProfitBeforeTax",
    "DepreciationDepletionAndAmortisationExpense",
    "ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",
    "CostOfMaterialsConsumed",
    "EmployeeBenefitsExpense",
    "FinanceCosts",
    "ProvisionsCurrent",
    "ProvisionsNoncurrent",
    "TaxExpense",
    "CurrentTaxExpense",
    "DeferredTaxExpense",
    "CashFlowsFromUsedInOperatingActivities",
)

EXPLANATORY_DISCLOSURE_CONCEPTS: tuple[str, ...] = (
    "DisclosureOfRatiosExplanatory",
    "NotesToFinancialStatementsExplanatory",
    "DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory",
    "DescriptionOfAccountingPolicyForExplorationAndEvaluationExpendituresExplanatory",
)


def all_health_numeric_concepts() -> tuple[str, ...]:
    """All numerical concepts to fetch from financial_facts (has_dimensions = false)."""
    return tuple(dict.fromkeys(
        ASSET_COMPOSITION_CONCEPTS
        + LIQUIDITY_CONCEPTS
        + FUNDING_CONCEPTS
        + PERFORMANCE_CONCEPTS
    ))


def all_health_disclosure_concepts() -> tuple[str, ...]:
    """All qualitative text concepts to fetch from disclosures."""
    return tuple(dict.fromkeys(
        BUSINESS_DISCLOSURE_CONCEPTS
        + EXPLANATORY_DISCLOSURE_CONCEPTS
    ))
