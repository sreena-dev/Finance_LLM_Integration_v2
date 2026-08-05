"""Deterministic trial-balance calculations, exposed as Yukta tools.

Every number reported to the user is computed HERE in Python - the LLM only
decides which tool to call and narrates the returned figures. The pure functions
(``compute_*``) are separated from the Yukta wrappers so they can be unit-tested
without an LLM, and ``build_tb_tools`` closes over a loaded TB + a ``results``
dict so the tools take only simple arguments and record their structured output
for the API/UI.

Sign convention: each account carries ``pN_net = debit - credit``. Assets and
expenses are debit-normal (net > 0); liabilities, equity and income are
credit-normal (net < 0). Category *magnitudes* below flip the sign for the
credit-normal categories so every reported total is a positive amount.
"""

from __future__ import annotations

import re

from yukta_rag.core.financial_math import pct_change as _fm_pct
from yukta_rag.trial_balance.tb_config import (RATIO_RISK_THRESHOLDS,
                                               VARIANCE_PCT_THRESHOLD as _CONFIG_VARIANCE_PCT_THRESHOLD)
from yukta_rag.core.financial_math import ratio as _fm_ratio

from yukta import create_custom_tool

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_CATEGORIES = ("asset", "liability", "equity", "income", "expense")

# normalize a free-text account-type/group label to a canonical category
_TYPE_ALIASES = {
    "asset": "asset", "assets": "asset", "non-current asset": "asset",
    "current asset": "asset", "fixed asset": "asset",
    "liability": "liability", "liabilities": "liability",
    "current liability": "liability", "non-current liability": "liability",
    "equity": "equity", "capital": "equity", "reserves": "equity",
    "reserves and surplus": "equity", "net worth": "equity",
    "income": "income", "revenue": "income", "revenues": "income", "sales": "income",
    "expense": "expense", "expenses": "expense", "expenditure": "expense", "cost": "expense",
}

# keyword rules used when there is no type column (rule-based inference).
# Ordered by SPECIFICITY, not balance-sheet order: liability/equity/income/
# expense are tested BEFORE asset so a name like "Bank Loan" or "Bank Overdraft"
# resolves to liability (on "loan"/"overdraft") rather than asset (on "bank").
# First matching category wins.
_INFER_RULES = [
    ("liability", ("payable", "creditor", "borrowing", "loan", "overdraft", "provision",
                   "accrued", "deferred tax liab", "current liab", "duties", "tax payable",
                   "gst output", "vat output", "debenture")),
    ("equity", ("capital", "reserve", "surplus", "retained earning", "retain earn",
                "share premium", "equity", "net worth", "drawings", "p&l account")),
    ("income", ("sales", "sale", "revenue", "income", "gain", "interest received",
                "dividend received", "commission received", "other income")),
    ("expense", ("expense", "cost", "cogs", "purchase", "salary", "salaries", "wages", "rent",
                 "depreciation", "amortis", "amortiz", "interest paid", "tax expense",
                 "electricity", "repairs", "freight", "discount allowed", "bad debt",
                 "insurance", "audit fee", "commission paid")),
    ("asset", ("cash", "bank", "receivable", "debtor", "inventor", "stock",
               "prepaid", "advance", "deposit", "investment", "property", "plant",
               "equipment", "machinery", "building", "land", "goodwill", "intangible",
               "fixed asset", "current asset", "gst input", "vat input",
               "capital work", "capital-work", "cwip", "capital wip",
               "loan given", "loans given", "loan to", "loans to", "loan receivable")),
]

# asset-side lending contains "loan" but is an ASSET, so it must not be swept
# into liabilities by the "loan" keyword above.
_ASSET_LOAN_KWS = ("loan given", "loans given", "loan to", "loans to", "loan receivable")

# capital work-in-progress contains "capital" but is an ASSET (part of PPE), so it
# must not be swept into equity by the "capital" keyword above.
_ASSET_CAPITAL_KWS = ("capital work", "capital-work", "cwip", "capital wip")

