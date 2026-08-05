"""Audit findings assembly, evidence catalogue and management queries (spec 13, 14,
18.2, Appendices B and C).

Turns the deterministic mapping / screen / relationship / materiality outputs into
the spec's findings schema — each finding carries observation → expectation → gap →
assertion[] → risk_basis[] → risk_rating → proposed_response → evidence_requested[]
→ mandatory safe_limitation — plus a consolidated, de-duplicated evidence-request
list and a management-query list. All wording is risk-indicator / no-opinion by
construction, so the deterministic output is safe even without the LLM.
"""

from __future__ import annotations

from yukta_rag.audit.audit_materiality import rate_risk

# spec 1.2 — mandatory safe limitation on every finding
SAFE_LIMITATION = (
    "This observation is a risk indicator from the trial balance only. It is not a "
    "conclusion on misstatement, fraud, non-compliance, irregularity, recoverability "
    "or going concern. It requires corroboration through the audit procedures and "
    "evidence listed."
)

# spec 14.2 — permitted closing wording
NO_OPINION_CLOSING = (
    "Based solely on the trial balance supplied for {entity}, the accounts and "
    "relationships above warrant audit attention during planning. These are risk "
    "observations, not conclusions on the accounts, and should be corroborated with "
    "the records requested before any view is formed. Qualitative readings used above "
    "(e.g. 'consistent', 'plausible order', 'not indicating an exception') are "
    "planning-stage observations only, not substantive conclusions. This analysis "
    "does not express an audit opinion and does not certify the financial statements."
)

# Appendix C — account area/FSLI -> primary assertions + first evidence
_FSLI_ASSERTIONS = {
    "Revenue from Operations": ["occurrence", "completeness", "accuracy", "cut-off", "classification"],
    "Other Income": ["occurrence", "completeness", "classification"],
    "Trade Receivables": ["existence", "valuation", "rights", "cut-off"],
    "Cash and Cash Equivalents": ["existence", "completeness", "rights", "classification"],
    "Inventories": ["existence", "valuation", "completeness", "rights"],
    "Property, Plant and Equipment": ["existence", "rights", "valuation", "classification"],
    "Capital Work-in-Progress": ["existence", "valuation", "completeness", "classification"],
    "Investments": ["existence", "valuation", "rights", "classification"],
    "Loans (financial asset)": ["existence", "valuation", "rights", "regularity"],
    "Trade Payables": ["completeness", "obligation", "cut-off", "classification"],
    "Borrowings": ["completeness", "obligation", "accuracy", "classification"],
    "Provisions": ["completeness", "valuation", "presentation"],
    "Statutory Dues / Other Current Liabilities": ["completeness", "accuracy", "regularity"],
    "Deferred Tax Liability": ["valuation", "recognition", "presentation"],
    "Equity Share Capital": ["rights", "completeness", "presentation"],
    "Other Equity / Reserves": ["completeness", "presentation", "regularity"],
}
_FSLI_EVIDENCE = {
    "Revenue from Operations": ["ledger", "contracts/invoices", "GST returns", "cut-off testing"],
    "Trade Receivables": ["ageing", "confirmations", "subsequent receipts", "ECL/provision working"],
    "Cash and Cash Equivalents": ["bank reconciliation", "bank confirmation", "cash verification",
                                  "list of bank accounts"],
    "Inventories": ["stock records", "physical verification", "valuation/NRV working", "ageing"],
    "Property, Plant and Equipment": ["fixed asset register", "title deeds", "physical verification",
                                      "depreciation/impairment working"],
    "Capital Work-in-Progress": ["CWIP ageing", "project status", "capitalisation policy", "approvals"],
    "Investments": ["holding statements", "confirmations", "valuation/impairment working"],
    "Loans (financial asset)": ["ageing", "sanctions/agreements", "confirmations", "recovery status"],
    "Trade Payables": ["ageing", "vendor confirmations", "subsequent payments", "MSME declarations"],
    "Borrowings": ["loan agreements", "confirmations", "repayment schedule", "interest working",
                   "covenant status"],
    "Provisions": ["provision working", "legal/tax confirmations", "board minutes"],
    "Statutory Dues / Other Current Liabilities": ["returns/challans", "reconciliations",
                                                   "due-date ageing"],
}

