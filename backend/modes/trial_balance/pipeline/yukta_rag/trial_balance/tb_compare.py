"""Year-over-year comparison of two trial balances.

Matches accounts across two parsed TBs (by cleaned GL code, falling back to
normalized name), and computes per-account and per-category variance between the
current and prior file. All arithmetic is deterministic (Python); the analyst
LLM only narrates the figures. Each TB is treated at its first period (the
closing balance for the SAP/PSU exports).
"""

from __future__ import annotations

import re

from yukta_rag.trial_balance.tb_config import (COMPARE_SWING_PCT_THRESHOLD,
                                               COMPARE_VALUE_COVERAGE_WARN_THRESHOLD,
                                               EXPENSE_GROWTH_PCT_TRIGGER,
                                               INCOME_DROP_PCT_TRIGGER,
                                               LIABILITY_RISE_PCT_TRIGGER,
                                               MATERIAL_NEW_BORROWING_FLOOR)
from yukta_rag.trial_balance.tb_tools import (
    _CATEGORIES,
    _SIGN,
    _credit,
    _debit,
    _net,
    classify_tb,
    compute_ratios,
    compute_statements,
    compute_tie_out,
)

DEFAULT_PCT_THRESHOLD = COMPARE_SWING_PCT_THRESHOLD  # flag a movement above +/- this %


_YEAR_LABEL_RE = re.compile(r"20\d{2}\s*[-/]\s*\d{2,4}|fy\s*20\d{2}[-/]?\d{0,4}|20\d{2}", re.I)


def _period_label(tb: dict) -> str:
    """A meaningful period label: the parsed period, else a year from the filename."""
    p = (tb.get("periods") or ["Balance"])[0]
    if p and p.strip().lower() not in ("balance", "current", "prior", ""):
        return p
    m = _YEAR_LABEL_RE.search(tb.get("filename") or "")
    return m.group(0).strip() if m else (p or "Balance")


def _match_key(acct: dict) -> str:
    """Key used to line accounts up across files: GL code if present, else name."""
    code = acct.get("code")
    if code and str(code).strip():
        return f"code:{str(code).strip().lower()}"
    return f"name:{(acct.get('name') or '').strip().lower()}"


def merge_trial_balances(tb_cur: dict, tb_prior: dict) -> dict:
    """Merge two single-period TB files into ONE two-period TB structure.

    Accounts are matched across the files with ``_match_key`` (GL code, else
    name); each merged account carries period 1 (``p1_*``) from the CURRENT file
    and period 2 (``p2_*``) from the PRIOR file, zero where an account exists in
    only one file. The result is shaped exactly like a natively two-period parse,
    so the whole deterministic toolkit (statements, ratios, VARIANCE, risk) works
    on it unchanged.
    """
    cur = {_match_key(a): a for a in tb_cur["accounts"]}
    prior = {_match_key(a): a for a in tb_prior["accounts"]}
    accounts = []
    for key in cur.keys() | prior.keys():
        a_cur, a_pri = cur.get(key), prior.get(key)
        base = a_cur or a_pri
        accounts.append({
            "code": base.get("code"), "name": base.get("name"),
            "type": base.get("type"), "currency": base.get("currency"),
            "source_row": base.get("source_row"),
            "p1_debit": round(_debit(a_cur, 1), 2) if a_cur else 0.0,
            "p1_credit": round(_credit(a_cur, 1), 2) if a_cur else 0.0,
            "p1_net": round(_net(a_cur, 1), 2) if a_cur else 0.0,
            "p2_debit": round(_debit(a_pri, 1), 2) if a_pri else 0.0,
            "p2_credit": round(_credit(a_pri, 1), 2) if a_pri else 0.0,
            "p2_net": round(_net(a_pri, 1), 2) if a_pri else 0.0,
        })
    pr_cur = tb_cur.get("parse_report", {})
    return {
        "periods": [_period_label(tb_cur), _period_label(tb_prior)],
        "accounts": accounts,
        "filename": f"{tb_cur.get('filename') or 'current'} + {tb_prior.get('filename') or 'prior'}",
        "parse_report": {
            **pr_cur,
            "n_periods": 2,
            "n_accounts": len(accounts),
            "balance_mode": pr_cur.get("balance_mode", "net"),
            "merged_from_two_files": True,
            "has_opening": False,
            "has_movement": False,
        },
    }


