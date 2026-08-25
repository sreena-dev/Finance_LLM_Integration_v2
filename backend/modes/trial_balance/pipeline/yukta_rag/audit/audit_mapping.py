"""Account mapping for Audit mode (spec section 5, Appendix C).

Layers three things on top of the 5-class classification in ``tb_tools.classify_tb``:
  * Schedule III FSLI mapping (financial-statement line item),
  * per-account mapping confidence (high / medium / low / unmapped),
  * sensitive tags (grant, statutory dues, MSME, CSR, related party, suspense,
    write-off, deposit, advance) and normal-balance / abnormal-sign flags.

Everything is deterministic keyword logic — no LLM, no invented facts. Where a
name is generic the account stays low-confidence / unmapped and is surfaced for a
chart-of-accounts request rather than force-mapped (spec 1.2, 5.2).
"""

from __future__ import annotations

import re

from yukta_rag.audit.audit_grouping import code_variants, norm_key
from yukta_rag.trial_balance.tb_tools import _net, _PUR_ABBREV_RE, classify_tb

# ---------------------------------------------------------------------------
# Schedule III FSLI rules, evaluated WITHIN a mapped account class. Each entry is
# (fsli_label, keyword tuple). First match wins; order = most specific first.
# ---------------------------------------------------------------------------
_FSLI_RULES = {
    "asset": [
        ("Property, Plant and Equipment", ("property", "plant", "machinery", "building",
         "vehicle", "furniture", "fixture", "equipment", "land", "fixed asset", "ppe",
         "office equip", "computer", "p&m", "leasehold improvement", "rou", "right of use")),
        ("Capital Work-in-Progress", ("cwip", "capital work", "work in progress", "wip capital")),
        ("Intangible Assets", ("intangible", "goodwill", "software", "licence", "license",
         "patent", "trademark", "brand")),
        ("Investments", ("investment", "invest", "mutual fund", "equity shares of", "bonds held",
         "debentures held")),
        ("Inventories", ("inventor", "stock", "raw material", "finished goods", "work-in-progress",
         "consumable", "stores", "spares", "goods in transit")),
        ("Trade Receivables", ("receivable", "debtor", "bills receivable", "sundry debtor",
         "trade debtor")),
        ("Cash and Cash Equivalents", ("cash", "bank", "petty cash", "imprest", "cheques on hand")),
        ("Loans (financial asset)", ("loan given", "loans given", "loan to", "loans to",
         "loan receivable", "inter corporate deposit", "icd")),
        ("Other Assets (advances/deposits/tax)", ("advance", "prepaid", "deposit", "gst input",
         "vat input", "cenvat", "tax recoverable", "tds recoverable", "input tax", "mobilisation")),
    ],
    "liability": [
        ("Borrowings", ("borrowing", "term loan", "cash credit", "overdraft", "working capital loan",
         "debenture", "bond", "ncd", "perpetual debt", "loan from", "bank loan", "secured loan",
         "unsecured loan", "commercial paper", "lease liability")),
        ("Trade Payables", ("payable", "creditor", "sundry creditor", "vendor", "gr/ir", "gr ir")),
        ("Provisions", ("provision", "ecl", "expected credit loss", "gratuity provision",
         "leave encashment", "warranty")),
        ("Deferred Tax Liability", ("deferred tax",)),
        ("Other Financial Liabilities (deposits/retention)", ("deposit received", "security deposit",
         "emd", "earnest", "retention", "advance from", "advance received")),
        ("Statutory Dues / Other Current Liabilities", ("gst output", "gst payable", "tds payable",
         "tcs", "pf", "esi", "professional tax", "duty payable", "cess", "statutory")),
    ],
    "equity": [
        ("Equity Share Capital", ("share capital", "equity capital", "paid up capital", "capital account")),
        ("Other Equity / Reserves", ("reserve", "surplus", "retained earning", "retain earn",
         "securities premium", "general reserve", "capital reserve", "revaluation", "oci",
         "p&l account")),
    ],
    "income": [
        ("Other Income", ("other income", "interest received", "dividend", "misc income",
         "rent income", "profit on sale", "gain on", "grant", "subsidy", "scrap")),
        ("Revenue from Operations", ("revenue from operation", "sales", "sale of", "sale ",
         "revenue", "turnover", "income from operation", "operating income", "service income",
         "interest income on loan", "fee income", "tariff", "toll")),
    ],
    "expense": [
        ("Cost of Materials Consumed / Purchases", ("material consumed", "cost of material",
         "purchase", "cost of goods", "consumption")),
        ("Finance Costs", ("finance cost", "interest paid", "interest expense", "interest on",
         "bank charges", "processing fee")),
        ("Employee Benefits Expense", ("salary", "salaries", "wages", "bonus", "gratuity",
         "employee", "staff", "pf contribution", "esi contribution", "leave encashment")),
        ("Depreciation and Amortisation", ("depreciation", "amortis", "amortiz", "impairment")),
        ("Other Expenses", ("rent", "repair", "insurance", "electricity", "travel", "audit fee",
         "legal", "professional", "csr", "write-off", "write off", "loss", "misc", "freight",
         "commission", "power", "fuel")),
    ],
}

