"""
Block 6 deterministic (non-LLM) risk-cluster synthesis. Authoritative fallback
used by `xbrl_risk_clusters.build_risk_clusters` when the LLM path is unavailable
or does not form a valid response. Split out of `xbrl_risk_clusters.py`, which now
only orchestrates LLM-vs-deterministic selection on top of this.
"""
from __future__ import annotations
from typing import Any

from . import xbrl_risk_concepts as C
from . import xbrl_signal_thresholds as TH
from . import xbrl_threshold_registry as RT
from . import xbrl_position as POS
from .xbrl_risk_text import _format_inr


def _inr_or_na(v: float | None) -> str:
    return _format_inr(v) if v is not None else "n/a"


def _rc01_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """The figures behind RC01 (working-capital/liquidity) — the same OCF,
    working-capital and current-ratio checks `xbrl_risk_signals.py` runs, shown for
    BOTH outcomes rather than only when the cluster raises."""
    ocf, pat = lookup.get("ocf"), lookup.get("pat")
    wc_net, current_ratio = lookup.get("wc_net"), lookup.get("current_ratio")
    if ocf is None and wc_net is None:
        return []
    return [
        {"label": "Operating cash flow", "value": _inr_or_na(ocf)},
        {"label": "Net profit for the year", "value": _inr_or_na(pat)},
        {"label": "Net working capital (current assets − current liabilities)", "value": _inr_or_na(wc_net)},
        {"label": "Current ratio", "value": f"{current_ratio:.2f}x" if current_ratio is not None else "n/a"},
    ]


def _rc02_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """Same receivables-concentration check as Signal 3 in `xbrl_risk_signals.py`
    (>25% of revenue), shown regardless of whether it fired."""
    trade_rec, rev = lookup.get("trade_rec"), lookup.get("rev")
    if rev is None:
        return []
    rec_rev_ratio = (trade_rec / rev) if (rev and trade_rec is not None) else None
    return [
        {"label": "Trade receivables", "value": _inr_or_na(trade_rec)},
        {"label": "Revenue from operations", "value": _inr_or_na(rev)},
        {"label": "Receivables as share of revenue", "value": f"{rec_rev_ratio:.1%}" if rec_rev_ratio is not None else "n/a"},
        {"label": "Threshold", "value": f">{RT.value('risk.rec_rev_flag') * 100:g}%"},
    ]


def _rc03_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """Same CWIP-concentration (>20% of PPE+CWIP) and investment-concentration
    (>30% of total assets) checks as Signals 4/4b, shown regardless of outcome."""
    cwip, cwip_ratio = lookup.get("cwip"), lookup.get("cwip_ratio")
    total_inv, inv_ratio, assets = lookup.get("total_inv"), lookup.get("inv_ratio"), lookup.get("assets")
    if assets is None:
        return []
    return [
        {"label": "Capital work-in-progress", "value": _inr_or_na(cwip)},
        {"label": "CWIP share of capital assets (PPE + CWIP)", "value": f"{cwip_ratio:.1%}" if cwip_ratio is not None else "n/a"},
        {"label": "Investment portfolio", "value": _inr_or_na(total_inv)},
        {"label": "Investments share of total assets", "value": f"{inv_ratio:.1%}" if inv_ratio is not None else "n/a"},
        {"label": "Thresholds", "value": f"CWIP >{RT.value('risk.cwip_flag') * 100:g}% · Investments >{RT.value('risk.inv_flag') * 100:g}%"},
    ]


