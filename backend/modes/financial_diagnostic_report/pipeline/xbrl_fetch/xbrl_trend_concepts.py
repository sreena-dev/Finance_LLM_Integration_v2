"""
xbrl_trend_concepts.py — Canonical Concepts and Signal Registry for Block 5.

Implements FDR Audit Planning Intelligence Specification v3.0:
- Layer 2: Financial Structure & Structural Drift (§7)
- Layer 3: Performance Decomposition & Attribution (§8)
- Layer 4: Financial Quality Trends (§9)
- Statistical Series Constraints (§9.5)
- Output Architecture Row 5 (§14.1)

Picks up all diagnostic cutoffs and pacing limits strictly from `xbrl_trend_thresholds.py`.
"""
from __future__ import annotations
from typing import Literal
from .xbrl_trend_thresholds import THRESHOLDS, TrendThresholds

# Canonical Signal IDs for Block 5
SIGNAL_IDS = (
    "S01",  # Cash-conversion cycle / working capital drag
    "S02",  # Payables funding growth
    "S04",  # Operating cash flow vs profit divergence
    "S05",  # Receivables outpacing revenue (Archetype 1)
    "S06",  # Accruals-heavy earnings / high accruals ratio
    "S09",  # CWIP accumulation / drift
    "S10",  # Non-current other drift
    "S11",  # Implied depreciation drift
    "S13",  # Leverage-driven ROE (DuPont decomposition)
    "S14",  # Short-term funding of long-term assets
    "S15",  # Coverage / borrowing divergence (Archetype 4)
    "S16",  # Provision volatility / estimate behaviour (Archetype 3)
    "S17",  # Liquidity mix / placement shift (Archetype 2)
)

SIGNAL_TITLES: dict[str, str] = {
    "S01": "Working Capital Drag & Cash-Conversion Lengthening",
    "S02": "Payables Funding Operational Growth",
    "S04": "Operating Cash Flow Decoupling from Reported Profits",
    "S05": "Receivables Outpacing Revenue & Turnover Contraction",
    "S06": "Accruals-Heavy Earnings Quality Deficit",
    "S09": "Capital Work-in-Progress Accumulation & Drift",
    "S10": "Non-Current Other Asset Expansion Drift",
    "S11": "Asset-Depreciation Decoupling Drift",
    "S13": "Leverage-Driven Return on Equity (DuPont Distortion)",
    "S14": "Short-Term Funding of Long-Term Asset Base",
    "S15": "Debt Service Coverage Directional Contraction",
    "S16": "Provision Volatility & Estimate Behaviour Shifts",
    "S17": "Liquidity Mix Shift & Financial Asset Placement Swings",
}

# The 4 Core Trend Archetype mappings
ARCHETYPE_SIGNALS: dict[str, str] = {
    "receivables_divergence": "S05",
    "liquidity_mix_shift": "S17",
    "provisions_volatility": "S16",
    "coverage_direction_drift": "S15",
}

# Substantive non-applicability reasons when single period or clean
CLEAN_SIGNAL_REASONS: dict[str, str] = {
    "S01": "Cash conversion cycle and working capital days remain stable across periods.",
    "S02": "Trade payables expansion is aligned with material consumption and inventory additions.",
    "S04": "Operating cash flow reliably tracks or exceeds operating profitability.",
    "S05": "Receivables growth is matched by revenue expansion; debtor turnover remains resilient.",
    "S06": "Accruals ratio is within acceptable bounds; reported earnings are cash-backed.",
    "S09": "CWIP capitalisation rate is normal; no disproportionate project accumulation detected.",
    "S10": "Other non-current assets remain a modest and stable proportion of total balance sheet.",
    "S11": "Depreciation expense growth is proportional to gross block fixed asset additions.",
    "S13": "Return on equity movements are primarily driven by operating margin or asset turnover.",
    "S14": "Long-term fixed and capital assets are fully funded by long-term debt and equity.",
    "S15": "Debt service coverage ratio direction remains stable or improving relative to EBIT.",
    "S16": "Provision and impairment balances reflect steady operational run-rate without sharp spikes.",
    "S17": "Cash and other current financial asset placements exhibit steady operational liquidity mix.",
}

SINGLE_PERIOD_DISCLOSURE = (
    "Entity in initial reporting period; multi-year trend and structural drift analysis "
    "requires at least two comparable periods."
)

# ---------------------------------------------------------------------------
# Numerical Concept Mappings from MCA Ind-AS Taxonomy
# ---------------------------------------------------------------------------

TREND_BALANCE_SHEET_CONCEPTS: tuple[str, ...] = (
    "Assets",
    "CurrentAssets",
    "NoncurrentAssets",
    "PropertyPlantAndEquipment",
    "CapitalWorkInProgress",
    "NoncurrentInvestments",
    "CurrentInvestments",
    "Investments",
    "Inventories",
    "TradeReceivablesCurrent",
    "TradeReceivablesNoncurrent",
    "CashAndCashEquivalents",
    "BankBalanceOtherThanCashAndCashEquivalents",
    "OtherCurrentFinancialAssets",
    "OtherNoncurrentFinancialAssets",
    "OtherNoncurrentAssets",
    "OtherCurrentAssets",
    "Equity",
    "EquityShareCapital",
    "OtherEquity",
    "BorrowingsNoncurrent",
    "BorrowingsCurrent",
    "TradePayablesCurrent",
    "TradePayablesNoncurrent",
    "OtherCurrentLiabilities",
    "OtherNoncurrentLiabilities",
    "ProvisionsCurrent",
    "ProvisionsNoncurrent",
)

TREND_PL_CONCEPTS: tuple[str, ...] = (
    "RevenueFromOperations",
    "OtherIncome",
    "CostOfMaterialsConsumed",
    "EmployeeBenefitsExpense",
    "FinanceCosts",
    "DepreciationDepletionAndAmortisationExpense",
    "OtherExpenses",
    "ProfitBeforeTax",
    "TaxExpense",
    "ProfitLossForPeriod",
)

TREND_CASH_FLOW_CONCEPTS: tuple[str, ...] = (
    "CashFlowsFromUsedInOperatingActivities",
    "CashFlowsFromUsedInInvestingActivities",
    "CashFlowsFromUsedInFinancingActivities",
    "ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",
)

TREND_SCHEDULE_III_RATIOS: tuple[str, ...] = (
    "TradeReceivablesTurnoverRatio",
    "DebtServiceCoverageRatio",
    "InterestCoverageRatio",
    "CurrentRatio",
    "DebtEquityRatio",
    "ReturnOnEquityRatio",
    "InventoryTurnoverRatio",
    "TradePayablesTurnoverRatio",
    "NetProfitRatio",
    "ReturnOnCapitalEmployed",
)

ALL_TREND_CONCEPTS: tuple[str, ...] = tuple(dict.fromkeys(
    TREND_BALANCE_SHEET_CONCEPTS
    + TREND_PL_CONCEPTS
    + TREND_CASH_FLOW_CONCEPTS
    + TREND_SCHEDULE_III_RATIOS
))


def all_trend_concepts() -> tuple[str, ...]:
    """Returns deduplicated tuple of all canonical concepts required for Block 5."""
    return ALL_TREND_CONCEPTS

