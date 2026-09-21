import datetime
import json
import statistics
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

PBT_MATERIALITY_PCT = 5.0

REVENUE_MATERIALITY_PCT = 1.0

ASSETS_MATERIALITY_PCT = 1.0

EXPENSES_MATERIALITY_PCT = 1.0

PBT_STABILITY_MIN_REVENUE_PCT = 0.02

PERFORMANCE_MATERIALITY_FRACTION = 0.75

TRIVIAL_THRESHOLD_FRACTION = 0.05

PRIORITY_CRITICAL_RATIO = 100.0

PRIORITY_HIGH_RATIO = 20.0

PRIORITY_MEDIUM_RATIO = 5.0

PRIORITY_LOW_RATIO = 0.75

DIST_TRIVIAL_TO_PERFORMANCE_RATIO = 0.75

DIST_PERFORMANCE_TO_OVERALL_RATIO = 1.0

DIST_ABOVE_10X_RATIO = 10.0

DIST_ABOVE_100X_RATIO = 100.0

COVERAGE_HIGH_PCT = 95.0

COVERAGE_MEDIUM_PCT = 85.0

def _build_materiality_candidates(revenue, expenses, assets, pbt, has_operating_revenue):
    """Builds the 4-candidate benchmark waterfall (PBT -> Revenue -> Assets -> Expenses),
    each gated on the previous one being invalid, and scores/selects among them. Pure
    function of the four financial-snapshot totals plus the has_operating_revenue gate."""
    candidates = []
    is_pbt_valid = pbt > 0 and revenue > 0 and pbt > (PBT_STABILITY_MIN_REVENUE_PCT * revenue) and has_operating_revenue
    candidates.append({
        "benchmark": "Profit Before Tax",
        "base_amount": round(pbt, 2),
        "hypothetical_materiality": round(abs(pbt) * PBT_MATERIALITY_PCT / 100, 2),
        "applicability": "Standard for for-profit entities",
        "available": True,
        "positive": pbt > 0,
        "stable": pbt > (PBT_STABILITY_MIN_REVENUE_PCT * revenue) if revenue > 0 else False,
        "recommended": is_pbt_valid,
        "selection_score": 100 if is_pbt_valid else 0,
        "reason": (
            "Profit Before Tax is valid and stable." if is_pbt_valid
            else "Entity has no material revenue from operations (holding/investment vehicle)." if not has_operating_revenue
            else "Negative, zero, or highly volatile."
        ),
        "confidence": "High" if is_pbt_valid else "Low",
        "pct": PBT_MATERIALITY_PCT,
        "value": pbt,
    })

    is_rev_valid = revenue > 0 and not is_pbt_valid and has_operating_revenue
    candidates.append({
        "benchmark": "Total Revenue",
        "base_amount": round(revenue, 2),
        "hypothetical_materiality": round(revenue * REVENUE_MATERIALITY_PCT / 100, 2),
        "applicability": "Standard for loss-making or low-margin entities",
        "available": True,
        "positive": revenue > 0,
        "stable": revenue > 0,
        "recommended": is_rev_valid,
        "selection_score": 80 if is_rev_valid else 20,
        "reason": (
            "Revenue is the primary driver of operations." if is_rev_valid
            else "Entity has no material revenue from operations (holding/investment vehicle) -- Total Revenue here is dominated by non-operating income, not a reliable planning basis." if not has_operating_revenue
            else ("Preferred over PBT due to volatility." if not is_pbt_valid else "PBT is preferred.")
        ),
        "confidence": "High" if is_rev_valid else "Medium",
        "pct": REVENUE_MATERIALITY_PCT,
        "value": revenue,
    })

    is_asset_valid = assets > 0 and not is_rev_valid and not is_pbt_valid
    candidates.append({
        "benchmark": "Total Assets",
        "base_amount": round(assets, 2),
        "hypothetical_materiality": round(assets * ASSETS_MATERIALITY_PCT / 100, 2),
        "applicability": "Asset-heavy or dormant entities",
        "available": True,
        "positive": assets > 0,
        "stable": True,
        "recommended": is_asset_valid,
        "selection_score": 60 if is_asset_valid else 10,
        "reason": "Entity has no operations, defaulting to assets." if is_asset_valid else "Operations (Revenue/PBT) take precedence.",
        "confidence": "Medium",
        "pct": ASSETS_MATERIALITY_PCT,
        "value": assets,
    })

    is_exp_valid = expenses > 0 and not is_asset_valid and not is_rev_valid and not is_pbt_valid
    candidates.append({
        "benchmark": "Total Expenses",
        "base_amount": round(expenses, 2),
        "hypothetical_materiality": round(expenses * EXPENSES_MATERIALITY_PCT / 100, 2),
        "applicability": "Non-profits or empty shell companies",
        "available": True,
        "positive": expenses > 0,
        "stable": True,
        "recommended": is_exp_valid,
        "selection_score": 40 if is_exp_valid else 0,
        "reason": "Fallback benchmark used." if is_exp_valid else "Better benchmarks available.",
        "confidence": "Low",
        "pct": EXPENSES_MATERIALITY_PCT,
        "value": expenses,
    })

    return candidates


