"""
The closed S01-S27 signal registry for the XBRL (`as_db`) FDR path.

Declares WHAT each signal is: which cluster it belongs to, which layer produces it
(spec Sec 6), which as_db concepts it needs, how many distinct `fy_end`s it needs to be
evaluated honestly, its severity if fired, and the non-error alternative explanations a
reviewer should weigh before treating it as an audit lead (spec Sec 2.2).

It does NOT declare the rule (see `xbrl_signal_rules.py` — the only place a verdict or a
number is formed) or the derivation (see `xbrl_signal_derivations.py` — where in as_db
each input is read from, and the exact dimension axis where one applies).

CLUSTER LABELLING
-------------------------------------------------------------------------------------
`cluster_id` uses the eight-cluster labels (`RC-WC`, `RC-REC`, `RC-CAP`, `RC-FUND`,
`RC-EST`, `RC-DEP`, `RC-CONT`, `RC-INV`) that already match this exact S01-S27 grouping
in `pipeline/fdr/clusters.py`, rather than the six-cluster `RC01`-`RC06` labels in this
package's `xbrl_risk_concepts.py` (which has no slot for contingent-liabilities or
investment-concentration signals). This is a LABEL choice only — no code or values are
imported from `pipeline/fdr`, per the explicit decision to keep the two FDR paths fully
independent. Cluster PACKAGE ASSEMBLY (raising RC-* themes from these signals) is out of
scope for this pass; the label exists so that work has a place to land later.

WINDOW YEARS AND THE `as_db` REALITY
-------------------------------------------------------------------------------------
`window_years` states how many distinct `fy_end`s a signal genuinely needs (1 = a single
filing suffices; 2 = a year-on-year movement; 3 = a multi-year trend, and spec Sec 9.5
forbids computing one on a shorter series). As_db was confirmed to carry effectively one
filing per entity today (see the implementation plan) — declaring `window_years` honestly
here is what lets `xbrl_signal_rules.py` abstain correctly rather than fabricate a trend
off a single point. Where a defensible single-period proxy exists, it is named in
`single_period_proxy` so the rule function's fallback is explicit and auditable, never
silent.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from . import xbrl_risk_concepts as C
from . import xbrl_signal_concepts as SC

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
SEVERITIES = frozenset({HIGH, MEDIUM, LOW})

CLUSTER_IDS = ("RC-WC", "RC-REC", "RC-CAP", "RC-FUND", "RC-EST", "RC-DEP", "RC-CONT", "RC-INV")


@dataclass(frozen=True)
class XbrlSignal:
    id: str
    cluster_id: str
    layer: int                                  # 1-4, spec Sec 6
    title: str
    spec_basis: str
    required_numeric_concepts: tuple[str, ...]
    required_disclosure_concepts: tuple[str, ...] = ()
    window_years: int = 1
    severity: str = MEDIUM
    single_period_proxy: str = ""               # "" = no proxy; a trend signal without
                                                 # one abstains outright on a short series
    alt_explanations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.cluster_id not in CLUSTER_IDS:
            raise ValueError(f"{self.id}: unknown cluster_id {self.cluster_id!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.id}: bad severity {self.severity!r}")
        if not (1 <= self.layer <= 4):
            raise ValueError(f"{self.id}: layer must be 1-4, got {self.layer}")

    @property
    def is_trend(self) -> bool:
        return self.window_years >= 3

    @property
    def needs_audit_authoring(self) -> bool:
        return not self.alt_explanations


SIGNALS: tuple[XbrlSignal, ...] = (

    XbrlSignal(
        id="S01", cluster_id="RC-WC", layer=4,
        title="Deteriorating cash-conversion cycle",
        spec_basis="Sec 9.2 — working capital read as an interacting system; a lengthening "
                   "CCC across the series is a working-capital change of audit interest.",
        required_numeric_concepts=(
            "TradeReceivablesCurrent", "Inventories", "TradePayablesCurrent",
            "RevenueFromOperations",
        ) + SC.COGS_NUMERIC_CONCEPTS,
        window_years=3,
        severity=MEDIUM,
        alt_explanations=(
            "A genuine business-model shift (e.g. a move to longer-tenor contracts) "
            "rather than a collection or payment-discipline problem.",
            "A one-off large year-end transaction distorting the day-count ratios "
            "without reflecting the underlying cycle.",
        ),
        notes=("Needs 3 distinct fy_ends for the same entity_cin — as_db has almost "
               "none today; expect this to ABSTAIN (PANEL_TOO_SHORT) on most filings.",),
    ),

    XbrlSignal(
        id="S02", cluster_id="RC-WC", layer=2,
        title="Payables funding growth",
        spec_basis="Sec 7 — funding of operations through suppliers is a working-capital-"
                   "stress signal when payables growth structurally outpaces revenue.",
        required_numeric_concepts=(
            "TradePayablesCurrent", "RevenueFromOperations", "Inventories",
        ) + SC.COGS_NUMERIC_CONCEPTS,
        window_years=2,
        severity=HIGH,
        alt_explanations=(
            "Improved (longer) supplier credit terms negotiated commercially, not "
            "distress-driven deferral.",
            "Inventory build-up ahead of a planned expansion, funded deliberately "
            "through suppliers rather than debt.",
        ),
    ),

    XbrlSignal(
        id="S03", cluster_id="RC-WC", layer=2,
        title="Net current-liability position",
        spec_basis="Sec 7 — current vs non-current balance; a net current-liability "
                   "position signals reliance on rolling short-term finance.",
        required_numeric_concepts=("CurrentAssets", "CurrentLiabilities"),
        window_years=1,
        severity=HIGH,
        alt_explanations=(
            "A business model that structurally operates with negative working capital "
            "by design (e.g. a subscription or advance-collection revenue model), where "
            "the position is a normal feature rather than a stress signal.",
        ),
    ),

    XbrlSignal(
        id="S04", cluster_id="RC-WC", layer=4,
        title="Weak / negative operating cash flow",
        spec_basis="Sec 9.1 — cash conversion: whether reported profit converts into "
                   "operating cash.",
        required_numeric_concepts=(
            "CashFlowsFromUsedInOperatingActivities", "ProfitLossForPeriod",
            "ProfitLossForPeriodFromContinuingOperations",
        ),
        window_years=1,
        single_period_proxy="Single-period OCF<0 or OCF<0.80*PAT check; the spec's "
                             "'>=2 of the last 3 years' persistence form needs a 3-year "
                             "window as_db does not carry for most entities.",
        severity=MEDIUM,
        alt_explanations=(
            "A growth-phase entity investing working capital ahead of scaling revenue, "
            "consistent with its business model rather than a quality-of-earnings issue.",
        ),
    ),

    XbrlSignal(
        id="S05", cluster_id="RC-REC", layer=2,
        title="Receivables outpacing revenue",
        spec_basis="Sec 9.1/7 — receivables growth materially ahead of revenue growth is "
                   "a revenue/receivable-quality lead.",
        required_numeric_concepts=("TradeReceivablesCurrent", "TradeReceivables", "RevenueFromOperations"),
        window_years=2,
        severity=HIGH,
        alt_explanations=(
            "A deliberate credit-term extension to win market share, disclosed and "
            "commercially reasoned rather than a collection or recognition problem.",
        ),
    ),

    XbrlSignal(
        id="S06", cluster_id="RC-REC", layer=4,
        title="Accruals-heavy earnings",
        spec_basis="Sec 9.1 — earnings quality / accruals ratio and its trend.",
        required_numeric_concepts=(
            "ProfitLossForPeriod", "CashFlowsFromUsedInOperatingActivities", "Assets",
        ),
        window_years=1,
        severity=MEDIUM,
        alt_explanations=(
            "Large non-cash but legitimate accruals (e.g. a one-off fair-value gain "
            "under Ind AS) rather than earnings management.",
        ),
    ),

    XbrlSignal(
        id="S07", cluster_id="RC-REC", layer=3,
        title="Period-end revenue concentration",
        spec_basis="Sec 8.2 — cut-off and period-end concentration as a revenue-quality lead.",
        required_numeric_concepts=("RevenueFromOperations",) + SC.CONTRACT_ASSET_NUMERIC_CONCEPTS,
        window_years=1,
        single_period_proxy="Unbilled contract-assets / revenue ratio only. Annual MCA "
                             "XBRL filings carry no quarterly figures (those exist only "
                             "in SEBI stock-exchange filings, out of as_db's scope), so "
                             "the quarterly-concentration branch is NOT_APPLICABLE, "
                             "always, not per-filing.",
        severity=MEDIUM,
        alt_explanations=(
            "A long-cycle contracting business (e.g. EPC) where unbilled contract "
            "assets are a normal feature of percentage-of-completion accounting.",
        ),
    ),

    XbrlSignal(
        id="S08", cluster_id="RC-REC", layer=2,
        title="Related-party receivable concentration",
        spec_basis="Sec 7 — concentration of receivables in related/government parties "
                   "is a risk in its own right and feeds the dependency lens (Sec 3.3).",
        required_numeric_concepts=(
            "TradeReceivablesCurrent",
        ) + SC.RELATED_PARTY_NUMERIC_CONCEPTS,
        window_years=1,
        severity=HIGH,
        alt_explanations=(
            "An intra-group treasury or shared-services arrangement with normal, "
            "arm's-length settlement terms rather than an uncollectible concentration.",
        ),
    ),

    XbrlSignal(
        id="S09", cluster_id="RC-CAP", layer=2,
        title="Capital work-in-progress concentration",
        spec_basis="Sec 7 — asset mix; a rising CWIP or PPE share points to capitalisation "
                   "and project-completion risk.",
        required_numeric_concepts=("CapitalWorkInProgress", "Assets", "PropertyPlantAndEquipment"),
        window_years=1,
        single_period_proxy="Face ratio (CWIP / Assets) only. as_db carries ZERO "
                             "dimensioned rows for CapitalWorkInProgress across the "
                             "entire corpus — the ageing-schedule (>3 years overdue) "
                             "branch is NOT_APPLICABLE, confirmed by direct query, not "
                             "per-filing.",
        severity=MEDIUM,
        alt_explanations=(
            "A large, genuinely in-progress capital programme appropriate to the "
            "entity's stated capex plan, not a stalled or mis-capitalised project.",
        ),
    ),

    XbrlSignal(
        id="S10", cluster_id="RC-CAP", layer=2,
        title="Rising non-current-other share",
        spec_basis="Sec 7 — a rising non-current-other share of the balance sheet points "
                   "to specific audit areas.",
        required_numeric_concepts=("OtherNoncurrentAssets", "Assets"),
        window_years=2,
        severity=MEDIUM,
        alt_explanations=(
            "A reclassification within non-current assets (e.g. into a newly separately "
            "disclosed line) rather than genuine asset growth in an opaque bucket.",
        ),
    ),

    XbrlSignal(
        id="S11", cluster_id="RC-CAP", layer=3,
        title="Depreciation-asset divergence",
        spec_basis="Sec 9.1 — expense and capitalisation behaviour; depreciation not "
                   "moving with the asset base is a capitalisation-risk signal.",
        required_numeric_concepts=(
            "DepreciationDepletionAndAmortisationExpense", "PropertyPlantAndEquipment",
        ),
        window_years=2,
        severity=MEDIUM,
        alt_explanations=(
            "A genuine, disclosed change in estimated useful lives or a shift in asset "
            "mix toward longer-life categories, rather than under-depreciation.",
        ),
    ),

    XbrlSignal(
        id="S12", cluster_id="RC-CAP", layer=4,
        title="Impairment timing disconnect",
        spec_basis="Sec 9.1 — impairment timing read as a risk signal requiring "
                   "corroboration, never a conclusion about intent.",
        required_numeric_concepts=(
            "ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment",
            "RevenueFromOperations",
        ),
        required_disclosure_concepts=("DisclosureInBoardOfDirectorsReportExplanatory",),
        window_years=2,
        severity=MEDIUM,
        single_period_proxy="Revenue decline year on year with zero impairment "
                             "recognised. CGU-level cash flows are internal management "
                             "data not present in as_db; RevenueFromOperations itself "
                             "carries no segment dimension in as_db either (confirmed "
                             "by query), so any segment-level read falls back to the "
                             "'Segment Information' disclosure category, narratively.",
        alt_explanations=(
            "A genuinely temporary revenue dip (e.g. a one-off contract gap) not "
            "indicative of a value-in-use shortfall requiring impairment.",
        ),
    ),

    XbrlSignal(
        id="S13", cluster_id="RC-FUND", layer=3,
        title="Leverage-driven return on equity",
        spec_basis="Sec 8.2 — DuPont decomposition distinguishes a genuine profitability "
                   "gain from a leverage-driven one.",
        required_numeric_concepts=(
            "ProfitLossForPeriod", "RevenueFromOperations", "Assets", "Equity",
        ),
        window_years=2,
        single_period_proxy="A single-period 3-stage DuPont decomposition (margin x "
                             "turnover x equity multiplier) is always reported; the "
                             "'>60% of ROE *expansion*' form needs a prior-period ROE "
                             "and abstains without one.",
        severity=MEDIUM,
        alt_explanations=(
            "A deliberate, disclosed capital-structure decision (e.g. a buyback or a "
            "planned refinancing) rather than an undisclosed leverage build-up.",
        ),
    ),

    XbrlSignal(
        id="S14", cluster_id="RC-FUND", layer=2,
        title="Short-term funding of long-term assets",
        spec_basis="Sec 7 — net current-liability positions and reliance on rolling "
                   "short-term finance to fund long-term assets.",
        required_numeric_concepts=(
            "BorrowingsCurrent", "BorrowingsNoncurrent", "CurrentAssets",
            "CurrentLiabilities", "Assets",
        ),
        window_years=1,
        severity=HIGH,
        alt_explanations=(
            "A deliberate, well-managed revolving short-term facility used as a matter "
            "of treasury policy in a sector where that is normal (e.g. trading), rather "
            "than distress-driven short-term reliance.",
        ),
    ),

    XbrlSignal(
        id="S15", cluster_id="RC-FUND", layer=3,
        title="Finance-cost / borrowings divergence",
        spec_basis="Sec 7 — finance cost not moving with borrowings signals a funding "
                   "and solvency risk.",
        required_numeric_concepts=("FinanceCosts", "BorrowingsCurrent", "BorrowingsNoncurrent"),
        window_years=2,
        severity=MEDIUM,
        alt_explanations=(
            "A mid-year refinancing at a materially different rate, or a shift between "
            "fixed- and floating-rate borrowings, rather than a disclosure gap.",
        ),
    ),

    XbrlSignal(
        id="S16", cluster_id="RC-EST", layer=4,
        title="Provision volatility & reversals",
        spec_basis="Sec 9.3 — estimate quality: consistency, volatility and reversals in "
                   "provisions.",
        required_numeric_concepts=(
            "ProvisionsCurrent", "ProvisionsNoncurrent", "ProfitBeforeTax",
        ) + SC.PROVISION_MOVEMENT_NUMERIC_CONCEPTS,
        window_years=1,
        single_period_proxy="Provisions-written-back / |PBT| level check only; the "
                             "'>25% YoY movement' form needs a prior-period provisions "
                             "balance and abstains without one.",
        severity=MEDIUM,
        alt_explanations=(
            "A single large, well-disclosed one-off settlement or claim resolution "
            "rather than a pattern of opportunistic provisioning.",
        ),
    ),

    XbrlSignal(
        id="S17", cluster_id="RC-EST", layer=4,
        title="Useful-life changes",
        spec_basis="Sec 9.3 — reporting-behaviour indicators: changes in useful lives "
                   "read strictly as risk signals requiring corroboration.",
        required_numeric_concepts=(),
        required_disclosure_concepts=(
            "SignificantAccountingJudgementsAndEstimatesTextBlock",
        ),
        window_years=1,
        severity=LOW,
        notes=("Narrative-only signal — see xbrl_signal_derivations.py for the "
               "structural (category-first, never a bare keyword regex) approach.",),
        alt_explanations=(
            "A routine, immaterial policy refinement rather than an earnings-"
            "management-motivated estimate change.",
        ),
    ),

    XbrlSignal(
        id="S18", cluster_id="RC-EST", layer=4,
        title="Non-cash gains dominance",
        spec_basis="Sec 9.1 — recurring vs one-off earnings; performance flattered by "
                   "non-recurring credits while cash conversion lags.",
        required_numeric_concepts=("OtherIncome", "ProfitBeforeTax", "CashFlowsFromUsedInOperatingActivities"),
        window_years=1,
        severity=MEDIUM,
        alt_explanations=(
            "A genuine, disclosed one-off gain (e.g. an asset sale) reported "
            "transparently rather than used to obscure weak operating performance.",
        ),
    ),

    XbrlSignal(
        id="S19", cluster_id="RC-EST", layer=3,
        title="Other-income sustainability",
        spec_basis="Sec 9.1 — sustainability of other income.",
        required_numeric_concepts=("OtherIncome", "RevenueFromOperations"),
        window_years=2,
        severity=LOW,
        alt_explanations=(
            "A treasury-heavy but stable and recurring investment-income stream "
            "appropriate to the entity's cash-rich balance sheet, not a distortion.",
        ),
    ),

    XbrlSignal(
        id="S20", cluster_id="RC-DEP", layer=4,
        title="High government-support dependency",
        spec_basis="Sec 3.3/9.3 — the dependency lens: the degree to which the apparent "
                   "position reflects government support rather than organic performance.",
        required_numeric_concepts=(
            "RevenueFromOperations", "OtherIncome",
        ) + SC.GOVERNMENT_INCOME_NUMERIC_CONCEPTS,
        window_years=1,
        severity=MEDIUM,
        alt_explanations=(
            "A capital-grant-funded infrastructure entity where government support is "
            "the designed, disclosed financing model rather than a distress dependency.",
        ),
    ),

    XbrlSignal(
        id="S21", cluster_id="RC-DEP", layer=2,
        title="Unspent-grant build-up",
        spec_basis="Sec 9.3 — unspent-grant build-up as a dependency and condition-"
                   "compliance risk.",
        required_numeric_concepts=(
            "DeferredGovernmentGrantsNoncurrent",
        ) + SC.DEFERRED_GRANT_CURRENT_NUMERIC_CONCEPTS,
        window_years=2,
        severity=MEDIUM,
        alt_explanations=(
            "A multi-year capital project on a disclosed, sanctioned schedule where the "
            "unspent balance simply tracks project milestones not yet reached.",
        ),
    ),

    XbrlSignal(
        id="S22", cluster_id="RC-DEP", layer=1,
        title="Administered-pricing reliance",
        spec_basis="Sec 5.2 — revenue model classification: tariff-based / regulated-"
                   "return revenue.",
        required_numeric_concepts=(),
        required_disclosure_concepts=(
            "DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory",
        ),
        window_years=1,
        severity=LOW,
        notes=("Business-model-level classification (Block 3 / Sec 5.2), not a "
               "balance-sheet fact — sourced from the Business Profile block's revenue-"
               "model classification where available, corroborated (never re-derived) "
               "by this signal; scoped out if that classification hasn't run.",),
        alt_explanations=(),
    ),

    XbrlSignal(
        id="S23", cluster_id="RC-CONT", layer=2,
        title="Contingent liabilities vs net worth",
        spec_basis="Sec 4.1 — contingent liabilities and commitments read against net "
                   "worth for planning materiality.",
        required_numeric_concepts=("ContingentLiabilities", "Equity"),
        window_years=1,
        severity=HIGH,
        alt_explanations=(
            "A large but low-probability or well-precedented class of claims (e.g. "
            "routine tax matters under active, disclosed appeal) rather than a genuine "
            "going-concern-adjacent exposure.",
        ),
    ),

    XbrlSignal(
        id="S24", cluster_id="RC-CONT", layer=2,
        title="Financial guarantees for group entities",
        spec_basis="Sec 4.1 — related-party and group guarantee exposure.",
        required_numeric_concepts=SC.GUARANTEE_NUMERIC_CONCEPTS + ("Equity",),
        window_years=1,
        severity=HIGH,
        alt_explanations=(
            "A guarantee issued for a wholly-owned, financially sound subsidiary as "
            "routine group treasury practice, not an exposure to a distressed entity.",
        ),
    ),

    XbrlSignal(
        id="S25", cluster_id="RC-INV", layer=2,
        title="Investment concentration in assets",
        spec_basis="Sec 7 — concentration of investments as a structural risk signal.",
        required_numeric_concepts=("NoncurrentInvestments", "CurrentInvestments", "Assets"),
        window_years=1,
        severity=MEDIUM,
        alt_explanations=(
            "A treasury-management or holding-company structure where a large "
            "investment book is the designed core of the business model.",
        ),
    ),

    XbrlSignal(
        id="S26", cluster_id="RC-INV", layer=4,
        title="Fair-value OCI volatility",
        spec_basis="Sec 9.3 — estimate quality: fair-value sensitivity in investment "
                   "carrying amounts.",
        required_numeric_concepts=SC.OCI_NUMERIC_CONCEPTS + ("Equity",),
        window_years=1,
        severity=LOW,
        alt_explanations=(
            "Ordinary mark-to-market movement of a listed-equity FVOCI portfolio "
            "tracking a volatile but liquid, well-disclosed market.",
        ),
    ),

    XbrlSignal(
        id="S27", cluster_id="RC-INV", layer=3,
        title="Investment income dependency",
        spec_basis="Sec 9.1 — recurring vs one-off earnings applied to treasury income.",
        required_numeric_concepts=SC.DIVIDEND_INCOME_NUMERIC_CONCEPTS + ("ProfitBeforeTax",),
        window_years=1,
        severity=LOW,
        alt_explanations=(
            "A holding-company structure where dividend income from subsidiaries is "
            "the designed primary revenue stream, not a distortion of an operating "
            "business's results.",
        ),
    ),
)

_BY_ID: dict[str, XbrlSignal] = {s.id: s for s in SIGNALS}
if len(_BY_ID) != len(SIGNALS):
    raise RuntimeError("duplicate signal id in xbrl_signal_registry")


def get(signal_id: str) -> XbrlSignal:
    try:
        return _BY_ID[signal_id]
    except KeyError:
        raise KeyError(f"no signal registered for {signal_id!r}") from None


def all_signals() -> tuple[XbrlSignal, ...]:
    return SIGNALS


def all_required_numeric_concepts() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for s in SIGNALS:
        for c in s.required_numeric_concepts:
            seen[c] = None
    return tuple(seen)


def all_required_disclosure_concepts() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for s in SIGNALS:
        for c in s.required_disclosure_concepts:
            seen[c] = None
    return tuple(seen)
