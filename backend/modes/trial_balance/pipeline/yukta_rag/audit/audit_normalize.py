"""Input-quality note and data-sufficiency grading for Audit mode (spec 3, 14).

Produces the ``input_quality`` block the audit output must lead with: source
system, period, currency, scale, balance status, comparative/movement presence,
the opening + movement = closing validation, mapping-confidence summary,
control/unmapped accounts, data-quality flags and an overall data-sufficiency
grade. Nothing is invented; unknowns are reported as unknown (spec forbids silent
rescaling or assumed metadata).
"""

from __future__ import annotations

from yukta_rag.audit.audit_config import (UNMAPPED_PCT_FLAG, UNMAPPED_PCT_HIGH_GRADE,
                                          UNMAPPED_PCT_LOW_GRADE,
                                          VALUE_COVERAGE_WARN_THRESHOLD)
from yukta_rag.audit.audit_mapping import map_accounts
from yukta_rag.audit.audit_screen import contra_pair_ids
from yukta_rag.trial_balance.tb_tools import _coa_scheme_detected, compute_tie_out

# below this, classification coverage (by ledger VALUE, not account count) is
# considered too thin to trust the financial snapshot / ratio tables computed
# from it — a few huge unclassified accounts distort a total far more than many
# small ones, which a simple account-count percentage would miss entirely.
_VALUE_COVERAGE_WARN_THRESHOLD = VALUE_COVERAGE_WARN_THRESHOLD

# spec INP-03: keyword-only signal that the TB's ledger names indicate Ind AS
# treatment (never a certification of which framework applies — just what the
# account names themselves suggest).
_FRAMEWORK_KEYWORDS = (
    "right-of-use", "right of use", "rou asset", "lease liability",
    "other comprehensive income", " oci ", "expected credit loss", " ecl ",
    "fair value", "amortised cost", "amortized cost",
)


def _detect_framework(tb: dict) -> str:
    names = " " + " ".join(a["name"].lower() for a in tb["accounts"]) + " "
    hits = sorted({kw.strip() for kw in _FRAMEWORK_KEYWORDS if kw in names})
    if hits:
        return f"Ind AS indicators present ({', '.join(hits[:3])})"
    return "framework not determinable from TB"


def _infer_source_system(tb: dict) -> str:
    cols = " ".join(str(v) for v in tb.get("parse_report", {}).get("columns", {}).values() if v).lower()
    sheet = (tb.get("sheet") or "").lower()
    if any(k in cols for k in ("g/l acct", "cocd", "short text")) or "sap" in sheet:
        return "SAP (inferred from column layout)"
    if "tally" in sheet or "tally" in cols:
        return "Tally (inferred)"
    if "oracle" in sheet:
        return "Oracle (inferred)"
    if _coa_scheme_detected(tb):
        return "SAP-style numeric GL code scheme (inferred from GL code pattern)"
    return "unknown"


def opening_movement_check(tb: dict, tol_rel: float = 1e-4) -> dict | None:
    """Validate opening + movement = closing per account, where both are present."""
    pr = tb.get("parse_report", {})
    if not (pr.get("has_opening") and pr.get("has_movement")):
        return None
    checked = mismatches = 0
    examples = []
    for a in tb["accounts"]:
        if "opening_net" in a and "movement_net" in a:
            checked += 1
            closing = float(a.get("p1_net", 0.0) or 0.0)
            expected = a["opening_net"] + a["movement_net"]
            tol = max(1.0, abs(closing) * tol_rel)
            if abs(expected - closing) > tol:
                mismatches += 1
                if len(examples) < 5:
                    examples.append({"account": a["name"], "opening": a["opening_net"],
                                     "movement": a["movement_net"], "closing": round(closing, 2),
                                     "expected": round(expected, 2)})
    return {"checked": checked, "mismatches": mismatches, "examples": examples}


def value_coverage(mapping: dict) -> dict:
    """Classification coverage weighted by account VALUE, not count.

    Confirmed contra/rollup pairs (net ~0 by design, e.g. INC/OTG mirror legs or
    IND-AS transition entries) are excluded from both sides of the ratio: a
    correctly-paired contra doesn't reduce reliability, but a stray unpaired leg
    does and should still count against coverage.
    """
    accounts = mapping["accounts"]
    excluded = contra_pair_ids(accounts)
    total_abs = classified_abs = 0.0
    for a in accounts:
        if id(a) in excluded:
            continue
        net = abs(a.get("net", 0.0) or 0.0)
        total_abs += net
        if a.get("category"):
            classified_abs += net
    pct = round(classified_abs / total_abs * 100, 1) if total_abs else 100.0
    return {"value_coverage_pct": pct, "n_contra_pairs_excluded": len(excluded) // 2,
            "classified_abs_value": round(classified_abs, 2), "total_abs_value": round(total_abs, 2)}


