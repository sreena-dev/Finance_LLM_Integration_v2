"""
The Block-3 Business Profile concept registry — declared data, not a fact store.

Every concept name below was checked against live `as_db` before being written here —
none are guessed from general Ind-AS taxonomy knowledge. Two concepts from an earlier
draft of this registry did not exist under the names originally used and have been
corrected or dropped:

  - "ExplorationCostsWrittenOff" does not exist under that name anywhere in the
    corpus. There is no single "exploration write-off" concept in the real taxonomy —
    only 18 granular E&P expenditure-by-type concepts (survey, geological/geophysical,
    extraction cost, etc.), none of them a combined write-off figure. Rather than guess
    which of the 18 map to a filer's own "written off" framing, this field is dropped
    until a specific mapping is verified.
  - "ImpairmentLossRecognisedInProfitAndLoss" (AndLoss) does not exist — the real
    concept spells it "OrLoss". The asset-class-specific variant
    ("...PropertyPlantAndEquipment") has far better coverage (367 docs) than the
    generic one (20 docs) and is used here instead.

`ContingentLiabilities` moved here from the disclosure registry — it is a numeric
`financial_facts` concept, not a `disclosures` one; putting it in the wrong table's
concept list meant it could never match anything.
"""
from __future__ import annotations
from dataclasses import dataclass

CURRENCY = "currency"
RATIO = "ratio"
PERCENT = "percent"

INSTANT = "instant"
DURATION = "duration"


@dataclass(frozen=True)
class ProfileMetric:
    id: str
    label: str
    unit: str
    kind: str  # INSTANT | DURATION | "derived"
    concepts: tuple[str, ...]
    denominator_concepts: tuple[str, ...] = ()
    is_optional: bool = False  # absence is a legitimate business fact, not a gap


# --- 1. NUMERICAL GROUNDING METRICS (financial_facts) --------------------------------
NUMERIC_METRICS: tuple[ProfileMetric, ...] = (
    ProfileMetric("P01", "Total equity", CURRENCY, INSTANT, ("Equity",)),
    ProfileMetric("P02", "Total borrowings", CURRENCY, INSTANT,
                  ("BorrowingsCurrent", "BorrowingsNoncurrent")),
    ProfileMetric("P03", "Debt-equity ratio", RATIO, "derived",
                  ("BorrowingsCurrent", "BorrowingsNoncurrent"),
                  denominator_concepts=("Equity",)),
    ProfileMetric("P04", "Depreciation, depletion and amortisation", CURRENCY, DURATION,
                  ("DepreciationDepletionAndAmortisationExpense",)),
    ProfileMetric("P05", "Impairment loss on property, plant and equipment", CURRENCY,
                  DURATION, ("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",),
                  is_optional=True),
    ProfileMetric("P06", "Investment book (current + non-current)", CURRENCY, INSTANT,
                  ("CurrentInvestments", "NoncurrentInvestments")),
    ProfileMetric("P07", "Revenue from operations", CURRENCY, DURATION,
                  ("RevenueFromOperations",)),
    ProfileMetric("P08", "Contingent liabilities", CURRENCY, INSTANT,
                  ("ContingentLiabilities",), is_optional=True),
)

NUMERIC_BY_ID: dict[str, ProfileMetric] = {m.id: m for m in NUMERIC_METRICS}


def all_profile_numeric_concepts() -> tuple[str, ...]:
    """Every concept name required from financial_facts for the business profile."""
    out: list[str] = []
    for m in NUMERIC_METRICS:
        out.extend(m.concepts)
        out.extend(m.denominator_concepts)
    return tuple(dict.fromkeys(out))


# --- 2. NARRATIVE & DISCLOSURE CONCEPTS (disclosures) --------------------------------
# Every entry below is present for at least 100 documents in the corpus at the time of
# writing (`documentation`-style verification, not a guess). Coverage will drift as
# as_db is (re)ingested — re-check before relying on an exact count.

MODEL_DISCLOSURES: tuple[str, ...] = (
    "DisclosureOfChangeInNatureOfBusinessExplanatory",   # ~759 docs
    "DisclosureInBoardOfDirectorsReportExplanatory",      # ~758 docs — genuine operational prose
)

REVENUE_DISCLOSURES: tuple[str, ...] = (
    "DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory",  # ~109 docs
    "DisclosureOfRevenueExplanatory",                                   # ~789 docs
)

COST_DISCLOSURES: tuple[str, ...] = (
    "DisclosureInAuditorsReportRelatingToMaintenanceOfCostRecords",  # ~735 docs
)

ESTIMATE_AND_RISK_DISCLOSURES: tuple[str, ...] = (
    "DetailsOfSignificantAndMaterialOrdersPassedByRegulatorsOrCourtsOrTribunalsImpactingGoingConcernStatusAndCompanysOperationsInFutureExplanatory",  # ~760 docs
    "AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReport",  # ~255 docs — often 0 rows: a clean opinion has nothing to say here, which is itself informative
)

ALL_DISCLOSURE_CONCEPTS: tuple[str, ...] = tuple(dict.fromkeys(
    MODEL_DISCLOSURES + REVENUE_DISCLOSURES + COST_DISCLOSURES + ESTIMATE_AND_RISK_DISCLOSURES
))


def all_profile_disclosure_concepts() -> tuple[str, ...]:
    """Every concept name required from disclosures table for the business profile."""
    return ALL_DISCLOSURE_CONCEPTS
