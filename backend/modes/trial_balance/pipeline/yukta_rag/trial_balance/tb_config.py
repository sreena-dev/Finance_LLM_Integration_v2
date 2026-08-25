"""Centralized numeric thresholds for the TB Compare (year-over-year) pipeline and
the single-TB trend/ratio tools it shares with the analysis pipeline (spec SCR-08).

Previously scattered as independent module-level magic numbers across
tb_compare.py and tb_tools.py — notably two DIFFERENT thresholds
(10% vs 20%) doing the same conceptual "flag a swing" job in two files of the
same pipeline, with no shared source. Centralizing here makes that visible and
intentional rather than accidental; the two values are NOT unified into one —
that would be a real behavior change requiring an explicit decision, not a
side-effect of a refactor.
"""

from __future__ import annotations

# tb_compare.py — year-over-year comparison
COMPARE_SWING_PCT_THRESHOLD = 10.0        # flag an account movement beyond +/- this %
LIABILITY_RISE_PCT_TRIGGER = 10.0         # category-level "higher leverage" risk trigger
EXPENSE_GROWTH_PCT_TRIGGER = 25.0         # category-level "cost growth" risk trigger
INCOME_DROP_PCT_TRIGGER = 10.0            # category-level "income fell" risk trigger
MATERIAL_NEW_BORROWING_FLOOR = 1e9        # size floor for a "significant new borrowing" flag
DIFFERENT_ENTITY_WARNING_RATIO = 0.7      # (new+dropped)/total accounts above this -> caution banner
# below this, value-weighted classification coverage across both compared periods
# is too thin to trust the category-movement figures — same convention as the
# audit pipeline's VALUE_COVERAGE_WARN_THRESHOLD (audit_config.py), kept as its
# own named constant since these are independent pipelines.
COMPARE_VALUE_COVERAGE_WARN_THRESHOLD = 85.0

# tb_tools.py — single-TB trend variance (conceptually the same "flag a swing"
# job as COMPARE_SWING_PCT_THRESHOLD above, kept as its own named constant —
# see module docstring)
VARIANCE_PCT_THRESHOLD = 20.0

# tb_tools.py — ratio-based risk comfort bands
RATIO_RISK_THRESHOLDS = {
    "current_ratio_min": 1.0, "quick_ratio_min": 1.0, "debt_to_equity_max": 2.0,
}