# Appendix B / spec 5.4 — sensitive tag -> finding template
_SENSITIVE_META = {
    "grant_subsidy": {"label": "Grants / subsidies",
        "assertions": ["classification", "completeness", "presentation", "regularity"],
        "regularity": True, "propriety": True,
        "response": "Verify grant classification (income / deferred income / asset) against sanction "
                    "conditions and utilisation.",
        "evidence": ["sanction order", "grant conditions", "fund-utilisation statement",
                     "utilisation certificate", "bank trail"]},
    "statutory_dues": {"label": "Statutory dues (GST/TDS/PF/ESI etc.)",
        "assertions": ["completeness", "accuracy", "regularity"], "regularity": True, "propriety": False,
        "response": "Run return-wise reconciliation and due-date ageing of statutory balances.",
        "evidence": ["GST/TDS/PF/ESI returns and challans", "reconciliations", "due-date ageing",
                     "demand/assessment orders", "subsequent payment evidence"]},
    "msme": {"label": "MSME dues",
        "assertions": ["completeness", "obligation", "presentation"], "regularity": True, "propriety": False,
        "response": "Confirm MSME classification and any interest liability under the MSMED Act.",
        "evidence": ["MSME declarations", "ageing", "interest computation"]},
    "csr": {"label": "CSR obligations",
        "assertions": ["completeness", "accuracy", "regularity"], "regularity": True, "propriety": True,
        "response": "Reconcile CSR spend/unspent against the Section 135 computation and approvals.",
        "evidence": ["Section 135 computation", "board/CSR-committee approvals", "project records",
                     "unspent-CSR details"]},
    "related_party": {"label": "Possible related-party / inter-company balances",
        "assertions": ["completeness", "presentation", "cut-off"], "regularity": False, "propriety": True,
        "response": "Flag as a possible related-party indicator only; obtain the RPT register and "
                    "reconcile counterparty balances.",
        "evidence": ["related-party register", "board approvals", "agreements", "confirmations",
                     "reconciliation"]},
    "suspense_control": {"label": "Suspense / control / clearing accounts",
        "assertions": ["classification", "accuracy", "completeness"], "regularity": False, "propriety": False,
        "response": "Obtain account-wise reconciliation, ageing and a clearance plan.",
        "evidence": ["account-wise reconciliation", "ageing", "transaction listing/journal dump",
                     "clearance plan", "approval trail"]},
    "propriety": {"label": "Write-offs / waivers / losses (propriety-sensitive)",
        "assertions": ["completeness", "regularity", "presentation"], "regularity": True, "propriety": True,
        "response": "Verify competent sanction and recovery analysis for write-offs/waivers/losses.",
        "evidence": ["competent sanction", "supporting justification", "recovery analysis"]},
    "deposit": {"label": "Deposits / retention / EMD",
        "assertions": ["completeness", "obligation", "valuation"], "regularity": False, "propriety": True,
        "response": "Obtain party-wise ageing; review long-outstanding and lapsing balances.",
        "evidence": ["party-wise ledger", "ageing", "terms/agreements", "lapsing analysis"]},
    "advance": {"label": "Advances (supplier/staff/mobilisation)",
        "assertions": ["existence", "valuation", "regularity"], "regularity": True, "propriety": True,
        "response": "Review recoverability, sanction and adjustment status of advances.",
        "evidence": ["party-wise ageing", "sanctions", "adjustment status", "recovery evidence"]},
}

_SEV_TO_RATING = {"high": "high", "medium": "medium", "low": "low",
                  "information_request": "information_request"}