def unraised_reason(cid: str, lookup: dict[str, Any]) -> str:
    """Why a cluster was not raised. RC04's wording is built from the filing's own
    leverage figures - a fixed sentence asserting "conservative leverage / nil
    borrowings" is only ever true for some filings, and a risk report must not say
    it for the others."""
    if cid != "RC04":
        return C.UNRAISED_REASONS.get(cid, "No contributing anomaly signals detected in reported figures or disclosures.")
    status = lookup.get("leverage_status")
    if status == POS.MISSING:
        return ("Not assessed: equity or borrowings were not reported in the filing's tagged "
                "facts, so leverage could not be evaluated.")
    de = lookup.get("de_ratio")
    if status == POS.OK and de is not None:
        return (f"Leverage signal not triggered: total borrowings of {_format_inr(lookup.get('borrowings'))} "
                f"against equity of {_format_inr(lookup.get('equity'))} give a debt-to-equity ratio of "
                f"{de:.2f}x, within the {TH.get('leverage_de_ceiling').value:.2f}x screening threshold.")
    return C.UNRAISED_REASONS["RC04"]


def _rc04_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """Same leverage check as Signal 5 (D/E >1.0x), shown regardless of outcome."""
    total_borr, eq, de_ratio = lookup.get("borrowings"), lookup.get("equity"), lookup.get("de_ratio")
    if total_borr is None and eq is None:
        return []
    return [
        {"label": "Total borrowings", "value": _inr_or_na(total_borr)},
        {"label": "Equity capital", "value": _inr_or_na(eq)},
        {"label": "Debt-to-equity ratio", "value": lookup.get("de_display") or "n/a"},
        {"label": "Threshold", "value": f">{TH.get('leverage_de_ceiling').value:.2f}x"},
    ]


def _rc05_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """Same other-income-vs-PBT check as Signal 6 (>30% of PBT), shown regardless
    of outcome. Provisions included for context — RC05 also covers estimation
    quality, which this figure speaks to even though it has no fixed threshold."""
    other_income, pbt, provisions = lookup.get("other_income"), lookup.get("pbt"), lookup.get("provisions")
    if pbt is None:
        return []
    oi_ratio = (other_income / pbt) if (pbt and pbt > 0 and other_income is not None) else None
    return [
        {"label": "Other income", "value": _inr_or_na(other_income)},
        {"label": "Profit before tax", "value": _inr_or_na(pbt)},
        {"label": "Other income share of PBT", "value": f"{oi_ratio:.1%}" if oi_ratio is not None else "n/a"},
        {"label": "Provisions (current + non-current)", "value": _inr_or_na(provisions)},
        {"label": "Threshold", "value": f">{RT.value('risk.oi_flag') * 100:g}%"},
    ]


def _rc06_metrics(lookup: dict[str, Any]) -> list[dict[str, str]]:
    """The figures RC06's raise/no-raise call was actually made on — surfaced for
    BOTH outcomes, not only when the cluster raises. A "Not Raised" verdict with
    no numbers next to it is unauditable: nothing here says how close the entity
    was to the threshold."""
    grants = lookup.get("grants")
    total_income = lookup.get("total_income")
    grant_share = lookup.get("grant_share")
    threshold = TH.get("government_support_share").value
    if grant_share is None or not total_income:
        return []
    return [
        {"label": "Grants & subsidies", "value": _format_inr(grants or 0.0)},
        {"label": "Total income (revenue + other income)", "value": _format_inr(total_income)},
        {"label": "Grants as share of total income", "value": f"{grant_share:.1%}"},
        {"label": "Threshold", "value": f">{threshold:.0%}"},
    ]


# One evidentiary-figures builder per canonical cluster — every "Not Raised" card
# gets the numbers the call was actually made on, not just the prose. Missing here
# means no cluster-specific figures exist yet, not that the cluster has none.
_CLUSTER_METRICS: dict[str, Any] = {
    "RC01": _rc01_metrics,
    "RC02": _rc02_metrics,
    "RC03": _rc03_metrics,
    "RC04": _rc04_metrics,
    "RC05": _rc05_metrics,
    "RC06": _rc06_metrics,
}


def _cluster_metrics(cid: str, lookup: dict[str, Any]) -> list[dict[str, str]]:
    builder = _CLUSTER_METRICS.get(cid)
    return builder(lookup) if builder else []


