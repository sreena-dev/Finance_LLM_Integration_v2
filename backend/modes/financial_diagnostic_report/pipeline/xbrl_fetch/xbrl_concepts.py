"""
The Block-2 metric registry — declared data, not a fact-store.

Each entry says: which XBRL concept(s) to sum, whether it is read at a balance-sheet
date (instant) or over the year (duration), how to present it, and which direction of
movement a reviewer should treat as adverse. This is the same idea `pipeline/fdr/
headline.py`'s `Tile` dataclass declares, stripped down to what XBRL actually needs —
no `unit_confidence`, `verify_verdict` or scale-guessing, because none of that applies
to a tagged, exact figure.

SALIENCE IS DECLARED, NEVER INFERRED FROM SIGN — same reasoning as `headline.py`:
receivables rising while revenue falls is the adverse case; profit falling is the
adverse case; the two have opposite signs, so guessing from sign alone is wrong about
half the tiles it touches.
"""
from __future__ import annotations
from dataclasses import dataclass, field

CURRENCY = "currency"   # a rupee amount, held in absolute INR (as stored in as_db)
PERCENT = "percent"     # held as a fraction, written as a percentage
TIMES = "times"         # a multiple, e.g. "2.10x of profit"

INSTANT = "instant"     # a balance-sheet-date figure (fy_start IS NULL)
DURATION = "duration"   # a figure for the year (fy_start IS NOT NULL)

ADVERSE_UP = "ADVERSE_WHEN_RISING"
ADVERSE_DOWN = "ADVERSE_WHEN_FALLING"
NEUTRAL = "DIRECTION_NEUTRAL"

# The signal layer's own material-movement trigger lives in a PDF-path module
# (pipeline/fdr/thresholds.py) this package deliberately does not import — see the
# package docstring. The value is read here as a plain number instead, so the two
# registries can be verified to agree without one importing the other.
ATTENTION_RELATIVE = 0.25   # matches thresholds.get("material_movement") as of this writing
ATTENTION_POINTS = 0.05     # for PERCENT-unit tiles, in percentage points

# A percentage move against a very small (but non-zero) prior-year base explodes into
# an enormous, misleading-looking number even when both years are correctly reported —
# dividing by something tiny amplifies any real change into a huge ratio. Beyond this
# ceiling (±1000%, i.e. 10x), the percentage itself is treated as not meaningful and is
# withheld in favour of the absolute change — see xbrl_dashboard.py's movement logic,
# which already withholds the movement outright when the base is exactly zero; this is
# the same guard, widened to cover "small enough that the percentage lies" as well as
# "exactly zero".
EXTREME_MOVEMENT_RATIO = 10.0


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str
    kind: str                       # INSTANT | DURATION | "derived"
    salience: str
    concepts: tuple[str, ...]       # summed together (e.g. current + non-current borrowings)
    denominator_concepts: tuple[str, ...] = ()   # for a derived ratio (e.g. effective tax rate)
    context_over: tuple[str, ...] = ()           # a second, declared read of the same figure
    context_label: str = ""
    absence_is_legitimate: bool = False          # e.g. CapitalWorkInProgress — a real "no CWIP"


METRICS: tuple[Metric, ...] = (
    Metric("X01", "Revenue from operations", CURRENCY, DURATION, ADVERSE_DOWN,
           ("RevenueFromOperations",)),
    Metric("X02", "Profit for the year", CURRENCY, DURATION, ADVERSE_DOWN,
           ("ProfitLossForPeriod",)),
    Metric("X03", "Profit / (loss) before tax", CURRENCY, DURATION, ADVERSE_DOWN,
           ("ProfitBeforeTax",)),
    Metric("X04", "Operating cash flow", CURRENCY, DURATION, ADVERSE_DOWN,
           ("CashFlowsFromUsedInOperatingActivities",),
           context_over=("ProfitLossForPeriod",), context_label="of profit for the year"),
    Metric("X05", "Depreciation, depletion and amortisation", CURRENCY, DURATION, NEUTRAL,
           ("DepreciationDepletionAndAmortisationExpense",)),
    Metric("X06", "Finance costs", CURRENCY, DURATION, ADVERSE_UP,
           ("FinanceCosts",)),
    Metric("X07", "Total assets", CURRENCY, INSTANT, NEUTRAL,
           ("Assets",)),
    Metric("X08", "Total equity", CURRENCY, INSTANT, NEUTRAL,
           ("Equity",)),
    Metric("X09", "Total liabilities", CURRENCY, INSTANT, NEUTRAL,
           ("Liabilities",)),
    Metric("X10", "Current assets", CURRENCY, INSTANT, NEUTRAL,
           ("CurrentAssets",)),
    Metric("X11", "Current liabilities", CURRENCY, INSTANT, NEUTRAL,
           ("CurrentLiabilities",)),
    Metric("X12", "Trade receivables (current)", CURRENCY, INSTANT, ADVERSE_UP,
           ("TradeReceivablesCurrent",)),
    Metric("X13", "Trade payables (current)", CURRENCY, INSTANT, NEUTRAL,
           ("TradePayablesCurrent",)),
    Metric("X14", "Cash and cash equivalents", CURRENCY, INSTANT, NEUTRAL,
           ("CashAndCashEquivalents",)),
    Metric("X15", "Property, plant and equipment", CURRENCY, INSTANT, NEUTRAL,
           ("PropertyPlantAndEquipment",)),
    Metric("X16", "Capital work-in-progress", CURRENCY, INSTANT, NEUTRAL,
           ("CapitalWorkInProgress",), absence_is_legitimate=True),
    Metric("X17", "Borrowings (current + non-current)", CURRENCY, INSTANT, ADVERSE_UP,
           ("BorrowingsCurrent", "BorrowingsNoncurrent")),
    Metric("X18", "Effective tax rate", PERCENT, "derived", NEUTRAL,
           ("TaxExpense",), denominator_concepts=("ProfitBeforeTax",)),
)

BY_ID: dict[str, Metric] = {m.id: m for m in METRICS}


def all_concepts() -> tuple[str, ...]:
    """Every XBRL concept name any metric reads — the ANY(%s) list for the single fetch."""
    out: list[str] = []
    for m in METRICS:
        out.extend(m.concepts)
        out.extend(m.denominator_concepts)
    return tuple(dict.fromkeys(out))
