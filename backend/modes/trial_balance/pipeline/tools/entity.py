import datetime
import json
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

VALID_CONTEXTS = (
    "statutory_audit",
    "CAG_supplementary_audit",
    "financial_attest_audit",
    "internal_analytics",
    "unknown",
)

CONTEXT_MISSING_STATEMENT = (
    "Engagement context has not been provided. Observations are limited to preliminary "
    "trial-balance analytics and should be refined after confirming whether the review is "
    "a statutory audit, C&AG supplementary audit, financial attest audit or internal analytics."
)

_IND_AS_MARKERS__build_engagement_context = (
    "right of use", "right-of-use", "rou asset", "lease liability",
    "other comprehensive income", "oci", "expected credit loss", "ecl",
    "fair value", "fvoci", "fvtpl", "amortised cost", "amortized cost",
    "ind as", "financial asset", "financial liabilit",
)

_AS_MARKERS__build_engagement_context = (
    "tangible asset", "shareholders' funds", "shareholders funds",
    "extraordinary item", "accounting standard", "miscellaneous expenditure",
    "deferred revenue expenditure",
)

_PUBLIC_FUNDING_MARKERS = (
    "grant-in-aid", "grant in aid", "budgetary support", "viability gap",
    "subsidy", "government grant", "capital grant", "plan fund", "non-plan",
)

def _scan(tb_df, markers) -> list:
    """Distinct markers present anywhere in the TB's account or grouping text."""
    if tb_df is None or tb_df.is_empty():
        return []
    cols = [c for c in ("gl_name", "main_head", "sub_head_1", "sub_head_2") if c in tb_df.columns]
    blob = " ".join(
        " ".join(str(r.get(c) or "") for c in cols).lower()
        for r in tb_df.iter_rows(named=True)
    )
    return [m for m in markers if m in blob]