# "sale"/"sales" is a strong revenue signal ("Sale of Gas", "Sale of LPG") but the
# same substring also appears in PPE-schedule/disposal entries that are NOT
# revenue: a loss on disposal (expense), a depreciation/disposal adjustment line
# (expense), a gross-block deduction schedule row (asset movement, not P&L), or
# an Ind AS 105 held-for-sale asset (still a balance-sheet asset). For those, the
# "sale" keyword is suppressed so the account falls through to its normal
# expense/asset classification instead of being swept into income.
_SALE_NOT_REVENUE_KWS = ("loss on sale", "held for sale", "deduction during the year",
                        "depreciation", "amortis", "amortiz")

# sub-classification used only for liquidity ratios (current vs non-current).
_CURRENT_ASSET_KWS = ("cash", "bank", "receivable", "debtor", "inventor", "stock",
                      "prepaid", "advance", "deposit", "gst input", "vat input",
                      "marketable", "current asset")
_CURRENT_LIAB_KWS = ("payable", "creditor", "overdraft", "accrued", "tax payable",
                     "gst output", "vat output", "short term", "short-term",
                     "current liab", "provision")


def _normalize_type(t: str | None) -> str | None:
    if not t:
        return None
    low = re.sub(r"\s+", " ", str(t)).strip().lower()
    if low in _TYPE_ALIASES:
        return _TYPE_ALIASES[low]
    for alias, cat in _TYPE_ALIASES.items():
        if alias in low:
            return cat
    return None


_CAPI_ABBREV_RE = re.compile(r"\bcapi\b")
# narrow abbreviation match for "Purchase" truncated to "Pur" (e.g. "Pur of
# Natural Gas", "Pur-LNG-Spot Gas") — real client files abbreviate this
# inconsistently even within the same file. Word-bounded so it can't fire
# inside "purchase" itself (already handled by the full-word keyword below)
# or any other "pur"-containing word.
_PUR_ABBREV_RE = re.compile(r"\bpur\b")


def _infer_type(name: str, code: str | None) -> str | None:
    text = f"{name or ''} {code or ''}".lower()
    asset_loan = any(k in text for k in _ASSET_LOAN_KWS)
    asset_capital = any(k in text for k in _ASSET_CAPITAL_KWS)
    # narrow abbreviation match ("Sh Capi-Eqiss-Cash") — word-bounded so it can't
    # fire inside "capital"/"capitalize" etc, which are already handled by the
    # full "capital" keyword below.
    equity_capi_abbrev = bool(_CAPI_ABBREV_RE.search(text))
    expense_pur_abbrev = bool(_PUR_ABBREV_RE.search(text))
    sale_not_revenue = any(k in text for k in _SALE_NOT_REVENUE_KWS)
    for cat, kws in _INFER_RULES:
        if cat == "liability" and asset_loan:
            continue  # "loan given / receivable" is an asset, not a borrowing
        if cat == "income" and sale_not_revenue:
            kws = tuple(k for k in kws if k not in ("sale", "sales"))
        if cat == "equity":
            if asset_capital:
                continue  # "capital work-in-progress" is an asset, not equity
            if equity_capi_abbrev:
                return cat
        if cat == "expense" and expense_pur_abbrev:
            return cat
        if any(kw in text for kw in kws):
            return cat
    return None


# ---- G/L account-code chart-of-accounts scheme --------------------------------
# Many ERPs (SAP etc.) number GL accounts so the leading digit encodes the class.
# The common Indian PSU convention (verified against balance signs): 1/2 = assets,
# 3 = equity, 4 = liabilities, 5 = income, 6 = expenses. This is only APPLIED when
# it is detected AND validated against the balance signs of the actual data, so a
# company that numbers differently (e.g. 1xxx = income) is never mis-mapped.
_CODE_FIRST_DIGIT = {"1": "asset", "2": "asset", "3": "equity",
                     "4": "liability", "5": "income", "6": "expense"}


