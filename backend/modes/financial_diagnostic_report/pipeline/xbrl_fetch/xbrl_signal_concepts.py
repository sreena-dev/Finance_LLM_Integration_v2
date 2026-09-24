"""
Additional MCA Ind-AS XBRL concept names the S01-S27 signal library needs, beyond what
`xbrl_risk_concepts.py` (Block 6's cluster-concept registry) already declares.

WHY A SEPARATE FILE RATHER THAN EXTENDING `xbrl_risk_concepts.py`
-------------------------------------------------------------------------------------
`xbrl_risk_concepts.py` backs the LIVE `xbrl_risk_signals.py` production path and is left
untouched per the user's explicit instruction. Where a concept this library needs is
already declared there (e.g. `CurrentAssets`, `TradeReceivablesCurrent`), it is imported
from there, not re-declared — this file only adds names that file does not have:
COGS components, related-party transaction amounts, contingent liabilities, guarantees,
OCI, dividend income, and provisions-movement detail.

Every name below was confirmed present with real data in `as_db.financial_facts` by a
direct query before being added here (see the implementation plan for the verification
run) — nothing here is guessed from the taxonomy alone.
"""
from __future__ import annotations

# ---- S01/S02 cost-of-goods-sold components (not in xbrl_risk_concepts.py) ----------
COGS_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "CostOfMaterialsConsumed",
    "PurchasesOfStockInTrade",
    "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
)

# ---- S07 unbilled contract assets --------------------------------------------------
CONTRACT_ASSET_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "GrossAmountDueFromCustomersForContractWorkAsAssets",
)

# ---- S08 related-party receivable concentration ------------------------------------
# Confirmed 100% dimensioned (0 undimensioned rows), by the `RelatedParty` /
# `CategoriesOfRelatedParties` axes — see xbrl_signal_derivations.py for the exact axis
# used at query time.
RELATED_PARTY_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "AmountsReceivableRelatedPartyTransactions",
)

# ---- S16 provision movement detail --------------------------------------------------
PROVISION_MOVEMENT_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "AdjustmentsForProvisionsCurrent",
    "ExcessProvisionsWrittenBack",
)

# ---- S20 government grant/subsidy income (income-statement form, distinct from the ----
# ---- balance-sheet DeferredGovernmentGrants* concepts xbrl_risk_concepts.py has) ----
GOVERNMENT_INCOME_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "CapitalSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
    "RevenueSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
    "IncomeGovernmentGrantsSubsidies",
)

# ---- S21 unspent/deferred grants (current-year balance; NoncurrentInvestments's ----
# ---- Noncurrent sibling already lives in xbrl_risk_concepts.py) --------------------
DEFERRED_GRANT_CURRENT_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "DeferredGovernmentGrantsCurrent",
)

# ---- S23 contingent liabilities vs net worth ----------------------------------------
# `ContingentLiabilities` already exists in xbrl_risk_concepts.py's RISK_DISCLOSURE_
# CONCEPTS as a narrative tag; confirmed it ALSO exists as a genuine numeric fact
# (1,587 rows / 803 docs) — used here as numeric, imported directly by name in
# xbrl_signal_derivations.py rather than duplicated.

# ---- S24 financial guarantees for group entities -------------------------------------
GUARANTEE_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "Guarantees",
    "BalancesHeldWithBanksToExtentHeldAsGuarantees",
)

# ---- S26 fair-value OCI volatility -----------------------------------------------------
OCI_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "OtherComprehensiveIncome",
)

# ---- S27 investment income dependency ---------------------------------------------------
DIVIDEND_INCOME_NUMERIC_CONCEPTS: tuple[str, ...] = (
    "DividendIncomeNoncurrentInvestmentsFromSubsidiaries",
    "DividendIncomeCurrentInvestmentsFromSubsidiaries",
)

ALL_SIGNAL_NUMERIC_CONCEPTS: tuple[str, ...] = tuple(dict.fromkeys(
    COGS_NUMERIC_CONCEPTS
    + CONTRACT_ASSET_NUMERIC_CONCEPTS
    + RELATED_PARTY_NUMERIC_CONCEPTS
    + PROVISION_MOVEMENT_NUMERIC_CONCEPTS
    + GOVERNMENT_INCOME_NUMERIC_CONCEPTS
    + DEFERRED_GRANT_CURRENT_NUMERIC_CONCEPTS
    + GUARANTEE_NUMERIC_CONCEPTS
    + OCI_NUMERIC_CONCEPTS
    + DIVIDEND_INCOME_NUMERIC_CONCEPTS
    + ("ContingentLiabilities",)
))


def all_signal_numeric_concepts() -> tuple[str, ...]:
    return ALL_SIGNAL_NUMERIC_CONCEPTS
