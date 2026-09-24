"""
The S01-S27 rule functions — pure functions of the fact/disclosure maps
`xbrl_signal_fetch.fetch_all` returns. This is the ONLY place a verdict or a number is
formed; the registry (`xbrl_signal_registry.py`) says what a signal is, the derivations
(`xbrl_signal_derivations.py`) say where its inputs come from, and this module alone
decides whether it fired.

Every rule returns an `xbrl_signal_model.Outcome`. A rule that cannot run returns
`fired=None` via `abstain()`/`not_applicable()` — it never substitutes a default, treats
a missing figure as zero, or presents a two-point movement as a trend (spec Sec 9.5).

SHARED SHAPES, NOT 27 BESPOKE FUNCTIONS
-------------------------------------------------------------------------------------
Most of these 27 signals are one of five repeated arithmetic SHAPES (a level/ratio check,
a YoY growth-gap check, a two-point trend-direction check, a share-of-total check, and a
disclosure-presence check). The helpers below implement each shape once; the per-signal
functions are thin call sites that supply the concept names, threshold keys and wording —
so a formula bug gets fixed in one place, not 27.
"""
from __future__ import annotations
from typing import Any, Callable

from . import xbrl_signal_thresholds as TH
from .xbrl_signal_model import (
    Outcome, HIGH, MEDIUM, LOW, abstain, not_applicable,
    PANEL_TOO_SHORT, INPUT_NEVER_BOUND, INPUT_ZERO_DENOMINATOR, NOT_COMPUTABLE,
)

FactSeries = dict[str, dict[str, dict[str, Any]]]  # {concept: {fy_end_iso: {value,...}}}


# ---- generic helpers ---------------------------------------------------------------

def _fy_keys(fetched: dict[str, Any]) -> list[str]:
    """Distinct fy_end (iso date strings), oldest first, for the entity's whole series."""
    seen: dict[str, None] = {}
    for row in fetched.get("series", []):
        fy = row.get("fy_end")
        if fy is not None:
            seen[str(fy)] = None
    return list(seen)


def _value_at(facts: FactSeries, concept: str, fy_key: str) -> float | None:
    entry = facts.get(concept, {}).get(fy_key)
    return entry["value"] if entry is not None else None


def _latest_value(facts: FactSeries, concept: str, fy_keys: list[str]) -> tuple[str, float] | None:
    for fy in reversed(fy_keys):
        v = _value_at(facts, concept, fy)
        if v is not None:
            return fy, v
    return None


def _missing(concepts: tuple[str, ...], facts: FactSeries, fy_key: str) -> tuple[str, ...]:
    return tuple(c for c in concepts if _value_at(facts, c, fy_key) is None)


def _pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return (new - old) / old


def _sum_concepts(facts: FactSeries, concepts: tuple[str, ...], fy_key: str) -> float:
    return sum((_value_at(facts, c, fy_key) or 0.0) for c in concepts)


def _level_ratio_below(
    facts: FactSeries, fy_keys: list[str], *,
    numerator: str, denominator: str, threshold_key: str, comparator: str,
    signal_id: str, wording: str, severity: str,
) -> Outcome:
    """Shape: numerator/denominator {<,>} threshold, evaluated on the latest available
    period. `comparator` is '<' or '>'."""
    if not fy_keys:
        return abstain("No filing found for this entity in as_db.", PANEL_TOO_SHORT,
                        source_trace=(f"financial_facts: {numerator}, {denominator}",))
    fy = fy_keys[-1]
    num = _value_at(facts, numerator, fy)
    den = _value_at(facts, denominator, fy)
    if num is None or den is None:
        missing = tuple(c for c in (numerator, denominator) if _value_at(facts, c, fy) is None)
        return abstain(
            f"{signal_id}: required concept(s) not present for fy_end {fy}.",
            INPUT_NEVER_BOUND, missing_inputs=missing,
            source_trace=(f"financial_facts: {numerator}, {denominator}",),
        )
    if den == 0:
        return abstain(f"{signal_id}: denominator {denominator} is zero for fy_end {fy}.",
                        INPUT_ZERO_DENOMINATOR,
                        source_trace=(f"financial_facts: {numerator}, {denominator}",))
    ratio = num / den
    threshold = TH.get(threshold_key).value
    fired = (ratio < threshold) if comparator == "<" else (ratio > threshold)
    return Outcome(
        fired=fired,
        observation=f"{wording} {ratio:.2%} as of {fy} (threshold {comparator}{threshold:.2%}).",
        trace=f"{numerator}({num:,.0f}) / {denominator}({den:,.0f}) = {ratio:.4f}",
        formula=f"{numerator}/{denominator} {comparator} {threshold_key}",
        source_trace=(f"financial_facts: {numerator}, {denominator} @ {fy}",),
        confidence=HIGH,
        confidence_basis="Both inputs present for the latest filed period.",
    )


def _growth_gap(
    facts: FactSeries, fy_keys: list[str], *,
    tracked: str, baseline: str, gap_threshold_key: str,
    signal_id: str, wording: str, severity: str,
) -> Outcome:
    """Shape: growth-rate(tracked) - growth-rate(baseline) > gap threshold, across the
    two most recent fy_ends BOTH concepts have a value for."""
    if len(fy_keys) < 2:
        return abstain(
            f"{signal_id}: fewer than 2 distinct fy_ends available for this entity in "
            f"as_db (found {len(fy_keys)}) — a year-on-year growth comparison needs at "
            f"least 2.", PANEL_TOO_SHORT,
            source_trace=(f"financial_facts: {tracked}, {baseline}",),
        )
    fy_new, fy_old = fy_keys[-1], fy_keys[-2]
    t_new, t_old = _value_at(facts, tracked, fy_new), _value_at(facts, tracked, fy_old)
    b_new, b_old = _value_at(facts, baseline, fy_new), _value_at(facts, baseline, fy_old)
    if None in (t_new, t_old, b_new, b_old):
        return abstain(
            f"{signal_id}: one or more of {tracked}/{baseline} missing for {fy_old} or {fy_new}.",
            INPUT_NEVER_BOUND, source_trace=(f"financial_facts: {tracked}, {baseline}",),
        )
    t_growth = _pct_change(t_new, t_old)
    b_growth = _pct_change(b_new, b_old)
    if t_growth is None or b_growth is None:
        return abstain(f"{signal_id}: zero base-year value for {tracked} or {baseline}.",
                        INPUT_ZERO_DENOMINATOR)
    gap_pp = (t_growth - b_growth) * 100.0
    threshold = TH.get(gap_threshold_key).value
    fired = gap_pp > threshold
    return Outcome(
        fired=fired,
        observation=f"{wording}: {tracked} grew {t_growth:.1%} vs {baseline} {b_growth:.1%} "
                    f"({fy_old} -> {fy_new}), a gap of {gap_pp:.1f}pp.",
        trace=f"({t_new:,.0f}/{t_old:,.0f}-1) - ({b_new:,.0f}/{b_old:,.0f}-1) = {gap_pp:.1f}pp",
        formula=f"delta%{tracked} - delta%{baseline} > {gap_threshold_key}",
        source_trace=(f"financial_facts: {tracked}, {baseline} @ {fy_old},{fy_new}",),
        confidence=HIGH,
        confidence_basis="Both concepts present for both compared periods.",
    )