def _numeric_suffix(code: str | None) -> str | None:
    """Trailing contiguous digit run of a code, stripping any ERP-style prefix
    (e.g. company code + separator: "GAIL/5410046" -> "5410046",
    "ABC-1234" -> "1234"). Plain numeric codes pass through unchanged."""
    if not code:
        return None
    m = re.search(r"(\d+)$", str(code).strip())
    return m.group(1) if m else None


def _first_digit(code: str | None) -> str | None:
    """Leading significant digit of a code's numeric suffix (strips any
    non-numeric ERP-style prefix and leading zeros)."""
    s = _numeric_suffix(code)
    if not s:
        return None
    s = s.lstrip("0") or "0"
    return s[0]


def _infer_from_code(code: str | None) -> str | None:
    return _CODE_FIRST_DIGIT.get(_first_digit(code) or "")


# expected net sign per leading digit under the scheme (+1 debit-normal, -1 credit-normal)
_CODE_EXPECTED_SIGN = {"1": 1, "2": 1, "3": -1, "4": -1, "5": -1, "6": 1}


def _numeric_codes_present(tb: dict) -> bool:
    """Lighter precondition than ``_coa_scheme_detected()``: true if most
    accounts have a numeric GL-code suffix, so grouping by a shared numeric
    prefix is a meaningful operation — independent of whether the stricter,
    sign-validated 1-6 classification scheme (used for account-TYPE inference)
    also holds. GL-series clearing detection only needs codes to be numeric
    enough to group; its own per-group net-vs-gross ratio is the real
    safety check against false positives, not the full scheme."""
    accts = tb.get("accounts", [])
    if not accts:
        return False
    numeric = [a for a in accts if _numeric_suffix(a.get("code"))]
    return len(numeric) >= max(10, 0.6 * len(accts))


def _coa_scheme_detected(tb: dict) -> bool:
    """True if GL codes follow the 1/2-asset .. 6-expense scheme, sign-validated.

    Requires most accounts to have numeric codes, and validates EACH leading
    digit independently against its expected balance sign (asset 1/2 -> net > 0,
    equity/liability/income 3/4/5 -> net < 0, expense 6 -> net > 0). Every digit
    present with enough balances must agree, so a company that numbers accounts
    differently (e.g. 1xxx = income) is rejected rather than mis-mapped.
    """
    accts = tb.get("accounts", [])
    if not accts:
        return False
    numeric = [a for a in accts if _first_digit(a.get("code"))]
    if len(numeric) < max(10, 0.6 * len(accts)):
        return False
    checked = 0
    for digit, sign in _CODE_EXPECTED_SIGN.items():
        side = [a for a in numeric if _first_digit(a["code"]) == digit]
        if len(side) < 3:
            continue  # too few to judge this class
        # Test the AGGREGATE net of the class, not each account: contra accounts
        # (e.g. accumulated depreciation under the asset prefix) flip individual
        # signs, but a genuine asset class still nets to a debit overall.
        total = sum(_net(a, 1) for a in side)
        if abs(total) < 1:
            continue  # class carries no material balance -> no signal
        if (total > 0) != (sign > 0):
            return False  # class nets to the wrong side -> not this scheme
        checked += 1
    return checked >= 3  # need a few validated classes to trust the scheme


