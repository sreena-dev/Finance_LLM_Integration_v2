"""Layer 1 — category H (ANOMALY DETECTION, all LLM-assisted judgment calls —
deterministic data-prep implemented here) and category J (PRESENTATION).

TB-021 (Benford), TB-022 (stub), TB-023 (round numbers), TB-033 (stale year)
TB-027 (static value vs live formula — reuses Layer 4's exact detection)
"""

from __future__ import annotations

import re
from collections import Counter

from yukta_rag.tb_validation.tbv_config import (BENFORD_EXPECTED_PCT,
                                                BENFORD_MAX_DEVIATION_PP,
                                                BENFORD_MIN_SAMPLE_SIZE,
                                                ROUND_NUMBER_MIN_ABS,
                                                ROUND_NUMBER_PCT_THRESHOLD,
                                                STALE_YEAR_GAP)
from yukta_rag.tb_validation.tbv_ingest import ParsedTBTable
from yukta_rag.tb_validation.tbv_types import PASS, SKIPPED, WARNING, result

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")  # matches "FY2015" too (letters, not digits, may abut)


# --- H. ANOMALY DETECTION -----------------------------------------------------

def tb_021_benford(table: ParsedTBTable) -> dict:
    magnitudes = [abs(r.closing_balance) for r in table.rows
                 if r.closing_balance is not None and abs(r.closing_balance) >= 1]
    if len(magnitudes) < BENFORD_MIN_SAMPLE_SIZE:
        return result("TB-021", SKIPPED,
                      f"Only {len(magnitudes)} usable balance(s); need >= "
                      f"{BENFORD_MIN_SAMPLE_SIZE} for a meaningful Benford's Law test.")
    leading_digits = [int(str(int(m))[0]) for m in magnitudes if str(int(m))[0] != "0"]
    n = len(leading_digits)
    counts = Counter(leading_digits)
    observed_pct = {d: round(counts.get(d, 0) / n * 100, 2) for d in range(1, 10)}
    deviations = {d: round(abs(observed_pct[d] - BENFORD_EXPECTED_PCT[d]), 2) for d in range(1, 10)}
    max_dev_digit = max(deviations, key=deviations.get)
    max_dev = deviations[max_dev_digit]
    details = {"n": n, "observed_pct": observed_pct, "expected_pct": BENFORD_EXPECTED_PCT,
              "deviations_pp": deviations}
    if max_dev > BENFORD_MAX_DEVIATION_PP:
        return result("TB-021", WARNING,
                      f"Leading-digit distribution deviates from Benford's Law by "
                      f"{max_dev} percentage points on digit {max_dev_digit} (n={n}). "
                      "This is a mechanical deviation flag only — materiality/significance "
                      "is a judgment call for the LLM-assisted pass, not asserted here.",
                      **details)
    return result("TB-021", PASS,
                  f"Leading-digit distribution is broadly consistent with Benford's Law (n={n}).",
                  **details)


def tb_022_gl_desc_group_mismatch(table: ParsedTBTable) -> dict:
    return result("TB-022", SKIPPED,
                  "GL description vs Group semantic-mismatch detection requires natural-"
                  "language judgment and is deferred to the LLM-assisted pass.")


def tb_023_round_number_entries(table: ParsedTBTable) -> dict:
    eligible = [r for r in table.rows if r.closing_balance and abs(r.closing_balance) >= ROUND_NUMBER_MIN_ABS]
    if not eligible:
        return result("TB-023", SKIPPED, "No eligible (non-trivial) closing balances to test.")
    round_rows = [r for r in eligible if abs(r.closing_balance) % 1000 == 0]
    pct = len(round_rows) / len(eligible) * 100
    details = {"n_eligible": len(eligible), "n_round": len(round_rows), "pct_round": round(pct, 2)}
    if pct > ROUND_NUMBER_PCT_THRESHOLD:
        return result("TB-023", WARNING,
                      f"{pct:.1f}% of eligible closing balances are exact round figures "
                      f"(threshold {ROUND_NUMBER_PCT_THRESHOLD}%) — a pattern associated with "
                      "estimates or manually parked balances.", **details)
    return result("TB-023", PASS, f"Round-figure entries ({pct:.1f}%) within the normal range.", **details)


def tb_033_stale_year_reference(table: ParsedTBTable, tb_period_year: int | None = None) -> dict:
    if tb_period_year is None:
        return result("TB-033", WARNING, "No TB period year supplied to compare against — skipped.")
    flagged = []
    for row in table.rows:
        if not row.ledger_name:
            continue
        for match in _YEAR_RE.finditer(row.ledger_name):
            year = int(match.group())
            if abs(tb_period_year - year) > STALE_YEAR_GAP:
                flagged.append({"row": row.row_number, "code": row.ledger_code,
                                "name": row.ledger_name, "year_found": year,
                                "tb_period_year": tb_period_year, "gap": abs(tb_period_year - year)})
    if flagged:
        return result("TB-033", WARNING,
                      f"{len(flagged)} GL description(s) reference a year more than "
                      f"{STALE_YEAR_GAP} years from the TB period ({tb_period_year}).",
                      flagged=flagged)
    return result("TB-033", PASS, "No stale year references found in GL descriptions.")


# --- J. PRESENTATION ----------------------------------------------------------

def tb_027_static_vs_formula(table: ParsedTBTable, flags: list[dict] | None) -> dict:
    """Consumes the SAME formula-flag list Layer 4 computes (tbv_formula.
    scan_formulas(), called once by the pipeline and passed in here) — same
    detection, reported at Warning severity instead of Info. Never
    re-scans the workbook a second time."""
    if flags is None:
        return result("TB-027", WARNING,
                      "Raw file bytes not available (doc_id/convenience ingestion mode) — "
                      "formula-cell detection requires the original workbook, skipped.")
    if flags:
        return result("TB-027", WARNING,
                      f"{len(flags)} cell(s) contain a live formula rather than a static value.",
                      flags=flags)
    return result("TB-027", PASS, "No live-formula cells found in the checked columns.")
