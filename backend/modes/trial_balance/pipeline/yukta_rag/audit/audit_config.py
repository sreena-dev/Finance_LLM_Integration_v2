"""Centralized numeric thresholds for TB Audit mode (spec SCR-08).

Previously scattered as module-level magic numbers across audit_screen.py,
audit_normalize.py, audit_materiality.py, audit_relationships.py and
audit_gaps.py — with the presence/absence/base-sizing group duplicated verbatim
between audit_relationships.py and audit_gaps.py. Every module now imports from
here instead of defining its own copy, so thresholds can be tuned in one place
without touching engine code.
"""

from __future__ import annotations

# audit_screen.py — whole-TB screen (spec 6)
MATERIAL_FRACTION = 0.01
CONCENTRATION_PCT = 10.0
CONTRA_PAIR_REL_TOL = 0.0001
CONTRA_PAIR_MIN_ABS = 1.0
ROUND_SUM_MIN_ABS = 100_000  # a balance with >= 5 trailing zeros looks manually set
# gross offsetting-activity check (spec E3/E6): an account's own debit-side and
# credit-side movement are each large relative to its group, but the net
# movement is small relative to the group — additions/disposals cycling
# through the year rather than a genuine net change.
OFFSET_DEBIT_CREDIT_PCT = 15.0
OFFSET_NET_PCT = 3.0
# clearing/inter-unit/statistical GL-series detection (client-format rule C9):
# a GL-code-prefix group whose combined net is small relative to its own gross
# debit+credit activity behaves like an internal clearing/suspense series
# rather than a real economic balance.
CLEARING_NET_TO_GROSS_PCT = 5.0
CLEARING_MIN_GROSS_ABS = 100_000  # ignore near-zero-activity groups (noise, not clearing)
# mirror/contra PAIR detection (bug-3 fix): two clearing-flavoured accounts that
# cross-reference each other by name or share a near-identical name differing
# only by a trailing suffix. A pair "resolves" (treated as a genuine offsetting
# clearing pair) when its combined net is small relative to its own gross;
# otherwise it's excluded from totals but surfaced as an unresolved finding.
MIRROR_PAIR_RESOLVED_PCT = 2.0
# structural (name-independent) clearing-series candidate signal: a GL-prefix
# group need not match a clearing/inter-unit keyword if it is large enough and
# already behaves like a wash (near-nil net vs. its own gross) — naming
# conventions vary too much across clients to rely on keywords alone. Stricter
# minimum size than the keyword-gated path (>=2) since this signal has no
# naming corroboration at all.
CLEARING_STRUCTURAL_MIN_MEMBERS = 3

# audit_normalize.py — input quality / data-sufficiency grading (spec 3, 14)
VALUE_COVERAGE_WARN_THRESHOLD = 85.0
UNMAPPED_PCT_FLAG = 20.0
UNMAPPED_PCT_LOW_GRADE = 40.0
UNMAPPED_PCT_HIGH_GRADE = 15.0

# audit_materiality.py — provisional materiality bases (spec 8.1)
BENCHMARK_PCT = {
    "profit_before_tax": 5.0, "revenue": 1.0, "total_assets": 1.0,
    "total_expenses": 1.0, "net_worth": 2.0,
}
PBT_HEALTHY_PCT_OF_REVENUE = 0.02  # PBT base chosen only if PBT is at least this share of revenue
# heuristic-only flag (not a base-selection rule): when the revenue fallback is
# used, note if total assets dwarf revenue by this multiple — a sign the entity
# may be asset-heavy/infrastructure, where total_assets could be the more
# defensible base. Worth a manual check; not enough signal to auto-switch on.
ASSET_HEAVY_REVENUE_RATIO = 3.0

# audit_relationships.py + audit_gaps.py — shared presence/absence/base-sizing
# (previously duplicated verbatim between the two files)
ROLE_BASE_FALLBACK_PCT = 0.02   # base = ... or this share of total ledger absolute value
ROLE_PRESENT_PCT_OF_BASE = 0.01
ROLE_ABSENT_PCT_OF_BASE = 0.001

# audit_relationships.py — individual relationship-check cuts (spec 7)
DEBTOR_INTENSITY_HIGH = 0.4
GST_OUTPUT_TO_REVENUE_MIN = 0.02

# audit_workbook.py
MAX_GAP_ACCOUNTS_PER_SHEET = 40