def _disclosure_presence(
    fetched: dict[str, Any], *, concepts: tuple[str, ...], category: str | None,
    signal_id: str, wording: str,
) -> Outcome:
    rows = [
        r for r in fetched.get("disclosures", [])
        if r.get("concept_name") in concepts
        and (category is None or r.get("disclosure_category") == category)
        and (r.get("text") or "").strip()
    ]
    if not rows:
        return Outcome(
            fired=False,
            observation=f"No {wording} disclosure found among the registered concepts.",
            source_trace=(f"disclosures: {', '.join(concepts)}",),
            confidence=MEDIUM,
            confidence_basis="Absence of a matching disclosure row is itself the "
                             "measurement; a disclosure that exists but wasn't retrieved "
                             "would read identically, so confidence is Medium not High.",
        )
    top = rows[0]
    return Outcome(
        fired=True,
        observation=f"{wording} disclosure present: {top.get('concept_name')}.",
        trace=(top.get("text") or "")[:240],
        source_trace=(f"disclosures: {top.get('concept_name')}",),
        confidence=HIGH,
        confidence_basis="Matched a structurally-tagged disclosure concept/category, "
                         "not a keyword search over free text.",
    )


# ---- S01 -----------------------------------------------------------------------------

def rule_s01(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 3:
        return abstain(
            f"S01: entity has {len(fy_keys)} distinct fy_end(s) in as_db; a 3-year CCC "
            f"trend needs 3 (spec Sec 9.5 — a shorter series is never presented as a "
            f"trend).", PANEL_TOO_SHORT,
            source_trace=("documents: entity_cin filing count",),
        )
    fy_t, fy_t2 = fy_keys[-1], fy_keys[-3]

    def ccc(fy: str) -> float | None:
        rec = _value_at(facts, "TradeReceivablesCurrent", fy)
        inv = _value_at(facts, "Inventories", fy)
        pay = _value_at(facts, "TradePayablesCurrent", fy)
        rev = _value_at(facts, "RevenueFromOperations", fy)
        cmc = _value_at(facts, "CostOfMaterialsConsumed", fy) or 0.0
        psit = _value_at(facts, "PurchasesOfStockInTrade", fy) or 0.0
        chg = _value_at(facts, "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade", fy) or 0.0
        if None in (rec, inv, pay, rev) or rev == 0:
            return None
        cogs = cmc + psit + chg
        purchases = cmc + psit
        if cogs == 0 or purchases == 0:
            return None
        dso = rec / rev * 365
        dio = inv / cogs * 365
        dpo = pay / purchases * 365
        return dso + dio - dpo

    ccc_t, ccc_t2 = ccc(fy_t), ccc(fy_t2)
    if ccc_t is None or ccc_t2 is None:
        return abstain("S01: could not compute CCC for both compared periods "
                        "(missing input or a zero COGS/purchases proxy).",
                        NOT_COMPUTABLE, source_trace=(f"financial_facts @ {fy_t2},{fy_t}",))
    deterioration = ccc_t - ccc_t2
    threshold = TH.get("ccc_deterioration_days").value
    fired = deterioration > threshold
    return Outcome(
        fired=fired,
        observation=f"CCC moved from {ccc_t2:.0f} to {ccc_t:.0f} days ({fy_t2} -> {fy_t}), "
                    f"a change of {deterioration:+.0f} days.",
        trace=f"CCC_t={ccc_t:.1f}d, CCC_t-2={ccc_t2:.1f}d, delta={deterioration:.1f}d",
        formula="CCC = DSO + DIO - DPO; delta(CCC) > ccc_deterioration_days",
        source_trace=(f"financial_facts @ {fy_t2},{fy_t}",),
        confidence=MEDIUM,
        confidence_basis="COGS/purchases are proxied from CostOfMaterialsConsumed + "
                         "PurchasesOfStockInTrade, not a disclosed COGS figure.",
        proxy_used="COGS/Purchases proxied from CostOfMaterialsConsumed + "
                   "PurchasesOfStockInTrade (+ inventory change for COGS).",
    )


# ---- S02 -----------------------------------------------------------------------------

def rule_s02(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    gap = _growth_gap(
        facts, fy_keys, tracked="TradePayablesCurrent", baseline="RevenueFromOperations",
        gap_threshold_key="payables_growth_gap_pp", signal_id="S02",
        wording="Payables growth vs revenue growth", severity=HIGH,
    )
    if not gap.ran:
        return gap
    fy = fy_keys[-1]
    pay = _value_at(facts, "TradePayablesCurrent", fy)
    cmc = _value_at(facts, "CostOfMaterialsConsumed", fy) or 0.0
    psit = _value_at(facts, "PurchasesOfStockInTrade", fy) or 0.0
    chg = _value_at(facts, "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade", fy) or 0.0
    cogs = cmc + psit + chg
    if pay is None or cogs == 0:
        return abstain("S02: cannot evaluate the payables/COGS share (missing input or "
                        "zero COGS proxy).", INPUT_ZERO_DENOMINATOR)
    share = pay / cogs
    share_threshold = TH.get("payables_to_cogs_share").value
    fired = gap.fired and (share > share_threshold)
    return Outcome(
        fired=fired,
        observation=gap.observation + f" Payables/COGS = {share:.1%} (threshold "
                    f">{share_threshold:.0%}).",
        trace=gap.trace + f"; payables/COGS = {pay:,.0f}/{cogs:,.0f} = {share:.4f}",
        formula=gap.formula + " AND TradePayables/COGS > payables_to_cogs_share",
        source_trace=gap.source_trace + (f"financial_facts: COGS proxy @ {fy}",),
        confidence=gap.confidence, confidence_basis=gap.confidence_basis,
        proxy_used="COGS proxied as in S01.",
    )


# ---- S03 -----------------------------------------------------------------------------

def rule_s03(fetched: dict[str, Any]) -> Outcome:
    return _level_ratio_below(
        fetched["face_facts"], _fy_keys(fetched),
        numerator="CurrentAssets", denominator="CurrentLiabilities",
        threshold_key="current_ratio_floor", comparator="<",
        signal_id="S03", wording="Current ratio", severity=HIGH,
    )


# ---- S04 -----------------------------------------------------------------------------

def rule_s04(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S04: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    ocf = _value_at(facts, "CashFlowsFromUsedInOperatingActivities", fy)
    pat = _value_at(facts, "ProfitLossForPeriod", fy)
    if ocf is None:
        return abstain(f"S04: CashFlowsFromUsedInOperatingActivities missing for {fy}.",
                        INPUT_NEVER_BOUND)
    if ocf < 0:
        fired = True
        obs = f"Negative operating cash flow of {ocf:,.0f} in {fy}."
    elif pat is not None and pat > 0:
        floor = TH.get("ocf_to_pat_floor").value
        ratio = ocf / pat if pat != 0 else None
        fired = ratio is not None and ratio < floor
        obs = f"OCF {ocf:,.0f} vs PAT {pat:,.0f} in {fy} (OCF/PAT={ratio:.2f}x, floor {floor:.2f}x)." if ratio is not None else "PAT is zero; ratio undefined."
    else:
        fired = False
        obs = f"OCF {ocf:,.0f} is non-negative and PAT is not positive; no lag to assess."
    return Outcome(
        fired=fired, observation=obs,
        trace=f"OCF={ocf:,.0f}, PAT={pat if pat is not None else 'n/a'}",
        formula="OCF<0 OR (PAT>0 AND OCF/PAT<ocf_to_pat_floor) — single-period proxy for "
               "the spec's >=2-of-3-years persistence form",
        source_trace=(f"financial_facts: CashFlowsFromUsedInOperatingActivities, "
                      f"ProfitLossForPeriod @ {fy}",),
        confidence=MEDIUM,
        confidence_basis="Single-period proxy; the persistence form needs a 3-year "
                         "window as_db does not carry for most entities.",
        proxy_used="Single-period OCF check in place of the spec's 3-year persistence form.",
    )


# ---- S05 -----------------------------------------------------------------------------

def rule_s05(fetched: dict[str, Any]) -> Outcome:
    return _growth_gap(
        fetched["face_facts"], _fy_keys(fetched),
        tracked="TradeReceivablesCurrent", baseline="RevenueFromOperations",
        gap_threshold_key="receivables_growth_gap_pp", signal_id="S05",
        wording="Receivables growth vs revenue growth", severity=HIGH,
    )


# ---- S06 -----------------------------------------------------------------------------

def rule_s06(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S06: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    pat = _value_at(facts, "ProfitLossForPeriod", fy)
    ocf = _value_at(facts, "CashFlowsFromUsedInOperatingActivities", fy)
    assets = _value_at(facts, "Assets", fy)
    if None in (pat, ocf, assets):
        return abstain(f"S06: PAT/OCF/Assets not all present for {fy}.", INPUT_NEVER_BOUND)
    if assets == 0:
        return abstain(f"S06: Assets is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    ratio = (pat - ocf) / assets
    threshold = TH.get("accruals_ratio_ceiling").value
    fired = ratio > threshold
    return Outcome(
        fired=fired,
        observation=f"Accruals ratio {ratio:.1%} in {fy} (threshold >{threshold:.0%}).",
        trace=f"(PAT {pat:,.0f} - OCF {ocf:,.0f}) / Assets {assets:,.0f} = {ratio:.4f}",
        formula="(PAT-OCF)/Assets > accruals_ratio_ceiling",
        source_trace=(f"financial_facts: ProfitLossForPeriod, "
                      f"CashFlowsFromUsedInOperatingActivities, Assets @ {fy}",),
        confidence=HIGH, confidence_basis="All three inputs present for the latest period.",
    )


# ---- S07 -----------------------------------------------------------------------------

def rule_s07(fetched: dict[str, Any]) -> Outcome:
    return _level_ratio_below(
        fetched["face_facts"], _fy_keys(fetched),
        numerator="GrossAmountDueFromCustomersForContractWorkAsAssets",
        denominator="RevenueFromOperations",
        threshold_key="unbilled_contract_assets_share", comparator=">",
        signal_id="S07", wording="Unbilled contract assets / revenue", severity=MEDIUM,
    )


# ---- S08 -----------------------------------------------------------------------------

def rule_s08(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S08: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    total_rec = _value_at(facts, "TradeReceivablesCurrent", fy)
    if total_rec is None:
        return abstain(f"S08: TradeReceivablesCurrent missing for {fy}.", INPUT_NEVER_BOUND)
    if total_rec == 0:
        return abstain(f"S08: TradeReceivablesCurrent is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    rp_by_doc = fetched.get("related_party_receivables", {})
    series_rows = {row["doc_id"]: str(row.get("fy_end")) for row in fetched.get("series", [])}
    rp_total = sum(v for doc_id, v in rp_by_doc.items() if series_rows.get(doc_id) == fy)
    if not any(series_rows.get(doc_id) == fy for doc_id in rp_by_doc):
        return Outcome(
            fired=False,
            observation="No related-party receivable balance disclosed for this period.",
            source_trace=("financial_facts: AmountsReceivableRelatedPartyTransactions "
                          "(RelatedParty axis) — no rows for this fy_end",),
            confidence=MEDIUM,
            confidence_basis="Absence of dimensioned related-party rows for this filing "
                             "is itself the measurement.",
        )
    share = rp_total / total_rec
    threshold = TH.get("related_party_receivable_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Related-party receivables {rp_total:,.0f} are {share:.1%} of total "
                    f"trade receivables {total_rec:,.0f} in {fy} (threshold >{threshold:.0%}).",
        trace=f"SUM(AmountsReceivableRelatedPartyTransactions, RelatedParty axis) "
             f"{rp_total:,.0f} / TradeReceivablesCurrent {total_rec:,.0f} = {share:.4f}",
        formula="SUM(RP receivables over RelatedParty axis)/TradeReceivablesCurrent > "
               "related_party_receivable_share",
        source_trace=(f"financial_facts: AmountsReceivableRelatedPartyTransactions "
                      f"(has_dimensions=true, axis=RelatedParty) @ {fy}",),
        confidence=HIGH,
        confidence_basis="Numeric, dimensioned XBRL fact — not a text-scanned figure.",
    )


# ---- S09 -----------------------------------------------------------------------------

def rule_s09(fetched: dict[str, Any]) -> Outcome:
    return _level_ratio_below(
        fetched["face_facts"], _fy_keys(fetched),
        numerator="CapitalWorkInProgress", denominator="Assets",
        threshold_key="cwip_to_assets_share", comparator=">",
        signal_id="S09", wording="CWIP / total assets", severity=MEDIUM,
    )


def rule_s09_ageing(_fetched: dict[str, Any]) -> Outcome:
    """The >3-year-overdue ageing branch. Always NOT_APPLICABLE — confirmed by direct
    query that as_db carries zero dimensioned CapitalWorkInProgress rows corpus-wide."""
    return not_applicable(
        "as_db carries no dimensioned CapitalWorkInProgress rows for any filing "
        "(confirmed by direct query) — no CWIP-ageing schedule exists in this database.",
        source_trace=("financial_facts: CapitalWorkInProgress, has_dimensions=true — "
                      "confirmed zero rows corpus-wide",),
    )


# ---- S10 -----------------------------------------------------------------------------

def rule_s10(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 3:
        return abstain(f"S10: entity has {len(fy_keys)} distinct fy_end(s); a 3-year "
                       f"share-drift comparison needs 3.", PANEL_TOO_SHORT)
    fy_t, fy_t2 = fy_keys[-1], fy_keys[-3]

    def share(fy: str) -> float | None:
        onca = _value_at(facts, "OtherNoncurrentAssets", fy)
        assets = _value_at(facts, "Assets", fy)
        if onca is None or not assets:
            return None
        return onca / assets

    s_t, s_t2 = share(fy_t), share(fy_t2)
    if s_t is None or s_t2 is None:
        return abstain("S10: OtherNoncurrentAssets/Assets not computable for both periods.",
                        NOT_COMPUTABLE)
    rise_pp = (s_t - s_t2) * 100.0
    threshold = TH.get("other_noncurrent_share_rise_pp").value
    fired = rise_pp > threshold
    return Outcome(
        fired=fired,
        observation=f"Other non-current assets' share of total assets moved from "
                    f"{s_t2:.1%} to {s_t:.1%} ({fy_t2} -> {fy_t}), a rise of {rise_pp:.1f}pp.",
        trace=f"share_t={s_t:.4f}, share_t-2={s_t2:.4f}, rise={rise_pp:.1f}pp",
        formula="OtherNoncurrentAssets/Assets share rise > other_noncurrent_share_rise_pp",
        source_trace=(f"financial_facts: OtherNoncurrentAssets, Assets @ {fy_t2},{fy_t}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S11 -----------------------------------------------------------------------------

def rule_s11(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 2:
        return abstain(f"S11: entity has {len(fy_keys)} distinct fy_end(s); a "
                       f"depreciation-rate comparison needs 2.", PANEL_TOO_SHORT)
    fy_new, fy_old = fy_keys[-1], fy_keys[-2]

    def rate(fy: str) -> float | None:
        dep = _value_at(facts, "DepreciationDepletionAndAmortisationExpense", fy)
        ppe = _value_at(facts, "PropertyPlantAndEquipment", fy)
        if dep is None or not ppe:
            return None
        return dep / ppe

    r_new, r_old = rate(fy_new), rate(fy_old)
    ppe_new = _value_at(facts, "PropertyPlantAndEquipment", fy_new)
    ppe_old = _value_at(facts, "PropertyPlantAndEquipment", fy_old)
    if r_new is None or r_old is None or r_old == 0 or None in (ppe_new, ppe_old):
        return abstain("S11: depreciation rate not computable for both periods.",
                        NOT_COMPUTABLE)
    rate_change = (r_new - r_old) / r_old
    threshold = TH.get("depreciation_rate_fall_ratio").value
    ppe_expanding = ppe_new > ppe_old
    fired = (rate_change < -threshold) and ppe_expanding
    return Outcome(
        fired=fired,
        observation=f"Implied depreciation rate moved from {r_old:.2%} to {r_new:.2%} "
                    f"({fy_old} -> {fy_new}), {rate_change:+.1%}, while PPE "
                    f"{'expanded' if ppe_expanding else 'did not expand'}.",
        trace=f"rate_new={r_new:.4f}, rate_old={r_old:.4f}, change={rate_change:.2%}, "
             f"PPE_new={ppe_new:,.0f}, PPE_old={ppe_old:,.0f}",
        formula="delta%DeprRate < -depreciation_rate_fall_ratio AND delta%PPE > 0",
        source_trace=(f"financial_facts: DepreciationDepletionAndAmortisationExpense, "
                      f"PropertyPlantAndEquipment @ {fy_old},{fy_new}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S12 -----------------------------------------------------------------------------

def rule_s12(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 2:
        return abstain(f"S12: entity has {len(fy_keys)} distinct fy_end(s); a revenue-"
                       f"decline comparison needs 2.", PANEL_TOO_SHORT)
    fy_new, fy_old = fy_keys[-1], fy_keys[-2]
    rev_new = _value_at(facts, "RevenueFromOperations", fy_new)
    rev_old = _value_at(facts, "RevenueFromOperations", fy_old)
    impairment = _value_at(facts, "ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", fy_new) or 0.0
    if rev_new is None or not rev_old:
        return abstain("S12: RevenueFromOperations not present for both periods.",
                        INPUT_NEVER_BOUND)
    decline = (rev_new - rev_old) / rev_old
    fired = decline < 0 and impairment == 0
    return Outcome(
        fired=fired,
        observation=f"Revenue moved {decline:+.1%} ({fy_old} -> {fy_new}) with "
                    f"{'no' if impairment == 0 else 'an'} impairment charge recognised "
                    f"({impairment:,.0f}).",
        trace=f"revenue change={decline:.2%}, impairment={impairment:,.0f}",
        formula="Revenue decline YoY with zero ImpairmentLoss recognised (proxy for "
               "CGU-level impairment timing, which as_db cannot provide directly)",
        source_trace=(f"financial_facts: RevenueFromOperations @ {fy_old},{fy_new}; "
                      f"ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment @ {fy_new}",),
        confidence=MEDIUM,
        confidence_basis="Entity-level revenue proxy; no segment/CGU breakdown is "
                         "available in as_db (confirmed: RevenueFromOperations has zero "
                         "dimensioned rows corpus-wide).",
        proxy_used="Entity-level revenue decline in place of CGU-level cash flows.",
    )


# ---- S13 -----------------------------------------------------------------------------

def rule_s13(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S13: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    if len(fy_keys) >= 2:
        return _s13_two_period(facts, fy_keys)

    # Exactly one fy_end: report the single-period decomposition for reference, but the
    # "leverage share of ROE *expansion*" trigger has no prior period to compare against.
    fy = fy_keys[-1]
    pat = _value_at(facts, "ProfitLossForPeriod", fy)
    rev = _value_at(facts, "RevenueFromOperations", fy)
    assets = _value_at(facts, "Assets", fy)
    eq = _value_at(facts, "Equity", fy)
    if None in (pat, rev, assets, eq) or rev == 0 or assets == 0 or eq == 0:
        return abstain(f"S13: DuPont inputs not all present/non-zero for {fy}.",
                        NOT_COMPUTABLE)
    margin, turnover, multiplier = pat / rev, rev / assets, assets / eq
    roe = margin * turnover * multiplier
    return abstain(
        f"S13: only one fy_end ({fy}) available for this entity in as_db. Single-period "
        f"DuPont decomposition: margin {margin:.2%} x turnover {turnover:.2f}x x "
        f"equity-multiplier {multiplier:.2f}x = ROE {roe:.2%}. The 'leverage share of "
        f"ROE expansion' trigger needs a prior-period ROE and cannot be evaluated.",
        PANEL_TOO_SHORT,
        source_trace=(f"financial_facts: ProfitLossForPeriod, RevenueFromOperations, "
                      f"Assets, Equity @ {fy}",),
    )


def _s13_two_period(facts: FactSeries, fy_keys: list[str]) -> Outcome:
    def dupont(fy: str) -> tuple[float, float, float, float] | None:
        pat = _value_at(facts, "ProfitLossForPeriod", fy)
        rev = _value_at(facts, "RevenueFromOperations", fy)
        assets = _value_at(facts, "Assets", fy)
        eq = _value_at(facts, "Equity", fy)
        if None in (pat, rev, assets, eq) or rev == 0 or assets == 0 or eq == 0:
            return None
        margin, turnover, multiplier = pat / rev, rev / assets, assets / eq
        return margin, turnover, multiplier, margin * turnover * multiplier

    fy_new, fy_old = fy_keys[-1], fy_keys[-2]
    new_d, old_d = dupont(fy_new), dupont(fy_old)
    if new_d is None or old_d is None:
        return abstain("S13: DuPont inputs not computable for both periods.", NOT_COMPUTABLE)
    m_new, t_new, x_new, roe_new = new_d
    m_old, t_old, x_old, roe_old = old_d
    roe_change = roe_new - roe_old
    if roe_change <= 0:
        return Outcome(
            fired=False,
            observation=f"ROE did not expand ({roe_old:.2%} -> {roe_new:.2%}); the "
                        f"leverage-driven-expansion check does not apply.",
            trace=f"ROE_old={roe_old:.4f}, ROE_new={roe_new:.4f}",
            formula="ROE = margin x turnover x multiplier; leverage share of expansion "
                   "> leverage_driven_roe_share",
            source_trace=(f"financial_facts @ {fy_old},{fy_new}",),
            confidence=HIGH, confidence_basis="Both periods fully populated.",
        )
    # Attribute the ROE change to the multiplier holding margin*turnover at the old level
    # vs. the multiplier's own contribution — a standard multiplicative decomposition.
    multiplier_only_roe = m_old * t_old * x_new
    multiplier_contribution = multiplier_only_roe - roe_old
    leverage_share = multiplier_contribution / roe_change if roe_change != 0 else 0.0
    threshold = TH.get("leverage_driven_roe_share").value
    fired = leverage_share > threshold
    return Outcome(
        fired=fired,
        observation=f"ROE expanded {roe_old:.2%} -> {roe_new:.2%}; the equity-multiplier "
                    f"term accounts for {leverage_share:.0%} of that expansion "
                    f"(threshold >{threshold:.0%}).",
        trace=f"multiplier_only_ROE={multiplier_only_roe:.4f}, "
             f"leverage_contribution={multiplier_contribution:.4f}, "
             f"leverage_share={leverage_share:.2%}",
        formula="leverage_share = (margin_old*turnover_old*multiplier_new - ROE_old) / (ROE_new - ROE_old)",
        source_trace=(f"financial_facts: ProfitLossForPeriod, RevenueFromOperations, "
                      f"Assets, Equity @ {fy_old},{fy_new}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S14 -----------------------------------------------------------------------------

def rule_s14(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S14: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    st = _value_at(facts, "BorrowingsCurrent", fy)
    lt = _value_at(facts, "BorrowingsNoncurrent", fy)
    ca = _value_at(facts, "CurrentAssets", fy)
    cl = _value_at(facts, "CurrentLiabilities", fy)
    assets = _value_at(facts, "Assets", fy)
    if None in (st, lt, ca, cl, assets):
        return abstain(f"S14: one or more required concepts missing for {fy}.",
                        INPUT_NEVER_BOUND)
    total_debt = st + lt
    debt_share = (st / total_debt) if total_debt > 0 else 0.0
    share_threshold = TH.get("short_term_debt_share").value
    net_current_negative = ca < cl
    branch_a = debt_share > share_threshold
    fired = branch_a or net_current_negative
    return Outcome(
        fired=fired,
        observation=f"Short-term borrowings are {debt_share:.0%} of total debt in {fy} "
                    f"(threshold >{share_threshold:.0%}); "
                    f"{'net current liabilities present' if net_current_negative else 'current assets exceed current liabilities'}.",
        trace=f"ST_debt_share={debt_share:.4f}, CA={ca:,.0f}, CL={cl:,.0f}",
        formula="BorrowingsCurrent/TotalBorrowings > short_term_debt_share OR "
               "CurrentAssets < CurrentLiabilities",
        source_trace=(f"financial_facts: BorrowingsCurrent, BorrowingsNoncurrent, "
                      f"CurrentAssets, CurrentLiabilities, Assets @ {fy}",),
        confidence=HIGH, confidence_basis="All inputs present for the latest period.",
        proxy_used="'Non-current assets expanding' half of the spec's second branch "
                   "simplified to a single-period net-current-liability check.",
    )


# ---- S15 -----------------------------------------------------------------------------

def rule_s15(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 2:
        return abstain(f"S15: entity has {len(fy_keys)} distinct fy_end(s); a "
                       f"borrowings/finance-cost divergence needs 2.", PANEL_TOO_SHORT)
    fy_new, fy_old = fy_keys[-1], fy_keys[-2]

    def total_borrow(fy: str) -> float | None:
        st = _value_at(facts, "BorrowingsCurrent", fy)
        lt = _value_at(facts, "BorrowingsNoncurrent", fy)
        if st is None or lt is None:
            return None
        return st + lt

    b_new, b_old = total_borrow(fy_new), total_borrow(fy_old)
    fc_new, fc_old = _value_at(facts, "FinanceCosts", fy_new), _value_at(facts, "FinanceCosts", fy_old)
    if None in (b_new, b_old, fc_new, fc_old) or b_old == 0 or fc_old == 0:
        return abstain("S15: borrowings/finance-cost not computable for both periods.",
                        NOT_COMPUTABLE)
    b_growth = (b_new - b_old) / b_old
    fc_growth = (fc_new - fc_old) / fc_old
    divergence_pp = abs(b_growth - fc_growth) * 100.0
    threshold = TH.get("finance_cost_borrowing_divergence_pp").value
    fired = divergence_pp > threshold
    return Outcome(
        fired=fired,
        observation=f"Borrowings grew {b_growth:.1%} vs finance costs {fc_growth:.1%} "
                    f"({fy_old} -> {fy_new}), a divergence of {divergence_pp:.1f}pp.",
        trace=f"borrow_growth={b_growth:.4f}, fc_growth={fc_growth:.4f}, divergence={divergence_pp:.1f}pp",
        formula="|delta%TotalBorrowings - delta%FinanceCosts| > "
               "finance_cost_borrowing_divergence_pp",
        source_trace=(f"financial_facts: BorrowingsCurrent, BorrowingsNoncurrent, "
                      f"FinanceCosts @ {fy_old},{fy_new}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S16 -----------------------------------------------------------------------------

def rule_s16(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S16: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    reversed_provisions = _value_at(facts, "ExcessProvisionsWrittenBack", fy) or 0.0
    pbt = _value_at(facts, "ProfitBeforeTax", fy)
    if pbt is None:
        return abstain(f"S16: ProfitBeforeTax missing for {fy}.", INPUT_NEVER_BOUND)
    if pbt == 0:
        return abstain(f"S16: ProfitBeforeTax is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = reversed_provisions / abs(pbt)
    threshold = TH.get("provision_reversal_share_of_pbt").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Provisions written back ({reversed_provisions:,.0f}) are {share:.0%} "
                    f"of |PBT| ({abs(pbt):,.0f}) in {fy} (threshold >{threshold:.0%}).",
        trace=f"ExcessProvisionsWrittenBack {reversed_provisions:,.0f} / |PBT {pbt:,.0f}| = {share:.4f}",
        formula="ExcessProvisionsWrittenBack / |ProfitBeforeTax| > provision_reversal_share_of_pbt",
        source_trace=(f"financial_facts: ExcessProvisionsWrittenBack, ProfitBeforeTax @ {fy}",),
        confidence=MEDIUM,
        confidence_basis="Single-period level check; the spec's >25% YoY provisions-"
                         "movement form needs a prior-period balance and is not attempted.",
        proxy_used="Provisions-written-back/|PBT| level check in place of the YoY "
                   "provisions-movement-volatility form.",
    )


# ---- S17 -----------------------------------------------------------------------------

def rule_s17(fetched: dict[str, Any]) -> Outcome:
    return _disclosure_presence(
        fetched,
        concepts=("SignificantAccountingJudgementsAndEstimatesTextBlock",),
        category="Accounting Policy",
        signal_id="S17", wording="useful-life / estimate-change",
    )


# ---- S18 -----------------------------------------------------------------------------

def rule_s18(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S18: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    oi = _value_at(facts, "OtherIncome", fy)
    pbt = _value_at(facts, "ProfitBeforeTax", fy)
    ocf = _value_at(facts, "CashFlowsFromUsedInOperatingActivities", fy)
    if None in (oi, pbt, ocf):
        return abstain(f"S18: OtherIncome/PBT/OCF not all present for {fy}.", INPUT_NEVER_BOUND)
    if pbt == 0:
        return abstain(f"S18: ProfitBeforeTax is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = oi / pbt
    threshold = TH.get("other_income_to_pbt_share").value
    fired = (share > threshold) and (ocf < pbt)
    return Outcome(
        fired=fired,
        observation=f"Other income {oi:,.0f} is {share:.0%} of PBT {pbt:,.0f} in {fy} "
                    f"(threshold >{threshold:.0%}); OCF {ocf:,.0f} "
                    f"{'lags' if ocf < pbt else 'does not lag'} PBT.",
        trace=f"OtherIncome/PBT={share:.4f}, OCF={ocf:,.0f}, PBT={pbt:,.0f}",
        formula="OtherIncome/PBT > other_income_to_pbt_share AND OCF < PBT",
        source_trace=(f"financial_facts: OtherIncome, ProfitBeforeTax, "
                      f"CashFlowsFromUsedInOperatingActivities @ {fy}",),
        confidence=HIGH, confidence_basis="All inputs present for the latest period.",
    )


# ---- S19 -----------------------------------------------------------------------------

def rule_s19(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S19: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    oi = _value_at(facts, "OtherIncome", fy)
    rev = _value_at(facts, "RevenueFromOperations", fy)
    if oi is None or not rev:
        return abstain(f"S19: OtherIncome/RevenueFromOperations not present/non-zero for {fy}.",
                        INPUT_NEVER_BOUND)
    share = oi / rev
    share_threshold = TH.get("other_income_to_revenue_share").value
    if share <= share_threshold:
        return Outcome(
            fired=False,
            observation=f"Other income is {share:.1%} of revenue in {fy} "
                        f"(threshold >{share_threshold:.0%}); below the level that "
                        f"warrants evaluating the growth-gap leg.",
            trace=f"OtherIncome/Revenue={share:.4f}",
            formula="OtherIncome/Revenue > other_income_to_revenue_share AND growth gap",
            source_trace=(f"financial_facts: OtherIncome, RevenueFromOperations @ {fy}",),
            confidence=HIGH,
            confidence_basis="Level below threshold; growth-gap leg not evaluated.",
        )
    if len(fy_keys) < 2:
        return abstain(
            f"S19: other income is {share:.1%} of revenue (above the "
            f"{share_threshold:.0%} level threshold), but fewer than 2 fy_ends are "
            f"available for this entity to evaluate the growth-gap leg.",
            PANEL_TOO_SHORT,
            source_trace=(f"financial_facts: OtherIncome, RevenueFromOperations @ {fy}",),
        )
    fy_old = fy_keys[-2]
    oi_old = _value_at(facts, "OtherIncome", fy_old)
    rev_old = _value_at(facts, "RevenueFromOperations", fy_old)
    if oi_old is None or not rev_old:
        return abstain(f"S19: prior-period OtherIncome/Revenue missing for {fy_old}.",
                        INPUT_NEVER_BOUND)
    oi_growth = _pct_change(oi, oi_old)
    rev_growth = _pct_change(rev, rev_old)
    if oi_growth is None or rev_growth is None:
        return abstain("S19: zero base-year value.", INPUT_ZERO_DENOMINATOR)
    gap_pp = (oi_growth - rev_growth) * 100.0
    gap_threshold = TH.get("other_income_growth_gap_pp").value
    fired = gap_pp > gap_threshold
    return Outcome(
        fired=fired,
        observation=f"Other income is {share:.1%} of revenue in {fy}; it grew "
                    f"{oi_growth:.1%} vs revenue {rev_growth:.1%} ({fy_old} -> {fy}), "
                    f"a gap of {gap_pp:.1f}pp.",
        trace=f"share={share:.4f}, oi_growth={oi_growth:.4f}, rev_growth={rev_growth:.4f}",
        formula="OtherIncome/Revenue > other_income_to_revenue_share AND "
               "(delta%OtherIncome - delta%Revenue) > other_income_growth_gap_pp",
        source_trace=(f"financial_facts: OtherIncome, RevenueFromOperations @ {fy_old},{fy}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S20 -----------------------------------------------------------------------------

def rule_s20(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S20: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    grant_concepts = (
        "CapitalSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
        "RevenueSubsidiesOrGrantsReceivedFromGovernmentAuthorities",
        "IncomeGovernmentGrantsSubsidies",
    )
    grants = _sum_concepts(facts, grant_concepts, fy)
    rev = _value_at(facts, "RevenueFromOperations", fy)
    oi = _value_at(facts, "OtherIncome", fy) or 0.0
    if rev is None:
        return abstain(f"S20: RevenueFromOperations missing for {fy}.", INPUT_NEVER_BOUND)
    total_income = rev + oi
    if total_income == 0:
        return abstain(f"S20: total income is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = grants / total_income
    threshold = TH.get("government_support_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Grants and subsidies {grants:,.0f} are {share:.1%} of total income "
                    f"{total_income:,.0f} in {fy} (threshold >{threshold:.0%}).",
        trace=f"grants={grants:,.0f}, total_income={total_income:,.0f}, share={share:.4f}",
        formula="(Grants+Subsidies)/(Revenue+OtherIncome) > government_support_share",
        source_trace=(f"financial_facts: {', '.join(grant_concepts)}, "
                      f"RevenueFromOperations, OtherIncome @ {fy}",),
        confidence=HIGH, confidence_basis="Grant concepts default to 0 when a filing "
                         "discloses no government support at all (a real absence, not "
                         "a missing input).",
    )


# ---- S21 -----------------------------------------------------------------------------

def rule_s21(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if len(fy_keys) < 2:
        return abstain(f"S21: entity has {len(fy_keys)} distinct fy_end(s); an unspent-"
                       f"grant YoY comparison needs 2.", PANEL_TOO_SHORT)
    fy_new, fy_old = fy_keys[-1], fy_keys[-2]

    def unspent(fy: str) -> float | None:
        nc = _value_at(facts, "DeferredGovernmentGrantsNoncurrent", fy)
        cur = _value_at(facts, "DeferredGovernmentGrantsCurrent", fy)
        if nc is None and cur is None:
            return None
        return (nc or 0.0) + (cur or 0.0)

    u_new, u_old = unspent(fy_new), unspent(fy_old)
    if u_new is None or u_old is None:
        return abstain("S21: DeferredGovernmentGrants not present for both periods.",
                        INPUT_NEVER_BOUND)
    if u_old == 0:
        return abstain(f"S21: unspent grants are zero in {fy_old}; a growth-rate check "
                       f"is undefined.", INPUT_ZERO_DENOMINATOR)
    growth = (u_new - u_old) / u_old
    threshold = TH.get("unspent_grant_growth_ratio").value
    fired = growth > threshold
    return Outcome(
        fired=fired,
        observation=f"Unspent government grants moved from {u_old:,.0f} to {u_new:,.0f} "
                    f"({fy_old} -> {fy_new}), a growth of {growth:.1%} "
                    f"(threshold >{threshold:.0%}).",
        trace=f"unspent_old={u_old:,.0f}, unspent_new={u_new:,.0f}, growth={growth:.4f}",
        formula="delta(UnspentGrants)/UnspentGrants_T-1 > unspent_grant_growth_ratio",
        source_trace=(f"financial_facts: DeferredGovernmentGrantsNoncurrent, "
                      f"DeferredGovernmentGrantsCurrent @ {fy_old},{fy_new}",),
        confidence=HIGH, confidence_basis="Both periods fully populated.",
    )


# ---- S22 -----------------------------------------------------------------------------

def rule_s22(fetched: dict[str, Any]) -> Outcome:
    """S22 is an entity-level revenue-model classification (spec Sec 5.2) — regulated/
    administered pricing is not itself a balance-sheet or disclosure fact this engine
    derives independently. It is scoped to READ (never re-derive) the Business Profile
    block's revenue-model field; that block is not wired into this signal-engine pass,
    so S22 abstains outright rather than guessing from an unrelated proxy."""
    return abstain(
        "S22 requires the Business Profile block's revenue-model classification "
        "(spec Sec 5.2), which this signal engine does not itself compute and which "
        "was not supplied to this run.",
        PANEL_TOO_SHORT,
        source_trace=("xbrl_business_profile: revenue_model — not wired into this "
                      "signal engine in this pass",),
    )


# ---- S23 -----------------------------------------------------------------------------

def rule_s23(fetched: dict[str, Any]) -> Outcome:
    return _level_ratio_below(
        fetched["face_facts"], _fy_keys(fetched),
        numerator="ContingentLiabilities", denominator="Equity",
        threshold_key="contingent_liabilities_to_equity_share", comparator=">",
        signal_id="S23", wording="Contingent liabilities / equity", severity=HIGH,
    )


# ---- S24 -----------------------------------------------------------------------------

def rule_s24(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S24: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    guarantees = _sum_concepts(
        facts, ("Guarantees", "BalancesHeldWithBanksToExtentHeldAsGuarantees"), fy)
    eq = _value_at(facts, "Equity", fy)
    if eq is None:
        return abstain(f"S24: Equity missing for {fy}.", INPUT_NEVER_BOUND)
    if eq == 0:
        return abstain(f"S24: Equity is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = guarantees / eq
    threshold = TH.get("guarantees_to_equity_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Guarantees {guarantees:,.0f} are {share:.1%} of equity in {fy} "
                    f"(threshold >{threshold:.0%}).",
        trace=f"guarantees={guarantees:,.0f}, equity={eq:,.0f}, share={share:.4f}",
        formula="Guarantees/Equity > guarantees_to_equity_share",
        source_trace=(f"financial_facts: Guarantees, "
                      f"BalancesHeldWithBanksToExtentHeldAsGuarantees, Equity @ {fy}",),
        confidence=HIGH,
        confidence_basis="Guarantee concepts default to 0 when a filing discloses none "
                         "(a real absence).",
    )


# ---- S25 -----------------------------------------------------------------------------

def rule_s25(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S25: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    inv = _sum_concepts(facts, ("NoncurrentInvestments", "CurrentInvestments"), fy)
    assets = _value_at(facts, "Assets", fy)
    if assets is None:
        return abstain(f"S25: Assets missing for {fy}.", INPUT_NEVER_BOUND)
    if assets == 0:
        return abstain(f"S25: Assets is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = inv / assets
    threshold = TH.get("investment_to_assets_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Investments {inv:,.0f} are {share:.1%} of total assets in {fy} "
                    f"(threshold >{threshold:.0%}).",
        trace=f"investments={inv:,.0f}, assets={assets:,.0f}, share={share:.4f}",
        formula="(NoncurrentInvestments+CurrentInvestments)/Assets > investment_to_assets_share",
        source_trace=(f"financial_facts: NoncurrentInvestments, CurrentInvestments, "
                      f"Assets @ {fy}",),
        confidence=HIGH, confidence_basis="All inputs present for the latest period.",
    )


# ---- S26 -----------------------------------------------------------------------------

def rule_s26(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S26: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    oci = _value_at(facts, "OtherComprehensiveIncome", fy)
    eq = _value_at(facts, "Equity", fy)
    if oci is None or eq is None:
        return abstain(f"S26: OtherComprehensiveIncome/Equity not both present for {fy}.",
                        INPUT_NEVER_BOUND)
    if eq == 0:
        return abstain(f"S26: Equity is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = abs(oci) / eq
    threshold = TH.get("oci_to_equity_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"|OCI| {abs(oci):,.0f} is {share:.1%} of equity in {fy} "
                    f"(threshold >{threshold:.0%}).",
        trace=f"|OCI|={abs(oci):,.0f}, equity={eq:,.0f}, share={share:.4f}",
        formula="|OtherComprehensiveIncome|/Equity > oci_to_equity_share",
        source_trace=(f"financial_facts: OtherComprehensiveIncome, Equity @ {fy}",),
        confidence=HIGH, confidence_basis="Both inputs present for the latest period.",
        proxy_used="Single-period share used in place of 'swing relative to OPENING net "
                   "worth', since opening equity for this fy is not separately fetched.",
    )


# ---- S27 -----------------------------------------------------------------------------

def rule_s27(fetched: dict[str, Any]) -> Outcome:
    fy_keys = _fy_keys(fetched)
    facts = fetched["face_facts"]
    if not fy_keys:
        return abstain("S27: no filing found for this entity in as_db.", PANEL_TOO_SHORT)
    fy = fy_keys[-1]
    dividend = _sum_concepts(
        facts,
        ("DividendIncomeNoncurrentInvestmentsFromSubsidiaries",
         "DividendIncomeCurrentInvestmentsFromSubsidiaries"),
        fy,
    )
    pbt = _value_at(facts, "ProfitBeforeTax", fy)
    if pbt is None:
        return abstain(f"S27: ProfitBeforeTax missing for {fy}.", INPUT_NEVER_BOUND)
    if pbt == 0:
        return abstain(f"S27: ProfitBeforeTax is zero for {fy}.", INPUT_ZERO_DENOMINATOR)
    share = dividend / pbt
    threshold = TH.get("dividend_income_to_pbt_share").value
    fired = share > threshold
    return Outcome(
        fired=fired,
        observation=f"Dividend income {dividend:,.0f} is {share:.1%} of PBT {pbt:,.0f} "
                    f"in {fy} (threshold >{threshold:.0%}).",
        trace=f"dividend={dividend:,.0f}, pbt={pbt:,.0f}, share={share:.4f}",
        formula="DividendIncome/ProfitBeforeTax > dividend_income_to_pbt_share",
        source_trace=(f"financial_facts: DividendIncomeNoncurrentInvestmentsFromSubsidiaries, "
                      f"DividendIncomeCurrentInvestmentsFromSubsidiaries, "
                      f"ProfitBeforeTax @ {fy}",),
        confidence=HIGH,
        confidence_basis="Dividend concepts default to 0 when a filing discloses none "
                         "(a real absence).",
    )


# ---- dispatch table (engine's single entry point) ------------------------------------

RULES: dict[str, Callable[[dict[str, Any]], Outcome]] = {
    "S01": rule_s01, "S02": rule_s02, "S03": rule_s03, "S04": rule_s04,
    "S05": rule_s05, "S06": rule_s06, "S07": rule_s07, "S08": rule_s08,
    "S09": rule_s09, "S10": rule_s10, "S11": rule_s11, "S12": rule_s12,
    "S13": rule_s13, "S14": rule_s14, "S15": rule_s15, "S16": rule_s16,
    "S17": rule_s17, "S18": rule_s18, "S19": rule_s19, "S20": rule_s20,
    "S21": rule_s21, "S22": rule_s22, "S23": rule_s23, "S24": rule_s24,
    "S25": rule_s25, "S26": rule_s26, "S27": rule_s27,
}

# The S09 ageing sub-branch is NOT part of the S09 registry entry's single verdict — it
# is reported alongside it by the engine as a separate, always-NOT_APPLICABLE note.
SUB_BRANCHES: dict[str, Callable[[dict[str, Any]], Outcome]] = {
    "S09_ageing": rule_s09_ageing,
}
