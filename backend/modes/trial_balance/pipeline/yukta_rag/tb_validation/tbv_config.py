"""Centralized numeric thresholds and keyword anchors for the standalone TB
validation/comparison module (tb-comparison-pipeline).

Follows the same convention as ``yukta_rag/audit/audit_config.py`` and
``yukta_rag/trial_balance/tb_config.py`` — flat module, grouped constants,
each with a rationale comment, no shared/imported thresholds from either of
those modules (this package is deliberately standalone).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Layer-1 equality/tolerance (used by every diff_pct-against-tolerance check:
# TB-005, TB-009, TB-010, TB-011, TB-012, TB-024)
# ---------------------------------------------------------------------------
NEGLIGENCE_TOLERANCE_PCT = 5.0  # default; configurable per engagement

# TB-019 — rounding-off head within materiality. Rupee amount, user-supplied,
# no default: the check cannot run without it (required call-time input).
ROUNDING_ACCOUNT_THRESHOLD_REQUIRED = True

# Layer 3 — variance materiality. Percentage, user-supplied, no default: if
# absent, variances are still computed but every row is flagged NO_THRESHOLD
# rather than HIGH_PRIORITY/MEDIUM/LOW.
VARIANCE_MATERIALITY_PCT_REQUIRED = True

# ---------------------------------------------------------------------------
# TB-000 — sign-convention heuristic (only used when the convention isn't
# explicitly documented by the caller). Debit-normal = assets & expenses;
# credit-normal = liabilities, equity & income — standard double-entry
# convention, not derived from any FSLI/audit-engine keyword list.
# ---------------------------------------------------------------------------
SIGN_CONVENTION_MIN_ANCHORS = 3       # need at least this many keyword hits per side to judge
SIGN_CONVENTION_AGREEMENT_THRESHOLD = 0.7  # >=70% of anchors on a side must agree on sign to PASS

DEBIT_NORMAL_KEYWORDS = (
    "cash", "bank", "petty cash", "receivable", "debtor", "sundry debtor",
    "inventory", "inventories", "stock", "raw material", "finished goods",
    "prepaid", "advance", "deposit given", "security deposit paid",
    "loan given", "loans given", "loan to", "investment",
    "property", "plant", "equipment", "machinery", "building", "land",
    "furniture", "fixture", "vehicle", "fixed asset", "capital work",
    "work in progress", "cwip", "intangible", "goodwill", "software",
    "purchase", "cost of material", "cost of goods", "consumption",
    "salary", "salaries", "wages", "rent", "depreciation", "amortisation",
    "amortization", "interest paid", "interest expense", "finance cost",
    "freight", "repairs", "insurance", "electricity", "audit fee",
    "professional fee", "travel", "expense", "expenditure",
)

CREDIT_NORMAL_KEYWORDS = (
    "payable", "creditor", "sundry creditor", "borrowing", "loan from",
    "term loan", "cash credit", "overdraft", "debenture", "bond",
    "provision", "deferred tax liab", "security deposit received",
    "advance received", "reserve", "surplus", "retained earning",
    "share capital", "equity capital", "share premium", "capital reserve",
    "sales", "sale of", "revenue", "turnover", "income", "interest received",
    "dividend received", "commission received", "other income",
    "gst payable", "gst output", "tds payable", "tcs payable",
    "statutory dues", "duty payable", "cess payable",
)

# ---------------------------------------------------------------------------
# TB-004 — numeric validity / precision consistency.
# ---------------------------------------------------------------------------
EXPECTED_DECIMAL_PLACES = 2

# ---------------------------------------------------------------------------
# TB-023 — excessive round-number entries (pure computation, no LLM needed).
# ---------------------------------------------------------------------------
ROUND_NUMBER_PCT_THRESHOLD = 15.0  # % of non-zero closing balances that are exact round figures
ROUND_NUMBER_MIN_ABS = 1000.0      # ignore trivially-small "round" values (e.g. 0, 1, 10)

# ---------------------------------------------------------------------------
# TB-021 — Benford's Law leading-digit test (data prep only; judgment is
# LLM-assisted per spec, but the deviation computation itself is pure math).
# ---------------------------------------------------------------------------
BENFORD_EXPECTED_PCT = {
    1: 30.1, 2: 17.6, 3: 12.5, 4: 9.7, 5: 7.9,
    6: 6.7, 7: 5.8, 8: 5.1, 9: 4.6,
}
BENFORD_MIN_SAMPLE_SIZE = 30            # too few rows -> the test isn't meaningful
BENFORD_MAX_DEVIATION_PP = 10.0         # percentage-point deviation on any single digit before flagging

# ---------------------------------------------------------------------------
# TB-033 — stale year reference in GL description vs TB period.
# ---------------------------------------------------------------------------
STALE_YEAR_GAP = 3  # a 4-digit year token more than this many years off the TB period -> flag

# ---------------------------------------------------------------------------
# Layer 3 — variance analysis severity bands (only evaluated when
# variance_materiality_pct is supplied; otherwise every row is NO_THRESHOLD).
# ---------------------------------------------------------------------------
HIGH_PRIORITY_MULTIPLE = 2.0  # abs(variance_pct) > threshold * this -> HIGH_PRIORITY

# ---------------------------------------------------------------------------
# Total-row filter — case-insensitive, trimmed exact/starts-with match.
# ---------------------------------------------------------------------------
TOTAL_ROW_EXACT = (
    "total", "totals", "grand total", "grand totals",
    "sub total", "subtotal", "sub totals", "subtotals",
)
TOTAL_ROW_STARTS_WITH = ("grand total", "total ")