def classify_tb(tb: dict) -> dict:
    """Assign each account a category (asset/liability/equity/income/expense).

    Precedence: an account-type column if present; else the GL code scheme (when
    detected/validated) as primary with name-keyword fallback; else name-keyword
    inference (with GL code as a fallback only when the scheme was detected).
    Returns per-account rows tagged with the classification source, plus the list
    of accounts that could not be classified.
    """
    has_type_col = tb.get("parse_report", {}).get("has_type_column", False)
    code_scheme = _coa_scheme_detected(tb) if not has_type_col else False
    method = ("type_column" if has_type_col else
              "gl_code_scheme" if code_scheme else "keyword_inference")

    rows, unclassified = [], []
    for a in tb["accounts"]:
        cat, source = None, "unclassified"
        if has_type_col:
            cat = _normalize_type(a.get("type"))
            if cat:
                source = "column"
        if cat is None and code_scheme:                 # GL scheme is primary here
            cat = _infer_from_code(a.get("code"))
            if cat:
                source = "gl_code"
        if cat is None:                                 # name-keyword fallback
            cat = _infer_type(a.get("name", ""), a.get("code"))
            if cat:
                source = "inferred"
        row = {"name": a["name"], "code": a.get("code"), "category": cat, "source": source}
        rows.append(row)
        if cat is None:
            unclassified.append(a["name"])
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in _CATEGORIES}
    return {
        "method": method,
        "counts": counts,
        "unclassified": unclassified,
        "accounts": rows,
    }


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------


def _periods(tb: dict) -> list[tuple[int, str]]:
    """List of (1-based index, label) for each period in the TB."""
    return [(i + 1, lbl) for i, lbl in enumerate(tb["periods"])]


def _net(a: dict, p: int) -> float:
    return float(a.get(f"p{p}_net", 0.0) or 0.0)


def _debit(a: dict, p: int) -> float:
    return float(a.get(f"p{p}_debit", 0.0) or 0.0)


def _credit(a: dict, p: int) -> float:
    return float(a.get(f"p{p}_credit", 0.0) or 0.0)


# ---------------------------------------------------------------------------
# 1. Tie-out validation
# ---------------------------------------------------------------------------


def compute_tie_out(tb: dict) -> dict:
    """Per period: total debits vs credits and the imbalance; data-quality flags."""
    per_period = []
    for p, label in _periods(tb):
        td = round(sum(_debit(a, p) for a in tb["accounts"]), 2)
        tc = round(sum(_credit(a, p) for a in tb["accounts"]), 2)
        diff = round(td - tc, 2)
        # tolerate immaterial rounding: absolute 1 paisa, or 1e-6 of the totals
        # (a 0.29 difference on a ~30bn TB is source rounding, not a real break).
        tol = max(0.01, 1e-6 * max(td, tc))
        per_period.append({
            "period": label,
            "total_debit": td,
            "total_credit": tc,
            "difference": diff,
            "balanced": abs(diff) <= tol,
        })

    names = [a["name"].strip().lower() for a in tb["accounts"] if a.get("name")]
    seen, dups = set(), []
    for n in names:
        if n in seen and n not in dups:
            dups.append(n)
        seen.add(n)
    blanks = sum(1 for a in tb["accounts"] if not (a.get("name") or "").strip())
    return {
        "per_period": per_period,
        "duplicate_accounts": dups,
        "blank_name_rows": blanks,
        "n_accounts": len(tb["accounts"]),
    }


# ---------------------------------------------------------------------------
# 2. Financial statements
# ---------------------------------------------------------------------------

# magnitude sign per category (debit-normal categories keep net, credit-normal flip)
_SIGN = {"asset": 1, "expense": 1, "liability": -1, "equity": -1, "income": -1}


def compute_statements(tb: dict, classification: dict | None = None) -> dict:
    """Balance Sheet and P&L subtotals per period, derived from the classification."""
    classification = classification or classify_tb(tb)
    cat_by_name = {r["name"]: r["category"] for r in classification["accounts"]}

    per_period = []
    for p, label in _periods(tb):
        cat_totals = {c: 0.0 for c in _CATEGORIES}
        for a in tb["accounts"]:
            cat = cat_by_name.get(a["name"])
            if cat:
                cat_totals[cat] += _net(a, p)
        mag = {c: round(_SIGN[c] * cat_totals[c], 2) for c in _CATEGORIES}
        profit = round(mag["income"] - mag["expense"], 2)
        # accounting identity check: Assets == Liabilities + Equity + retained profit
        equity_plus_profit = round(mag["liability"] + mag["equity"] + profit, 2)
        per_period.append({
            "period": label,
            "balance_sheet": {
                "assets": mag["asset"],
                "liabilities": mag["liability"],
                "equity": mag["equity"],
            },
            "profit_and_loss": {
                "income": mag["income"],
                "expenses": mag["expense"],
                "net_profit": profit,
            },
            "identity_check": {
                "assets": mag["asset"],
                "liabilities_plus_equity_plus_profit": equity_plus_profit,
                "difference": round(mag["asset"] - equity_plus_profit, 2),
                "balanced": abs(mag["asset"] - equity_plus_profit) < 1.0,
            },
        })
    return {"per_period": per_period}


