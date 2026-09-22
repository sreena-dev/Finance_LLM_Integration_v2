import json
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.materiality import COVERAGE_HIGH_PCT, TRIVIAL_THRESHOLD_FRACTION
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

EXTREME_MOVEMENT_PCT_THRESHOLD = 300.0

MATERIALITY_RATIO_CRITICAL_PCT = 10000.0  # 100x

MATERIALITY_RATIO_HIGH_PCT = 2000.0  # 20x

MATERIALITY_RATIO_MEDIUM_PCT = 500.0  # 5x

MATERIALITY_RATIO_LOW_PCT = 75.0  # 0.75x

MIN_PEER_GROUP_SIZE = 2

PEER_Z_SCORE_OUTLIER_THRESHOLD = 3.0

ABNORMAL_MIN_TRIGGERED_RULES = 2

OFFSETTING_NET_TO_GROSS_RATIO = 0.2

FSLI_PRIORITY_CRITICAL_MULTIPLE = 10.0

TOP_N_HIERARCHY_FSLI = 15

TOP_N_HIERARCHY_SUB_FSLI = 30

TOP_N_ACCOUNTS = 20

TOP_N_DRIVERS = 15

TOP_N_PEER_OUTLIERS = 20

TOP_N_MATERIAL_VARIANCES_JSON = 50

TOP_N_FSLI_PRIORITY = 10

def _compute_variance(row, overall_mat: float, perf_mat: float) -> dict:
    ob = float(row.get("opening_balance", 0.0))
    cb = float(row.get("closing_balance", 0.0))
    mov = cb - ob
    abs_mov = abs(mov)

    pct = (mov / abs(ob)) * 100 if ob != 0 else 0.0

    trend = "Stable"
    if ob == 0 and cb != 0:
        trend = "New"
    elif ob != 0 and cb == 0:
        trend = "Closed"
    elif ob > 0 and cb < 0:
        trend = "Reversal (Asset to Liab)"
    elif ob < 0 and cb > 0:
        trend = "Reversal (Liab to Asset)"
    elif abs_mov > 0:
        trend = "Increase" if abs(cb) > abs(ob) else "Decrease"

    mat_ratio = (abs_mov / overall_mat * 100) if overall_mat > 0 else 0.0
    priority = "None"
    if mat_ratio > MATERIALITY_RATIO_CRITICAL_PCT:
        priority = "Critical"
    elif mat_ratio >= MATERIALITY_RATIO_HIGH_PCT:
        priority = "High"
    elif mat_ratio >= MATERIALITY_RATIO_MEDIUM_PCT:
        priority = "Medium"
    elif mat_ratio >= MATERIALITY_RATIO_LOW_PCT:
        priority = "Low"

    movement_class = "Normal"
    if abs_mov >= perf_mat and abs(pct) > EXTREME_MOVEMENT_PCT_THRESHOLD:
        movement_class = "Extreme"

    return {
        "opening_balance": round(ob, 2), "closing_balance": round(cb, 2), "movement": round(mov, 2),
        "abs_movement": round(abs_mov, 2), "movement_percent": round(pct, 2), "trend_classification": trend,
        "materiality_ratio": round(mat_ratio, 2), "priority": priority, "movement_class": movement_class,
    }