def _finding(**kw) -> dict:
    # every finding carries the full spec 1.1(4) audit language
    kw.setdefault("safe_limitation", SAFE_LIMITATION)
    kw.setdefault("regularity_flag", False)
    kw.setdefault("propriety_flag", False)      # 1.1(4) regularity / PROPRIETY flag
    kw.setdefault("calculation_basis", None)    # 1.1(6) show the calculation basis
    kw.setdefault("source_row_id", None)        # 1.1(1) source-row lineage
    return kw


def _rows(items: list[dict], key: str = "source_row") -> list:
    """Representative source-row ids from a list of example dicts (lineage)."""
    return [it[key] for it in items if it.get(key) is not None][:8] or None


def _named(items: list[dict], n: int = 3) -> str:
    """Client-format rule C10/F17 — name the largest contributing GL codes/
    accounts (not just a count), e.g. 'GL 401021 Suspense-Freight (120,000);
    GL 401022 Suspense-Duty (95,000)'. Items should already be sorted largest
    first by the caller."""
    parts = []
    for it in items[:n]:
        code, name, net = it.get("code"), it.get("account") or it.get("name"), it.get("net")
        label = f"GL {code} {name}" if code else name
        parts.append(f"{label} ({net:,.0f})" if net is not None else label)
    return "; ".join(parts)


def _comparison_findings(risk: dict) -> list[dict]:
    """Trial-Balance-mode consolidation — comparison-driven risks (from
    ``compare_trial_balances()``'s own risk register, folded in when a prior
    period is picked) become ordinary Focus Areas instead of a separate
    risk-register list, so the unified report has one findings surface."""
    out = []
    for r in (risk or {}).get("risks", []):
        label = (r.get("category") or "comparison").replace("_", " ").title()
        out.append(_finding(
            account=f"Comparison — {label}", fsli=None, amount=0,
            normal_balance_expectation="n/a",
            observation=r.get("detail", ""),
            expectation="Year-over-year movement should be explicable by known business change.",
            gap="Movement flagged by period-over-period comparison; not yet corroborated.",
            assertion=["classification"], risk_basis=["relationship"],
            risk_rating=r.get("severity", "medium"),
            proposed_response="Obtain the explanation and supporting reconciliation for this "
                              "movement from management.",
            evidence_requested=["prior-year reconciliation", "explanation for the movement"]))
    return out


