"""
xbrl_trend_thresholds.py — Centralized Threshold & Calibration Registry for Block 5.

Maintains all diagnostic cutoffs, drift tolerances, pacing boundaries, and DuPont
sensitivity limits for Block 5 (Key Trends & Structural Drift) as specified in the
FDR Audit Planning Intelligence Specification v3.0 (Sections 7, 8, 9).

Separating these thresholds ensures:
1. Zero hardcoded numerical tolerances in analytical logic or SQL routines.
2. Direct traceability to FDR Spec v3.0 calibration standards.
3. Easy hermetic test override and programmatic calibration auditing.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class TrendThresholds:
    # --- Common-Size Structural Drift (Spec §7) ---
    # Percentage point (pp) drift across the multi-period window
    NON_CURRENT_OTHER_DRIFT_THRESHOLD: float = 0.03   # 3 pp drift in Other Non-Current Assets / Assets
    CWIP_DRIFT_THRESHOLD: float = 0.05               # 5 pp drift in CWIP / Assets
    PPE_DRIFT_THRESHOLD: float = 0.05                # 5 pp drift in PPE / Assets
    INVESTMENTS_DRIFT_THRESHOLD: float = 0.05        # 5 pp drift in Investments / Assets
    BORROWINGS_DRIFT_THRESHOLD: float = 0.05         # 5 pp drift in Borrowings / Assets
    EQUITY_DRIFT_THRESHOLD: float = 0.05             # 5 pp drift in Net Worth / Assets

    # --- Archetype 1: Receivables Divergence (Spec §7, §8, Signal S05) ---
    RECEIVABLE_PACING_TOLERANCE: float = 0.10        # Receivables growth outpacing revenue by > 10%
    TURNOVER_DROP_TOLERANCE: float = -0.05           # Drop in receivables turnover ratio by > 5%

    # --- Archetype 2: Liquidity Mix & Placement Shift (Spec §7) ---
    LIQUIDITY_MIX_SHIFT_THRESHOLD: float = 0.25      # 25% relative swing in other bank vs current financial

    # --- Archetype 3: Provisions & Estimate Behaviour (Spec §9.3, Signal S16) ---
    PROVISION_VOLATILITY_THRESHOLD: float = 0.25     # 25% YoY change in provision/impairment balance

    # --- Archetype 4: Coverage & Solvency Direction Drift (Spec §8.1, Signal S15) ---
    COVERAGE_DRIFT_THRESHOLD: float = -0.20          # 20% or greater decline in DSCR or Interest Coverage
    FINANCE_COST_DIVERGENCE_THRESHOLD: float = 0.15  # Borrowings up >15% while finance costs flat or falling

    # --- Working Capital & Payables Funding (Spec §7, Signal S02) ---
    PAYABLES_GROWTH_TOLERANCE: float = 0.15          # Payables growth outpacing revenue/cost by > 15%

    # --- Quality of Earnings & Accruals (Spec §9.1, Signal S06) ---
    ACCRUALS_RATIO_ALERT: float = 0.10               # Accruals ratio (PAT - OCF) / Total Assets > 10%
    OCF_PAT_DIVERGENCE_RATIO: float = 0.50           # OCF < 50% of PAT for profitable entities

    # --- Extended DuPont Leverage Dominance (Spec §8.2, Signal S13) ---
    DUPONT_LEVERAGE_DOMINANCE: float = 0.50          # > 50% of ROE delta driven by equity multiplier
    MIN_MEANINGFUL_ROE_DELTA: float = 0.01           # Minimum 1% ROE change to run attribution

    # --- Depreciation Drift (Spec §9.2, Signal S11) ---
    DEPRECIATION_DRIFT_THRESHOLD: float = 0.15       # PPE growth outpacing depreciation expense by > 15%


# Default global instance
THRESHOLDS = TrendThresholds()

