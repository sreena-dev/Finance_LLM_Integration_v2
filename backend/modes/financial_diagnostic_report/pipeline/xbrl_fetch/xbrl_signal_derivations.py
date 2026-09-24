"""
Per-signal derivation: which `as_db` table, which concept name(s), whether the figure is
undimensioned (face) or dimensioned, and — where dimensioned — the EXACT axis name to
filter on. This is what answers "where does the context id / dimension matter" as data,
rather than as a comment buried inside a formula.

Every dimension axis and every "no dimensioned rows exist" claim below was confirmed by a
direct read-only query against `as_db` (see the implementation plan) before being written
here — nothing is guessed from the taxonomy alone. Where as_db was confirmed to carry NO
dimensioned rows for a concept, the derivation says so explicitly with `axis=None` and
`confirmed_absent=True`, so `xbrl_signal_rules.py` can map that branch straight to
NOT_APPLICABLE instead of querying for it on every filing.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

# ---- which table -----------------------------------------------------------------
FINANCIAL_FACTS = "financial_facts"
DISCLOSURES = "disclosures"
TABLES = frozenset({FINANCIAL_FACTS, DISCLOSURES})


@dataclass(frozen=True)
class Derivation:
    signal_id: str
    table: str
    concepts: tuple[str, ...]
    dimensioned: bool = False
    axis: str | None = None                # the dimensions-jsonb key to filter on, when
                                            # dimensioned=True
    confirmed_absent: bool = False         # True => as_db has zero dimensioned rows for
                                            # this concept, confirmed by query; the branch
                                            # is NOT_APPLICABLE for every filing, not just
                                            # this one
    formula: str = ""
    basis: str = ""

    def __post_init__(self) -> None:
        if self.table not in TABLES:
            raise ValueError(f"{self.signal_id}: unknown table {self.table!r}")
        if self.dimensioned and not self.axis and not self.confirmed_absent:
            raise ValueError(
                f"{self.signal_id}: a dimensioned derivation needs an axis, or must be "
                f"marked confirmed_absent if as_db has no dimensioned rows for it"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DERIVATIONS: tuple[Derivation, ...] = (

    Derivation(
        "S01", FINANCIAL_FACTS,
        ("TradeReceivablesCurrent", "Inventories", "TradePayablesCurrent",
         "RevenueFromOperations", "CostOfMaterialsConsumed", "PurchasesOfStockInTrade",
         "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade"),
        formula="CCC = DSO + DIO - DPO, where DSO=Receivables/Revenue*365, "
                "DIO=Inventories/COGS*365, DPO=Payables/Purchases*365; "
                "COGS = CostOfMaterialsConsumed + PurchasesOfStockInTrade + "
                "ChangesInInventories...; Purchases = CostOfMaterialsConsumed + "
                "PurchasesOfStockInTrade.",
    ),
    Derivation(
        "S02", FINANCIAL_FACTS,
        ("TradePayablesCurrent", "RevenueFromOperations", "CostOfMaterialsConsumed",
         "PurchasesOfStockInTrade",
         "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade", "Inventories"),
        formula="delta%TradePayables - delta%Revenue > payables_growth_gap_pp AND "
                "TradePayables/COGS > payables_to_cogs_share.",
    ),
    Derivation(
        "S03", FINANCIAL_FACTS, ("CurrentAssets", "CurrentLiabilities"),
        formula="CurrentAssets / CurrentLiabilities < current_ratio_floor.",
    ),
    Derivation(
        "S04", FINANCIAL_FACTS,
        ("CashFlowsFromUsedInOperatingActivities", "ProfitLossForPeriod"),
        formula="OCF < 0 OR (PAT > 0 AND OCF/PAT < ocf_to_pat_floor).",
    ),
    Derivation(
        "S05", FINANCIAL_FACTS, ("TradeReceivablesCurrent", "RevenueFromOperations"),
        formula="delta%Receivables - delta%Revenue > receivables_growth_gap_pp.",
    ),
    Derivation(
        "S06", FINANCIAL_FACTS,
        ("ProfitLossForPeriod", "CashFlowsFromUsedInOperatingActivities", "Assets"),
        formula="(PAT - OCF) / Assets > accruals_ratio_ceiling.",
    ),
    Derivation(
        "S07", FINANCIAL_FACTS,
        ("GrossAmountDueFromCustomersForContractWorkAsAssets", "RevenueFromOperations"),
        formula="UnbilledContractAssets / Revenue > unbilled_contract_assets_share.",
        basis="Quarterly revenue breakdown confirmed absent from as_db (annual-only "
              "MCA AOC-4 filings) — the concentration branch is scoped to this proxy "
              "only, not attempted per-filing.",
    ),
    Derivation(
        "S08", FINANCIAL_FACTS,
        ("TradeReceivablesCurrent", "AmountsReceivableRelatedPartyTransactions"),
        dimensioned=True, axis="RelatedParty",
        formula="SUM(AmountsReceivableRelatedPartyTransactions over RelatedParty axis) "
                "/ TradeReceivablesCurrent > related_party_receivable_share.",
        basis="Confirmed: AmountsReceivableRelatedPartyTransactions is 100% dimensioned "
              "(0 undimensioned rows) by the RelatedParty / CategoriesOfRelatedParties "
              "axes, 5,213 rows across 456 docs — query the RelatedParty axis and sum "
              "value_numeric per doc_id, no text scanning needed.",
    ),
    Derivation(
        "S09", FINANCIAL_FACTS, ("CapitalWorkInProgress", "Assets"),
        dimensioned=True, axis=None, confirmed_absent=True,
        formula="CapitalWorkInProgress / Assets > cwip_to_assets_share (face ratio only).",
        basis="Confirmed: CapitalWorkInProgress has ZERO dimensioned rows anywhere in "
              "as_db — no ageing-schedule breakdown exists. The >3-year-overdue branch "
              "is NOT_APPLICABLE for every filing, not queried per-filing.",
    ),
    Derivation(
        "S10", FINANCIAL_FACTS, ("OtherNoncurrentAssets", "Assets"),
        formula="OtherNoncurrentAssets_T/Assets_T - OtherNoncurrentAssets_T-2/Assets_T-2 "
                "> other_noncurrent_share_rise_pp.",
    ),
    Derivation(
        "S11", FINANCIAL_FACTS,
        ("DepreciationDepletionAndAmortisationExpense", "PropertyPlantAndEquipment"),
        formula="DeprRate_t = Depreciation_t / PPE_t; "
                "delta%DeprRate < -depreciation_rate_fall_ratio while delta%PPE > 0.",
    ),
    Derivation(
        "S12", FINANCIAL_FACTS,
        ("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", "RevenueFromOperations"),
        dimensioned=True, axis=None, confirmed_absent=True,
        formula="Revenue decline YoY with zero ImpairmentLoss recognised.",
        basis="Confirmed: RevenueFromOperations has ZERO dimensioned rows in as_db — no "
              "segment axis exists on it. A true segment-level read is not available "
              "from financial_facts; a segment-level narrative corroboration would have "
              "to come from disclosures.disclosure_category='Segment Information' "
              "instead, out of scope for the numeric rule itself.",
    ),
    Derivation(
        "S13", FINANCIAL_FACTS,
        ("ProfitLossForPeriod", "RevenueFromOperations", "Assets", "Equity"),
        formula="ROE = (PAT/Revenue) x (Revenue/Assets) x (Assets/Equity); flag when the "
                "equity-multiplier term accounts for > leverage_driven_roe_share of the "
                "period-over-period ROE change.",
    ),
    Derivation(
        "S14", FINANCIAL_FACTS,
        ("BorrowingsCurrent", "BorrowingsNoncurrent", "CurrentAssets", "CurrentLiabilities", "Assets"),
        formula="BorrowingsCurrent/(BorrowingsCurrent+BorrowingsNoncurrent) > "
                "short_term_debt_share OR (CurrentAssets < CurrentLiabilities AND "
                "delta(Assets-CurrentAssets) > 0).",
    ),
    Derivation(
        "S15", FINANCIAL_FACTS,
        ("FinanceCosts", "BorrowingsCurrent", "BorrowingsNoncurrent"),
        formula="|delta%TotalBorrowings - delta%FinanceCosts| > "
                "finance_cost_borrowing_divergence_pp.",
    ),
    Derivation(
        "S16", FINANCIAL_FACTS,
        ("ProvisionsCurrent", "ProvisionsNoncurrent", "AdjustmentsForProvisionsCurrent",
         "ExcessProvisionsWrittenBack", "ProfitBeforeTax"),
        formula="ExcessProvisionsWrittenBack / |ProfitBeforeTax| > "
                "provision_reversal_share_of_pbt (single-period proxy); "
                "delta(TotalProvisions)/TotalProvisions_T-1 > provision_movement_ratio "
                "requires a prior-period balance.",
    ),
    Derivation(
        "S17", DISCLOSURES, ("SignificantAccountingJudgementsAndEstimatesTextBlock",),
        formula="Structural: filter disclosures to "
                "disclosure_category='Accounting Policy' AND concept_name in the "
                "registered set FIRST; only within that narrowed, already-structurally-"
                "qualified set does any language classification happen (never a bare "
                "regex over the whole disclosures table).",
        basis="disclosure_category='Accounting Policy' confirmed to exist as a real, "
              "structured category in as_db.",
    ),
    Derivation(
        "S18", FINANCIAL_FACTS,
        ("OtherIncome", "ProfitBeforeTax", "CashFlowsFromUsedInOperatingActivities"),
        formula="OtherIncome/ProfitBeforeTax > other_income_to_pbt_share AND "
                "OCF < ProfitBeforeTax.",
    ),
    Derivation(
        "S19", FINANCIAL_FACTS, ("OtherIncome", "RevenueFromOperations"),
        formula="OtherIncome/Revenue > other_income_to_revenue_share AND "
                "(delta%OtherIncome - delta%Revenue) > other_income_growth_gap_pp.",
    ),
    Derivation(
        "S20", FINANCIAL_FACTS,
        ("RevenueFromOperations", "OtherIncome",
         "CapitalSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
         "RevenueSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
         "IncomeGovernmentGrantsSubsidies"),
        formula="(Grants+Subsidies) / (Revenue+OtherIncome) > government_support_share.",
    ),
    Derivation(
        "S21", FINANCIAL_FACTS,
        ("DeferredGovernmentGrantsNoncurrent", "DeferredGovernmentGrantsCurrent"),
        formula="delta(UnspentGrants)/UnspentGrants_T-1 > unspent_grant_growth_ratio.",
    ),
    Derivation(
        "S22", DISCLOSURES,
        ("DescriptionOfAccountingPolicyForRecognitionOfRevenueExplanatory",),
        formula="Business-model-level classification, corroborated from the Business "
                "Profile block's revenue-model field (Sec 5.2) where available — this "
                "signal reads, never re-derives, that classification.",
        basis="Regulated/administered pricing is an entity-level qualitative "
              "classification, not a balance-sheet line item; not independently "
              "derivable from financial_facts.",
    ),
    Derivation(
        "S23", FINANCIAL_FACTS, ("ContingentLiabilities", "Equity"),
        formula="ContingentLiabilities / Equity > contingent_liabilities_to_equity_share.",
    ),
    Derivation(
        "S24", FINANCIAL_FACTS,
        ("Guarantees", "BalancesHeldWithBanksToExtentHeldAsGuarantees", "Equity"),
        formula="Guarantees / Equity > guarantees_to_equity_share.",
    ),
    Derivation(
        "S25", FINANCIAL_FACTS, ("NoncurrentInvestments", "CurrentInvestments", "Assets"),
        formula="(NoncurrentInvestments+CurrentInvestments) / Assets > "
                "investment_to_assets_share.",
    ),
    Derivation(
        "S26", FINANCIAL_FACTS, ("OtherComprehensiveIncome", "Equity"),
        formula="|OtherComprehensiveIncome| / Equity > oci_to_equity_share.",
    ),
    Derivation(
        "S27", FINANCIAL_FACTS,
        ("DividendIncomeNoncurrentInvestmentsFromSubsidiaries",
         "DividendIncomeCurrentInvestmentsFromSubsidiaries", "ProfitBeforeTax"),
        formula="DividendIncome / ProfitBeforeTax > dividend_income_to_pbt_share.",
    ),
)

_BY_ID: dict[str, Derivation] = {d.signal_id: d for d in DERIVATIONS}
if len(_BY_ID) != len(DERIVATIONS):
    raise RuntimeError("duplicate derivation in xbrl_signal_derivations")


def get(signal_id: str) -> Derivation:
    try:
        return _BY_ID[signal_id]
    except KeyError:
        raise KeyError(f"no derivation registered for {signal_id!r}") from None


def all_derivations() -> tuple[Derivation, ...]:
    return DERIVATIONS