def build_findings(mapping: dict, screen: dict, relationships: dict, materiality: dict,
                   input_quality: dict, clearing: dict | None = None,
                   comparison_risk: dict | None = None,
                   mirror_pairs: dict | None = None) -> dict:
    """Assemble risk-ranked findings + consolidated evidence and management-query lists."""
    prov_mat = materiality.get("provisional_overall_materiality")
    suff = input_quality.get("data_sufficiency", "medium")
    findings: list[dict] = []

    # --- A. information requests from input quality (kept separate from audit risk) ---
    if not input_quality["balanced"]:
        findings.append(_finding(
            account="Trial balance (whole)", fsli=None,
            amount=input_quality["trial_balance_residual"], normal_balance_expectation="debits = credits",
            observation=f"Trial balance does not tie out; residual "
                        f"{input_quality['trial_balance_residual']:,.2f}.",
            expectation="Total debits should equal total credits.",
            gap="Arithmetic integrity fails; analytics are unreliable until corrected.",
            assertion=["accuracy"], risk_basis=["data_quality"], risk_rating="information_request",
            data_sufficiency="low",
            calculation_basis=f"residual (total debits - total credits) = "
                              f"{input_quality['trial_balance_residual']:,.2f}",
            proposed_response="Obtain a corrected, complete trial balance before reliance.",
            evidence_requested=["corrected trial balance", "export/source report", "sign convention"]))
    if input_quality["n_unmapped"] and input_quality["mapping_confidence_summary"].get("unmapped", 0):
        findings.append(_finding(
            account=f"{input_quality['n_unmapped']} unmapped account(s)", fsli=None, amount=0,
            normal_balance_expectation="n/a",
            observation=f"{input_quality['n_unmapped']} account(s) could not be mapped to an FSLI "
                        f"(examples: {', '.join(input_quality['unmapped_accounts'][:6])}).",
            expectation="Every material account should map to a Schedule III line item.",
            gap="Unmapped accounts cannot be risk-rated.",
            assertion=["classification"], risk_basis=["data_quality"], risk_rating="information_request",
            data_sufficiency="low",
            calculation_basis=f"{input_quality['n_unmapped']} of "
                              f"{input_quality['n_accounts']} accounts unmapped",
            proposed_response="Do not force a mapping; obtain the chart of accounts / management "
                              "FSLI mapping.",
            evidence_requested=["chart of accounts", "management FSLI mapping"]))

    # --- B. sensitive heads (nature/context risk) ---
    for h in screen.get("sensitive_heads", []):
        meta = _SENSITIVE_META.get(h["tag"])
        if not meta:
            continue
        # true combined net across ALL tagged ledgers (screen supplies total_net;
        # fall back to the examples' sum only if an older screen dict lacks it)
        total = h.get("total_net")
        if total is None:
            total = round(sum(e["net"] for e in h["examples"]), 2)
        rating, basis = rate_risk(amount=total, materiality=prov_mat, sensitive=True)
        named = _named(sorted(h["examples"], key=lambda e: abs(e["net"]), reverse=True))
        findings.append(_finding(
            account=f"{meta['label']} ({h['count']} ledger(s))",
            fsli=None, amount=total, normal_balance_expectation="n/a",
            observation=f"{h['count']} {meta['label'].lower()} ledger(s), combined net "
                        f"{total:,.0f} — material by nature/context regardless of size; the "
                        "trial balance alone cannot confirm conditions, sanction, utilisation "
                        "or reconciliation." + (f" Largest: {named}." if named else ""),
            expectation="Sensitive heads are material by nature/context regardless of size.",
            gap="TB cannot establish conditions, sanction, utilisation or reconciliation.",
            assertion=meta["assertions"], risk_basis=basis, risk_rating=rating,
            regularity_flag=meta["regularity"], propriety_flag=meta.get("propriety", False),
            data_sufficiency=suff, source_row_id=_rows(h["examples"]),
            calculation_basis=f"{h['count']} ledger(s), combined net {total:,.0f}"
                              + (f" vs provisional materiality {prov_mat:,.0f}" if prov_mat else ""),
            proposed_response=meta["response"], evidence_requested=meta["evidence"]))

    # --- C. relationship analytics ---
    for rel in relationships.get("relationships", []):
        if rel["severity"] == "none":
            continue  # "checked, no exception" — reported in Relationship Analytics only, not a Focus Area
        low = rel["relationship"].lower()
        propriety = any(k in low for k in ("grant", "loans/advances"))
        findings.append(_finding(
            account=rel["relationship"], fsli=None, amount=0, normal_balance_expectation="n/a",
            observation=rel["observation"], expectation=rel["expectation"], gap=rel["gap"],
            assertion=rel["assertions"], risk_basis=["relationship"],
            risk_rating=_SEV_TO_RATING.get(rel["severity"], "medium"), data_sufficiency=suff,
            regularity_flag=propriety, propriety_flag=propriety,
            calculation_basis=rel.get("calculation_basis"),
            proposed_response="Reconcile the relationship; valid reasons include: "
                              + "; ".join(rel["valid_reasons"]) + ".",
            evidence_requested=rel["evidence_requested"]))

    # --- D. abnormal signs (top groups) ---
    for g in screen.get("abnormal_signs", [])[:6]:
        fsli = g["fsli"]
        rating, basis = rate_risk(amount=g["total"], materiality=prov_mat,
                                  relationship_gap=False, data_quality_issue=False,
                                  sensitive=False)
        named = _named(g["examples"])  # already sorted largest-first, audit_screen.py
        findings.append(_finding(
            account=f"Abnormal sign in {fsli} ({g['count']} ledger(s))", fsli=fsli, amount=g["total"],
            normal_balance_expectation="see note",
            observation=f"{g['count']} {fsli} ledger(s) carrying a balance on the abnormal side, "
                        f"combined net {g['total']:,.0f} — opposite the normal side for this "
                        "account class." + (f" Largest: {named}." if named else ""),
            expectation="Account class should carry its normal balance.",
            gap=g["note"] or "Abnormal debit/credit side for the account class.",
            assertion=_FSLI_ASSERTIONS.get(fsli, ["classification"]), risk_basis=basis or ["nature"],
            risk_rating=rating if rating != "low" else "medium", data_sufficiency=suff,
            source_row_id=_rows(g["examples"]),
            calculation_basis=f"net {g['total']:,.0f} across {g['count']} abnormal-sign ledger(s)",
            proposed_response="Obtain the ledger, ageing and reconciliation to explain the abnormal side.",
            evidence_requested=_FSLI_EVIDENCE.get(fsli, ["account-wise ledger", "ageing",
                                                         "reconciliation"])))

    # --- E. concentration (largest accounts) ---
    for c in screen.get("concentration", [])[:5]:
        rating, basis = rate_risk(amount=c["net"], materiality=prov_mat)
        findings.append(_finding(
            account=c["account"], fsli=c["fsli"], amount=c["net"],
            normal_balance_expectation="n/a",
            observation=f"{c['account']} (net {c['net']:,.0f}) is a material share of its "
                        f"{c['category']} class — concentrated by value.",
            expectation="Material/concentrated accounts warrant a planned response.",
            gap="Concentration by value; corroboration needed for the balance.",
            assertion=_FSLI_ASSERTIONS.get(c["fsli"], ["valuation", "existence"]),
            risk_basis=basis, risk_rating=rating if rating != "low" else "medium",
            data_sufficiency=suff,
            calculation_basis=f"net {c['net']:,.0f}, a material share of the {c['category']} "
                              "class total",
            proposed_response="Perform focused substantive procedures on this account.",
            evidence_requested=_FSLI_EVIDENCE.get(c["fsli"], ["supporting schedule", "ledger"]),
            source_row_id=[c["source_row"]] if c.get("source_row") is not None else None))

    # --- F. round-sum / repeated amounts ---
    if screen.get("round_sums"):
        rs = screen["round_sums"][:5]
        n_rs = screen.get("n_round_sums", len(screen["round_sums"]))
        rs_total = screen.get("round_sums_total")
        if rs_total is None:
            rs_total = round(sum(r["net"] for r in screen["round_sums"]), 2)
        named = _named(rs)
        findings.append(_finding(
            account="Round-sum balances", fsli=None,
            amount=rs_total, normal_balance_expectation="n/a",
            observation=f"{n_rs} large round-sum balance(s), combined {rs_total:,.0f} — a "
                        "pattern associated with manual estimates or parked balances."
                        + (f" Largest: {named}." if named else ""),
            expectation="Large balances should be supported by computation, not round estimates.",
            gap="Round sums can indicate manual estimates or parked balances.",
            assertion=["valuation", "accuracy"], risk_basis=["nature"], risk_rating="medium",
            data_sufficiency=suff, source_row_id=_rows(rs),
            calculation_basis=f"{n_rs} round-sum balance(s), combined {rs_total:,.0f}",
            proposed_response="Obtain the computation and approval trail for round-sum balances.",
            evidence_requested=["provision/estimate computation", "approval trail"]))

    # --- G. unresolved clearing/inter-unit/statistical series (client-format rule C9) ---
    for g in (clearing or {}).get("unresolved", []):
        named = _named(sorted(g["accounts"], key=lambda a: abs(a.get("net") or 0), reverse=True))
        findings.append(_finding(
            account=f"Unresolved clearing/inter-unit series (GL {g['prefix']}xx)", fsli=None,
            amount=g["net"], normal_balance_expectation="nets to nil",
            observation=f"GL series {g['prefix']}xx behaves like an internal clearing/inter-unit "
                        f"series (gross activity {g['gross']:,.0f}) but does NOT resolve to nil — "
                        f"combined net {g['net']:,.0f} remains outstanding." + (f" Largest: {named}." if named else ""),
            expectation="A genuine clearing/inter-unit/statistical series should net to nil or near-nil.",
            gap="Net balance outstanding in a series expected to clear — excluded from FSLI totals "
                "and the Financial Snapshot pending reconciliation.",
            assertion=["completeness", "classification", "accuracy"], risk_basis=["data_quality"],
            risk_rating="high", data_sufficiency=suff,
            calculation_basis=f"gross {g['gross']:,.0f}, net {g['net']:,.0f} across "
                              f"{len(g['accounts'])} GL {g['prefix']}xx account(s)",
            proposed_response="Obtain the clearing/inter-unit reconciliation and clearance plan for "
                              "this GL series before relying on FSLI totals that exclude it.",
            evidence_requested=["account-wise reconciliation", "inter-unit confirmation",
                               "clearance plan", "transaction listing"]))

    # --- H. unresolved mirrored clearing pairs (bug-3 fix) ---
    for p in (mirror_pairs or {}).get("unresolved", []):
        a, b = p["accounts"]
        findings.append(_finding(
            account=f"Unresolved mirrored clearing pair ({a['name']} / {b['name']})",
            fsli=None, amount=p["net"], normal_balance_expectation="nets to nil",
            observation=f"{a.get('code') or '—'} {a['name']} ({a['net']:,.0f}) and "
                        f"{b.get('code') or '—'} {b['name']} ({b['net']:,.0f}) appear to be an "
                        f"internal transfer/contra pair (gross {p['gross']:,.0f}) but do NOT net "
                        f"to nil — combined net {p['net']:,.0f} remains outstanding.",
            expectation="A genuine internal transfer/contra pair should net to nil or near-nil.",
            gap="Net balance outstanding between two accounts expected to offset — excluded from "
                "FSLI totals and the Financial Snapshot pending reconciliation.",
            assertion=["completeness", "classification", "accuracy"], risk_basis=["data_quality"],
            risk_rating="high", data_sufficiency=suff,
            calculation_basis=f"gross {p['gross']:,.0f}, net {p['net']:,.0f} across the pair",
            proposed_response="Obtain the reconciliation and clearance status for this "
                              "transfer/contra pair before relying on FSLI totals that exclude it.",
            evidence_requested=["account-wise reconciliation", "inter-unit confirmation",
                               "clearance plan", "transaction listing"]))

    # --- I. comparison-driven risks (Trial Balance mode consolidation) ---
    findings.extend(_comparison_findings(comparison_risk))

    # rank findings: high > medium > low > information_request-last? spec ranks by risk;
    # keep information_request items grouped last (they are prerequisites, not risks).
    rank = {"high": 0, "medium": 1, "low": 2, "information_request": 3}
    findings.sort(key=lambda f: rank.get(f["risk_rating"], 4))
    # client-format rule F17 — stable reference id per finding, assigned after final ordering
    for i, f in enumerate(findings, start=1):
        f["reference_id"] = f"F{i:02d}"

    # consolidated, de-duplicated evidence-request list (spec 18.2), first-seen order
    seen, evidence_list = set(), []
    for f in findings:
        for e in f.get("evidence_requested", []):
            k = e.lower()
            if k not in seen:
                seen.add(k)
                evidence_list.append(e)

    # management-query list from the top substantive findings
    mgmt = []
    for f in findings:
        if f["risk_rating"] in ("high", "medium"):
            mgmt.append(f"Explain and provide support for: {f['account']} — {f['proposed_response']}")
    # de-dup, cap
    mgmt = list(dict.fromkeys(mgmt))[:15]

    summary = {r: sum(1 for f in findings if f["risk_rating"] == r)
               for r in ("high", "medium", "low", "information_request")}
    return {"findings": findings, "summary": summary,
            "evidence_request_list": evidence_list, "management_query_list": mgmt}