def input_quality_note(tb: dict, mapping: dict | None = None,
                       metadata: dict | None = None) -> dict:
    """Build the spec ``input_quality`` block + data-sufficiency grade."""
    pr = tb.get("parse_report", {})
    metadata = metadata or {}
    mapping = mapping or map_accounts(tb)
    n = len(tb["accounts"]) or 1

    tie = compute_tie_out(tb)["per_period"][0]           # period 1 (closing)
    unmapped = mapping["unmapped_accounts"]
    low_conf = mapping["mapping_confidence_summary"].get("low", 0)
    unmapped_pct = round((len(unmapped) + low_conf) / n * 100, 1)
    control = sorted({a["name"] for a in mapping["accounts"]
                      if "suspense_control" in a["sensitive_tags"]})
    om = opening_movement_check(tb)
    cov = value_coverage(mapping)
    low_value_coverage = cov["value_coverage_pct"] < _VALUE_COVERAGE_WARN_THRESHOLD

    flags = []
    if pr.get("scale", "unknown") == "unknown":
        flags.append("Scale (units/thousands/lakh/crore) is not stated in the file; figures are "
                     "NOT rescaled. Confirm the reporting scale before relying on magnitudes.")
    if unmapped_pct >= UNMAPPED_PCT_FLAG:
        flags.append(f"{unmapped_pct}% of accounts are unmapped or low-confidence; request the "
                     "chart of accounts / management FSLI mapping.")
    if low_value_coverage:
        flags.append(f"Only {cov['value_coverage_pct']}% of total ledger VALUE is classified "
                     f"(by absolute balance, {cov['n_contra_pairs_excluded']} contra/rollup "
                     "pair(s) excluded as immaterial to this) — the Financial Snapshot and "
                     "Ratios-per-period figures below may not reflect true magnitudes. Do not "
                     "use them for ratio/trend analysis until chart-of-accounts mapping improves.")
    if not tie["balanced"]:
        flags.append(f"Trial balance does not tie out; residual {tie['difference']:,.2f}. "
                     "Request a corrected trial balance before reliance.")
    if control:
        flags.append(f"{len(control)} suspense/control account(s) present; balances lie outside "
                     "the trial balance and need reconciliation.")
    if om and om["mismatches"]:
        flags.append(f"Opening + movement does not equal closing for {om['mismatches']} of "
                     f"{om['checked']} accounts; verify movement columns / period scope.")
    if pr.get("n_periods", 1) < 2 and not pr.get("has_movement"):
        flags.append("No comparative period or movement columns — the Variance section below "
                     "will be omitted, and current/quick ratios in Ratios per period may be null. "
                     "Request the prior-year trial balance.")

    # data-sufficiency grade (spec 3.2)
    if not tie["balanced"]:
        sufficiency = "information_request"
    elif unmapped_pct >= UNMAPPED_PCT_LOW_GRADE or low_value_coverage:
        sufficiency = "low"
    elif (pr.get("scale", "unknown") != "unknown" and unmapped_pct < UNMAPPED_PCT_HIGH_GRADE
          and (pr.get("n_periods", 1) >= 2 or pr.get("has_movement"))):
        sufficiency = "high"
    else:
        sufficiency = "medium"

    raw_currency = pr.get("currency")
    currency_stated = bool(raw_currency) and raw_currency != "unknown"
    return {
        "source_system": metadata.get("source_system") or _infer_source_system(tb),
        "period_end": metadata.get("period_end") or (tb.get("periods") or ["unknown"])[0],
        "currency": raw_currency if currency_stated else "INR",
        "currency_assumed": not currency_stated,
        "scale": pr.get("scale", "unknown"),
        "balanced": tie["balanced"],
        "trial_balance_residual": tie["difference"],
        "comparative_present": pr.get("n_periods", 1) >= 2,
        "movement_columns_present": bool(pr.get("has_movement")),
        "opening_present": bool(pr.get("has_opening")),
        "opening_movement_check": om,
        "n_accounts": len(tb["accounts"]),
        "mapping_method": mapping.get("method"),
        "mapping_confidence_summary": mapping["mapping_confidence_summary"],
        "unmapped_accounts": unmapped[:50],
        "n_unmapped": len(unmapped),
        "control_accounts": control[:50],
        "data_quality_flags": flags,
        "data_sufficiency": sufficiency,
        "value_coverage_pct": cov["value_coverage_pct"],
        "n_contra_pairs_excluded": cov["n_contra_pairs_excluded"],
        "low_value_coverage": low_value_coverage,
        "engagement_context": metadata.get("engagement_context") or "unknown",
        "framework": metadata.get("framework") or _detect_framework(tb),
    }