def _build_material_hierarchy_nodes(tree_df, performance_materiality, overall_materiality, total_tb_balance, sibling_counts, get_priority):
    """Walks the snapshot-drilldown tree, keeping only nodes at/above performance
    materiality, and splits them into FSLI-level (hierarchy_level<=2) vs sub-FSLI nodes."""
    mat_fslis = []
    mat_sub_fslis = []

    for row in tree_df.iter_rows(named=True):
        abs_bal = abs(row["closing_balance"])
        if abs_bal < performance_materiality:
            continue

        pct_tb = (abs_bal / total_tb_balance * 100) if total_tb_balance > 0 else 0
        pct_om = (abs_bal / overall_materiality * 100) if overall_materiality > 0 else 0

        node = {
            "name": str(row["node_name"]),
            "parent_fsli": str(row.get("parent_node_name", "") or ""),
            "hierarchy_path": str(row["hierarchy_path"]),
            "hierarchy_level": int(row["hierarchy_level"]),
            "balance": round(float(row["closing_balance"]), 2),
            "absolute_balance": round(abs_bal, 2),
            "percentage_of_tb": round(pct_tb, 2),
            "percentage_of_benchmark": round(pct_om, 2),
            "sibling_count": sibling_counts.get(row.get("parent_path", ""), 0),
            "hierarchy_depth": int(row["hierarchy_level"]),
            "descendant_count": int(row.get("descendant_gl_count", 0)),
            "contribution_to_parent": round(float(row.get("percent_of_parent", 0)), 2),
            "priority": get_priority(abs_bal),
        }

        if row["hierarchy_level"] <= 2:
            mat_fslis.append(node)
        else:
            mat_sub_fslis.append(node)

    return mat_fslis, mat_sub_fslis