# ---------------------------------------------------------------------------
# 3. Ratios
# ---------------------------------------------------------------------------


def _current_split(tb: dict, classification: dict, p: int) -> dict:
    """Approximate current assets/liabilities & inventory by keyword (for liquidity)."""
    cat_by_name = {r["name"]: r["category"] for r in classification["accounts"]}
    ca = cl = inv = 0.0
    for a in tb["accounts"]:
        cat = cat_by_name.get(a["name"])
        low = a["name"].lower()
        if cat == "asset" and any(k in low for k in _CURRENT_ASSET_KWS):
            ca += _net(a, p)
            if "inventor" in low or "stock" in low:
                inv += _net(a, p)
        elif cat == "liability" and any(k in low for k in _CURRENT_LIAB_KWS):
            cl += -_net(a, p)
    return {"current_assets": round(ca, 2), "current_liabilities": round(cl, 2),
            "inventory": round(inv, 2)}


def _ratio(num: float, den: float) -> float | None:
    return _fm_ratio(num, den, 4)  # shared financial_math (round 4dp, None on zero den)


def compute_ratios(tb: dict, classification: dict | None = None,
                   statements: dict | None = None) -> dict:
    """Liquidity / solvency / profitability ratios per period, from the subtotals.

    Only ratios the TB granularity supports are computed; any that need
    sub-ledger detail return null with a note rather than a guessed value.
    """
    classification = classification or classify_tb(tb)
    statements = statements or compute_statements(tb, classification)
    notes = []
    per_period = []
    for idx, (p, label) in enumerate(_periods(tb)):
        st = statements["per_period"][idx]
        bs, pl = st["balance_sheet"], st["profit_and_loss"]
        cur = _current_split(tb, classification, p)
        equity_base = bs["equity"] + pl["net_profit"]  # closing equity incl. period profit
        # A keyword current-item split can net to zero/negative on cryptic account
        # names; a negative "ratio" is meaningless, so report it as not computable
        # rather than emit a misleading number (and a false liquidity alarm).
        ca, cl = cur["current_assets"], cur["current_liabilities"]
        reliable = ca > 0 and cl > 0
        current_ratio = _ratio(ca, cl) if reliable else None
        quick_ratio = _ratio(ca - cur["inventory"], cl) if reliable else None
        if not reliable:
            cur = {**cur, "current_split_reliable": False}
        per_period.append({
            "period": label,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "debt_to_equity": _ratio(bs["liabilities"], equity_base),
            "net_profit_margin": _ratio(pl["net_profit"], pl["income"]),
            "return_on_equity": _ratio(pl["net_profit"], equity_base),
            "inputs": {**cur, "liabilities": bs["liabilities"],
                       "equity_incl_profit": round(equity_base, 2),
                       "income": pl["income"], "net_profit": pl["net_profit"]},
        })
    notes.append("Current/quick ratios use a keyword split of current items and are approximate "
                 "(null when the split can't be reliably identified); operating margin is not "
                 "computed because a raw TB does not separate operating vs non-operating expenses.")
    return {"per_period": per_period, "notes": notes}


# ---------------------------------------------------------------------------
# 4. Variance (comparative TBs only)
# ---------------------------------------------------------------------------

