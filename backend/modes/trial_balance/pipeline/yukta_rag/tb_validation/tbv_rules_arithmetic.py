"""Layer 1 — category B (ROW ARITHMETIC) and category C (AGGREGATE TIE-OUT).

TB-005, TB-006, TB-007, TB-008, TB-032
TB-009, TB-010, TB-011, TB-012, TB-024
"""

from __future__ import annotations

from yukta_rag.tb_validation.tbv_config import (CREDIT_NORMAL_KEYWORDS,
                                                DEBIT_NORMAL_KEYWORDS,
                                                NEGLIGENCE_TOLERANCE_PCT)
from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable
from yukta_rag.tb_validation.tbv_types import (HALT, PASS, WARNING,
                                               compute_variance, result)

# --- shared helper: every Layer-1 aggregate blocking check uses this same
# diff-against-tolerance pattern (TB-005, TB-009, TB-010, TB-011, TB-012, TB-024).
# Three-way outcome: an EXACT match (diff == 0) -> PASS; a non-zero difference
# that's still within tolerance (rounding drift) -> WARNING, reporting the
# drift rather than silently passing; beyond tolerance -> HALT (unchanged). ---


def _diff_pct_check(rule_id: str, expected: float, actual: float, activity_base: float,
                    tolerance_pct: float, pass_message: str, warning_message: str,
                    fail_message: str, **extra_details) -> dict:
    diff = actual - expected
    base = activity_base if activity_base else 1.0
    diff_pct = abs(diff) / base * 100
    details = {"expected": round(expected, 2), "actual": round(actual, 2),
              "diff": round(diff, 2), "diff_pct": round(diff_pct, 4), **extra_details}
    if diff == 0:
        return result(rule_id, PASS, pass_message, **details)
    if diff_pct <= tolerance_pct:
        return result(rule_id, WARNING, warning_message, **details)
    return result(rule_id, HALT, fail_message, **details)


def _group_expected_sign(group_label: str | None) -> int | None:
    """+1 = debit-normal expected, -1 = credit-normal expected, None = unresolvable
    from the group label's own text (not an account-name re-derivation)."""
    low = (group_label or "").lower()
    if any(k in low for k in DEBIT_NORMAL_KEYWORDS):
        return 1
    if any(k in low for k in CREDIT_NORMAL_KEYWORDS):
        return -1
    return None


# --- B. ROW ARITHMETIC -------------------------------------------------------