def _build_material_gl_accounts_and_distribution(tb_df, overall_materiality, performance_materiality, total_tb_balance, get_priority):
    """Classifies every GL row into a materiality-ratio distribution bucket, and separately
    collects the subset at/above performance materiality as the material GL population."""
    mat_gls = []
    dist_buckets = {
        "below_trivial": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
        "trivial_to_performance": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
        "performance_to_overall": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
        "above_overall": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
        "above_10x": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
        "above_100x": {"accounts": 0, "balance": 0.0, "percentage": 0.0},
    }

    for row in tb_df.iter_rows(named=True):
        abs_bal = abs(row.get("closing_balance", 0.0))
        pct_tb = (abs_bal / total_tb_balance * 100) if total_tb_balance > 0 else 0

        if overall_materiality > 0:
            ratio = abs_bal / overall_materiality
            if ratio < TRIVIAL_THRESHOLD_FRACTION:
                b = "below_trivial"
            elif ratio < DIST_TRIVIAL_TO_PERFORMANCE_RATIO:
                b = "trivial_to_performance"
            elif ratio < DIST_PERFORMANCE_TO_OVERALL_RATIO:
                b = "performance_to_overall"
            elif ratio < DIST_ABOVE_10X_RATIO:
                b = "above_overall"
            elif ratio < DIST_ABOVE_100X_RATIO:
                b = "above_10x"
            else:
                b = "above_100x"

            dist_buckets[b]["accounts"] += 1
            dist_buckets[b]["balance"] = round(dist_buckets[b]["balance"] + abs_bal, 2)
            dist_buckets[b]["percentage"] = round(dist_buckets[b]["percentage"] + pct_tb, 2)

        if abs_bal >= performance_materiality:
            mat_gls.append({
                "gl_code": str(row.get("gl_code", "")),
                "gl_name": str(row.get("gl_name", "")),
                "fsli": row_fsli(row),
                "parent_hierarchy": row_fs_head(row),
                "hierarchy_depth": 3,
                "balance": round(float(row.get("closing_balance", 0.0)), 2),
                "absolute_balance": round(abs_bal, 2),
                "percentage_of_tb": round(pct_tb, 2),
                "percentage_of_benchmark": round((abs_bal / overall_materiality * 100), 2) if overall_materiality > 0 else 0,
                "priority": get_priority(abs_bal),
            })

    return mat_gls, dist_buckets