_CATEGORY_DEFAULT_FSLI = {
    "asset": "Other Assets", "liability": "Other Liabilities", "equity": "Other Equity / Reserves",
    "income": "Other Income", "expense": "Other Expenses",
}

# ---------------------------------------------------------------------------
# Sensitive tags (spec 5.4) — keyword -> tag. Elevated by nature/context.
# ---------------------------------------------------------------------------
_SENSITIVE_RULES = [
    ("grant_subsidy", ("grant", "subsidy", "grant-in-aid", "grant in aid", "budgetary support",
     "viability gap")),
    ("statutory_dues", ("gst", "tds", "tcs", "pf ", "provident fund", "esi", "professional tax",
     "income tax", "duty", "cess", "customs", "excise")),
    ("msme", ("msme", "micro enterprise", "small enterprise", "medium enterprise")),
    ("csr", ("csr", "corporate social responsibility")),
    ("related_party", ("holding company", "subsidiary", "associate", "joint venture", " jv ",
     "director", "kmp", "promoter", "group company", "inter-company", "inter company",
     "inter corporate", "icd", "due from", "due to", "related party")),
    ("suspense_control", ("suspense", "clearing", "control account", "unclassified",
     "difference", "mismatch", "temporary")),
    ("propriety", ("write-off", "write off", "written off", "waiver", "ex-gratia", "ex gratia",
     "shortage", "idle fund", "advance to staff", "advance to officer", "advance to employee",
     "loss on")),
    ("deposit", ("deposit", "emd", "earnest money", "retention money", "security deposit")),
    ("advance", ("advance", "mobilisation advance", "mobilization advance")),
    ("foreign_currency", ("forex", "foreign currency", "fcnr", "ecb", "fctl", "exchange")),
]

_CATEGORY_NORMAL = {"asset": "debit", "expense": "debit",
                    "liability": "credit", "equity": "credit", "income": "credit"}

# contra-asset accounts legitimately carry a credit balance -> not an abnormal sign
_CONTRA_ASSET_KWS = ("depreciation", "dep res", "dep.res", "amortis", "amortiz", "provision",
                     "impairment", "allowance", "accumulated", "ecl", "doubtful", "obsolescence")


def map_fsli(name: str, code: str | None, category: str | None) -> tuple[str | None, str]:
    """Return (fsli, confidence) for one account. Confidence: high/medium/low/unmapped."""
    if not category:
        return None, "unmapped"
    text = f"{name or ''} {code or ''}".lower()
    if category == "expense" and _PUR_ABBREV_RE.search(text):
        return "Cost of Materials Consumed / Purchases", "high"
    for fsli, kws in _FSLI_RULES.get(category, []):
        if any(k in text for k in kws):
            return fsli, "high"
    # class known but no specific FSLI keyword -> a generic line, medium confidence
    return _CATEGORY_DEFAULT_FSLI.get(category), "medium"


def sensitive_tags(name: str, code: str | None) -> list[str]:
    """Sensitive tags for an account (spec 5.4)."""
    text = f" {(name or '').lower()} {(code or '').lower()} "
    tags = []
    for tag, kws in _SENSITIVE_RULES:
        if any(k in text for k in kws):
            tags.append(tag)
    return tags


def normal_balance(category: str | None) -> str | None:
    return _CATEGORY_NORMAL.get(category) if category else None


def abnormal_sign(name: str, category: str | None, net: float) -> tuple[bool, str | None]:
    """Detect an abnormal debit/credit side for the account class (spec 5.3).

    Returns (is_abnormal, note). Non-abnormal or unknown returns (False, None).
    """
    if not category or net == 0:
        return False, None
    low = (name or "").lower()
    if category == "asset" and net < 0:
        if any(k in low for k in _CONTRA_ASSET_KWS):
            return False, None   # accumulated depreciation / provision: legitimate contra
        return True, ("Credit balance in an asset ledger; may be an advance/receipt, credit "
                      "note, overdraft or misclassification. Request party-wise ledger and ageing.")
    if category == "liability" and net > 0:
        return True, ("Debit balance in a liability ledger; may be an advance to vendor, "
                      "overpayment, recoverable statutory due or misclassification. Request ledger.")
    if category == "equity" and net > 0:
        if "loss" in low or "deficit" in low or "retained" in low:
            return False, None   # accumulated losses legitimately sit debit
        return True, ("Debit balance in equity/reserves other than accumulated losses; "
                      "request composition and movement of the reserve.")
    if category == "income" and net > 0:
        return True, ("Debit balance in an income ledger; may be returns, reversals, grant "
                      "misclassification or netting. Request breakup.")
    if category == "expense" and net < 0:
        return True, ("Credit balance in an expense ledger; may be recoveries netted off or a "
                      "provision reversal. Request breakup.")
    return False, None


