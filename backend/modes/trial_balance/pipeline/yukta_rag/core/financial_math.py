"""Shared deterministic financial math (single source of truth).

Every ratio, percentage, growth and arithmetic result in the system comes from
here — computed in Python, never by the LLM. Used by the trial-balance / audit
pipeline and exposed to the chatbot's tool-calling agent as the ``calculate`` tool.

Contract (matches the historical helpers being replaced):
  * a zero / invalid denominator returns ``None`` (never raises);
  * results are rounded at the call site's precision via ``ndigits``.
"""

from __future__ import annotations

import ast
import math
import operator
import re

Number = int | float


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def ratio(num: Number | None, den: Number | None, ndigits: int | None = 4) -> float | None:
    """num / den, rounded; None when the denominator is zero/None/invalid."""
    if den in (0, 0.0, None) or num is None:
        return None
    try:
        r = float(num) / float(den)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(r, ndigits) if ndigits is not None else r


def pct_change(current: Number | None, prior: Number | None, ndigits: int | None = 2) -> float | None:
    """(current - prior) / |prior| * 100; None when prior is zero/None."""
    if prior in (0, 0.0, None) or current is None:
        return None
    try:
        r = (float(current) - float(prior)) / abs(float(prior)) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(r, ndigits) if ndigits is not None else r


def variance(current: Number | None, prior: Number | None, ndigits: int | None = 2) -> float | None:
    """Absolute change current - prior."""
    if current is None or prior is None:
        return None
    r = float(current) - float(prior)
    return round(r, ndigits) if ndigits is not None else r


def growth(current: Number | None, prior: Number | None, ndigits: int | None = 2) -> float | None:
    """Alias of pct_change (period-over-period growth %)."""
    return pct_change(current, prior, ndigits)


def cagr(begin: Number | None, end: Number | None, years: Number | None,
         ndigits: int | None = 2) -> float | None:
    """Compound annual growth rate %; None if inputs are non-positive/invalid."""
    try:
        b, e, n = float(begin), float(end), float(years)
    except (TypeError, ValueError):
        return None
    if b <= 0 or e <= 0 or n <= 0:
        return None
    r = (math.pow(e / b, 1.0 / n) - 1.0) * 100
    return round(r, ndigits) if ndigits is not None else r


def common_size(part: Number | None, whole: Number | None, ndigits: int | None = 2) -> float | None:
    """part / whole * 100 (common-size %); None when whole is zero/None."""
    r = ratio(part, whole, ndigits=None)
    return round(r * 100, ndigits) if r is not None and ndigits is not None else (
        r * 100 if r is not None else None)


def _basis(num: Number | None, den: Number | None, result: float | None, times: int = 1,
           suffix: str = "") -> str:
    """Human-readable arithmetic basis, e.g. 'X / Y = Z'."""
    if result is None:
        return f"{num} / {den} = not computable (zero/None denominator)"
    body = f"{num:,} / {den:,} = {result}" if isinstance(num, (int, float)) and isinstance(den, (int, float)) \
        else f"{num} / {den} = {result}"
    return body + suffix


# ---------------------------------------------------------------------------
# Named financial ratios (thin wrappers returning value + basis)
# ---------------------------------------------------------------------------


def _rr(num, den, ndigits=4) -> dict:
    r = ratio(num, den, ndigits)
    return {"value": r, "basis": _basis(num, den, r)}


def current_ratio(current_assets, current_liabilities):
    return _rr(current_assets, current_liabilities)


def quick_ratio(current_assets, inventory, current_liabilities):
    quick_assets = None if current_assets is None or inventory is None else current_assets - inventory
    r = ratio(quick_assets, current_liabilities)
    return {"value": r, "basis": _basis(quick_assets, current_liabilities, r,
                                        suffix="  (quick assets = current assets - inventory)")}


def debt_to_equity(total_debt, equity):
    return _rr(total_debt, equity)


def net_profit_margin(net_profit, revenue):
    return _rr(net_profit, revenue)


def gross_margin(gross_profit, revenue):
    return _rr(gross_profit, revenue)


def operating_margin(operating_profit, revenue):
    return _rr(operating_profit, revenue)


def return_on_equity(net_profit, equity):
    return _rr(net_profit, equity)


def return_on_assets(net_profit, total_assets):
    return _rr(net_profit, total_assets)


def interest_coverage(ebit, interest):
    return _rr(ebit, interest)


def debtor_intensity(trade_receivables, revenue):
    return _rr(trade_receivables, revenue)


def creditor_intensity(trade_payables, purchases):
    return _rr(trade_payables, purchases)


def inventory_intensity(inventory, base):
    return _rr(inventory, base)


def depreciation_proxy(depreciation, gross_ppe):
    return _rr(depreciation, gross_ppe)


def finance_cost_ratio(finance_cost, borrowings):
    return _rr(finance_cost, borrowings)


def eps(net_profit, shares):
    return _rr(net_profit, shares, ndigits=2)


# ---------------------------------------------------------------------------
# Safe formula evaluator (for the `calculate` tool)
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sum": sum}


class _MathError(ValueError):
    pass


def _eval_node(node: ast.AST, variables: dict) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _MathError("only numeric constants are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise _MathError(f"operator {type(node.op).__name__} not allowed")
        left, right = _eval_node(node.left, variables), _eval_node(node.right, variables)
        if op in (operator.truediv, operator.floordiv, operator.mod) and right == 0:
            raise ZeroDivisionError
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise _MathError("unary operator not allowed")
        return op(_eval_node(node.operand, variables))
    if isinstance(node, ast.Name):
        if node.id in variables:
            return float(variables[node.id])
        raise _MathError(f"unknown variable '{node.id}'")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise _MathError("only abs/round/min/max/sum are allowed")
        if node.keywords:
            raise _MathError("keyword arguments not allowed")
        args = [_eval_node(a, variables) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(e, variables) for e in node.elts]
    raise _MathError(f"expression element {type(node).__name__} not allowed")


def evaluate(expression: str, variables: dict | None = None,
             ndigits: int | None = 4) -> dict:
    """Safely evaluate a numeric ``expression`` with named ``variables``.

    Only numbers, + - * / // % **, unary +/-, parentheses, the named variables and
    the functions abs/round/min/max/sum are permitted. Division by zero -> None.
    Returns ``{result, expression, variables, basis, error}``.
    """
    variables = {k: v for k, v in (variables or {}).items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool)}
    out = {"result": None, "expression": expression, "variables": variables,
           "basis": None, "error": None}
    try:
        tree = ast.parse(expression, mode="eval")
        val = _eval_node(tree, variables)
        if isinstance(val, list):
            raise _MathError("expression must reduce to a number")
        result = round(float(val), ndigits) if ndigits is not None else float(val)
        out["result"] = result
        # substituted arithmetic for transparency (whole-word replace only, so a
        # variable named 'a' is not spliced into function names like 'abs'/'max')
        subst = expression
        for name, num in sorted(variables.items(), key=lambda kv: -len(kv[0])):
            rep = f"{num:,}" if isinstance(num, (int, float)) else str(num)
            subst = re.sub(rf"\b{re.escape(name)}\b", rep, subst)
        out["basis"] = f"{subst} = {result}"
    except ZeroDivisionError:
        out["error"] = "division by zero"
    except _MathError as exc:
        out["error"] = str(exc)
    except (SyntaxError, ValueError, TypeError) as exc:
        out["error"] = f"invalid expression: {exc}"
    return out