@pipeline_tool("build_variance_analysis", domain="variance")
def build_variance_analysis(canonical_tb_file: str, materiality_file: str = None, output_dir: str = None) -> dict:
    """Compute materiality-aware opening->closing movement analytics at GL and hierarchy level."""
    errors = []
    warnings = []
    artifacts = []

    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    snapshot_path = tb_path.parent / "snapshot_drilldown.parquet"
    if not snapshot_path.exists():
        snapshot_path = out_dir / "snapshot_drilldown.parquet"

    mat_path = Path(materiality_file) if materiality_file else (tb_path.parent / "materiality.json")
    if not mat_path.exists():
        mat_path = out_dir / "materiality.json"

    mapping_path = out_dir / "mapping_summary.json"
    if not mapping_path.exists():
        mapping_path = tb_path.parent / "mapping_summary.json"

    for p, label in [
        (tb_path, "Canonical TB (supply canonical_tb_file from load_tb_from_db/ingest_tb_to_live)"),
        (snapshot_path, "Drilldown Snapshot (run build_financial_snapshot first)"),
        (mat_path, "Materiality JSON (run build_materiality first)"),
    ]:
        if not p.exists():
            raise PipelineFileError(str(p), label)

    with open(mat_path, "r", encoding="utf-8") as f:
        mat_data = json.load(f)

    thresholds = mat_data.get("thresholds", {})
    overall_mat = float(thresholds.get("overall", 0.0))
    perf_mat = float(thresholds.get("performance", 0.0))

    if perf_mat == 0:
        overall_mat = mat_data.get("selected_materiality", {}).get("overall_materiality", 0.0)
        perf_mat = overall_mat * 0.75

    # Read GL Level
    tb_df = pl.read_parquet(tb_path)

    # An empty canonical TB used to crash here with a bare polars ColumnNotFoundError
    # ("unable to find column abs_movement"): the per-row variance dicts below come out
    # as an empty list, so pl.DataFrame([]) contributes no columns to the horizontal
    # concat and every derived column silently fails to exist. Fail explicitly instead
    # -- an empty TB is not a valid audit input, and the caller needs to be told that
    # rather than handed an internal column error.
    if tb_df.is_empty():
        return {
            "execution_status": "FAILED",
            "pipeline_status": "FAILED",
            "message": "Canonical TB contains no rows -- variance analysis requires at least one account.",
            "artifacts": [],
            "errors": [{"type": "EmptyInputError", "file": str(tb_path)}],
            "warnings": warnings,
        }

    tb_df = tb_df.with_columns(
        [
            (
                pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).alias(c)
                if c in tb_df.columns
                else pl.lit(0.0).alias(c)
            )
            for c in ["opening_balance", "closing_balance"]
        ]
    )
    tb_rows_pre = list(tb_df.iter_rows(named=True))
    tb_df = tb_df.with_columns(
        [
            pl.Series("fsli", [row_fsli(r) for r in tb_rows_pre]),
            pl.Series("fs_head", [row_fs_head(r) for r in tb_rows_pre]),
        ]
    )

    # Read Hierarchy Level
    tree_df = pl.read_parquet(snapshot_path)
    tree_df = tree_df.with_columns(
        [
            (
                pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).alias(c)
                if c in tree_df.columns
                else pl.lit(0.0).alias(c)
            )
            for c in ["opening_balance", "closing_balance"]
        ]
    )

    # Compute for GL -- _compute_variance is a whole-row, multi-branch classifier
    # (trend/priority/movement_class), ported as an explicit Python loop rather
    # than forced into polars expressions (same idiom used throughout this
    # migration for this class of function).
    gl_var_dicts = [_compute_variance(r, overall_mat, perf_mat) for r in tb_df.iter_rows(named=True)]
    tb_df = tb_df.drop([c for c in ["opening_balance", "closing_balance"] if c in tb_df.columns])
    tb_df = pl.concat([tb_df, pl.DataFrame(gl_var_dicts)], how="horizontal")

    # Compute for Tree
    tree_var_dicts = [_compute_variance(r, overall_mat, perf_mat) for r in tree_df.iter_rows(named=True)]
    tree_df = tree_df.drop([c for c in ["opening_balance", "closing_balance"] if c in tree_df.columns])
    tree_df = pl.concat([tree_df, pl.DataFrame(tree_var_dicts)], how="horizontal")

    # Peer Analysis Z-Scores (grouped by derived FSLI, TB-v1 grouped by persisted line_item).
    # groupby+index-alignment .loc writes have no polars equivalent -- windowed
    # .over("fsli") expressions instead, gated by the same group-size/std>0 conditions.
    tb_df = tb_df.with_columns(
        [
            pl.col("abs_movement").mean().over("fsli").alias("_peer_mean"),
            pl.col("abs_movement").std().over("fsli").alias("_peer_std"),
            pl.len().over("fsli").alias("_peer_count"),
        ]
    )
    peer_gate = (pl.col("_peer_count") > MIN_PEER_GROUP_SIZE) & (pl.col("_peer_std") > 0)
    tb_df = tb_df.with_columns(
        [
            pl.when(peer_gate)
            .then((pl.col("abs_movement") - pl.col("_peer_mean")) / pl.col("_peer_std"))
            .otherwise(0.0)
            .alias("z_score"),
            pl.when(peer_gate)
            .then(pl.col("abs_movement").rank(method="average").over("fsli") / pl.col("_peer_count") * 100)
            .otherwise(0.0)
            .alias("percentile"),
        ]
    ).drop(["_peer_mean", "_peer_std", "_peer_count"])

    # Sorted by fsli (stable) before filtering to mirror pandas' groupby(key)
    # traversal order (sorted groups, original row order within each group) --
    # this only affects which outliers land in the top-N preview cutoff below,
    # never the z-scores/classification themselves.
    peer_analysis = []
    def _compute_peer_analysis():
        for out in tb_df.filter(pl.col("z_score") > PEER_Z_SCORE_OUTLIER_THRESHOLD).sort("fsli").iter_rows(named=True):
            peer_analysis.append({
                "peer_group": str(out.get("fsli", "")),
                "account": str(out.get("gl_code", "")) + " - " + str(out.get("gl_name", "")),
                "movement": round(out["movement"], 2),
                "percentile": round(out["percentile"], 2),
                "z_score": round(out["z_score"], 2),
                "classification": "Extreme",
            })

    _compute_peer_analysis()

    abn_classes = []
    abn_rules = []

    def _apply_abnormal_detection():
        # Abnormal Rules Detection
        def detect_abnormal(row):
            rules = []
            if row.get("abs_movement", 0) >= perf_mat:
                rules.append("Materiality")
            if "Reversal" in row.get("trend_classification", ""):
                rules.append("Sign Reversal")
            if abs(row.get("movement_percent", 0)) > EXTREME_MOVEMENT_PCT_THRESHOLD:
                rules.append(f"Extreme Percentage (>{EXTREME_MOVEMENT_PCT_THRESHOLD:.0f}%)")
            if row.get("z_score", 0) > PEER_Z_SCORE_OUTLIER_THRESHOLD:
                rules.append(f"Peer Outlier (Z>{PEER_Z_SCORE_OUTLIER_THRESHOLD:.0f})")

            if len(rules) >= ABNORMAL_MIN_TRIGGERED_RULES and row.get("abs_movement", 0) >= perf_mat:
                return "Extreme", rules
            return "Normal", rules

        tb_rows_for_abn = list(tb_df.iter_rows(named=True))
        for row in tb_rows_for_abn:
            c, r = detect_abnormal(row)
            abn_classes.append(c)
            abn_rules.append(r)

    _apply_abnormal_detection()
    tb_df = tb_df.with_columns(
        [
            pl.Series("movement_class", abn_classes),
            pl.Series("triggered_rules", abn_rules, dtype=pl.List(pl.Utf8)),
        ]
    )

    total_movement = tb_df["abs_movement"].sum()

    gl_records = tb_df.to_dicts()
    tree_records = tree_df.to_dicts()

    material_variances = []
    hierarchy_variances = {"fsli": [], "sub_fsli": []}
    new_accounts = []
    closed_accounts = []
    dormant_accounts = []
    sign_reversals = []
    abnormal_variances = []
    variance_drivers = {}

    # Process Hierarchy
    def _process_hierarchy_variances():
        for row in tree_records:
            if row["abs_movement"] >= perf_mat:
                node = {
                    "name": str(row.get("node_name", "")),
                    "parent_fsli": str(row.get("parent_node_name", "") or ""),
                    "hierarchy_level": int(row.get("hierarchy_level", 0)),
                    "opening_balance": row["opening_balance"],
                    "closing_balance": row["closing_balance"],
                    "movement": row["movement"],
                    "movement_percent": row["movement_percent"],
                    "trend": row["trend_classification"],
                    "contribution_to_total_variance": round((row["abs_movement"] / total_movement * 100), 2) if total_movement > 0 else 0,
                }
                if row["hierarchy_level"] <= 2:
                    hierarchy_variances["fsli"].append(node)
                    variance_drivers[node["name"]] = {"driver": node["name"], "accounts": 0, "net_variance": row["movement"]}
                else:
                    hierarchy_variances["sub_fsli"].append(node)


    _process_hierarchy_variances()
    hierarchy_variances["fsli"] = sorted(hierarchy_variances["fsli"], key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_HIERARCHY_FSLI]
    hierarchy_variances["sub_fsli"] = sorted(hierarchy_variances["sub_fsli"], key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_HIERARCHY_SUB_FSLI]

    # Process GL
    def _process_gl_variances():
        for row in gl_records:
            fsli = str(row.get("fsli", ""))
            rec = {
                "account": str(row.get("gl_code", "")) + " - " + str(row.get("gl_name", "")),
                "fsli": fsli,
                "opening_balance": row["opening_balance"],
                "closing_balance": row["closing_balance"],
                "movement": row["movement"],
                "movement_percent": row["movement_percent"],
                "materiality_ratio": row["materiality_ratio"],
                "priority": row["priority"],
            }

            if fsli in variance_drivers:
                variance_drivers[fsli]["accounts"] += 1

            if row["abs_movement"] >= perf_mat:
                c = "Material " + row["trend_classification"]
                rec_mat = rec.copy()
                rec_mat["classification"] = c
                multiple = round(row["abs_movement"] / perf_mat, 2) if perf_mat > 0 else "N/A "
                rec_mat["reason"] = f"Movement exceeds Performance Materiality by {multiple}x"
                rec_mat["performance_materiality"] = perf_mat
                rec_mat["overall_materiality"] = overall_mat
                material_variances.append(rec_mat)

            if row["trend_classification"] == "New" and row["abs_movement"] >= perf_mat:
                new_accounts.append(rec)

            if row["trend_classification"] == "Closed" and row["abs_movement"] >= perf_mat:
                closed_accounts.append(rec)

            if row["abs_movement"] == 0 and row["closing_balance"] == 0 and row["opening_balance"] == 0:
                dormant_accounts.append(rec)

            if "Reversal" in row["trend_classification"] and row["abs_movement"] >= perf_mat:
                sign_reversals.append(rec)

            if row["movement_class"] == "Extreme":
                rec_abn = rec.copy()
                rec_abn["movement_class"] = "Extreme"
                rec_abn["triggered_rules"] = row["triggered_rules"]
                abnormal_variances.append(rec_abn)

    _process_gl_variances()

    material_variances = sorted(material_variances, key=lambda x: abs(x["movement"]), reverse=True)
    new_accounts = sorted(new_accounts, key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_ACCOUNTS]
    closed_accounts = sorted(closed_accounts, key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_ACCOUNTS]
    dormant_accounts = dormant_accounts[:TOP_N_ACCOUNTS]
    sign_reversals = sorted(sign_reversals, key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_ACCOUNTS]
    abnormal_variances = sorted(abnormal_variances, key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_ACCOUNTS]
    variance_drivers_list = sorted(list(variance_drivers.values()), key=lambda x: abs(x["net_variance"]), reverse=True)[:TOP_N_DRIVERS]

    fsli_priority = []
    def _compute_fsli_priority():
        for f in hierarchy_variances["fsli"]:
            fsli_priority.append({
                "fsli": f["name"],
                "priority": "Critical" if abs(f["movement"]) >= overall_mat * FSLI_PRIORITY_CRITICAL_MULTIPLE else ("High" if abs(f["movement"]) >= overall_mat else "Medium"),
                "movement": f["movement"],
            })

    _compute_fsli_priority()
    fsli_priority = sorted(fsli_priority, key=lambda x: abs(x["movement"]), reverse=True)[:TOP_N_FSLI_PRIORITY]

    # Concentration
    sorted_bals = tb_df["abs_movement"].sort(descending=True)
    top10_sum = float(sorted_bals.head(10).sum()) if len(sorted_bals) >= 10 else float(sorted_bals.sum())
    top20_sum = float(sorted_bals.head(20).sum()) if len(sorted_bals) >= 20 else float(sorted_bals.sum())
    top50_sum = float(sorted_bals.head(50).sum()) if len(sorted_bals) >= 50 else float(sorted_bals.sum())

    concentration_analysis = {
        "top10_percent": round((top10_sum / total_movement * 100), 2) if total_movement > 0 else 0,
        "top20_percent": round((top20_sum / total_movement * 100), 2) if total_movement > 0 else 0,
        "top50_percent": round((top50_sum / total_movement * 100), 2) if total_movement > 0 else 0,
        "dominant_fsli": hierarchy_variances["fsli"][0]["name"] if hierarchy_variances["fsli"] else "None",
        "dominant_sub_fsli": hierarchy_variances["sub_fsli"][0]["name"] if hierarchy_variances["sub_fsli"] else "None",
        "largest_gl": material_variances[0]["account"] if material_variances else "None",
        "cumulative_movement": round(top50_sum, 2),
    }

    # Variance Distribution
    variance_distribution = {
        "below_trivial": {"accounts": 0, "balance": 0.0},
        "trivial_to_performance": {"accounts": 0, "balance": 0.0},
        "performance_to_materiality": {"accounts": 0, "balance": 0.0},
        "above_materiality": {"accounts": 0, "balance": 0.0},
    }
    t_trivial = overall_mat * TRIVIAL_THRESHOLD_FRACTION
    for row in tb_df.iter_rows(named=True):
        am = row["abs_movement"]
        if am < t_trivial:
            b = "below_trivial"
        elif am < perf_mat:
            b = "trivial_to_performance"
        elif am < overall_mat:
            b = "performance_to_materiality"
        else:
            b = "above_materiality"

        variance_distribution[b]["accounts"] += 1
        variance_distribution[b]["balance"] += am

    for k in variance_distribution:
        variance_distribution[k]["balance"] = round(variance_distribution[k]["balance"], 2)

    # Offsetting Movements
    def _compute_offsetting_movements():
        offsetting_movements = []
        pos_sum = tb_df.filter(pl.col("movement") > 0)["movement"].sum()
        neg_sum = abs(tb_df.filter(pl.col("movement") < 0)["movement"].sum())
        gross_mov = pos_sum + neg_sum
        net_mov = abs(pos_sum - neg_sum)

        if gross_mov > 0 and net_mov < (OFFSETTING_NET_TO_GROSS_RATIO * gross_mov):
            offsetting_movements.append({
                "accounts": ["Entity Wide Portfolio"],
                "gross_movement": round(gross_mov, 2),
                "net_movement": round(net_mov, 2),
                "confidence": 0.95,
                "reason": "Highly balanced net movement across the entire ledger.",
            })

        if len(hierarchy_variances["fsli"]) >= 2:
            top_pos = None
            top_neg = None
            for f in hierarchy_variances["fsli"]:
                if f["movement"] > 0 and not top_pos:
                    top_pos = f
                if f["movement"] < 0 and not top_neg:
                    top_neg = f
                if top_pos and top_neg:
                    break

            if top_pos and top_neg and abs(abs(top_pos["movement"]) - abs(top_neg["movement"])) < overall_mat:
                offsetting_movements.append({
                    "accounts": [top_pos["name"], top_neg["name"]],
                    "gross_movement": round(abs(top_pos["movement"]) + abs(top_neg["movement"]), 2),
                    "net_movement": round(abs(top_pos["movement"] + top_neg["movement"]), 2),
                    "confidence": 0.97,
                    "reason": "One-to-one FSLI offset.",
                })

        return offsetting_movements

    offsetting_movements = _compute_offsetting_movements()

    # Cross-FSLI Variance Relationships
    variance_relationships = []
    fslis = hierarchy_variances["fsli"]
    rev = next((f for f in fslis if "revenue" in f["name"].lower()), None)
    exp = next((f for f in fslis if "expense" in f["name"].lower() or "cost" in f["name"].lower()), None)
    if rev and exp:
        variance_relationships.append({
            "source": rev["name"],
            "target": exp["name"],
            "relationship": "Expected Parallel Movement (Revenue vs Cost)",
            "confidence": 0.95,
        })

    # Statistics
    pos_mask = tb_df["movement"] > 0
    neg_mask = tb_df["movement"] < 0
    zero_mask = tb_df["movement"] == 0

    movement_statistics = {
        "positive_movements": int(pos_mask.sum()),
        "negative_movements": int(neg_mask.sum()),
        "zero_movements": int(zero_mask.sum()),
        "average_movement": round(tb_df["abs_movement"].mean(), 2) if len(tb_df) > 0 else 0,
        "median_movement": round(tb_df["abs_movement"].median(), 2) if len(tb_df) > 0 else 0,
        "largest_increase": round(tb_df["movement"].max(), 2) if len(tb_df) > 0 else 0,
        "largest_decrease": round(tb_df["movement"].min(), 2) if len(tb_df) > 0 else 0,
    }

    summary = {
        "total_accounts": len(tb_df),
        "changed_accounts": int((~zero_mask).sum()),
        "unchanged_accounts": int(zero_mask.sum()),
        "new_accounts": len([x for x in gl_records if x["trend_classification"] == "New"]),
        "closed_accounts": len([x for x in gl_records if x["trend_classification"] == "Closed"]),
        "total_movement": round(total_movement, 2),
        "largest_variance": movement_statistics["largest_increase"] if abs(movement_statistics["largest_increase"]) > abs(movement_statistics["largest_decrease"]) else movement_statistics["largest_decrease"],
        "planning_confidence": "High",
    }

    coverage = {
        "mapped_accounts": 0,
        "mapped_balance": 0.0,
        "unmapped_accounts": 0,
        "unmapped_balance": 0.0,
        "confidence": "Unknown",
    }
    if mapping_path.exists():
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping_stats = json.load(f)
            coverage["mapped_accounts"] = mapping_stats.get("mapped_rows", 0)
            coverage["unmapped_accounts"] = mapping_stats.get("unmapped_rows", 0)
            t = coverage["mapped_accounts"] + coverage["unmapped_accounts"]
            if t > 0:
                coverage["mapped_balance"] = round((coverage["mapped_accounts"] / t) * 100, 2)
                coverage["unmapped_balance"] = round((coverage["unmapped_accounts"] / t) * 100, 2)
            coverage["confidence"] = "High" if coverage["mapped_balance"] > COVERAGE_HIGH_PCT else "Medium"

    audit_focus = {
        "critical_variances": [x["account"] for x in material_variances if x["priority"] == "Critical"][:5],
        "significant_increases": [x["account"] for x in material_variances if x["movement"] > 0][:5],
        "significant_decreases": [x["account"] for x in material_variances if x["movement"] < 0][:5],
        "new_material_accounts": [x["account"] for x in new_accounts][:5],
        "sign_reversals": [x["account"] for x in sign_reversals][:5],
        "largest_movements": [x["account"] for x in material_variances][:5],
    }

    planning_actions = {
        "substantive_testing": [x["account"] for x in material_variances if x["priority"] in ["Critical", "High"]][:5],
        "analytical_review": [x["name"] for x in hierarchy_variances["fsli"]][:5],
        "sampling_candidates": [x["account"] for x in material_variances if x["priority"] in ["Medium", "Low"]][:5],
        "management_inquiry": [x["account"] for x in abnormal_variances][:5],
    }

    downstream_metadata = {
        "variance_threshold": perf_mat,
        "critical_variance_count": len([x for x in material_variances if x["priority"] == "Critical"]),
        "high_variance_count": len([x for x in material_variances if x["priority"] == "High"]),
        "new_account_count": summary["new_accounts"],
        "closed_account_count": summary["closed_accounts"],
        "sign_reversal_count": len(sign_reversals),
        "dominant_fsli": concentration_analysis["dominant_fsli"],
        "planning_confidence": coverage["confidence"],
    }

    # Save Parquet
    parquet_path = out_dir / "variance_analysis.parquet"
    write_parquet_atomic(tb_df.drop("triggered_rules"), parquet_path)
    artifacts.append(str(parquet_path.resolve()))

    # Save JSON
    output_data = {
        "methodology": "Deterministic quantitative movement classification relative to planning materiality thresholds.",
        "summary": summary,
        "movement_statistics": movement_statistics,
        "variance_distribution": variance_distribution,
        "material_variances": material_variances[:TOP_N_MATERIAL_VARIANCES_JSON],
        "hierarchy_variances": hierarchy_variances,
        "concentration_analysis": concentration_analysis,
        "variance_drivers": variance_drivers_list,
        "peer_analysis": peer_analysis[:TOP_N_PEER_OUTLIERS],
        "new_accounts": new_accounts,
        "closed_accounts": closed_accounts,
        "dormant_accounts": dormant_accounts,
        "sign_reversals": sign_reversals,
        "offsetting_movements": offsetting_movements,
        "variance_relationships": variance_relationships,
        "abnormal_variances": abnormal_variances,
        "fsli_priority": fsli_priority,
        "planning_actions": planning_actions,
        "coverage": coverage,
        "audit_focus": audit_focus,
        "downstream_metadata": downstream_metadata,
        "limitations": [],
    }

    json_path = out_dir / "variance_analysis.json"
    write_json_atomic(output_data, json_path, indent=4)
    artifacts.append(str(json_path.resolve()))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "message": "Phase 2 Quantitative Variance Intelligence Engine completed. Exported metadata and metrics.",
        "artifacts": artifacts,
        "errors": errors,
        "warnings": warnings,
    }