VARIANCE_PCT_THRESHOLD = _CONFIG_VARIANCE_PCT_THRESHOLD  # flag swings beyond +/- this %


def compute_variance(tb: dict, classification: dict | None = None,
                     pct_threshold: float = VARIANCE_PCT_THRESHOLD) -> dict:
    """Period-over-period change per account and per category (needs 2 periods)."""
    if len(tb["periods"]) < 2:
        return {"available": False,
                "reason": "single-period trial balance - variance needs two periods"}
    classification = classification or classify_tb(tb)
    cat_by_name = {r["name"]: r["category"] for r in classification["accounts"]}
    p_cur, p_prior = 1, 2  # period 1 is current, period 2 is prior (see parser labels)
    cur_label, prior_label = tb["periods"][0], tb["periods"][1]

    def _pct(cur, prior):
        return _fm_pct(cur, prior, 2)  # shared financial_math

    lines, flagged = [], []
    cat_curr = {c: 0.0 for c in _CATEGORIES}
    cat_prior = {c: 0.0 for c in _CATEGORIES}
    for a in tb["accounts"]:
        cur, prior = _net(a, p_cur), _net(a, p_prior)
        change = round(cur - prior, 2)
        pct = _pct(cur, prior)
        row = {"name": a["name"], "category": cat_by_name.get(a["name"]),
               "current": round(cur, 2), "prior": round(prior, 2),
               "change": change, "pct_change": pct}
        lines.append(row)
        if pct is not None and abs(pct) >= pct_threshold and abs(change) > 0:
            flagged.append(row)
        cat = cat_by_name.get(a["name"])
        if cat:
            cat_curr[cat] += _SIGN[cat] * cur
            cat_prior[cat] += _SIGN[cat] * prior
    by_category = [{
        "category": c, "current": round(cat_curr[c], 2), "prior": round(cat_prior[c], 2),
        "change": round(cat_curr[c] - cat_prior[c], 2), "pct_change": _pct(cat_curr[c], cat_prior[c]),
    } for c in _CATEGORIES]

    flagged.sort(key=lambda r: abs(r["change"]), reverse=True)
    return {
        "available": True,
        "current_period": cur_label,
        "prior_period": prior_label,
        "pct_threshold": pct_threshold,
        "by_category": by_category,
        "flagged_swings": flagged[:25],
        "n_flagged": len(flagged),
    }


# ---------------------------------------------------------------------------
# 5. Risk assessment
# ---------------------------------------------------------------------------

# liquidity/solvency comfort thresholds used to raise ratio-based flags
_RISK_THRESHOLDS = RATIO_RISK_THRESHOLDS


