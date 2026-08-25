"""Layer 1 — category A (STRUCTURAL/SCHEMA) and category D (GL MASTER DATA).

TB-000, TB-001, TB-002, TB-003, TB-004, TB-025, TB-026, TB-029
TB-013, TB-014, TB-030
"""

from __future__ import annotations

from collections import Counter

from yukta_rag.tb_validation.tbv_config import (CREDIT_NORMAL_KEYWORDS,
                                                DEBIT_NORMAL_KEYWORDS,
                                                EXPECTED_DECIMAL_PLACES,
                                                SIGN_CONVENTION_AGREEMENT_THRESHOLD,
                                                SIGN_CONVENTION_MIN_ANCHORS)
from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable, REQUIRED_ROLES
from yukta_rag.tb_validation.tbv_types import HALT, PASS, SKIPPED, WARNING, result

_PRECISION_EPS = 1e-9


# --- A. STRUCTURAL/SCHEMA ---------------------------------------------------

def tb_000_sign_convention(table: ParsedTBTable, documented_convention: str | None = None) -> dict:
    """Documented-and-mixed convention -> HALT. Otherwise, when undocumented,
    a keyword-anchor heuristic against DEBIT_NORMAL/CREDIT_NORMAL_KEYWORDS —
    this heuristic sub-check never itself halts, only WARNs or PASSes."""
    if documented_convention:
        low = documented_convention.strip().lower()
        if low in ("mixed", "inconsistent", "undocumented-mixed"):
            return result("TB-000", HALT,
                          f"Sign convention is explicitly documented as '{documented_convention}' "
                          "(mixed/inconsistent) — cannot proceed without a single convention.")
        return result("TB-000", PASS,
                      f"Sign convention documented as '{documented_convention}'.")

    debit_hits, debit_agree = 0, 0
    credit_hits, credit_agree = 0, 0
    for row in table.rows:
        name = (row.ledger_name or "").lower()
        if row.closing_balance is None:
            continue
        if any(k in name for k in DEBIT_NORMAL_KEYWORDS):
            debit_hits += 1
            if row.closing_balance >= 0:
                debit_agree += 1
        elif any(k in name for k in CREDIT_NORMAL_KEYWORDS):
            credit_hits += 1
            if row.closing_balance <= 0:
                credit_agree += 1

    if debit_hits < SIGN_CONVENTION_MIN_ANCHORS or credit_hits < SIGN_CONVENTION_MIN_ANCHORS:
        return result("TB-000", WARNING,
                      "Not enough keyword-anchor accounts on one or both sides "
                      f"(debit-side anchors: {debit_hits}, credit-side anchors: {credit_hits}, "
                      f"need >= {SIGN_CONVENTION_MIN_ANCHORS} each) to judge the sign convention "
                      "heuristically; convention was not separately documented.",
                      debit_hits=debit_hits, credit_hits=credit_hits)

    debit_pct = debit_agree / debit_hits
    credit_pct = credit_agree / credit_hits
    if debit_pct < SIGN_CONVENTION_AGREEMENT_THRESHOLD or credit_pct < SIGN_CONVENTION_AGREEMENT_THRESHOLD:
        return result("TB-000", WARNING,
                      "Sign-convention heuristic suggests a possibly mixed/inconsistent "
                      f"convention (debit-anchor agreement {debit_pct:.0%}, "
                      f"credit-anchor agreement {credit_pct:.0%}, need >= "
                      f"{SIGN_CONVENTION_AGREEMENT_THRESHOLD:.0%}). Convention was not "
                      "documented — requires manual review.",
                      debit_agreement=round(debit_pct, 3), credit_agreement=round(credit_pct, 3))
    return result("TB-000", PASS,
                  f"Undocumented convention inferred consistent (debit-anchor agreement "
                  f"{debit_pct:.0%}, credit-anchor agreement {credit_pct:.0%}).",
                  debit_agreement=round(debit_pct, 3), credit_agreement=round(credit_pct, 3))


def tb_001_required_columns(table: ParsedTBTable) -> dict:
    missing = [r for r in REQUIRED_ROLES if not table.roles_resolved.get(r)]
    if missing:
        return result("TB-001", HALT,
                      f"Required column role(s) could not be resolved: {', '.join(missing)}. "
                      "This table is unusable for validation.", missing_roles=missing)
    return result("TB-001", PASS, "ledger_code and closing_balance both resolved.")


def tb_002_gl_code_non_blank(table: ParsedTBTable) -> dict:
    blanks = [r.row_number for r in table.rows if not (r.ledger_code or "").strip()]
    if blanks:
        return result("TB-002", HALT,
                      f"{len(blanks)} row(s) have a blank GL code.", blank_rows=blanks)
    return result("TB-002", PASS, "No blank GL codes.")


def tb_003_gl_code_unique(table: ParsedTBTable) -> dict:
    counts = Counter(r.ledger_code for r in table.rows if r.ledger_code)
    dupes = {code: n for code, n in counts.items() if n > 1}
    if dupes:
        return result("TB-003", HALT,
                      f"{len(dupes)} GL code(s) appear more than once: "
                      f"{', '.join(list(dupes)[:10])}.", duplicate_codes=dupes)
    return result("TB-003", PASS, "All GL codes are unique.")