@pipeline_tool("build_materiality", domain="materiality")
def build_materiality(canonical_tb_file: str, metadata_file: str = None, output_dir: str = None) -> dict:
    """Compute overall/performance/trivial materiality and material FSLI/GL populations."""
    errors = []
    warnings = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    fsli_path = out_dir / "fsli_summary.parquet"
    if not fsli_path.exists():
        fsli_path = tb_path.parent / "fsli_summary.parquet"
    snapshot_path = out_dir / "snapshot_drilldown.parquet"
    if not snapshot_path.exists():
        snapshot_path = tb_path.parent / "snapshot_drilldown.parquet"
    stats_path = out_dir / "financial_snapshot_statistics.json"
    if not stats_path.exists():
        stats_path = tb_path.parent / "financial_snapshot_statistics.json"
    mapping_path = out_dir / "mapping_summary.json"
    if not mapping_path.exists():
        mapping_path = tb_path.parent / "mapping_summary.json"
    entity_profile_path = out_dir / "entity_profile.json"
    if not entity_profile_path.exists():
        entity_profile_path = tb_path.parent / "entity_profile.json"

    for p, label in [
        (stats_path, "Financial snapshot statistics (run build_financial_snapshot first)"),
        (fsli_path, "FSLI Summary (run build_fsli_summary first)"),
        (tb_path, "Canonical TB (supply canonical_tb_file from load_tb_from_db/ingest_tb_to_live)"),
        (snapshot_path, "Drilldown Snapshot (run build_financial_snapshot first)"),
    ]:
        if not p.exists():
            raise PipelineFileError(str(p), label)

    with open(stats_path, "r", encoding="utf-8") as f:
        stats = json.load(f)

    revenue = abs(float(stats.get("total_revenue", 0.0)))
    expenses = abs(float(stats.get("total_expenses", 0.0)))
    assets = abs(float(stats.get("total_assets", 0.0)))
    equity = abs(float(stats.get("total_equity", 0.0)))  # noqa: F841 (kept for parity with TB-v1 signature/inputs)
    pbt = revenue - expenses

    # TB-R08: gate PBT/Revenue eligibility on build_entity_profile.py's has_operating_revenue
    # flag -- without this, a no-turnover holding/E&P vehicle whose "Revenue" is entirely
    # Other income (interest/dividend) still won the benchmark waterfall on `revenue > 0`
    # alone, since that check never asked whether the revenue was operationally real.
    # Defaults to True (unchanged legacy behavior) when entity_profile.json wasn't run for
    # this session -- e.g. an upload-path run, or a COMPARISON PY leg.
    entity_flags = {}
    if entity_profile_path.exists():
        try:
            with open(entity_profile_path, "r", encoding="utf-8") as f:
                entity_flags = json.load(f).get("flags", {})
        except Exception:
            entity_flags = {}
    has_operating_revenue = entity_flags.get("has_operating_revenue", True)

    candidates = _build_materiality_candidates(revenue, expenses, assets, pbt, has_operating_revenue)
    chosen = max(candidates, key=lambda x: x["selection_score"])

    overall_materiality = chosen["hypothetical_materiality"]
    performance_materiality = round(overall_materiality * PERFORMANCE_MATERIALITY_FRACTION, 2)
    trivial_threshold = round(overall_materiality * TRIVIAL_THRESHOLD_FRACTION, 2)
    posting_threshold = trivial_threshold

    def get_priority(abs_bal):
        if overall_materiality == 0:
            return "Unknown"
        ratio = abs_bal / overall_materiality
        if ratio > PRIORITY_CRITICAL_RATIO:
            return "Critical"
        if ratio >= PRIORITY_HIGH_RATIO:
            return "High"
        if ratio >= PRIORITY_MEDIUM_RATIO:
            return "Medium"
        if ratio >= PRIORITY_LOW_RATIO:
            return "Low"
        return "None"

    sensitivity = []
    for c in candidates:
        if c["value"] != 0:
            diff = (c["hypothetical_materiality"] - overall_materiality) / overall_materiality * 100 if overall_materiality > 0 else 0
            sensitivity.append({
                "benchmark": c["benchmark"],
                "base_amount": c["base_amount"],
                "hypothetical_materiality": c["hypothetical_materiality"],
                "variation": round(c["hypothetical_materiality"] - overall_materiality, 2),
                "deviation": round(diff, 2),
                "recommendation": c["reason"],
            })

    tree_df = pl.read_parquet(snapshot_path)
    tb_df = pl.read_parquet(tb_path)

    # Rows with no resolvable GL code (e.g. a trailing synthetic grand-total/tie-out row) are
    # retained in canonical_tb.parquet for traceability but are not a real ledger account and
    # must never be summed into total_tb_balance or the mapping-coverage stats below.
    no_gl_code_mask = pl.col("gl_code").is_null() | (pl.col("gl_code").cast(pl.Utf8).str.strip_chars() == "")
    if tb_df.filter(no_gl_code_mask).height:
        tb_df = tb_df.filter(~no_gl_code_mask)

    tb_df = tb_df.with_columns(pl.col("closing_balance").cast(pl.Float64, strict=False).fill_null(0.0))

    # Analysis-stage figures (materiality basis, material GL/concentration population) are
    # computed over mapped rows only. `tb_df` itself stays the full population -- it's still
    # needed below for the validation-stage coverage/mapped_value/unmapped_value split and
    # total_gl_accounts count, which must include unmapped/unmatched rows by design.
    analysis_df = mapped_only(tb_df)
    total_tb_balance = float(analysis_df["closing_balance"].abs().sum())

    sibling_counts = (
        dict(tree_df.group_by("parent_path").agg(pl.len().alias("_n")).iter_rows())
        if "parent_path" in tree_df.columns
        else {}
    )

    mat_fslis, mat_sub_fslis = _build_material_hierarchy_nodes(
        tree_df, performance_materiality, overall_materiality, total_tb_balance, sibling_counts, get_priority
    )

    mat_fslis = sorted(mat_fslis, key=lambda x: x["absolute_balance"], reverse=True)
    mat_sub_fslis = sorted(mat_sub_fslis, key=lambda x: x["absolute_balance"], reverse=True)

    cum_fsli = 0.0
    for n in mat_fslis:
        cum_fsli += n["percentage_of_tb"]
        n["cumulative_contribution"] = round(cum_fsli, 2)

    cum_sub = 0.0
    for n in mat_sub_fslis:
        cum_sub += n["percentage_of_tb"]
        n["cumulative_contribution"] = round(cum_sub, 2)

    mat_gls, dist_buckets = _build_material_gl_accounts_and_distribution(
        analysis_df, overall_materiality, performance_materiality, total_tb_balance, get_priority
    )

    mat_gls = sorted(mat_gls, key=lambda x: x["absolute_balance"], reverse=True)

    cum_gl = 0.0
    for i, n in enumerate(mat_gls):
        n["ranking"] = i + 1
        cum_gl += n["percentage_of_tb"]
        n["cumulative_contribution"] = round(cum_gl, 2)

    sorted_bals = analysis_df["closing_balance"].abs().sort(descending=True)
    top10_sum = float(sorted_bals.head(10).sum()) if len(sorted_bals) >= 10 else float(sorted_bals.sum())
    top20_sum = float(sorted_bals.head(20).sum()) if len(sorted_bals) >= 20 else float(sorted_bals.sum())
    top50_sum = float(sorted_bals.head(50).sum()) if len(sorted_bals) >= 50 else float(sorted_bals.sum())

    concentration = {
        "top10_percent": round((top10_sum / total_tb_balance * 100), 2) if total_tb_balance > 0 else 0,
        "top20_percent": round((top20_sum / total_tb_balance * 100), 2) if total_tb_balance > 0 else 0,
        "top50_percent": round((top50_sum / total_tb_balance * 100), 2) if total_tb_balance > 0 else 0,
        "largest_fsli": mat_fslis[0]["name"] if mat_fslis else "None",
        "largest_sub_fsli": mat_sub_fslis[0]["name"] if mat_sub_fslis else "None",
        "largest_gl": mat_gls[0]["gl_name"] if mat_gls else "None",
    }

    material_balance = sum(g["absolute_balance"] for g in mat_gls)
    planning_metrics = {
        "total_tb_balance": round(total_tb_balance, 2),
        "material_balance": round(material_balance, 2),
        "material_balance_percent": round(material_balance / total_tb_balance * 100, 2) if total_tb_balance > 0 else 0,
        "material_account_count": len(mat_gls),
        "average_material_balance": round(material_balance / len(mat_gls), 2) if mat_gls else 0,
        "median_material_balance": round(statistics.median([g["absolute_balance"] for g in mat_gls]), 2) if mat_gls else 0,
    }

    coverage = {
        "mapped_accounts": 0,
        "mapped_value": 0.0,
        "unmapped_accounts": 0,
        "unmapped_value": 0.0,
        "confidence": "Unknown",
    }
    if mapping_path.exists():
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping_stats = json.load(f)
            coverage["mapped_accounts"] = mapping_stats.get("mapped_rows", 0)
            coverage["unmapped_accounts"] = mapping_stats.get("unmapped_rows", 0)

    if "mapped_status" in tb_df.columns:
        mapped_mask = pl.col("mapped_status") == MAPPED_STATUS_MAPPED
        coverage["unmapped_value"] = round(tb_df.filter(~mapped_mask)["closing_balance"].abs().sum(), 2)
        coverage["mapped_value"] = round(tb_df.filter(mapped_mask)["closing_balance"].abs().sum(), 2)
    else:
        coverage["mapped_value"] = round(tb_df["closing_balance"].abs().sum(), 2)

    total_cov_val = coverage["mapped_value"] + coverage["unmapped_value"]
    val_pct = (coverage["mapped_value"] / total_cov_val * 100) if total_cov_val > 0 else 0

    if val_pct >= COVERAGE_HIGH_PCT:
        coverage["confidence"] = "High"
    elif val_pct >= COVERAGE_MEDIUM_PCT:
        coverage["confidence"] = "Medium"
    else:
        coverage["confidence"] = "Low"

    summary = {
        "total_gl_accounts": len(tb_df),
        "total_material_gl_accounts": len(mat_gls),
        "total_material_fsli": len(mat_fslis),
        "largest_fsli": mat_fslis[0]["name"] if mat_fslis else "None",
        "largest_gl": mat_gls[0]["gl_name"] if mat_gls else "None",
        "benchmark_used": chosen["benchmark"],
        "overall_materiality": overall_materiality,
    }

    planning_summary = {
        "significant_fsli": [x["name"] for x in mat_fslis[:5]],
        "dominant_balance_classes": ["Assets", "Revenue"] if assets > expenses else ["Expenses", "Liabilities"],
        "dominant_sub_fsli": [x["name"] for x in mat_sub_fslis[:3]],
        "recommended_testing": [f"Test {x['name']} explicitly" for x in mat_fslis[:3]],
        "largest_business_areas": [mat_fslis[0]["name"]] if mat_fslis else [],
    }

    metadata = {
        "variance_threshold": trivial_threshold,
        "concentration_threshold": performance_materiality,
        "sampling_threshold": trivial_threshold,
        "significant_balance_threshold": overall_materiality,
        "planning_confidence": coverage["confidence"],
    }

    limitations = []
    if coverage["confidence"] == "Low":
        limitations.append("Low mapping confidence. Materiality calculations include a large unmapped population.")
    if pbt <= 0 and revenue <= 0 and assets <= 0:
        limitations.append("Scale unknown. Entity appears to have no operations, revenues, or assets.")

    write_parquet_atomic(pl.DataFrame(mat_fslis), out_dir / "material_fsli.parquet")
    write_parquet_atomic(pl.DataFrame(mat_sub_fslis), out_dir / "material_sub_fsli.parquet")
    write_parquet_atomic(pl.DataFrame(mat_gls), out_dir / "material_gl_accounts.parquet")
    artifacts.extend([
        str((out_dir / "material_fsli.parquet").resolve()),
        str((out_dir / "material_sub_fsli.parquet").resolve()),
        str((out_dir / "material_gl_accounts.parquet").resolve()),
    ])

    output_data = {
        "methodology": "Deterministic quantitative benchmark selection, sensitivity analysis, and hierarchy traversal.",
        "benchmark_analysis": candidates,
        "benchmark_sensitivity": sensitivity,
        "selected_materiality": {
            "overall_materiality": overall_materiality,
            "base_amount": round(chosen["value"], 2),
            "percentage": chosen["pct"],
        },
        "thresholds": {
            "overall": overall_materiality,
            "performance": performance_materiality,
            "clearly_trivial": trivial_threshold,
            "posting": posting_threshold,
        },
        "summary": summary,
        "planning_metrics": planning_metrics,
        "concentration": concentration,
        "distribution": dist_buckets,
        "planning_summary": planning_summary,
        "coverage": coverage,
        "material_fsli": mat_fslis[:15],
        "material_sub_fsli": mat_sub_fslis[:30],
        "material_gl_accounts": mat_gls[:50],
        "downstream_metadata": metadata,
        "limitations": limitations,
    }

    mat_json_path = out_dir / "materiality.json"
    write_json_atomic(output_data, mat_json_path, indent=4)
    artifacts.append(str(mat_json_path.resolve()))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": f"Quantitative Audit Planning Metadata built. Overall Materiality: {overall_materiality:,.0f}",
        "artifacts": artifacts,
        "errors": errors,
        "warnings": warnings,
    }