from yukta_rag.core.financial_math import pct_change as _pct  # (cur, prior, ndigits=2)


def _classification_coverage(lines: list[dict]) -> float:
    """Value-weighted classification coverage across both compared periods'
    matched accounts (spec QNT-04) — each line already carries a category (or
    None), summed across current+prior magnitude so a thinly-mapped line still
    counts against coverage even if it nets small in one period."""
    total = classified = 0.0
    for r in lines:
        w = abs(r["current"]) + abs(r["prior"])
        total += w
        if r["category"]:
            classified += w
    return round(classified / total * 100, 1) if total else 100.0


def _category_totals(tb: dict, classification: dict) -> dict:
    """Signed magnitude per category for the TB's first period."""
    cat_by_name = {r["name"]: r["category"] for r in classification["accounts"]}
    totals = {c: 0.0 for c in _CATEGORIES}
    for a in tb["accounts"]:
        cat = cat_by_name.get(a["name"])
        if cat:
            totals[cat] += _net(a, 1)
    return {c: round(_SIGN[c] * totals[c], 2) for c in _CATEGORIES}


def compare_trial_balances(tb_cur: dict, tb_prior: dict,
                           pct_threshold: float = DEFAULT_PCT_THRESHOLD) -> dict:
    """Compare two trial balances (current vs prior). Returns a structured diff."""
    cls_cur = classify_tb(tb_cur)
    cls_prior = classify_tb(tb_prior)
    cat_cur = {r["name"]: r["category"] for r in cls_cur["accounts"]}

    cur = {_match_key(a): a for a in tb_cur["accounts"]}
    prior = {_match_key(a): a for a in tb_prior["accounts"]}

    cat_pri = {r["name"]: r["category"] for r in cls_prior["accounts"]}
    lines, flagged, new_accounts, dropped_accounts, sign_flips = [], [], [], [], []
    for key in cur.keys() | prior.keys():
        a_cur, a_pri = cur.get(key), prior.get(key)
        cur_net = round(_net(a_cur, 1), 2) if a_cur else 0.0
        pri_net = round(_net(a_pri, 1), 2) if a_pri else 0.0
        name = (a_cur or a_pri).get("name")
        code = (a_cur or a_pri).get("code")
        change = round(cur_net - pri_net, 2)
        pct = _pct(cur_net, pri_net)
        status = "both" if a_cur and a_pri else ("new" if a_cur else "dropped")
        sign_flip = bool(a_cur and a_pri and cur_net and pri_net and (cur_net > 0) != (pri_net > 0))
        # flag reason per README: new / dropped / sign flip / above threshold
        reason = None
        if status == "new":
            reason = "new"
        elif status == "dropped":
            reason = "dropped"
        elif sign_flip:
            reason = "sign flip"
        elif pct is not None and abs(pct) >= pct_threshold and abs(change) > 0:
            reason = f">= {pct_threshold:g}% move"
        row = {
            "name": name, "code": code,
            "category": cat_cur.get(name) or cat_pri.get(name),
            # signed net balances (current/prior) plus the split debit/credit each year
            "current": cur_net, "prior": pri_net, "change": change, "pct_change": pct,
            "cy_debit": round(_debit(a_cur, 1), 2) if a_cur else 0.0,
            "cy_credit": round(_credit(a_cur, 1), 2) if a_cur else 0.0,
            "py_debit": round(_debit(a_pri, 1), 2) if a_pri else 0.0,
            "py_credit": round(_credit(a_pri, 1), 2) if a_pri else 0.0,
            "status": status, "sign_flip": sign_flip, "flag_reason": reason,
        }
        lines.append(row)
        if status == "new":
            new_accounts.append(row)
        elif status == "dropped":
            dropped_accounts.append(row)
        elif sign_flip:
            sign_flips.append(row)
        if reason is not None:
            flagged.append(row)

    tot_cur = _category_totals(tb_cur, cls_cur)
    tot_pri = _category_totals(tb_prior, cls_prior)
    by_category = [{
        "category": c, "current": tot_cur[c], "prior": tot_pri[c],
        "change": round(tot_cur[c] - tot_pri[c], 2), "pct_change": _pct(tot_cur[c], tot_pri[c]),
    } for c in _CATEGORIES]

    flagged.sort(key=lambda r: abs(r["change"]), reverse=True)
    new_accounts.sort(key=lambda r: abs(r["current"]), reverse=True)
    dropped_accounts.sort(key=lambda r: abs(r["prior"]), reverse=True)
    sign_flips.sort(key=lambda r: abs(r["change"]), reverse=True)
    # main comparative table: grouped by category, largest movement first
    lines.sort(key=lambda r: (_CATEGORIES.index(r["category"]) if r["category"] in _CATEGORIES else 99,
                              -abs(r["change"])))

    tie_cur = compute_tie_out(tb_cur)["per_period"][0]
    tie_pri = compute_tie_out(tb_prior)["per_period"][0]

    result = {
        "current_label": _period_label(tb_cur),
        "prior_label": _period_label(tb_prior),
        "pct_threshold": pct_threshold,
        "tie_out": {"current": tie_cur, "prior": tie_pri},
        "lines": lines,                      # every matched account (for the workbook)
        "by_category": by_category,
        "flagged_swings": flagged[:25],
        "n_flagged": len(flagged),
        "new_accounts": new_accounts[:15],
        "n_new": len(new_accounts),
        "dropped_accounts": dropped_accounts[:15],
        "n_dropped": len(dropped_accounts),
        "sign_flips": sign_flips[:15],
        "n_sign_flips": len(sign_flips),
        "statements": {
            "current": compute_statements(tb_cur, cls_cur)["per_period"][0],
            "prior": compute_statements(tb_prior, cls_prior)["per_period"][0],
        },
        "ratios": {
            "current": compute_ratios(tb_cur, cls_cur)["per_period"][0],
            "prior": compute_ratios(tb_prior, cls_prior)["per_period"][0],
        },
        "classification": {"current": cls_cur, "prior": cls_prior},
    }
    result["classification_coverage_pct"] = _classification_coverage(lines)
    result["low_classification_coverage"] = (
        result["classification_coverage_pct"] < COMPARE_VALUE_COVERAGE_WARN_THRESHOLD)
    result["risk"] = compute_comparison_risk(result, pct_threshold)
    return result


