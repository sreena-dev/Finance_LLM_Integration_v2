"""Relationship analytics and expected linkages for Audit mode (spec 7).

Tests relationships visible inside a single TB (revenue↔receivables↔GST,
purchases↔payables↔ITC, PPE↔depreciation, borrowings↔finance cost,
payroll↔statutory dues, loans↔interest income, grants↔utilisation …). Each check
states the expected linkage, the quantified gap, valid reasons a mismatch may
exist (spec 7.2) and the evidence that resolves it. Indicators only — never a
conclusion. A relationship is skipped when its inputs are absent/immaterial.
"""

from __future__ import annotations

from yukta_rag.audit.audit_config import (DEBTOR_INTENSITY_HIGH,
                                          GST_OUTPUT_TO_REVENUE_MIN,
                                          ROLE_ABSENT_PCT_OF_BASE,
                                          ROLE_BASE_FALLBACK_PCT,
                                          ROLE_PRESENT_PCT_OF_BASE)
from yukta_rag.core.financial_math import ratio as _fm_ratio
from yukta_rag.trial_balance.tb_tools import _SIGN

# role -> how to detect it from a mapped account (by FSLI and/or name keyword).
# Magnitudes are sign-adjusted so every role total is a positive amount.
_FSLI_ROLE = {
    "Revenue from Operations": "revenue",
    "Other Income": "other_income",
    "Trade Receivables": "receivables",
    "Cash and Cash Equivalents": "cash_bank",
    "Trade Payables": "payables",
    "Cost of Materials Consumed / Purchases": "purchases",
    "Property, Plant and Equipment": "ppe",
    "Capital Work-in-Progress": "cwip",
    "Depreciation and Amortisation": "depreciation",
    "Borrowings": "borrowings",
    "Finance Costs": "finance_cost",
    "Employee Benefits Expense": "salary",
    "Loans (financial asset)": "loans_given",
    "Inventories": "inventory",
    "Investments": "investments",
}
_KW_ROLE = [
    ("gst_output", ("gst output", "output gst", "gst payable", "output tax", "igst payable",
                    "cgst payable", "sgst payable")),
    ("gst_input", ("gst input", "input gst", "input tax", "itc", "cenvat", "igst input",
                   "cgst input", "sgst input")),
    ("statutory_payroll", ("pf ", "provident fund", "esi", "professional tax", "tds payable",
                           "tds on salary", "employee state")),
    ("interest_income", ("interest income", "interest received", "interest on loan",
                         "interest on advance")),
    ("grants", ("grant", "subsidy", "grant-in-aid", "budgetary support")),
    ("deferred_tax", ("deferred tax",)),
    ("depreciation", ("depreciation", "amortis", "amortiz")),
    ("finance_cost", ("finance cost", "interest paid", "interest expense", "interest on borrowing")),
]


def _role_totals(mapping: dict) -> tuple[dict, dict]:
    """Sum sign-adjusted magnitudes per role across the mapped accounts, plus a
    per-role mapping-coverage percentage (high/medium-confidence share of that
    role's total absolute value — mirrors ``audit_normalize.value_coverage()``'s
    TB-wide logic, scoped per role) so ratios built from these totals can be
    gated per-pair rather than only by a single TB-wide flag (spec QNT-04)."""
    roles: dict = {}
    total_abs: dict = {}
    reliable_abs: dict = {}
    for a in mapping["accounts"]:
        cat = a.get("category")
        mag = _SIGN.get(cat, 0) * a["net"] if cat else 0.0
        low = f" {a['name'].lower()} "
        hit = set()
        r = _FSLI_ROLE.get(a.get("fsli"))
        if r:
            hit.add(r)
        for role, kws in _KW_ROLE:
            if any(k in low for k in kws):
                hit.add(role)
        abs_net = abs(a["net"])
        reliable = a.get("mapping_confidence") in ("high", "medium")
        for role in hit:
            roles[role] = round(roles.get(role, 0.0) + mag, 2)
            total_abs[role] = total_abs.get(role, 0.0) + abs_net
            if reliable:
                reliable_abs[role] = reliable_abs.get(role, 0.0) + abs_net
    role_coverage_pct = {role: round(reliable_abs.get(role, 0.0) / total * 100, 1)
                         for role, total in total_abs.items() if total}
    return roles, role_coverage_pct