def _scale_confirmed(layer1_results) -> tuple:
    """Whether TB-026 (currency and unit declared) passed. Returns (confirmed, note)."""
    if not isinstance(layer1_results, list):
        return None, "Layer-1 results unavailable -- scale confirmation status unknown."
    for rule in layer1_results:
        if rule.get("rule") == "TB-026":
            status = rule.get("status")
            return status == "PASS", rule.get("message", "")
    return None, "TB-026 not present in Layer-1 results."

@pipeline_tool("build_materiality_lens", domain="materiality")
def build_materiality_lens(
    materiality_file: str = None,
    sensitive_accounts_file: str = None,
    layer1_results_file: str = None,
    audit_approved_materiality: float = None,
    output_dir: str = None,
    **kwargs,
) -> dict:
    """Layer materiality by nature and context over the computed quantitative
    materiality: apply sensitive-item floors so grants, write-offs, related-party and
    statutory balances are elevated regardless of value, accept an audit-approved
    materiality that overrides the computed proxy, and mark output provisional when
    currency/scale was never confirmed. Writes materiality_lens.json.

    Does not modify materiality.json -- reads it and adds the nature/context layer."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mat_path = Path(materiality_file) if materiality_file else out_dir / "materiality.json"
    if not mat_path.exists():
        raise PipelineFileError(str(mat_path), "Materiality (run build_materiality first)")
    mat = safe_load_json(mat_path)

    warnings = []
    computed_om = float((mat.get("selected_materiality") or {}).get("overall_materiality") or 0.0)
    thresholds = mat.get("thresholds") or {}
    computed_trivial = float(thresholds.get("clearly_trivial") or 0.0)

    # sec 8.1: auditor-set materiality overrides the computed proxy outright.
    if audit_approved_materiality and audit_approved_materiality > 0:
        effective_om = float(audit_approved_materiality)
        basis = "audit_approved"
        basis_note = (
            "Audit-approved materiality supplied by the engagement team. The computed "
            f"proxy of {computed_om:,.2f} is retained for reference but not applied."
        )
    else:
        effective_om = computed_om
        basis = "computed_proxy"
        basis_note = (
            "No audit-approved materiality supplied. The computed benchmark proxy is used "
            "and is PROVISIONAL -- for prioritisation only (sec 8.1)."
        )
        warnings.append(
            "No audit_approved_materiality supplied -- figures are provisional proxies and "
            "must not be presented as auditor-set materiality."
        )

    # sec 3.2 / sec 4.1: scale must be confirmed before materiality is relied on.
    l1_path = Path(layer1_results_file) if layer1_results_file else out_dir / "layer1_results.json"
    scale_confirmed, scale_note = _scale_confirmed(safe_load_json(l1_path) if l1_path.exists() else None)
    if scale_confirmed is False:
        warnings.append(
            "TB-026 did not pass -- currency/unit is unconfirmed, so every materiality figure "
            "here may be wrong by a factor of 1,000 or more. Confirm scale before ranking risk."
        )

    pack = load_pack("materiality")
    floors_spec = pack["sensitive_floors"]
    default_floor = float(floors_spec["default_floor_fraction"])
    by_tag = {k: v for k, v in floors_spec["by_tag"].items() if not k.startswith("_")}

    sens_path = Path(sensitive_accounts_file) if sensitive_accounts_file else out_dir / "sensitive_accounts.json"
    sens = safe_load_json(sens_path) if sens_path.exists() else {}
    sensitive_accounts = sens.get("account_sensitivity") or []
    if not sens_path.exists():
        warnings.append(
            "sensitive_accounts.json not available -- no account can be elevated by nature. "
            "Run build_sensitive_detector first."
        )

    # Apply the floors: an account below the quantitative trivial threshold but above
    # its tag's floor is elevated. This is the whole point of the tool.
    elevated, floors_applied = [], {}
    for acct in sensitive_accounts:
        tag = acct.get("category")
        spec = by_tag.get(tag)
        floor_fraction = float(spec["floor_fraction"]) if spec else default_floor
        floor_value = effective_om * floor_fraction
        floors_applied[tag] = {"floor_fraction": floor_fraction, "floor_value": round(floor_value, 2)}

        balance = abs(float(acct.get("balance") or 0.0))
        if balance == 0.0:
            continue
        below_quantitative = computed_trivial and balance < computed_trivial
        above_floor = balance >= floor_value
        if below_quantitative and above_floor:
            elevated.append({
                "account": acct.get("account"),
                "gl_code": acct.get("gl_code"),
                "gl_name": acct.get("gl_name"),
                "category": tag,
                "balance": round(float(acct.get("balance") or 0.0), 2),
                "clearly_trivial_threshold": round(computed_trivial, 2),
                "sensitive_floor": round(floor_value, 2),
                "basis": (spec or {}).get("basis", "nature"),
                "reason": (spec or {}).get("reason", "Sensitive head -- material by nature."),
            })

    findings = []
    for e in elevated:
        findings.append(make_record(
            source_screen="build_materiality_lens",
            account=e["account"],
            fsli=e["category"],
            amount=e["balance"],
            source_row_id=e["gl_code"],
            observation=(
                f"'{e['gl_name']}' carries {abs(e['balance']):,.2f}, below the clearly-trivial "
                f"threshold of {e['clearly_trivial_threshold']:,.2f}, but is a "
                f"{e['category'].replace('_', ' ')} head."
            ),
            expectation=(
                "Materiality is judged by value, nature AND context. A head of this kind is "
                "material by its nature regardless of amount (sec 2.2)."
            ),
            gap=e["reason"],
            assertions=["Classification", "Presentation"],
            risk_basis=["nature"] if e["basis"] == "nature" else ["context"],
            risk_rating="medium",
            regularity_flag=e["basis"] == "context",
            data_sufficiency="medium",
            proposed_response=(
                "Include this balance in the audit population despite its size, and obtain "
                "the authorisation and utilisation records appropriate to its category."
            ),
            evidence_requested=[
                f"Account-wise ledger and supporting file for {e['gl_name']}",
                "Competent sanction or approval for the transaction",
            ],
            extra={
                "sensitive_category": e["category"],
                "sensitive_floor": e["sensitive_floor"],
                "clearly_trivial_threshold": e["clearly_trivial_threshold"],
                "elevation_basis": e["basis"],
            },
        ))

    payload = {
        "methodology": (
            "Reads the quantitative materiality computed by build_materiality and layers "
            "sec 2.2's nature and context lenses over it. Sensitive-head floors elevate "
            "accounts that fall below the quantitative threshold but carry audit "
            "significance by what they represent. Auditor-set materiality, when supplied, "
            "overrides the computed proxy entirely."
        ),
        "knowledge_pack_version": pack["_meta"]["version"],
        "generated_at": datetime.datetime.now().isoformat(),
        "materiality_basis": basis,
        "basis_note": basis_note,
        "provisional": basis == "computed_proxy" or scale_confirmed is not True,
        "effective_overall_materiality": round(effective_om, 2),
        "computed_overall_materiality": round(computed_om, 2),
        "clearly_trivial_threshold": round(computed_trivial, 2),
        "scale_confirmed": scale_confirmed,
        "scale_note": scale_note,
        "sensitive_floors_applied": floors_applied,
        "summary": {
            "sensitive_accounts_assessed": len(sensitive_accounts),
            "elevated_by_nature_or_context": len(elevated),
        },
        "elevated_accounts": elevated,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "materiality_lens.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "WARNING" if warnings else "SUCCESS",
        "can_continue": True,
        "message": (
            f"Materiality lens ({basis}, "
            f"{'provisional' if payload['provisional'] else 'confirmed'}): "
            f"{len(elevated)} account(s) elevated by nature or context that the quantitative "
            f"threshold alone would have dropped."
        ),
        "artifacts": [str(out_path.resolve())],
        "errors": [],
        "warnings": warnings,
    }