# ---------------------------------------------------------------------------
# Risk assessment on the year-over-year movement
# ---------------------------------------------------------------------------

# risk topic -> an Ind AS retrieval query (the analyst cites whatever text comes
# back). Keeps the standard mapping in one place instead of hard-coding numbers.
_RISK_INDAS_QUERY = {
    "profitability": "recognition of revenue and income; presentation of profit or loss",
    "solvency": "borrowings and other financial liabilities; financial instruments disclosures",
    "financing": "borrowings and financial liabilities measured at amortised cost",
    "credit_risk": "expected credit loss and impairment of financial assets (loans)",
    "tax": "income taxes and deferred tax assets and liabilities",
    "cost": "presentation of expenses in the statement of profit and loss",
    "liquidity": "presentation of current assets and current liabilities; liquidity risk",
}

_BORROWING_KWS = ("loan", "bond", "ncd", "debt", "borrow", "perpetual", "debenture")


def compute_comparison_risk(cmp: dict, pct_threshold: float = DEFAULT_PCT_THRESHOLD) -> dict:
    """Derive a ranked risk register from the year-over-year comparison.

    Each risk carries an ``indas_query`` used to pull the relevant Ind AS text so
    the analyst can cite it. All figures come from the deterministic comparison.
    """
    risks = []

    def add(severity, category, detail):
        risks.append({"severity": severity, "category": category, "detail": detail,
                      "indas_query": _RISK_INDAS_QUERY.get(category)})

    sc = cmp["statements"]["current"]["profit_and_loss"]
    sp = cmp["statements"]["prior"]["profit_and_loss"]
    np_c, np_p = sc["net_profit"], sp["net_profit"]
    if np_c < np_p:
        turned_loss = np_c < 0
        add("high" if turned_loss else "medium", "profitability",
            f"Net profit fell from {np_p:,.0f} to {np_c:,.0f}"
            + (" (a loss / wider loss)." if turned_loss else "."))

    rc, rp = cmp["ratios"]["current"], cmp["ratios"]["prior"]
    if (rc["net_profit_margin"] is not None and rp["net_profit_margin"] is not None
            and rc["net_profit_margin"] < rp["net_profit_margin"]):
        add("medium", "profitability",
            f"Net margin compressed from {rp['net_profit_margin']} to {rc['net_profit_margin']}.")

    for c in cmp["by_category"]:
        pct = c["pct_change"]
        if (c["category"] == "liability" and c["change"] > 0 and pct is not None
                and pct >= LIABILITY_RISE_PCT_TRIGGER):
            add("high", "solvency",
                f"Liabilities rose from {c['prior']:,.0f} to {c['current']:,.0f} ({pct}%) "
                "— higher leverage.")
        if c["category"] == "expense" and pct is not None and pct >= EXPENSE_GROWTH_PCT_TRIGGER:
            add("medium", "cost",
                f"Expenses grew {pct}% ({c['prior']:,.0f} -> {c['current']:,.0f}), "
                "outpacing typical income growth.")
        if (c["category"] == "income" and c["change"] < 0 and pct is not None
                and pct <= -INCOME_DROP_PCT_TRIGGER):
            add("medium", "profitability",
                f"Income fell {pct}% ({c['prior']:,.0f} -> {c['current']:,.0f}).")

    # largest individual swings -> classify the driver
    for s in cmp["flagged_swings"][:8]:
        nm = s["name"].lower()
        move = f"{s['prior']:,.0f} -> {s['current']:,.0f} ({s['pct_change']}%)"
        if "provision" in nm or "ecl" in nm or "impair" in nm:
            add("high", "credit_risk",
                f"{s['name']}: {move} — change in expected credit loss / provisioning.")
        elif "deferred tax" in nm or "deferred tax" in nm:
            add("medium", "tax", f"{s['name']}: {move}.")
        elif any(k in nm for k in _BORROWING_KWS):
            add("medium", "financing", f"{s['name']}: {move}.")

    # material new borrowings raised in the current year
    big_new = [r for r in cmp["new_accounts"]
               if abs(r["current"]) >= MATERIAL_NEW_BORROWING_FLOOR
               and any(k in r["name"].lower() for k in _BORROWING_KWS)]
    if big_new:
        names = "; ".join(f"{r['name']} ({r['current']:,.0f})" for r in big_new[:6])
        add("high", "financing", f"Significant new borrowings raised this year: {names}.")

    # dedupe identical details, then rank by severity
    seen, deduped = set(), []
    for r in risks:
        key = (r["severity"], r["detail"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    order = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(key=lambda r: order.get(r["severity"], 3))
    summary = {sev: sum(1 for r in deduped if r["severity"] == sev)
               for sev in ("high", "medium", "low")}
    return {"summary": summary, "risks": deduped}


def _fmt_comparison_risk(d: dict) -> str:
    s = d["summary"]
    out = [f"Risk assessment - high={s['high']}, medium={s['medium']}, low={s['low']}:"]
    for r in d["risks"]:
        # ``ind_as_refs`` (real standard citations) are attached by the pipeline
        # after retrieval; fall back to nothing rather than the internal topic hint.
        refs = r.get("ind_as_refs") or []
        ref = f" (relevant: {', '.join(refs)})" if refs else ""
        out.append(f"- [{r['severity'].upper()}] ({r['category']}) {r['detail']}{ref}")
    return "\n".join(out)


def _fmt_comparison(d: dict) -> str:
    """Compact text of the comparison for the analyst LLM (and deterministic fallback)."""
    cur, pri = d["current_label"], d["prior_label"]
    out = [f"Year-over-year comparison: {pri} (prior) -> {cur} (current); "
           f"swings flagged at >= {d['pct_threshold']:g}%."]
    # validation first (per the output standard): does each year tie out?
    tie = d.get("tie_out")
    if tie:
        def _bal(t):
            return ("in balance" if t["balanced"]
                    else f"OUT OF BALANCE by {t['difference']:,.0f}")
        out += ["", "Validation (before comparison):",
                f"- Prior ({pri}): debit {tie['prior']['total_debit']:,.0f} vs credit "
                f"{tie['prior']['total_credit']:,.0f} -> {_bal(tie['prior'])}",
                f"- Current ({cur}): debit {tie['current']['total_debit']:,.0f} vs credit "
                f"{tie['current']['total_credit']:,.0f} -> {_bal(tie['current'])}"]
    out += ["", f"Flags: {d['n_new']} new, {d['n_dropped']} dropped, "
            f"{d.get('n_sign_flips', 0)} sign-flipped, {d['n_flagged']} total above threshold.",
            "", "By category (magnitudes):"]
    if d.get("low_classification_coverage"):
        out.append(f"_Not reliable — only {d.get('classification_coverage_pct')}% of ledger "
                   "value (across both periods) is classified; category movements below may "
                   "not reflect true magnitudes until chart-of-accounts mapping improves._")
    for c in d["by_category"]:
        out.append(f"- {c['category']}: {c['prior']:,.0f} -> {c['current']:,.0f} "
                   f"(change {c['change']:,.0f}, {c['pct_change']}%)")
    sc, sp = d["statements"]["current"], d["statements"]["prior"]
    out += ["", "Statements (prior -> current):",
            f"- Net profit: {sp['profit_and_loss']['net_profit']:,.0f} -> "
            f"{sc['profit_and_loss']['net_profit']:,.0f}",
            f"- Assets: {sp['balance_sheet']['assets']:,.0f} -> {sc['balance_sheet']['assets']:,.0f}"]
    rc, rp = d["ratios"]["current"], d["ratios"]["prior"]
    out += ["", "Ratios (prior -> current):",
            f"- current ratio: {rp['current_ratio']} -> {rc['current_ratio']}",
            f"- debt/equity: {rp['debt_to_equity']} -> {rc['debt_to_equity']}",
            f"- net margin: {rp['net_profit_margin']} -> {rc['net_profit_margin']}"]
    out += ["", f"Largest flagged swings ({d['n_flagged']}):"]
    for s in d["flagged_swings"][:15]:
        out.append(f"- {s['name']} [{s['category']}]: {s['prior']:,.0f} -> {s['current']:,.0f} "
                   f"({s['pct_change']}%)")
    if d["n_new"]:
        out.append(f"New accounts ({d['n_new']}): " +
                   ", ".join(f"{r['name']} ({r['current']:,.0f})" for r in d["new_accounts"][:8]))
    if d["n_dropped"]:
        out.append(f"Dropped accounts ({d['n_dropped']}): " +
                   ", ".join(f"{r['name']} ({r['prior']:,.0f})" for r in d["dropped_accounts"][:8]))
    if d.get("risk"):
        out += ["", _fmt_comparison_risk(d["risk"])]
    return "\n".join(out)