def _present(roles: dict, key: str, base: float) -> bool:
    return abs(roles.get(key, 0.0)) >= max(1.0, ROLE_PRESENT_PCT_OF_BASE * base)


def _absent(roles: dict, key: str, base: float) -> bool:
    return abs(roles.get(key, 0.0)) < max(1.0, ROLE_ABSENT_PCT_OF_BASE * base)


def analyze_relationships(mapping: dict) -> dict:
    """Return {relationships: [...]} — each a quantified, evidence-backed indicator.

    Symmetric by design: most relationships record BOTH an anomalous outcome
    (severity high/medium/low, a genuine gap worth corroborating) and a
    "checked, no exception" outcome (severity "none") when the expected
    counterpart IS present — audit-planning notes should also record where a
    routine risk indicator is not present, not only where one is."""
    roles, role_coverage_pct = _role_totals(mapping)
    base = max(roles.get("revenue", 0.0), roles.get("purchases", 0.0),
               roles.get("ppe", 0.0), roles.get("borrowings", 0.0),
               sum(abs(a["net"]) for a in mapping["accounts"]) * ROLE_BASE_FALLBACK_PCT, 1.0)
    out = []

    def add(key, severity, observation, expectation, gap, valid_reasons, assertions, evidence):
        # the observation already carries the numbers; it doubles as the calculation basis
        out.append({"relationship": key, "severity": severity, "observation": observation,
                    "expectation": expectation, "gap": gap, "valid_reasons": valid_reasons,
                    "assertions": assertions, "evidence_requested": evidence,
                    "calculation_basis": observation})

    def ratio(n, d):
        return _fm_ratio(roles.get(n, 0.0), roles.get(d, 0.0), 3)

    rev = roles.get("revenue", 0.0)

    # Revenue vs receivables (debtor intensity, absolute figures + qualitative reading only —
    # client-format rule B6: no computed ratio/percentage/day-count in the observation text)
    if _present(roles, "revenue", base) and _present(roles, "receivables", base):
        di = ratio("receivables", "revenue")
        sev = "high" if di is not None and di >= DEBTOR_INTENSITY_HIGH else "medium"
        reading = ("a high proportion of revenue remains tied up in receivables"
                  if sev == "high" else "a plausible proportion of revenue tied up in receivables")
        add("Revenue vs Trade Receivables", sev,
            f"Trade receivables {roles['receivables']:,.0f} against revenue {rev:,.0f} — "
            f"{reading}.",
            "Receivables should be reasonable relative to revenue and credit terms.",
            "TB does not show ageing, credit terms or collections.",
            ["cash sales", "advances", "long credit terms", "government-customer delays",
             "factoring", "related-party balances"],
            ["existence", "valuation", "rights", "cut-off"],
            ["debtors ageing", "confirmations", "subsequent receipts", "disputes",
             "ECL/provision working", "credit policy"])

    # Revenue vs GST output
    if _present(roles, "revenue", base):
        go = roles.get("gst_output", 0.0)
        if _absent(roles, "gst_output", base) or (rev and go / rev < GST_OUTPUT_TO_REVENUE_MIN):
            add("Revenue vs GST output liability", "medium",
                f"Revenue {rev:,.0f} but GST output {go:,.0f} appears low/absent relative to revenue.",
                "Book revenue should reconcile to GST returns after exempt/zero-rated/non-GST items.",
                "TB cannot show taxability or timing.",
                ["exempt revenue", "zero-rated exports", "non-GST grants", "GST on advances",
                 "credit notes", "timing differences"],
                ["completeness", "accuracy", "classification"],
                ["GST returns", "revenue-GST reconciliation", "taxability matrix",
                 "credit-note register"])
        else:
            add("Revenue vs GST output liability", "none",
                f"Revenue {rev:,.0f} with a GST output liability of {go:,.0f} also present.",
                "Book revenue should reconcile to GST returns after exempt/zero-rated/non-GST items.",
                "No missing-counterpart exception noted; a GST output liability exists against "
                "revenue, which is a reasonable sign — taxability mix cannot be confirmed from the TB.",
                [], ["completeness", "accuracy"], [])

    # Purchases/expenses vs payables
    if _present(roles, "purchases", base):
        if _absent(roles, "payables", base):
            add("Purchases/expenses vs Trade Payables", "medium",
                f"Purchases/expenses {roles['purchases']:,.0f} but trade payables appear absent/immaterial.",
                "Payables should be reasonable relative to procurement and credit terms.",
                "Possible unrecorded liabilities or cut-off issue.",
                ["cash purchases", "advance-paid procurement", "year-end settlement"],
                ["completeness", "obligation", "cut-off", "classification"],
                ["payables ageing", "vendor confirmations", "subsequent payments",
                 "purchase cut-off testing"])
        else:
            add("Purchases/expenses vs Trade Payables", "none",
                f"Purchases/expenses {roles['purchases']:,.0f} with trade payables of "
                f"{roles.get('payables', 0.0):,.0f} also present.",
                "Payables should be reasonable relative to procurement and credit terms.",
                "No missing-counterpart exception noted; ageing and credit terms cannot be "
                "confirmed from the TB regardless.",
                [], ["completeness", "obligation"], [])

    # Purchases vs GST input
    if _present(roles, "purchases", base):
        if _absent(roles, "gst_input", base):
            add("Purchases vs GST input credit", "low",
                f"Purchases/expenses {roles['purchases']:,.0f} but GST input credit appears absent.",
                "Input credit should broadly relate to eligible taxable purchases.",
                "TB cannot show ITC eligibility.",
                ["exempt/blocked ITC", "composition scheme", "reverse charge", "capital-goods ITC"],
                ["completeness", "accuracy"],
                ["purchase register", "GST returns (2A/2B/3B)", "ITC reconciliation"])
        else:
            add("Purchases vs GST input credit", "none",
                f"Purchases/expenses {roles['purchases']:,.0f} with GST input credit of "
                f"{roles.get('gst_input', 0.0):,.0f} also present.",
                "Input credit should broadly relate to eligible taxable purchases.",
                "No missing-counterpart exception noted; ITC eligibility cannot be confirmed "
                "from the TB regardless.",
                [], ["completeness", "accuracy"], [])

    # PPE/CWIP vs depreciation
    if _present(roles, "ppe", base) or _present(roles, "cwip", base):
        if _absent(roles, "depreciation", base):
            add("PPE / CWIP vs Depreciation", "high",
                f"PPE {roles.get('ppe',0):,.0f} / CWIP {roles.get('cwip',0):,.0f} but depreciation "
                "appears absent/immaterial.",
                "Depreciation should be consistent with the depreciable asset base and period of use.",
                "TB cannot show useful lives, readiness for use or capitalisation dates.",
                ["fully depreciated assets", "assets not ready for use", "late additions",
                 "land/non-depreciable assets", "CWIP not yet capitalised"],
                ["existence", "valuation", "accuracy", "classification"],
                ["fixed asset register", "depreciation working", "capitalisation dates",
                 "useful-life policy", "impairment assessment"])
        else:
            add("PPE / CWIP vs Depreciation", "none",
                f"PPE {roles.get('ppe',0):,.0f} / CWIP {roles.get('cwip',0):,.0f} with depreciation "
                f"{roles.get('depreciation',0):,.0f} also present.",
                "Depreciation should be consistent with the depreciable asset base and period of use.",
                "No missing-counterpart exception noted; depreciation is present and of a "
                "plausible order relative to the asset base. Useful-life testing is not "
                "possible from the TB alone.",
                [], ["existence", "valuation"], [])

    # Borrowings vs finance cost
    if _present(roles, "borrowings", base):
        if _absent(roles, "finance_cost", base):
            add("Borrowings vs Finance cost", "high",
                f"Borrowings {roles['borrowings']:,.0f} but finance cost appears absent/immaterial.",
                "Borrowings ordinarily generate interest unless proven otherwise.",
                "TB cannot show terms or capitalisation.",
                ["interest-free government loan", "moratorium", "interest capitalised to CWIP",
                 "loan drawn near year end"],
                ["completeness", "accuracy", "obligation", "classification"],
                ["loan agreements", "sanction letters", "bank confirmations", "repayment schedules",
                 "interest computation", "capitalisation working", "covenant/default status"])
        else:
            add("Borrowings vs Finance cost", "none",
                f"Borrowings {roles['borrowings']:,.0f} with finance cost of "
                f"{roles.get('finance_cost', 0.0):,.0f} also present.",
                "Borrowings ordinarily generate interest unless proven otherwise.",
                "No missing-counterpart exception noted; the relationship appears broadly "
                "consistent. Loan-wise confirmation is recommended regardless.",
                [], ["completeness", "accuracy"], [])

    # Payroll vs statutory dues
    if _present(roles, "salary", base):
        if _absent(roles, "statutory_payroll", base):
            add("Payroll vs PF/ESI/TDS", "medium",
                f"Employee benefits {roles['salary']:,.0f} but PF/ESI/TDS dues appear absent.",
                "Payroll-related statutory deductions should exist where applicable.",
                "TB cannot show employee thresholds or outsourcing.",
                ["employees below thresholds", "outsourced/contract labour",
                 "government deputation", "exempt categories"],
                ["completeness", "accuracy", "regularity"],
                ["payroll register", "PF/ESI challans", "TDS returns/challans",
                 "employee classification", "contractor agreements"])
        else:
            add("Payroll vs PF/ESI/TDS", "none",
                f"Employee benefits {roles['salary']:,.0f} with PF/ESI/TDS-tagged dues of "
                f"{roles.get('statutory_payroll', 0.0):,.0f} also present.",
                "Payroll-related statutory deductions should exist where applicable.",
                "No missing-counterpart exception noted; statutory employee-benefit accounts "
                "are present. Threshold/contractor-substitution testing is not possible from the TB.",
                [], ["completeness", "accuracy"], [])

    # Loans given vs interest income
    if _present(roles, "loans_given", base):
        if _absent(roles, "interest_income", base):
            add("Loans/advances vs Interest income", "medium",
                f"Loans/advances given {roles['loans_given']:,.0f} but interest income appears absent.",
                "Interest-bearing loans should generate income unless terms indicate otherwise.",
                "TB cannot show loan terms.",
                ["interest-free loans", "related-party arrangements", "moratorium",
                 "staff/subsidised advances"],
                ["completeness", "rights", "valuation"],
                ["loan agreements", "interest computation", "board approvals",
                 "recovery status", "related-party register"])
        else:
            add("Loans/advances vs Interest income", "none",
                f"Loans/advances given {roles['loans_given']:,.0f} with interest income of "
                f"{roles.get('interest_income', 0.0):,.0f} also present.",
                "Interest-bearing loans should generate income unless terms indicate otherwise.",
                "No missing-counterpart exception noted; income exists against the loan "
                "balance, which is a reasonable sign.",
                [], ["completeness", "rights"], [])

    # Grants / subsidies present
    if _present(roles, "grants", base):
        add("Grants / subsidies vs utilisation", "medium",
            f"Grant/subsidy balances {roles['grants']:,.0f} present.",
            "Grant treatment (income/deferred income/asset) depends on conditions and utilisation.",
            "TB cannot show grant conditions or utilisation.",
            ["unconditional revenue grant", "capital/conditional grant", "timing of utilisation"],
            ["classification", "completeness", "presentation", "regularity"],
            ["sanction order", "grant conditions", "utilisation certificate",
             "unspent-funds schedule", "bank trail"])

    # Deferred tax present
    if _present(roles, "deferred_tax", base):
        add("Deferred tax support", "low",
            f"Deferred tax balances {roles['deferred_tax']:,.0f} present.",
            "Deferred tax should be supported by temporary differences and recoverability.",
            "TB cannot show temporary differences.",
            ["timing differences", "unabsorbed depreciation/losses", "recognition thresholds"],
            ["valuation", "recognition", "presentation"],
            ["deferred-tax working", "temporary-difference schedule",
             "recoverability assessment"])

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: order.get(r["severity"], 3))
    return {"relationships": out, "role_totals": roles, "role_coverage_pct": role_coverage_pct}