def tb_004_numeric_validity(table: ParsedTBTable) -> dict:
    failures = []
    for row in table.rows:
        for field_name, value, bad in (
            ("opening_balance", row.opening_balance, row.opening_unparseable),
            ("closing_balance", row.closing_balance, row.closing_unparseable),
            ("debit", row.debit, row.debit_unparseable),
            ("credit", row.credit, row.credit_unparseable),
        ):
            if bad:
                failures.append({"row": row.row_number, "code": row.ledger_code,
                                 "field": field_name, "issue": "unparseable"})
                continue
            if value is not None and abs(round(value, EXPECTED_DECIMAL_PLACES) - value) > _PRECISION_EPS:
                failures.append({"row": row.row_number, "code": row.ledger_code,
                                 "field": field_name, "issue": "inconsistent precision", "value": value})
    if failures:
        return result("TB-004", HALT,
                      f"{len(failures)} numeric-validity/precision failure(s) found.",
                      failures=failures[:50], n_failures=len(failures))
    return result("TB-004", PASS, "All numeric values parsed cleanly at consistent 2-decimal precision.")


def tb_025_period_matches_engagement(table: ParsedTBTable, engagement_period: str | None = None) -> dict:
    if not engagement_period:
        return result("TB-025", WARNING, "No engagement_period supplied to check against — skipped.")
    detected = (table.period_label or "").strip().lower()
    expected = engagement_period.strip().lower()
    if not detected:
        return result("TB-025", HALT,
                      f"No period could be detected in the table to compare against the "
                      f"engagement period '{engagement_period}'.")
    if expected not in detected and detected not in expected:
        return result("TB-025", HALT,
                      f"Detected period '{table.period_label}' does not match the engagement "
                      f"period '{engagement_period}'.",
                      detected=table.period_label, expected=engagement_period)
    return result("TB-025", PASS, f"Detected period '{table.period_label}' matches engagement period.")


def tb_026_currency_scale(table: ParsedTBTable, target_currency: str | None = None) -> dict:
    if not table.roles_resolved.get("currency"):
        return result("TB-026", WARNING, "Currency column not resolved; cannot check — skipped.")
    currencies = {(r.currency or "").strip().upper() for r in table.rows if r.currency}
    reasons = []
    if target_currency and currencies:
        offending = currencies - {target_currency.strip().upper()}
        if offending:
            return result("TB-026", HALT,
                          f"Non-target currency/currencies found: {', '.join(sorted(offending))}. "
                          "No currency conversion is performed by this check.",
                          offending_currencies=sorted(offending))
    # scale-outlier heuristic: a closing balance ~100x its neighbours' median magnitude
    magnitudes = [abs(r.closing_balance) for r in table.rows if r.closing_balance]
    if len(magnitudes) >= 5:
        sorted_mags = sorted(magnitudes)
        median = sorted_mags[len(sorted_mags) // 2] or 1.0
        outliers = [m for m in magnitudes if median > 0 and m > median * 100]
        if outliers:
            reasons.append(f"{len(outliers)} value(s) are >100x the median magnitude — "
                           "possible scale mismatch (e.g. absolute vs. thousands/lakh/crore).")
    if reasons:
        return result("TB-026", WARNING, " ".join(reasons))
    return result("TB-026", PASS, "Currency consistent with target; no scale outliers detected.")


def tb_029_source_trail(table: ParsedTBTable) -> dict:
    if not table.filename:
        return result("TB-029", WARNING, "No source filename/extraction trail identified for this table.")
    return result("TB-029", PASS, f"Source identified: {table.filename}.")


# --- D. GL MASTER DATA -------------------------------------------------------

def tb_013_one_group_per_code(table: ParsedTBTable) -> dict:
    if not table.roles_resolved.get("group"):
        return result("TB-013", WARNING, "Group column not resolved; cannot check — skipped.")
    by_code: dict[str, set[str]] = {}
    for row in table.rows:
        if row.ledger_code and row.group:
            by_code.setdefault(row.ledger_code, set()).add(row.group)
    offenders = {code: sorted(groups) for code, groups in by_code.items() if len(groups) > 1}
    if offenders:
        return result("TB-013", HALT,
                      f"{len(offenders)} GL code(s) map to more than one Group.",
                      offenders=offenders)
    return result("TB-013", PASS, "Every GL code maps to exactly one Group.")


def tb_014_group_in_master_list(table: ParsedTBTable,
                                approved_group_master: list[str] | None = None) -> dict:
    if not approved_group_master:
        return result("TB-014", WARNING, "No approved Group master list supplied — skipped.")
    if not table.roles_resolved.get("group"):
        return result("TB-014", WARNING, "Group column not resolved; cannot check — skipped.")
    master = {g.strip().lower() for g in approved_group_master}
    offenders = sorted({row.group for row in table.rows
                        if row.group and row.group.strip().lower() not in master})
    if offenders:
        return result("TB-014", HALT,
                      f"{len(offenders)} Group value(s) not in the approved master list: "
                      f"{', '.join(offenders[:10])}.", offending_groups=offenders)
    return result("TB-014", PASS, "All Group values are in the approved master list.")


def tb_030_fuzzy_duplicate_descriptions(table: ParsedTBTable) -> dict:
    return result("TB-030", SKIPPED,
                  "Fuzzy-duplicate GL description detection deferred to the LLM-assisted "
                  "pass — no fuzzy-matching library is a project dependency (pure-code "
                  "implementation would require adding one).")
