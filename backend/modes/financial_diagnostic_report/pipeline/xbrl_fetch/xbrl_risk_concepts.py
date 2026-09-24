"""
Canonical concepts for Block 6: Key Risk Clusters with Interactions
(FDR Specification v3.0 §10, §14.1 Row 6, Appendices D, E & F).

Keeps concept registrations strictly isolated from Block 2 (Dashboard) and
Block 3 (Business Profile) per architectural non-duplication rules.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

# The 6 Canonical Risk Clusters from Spec Appendix D
CLUSTER_IDS = ("RC01", "RC02", "RC03", "RC04", "RC05", "RC06")

CLUSTER_NAMES: dict[str, str] = {
    "RC01": "Working-Capital and Liquidity Stress",
    "RC02": "Revenue and Receivable Quality",
    "RC03": "Asset and Capitalisation Risk",
    "RC04": "Funding and Solvency Risk",
    "RC05": "Estimate and Reporting-Quality Risk",
    "RC06": "Government-Dependency and Grant Risk",
}

# Canonical Assertions Threatened per Cluster (Spec Appendix D)
CLUSTER_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "RC01": ("Completeness of liabilities", "Valuation of current assets"),
    "RC02": ("Occurrence of revenue", "Cut-off", "Valuation of receivables"),
    "RC03": ("Existence of assets", "Valuation and allocation", "Capitalisation accuracy"),
    "RC04": ("Completeness of borrowings", "Classification of borrowings"),
    "RC05": ("Valuation and allocation", "Accuracy", "Classification"),
    "RC06": ("Completeness of grant disclosures", "Classification", "Condition compliance"),
}

# Likely Specialist Referral per Cluster (Spec Appendix F)
CLUSTER_SPECIALISTS: dict[str, str] = {
    "RC01": "None",
    "RC02": "Legal (for disputed or litigated receivables)",
    "RC03": "Engineering / Technical (for project milestones and CWIP)",
    "RC04": "None",
    "RC05": "Valuation specialist / Actuary (for model assumptions)",
    "RC06": "None",
}

# Substantive non-applicability reasons when a canonical cluster is not raised (Spec §10 / §14)
UNRAISED_REASONS: dict[str, str] = {
    "RC01": "No liquidity stress signals detected. Operating cash flow covers reported earnings, and current assets are sufficient to meet short-term obligations.",
    "RC02": "No revenue or trade receivable quality anomalies identified. Debtor turnover velocity is within operational bounds without period-end concentration.",
    "RC03": "No unusual capital accumulation or impairment indicators. Capital work-in-progress is immaterial relative to operating fixed assets, and investment holdings do not exhibit concentration or impairment signals.",
    "RC04": "No gearing or debt servicing distress detected. Debt-to-equity leverage remains conservative with zero default disclosures, or the capital structure is 100% equity-funded with nil borrowings.",
    "RC05": "No significant estimation or reporting-quality anomalies flagged. Provisions and other income items remain within ordinary operational variances without auditor modifications.",
    "RC06": "No abnormal government dependency or grant compliance risk identified. Grant accounting under Ind AS 20 complies with standard recognition criteria without unfulfilled conditions.",
}


# Numerical Concept Mappings from MCA Ind-AS Taxonomy
# ---------------------------------------------------------------------------

# 1. Working Capital & Liquidity Concepts (RC01)
LIQUIDITY_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "CurrentAssets",
    "CurrentLiabilities",
    "TradeReceivablesCurrent",
    "TradePayablesCurrent",
    "Inventories",
    "CashAndCashEquivalents",
    "BankBalanceOtherThanCashAndCashEquivalents",
    "CashFlowsFromUsedInOperatingActivities",
    "ProfitLossForPeriod",
)

# 2. Revenue & Receivable Concepts (RC02)
REVENUE_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "RevenueFromOperations",
    "TradeReceivablesCurrent",
    "TradeReceivablesNoncurrent",
    "CashFlowsFromUsedInOperatingActivities",
    "ProfitLossForPeriod",
)

# 3. Asset & Capitalisation Concepts (RC03)
ASSET_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "Assets",
    "CapitalWorkInProgress",
    "PropertyPlantAndEquipment",
    "NoncurrentInvestments",
    "CurrentInvestments",
    "Investments",
    "OtherNoncurrentAssets",
    "DepreciationDepletionAndAmortisationExpense",
    "ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",
)

# 4. Funding & Solvency Concepts (RC04)
SOLVENCY_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "Equity",
    "BorrowingsCurrent",
    "BorrowingsNoncurrent",
    "FinanceCosts",
)

# 5. Estimate & Reporting Quality Concepts (RC05)
ESTIMATE_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "OtherIncome",
    "ProvisionsCurrent",
    "ProvisionsNoncurrent",
    "ProfitBeforeTax",
)

# 6. Government Dependency & Grant Concepts (RC06)
GOVERNMENT_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "GovernmentGrants",
    "Subsidies",
    "DeferredGovernmentGrantsNoncurrent",
    "BalancesWithGovernmentAuthorities",
)

# Union of all numeric concepts required for Block 6
ALL_RISK_NUMERIC_CONCEPTS: tuple[str, ...] = tuple(dict.fromkeys(
    LIQUIDITY_NUMERIC_CONCEPTS
    + REVENUE_NUMERIC_CONCEPTS
    + ASSET_NUMERIC_CONCEPTS
    + SOLVENCY_NUMERIC_CONCEPTS
    + ESTIMATE_NUMERIC_CONCEPTS
    + GOVERNMENT_NUMERIC_CONCEPTS
))

# Narrative Disclosure Concepts from MCA Ind-AS Taxonomy
# ---------------------------------------------------------------------------
RISK_DISCLOSURE_CONCEPTS: tuple[str, ...] = (
    "SignificantAccountingJudgementsAndEstimatesTextBlock",
    "DisclosureInAuditorsReportRelatingToMaintenanceOfCostRecords",
    "AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReport",
    "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
    "DirectorsCommentOnAuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
    "WhetherAuditorsReportHasBeenQualifiedOrHasAnyReservationsOrContainsAdverseRemarks",
    "SecretarialQualificationsOrObservationsOrOtherRemarksInSecretarialAuditReport",
    "CompanySecretaryQualificationOrObservationOrOtherRemarksInSecretarialAuditReport",
    "DirectorsCommentOnCompanySecretaryQualificationOrObservationOrOtherRemarksInSecretarialAuditReport",
    "DisclosureInAuditorsReportExplanatory",
    "DisclosureInAuditorsReportRelatingToFraudByTheCompanyOrOnTheCompanyByItsOfficersOrItsEmployeesReportedDuringPeriod",
    "DisclosureInAuditorsReportRelatingToDefaultInRepaymentOfFinancialDues",
    "DisclosureInBoardOfDirectorsReportExplanatory",
    "DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory",
    "DetailsOfSignificantAndMaterialOrdersPassedByRegulatorsOrCourtsOrTribunalsImpactingGoingConcernStatusAndCompanysOperationsInFutureExplanatory",
    "ContingentLiabilities",
)


def all_risk_numeric_concepts() -> tuple[str, ...]:
    return ALL_RISK_NUMERIC_CONCEPTS


def all_risk_disclosure_concepts() -> tuple[str, ...]:
    return RISK_DISCLOSURE_CONCEPTS