def tb_005_row_arithmetic(table: ParsedTBTable, tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    """opening/closing use the signed opening/closing (debit-positive)
    convention; debit/credit are unsigned magnitudes. Any row with a real
    mismatch (beyond tolerance) HALTs the whole table. If there are no real
    mismatches but some rows show non-zero-but-within-tolerance drift, that's
    a WARNING (listed, not silently passed) rather than a bare PASS — PASS is
    reserved for every row tying out exactly."""
    needed = ("opening_balance", "debit", "credit", "closing_balance")
    if not all(table.roles_resolved.get(r) for r in needed):
        return result("TB-005", WARNING, f"Requires all of {needed} resolved — skipped.")
    if not table.has_genuine_movement:
        return result("TB-005", WARNING,
                      "Source has no genuine period-movement columns (only a combined closing "
                      "balance was available); debit/credit would be a derived sign-split of "
                      "the closing balance, not real movement — the row-level opening + "
                      "movement = closing check does not apply here, skipped.")
    mismatches, drift = [], []
    for row in table.rows:
        if row.closing_balance is None:
            continue
        opening = row.opening_balance or 0.0
        debit = row.debit or 0.0
        credit = row.credit or 0.0
        expected_closing = opening + debit - credit
        actual_closing = row.closing_balance
        diff = actual_closing - expected_closing
        if diff == 0:
            continue
        activity_base = max(abs(expected_closing), abs(actual_closing),
                            abs(opening) + debit + credit, 1.0)
        diff_pct = abs(diff) / activity_base * 100
        entry = {
            "row": row.row_number, "code": row.ledger_code, "name": row.ledger_name,
            "opening": round(opening, 2), "debit": round(debit, 2), "credit": round(credit, 2),
            "expected_closing": round(expected_closing, 2), "actual_closing": round(actual_closing, 2),
            "diff": round(diff, 2), "diff_pct": round(diff_pct, 4),
        }
        (mismatches if diff_pct > tolerance_pct else drift).append(entry)
    if mismatches:
        return result("TB-005", HALT,
                      f"{len(mismatches)} row(s) fail closing = opening + debit - credit "
                      f"beyond {tolerance_pct}% tolerance.", mismatches=mismatches)
    if drift:
        return result("TB-005", WARNING,
                      f"No real mismatches, but {len(drift)} row(s) show non-zero rounding "
                      f"drift within the {tolerance_pct}% tolerance.", drift=drift)
    return result("TB-005", PASS, "All rows tie out exactly: closing = opening + debit - credit.")


def build_single_table_rows(table: ParsedTBTable,
                            variance_materiality_pct: float | None = None) -> list[dict]:
    """One row per ledger code: opening, debit, credit, closing, and the
    opening->closing movement where an opening figure is available. Always
    returns a row for every ledger code with a closing balance — if this
    table's opening_balance role wasn't resolved at all (a Balance-only
    source with no true opening column), movement/flag are reported as
    unavailable ("NO_DATA") per row rather than fabricating a movement from
    an assumed opening of zero, and rather than omitting the whole table."""
    rows = []
    for row in table.rows:
        if not row.ledger_code or row.closing_balance is None:
            continue
        closing = row.closing_balance
        if row.opening_balance is None:
            opening, movement, movement_pct, flag = None, None, None, "NO_DATA"
        else:
            opening = row.opening_balance
            movement, movement_pct, flag = compute_variance(opening, closing, variance_materiality_pct)
        rows.append({
            "ledger_code": row.ledger_code, "ledger_name": row.ledger_name,
            "opening": round(opening, 2) if opening is not None else None,
            "debit": round(row.debit or 0.0, 2), "credit": round(row.credit or 0.0, 2),
            "closing": round(closing, 2),
            "movement": movement, "movement_pct": movement_pct, "flag": flag,
        })
    rows.sort(key=lambda r: r["ledger_code"])
    return rows


def tb_006_opening_sign_vs_group(table: ParsedTBTable) -> dict:
    if not table.roles_resolved.get("group") or not table.roles_resolved.get("opening_balance"):
        return result("TB-006", WARNING, "Requires Group and opening balance resolved — skipped.")
    offenders = []
    for row in table.rows:
        if row.opening_balance is None or not row.group:
            continue
        expected = _group_expected_sign(row.group)
        if expected is None or row.opening_balance == 0:
            continue
        if (expected == 1 and row.opening_balance < 0) or (expected == -1 and row.opening_balance > 0):
            offenders.append({"row": row.row_number, "code": row.ledger_code,
                              "group": row.group, "opening_balance": row.opening_balance})
    if offenders:
        return result("TB-006", WARNING,
                      f"{len(offenders)} row(s) have an opening-balance sign inconsistent with "
                      "their Group's inferred normal-balance nature.", offenders=offenders)
    return result("TB-006", PASS, "Opening-balance signs consistent with Group normal-balance nature.")


def tb_007_closing_sign_vs_group(table: ParsedTBTable) -> dict:
    if not table.roles_resolved.get("group"):
        return result("TB-007", WARNING, "Requires Group resolved — skipped.")
    offenders = []
    for row in table.rows:
        if row.closing_balance is None or not row.group:
            continue
        expected = _group_expected_sign(row.group)
        if expected is None or row.closing_balance == 0:
            continue
        if (expected == 1 and row.closing_balance < 0) or (expected == -1 and row.closing_balance > 0):
            offenders.append({"row": row.row_number, "code": row.ledger_code,
                              "group": row.group, "closing_balance": row.closing_balance})
    if offenders:
        return result("TB-007", WARNING,
                      f"{len(offenders)} row(s) have a closing-balance sign inconsistent with "
                      "their Group's inferred normal-balance nature.", offenders=offenders)
    return result("TB-007", PASS, "Closing-balance signs consistent with Group normal-balance nature.")


def tb_008_never_invert(table: ParsedTBTable, never_invert_accounts: list[str] | None = None) -> dict:
    if not never_invert_accounts:
        return result("TB-008", WARNING, "No never-invert account list supplied — skipped.")
    watch = {c.strip() for c in never_invert_accounts}
    offenders = []
    for row in table.rows:
        if row.ledger_code not in watch:
            continue
        if row.opening_balance is None or row.closing_balance is None:
            continue
        if row.opening_balance == 0 or row.closing_balance == 0:
            continue
        if (row.opening_balance > 0) != (row.closing_balance > 0):
            offenders.append({"row": row.row_number, "code": row.ledger_code,
                              "opening_balance": row.opening_balance,
                              "closing_balance": row.closing_balance})
    if offenders:
        return result("TB-008", WARNING,
                      f"{len(offenders)} never-invert account(s) changed sign between opening "
                      "and closing.", offenders=offenders)
    return result("TB-008", PASS, "No never-invert account changed sign.")


def tb_032_pl_no_opening(table: ParsedTBTable) -> dict:
    if not table.roles_resolved.get("group") or not table.roles_resolved.get("opening_balance"):
        return result("TB-032", WARNING, "Requires Group and opening balance resolved — skipped.")
    pl_kws = ("income", "revenue", "sales", "expense", "expenditure", "cost of")
    offenders = []
    for row in table.rows:
        if not row.group or row.opening_balance is None:
            continue
        low = row.group.lower()
        if any(k in low for k in pl_kws) and abs(row.opening_balance) > 0.005:
            offenders.append({"row": row.row_number, "code": row.ledger_code,
                              "group": row.group, "opening_balance": row.opening_balance})
    if offenders:
        return result("TB-032", WARNING,
                      f"{len(offenders)} P&L-type account(s) carry a non-zero opening balance.",
                      offenders=offenders)
    return result("TB-032", PASS, "No P&L-type account carries an opening balance.")


# --- C. AGGREGATE TIE-OUT ----------------------------------------------------

def tb_009_total_debit_credit(table: ParsedTBTable, tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    if not table.roles_resolved.get("debit") or not table.roles_resolved.get("credit"):
        return result("TB-009", WARNING, "Requires debit and credit both resolved — skipped.")
    total_debit = sum(r.debit or 0.0 for r in table.rows)
    total_credit = sum(r.credit or 0.0 for r in table.rows)
    return _diff_pct_check("TB-009", total_credit, total_debit,
                          max(abs(total_debit), abs(total_credit), 1.0), tolerance_pct,
                          "Total debit = total credit exactly.",
                          "Total debit and total credit differ by a small rounding drift "
                          "within tolerance.",
                          "Total debit does not equal total credit beyond tolerance.")


def tb_010_opening_sum_zero(table: ParsedTBTable, tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    if not table.roles_resolved.get("opening_balance"):
        return result("TB-010", WARNING, "Opening balance not resolved — skipped.")
    total = sum(r.opening_balance or 0.0 for r in table.rows)
    gross = sum(abs(r.opening_balance or 0.0) for r in table.rows)
    return _diff_pct_check("TB-010", 0.0, total, max(gross, 1.0), tolerance_pct,
                          "Sum of opening balances is exactly nil.",
                          "Sum of opening balances is not exactly nil, but within tolerance "
                          "(rounding drift).",
                          "Sum of opening balances is not nil beyond tolerance.")


def tb_011_closing_sum_zero(table: ParsedTBTable, tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    if not table.roles_resolved.get("closing_balance"):
        return result("TB-011", WARNING, "Closing balance not resolved — skipped.")
    total = sum(r.closing_balance or 0.0 for r in table.rows)
    gross = sum(abs(r.closing_balance or 0.0) for r in table.rows)
    return _diff_pct_check("TB-011", 0.0, total, max(gross, 1.0), tolerance_pct,
                          "Sum of closing balances is exactly nil.",
                          "Sum of closing balances is not exactly nil, but within tolerance "
                          "(rounding drift).",
                          "Sum of closing balances is not nil beyond tolerance.")


_ASSET_GROUP_KWS = ("asset", "cash", "bank", "receivable", "debtor", "inventor", "stock",
                   "prepaid", "advance", "investment", "property", "plant", "equipment")
_LIABILITY_GROUP_KWS = ("liabilit", "payable", "creditor", "borrowing", "provision",
                        "deferred tax liab")
_EQUITY_GROUP_KWS = ("equity", "capital", "reserve", "surplus", "retained earning")


def _balance_sheet_bucket(group_label: str | None) -> str | None:
    low = (group_label or "").lower()
    if "capital work" in low or "cwip" in low or "capital wip" in low:
        return "asset"
    if any(k in low for k in _EQUITY_GROUP_KWS):
        return "equity"
    if any(k in low for k in _LIABILITY_GROUP_KWS):
        return "liability"
    if any(k in low for k in _ASSET_GROUP_KWS):
        return "asset"
    return None


def tb_012_assets_equal_liab_equity(table: ParsedTBTable,
                                    tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    if not table.roles_resolved.get("group") or not table.roles_resolved.get("closing_balance"):
        return result("TB-012", WARNING, "Requires Group and closing balance resolved — skipped.")
    assets = liabilities = equity = 0.0
    for row in table.rows:
        bucket = _balance_sheet_bucket(row.group)
        if bucket is None or row.closing_balance is None:
            continue
        if bucket == "asset":
            assets += row.closing_balance
        elif bucket == "liability":
            liabilities += row.closing_balance
        elif bucket == "equity":
            equity += row.closing_balance
    if assets == 0 and liabilities == 0 and equity == 0:
        return result("TB-012", WARNING,
                      "No rows could be bucketed into asset/liability/equity from Group text "
                      "— skipped.")
    total = assets + liabilities + equity  # should net to ~0 in debit-positive convention
    activity_base = max(abs(assets), abs(liabilities + equity), 1.0)
    return _diff_pct_check("TB-012", 0.0, total, activity_base, tolerance_pct,
                          "Assets = Liabilities + Equity exactly.",
                          "Assets vs Liabilities + Equity differ by a small rounding drift "
                          "within tolerance.",
                          "Assets != Liabilities + Equity beyond tolerance.",
                          assets=round(assets, 2), liabilities=round(liabilities, 2),
                          equity=round(equity, 2))


_INCOME_GROUP_KWS = ("income", "revenue", "sales", "other income")
_EXPENSE_GROUP_KWS = ("expense", "expenditure", "cost of", "depreciation", "finance cost")


def tb_024_income_expense_net_pl(table: ParsedTBTable, external_pl_figure: float | None = None,
                                 tolerance_pct: float = NEGLIGENCE_TOLERANCE_PCT) -> dict:
    if external_pl_figure is None:
        return result("TB-024", WARNING, "No external P&L figure supplied to compare against — skipped.")
    if not table.roles_resolved.get("group") or not table.roles_resolved.get("closing_balance"):
        return result("TB-024", WARNING, "Requires Group and closing balance resolved — skipped.")
    income = expense = 0.0
    for row in table.rows:
        if row.closing_balance is None or not row.group:
            continue
        low = row.group.lower()
        if any(k in low for k in _INCOME_GROUP_KWS):
            income += row.closing_balance
        elif any(k in low for k in _EXPENSE_GROUP_KWS):
            expense += row.closing_balance
    # income is credit-normal (negative-signed), expense is debit-normal (positive-signed);
    # net P&L (profit positive) = -income - expense in this signed convention.
    computed_pl = -income - expense
    return _diff_pct_check("TB-024", external_pl_figure, computed_pl,
                          max(abs(external_pl_figure), abs(computed_pl), 1.0), tolerance_pct,
                          "Income - Expense matches the supplied external P&L figure exactly.",
                          "Income - Expense differs from the supplied external P&L figure by "
                          "a small rounding drift within tolerance.",
                          "Income - Expense does not match the supplied external P&L figure "
                          "beyond tolerance.", income=round(income, 2), expense=round(expense, 2))