@pipeline_tool("build_engagement_context", domain="entity")
def build_engagement_context(
    canonical_tb_file: str,
    engagement_context: str = None,
    accounting_framework: str = None,
    entity_name: str = None,
    period_end: str = None,
    currency: str = None,
    scale: str = None,
    consolidation_basis: str = None,
    source_system: str = None,
    caro_applicable: bool = None,
    government_company: bool = None,
    output_dir: str = None,
    **kwargs,
) -> dict:
    """Record the engagement context, reporting framework, currency, scale, period and
    consolidation basis for this run, emitting the specification's mandatory statement
    when context was not supplied. Infers AS vs Ind AS from the trial balance's own
    account names and reports it as inferred_to_confirm, never as settled. Flags
    whether the CARO and public-sector lenses are gated on unconfirmed applicability.
    Writes engagement_context.json.

    Every parameter is optional: supplied values are recorded as `supplied`, absent
    ones as `unknown` or `inferred_to_confirm`, and nothing is assumed."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")
    tb_df = load_canonical_tb(tb_path)

    warnings = []

    # ── engagement context ────────────────────────────────────────────────────
    context = (engagement_context or "").strip() or "unknown"
    if context not in VALID_CONTEXTS:
        warnings.append(
            f"engagement_context {context!r} is not one of {VALID_CONTEXTS}; recorded as 'unknown'."
        )
        context = "unknown"
    context_statement = CONTEXT_MISSING_STATEMENT if context == "unknown" else None
    if context == "unknown":
        warnings.append(
            "Engagement context not supplied -- audit-context conclusions are provisional "
            "and the specification's mandatory statement has been recorded (sec 2.1)."
        )

    # ── framework ─────────────────────────────────────────────────────────────
    ind_as_hits = _scan(tb_df, _IND_AS_MARKERS__build_engagement_context)
    as_hits = _scan(tb_df, _AS_MARKERS__build_engagement_context)
    if accounting_framework:
        framework, framework_basis = accounting_framework.strip(), "supplied"
    elif ind_as_hits or as_hits:
        framework = "Ind AS" if len(ind_as_hits) >= len(as_hits) else "AS"
        framework_basis = "inferred_to_confirm"
        warnings.append(
            f"Accounting framework inferred as {framework} from account-name markers and must "
            "be CONFIRMED by the audit team before any recognition or measurement conclusion "
            "is drawn (sec 1.2)."
        )
    else:
        framework, framework_basis = "unknown", "unknown"
        warnings.append("No framework markers found in the TB and none supplied -- framework unknown.")

    # ── currency, scale, period ───────────────────────────────────────────────
    if not currency:
        warnings.append("Currency not supplied -- amounts are interpreted as-is and never rescaled (sec 4.1).")
    if not scale:
        warnings.append(
            "Scale/unit not supplied -- figures may be out by a factor of 1,000 or more, which "
            "distorts materiality and risk ranking. Confirm before relying on any threshold (sec 3.2)."
        )

    # ── applicability gates ───────────────────────────────────────────────────
    public_markers = _scan(tb_df, _PUBLIC_FUNDING_MARKERS)
    gates = {
        "caro": {
            "applicable": caro_applicable,
            "basis": "supplied" if caro_applicable is not None else "unconfirmed",
            "note": (
                "CARO applicability is not derivable from a trial balance. Until confirmed, "
                "CARO indicators are information requests, not reporting matters (sec 10.2)."
            ),
        },
        "public_sector_lens": {
            "applicable": government_company,
            "basis": "supplied" if government_company is not None else "unconfirmed",
            "trial_balance_indicators": public_markers,
            "note": (
                "Public-funding heads present in the TB indicate the public-sector lens MAY be "
                "relevant. They do NOT establish Government-company status, which no trial "
                "balance can show (sec 1.2)."
                if public_markers else
                "No public-funding heads detected. This does not rule out public-sector status."
            ),
        },
    }
    if caro_applicable is None:
        warnings.append("CARO applicability unconfirmed -- CARO indicators will be gated as information requests.")
    if government_company is None and public_markers:
        warnings.append(
            f"Public-funding heads detected ({', '.join(public_markers[:3])}) but Government-company "
            "status was not supplied -- confirm whether the public-sector regularity lens applies."
        )

    payload = {
        "entity": entity_name,
        "engagement_context": context,
        "engagement_context_basis": "supplied" if engagement_context else "not_supplied",
        "engagement_context_statement": context_statement,
        "framework": framework,
        "framework_basis": framework_basis,
        "framework_evidence": {
            "ind_as_markers_found": ind_as_hits,
            "as_markers_found": as_hits,
            "note": (
                "Markers are scanned across gl_name, main_head, sub_head_1 and sub_head_2 -- the "
                "framework signal often sits in grouping text rather than the ledger name itself. "
                "This is an inference from vocabulary, not a determination."
            ),
        },
        "period_end": period_end,
        "currency": currency or "unknown",
        "scale": scale or "unknown",
        "consolidation_basis": consolidation_basis or "unknown",
        "source_system": source_system or "unknown",
        "applicability_gates": gates,
        "generated_at": datetime.datetime.now().isoformat(),
        "limitations": [
            "The entity name is an output label only and is never used as a source of financial facts (sec 1.2).",
            "Framework, CARO applicability and Government-company status cannot be concluded from a trial balance.",
        ],
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "engagement_context.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "WARNING" if warnings else "SUCCESS",
        "can_continue": True,
        "message": (
            f"Engagement context: {context} ({payload['engagement_context_basis']}); "
            f"framework {framework} ({framework_basis}); "
            f"CARO {gates['caro']['basis']}; public-sector lens {gates['public_sector_lens']['basis']}."
        ),
        "artifacts": [str(out_path.resolve())],
        "errors": [],
        "warnings": warnings,
        "data": {"engagement_context": context, "framework": framework, "gates": gates},
    }


_OPERATING_REVENUE_RATIO = 0.01

_FCY_BALANCE_RATIO = 0.001

_NON_CURRENT_ASSETS_RATIO_FOR_HOLDING = 0.30

@pipeline_tool("build_entity_profile", domain="entity")
def build_entity_profile(canonical_tb_file: str, output_dir: str = None, **kwargs) -> dict:
    """Classifies the entity's business shape from the canonical TB itself -- sets
    has_operating_revenue / has_foreign_ops / is_holding_vehicle flags consumed by
    build_materiality.py's basis selector, build_sensitive_detector.py's related-party
    register, and build_fx_exposure.py's gate. Writes entity_profile.json."""
    errors = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "canonical_tb.parquet")

    df = load_canonical_tb(tb_path)
    if df is None or df.is_empty():
        errors.append({"type": "EmptyDataError", "message": "canonical_tb.parquet loaded but is empty."})
        return {"execution_status": "FAILED", "errors": errors, "message": "Canonical TB is empty."}

    total_assets = abs(float(df.filter(pl.col("report_head") == "Assets")["closing_balance"].sum() or 0.0))
    assets_basis = total_assets if total_assets > 0 else 1.0

    main_head_lower = pl.col("main_head").cast(pl.Utf8).str.to_lowercase().fill_null("")
    op_revenue = abs(float(
        df.filter(main_head_lower.str.contains("revenue from operations", literal=True))["closing_balance"].sum() or 0.0
    ))
    other_income = abs(float(
        df.filter(main_head_lower.str.contains("other income", literal=True))["closing_balance"].sum() or 0.0
    ))
    op_revenue_ratio = op_revenue / assets_basis
    has_operating_revenue = op_revenue_ratio > _OPERATING_REVENUE_RATIO

    # The FCY signal can live in the grouping workbook's FSLI text (sub_head_1/sub_head_2,
    # e.g. an "Exchange rate fluctuation" FSLI bucket) rather than the individual gl_name --
    # live testing against a real TB confirmed gl_name-only matching missed a genuine FCY
    # account whose own name didn't mention currency at all. Scan all three.
    gl_name_lower = pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().fill_null("")
    sub_head_1_lower = pl.col("sub_head_1").cast(pl.Utf8).str.to_lowercase().fill_null("") if "sub_head_1" in df.columns else pl.lit("")
    sub_head_2_lower = pl.col("sub_head_2").cast(pl.Utf8).str.to_lowercase().fill_null("") if "sub_head_2" in df.columns else pl.lit("")
    fcy_mask = pl.lit(False)
    for kw in FCY_KEYWORDS:
        fcy_mask = (
            fcy_mask
            | gl_name_lower.str.contains(kw, literal=True)
            | sub_head_1_lower.str.contains(kw, literal=True)
            | sub_head_2_lower.str.contains(kw, literal=True)
        )
    fcy_balance = abs(float(df.filter(fcy_mask)["closing_balance"].sum() or 0.0))
    fcy_ratio = fcy_balance / assets_basis
    has_foreign_ops = fcy_ratio > _FCY_BALANCE_RATIO

    # Real-data check against a live Ind AS Schedule III-taxonomy TB showed this pipeline's
    # main_head labels are "Non-current assets"/"Current assets" (Schedule III groupings),
    # not "Financial assets" -- matching on the actual taxonomy in use instead of a label
    # that doesn't appear in it. Long-term-asset-dominated balance sheet + no operating
    # revenue is the holding/investment-vehicle pattern this flag is meant to catch.
    non_current_assets = abs(float(
        df.filter(main_head_lower.str.contains("non-current asset", literal=True) | main_head_lower.str.contains("non current asset", literal=True))["closing_balance"].sum() or 0.0
    ))
    non_current_assets_ratio = non_current_assets / assets_basis
    is_holding_vehicle = (not has_operating_revenue) and non_current_assets_ratio > _NON_CURRENT_ASSETS_RATIO_FOR_HOLDING

    profile = {
        "flags": {
            "has_operating_revenue": has_operating_revenue,
            "has_foreign_ops": has_foreign_ops,
            "is_holding_vehicle": is_holding_vehicle,
        },
        "signals": {
            "total_assets": total_assets,
            "revenue_from_operations": op_revenue,
            "revenue_from_operations_ratio_to_assets": round(op_revenue_ratio, 4),
            "other_income": other_income,
            "foreign_currency_tagged_balance": fcy_balance,
            "foreign_currency_ratio_to_assets": round(fcy_ratio, 4),
            "non_current_assets": non_current_assets,
            "non_current_assets_ratio_to_assets": round(non_current_assets_ratio, 4),
        },
        "methodology": (
            "Deterministic, data-driven classification from the canonical TB's own main_head "
            "composition -- no client-supplied entity-type field exists anywhere in this "
            "pipeline's inputs. Thresholds are pre-materiality heuristics (this stage runs "
            "before materiality.json exists), not materiality-precise cutoffs."
        ),
        "generated_at": datetime.datetime.now().isoformat(),
    }

    out_file = out_dir / "entity_profile.json"
    write_json_atomic(profile, out_file, indent=4)
    artifacts.append(str(out_file.resolve()))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": (
            f"Entity profile: has_operating_revenue={has_operating_revenue}, "
            f"has_foreign_ops={has_foreign_ops}, is_holding_vehicle={is_holding_vehicle}."
        ),
        "artifacts": artifacts,
        "errors": errors,
        "data": profile,
    }




