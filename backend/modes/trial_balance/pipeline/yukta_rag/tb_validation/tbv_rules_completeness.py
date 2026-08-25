"""Layer 1 — category E (CONTINUITY), F (COMPLETENESS), G (STATUTORY MAPPING),
K (CROSS-DOCUMENT).

TB-015
TB-016, TB-017, TB-018, TB-019, TB-028
TB-020
TB-031

TB-028 is the ONE explicitly-approved exception importing from
``yukta_rag.audit`` (a narrow, keyword-only tagging utility, not the
FSLI/findings engine) — no other rule in this module touches ``audit/``.
"""

from __future__ import annotations

from yukta_rag.audit.audit_mapping import sensitive_tags  # approved exception, TB-028 only
from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable
from yukta_rag.tb_validation.tbv_types import HALT, PASS, WARNING, result

_ACTIVITY_MIN_ABS = 1.0
_ZERO_CLOSE_EPS = 0.005


# --- E. CONTINUITY -----------------------------------------------------------

def tb_015_activity_closes_zero(table: ParsedTBTable) -> dict:
    if not table.roles_resolved.get("debit") or not table.roles_resolved.get("credit"):
        return result("TB-015", WARNING, "Requires debit and credit resolved — skipped.")
    flagged = []
    for row in table.rows:
        if row.closing_balance is None:
            continue
        activity = (row.debit or 0.0) + (row.credit or 0.0)
        if activity > _ACTIVITY_MIN_ABS and abs(row.closing_balance) <= _ZERO_CLOSE_EPS:
            flagged.append({"row": row.row_number, "code": row.ledger_code,
                            "name": row.ledger_name, "activity": round(activity, 2)})
    if flagged:
        return result("TB-015", WARNING,
                      f"{len(flagged)} account(s) had in-period activity but close at zero.",
                      flagged=flagged)
    return result("TB-015", PASS, "No account with activity closes at zero.")


# --- F. COMPLETENESS ---------------------------------------------------------

def tb_016_no_duplicate_gl_group(table: ParsedTBTable) -> dict:
    """Reduces to TB-003 when Group isn't resolved — the (code, group) key
    naturally collapses to a code-only key in that case."""
    counts: dict[tuple, int] = {}
    for row in table.rows:
        if not row.ledger_code:
            continue
        key = (row.ledger_code, row.group or "")
        counts[key] = counts.get(key, 0) + 1
    dupes = {k: n for k, n in counts.items() if n > 1}
    if dupes:
        return result("TB-016", HALT,
                      f"{len(dupes)} GL code+Group combination(s) are duplicated.",
                      duplicates={f"{c}|{g}": n for (c, g), n in list(dupes.items())[:20]})
    return result("TB-016", PASS, "No duplicate GL code+Group combination.")


def tb_017_mandatory_heads_present(table: ParsedTBTable,
                                   required_heads: list[str] | None = None) -> dict:
    if not required_heads:
        return result("TB-017", WARNING, "No mandatory-heads list supplied — skipped.")
    present_text = " | ".join(
        f"{r.group or ''} {r.ledger_name or ''}".lower() for r in table.rows)
    missing = [h for h in required_heads if h.strip().lower() not in present_text]
    if missing:
        return result("TB-017", WARNING,
                      f"{len(missing)} mandatory head(s) not found: {', '.join(missing)}.",
                      missing_heads=missing)
    return result("TB-017", PASS, "All mandatory heads are present.")


def tb_018_suspense_nonzero(table: ParsedTBTable) -> dict:
    flagged = []
    for row in table.rows:
        text = f"{row.group or ''} {row.ledger_name or ''}".lower()
        if "suspense" in text and row.closing_balance and abs(row.closing_balance) > _ZERO_CLOSE_EPS:
            flagged.append({"row": row.row_number, "code": row.ledger_code,
                            "name": row.ledger_name, "closing_balance": row.closing_balance})
    if flagged:
        return result("TB-018", WARNING,
                      f"{len(flagged)} suspense account(s) carry a non-zero balance at period end.",
                      flagged=flagged)
    return result("TB-018", PASS, "No suspense account carries a non-zero period-end balance.")