def compute_risk(tb: dict, classification: dict | None = None) -> dict:
    """Combine tie-out, balances, ratios and variance into a ranked risk register."""
    classification = classification or classify_tb(tb)
    statements = compute_statements(tb, classification)
    ratios = compute_ratios(tb, classification, statements)
    tie = compute_tie_out(tb)
    variance = compute_variance(tb, classification)

    risks = []

    def add(severity, category, detail):
        risks.append({"severity": severity, "category": category, "detail": detail})

    # tie-out failures (data integrity)
    for pp in tie["per_period"]:
        if not pp["balanced"]:
            add("high", "tie_out",
                f"{pp['period']}: debits {pp['total_debit']:,} != credits {pp['total_credit']:,} "
                f"(off by {pp['difference']:,}) - the trial balance does not tie out.")
    if tie["duplicate_accounts"]:
        add("medium", "data_quality",
            f"duplicate account names: {', '.join(tie['duplicate_accounts'][:10])}")
    if classification["unclassified"]:
        add("low", "classification",
            f"{len(classification['unclassified'])} account(s) could not be classified: "
            f"{', '.join(classification['unclassified'][:10])}")

    # accounting-identity failures
    for st in statements["per_period"]:
        ic = st["identity_check"]
        if not ic["balanced"]:
            add("medium", "statement_integrity",
                f"{st['period']}: Assets {ic['assets']:,} != Liabilities+Equity+Profit "
                f"{ic['liabilities_plus_equity_plus_profit']:,} (off by {ic['difference']:,}).")

    # ratio-based liquidity/solvency risk
    for rp in ratios["per_period"]:
        cr, qr, de = rp["current_ratio"], rp["quick_ratio"], rp["debt_to_equity"]
        if cr is not None and cr < _RISK_THRESHOLDS["current_ratio_min"]:
            add("high", "liquidity",
                f"{rp['period']}: current ratio {cr} < {_RISK_THRESHOLDS['current_ratio_min']} - "
                "current liabilities exceed current assets.")
        if qr is not None and qr < _RISK_THRESHOLDS["quick_ratio_min"]:
            add("medium", "liquidity", f"{rp['period']}: quick ratio {qr} below 1.0.")
        if de is not None and de > _RISK_THRESHOLDS["debt_to_equity_max"]:
            add("high", "solvency",
                f"{rp['period']}: debt-to-equity {de} > {_RISK_THRESHOLDS['debt_to_equity_max']} - "
                "highly leveraged.")
        if rp["net_profit_margin"] is not None and rp["net_profit_margin"] < 0:
            add("high", "profitability", f"{rp['period']}: net loss (negative profit margin).")

    # large period-over-period swings
    if variance.get("available"):
        for sw in variance["flagged_swings"][:10]:
            add("medium", "variance",
                f"{sw['name']}: {sw['pct_change']}% change ({sw['prior']:,} -> {sw['current']:,}).")

    order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: order.get(r["severity"], 3))
    summary = {sev: sum(1 for r in risks if r["severity"] == sev)
               for sev in ("high", "medium", "low")}
    return {"summary": summary, "risks": risks}


# ---------------------------------------------------------------------------
# Formatting (compact text the analyst LLM narrates from)
# ---------------------------------------------------------------------------


def _fmt_tie_out(d: dict) -> str:
    lines = ["Tie-out validation:"]
    for pp in d["per_period"]:
        status = "BALANCED" if pp["balanced"] else f"IMBALANCED (off {pp['difference']:,})"
        lines.append(f"- {pp['period']}: debit {pp['total_debit']:,} vs credit {pp['total_credit']:,} - {status}")
    if d["duplicate_accounts"]:
        lines.append(f"- duplicate account names: {', '.join(d['duplicate_accounts'][:10])}")
    lines.append(f"- {d['n_accounts']} accounts, {d['blank_name_rows']} blank-name rows")
    return "\n".join(lines)


def _fmt_classification(d: dict) -> str:
    counts = ", ".join(f"{k}={v}" for k, v in d["counts"].items())
    out = [f"Classification ({d['method']}): {counts}"]
    if d["unclassified"]:
        out.append(f"- unclassified ({len(d['unclassified'])}): {', '.join(d['unclassified'][:15])}")
    return "\n".join(out)


def _fmt_statements(d: dict) -> str:
    out = ["Financial statements (per period):"]
    for st in d["per_period"]:
        bs, pl = st["balance_sheet"], st["profit_and_loss"]
        out.append(
            f"- {st['period']}: Assets {bs['assets']:,} | Liabilities {bs['liabilities']:,} | "
            f"Equity {bs['equity']:,} | Income {pl['income']:,} | Expenses {pl['expenses']:,} | "
            f"Net profit {pl['net_profit']:,}"
            + ("" if st['identity_check']['balanced']
               else f"  [identity off by {st['identity_check']['difference']:,}]")
        )
    return "\n".join(out)


def _fmt_ratios(d: dict) -> str:
    out = ["Ratios (per period):"]
    for rp in d["per_period"]:
        out.append(
            f"- {rp['period']}: current {rp['current_ratio']}, quick {rp['quick_ratio']}, "
            f"debt/equity {rp['debt_to_equity']}, net margin {rp['net_profit_margin']}, "
            f"ROE {rp['return_on_equity']}"
        )
    out.extend(f"note: {n}" for n in d["notes"])
    return "\n".join(out)