def sign_flip(opening_net: float | None, closing_net: float) -> bool:
    """Spec E10 step 4 — did the account's sign change between opening and
    closing? A higher-priority pattern than a merely-abnormal closing sign,
    since it indicates something changed sides during the year rather than
    having always been on an unusual side."""
    if opening_net is None or opening_net == 0 or closing_net == 0:
        return False
    return (opening_net > 0) != (closing_net > 0)


def _variant_lookup(canonical_override: dict[str, str], code) -> str | None:
    """Look a GL code up under every form it might have been written in.

    Covers the direction ``_assign`` cannot: the trial balance carries
    ``GAIL/1010010`` while the grouping file listed it bare as ``1010010``, so
    the override's keys hold the short form and the account's code is the long
    one. Deterministic across runs — variants are sorted, not set-ordered."""
    for variant in sorted(code_variants(code)):
        hit = canonical_override.get(variant)
        if hit:
            return hit
    return None


def map_accounts(tb: dict, classification: dict | None = None,
                 grouping_override: dict[str, str] | None = None) -> dict:
    """Enrich every account with FSLI, confidence, sensitive tags and abnormal sign.

    ``grouping_override`` (client-format rule C7): an optional
    ``{gl_code_or_name: fsli_label}`` map from a client-supplied chart-of-
    accounts / management FSLI grouping. When an account's code (or, failing
    that, its name) is a key in this map, that FSLI label wins outright over
    the keyword engine — tagged ``mapping_source: "management_grouping"`` so
    the report can state which classification source was used. Lookups fall
    back to ``norm_key`` on both sides, so a client file that writes a code as
    ``1001.0`` or an account name in different case/spacing still applies
    instead of silently reverting to keyword inference.

    Returns ``{accounts: [...], fsli_groups: {...}, mapping_confidence_summary: {...},
    unmapped_accounts: [...], sensitive_summary: {...}}`` for the audit output.
    """
    classification = classification or classify_tb(tb)
    cat_by_name = {r["name"]: r for r in classification["accounts"]}
    grouping_override = grouping_override or {}
    canonical_override = {}
    for k, v in grouping_override.items():
        canonical = norm_key(k)
        if canonical:
            canonical_override[canonical] = v

    accounts, unmapped = [], []
    conf_summary = {"high": 0, "medium": 0, "low": 0, "unmapped": 0}
    fsli_groups: dict[str, list[str]] = {}
    sensitive_summary: dict[str, int] = {}
    n_grouping_override = 0

    for a in tb["accounts"]:
        cinfo = cat_by_name.get(a["name"], {})
        category = cinfo.get("category")
        net = _net(a, 1)
        override_fsli = (grouping_override.get(a.get("code") or "")
                         or grouping_override.get(a["name"])
                         or canonical_override.get(norm_key(a.get("code")))
                         or canonical_override.get(norm_key(a["name"]))
                         or _variant_lookup(canonical_override, a.get("code")))
        if override_fsli:
            fsli, conf, source = override_fsli, "high", "management_grouping"
            n_grouping_override += 1
        else:
            fsli, conf = map_fsli(a["name"], a.get("code"), category)
            # a low-confidence keyword-inferred class is at best 'low', never 'high'
            if conf == "high" and cinfo.get("source") == "inferred":
                conf = "medium"
            source = "keyword_inference"
        tags = sensitive_tags(a["name"], a.get("code"))
        abn, abn_note = abnormal_sign(a["name"], category, net)
        flipped = sign_flip(a.get("opening_net"), net)

        conf_summary[conf if conf in conf_summary else "unmapped"] += 1
        if conf == "unmapped" or category is None:
            unmapped.append(a["name"])
        if fsli:
            fsli_groups.setdefault(fsli, []).append(a["name"])
        for t in tags:
            sensitive_summary[t] = sensitive_summary.get(t, 0) + 1

        accounts.append({
            "name": a["name"], "code": a.get("code"), "source_row": a.get("source_row"),
            "category": category, "fsli": fsli, "mapping_confidence": conf,
            "mapping_source": source,
            "normal_balance": normal_balance(category),
            "net": round(net, 2), "abnormal_sign": abn, "abnormal_note": abn_note,
            "sign_flipped": flipped,
            "sensitive_tags": tags,
            "opening_net": a.get("opening_net"),
        })

    return {
        "accounts": accounts,
        "method": classification.get("method"),
        "grouping_source": "management_grouping" if n_grouping_override else "keyword_inference",
        "n_grouping_override": n_grouping_override,
        "fsli_groups": {k: v for k, v in sorted(fsli_groups.items(),
                                                key=lambda kv: -len(kv[1]))},
        "mapping_confidence_summary": conf_summary,
        "unmapped_accounts": unmapped,
        "sensitive_summary": sensitive_summary,
    }
