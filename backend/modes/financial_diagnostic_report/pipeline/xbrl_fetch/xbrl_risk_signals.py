"""
Block 6 signal detection and interaction evaluation (Spec v3.0 §10, §10.2).

Converts raw financial facts and disclosures into diagnostic signals, and
evaluates the Risk-Interaction Matrix (reinforcing vs. offsetting relationships
between clusters). Split out of `xbrl_risk_clusters.py`, which now only handles
deterministic/LLM cluster synthesis on top of what this module detects.
"""
from __future__ import annotations
import re
from typing import Any

from .xbrl_risk_text import _format_inr, _extract_qualification_snippet
from . import xbrl_signal_thresholds as TH
from . import xbrl_threshold_registry as RT
from . import xbrl_position as POS

# Substantive qualification/reservation language — required before ANY disclosure
# passage is treated as an auditor qualification, regardless of which concept it
# was filed under (see the docstring at its use site in `detect_signals_and_metrics`
# for the false-positive this guards against). Terms are drawn from the actual
# qualification language seen in this corpus (SA 705 "Basis for Qualified/Adverse
# Opinion" wording, "attention is drawn to Note...", disallowed-amount findings)
# rather than invented — a routine disclosure about shareholding, related parties
# or CSR does not use any of this vocabulary.
_QUALIFICATION_CONTENT = re.compile(
    r"\b(qualified opinion|adverse opinion|disclaimer of opinion|audit qualification|"
    r"reservation[s]?|adverse remark[s]?|basis for (?:qualified|adverse) opinion|"
    r"attention is drawn|note no\.?\s*\d+|has not (?:been )?(?:made|charged|provided|complied)|"
    r"disallowed|material misstatement|were it not for|except for the (?:possible )?effects?|"
    r"unable to obtain sufficient|non[- ]compliance|contrary to)\b",
    re.IGNORECASE,
)