def deterministic_risk_clusters(
    company_name: str,
    cin: str,
    signals: list[dict[str, Any]],
    lookup: dict[str, Any],
    interactions: list[dict[str, Any]],
    disclosure_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Authoritative fallback synthesis for Block 6 when LLM is unavailable.
    Assembles standards-aligned cluster packages from Spec Appendices D, E, & F.
    Guarantees that all 6 canonical risk clusters (RC01 to RC06) are present,
    with unraised clusters explicitly presenting their non-applicability rationale.
    """
    active_clusters: list[dict[str, Any]] = []

    # Map signals to cluster IDs
    cluster_signals: dict[str, list[dict[str, Any]]] = {cid: [] for cid in C.CLUSTER_IDS}
    for s in signals:
        cid = s.get("cluster_id")
        if cid in cluster_signals:
            cluster_signals[cid].append(s)

    # 1. RC01: Working-Capital and Liquidity Stress
    s_rc01 = cluster_signals["RC01"]
    if s_rc01:
        is_offset = any(i["cluster_a"] == "RC01" and i["relationship"] == "offsetting" for i in interactions)
        rank = 2 if not is_offset else 4
        alt_exp = (
            ["Pre-operating gestation phase with planned initial cash burn funded by equity capital rather than commercial insolvency."]
            if lookup.get("rev") == 0 else
            ["Temporary working-capital absorption due to customer milestone billing cycles or buffer inventory build-up."]
        )
        active_clusters.append({
            "id": "RC01",
            "theme": C.CLUSTER_NAMES["RC01"],
            "raised": True,
            "reason": "",
            "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc01],
            "alt_explanations": alt_exp,
            "metrics": _rc01_metrics(lookup),
            "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC01"]),
            "inherent_risk": "high" if not is_offset else "medium",
            "significant_risk": not is_offset,
            "control_implications": "Review internal controls over working-capital forecasting, supplier payment authorization, and cash management.",
            "recommended_response": {
                "nature": "Substantive tests of detail and search for unrecorded liabilities",
                "timing": "Year-end testing and post-balance sheet cash flow review",
                "extent": "Examine unbilled liabilities, supplier credit notes, and 12-month rolling cash flow forecast",
            },
            "specialist_referral": C.CLUSTER_SPECIALISTS["RC01"],
            "evidence_request": "Requisition: (1) Cash flow forecasts for next 12 months, (2) Bank reconciliation statements, (3) Unrecorded liability check register post year-end.",
            "diagnostic_confidence": "high",
            "priority_rank": rank,
            "priority_reasoning": "Moderated by debt-free equity backing." if is_offset else "Prioritised due to cash flow deficit and current liability pressure.",
        })

    # 2. RC02: Revenue and Receivable Quality
    s_rc02 = cluster_signals["RC02"]
    if s_rc02:
        is_pre_rev = (lookup.get("rev") == 0)
        has_zero_debtors = (lookup.get("trade_rec", 0) == 0)

        if is_pre_rev or has_zero_debtors:
            active_clusters.append({
                "id": "RC02",
                "theme": C.CLUSTER_NAMES["RC02"],
                "raised": True,
                "reason": "",
                "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc02],
                "alt_explanations": [
                    "Entity is in pre-revenue establishment phase; zero commercial billing and nil trade receivables are expected features of the development stage."
                ],
                "affected_assertions": ["Cut-off", "Completeness of revenue"],
                "inherent_risk": "low",
                "significant_risk": False,
                "control_implications": "Verify internal controls over project commissioning cut-off, revenue recognition criteria under Ind AS 115, and milestone billing approvals.",
                "recommended_response": {
                    "nature": "Revenue cut-off testing and review of commercial contracts / PPAs",
                    "timing": "Year-end audit testing",
                    "extent": "Review scheduled Commercial Operation Date (COD) timelines, executed power purchase/customer agreements, and verify zero unrecorded commercial dispatches",
                },
                "specialist_referral": "None",
                "evidence_request": "Requisition: (1) Scheduled Commercial Operation Date (COD) milestone reports, (2) Executed customer off-take agreements / PPAs, (3) Bank statements confirming absence of unrecorded operating revenue receipts.",
                "diagnostic_confidence": "high",
                "priority_rank": 5,
                "priority_reasoning": "Consistent with development stage; minimal operational credit exposure with nil trade receivables.",
            })
        else:
            active_clusters.append({
                "id": "RC02",
                "theme": C.CLUSTER_NAMES["RC02"],
                "raised": True,
                "reason": "",
                "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc02],
                "alt_explanations": [
                    "Receivables concentration reflects standard credit terms with major sovereign/public-sector institutional counterparties."
                ],
                "metrics": _rc02_metrics(lookup),
                "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC02"]),
                "inherent_risk": "high",
                "significant_risk": True,
                "control_implications": "Review credit control approvals, customer billing cut-off, and adequacy of expected credit loss (ECL) provisioning.",
                "recommended_response": {
                    "nature": "Debtor circularisation and subsequent realization verification",
                    "timing": "Interim and year-end cut-off testing",
                    "extent": "Positive confirmations for top customer balances and subsequent cash receipts testing",
                },
                "specialist_referral": C.CLUSTER_SPECIALISTS["RC02"],
                "evidence_request": "Requisition: (1) Aged debtors trial balance, (2) Direct customer confirmations, (3) Subsequent realization register post year-end.",
                "diagnostic_confidence": "high",
                "priority_rank": 1,
                "priority_reasoning": "Elevated due to receivables growth outpacing revenue recognition or significant collection cycle lengthening.",
            })

    # 3. RC03: Asset and Capitalisation Risk
    s_rc03 = cluster_signals["RC03"]
    has_inv_risk = any(s["id"] == "SIG_INVESTMENT_CONCENTRATION" for s in s_rc03) or lookup.get("inv_ratio", 0) > RT.value("risk.inv_flag")
    has_cwip_risk = any(s["id"] == "SIG_CWIP_CONCENTRATION" for s in s_rc03) or lookup.get("cwip", 0) > 0

    if s_rc03 or has_inv_risk or has_cwip_risk:
        if has_inv_risk and not has_cwip_risk:
            # Investment Concentration (e.g. holding/SPV entity)
            inv_val = lookup.get("total_inv", 0.0)
            inv_ratio = lookup.get("inv_ratio", 0.0)
            contributing = [
                {"signal": s["signal"], "source_trace": s["source_trace"]}
                for s in s_rc03 if s["id"] == "SIG_INVESTMENT_CONCENTRATION"
            ]
            if not contributing:
                contributing = [{
                    "signal": f"Investment portfolio of {_format_inr(inv_val)} represents {inv_ratio:.1%} of total assets",
                    "source_trace": "financial_facts: NoncurrentInvestments vs Assets"
                }]
            active_clusters.append({
                "id": "RC03",
                "theme": "Asset and Investment Valuation Risk",
                "raised": True,
                "reason": "",
                "contributing_signals": contributing,
                "alt_explanations": [
                    "Strategic capital investments in development-phase subsidiary/joint-venture green energy projects with multi-year return gestation."
                ],
                "metrics": _rc03_metrics(lookup),
                "affected_assertions": ["Valuation and allocation", "Existence of assets", "Rights and obligations"],
                "inherent_risk": "high",
                "significant_risk": True,
                "control_implications": "Verify internal financial controls over investment valuation, periodic impairment testing of equity holdings, and monitoring of investee operating performance.",
                "recommended_response": {
                    "nature": "Independent impairment assessment and valuation verification of investee entities under Ind AS 36 / Ind AS 109",
                    "timing": "Year-end impairment testing",
                    "extent": "Review audited financial statements of investee entities, evaluate discounted cash flow (DCF) models, and test key valuation assumptions (discount rates, projected tariffs, COD timelines)",
                },
                "specialist_referral": "Valuation Specialist / Technical Expert (for project DCF and cash flow projections)",
                "evidence_request": "Requisition: (1) Audited financial statements of investee subsidiaries/JVs, (2) Management's Ind AS 36 impairment assessment and DCF valuation models, (3) Independent registered valuer reports and discount rate benchmarks.",
                "diagnostic_confidence": "high",
                "priority_rank": 1,
                "priority_reasoning": f"Significant concentration of assets in investment book ({inv_ratio:.1%} of total balance sheet); recoverability depends entirely on investee project completion and operational viability.",
            })
        else:
            # CWIP / Fixed Asset Risk
            cwip_val = lookup.get("cwip", 0.0)
            contributing = [
                {"signal": s["signal"], "source_trace": s["source_trace"]}
                for s in s_rc03 if s["id"] == "SIG_CWIP_CONCENTRATION"
            ]
            if not contributing:
                contributing = [{
                    "signal": f"Capital Work-in-Progress of {_format_inr(cwip_val)} undergoing commissioning",
                    "source_trace": "financial_facts: CapitalWorkInProgress"
                }]
            active_clusters.append({
                "id": "RC03",
                "theme": C.CLUSTER_NAMES["RC03"],
                "raised": True,
                "reason": "",
                "contributing_signals": contributing,
                "alt_explanations": [
                    "Planned major infrastructure asset addition undergoing scheduled multi-year engineering execution."
                ],
                "metrics": _rc03_metrics(lookup),
                "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC03"]),
                "inherent_risk": "high" if cwip_val > RT.value("risk.cwip_significant_amount") else "medium",
                "significant_risk": cwip_val > RT.value("risk.cwip_significant_amount"),
                "control_implications": "Verify controls over project milestone inspection, capitalization approvals, and borrowing cost cut-off.",
                "recommended_response": {
                    "nature": "Physical inspection of project sites and technical milestone audit",
                    "timing": "Year-end capitalization review",
                    "extent": "Sample major capital vouchers, vendor contracts, and capitalization test certificates",
                },
                "specialist_referral": C.CLUSTER_SPECIALISTS["RC03"],
                "evidence_request": "Requisition: (1) CWIP ageing schedule by project, (2) Engineers' milestone completion certificates, (3) Board approvals for project cost revisions.",
                "diagnostic_confidence": "high",
                "priority_rank": 1 if cwip_val > RT.value("risk.cwip_significant_amount") else 3,
                "priority_reasoning": "High capital commitment in execution phase; requires physical and technical verification.",
            })

    # 4. RC04: Funding and Solvency Risk
    s_rc04 = cluster_signals["RC04"]
    if s_rc04:
        is_debt_free = (lookup.get("borrowings", 0) == 0)
        if is_debt_free:
            active_clusters.append({
                "id": "RC04",
                "theme": C.CLUSTER_NAMES["RC04"],
                "raised": True,
                "reason": "",
                "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc04],
                "alt_explanations": [
                    "Capital structure is 100% equity-funded by holding entity; zero external borrowing eliminates default and covenant non-compliance risk."
                ],
                "metrics": _rc04_metrics(lookup),
                "affected_assertions": ["Completeness of borrowings", "Presentation and disclosure"],
                "inherent_risk": "low",
                "significant_risk": False,
                "control_implications": "Verify treasury controls over opening bank accounts, credit facility mandates, and borrowing authorizations.",
                "recommended_response": {
                    "nature": "Search for unrecorded borrowings and verification of debt-free capital structure",
                    "timing": "Year-end audit testing",
                    "extent": "Search MCA-21 charge index (Form CHG-1 / CHG-4) and obtain direct bank confirmation of nil outstanding credit facilities across all operational bank accounts",
                },
                "specialist_referral": "None",
                "evidence_request": "Requisition: (1) MCA-21 Register of Charges / independent search report, (2) Bank confirmation certificates confirming nil sanctioned loans or credit lines, (3) Board minutes approving equity funding without debt leverage.",
                "diagnostic_confidence": "high",
                "priority_rank": 6,
                "priority_reasoning": "Debt-free balance sheet (₹0.00 borrowings) eliminates debt service default and covenant non-compliance risk.",
            })
        else:
            active_clusters.append({
                "id": "RC04",
                "theme": C.CLUSTER_NAMES["RC04"],
                "raised": True,
                "reason": "",
                "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc04],
                "alt_explanations": (
                    ["Accumulated losses or sponsor-funded project loans may explain the net-worth "
                     "position; support from the promoter or government, or a restructuring, may be "
                     "in place - to be evidenced, not assumed."]
                    if lookup.get("leverage_status") in (POS.NEGATIVE_NET_WORTH, POS.NIL_EQUITY) else
                    ["Leverage is structured via long-term project loans matched to asset life cycles."]
                ),
                "metrics": _rc04_metrics(lookup),
                "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC04"]),
                "inherent_risk": "high",
                "significant_risk": ((lookup.get("de_ratio") or 0) > RT.value("risk.de_significant")
                                     or lookup.get("leverage_status") in (POS.NEGATIVE_NET_WORTH, POS.NIL_EQUITY)),
                "control_implications": "Review treasury controls over debt covenant monitoring, limit sanctions, and debt service escrow accounts.",
                "recommended_response": {
                    "nature": "Bank loan circularisation and debt covenant compliance audit",
                    "timing": "Year-end confirmation",
                    "extent": "Direct bank confirmations for 100% of outstanding loan limits and interest rate agreements",
                },
                "specialist_referral": C.CLUSTER_SPECIALISTS["RC04"],
                "evidence_request": "Requisition: (1) Bank sanction letters, (2) Covenant compliance certificates, (3) Loan repayment schedules for upcoming 24 months.",
                "diagnostic_confidence": "high",
                "priority_rank": 2,
                "priority_reasoning": (
                    "Prioritised because borrowings are outstanding against negative or nil net worth, "
                    "which D/E cannot express; debt service capacity and covenant position need audit attention."
                    if lookup.get("leverage_status") in (POS.NEGATIVE_NET_WORTH, POS.NIL_EQUITY) else
                    "Prioritised due to elevated gearing and debt service requirements."),
            })

    # 5. RC05: Estimate and Reporting-Quality Risk
    s_rc05 = cluster_signals["RC05"]
    has_auditor_qual = any(s["id"] == "SIG_AUDITOR_QUALIFICATION" for s in s_rc05)

    if has_auditor_qual:
        active_clusters.append({
            "id": "RC05",
            "theme": "Estimate and Reporting-Quality Risk (Auditor Qualification)",
            "raised": True,
            "reason": "",
            "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc05],
            "alt_explanations": [
                "Management has provided formal explanations and remediation positions in the Board's Report regarding the statutory auditor's qualifications and observations."
            ],
            "metrics": _rc05_metrics(lookup),
            "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC05"]),
            "inherent_risk": "high",
            "significant_risk": True,
            "control_implications": "Review and remediate internal financial reporting controls (ICFR) cited in the statutory auditor's qualification or adverse remarks.",
            "recommended_response": {
                "nature": "Substantive evaluation of statutory auditor qualifications and management remediation under SA 705",
                "timing": "Immediate planning and year-end review",
                "extent": "Review 100% of qualified audit issues, assess quantifiable impact on balance sheet and P&L, and inspect Audit Committee minutes and management replies",
            },
            "specialist_referral": "Legal / Accounting Technical Specialist",
            "evidence_request": "Requisition: (1) Full Statutory Audit Report with Annexures and CARO report, (2) Management's formal reply to audit qualifications, (3) Audit Committee minutes approving remediation.",
            "diagnostic_confidence": "high",
            "priority_rank": 1,
            "priority_reasoning": "Top priority: Statutory auditor has issued a formal qualification / adverse remark directly impacting financial statement reliability (SA 705 / SA 315).",
        })
    elif s_rc05:
        active_clusters.append({
            "id": "RC05",
            "theme": C.CLUSTER_NAMES["RC05"],
            "raised": True,
            "reason": "",
            "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc05],
            "alt_explanations": [
                "Provisions and estimates reflect management's best assessment based on actuarial valuations and contract clauses."
            ],
            "metrics": _rc05_metrics(lookup),
            "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC05"]),
            "inherent_risk": "medium",
            "significant_risk": False,
            "control_implications": "Verify controls over management estimation methodology, expert assumptions, and impairment testing models.",
            "recommended_response": {
                "nature": "Evaluation of estimation models and expert assumption benchmarking",
                "timing": "Year-end testing",
                "extent": "Substantive testing of provision models and comparison against historical actuals",
            },
            "specialist_referral": C.CLUSTER_SPECIALISTS["RC05"],
            "evidence_request": "Requisition: (1) Actuarial valuation reports, (2) Valuation models and discount rate rationale, (3) Management representation letters.",
            "diagnostic_confidence": "high",
            "priority_rank": 4,
            "priority_reasoning": "Subject to estimation uncertainty; requires corroboration of management judgment.",
        })

    # 6. RC06: Government-Dependency and Grant Risk
    s_rc06 = cluster_signals["RC06"]
    if s_rc06:
        active_clusters.append({
            "id": "RC06",
            "theme": C.CLUSTER_NAMES["RC06"],
            "raised": True,
            "reason": "",
            "contributing_signals": [{"signal": s["signal"], "source_trace": s["source_trace"]} for s in s_rc06],
            "alt_explanations": [
                "Government grant and subsidy recognitions comply with specified milestone conditions under Ind AS 20."
            ],
            "metrics": _rc06_metrics(lookup),
            "affected_assertions": list(C.CLUSTER_ASSERTIONS["RC06"]),
            "inherent_risk": "medium",
            "significant_risk": False,
            "control_implications": "Review compliance controls for grant sanction covenants and end-use certificates.",
            "recommended_response": {
                "nature": "Grant compliance audit and clawback risk review",
                "timing": "Year-end testing",
                "extent": "Review 100% of material government grant sanction terms and utilization certificates",
            },
            "specialist_referral": C.CLUSTER_SPECIALISTS["RC06"],
            "evidence_request": "Requisition: (1) Government sanction orders, (2) Utilization certificates submitted to authorities, (3) Correspondence regarding pending claims.",
            "diagnostic_confidence": "high",
            "priority_rank": 5,
            "priority_reasoning": "Pertains to compliance with sovereign grant stipulations and clawback conditions.",
        })

    # Sort raised clusters by priority_rank and assign 1..N
    active_clusters.sort(key=lambda x: x.get("priority_rank", 99))
    for i, c in enumerate(active_clusters, 1):
        c["priority_rank"] = i

    # Generate unraised entries for remaining canonical clusters
    active_ids = {c["id"] for c in active_clusters}
    unraised_clusters: list[dict[str, Any]] = []
    for cid in C.CLUSTER_IDS:
        if cid not in active_ids:
            unraised_clusters.append({
                "id": cid,
                "theme": C.CLUSTER_NAMES[cid],
                "raised": False,
                "reason": unraised_reason(cid, lookup),
                "contributing_signals": [],
                "alt_explanations": [],
                "metrics": _cluster_metrics(cid, lookup),
                "affected_assertions": list(C.CLUSTER_ASSERTIONS[cid]),
                "inherent_risk": "low",
                "significant_risk": False,
                "control_implications": "Standard internal financial control (IFC) testing under regular audit cycle.",
                "recommended_response": {
                    "nature": "Standard analytical procedures and management inquiries",
                    "timing": "Normal year-end schedule",
                    "extent": "Standard sample size under normal materiality thresholds",
                },
                "specialist_referral": "None",
                "evidence_request": "Routine audit lead schedules under standard audit plan.",
                "diagnostic_confidence": "low" if (cid == "RC04" and lookup.get("leverage_status") == POS.MISSING) else "high",
                "priority_rank": None,
                "priority_reasoning": "Not raised; financial indicators and disclosures remain within normal operational parameters.",
            })

    all_clusters = active_clusters + unraised_clusters

    return {
        "risk_clusters": all_clusters,
        "interactions": interactions,
    }
