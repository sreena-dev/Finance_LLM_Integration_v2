"""Financial snapshot + Ind AS gap-by-standard for Audit mode (TB-only, honest).

Both are derived purely from the trial balance as risk INDICATORS + evidence
requests — no fabricated misstatement amounts, no audit opinion (spec 2.3). Reuses
the mapping (FSLI + sensitive tags), the relationship role totals and the sign
convention already computed by the audit engine.
"""

from __future__ import annotations

from yukta_rag.audit.audit_config import (ROLE_ABSENT_PCT_OF_BASE,
                                          ROLE_BASE_FALLBACK_PCT,
                                          ROLE_PRESENT_PCT_OF_BASE)
from yukta_rag.trial_balance.tb_tools import _SIGN, _net

# ---------------------------------------------------------------------------
# Financial snapshot (per period)
# ---------------------------------------------------------------------------

# label -> (kind, key): 'cat' = account-class magnitude, 'fsli' = FSLI-line magnitude,
# 'pbt' = income - expenses. All returned as positive-normal magnitudes.
_SNAPSHOT_METRICS = [
    ("Total assets", ("cat", "asset")),
    ("Equity (incl. reserves)", ("cat", "equity")),
    ("Profit / (Loss) before tax", ("pbt", None)),
    ("Revenue from operations", ("fsli", "Revenue from Operations")),
    ("Trade receivables", ("fsli", "Trade Receivables")),
    ("Trade payables", ("fsli", "Trade Payables")),
    ("Borrowings (total)", ("fsli", "Borrowings")),
    ("Property, plant & equipment", ("fsli", "Property, Plant and Equipment")),
    ("Capital work-in-progress", ("fsli", "Capital Work-in-Progress")),
    ("Inventories", ("fsli", "Inventories")),
    ("Investments", ("fsli", "Investments")),
    ("Cash & bank", ("fsli", "Cash and Cash Equivalents")),
]


def _metric_members(tb: dict, mapping: dict, kind: str, key: str | None,
                    excluded_names: set[str] | None = None) -> list[tuple[dict, int]]:
    """The accounts contributing to one snapshot metric, each with its sign.

    'cat' and 'fsli' members carry the class's positive-normal sign; for 'pbt'
    income contributes + and expense contributes − (income − expenses).
    ``excluded_names`` (confirmed clearing/mirror-pair members) are skipped so
    a huge internal-transfer pair never distorts a Snapshot total.
    """
    excluded_names = excluded_names or set()
    cat_by = {a["name"]: a["category"] for a in mapping["accounts"]}
    fsli_by = {a["name"]: a["fsli"] for a in mapping["accounts"]}
    members = []
    for a in tb["accounts"]:
        if a["name"] in excluded_names:
            continue
        cat = cat_by.get(a["name"])
        if not cat:
            continue
        if kind == "cat" and cat == key:
            members.append((a, _SIGN[cat]))
        elif kind == "fsli" and fsli_by.get(a["name"]) == key:
            members.append((a, _SIGN[cat]))
        elif kind == "pbt" and cat in ("income", "expense"):
            members.append((a, _SIGN[cat] if cat == "income" else -_SIGN[cat]))
    return members


def financial_snapshot(tb: dict, mapping: dict, excluded_names: set[str] | None = None) -> dict:
    """Key-metrics table per period (current + prior when the TB is comparative).

    ``excluded_names``: confirmed clearing-series/mirror-pair account names
    (from ``find_clearing_series``/``find_mirrored_pairs``) omitted from every
    metric so they can't distort a total — previously this function had no
    exclusion mechanism at all, so even the pre-existing GL-series clearing
    exclusion never actually reached Snapshot totals.
    """
    periods = tb.get("periods", ["Balance"])
    rows = []
    for label, (kind, key) in _SNAPSHOT_METRICS:
        members = _metric_members(tb, mapping, kind, key, excluded_names)
        vals = [round(sum(sign * _net(a, i + 1) for a, sign in members), 2)
                for i in range(len(periods))]
        if any(abs(x) > 0 for x in vals):   # only show lines that exist in this TB
            rows.append({"metric": label, "values": vals})
    return {"periods": periods, "rows": rows}


def snapshot_contributors(tb: dict, mapping: dict) -> dict:
    """Account-level drill-down of every snapshot metric (workbook OUTPUT).

    ``{metric: {"values": [per-period totals], "accounts": [{code, name, fsli,
    category, contributions: [per-period signed values]}]}}`` — computed with the
    SAME member selection and sign math as ``financial_snapshot``, so each
    metric's contributions sum exactly to the reported figure.
    """
    fsli_by = {a["name"]: a["fsli"] for a in mapping["accounts"]}
    cat_by = {a["name"]: a["category"] for a in mapping["accounts"]}
    periods = tb.get("periods", ["Balance"])
    out = {}
    for label, (kind, key) in _SNAPSHOT_METRICS:
        members = _metric_members(tb, mapping, kind, key)
        accounts = []
        for a, sign in members:
            contribs = [round(sign * _net(a, i + 1), 2) for i in range(len(periods))]
            if any(abs(c) > 0 for c in contribs):
                accounts.append({"code": a.get("code"), "name": a["name"],
                                 "fsli": fsli_by.get(a["name"]),
                                 "category": cat_by.get(a["name"]),
                                 "contributions": contribs})
        vals = [round(sum(acc["contributions"][i] for acc in accounts), 2)
                for i in range(len(periods))]
        if accounts:
            accounts.sort(key=lambda r: abs(r["contributions"][0]), reverse=True)
            out[label] = {"values": vals, "accounts": accounts}
    return out