def detect_signals_and_metrics(
    metric_rows: list[dict[str, Any]],
    disclosure_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Computes diagnostic financial ratios and extracts active signals from financial_facts
    and narrative disclosures. Strictly binds numerical facts to the current reporting period
    (latest fy_end) to eliminate prior-year overwrite.
    Returns (signals_list, lookup_dict).
    """
    # 1. Determine current reporting period end (current_fy_end)
    valid_fy_ends = [r["fy_end"] for r in metric_rows if r.get("fy_end") is not None]
    current_fy_end = max(valid_fy_ends) if valid_fy_ends else None

    # Filter facts to only those matching current_fy_end (avoids multi-period overwrite)
    facts: dict[str, float] = {}
    for r in metric_rows:
        fy_end = r.get("fy_end")
        if current_fy_end is not None and fy_end is not None and fy_end != current_fy_end:
            continue
        cname = r.get("concept_name")
        v = r.get("value_numeric")
        if cname and v is not None:
            facts[cname] = float(v)

    ca = facts.get("CurrentAssets")
    cl = facts.get("CurrentLiabilities")
    trade_rec = facts.get("TradeReceivablesCurrent", 0.0)
    trade_pay = facts.get("TradePayablesCurrent", 0.0)
    inv = facts.get("Inventories", 0.0)
    cash = facts.get("CashAndCashEquivalents", 0.0)
    bank_other = facts.get("BankBalanceOtherThanCashAndCashEquivalents", 0.0)
    total_cash_bank = cash + bank_other

    ocf = facts.get("CashFlowsFromUsedInOperatingActivities")
    pat = facts.get("ProfitLossForPeriod")
    rev = facts.get("RevenueFromOperations")

    assets = facts.get("Assets")
    ppe = facts.get("PropertyPlantAndEquipment", 0.0)
    cwip = facts.get("CapitalWorkInProgress", 0.0)
    noncurrent_inv = facts.get("NoncurrentInvestments", 0.0)
    current_inv = facts.get("CurrentInvestments", 0.0)
    inv_other = facts.get("Investments", 0.0)
    total_inv = noncurrent_inv + current_inv + inv_other
    other_nc_assets = facts.get("OtherNoncurrentAssets", 0.0)
    dda = facts.get("DepreciationDepletionAndAmortisationExpense", 0.0)
    impairment = facts.get("ImpairmentLossRecognisedInProfitOrLossPropertyPlantAndEquipment", 0.0)

    eq = facts.get("Equity", 0.0)
    borr_curr = facts.get("BorrowingsCurrent", 0.0)
    borr_noncurr = facts.get("BorrowingsNoncurrent", 0.0)
    total_borr = borr_curr + borr_noncurr
    fin_costs = facts.get("FinanceCosts", 0.0)

    other_income = facts.get("OtherIncome", 0.0)
    pbt = facts.get("ProfitBeforeTax", 0.0)
    provisions = facts.get("ProvisionsCurrent", 0.0) + facts.get("ProvisionsNoncurrent", 0.0)
    # Same three concepts S20 (xbrl_signal_rules.py) reads — kept in sync rather than
    # invented here, so the "not raised" narrative below and the fired-signal path agree
    # on what "grants" means.
    grants = (
        facts.get("CapitalSubsidiesOrGrantsReceivedFromGovernmentAuthorities", 0.0)
        + facts.get("RevenueSubsidiesOrGrantsReceivedFromGovernmentAuthorities", 0.0)
        + facts.get("IncomeGovernmentGrantsSubsidies", 0.0)
    )
    total_income = (rev or 0.0) + other_income
    grant_share = (grants / total_income) if total_income else None

    wc_net = (ca - cl) if (ca is not None and cl is not None) else None
    current_ratio = (ca / cl) if (ca is not None and cl is not None and cl > 0) else None
    # Leverage status comes from the shared helper, from the filing's OWN figures: an
    # absent Equity or borrowings fact is "missing", not a zero that would read as a
    # debt-free or zero-equity balance sheet.
    _borr_concepts = ("BorrowingsCurrent", "BorrowingsNoncurrent")
    lev = POS.classify_leverage(
        facts.get("Equity"),
        total_borr if any(c in facts for c in _borr_concepts) else None,
    )
    de_ratio = lev.de_ratio
    cwip_ratio = (cwip / (ppe + cwip)) if (ppe + cwip) > 0 else 0.0
    inv_ratio = (total_inv / assets) if (assets is not None and assets > 0) else 0.0

    lookup = {
        "current_fy_end": str(current_fy_end) if current_fy_end else None,
        "ca": ca, "cl": cl, "wc_net": wc_net, "current_ratio": current_ratio,
        "trade_rec": trade_rec, "trade_pay": trade_pay, "inv": inv,
        "cash": cash, "bank_other": bank_other, "total_cash_bank": total_cash_bank,
        "ocf": ocf, "pat": pat, "rev": rev,
        "assets": assets, "ppe": ppe, "cwip": cwip, "cwip_ratio": cwip_ratio,
        "total_inv": total_inv, "noncurrent_inv": noncurrent_inv, "current_inv": current_inv,
        "inv_ratio": inv_ratio, "other_nc_assets": other_nc_assets,
        "dda": dda, "impairment": impairment,
        "equity": eq, "borrowings": total_borr, "borrowings_curr": borr_curr,
        "borrowings_noncurr": borr_noncurr, "de_ratio": de_ratio, "fin_costs": fin_costs,
        "leverage_status": lev.status, "de_display": lev.de_display(),
        "other_income": other_income, "pbt": pbt, "provisions": provisions, "grants": grants,
        "total_income": total_income, "grant_share": grant_share,
    }

    signals: list[dict[str, Any]] = []

    # Signal 1: Operating Cash Flow Deficit / Negative OCF / OCF Lag
    if ocf is not None and ocf < 0:
        signals.append({
            "id": "SIG_OCF_NEG",
            "cluster_id": "RC01",
            "signal": f"Negative Operating Cash Flow of {_format_inr(ocf)}",
            "source_trace": "financial_facts: CashFlowsFromUsedInOperatingActivities",
            "severity": "high" if total_borr > 0 else "medium",
        })
    elif ocf is not None and pat is not None and pat > 0 and ocf < pat:
        signals.append({
            "id": "SIG_OCF_LAG_PAT",
            "cluster_id": "RC01",
            "signal": f"Operating Cash Flow ({_format_inr(ocf)}) lags Net Profit ({_format_inr(pat)})",
            "source_trace": "financial_facts: CashFlowsFromUsedInOperatingActivities vs ProfitLossForPeriod",
            "severity": "medium",
        })

    # Signal 2: Net Current Liabilities
    if wc_net is not None and wc_net < 0:
        signals.append({
            "id": "SIG_NET_CURR_LIAB",
            "cluster_id": "RC01",
            "signal": f"Net current liabilities of {_format_inr(abs(wc_net))} (current ratio: {current_ratio:.2f}x)",
            "source_trace": "financial_facts: CurrentAssets vs CurrentLiabilities",
            "severity": "high",
        })

    # Signal 3: Revenue & Receivables Decoupling / Overconcentration
    if rev is not None and rev > 0 and trade_rec > 0:
        rec_rev_ratio = trade_rec / rev
        if rec_rev_ratio > RT.value("risk.rec_rev_flag"):  # Over 90 days outstanding equivalent
            signals.append({
                "id": "SIG_REC_CONCENTRATION",
                "cluster_id": "RC02",
                "signal": f"Trade receivables of {_format_inr(trade_rec)} constitute {rec_rev_ratio:.1%} of revenue",
                "source_trace": "financial_facts: TradeReceivablesCurrent vs RevenueFromOperations",
                "severity": "high" if rec_rev_ratio > RT.value("risk.rec_rev_high") else "medium",
            })

    # Signal 4: Capital Work-in-Progress Concentration
    if cwip > 0 and cwip_ratio > RT.value("risk.cwip_flag"):
        signals.append({
            "id": "SIG_CWIP_CONCENTRATION",
            "cluster_id": "RC03",
            "signal": f"Capital Work-in-Progress of {_format_inr(cwip)} represents {cwip_ratio:.1%} of total capital assets",
            "source_trace": "financial_facts: CapitalWorkInProgress vs PropertyPlantAndEquipment",
            "severity": "high" if cwip_ratio > RT.value("risk.cwip_high") else "medium",
        })

    # Signal 4b: Investment Portfolio Concentration / Valuation Risk (RC03)
    if total_inv > 0 and assets and assets > 0:
        if inv_ratio > RT.value("risk.inv_flag") or (total_inv > RT.value("risk.inv_min_nonoperating") and ppe == 0 and cwip == 0):
            signals.append({
                "id": "SIG_INVESTMENT_CONCENTRATION",
                "cluster_id": "RC03",
                "signal": f"Investment portfolio of {_format_inr(total_inv)} represents {inv_ratio:.1%} of total assets ({_format_inr(assets)})",
                "source_trace": "financial_facts: NoncurrentInvestments/CurrentInvestments vs Assets",
                "severity": "high" if inv_ratio > RT.value("risk.inv_high") else "medium",
            })

    # Signal 5: Leverage & Debt Exposure
    if total_borr > 0 and lev.impaired_equity:
        # D/E is undefined, but borrowings against negative/nil net worth is a stronger
        # solvency lead than any D/E above 1.0x - it must not fall silent just because
        # the ratio cannot be computed.
        _word = "Negative net worth" if lev.status == POS.NEGATIVE_NET_WORTH else "Nil equity"
        signals.append({
            "id": "SIG_NEGATIVE_NET_WORTH",
            "cluster_id": "RC04",
            "signal": (f"{_word}: equity of {_format_inr(eq)} against total borrowings of "
                       f"{_format_inr(total_borr)} (D/E not meaningful)"),
            "source_trace": "financial_facts: Equity, BorrowingsCurrent + BorrowingsNoncurrent",
            "severity": "high",
        })
    elif total_borr > 0 and de_ratio is not None and de_ratio > TH.get('leverage_de_ceiling').value:
        signals.append({
            "id": "SIG_LEVERAGE_HIGH",
            "cluster_id": "RC04",
            "signal": f"Elevated leverage with total borrowings of {_format_inr(total_borr)} (D/E ratio: {de_ratio:.2f}x)",
            "source_trace": "financial_facts: Borrowings vs Equity",
            "severity": "high" if de_ratio > RT.value("risk.de_high") else "medium",
        })
    elif total_borr == 0 and eq > 0:
        # Debt-free mitigating signal
        signals.append({
            "id": "SIG_DEBT_FREE",
            "cluster_id": "RC04",
            "signal": f"Debt-free balance sheet (nil borrowings) with {_format_inr(eq)} equity capital",
            "source_trace": "financial_facts: Borrowings == 0, Equity",
            "severity": "low",
        })

    # Signal 6: Disproportionate Other Income
    if other_income > 0 and pbt is not None:
        if pbt > 0:
            oi_ratio = other_income / pbt
            if oi_ratio > RT.value("risk.oi_flag"):
                signals.append({
                    "id": "SIG_OTHER_INCOME_HIGH",
                    "cluster_id": "RC05",
                    "signal": f"Other income of {_format_inr(other_income)} represents {oi_ratio:.1%} of profit before tax ({_format_inr(pbt)})",
                    "source_trace": "financial_facts: OtherIncome vs ProfitBeforeTax",
                    "severity": "high" if oi_ratio > RT.value("risk.oi_high") else "medium",
                })
        elif pbt < 0:
            op_loss = pbt - other_income
            signals.append({
                "id": "SIG_OTHER_INCOME_HIGH",
                "cluster_id": "RC05",
                "signal": f"Other income of {_format_inr(other_income)} partially cushioned an operating loss of {_format_inr(op_loss)} (reported pre-tax loss: {_format_inr(pbt)})",
                "source_trace": "financial_facts: OtherIncome vs ProfitBeforeTax (Loss mitigation)",
                "severity": "medium",
            })

    # Signal 7: Pre-revenue Gestation Status
    if rev is not None and rev == 0:
        signals.append({
            "id": "SIG_PRE_REVENUE",
            "cluster_id": "RC02",
            "signal": "Nil revenue from operations (development/establishment phase)",
            "source_trace": "financial_facts: RevenueFromOperations == 0",
            "severity": "low",
        })

    # Signal 7b: Government Grant/Subsidy Dependency (RC06) — same formula and
    # threshold as S20 (xbrl_signal_rules.py), kept in sync rather than duplicated
    # as a separate magic number.
    if grant_share is not None and grant_share > TH.get("government_support_share").value:
        signals.append({
            "id": "SIG_GOVERNMENT_GRANT_DEPENDENCY",
            "cluster_id": "RC06",
            "signal": (
                f"Grants and subsidies {_format_inr(grants)} are {grant_share:.1%} of total "
                f"income {_format_inr(total_income)} "
                f"(threshold >{TH.get('government_support_share').value:.0%})"
            ),
            "source_trace": (
                "financial_facts: CapitalSubsidiesOrGrantsReceivedFromGovernmentAuthorities, "
                "RevenueSubsidiesOrGrantsReceivedFromGovernmentAuthorities, "
                "IncomeGovernmentGrantsSubsidies, RevenueFromOperations, OtherIncome"
            ),
            "severity": "medium",
        })

    # Signal 8: Statutory Auditor Qualifications / Modifications (RC05)
    if disclosure_rows:
        qual_passages: list[tuple[str, str]] = []
        for d in disclosure_rows:
            cname = d.get("concept_name") or ""
            txt = (d.get("text") or "").strip()
            if not txt:
                continue

            is_qual_concept = cname in (
                "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
                "AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReport",
                "DirectorsCommentOnAuditorsQualificationsReservationsAdverseRemarksInAuditorsReport",
                "SecretarialQualificationsOrObservationsOrOtherRemarksInSecretarialAuditReport",
                "CompanySecretaryQualificationOrObservationOrOtherRemarksInSecretarialAuditReport",
            )
            is_bool_flag = (
                cname == "WhetherAuditorsReportHasBeenQualifiedOrHasAnyReservationsOrContainsAdverseRemarks"
                and txt.lower() in ("true", "yes", "1")
            )
            # Content-level check, required even when `cname` is one of the five
            # qualification concepts above — `cname` says WHERE as_db filed the
            # text, not WHAT it says, and a mis-bound disclosure (a routine
            # Board's-Report line landing under an auditor-qualification concept
            # tag) has been observed to carry unrelated boilerplate. Without this,
            # `is_qual_concept and len(txt) > 20` alone would raise a Priority-1
            # significant risk off text as unrelated as a nominee-shareholding
            # disclosure, purely because of which concept it was tagged under.
            has_qual_text = bool(_QUALIFICATION_CONTENT.search(txt))

            if is_bool_flag:
                qual_passages.append((cname, "Statutory auditor report contains explicit qualifications or reservations."))
            elif is_qual_concept and has_qual_text and len(txt) > 20:
                qual_passages.append((cname, _extract_qualification_snippet(txt, 240)))
            elif has_qual_text and len(txt) > 20:
                qual_passages.append((cname, _extract_qualification_snippet(txt, 240)))

        if qual_passages:
            def _qual_priority(p: tuple[str, str]) -> int:
                concept, snippet = p
                # Lowest priority: boilerplate audit opening or unmodified statements
                if re.search(r'^\s*We have audited\b', snippet, re.IGNORECASE) or "opinion is not modified" in snippet.lower():
                    return 99
                # Explicit ECL on receivables or specific reservation clauses
                if re.search(r'\b(not made the provision|provision for expected credit loss|ecl)\b', snippet, re.IGNORECASE):
                    return 0
                if re.search(r'\b(regarding advance of|claims filed by|lying outstanding and unadjusted)\b', snippet, re.IGNORECASE):
                    return 1
                if concept in ("AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReport", "AuditorsQualificationsReservationsAdverseRemarksInAuditorsReport"):
                    if re.search(r'\b(note no\.\s*\d+|attention is drawn|basis for qualified opinion)\b', snippet, re.IGNORECASE):
                        return 2
                    return 3
                if "DirectorsCommentOnAuditorsQualifications" in concept:
                    return 4
                if "WhetherAuditorsReportHasBeenQualified" in concept:
                    return 5
                return 10

            qual_passages.sort(key=_qual_priority)
            top_concept, top_snippet = qual_passages[0]
            signals.append({
                "id": "SIG_AUDITOR_QUALIFICATION",
                "cluster_id": "RC05",
                "signal": f"Statutory auditor qualification / adverse observation reported: {top_snippet}",
                "source_trace": f"disclosures: {top_concept}",
                "severity": "high",
            })
            lookup["auditor_qualification"] = top_snippet
            lookup["auditor_qualification_concept"] = top_concept

            qual_targets_receivables = bool(re.search(
                r'\b(trade receivable|debtor|ecl|expected credit loss|ind as 109|credit risk|customer balance)\b',
                top_snippet,
                re.IGNORECASE,
            ))
            qual_targets_liabilities = bool(re.search(
                r'\b(trade payable|creditor|current liabilit|statutory due|contingent liabilit|advance received|advance of|delayed delivery|unadjusted advance|unadjusted as on|disputed due|disputed liabilit|claim against company|provision for (?:claims|warranties|decommissioning|tax|damages|litigation|penalt))\b',
                top_snippet,
                re.IGNORECASE,
            ))
            qual_targets_assets = bool(re.search(
                r'\b(capital work|cwip|fixed asset|property plant|depreciation|impairment|investment property|project in progress|salable land)\b',
                top_snippet,
                re.IGNORECASE,
            ))
            lookup["qual_targets_receivables"] = qual_targets_receivables
            lookup["qual_targets_liabilities"] = qual_targets_liabilities
            lookup["qual_targets_assets"] = qual_targets_assets

    return signals, lookup


def evaluate_risk_interactions(
    signals: list[dict[str, Any]],
    lookup: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Evaluates the Risk-Interaction Matrix (Spec §10.2).
    Determines reinforcing (compounding) vs. offsetting (mitigating) relationships.
    """
    interactions: list[dict[str, Any]] = []
    signal_ids = {s["id"] for s in signals}

    # Interaction 1: Liquidity Stress + Debt Leverage (Reinforcing)
    if ("SIG_OCF_NEG" in signal_ids or "SIG_NET_CURR_LIAB" in signal_ids) and "SIG_LEVERAGE_HIGH" in signal_ids:
        interactions.append({
            "cluster_a": "RC01",
            "cluster_b": "RC04",
            "relationship": "reinforcing",
            "rationale": "Negative operating cash flow combined with elevated debt obligations compounds liquidity and debt service refinancing exposure.",
        })

    # Interaction 2: Negative OCF OFFSET by 100% Equity & Nil Debt (Offsetting)
    # Strictly requires actual negative OCF (SIG_OCF_NEG).
    # NEVER trigger cash outflow narrative if OCF is positive (even if accounting PAT is negative).
    if "SIG_OCF_NEG" in signal_ids and "SIG_DEBT_FREE" in signal_ids:
        interactions.append({
            "cluster_a": "RC01",
            "cluster_b": "RC04",
            "relationship": "offsetting",
            "rationale": "Operating cash deficit is fully offset by a debt-free balance sheet (₹0.00 borrowings) and strong promoter equity infusion; immediate insolvency risk is mitigated.",
        })

    # Interaction 3: High Receivables + OCF Deficit (Reinforcing)
    if "SIG_REC_CONCENTRATION" in signal_ids and "SIG_OCF_NEG" in signal_ids:
        interactions.append({
            "cluster_a": "RC02",
            "cluster_b": "RC01",
            "relationship": "reinforcing",
            "rationale": "Uncollected trade receivables directly constrain cash realization, reinforcing operating cash flow deficit and working-capital stress.",
        })

    # Interaction 4: High CWIP + High Borrowings (Reinforcing)
    if "SIG_CWIP_CONCENTRATION" in signal_ids and "SIG_LEVERAGE_HIGH" in signal_ids:
        interactions.append({
            "cluster_a": "RC03",
            "cluster_b": "RC04",
            "relationship": "reinforcing",
            "rationale": "Large capital work-in-progress funded by borrowed capital raises project execution risk and borrowing-cost capitalisation sensitivity under Ind AS 23.",
        })

    # Interaction 5: High CWIP + Debt Free (Offsetting)
    if "SIG_CWIP_CONCENTRATION" in signal_ids and "SIG_DEBT_FREE" in signal_ids:
        interactions.append({
            "cluster_a": "RC03",
            "cluster_b": "RC04",
            "relationship": "offsetting",
            "rationale": "Capital project execution is funded by internal equity accruals without debt leverage; capitalisation delay does not threaten loan covenant compliance.",
        })

    # Interaction 6: High Investment Portfolio + Debt Free (Offsetting)
    if "SIG_INVESTMENT_CONCENTRATION" in signal_ids and "SIG_DEBT_FREE" in signal_ids:
        interactions.append({
            "cluster_a": "RC03",
            "cluster_b": "RC04",
            "relationship": "offsetting",
            "rationale": "Substantial capital investment portfolio is 100% funded by promoter equity capital with zero debt leverage; investee development delay does not threaten immediate debt covenant compliance or solvency.",
        })

    # Interaction 7: Auditor Qualification Domain Interactions
    if "SIG_AUDITOR_QUALIFICATION" in signal_ids:
        qual_targets_rec = lookup.get("qual_targets_receivables", False)
        qual_targets_liab = lookup.get("qual_targets_liabilities", False)
        qual_targets_asset = lookup.get("qual_targets_assets", False)

        # Domain A: Auditor Qualification + High Receivables (Reinforcing RC05 <-> RC02)
        if "SIG_REC_CONCENTRATION" in signal_ids and qual_targets_rec:
            interactions.append({
                "cluster_a": "RC05",
                "cluster_b": "RC02",
                "relationship": "reinforcing",
                "rationale": "Statutory auditor qualification directly challenges the valuation and expected credit loss (ECL) provisioning of outstanding trade receivables, compounding revenue recognition and asset recoverability risk.",
            })

        # Domain B: Auditor Qualification + Working Capital / Payables / Advances (Reinforcing RC05 <-> RC01)
        if qual_targets_liab:
            interactions.append({
                "cluster_a": "RC05",
                "cluster_b": "RC01",
                "relationship": "reinforcing",
                "rationale": "Statutory auditor reservation regarding unadjusted advances and outstanding liabilities creates uncertainty around current obligation settlement and working capital liquidity.",
            })

        # Domain C: Auditor Qualification + Capital Assets / CWIP (Reinforcing RC05 <-> RC03)
        if ("SIG_CWIP_CONCENTRATION" in signal_ids or "SIG_INVESTMENT_CONCENTRATION" in signal_ids) and qual_targets_asset:
            interactions.append({
                "cluster_a": "RC05",
                "cluster_b": "RC03",
                "relationship": "reinforcing",
                "rationale": "Statutory auditor reservation regarding capital work, property assets, or valuation challenges capital asset carrying amounts under Ind AS 16 / 36.",
            })

    return interactions