def _fmt_variance(d: dict) -> str:
    if not d.get("available"):
        return f"Variance: not available - {d.get('reason')}"
    out = [f"Variance {d['prior_period']} -> {d['current_period']} (flag >= {d['pct_threshold']}%):",
           "By category:"]
    for c in d["by_category"]:
        out.append(f"- {c['category']}: {c['prior']:,} -> {c['current']:,} "
                   f"(delta  {c['change']:,}, {c['pct_change']}%)")
    out.append(f"Flagged swings ({d['n_flagged']}):")
    for sw in d["flagged_swings"][:15]:
        out.append(f"- {sw['name']}: {sw['prior']:,} -> {sw['current']:,} ({sw['pct_change']}%)")
    return "\n".join(out)


def _fmt_risk(d: dict) -> str:
    s = d["summary"]
    out = [f"Risk register - high={s['high']}, medium={s['medium']}, low={s['low']}:"]
    for r in d["risks"]:
        out.append(f"- [{r['severity'].upper()}] ({r['category']}) {r['detail']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Yukta tool wrappers
# ---------------------------------------------------------------------------


def build_tb_tools(tb: dict, results: dict) -> list:
    """Build the trial-balance tools, closed over ``tb``.

    Each tool computes deterministically, stashes its structured output in
    ``results`` (so the pipeline can return it verbatim to the UI), and returns a
    compact text block for the analyst LLM to narrate. ``classify_accounts`` runs
    once and is cached in ``results`` so the other tools reuse it.
    """

    def _classification() -> dict:
        if "classification" not in results:
            results["classification"] = classify_tb(tb)
        return results["classification"]

    def validate_tie_out() -> str:
        results["tie_out"] = compute_tie_out(tb)
        return _fmt_tie_out(results["tie_out"])

    def classify_accounts() -> str:
        return _fmt_classification(_classification())

    def build_statements() -> str:
        results["statements"] = compute_statements(tb, _classification())
        return _fmt_statements(results["statements"])

    def compute_ratios_tool() -> str:
        st = results.get("statements") or compute_statements(tb, _classification())
        results["ratios"] = compute_ratios(tb, _classification(), st)
        return _fmt_ratios(results["ratios"])

    def compute_variance_tool() -> str:
        results["variance"] = compute_variance(tb, _classification())
        return _fmt_variance(results["variance"])

    def assess_risk() -> str:
        results["risk"] = compute_risk(tb, _classification())
        return _fmt_risk(results["risk"])

    specs = [
        (validate_tie_out, "validate_tie_out",
         "Validate the trial balance ties out: total debits vs credits per period, "
         "the imbalance amount, and duplicate/blank account rows."),
        (classify_accounts, "classify_accounts",
         "Classify every account into asset/liability/equity/income/expense (using the "
         "sheet's type column when present, else keyword inference) and list any that "
         "could not be classified."),
        (build_statements, "build_statements",
         "Aggregate the classified balances into Balance Sheet (assets/liabilities/equity) "
         "and P&L (income/expenses/net profit) subtotals per period, with an accounting-"
         "identity check."),
        (compute_ratios_tool, "compute_ratios",
         "Compute liquidity/solvency/profitability ratios per period (current, quick, "
         "debt-to-equity, net margin, ROE) from the statement subtotals."),
        (compute_variance_tool, "compute_variance",
         "Period-over-period variance (absolute and %) per account and per category, "
         "flagging large swings. Only meaningful for a comparative (two-period) TB."),
        (assess_risk, "assess_risk",
         "Produce a ranked risk register combining tie-out failures, weak ratios, "
         "abnormal balances and large variances."),
    ]
    return [create_custom_tool(name=name, description=desc, parameters=[], function=fn)
            for fn, name, desc in specs]