# ---------------------------------------------------------------------------
# Ind AS gap analysis by standard
# ---------------------------------------------------------------------------

_IND_AS_REQUIREMENTS = {
    16: ("Property, Plant and Equipment",
         "Recognise PPE, capitalise from the date the asset is ready for use, and "
         "depreciate systematically over its useful life."),
    23: ("Borrowing Costs",
         "Capitalise borrowing costs directly attributable to a qualifying asset; "
         "suspend capitalisation during extended interruptions to active development."),
    2: ("Inventories",
        "Measure inventories at the lower of cost and net realisable value."),
    115: ("Revenue from Contracts with Customers",
          "Recognise revenue on transfer of control, with correct measurement and cut-off."),
    109: ("Financial Instruments",
          "Measure financial assets/liabilities (effective interest where applicable) "
          "and recognise expected credit losses on financial assets."),
    116: ("Leases",
          "Recognise a right-of-use asset and a lease liability for lease arrangements."),
    36: ("Impairment of Assets",
         "Assess assets and investments for impairment indicators and test where present."),
    37: ("Provisions, Contingent Liabilities and Contingent Assets",
         "Provide for probable outflows and disclose contingent liabilities."),
    20: ("Accounting for Government Grants",
         "Recognise and present government grants in accordance with their conditions."),
    12: ("Income Taxes",
         "Recognise current and deferred tax, supported by temporary differences and "
         "recoverability."),
    21: ("Effects of Changes in Foreign Exchange Rates",
         "Translate and measure foreign-currency items per the prescribed methodology."),
    8: ("Accounting Policies, Changes in Estimates and Errors",
        "Restate prior-period errors retrospectively; disclose changes in estimates."),
    110: ("Consolidated Financial Statements",
          "Prepare consolidated financial statements where control over an investee exists."),
}