def tb_019_rounding_within_materiality(table: ParsedTBTable,
                                       rounding_account_threshold: float | None = None) -> dict:
    if rounding_account_threshold is None:
        return result("TB-019", WARNING, "ROUNDING_ACCOUNT_THRESHOLD not supplied — required input, skipped.")
    flagged = []
    for row in table.rows:
        text = f"{row.group or ''} {row.ledger_name or ''}".lower()
        if "round" in text and row.closing_balance is not None:
            if abs(row.closing_balance) > rounding_account_threshold:
                flagged.append({"row": row.row_number, "code": row.ledger_code,
                                "name": row.ledger_name, "closing_balance": row.closing_balance,
                                "threshold": rounding_account_threshold})
    if flagged:
        return result("TB-019", WARNING,
                      f"{len(flagged)} rounding-off head(s) exceed the materiality threshold "
                      f"of {rounding_account_threshold}.", flagged=flagged)
    return result("TB-019", PASS, "Rounding-off head(s), if any, are within materiality.")


def tb_028_sensitive_heads(table: ParsedTBTable) -> dict:
    tagged = []
    for row in table.rows:
        tags = sensitive_tags(row.ledger_name or "", row.ledger_code)
        if tags:
            tagged.append({"row": row.row_number, "code": row.ledger_code,
                           "name": row.ledger_name, "tags": tags})
    if tagged:
        return result("TB-028", WARNING,
                      f"{len(tagged)} sensitive-nature head(s) present (surfaced regardless of "
                      "materiality).", tagged=tagged)
    return result("TB-028", PASS, "No sensitive-nature heads identified by keyword/tag match.")


# --- G. STATUTORY MAPPING -----------------------------------------------------

def tb_020_tax_head_sign(table: ParsedTBTable,
                         tax_head_sign_map: dict[str, str] | None = None) -> dict:
    if not tax_head_sign_map:
        return result("TB-020", WARNING, "No tax-head-to-expected-sign map supplied — skipped.")
    offenders = []
    norm_map = {k.strip().lower(): v.strip().lower() for k, v in tax_head_sign_map.items()}
    for row in table.rows:
        text = f"{row.group or ''} {row.ledger_name or ''}".lower()
        for head, expected in norm_map.items():
            if head in text and row.closing_balance is not None and row.closing_balance != 0:
                actual = "debit" if row.closing_balance > 0 else "credit"
                if actual != expected:
                    offenders.append({"row": row.row_number, "code": row.ledger_code,
                                      "name": row.ledger_name, "matched_head": head,
                                      "expected": expected, "actual": actual,
                                      "closing_balance": row.closing_balance})
    if offenders:
        return result("TB-020", WARNING,
                      f"{len(offenders)} tax-head account(s) carry a sign inconsistent with "
                      "their expected nature.", offenders=offenders)
    return result("TB-020", PASS, "All matched tax heads carry the expected sign.")


# --- K. CROSS-DOCUMENT --------------------------------------------------------

def tb_031_control_branch_sub_ledger(table: ParsedTBTable,
                                     sub_ledger_ref: list[str] | None = None) -> dict:
    if sub_ledger_ref is None:
        return result("TB-031", WARNING, "No sub-ledger reference file supplied — skipped.")
    backed = {c.strip() for c in sub_ledger_ref}
    offenders = []
    for row in table.rows:
        text = f"{row.group or ''} {row.ledger_name or ''}".lower()
        if ("control" in text or "branch" in text) and row.ledger_code not in backed:
            offenders.append({"row": row.row_number, "code": row.ledger_code, "name": row.ledger_name})
    if offenders:
        return result("TB-031", WARNING,
                      f"{len(offenders)} control/branch account(s) have no matching sub-ledger "
                      "backup in the supplied reference.", offenders=offenders)
    return result("TB-031", PASS, "All control/branch accounts have sub-ledger backup.")
