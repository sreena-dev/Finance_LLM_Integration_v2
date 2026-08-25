"""Category L (CROSS-YEAR, comparison mode only — runs after A-K complete
for both years): L-1, L-2, L-3.

Plus Layer 2 (structural diff) and Layer 3 (variance analysis), which only
run in comparison mode, after Layer 1 completes with no HALT.
"""

from __future__ import annotations

from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable
from yukta_rag.tb_validation.tbv_types import (HALT, PASS, WARNING,
                                               compute_variance, result)


def l1_company_code_filter(table: ParsedTBTable, target_company_code: str | None = None) -> dict:
    if not table.roles_resolved.get("company_code"):
        return result("L-1", WARNING, "Company code column not resolved; nothing to filter — skipped.")
    companies = sorted({r.company_code for r in table.rows if r.company_code})
    if len(companies) <= 1:
        return result("L-1", PASS, "Single (or no) company code detected; no ambiguity.",
                      companies=companies)
    if not target_company_code:
        return result("L-1", HALT,
                      f"Multiple company codes present ({', '.join(companies)}) but no target "
                      "company code was specified — cannot determine which company this "
                      "validation applies to.", companies=companies)
    if target_company_code not in companies:
        return result("L-1", HALT,
                      f"Target company code '{target_company_code}' not found among the "
                      f"companies present ({', '.join(companies)}).", companies=companies)
    return result("L-1", PASS,
                  f"Target company code '{target_company_code}' is present in the table.",
                  companies=companies)


def l2_ledger_alignment(py_table: ParsedTBTable, cy_table: ParsedTBTable) -> dict:
    if not py_table.roles_resolved.get("ledger_code") or not cy_table.roles_resolved.get("ledger_code"):
        return result("L-2", HALT, "ledger_code not resolved in one or both years.")
    py_by_code = {r.ledger_code: r.ledger_name for r in py_table.rows if r.ledger_code}
    cy_by_code = {r.ledger_code: r.ledger_name for r in cy_table.rows if r.ledger_code}
    common = set(py_by_code) & set(cy_by_code)
    name_mismatches = [
        {"ledger_code": code, "py_name": py_by_code.get(code), "cy_name": cy_by_code.get(code)}
        for code in sorted(common)
        if (py_by_code.get(code) or "").strip().lower() != (cy_by_code.get(code) or "").strip().lower()
    ]
    if name_mismatches:
        return result("L-2", WARNING,
                      f"{len(name_mismatches)} of {len(common)} common ledger code(s) have "
                      "mismatched names between PY and CY (never a HALT).",
                      name_mismatches=name_mismatches, n_common=len(common))
    return result("L-2", PASS,
                  f"{len(common)} common ledger code(s) align between PY and CY with matching names.",
                  n_common=len(common))


def l3_opening_closing_continuity(py_table: ParsedTBTable, cy_table: ParsedTBTable) -> dict:
    if not cy_table.roles_resolved.get("opening_balance"):
        return result("L-3", WARNING, "CY opening balance not resolved — skipped.")
    py_close = {r.ledger_code: r.closing_balance for r in py_table.rows if r.ledger_code}
    cy_open = {r.ledger_code: r.opening_balance for r in cy_table.rows if r.ledger_code}
    common = set(py_close) & set(cy_open)
    mismatches = []
    for code in sorted(common):
        p, c = py_close.get(code), cy_open.get(code)
        if p is None or c is None:
            continue
        if round(p, 2) != round(c, 2):
            mismatches.append({"ledger_code": code, "py_closing": round(p, 2),
                               "cy_opening": round(c, 2), "diff": round(c - p, 2)})
    if mismatches:
        return result("L-3", WARNING,
                      f"{len(mismatches)} of {len(common)} ledger code(s) show PY closing != "
                      "CY opening. Legitimate restatement/reclassification can cause this — "
                      "not a HALT.", mismatches=mismatches, n_common=len(common))
    return result("L-3", PASS,
                  f"PY closing matches CY opening for all {len(common)} common ledger code(s).",
                  n_common=len(common))


def layer2_structural(py_table: ParsedTBTable, cy_table: ParsedTBTable) -> dict:
    py_by_code = {r.ledger_code: r for r in py_table.rows if r.ledger_code}
    cy_by_code = {r.ledger_code: r for r in cy_table.rows if r.ledger_code}
    new_codes = sorted(set(cy_by_code) - set(py_by_code))
    removed_codes = sorted(set(py_by_code) - set(cy_by_code))
    new_ledgers = [{"s_no": i + 1, "ledger_code": c, "ledger_name": cy_by_code[c].ledger_name}
                  for i, c in enumerate(new_codes)]
    removed_ledgers = [{"s_no": i + 1, "ledger_code": c, "ledger_name": py_by_code[c].ledger_name}
                       for i, c in enumerate(removed_codes)]
    return {
        "py_ledger_count": len(py_by_code), "cy_ledger_count": len(cy_by_code),
        "new_ledgers": new_ledgers, "removed_ledgers": removed_ledgers,
        "n_new": len(new_ledgers), "n_removed": len(removed_ledgers),
        "delta_count": len(cy_by_code) - len(py_by_code),
    }


def build_full_table_rows(py_table: ParsedTBTable, cy_table: ParsedTBTable,
                          variance_rows: list[dict]) -> list[dict]:
    """One row per ledger code (union of PY/CY), merging both periods' raw
    opening/closing/debit/credit with Layer 3's variance/flag — "the full
    trial balance," Layer 1-3 in one table."""
    py_by_code = {r.ledger_code: r for r in py_table.rows if r.ledger_code}
    cy_by_code = {r.ledger_code: r for r in cy_table.rows if r.ledger_code}
    variance_by_code = {r["ledger_code"]: r for r in variance_rows}
    rows = []
    for code in sorted(set(py_by_code) | set(cy_by_code)):
        p, c = py_by_code.get(code), cy_by_code.get(code)
        v = variance_by_code.get(code, {})
        rows.append({
            "ledger_code": code,
            "ledger_name": (c.ledger_name if c else None) or (p.ledger_name if p else None),
            "py_opening": p.opening_balance if p else None,
            "py_debit": p.debit if p else None,
            "py_credit": p.credit if p else None,
            "py_closing": p.closing_balance if p else None,
            "cy_opening": c.opening_balance if c else None,
            "cy_debit": c.debit if c else None,
            "cy_credit": c.credit if c else None,
            "cy_closing": c.closing_balance if c else None,
            "variance": v.get("variance"), "variance_pct": v.get("variance_pct"),
            "flag": v.get("flag"),
        })
    return rows


def layer3_variance(py_table: ParsedTBTable, cy_table: ParsedTBTable,
                    variance_materiality_pct: float | None = None) -> list[dict]:
    py_by_code = {r.ledger_code: (r.closing_balance or 0.0) for r in py_table.rows if r.ledger_code}
    cy_by_code = {r.ledger_code: (r.closing_balance or 0.0) for r in cy_table.rows if r.ledger_code}
    all_codes = sorted(set(py_by_code) | set(cy_by_code))

    rows = []
    for code in all_codes:
        py_bal = py_by_code.get(code, 0.0)
        cy_bal = cy_by_code.get(code, 0.0)
        variance, variance_pct, flag = compute_variance(py_bal, cy_bal, variance_materiality_pct)

        rows.append({
            "ledger_code": code,
            "py_balance": round(py_bal, 2), "cy_balance": round(cy_bal, 2),
            "variance": variance, "variance_pct": variance_pct, "flag": flag,
        })
    return rows