def ind_as_gaps(mapping: dict, relationships: dict) -> dict:
    """Ind AS gaps INDICATED by the trial balance (never concluded)."""
    roles = relationships.get("role_totals", {})
    accts = mapping["accounts"]
    base = max(roles.get("revenue", 0.0), roles.get("ppe", 0.0), roles.get("borrowings", 0.0),
               sum(abs(a["net"]) for a in accts) * ROLE_BASE_FALLBACK_PCT, 1.0)

    def present(k):
        return abs(roles.get(k, 0.0)) >= max(1.0, ROLE_PRESENT_PCT_OF_BASE * base)

    def absent(k):
        return abs(roles.get(k, 0.0)) < max(1.0, ROLE_ABSENT_PCT_OF_BASE * base)

    tags = mapping.get("sensitive_summary", {})
    fsli_groups = mapping.get("fsli_groups", {})
    names = " ".join(a["name"].lower() for a in accts)
    abnormal = any(a.get("abnormal_sign") for a in accts)

    def matched(*kws) -> list[str]:
        """Account names containing any keyword — the literal trigger evidence."""
        return [a["name"] for a in accts
                if any(k in a["name"].lower() for k in kws)][:8]

    out = []

    def add(std, gap, evidence, inputs=None):
        name, req = _IND_AS_REQUIREMENTS[std]
        out.append({"standard": f"Ind AS {std}", "name": name, "requirement": req,
                    "gap": gap, "evidence_requested": evidence, "inputs": inputs or {}})

    if present("ppe"):
        if absent("depreciation"):
            add(16, f"PPE {roles['ppe']:,.0f} present but the depreciation charge is absent/"
                    "immaterial — depreciation and capitalisation completeness are not "
                    "evidenced by the TB.",
                ["fixed asset register", "depreciation working", "capitalisation dates"],
                {"ppe": roles["ppe"], "depreciation": roles.get("depreciation", 0.0)})
        else:
            add(16, f"PPE {roles['ppe']:,.0f} present — additions/deletions and depreciation "
                    "are not reconcilable from the TB.",
                ["fixed asset register", "additions/deletions reconciliation", "depreciation working"],
                {"ppe": roles["ppe"], "depreciation": roles.get("depreciation", 0.0)})
    if present("borrowings"):
        extra = f" and CWIP {roles.get('cwip', 0):,.0f}" if present("cwip") else ""
        add(23, f"Borrowings {roles['borrowings']:,.0f}{extra} present — borrowing-cost "
                "capitalisation/suspension and completeness of interest are not evidenced by the TB.",
            ["loan agreements", "interest & capitalisation working", "CWIP project status"],
            {"borrowings": roles["borrowings"], "cwip": roles.get("cwip", 0.0),
             "finance_cost": roles.get("finance_cost", 0.0)})
    if present("inventory"):
        add(2, f"Inventories {roles['inventory']:,.0f} present — measurement at the lower of "
               "cost and NRV (and obsolescence) is not evidenced by the TB.",
            ["inventory valuation working", "NRV assessment", "ageing"],
            {"inventory": roles["inventory"]})
    if present("revenue"):
        add(115, f"Revenue {roles['revenue']:,.0f} present — recognition and cut-off are not "
                 "verifiable from the TB.",
            ["revenue recognition policy", "cut-off testing", "contracts / GST returns"],
            {"revenue": roles["revenue"], "receivables": roles.get("receivables", 0.0)})
    if present("receivables") or present("loans_given") or present("borrowings"):
        add(109, "Financial assets/liabilities (receivables / loans / borrowings) present — "
                 "expected credit loss and effective-interest measurement are not evidenced by the TB.",
            ["party-wise ageing", "ECL working", "EIR computation"],
            {"receivables": roles.get("receivables", 0.0),
             "loans_given": roles.get("loans_given", 0.0),
             "borrowings": roles.get("borrowings", 0.0)})
    if any(k in names for k in ("lease", "right of use", "rou", "leasehold")):
        add(116, "Lease / right-of-use related balances present — recognition of a "
                 "right-of-use asset and lease liability is not evidenced by the TB.",
            ["lease agreements", "ROU / lease-liability schedule"],
            {"matched_accounts": matched("lease", "right of use", "rou", "leasehold")})
    if present("investments") or present("ppe") or present("cwip"):
        add(36, "Investments / PPE / CWIP present — assessment of impairment indicators and "
                "testing is not evidenced by the TB.",
            ["impairment assessment", "recoverable-amount working"],
            {"investments": roles.get("investments", 0.0), "ppe": roles.get("ppe", 0.0),
             "cwip": roles.get("cwip", 0.0)})
    if "Provisions" in fsli_groups or tags.get("propriety") or \
            any(k in names for k in ("provision", "contingent", "write-off", "waiver")):
        add(37, "Provision / write-off / loss heads present — completeness of provisions and "
                "disclosure of contingent liabilities are not evidenced by the TB.",
            ["provision computations", "legal/contingency schedule", "board minutes"],
            {"provision_accounts": len(fsli_groups.get("Provisions", [])),
             "propriety_tagged": tags.get("propriety", 0),
             "matched_accounts": matched("provision", "contingent", "write-off", "waiver")})
    if tags.get("grant_subsidy") or roles.get("grants"):
        add(20, "Grant / subsidy balances present — classification (income / deferred income "
                "/ asset) against conditions is not evidenced by the TB.",
            ["sanction & conditions", "utilisation certificate", "bank trail"],
            {"grant_subsidy_tagged": tags.get("grant_subsidy", 0),
             "grants": roles.get("grants", 0.0),
             "matched_accounts": matched("grant", "subsidy")})
    if roles.get("deferred_tax") or "deferred tax" in names:
        add(12, "Deferred tax balances present — support by temporary differences and "
                "recoverability is not evidenced by the TB.",
            ["deferred-tax working", "temporary-difference schedule"],
            {"deferred_tax": roles.get("deferred_tax", 0.0),
             "matched_accounts": matched("deferred tax")})
    if tags.get("foreign_currency") or any(
            k in names for k in ("foreign exchange", "forex", "exchange gain", "exchange loss",
                                 "fcnr", "ecb", "fctl")):
        add(21, "Foreign-currency balances present — measurement / restatement per the "
                "standard is not evidenced by the TB.",
            ["year-end rates", "restatement working", "FX policy"],
            {"foreign_currency_tagged": tags.get("foreign_currency", 0),
             "matched_accounts": matched("foreign exchange", "forex", "exchange gain",
                                          "exchange loss", "fcnr", "ecb", "fctl")})
    if any(k in names for k in ("subsidiary", "associate", "joint venture", "investment in ")):
        add(110, "Investments in group entities present — whether consolidated financial "
                 "statements are required (control / significant influence) is not evidenced by the TB.",
            ["shareholding / control assessment", "group structure", "CFS / elimination working"],
            {"matched_accounts": matched("subsidiary", "associate", "joint venture",
                                          "investment in ")})
    if tags.get("suspense_control") or abnormal or \
            any(k in names for k in ("prior period", "legacy", "migration", "adjustment")):
        add(8, "Suspense/control, legacy-adjustment or abnormal-sign balances present — "
               "prior-period restatements and estimate-change disclosures are not evidenced by the TB.",
            ["reconciliation & clearance plan", "restatement working", "estimate-change disclosure"],
            {"suspense_control_tagged": tags.get("suspense_control", 0),
             "abnormal_signs_present": abnormal,
             "matched_accounts": matched("suspense", "clearing", "prior period",
                                          "legacy", "migration", "adjustment")})

    return {"gaps": out}
