import functools
import inspect
import json
import re
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.duckdb_query import query_parquet
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403
from modes.trial_balance.pipeline.valkey_client import chat_cache_get, chat_cache_set, make_cache_key


def require_file(path: str, label: str) -> str:
    if not path or not Path(path).exists():
        raise PipelineFileError(str(path), label)
    return path


def _file_fingerprint(path_str: str) -> str:
    """mtime_ns + size, not just the path -- an artifact can be regenerated
    mid-session (e.g. a re-run of ingest_tb_to_live), and this makes a stale
    cache entry self-correct: new mtime/size -> new cache key -> automatic
    miss, no explicit invalidation call needed anywhere."""
    p = Path(path_str)
    if not p.exists():
        return "missing"
    st = p.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def chat_cacheable(func):
    """Wraps a chat.py tool's raw function (apply BELOW @pipeline_tool, i.e.
    `@pipeline_tool(...)` then `@chat_cacheable` then `def ...`) with a
    Valkey-backed cache keyed by the function name, every `*_file` kwarg's
    content fingerprint, and every other kwarg's value. All 9 chat.py tools
    take their inputs as `*_file` path args plus small scalar filters, so this
    one decorator covers every tool without per-tool cache-key logic.
    Empirically, every one of the 9 chat.py tools re-parses its full Parquet/
    JSON artifact from scratch on every single call with zero caching -- this
    is the fix for that, independent of whether a given tool also uses
    DuckDB (query_parquet) or stays on Polars underneath."""
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Bind by parameter name regardless of positional/keyword call style --
        # every chat.py tool is called positionally by the LLM agent's tool
        # dispatcher AND by direct test/route calls, so a fingerprint keyed
        # only off **kwargs (missing positional args entirely) silently never
        # fingerprints a file passed positionally, defeating the whole
        # invalidation guarantee below.
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()

        key_parts = [func.__name__]
        for k in sorted(bound.arguments):
            v = bound.arguments[k]
            if k.endswith("_file") and v:
                key_parts.append(f"{k}={v}:{_file_fingerprint(v)}")
            else:
                key_parts.append(f"{k}={v}")
        cache_key = make_cache_key(*key_parts)

        cached = chat_cache_get(cache_key)
        if cached is not None:
            return cached

        result = func(*args, **kwargs)
        if isinstance(result, dict):
            chat_cache_set(cache_key, result)
        return result

    return wrapper





@pipeline_tool("chat_get_account_balance", domain="chat")
@chat_cacheable
def chat_get_account_balance(canonical_tb_file: str, gl_code: str = None, gl_name_contains: str = None) -> dict:
    """Look up a GL account's balance in canonical_tb.parquet by exact gl_code or a gl_name substring.

    A genuine point/filter lookup against the full Parquet file -- one of the
    two chat.py cases DuckDB is used for (see backend/tools/duckdb_query.py's
    module docstring for the scoping rationale) instead of Polars' full
    pl.read_parquet(...).filter(...), which re-parses the whole file on every
    call. contains(lower(...), lower(?)) mirrors Polars'
    str.contains(literal=True) exactly -- a literal substring match, not a
    LIKE pattern, so a needle containing '%' or '_' is still matched
    literally rather than as a wildcard."""
    path = require_file(canonical_tb_file, "canonical TB")

    if gl_code:
        matches_df = query_parquet("SELECT * FROM t WHERE CAST(gl_code AS VARCHAR) = ?", [str(gl_code)], path)
    elif gl_name_contains:
        matches_df = query_parquet(
            "SELECT * FROM t WHERE contains(lower(CAST(gl_name AS VARCHAR)), lower(?))",
            [gl_name_contains], path,
        )
    else:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "Provide gl_code or gl_name_contains.",
            "data": None,
            "artifacts": [],
        }

    if matches_df.is_empty():
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "No matching account found.",
            "data": None,
            "artifacts": [],
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(matches_df)} matching account(s).",
        "data": matches_df.row(0, named=True) if len(matches_df) == 1 else matches_df.to_dicts(),
    }


_RATIO_NAMES = (
    "revenue_to_assets", "current", "quick", "debt_equity",
    "interest_coverage", "gross_margin", "net_margin", "roce",
)

@pipeline_tool("chat_get_financial_ratios", domain="chat")
@chat_cacheable
def chat_get_financial_ratios(financial_ratios_file: str, ratio_name: str = None) -> dict:
    """Look up one audit ratio by name (current, quick, debt_equity, interest_coverage,
    gross_margin, net_margin, roce, revenue_to_assets), or all of them if ratio_name is
    omitted. Reads financial_ratios.json (produced by build_financial_ratios)."""
    path = require_file(financial_ratios_file, "Financial ratios (financial_ratios.json)")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    ratios = data.get("ratios", {})

    if ratio_name:
        if ratio_name not in _RATIO_NAMES:
            return {
                "execution_status": "FAILED",
                "pipeline_status": "WARNING",
                "message": f"Unknown ratio_name {ratio_name!r}. Valid values: {', '.join(_RATIO_NAMES)}.",
                "data": None,
                "artifacts": [],
            }
        ratio = ratios.get(ratio_name)
        if not ratio:
            return {
                "execution_status": "FAILED",
                "pipeline_status": "WARNING",
                "message": f"Ratio {ratio_name!r} not present in this run's financial_ratios.json.",
                "data": None,
                "artifacts": [],
            }
        return {
            "execution_status": "SUCCESS",
            "pipeline_status": "SUCCESS",
            "artifacts": [],
            "message": f"{ratio_name} = {ratio.get('value')} ({ratio.get('formula')})." if ratio.get("value") is not None
                       else f"{ratio_name} could not be computed -- missing component(s): {', '.join(ratio.get('components_missing', []))}.",
            "data": {ratio_name: ratio},
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Returning all {len(ratios)} ratio(s).",
        "data": ratios,
    }


_GROUP_COLUMNS__chat_get_fsli_breakdown = ["main_head", "sub_head_1", "sub_head_2", "fs_head", "line_item", "hierarchy_path"]

@pipeline_tool("chat_get_fsli_breakdown", domain="chat")
@chat_cacheable
def chat_get_fsli_breakdown(fsli_summary_file: str, fsli_group: str = None) -> dict:
    """Look up FSLI hierarchy nodes, optionally filtered by main_head/sub_head_1/sub_head_2 substring."""
    df = pl.read_parquet(require_file(fsli_summary_file, "FSLI summary"))

    present_group_cols = [c for c in _GROUP_COLUMNS__chat_get_fsli_breakdown if c in df.columns]

    if fsli_group and present_group_cols:
        needle = fsli_group.lower()
        mask = pl.any_horizontal(
            [pl.col(c).cast(pl.Utf8).str.to_lowercase().str.contains(needle, literal=True).fill_null(False) for c in present_group_cols]
        )
        matches = df.filter(mask)
    else:
        matches = df

    if matches.is_empty():
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "No matching FSLI node found.",
            "data": None,
            "artifacts": [],
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(matches)} matching FSLI node(s).",
        "data": matches.to_dicts(),
    }


@pipeline_tool("chat_get_reasoning", domain="chat")
@chat_cacheable
def chat_get_reasoning(reasoning_file: str, limit: int = 5) -> dict:
    """Return the executive summary and top observations from an audit or comparison reasoning file, for grounding chat answers."""
    path = require_file(reasoning_file, "reasoning file")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    exec_summary = data.get("summary", {}).get("executive_summary", "")
    # audit_reasoning.json (single-TB) uses "audit_observations"; comparison_reasoning.json uses "observations".
    obs_list = data.get("audit_observations") or data.get("observations") or []

    previews = []
    for item in obs_list[:limit]:
        obs = item.get("observation", item) if isinstance(item, dict) else {}
        previews.append({
            "observation_id": obs.get("observation_id"),
            "title": obs.get("title"),
            "executive_summary": obs.get("executive_summary"),
            "priority": obs.get("priority"),
            "conclusion": obs.get("conclusion"),
        })

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Loaded reasoning summary with {len(obs_list)} total observation(s); returning top {len(previews)}.",
        "data": {
            "executive_summary": exec_summary,
            "observations": previews,
            "total_observations": len(obs_list),
        },
    }


@pipeline_tool("chat_get_top_exceptions", domain="chat")
@chat_cacheable
def chat_get_top_exceptions(consolidated_exceptions_file: str, severity: str = None, limit: int = 10) -> dict:
    """Return the top-scoring consolidated exceptions, optionally filtered by severity."""
    path = require_file(consolidated_exceptions_file, "consolidated exceptions")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    exceptions = data.get("exceptions", []) or []

    if severity:
        exceptions = [
            exc for exc in exceptions
            if str(exc.get("scoring", {}).get("severity", "")).lower() == severity.lower()
        ]

    if not exceptions:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "No matching exceptions found.",
            "data": None,
            "artifacts": [],
        }

    exceptions = sorted(
        exceptions,
        key=lambda exc: exc.get("scoring", {}).get("composite_score", 0.0),
        reverse=True,
    )
    top = exceptions[:limit]

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(top)} exception(s) (of {len(exceptions)} matching).",
        "data": top,
    }


_GROUP_COLUMNS__chat_get_variance_for_account = ["fsli_group", "main_head", "sub_head_1", "line_item"]

@pipeline_tool("chat_get_variance_for_account", domain="chat")
@chat_cacheable
def chat_get_variance_for_account(comparison_variance_file: str, gl_code: str = None, fsli_group: str = None) -> dict:
    """Look up PY/CY variance for a GL account by code, or all accounts within an FSLI group."""
    path = require_file(comparison_variance_file, "comparison variance")

    if gl_code:
        # Cheap schema-only read (Parquet footer metadata, not the full file) to
        # preserve the exact original "column may not exist" behavior before
        # committing to the DuckDB point-lookup below.
        if "gl_code" not in pl.scan_parquet(path).columns:
            matches = pl.DataFrame()
        else:
            matches = query_parquet("SELECT * FROM t WHERE CAST(gl_code AS VARCHAR) = ?", [str(gl_code)], path)
    elif fsli_group:
        df = pl.read_parquet(path)
        group_col = next((c for c in _GROUP_COLUMNS__chat_get_variance_for_account if c in df.columns), None)
        if group_col:
            needle = fsli_group.lower()
            matches = df.filter(
                pl.col(group_col).cast(pl.Utf8).str.to_lowercase().str.contains(needle, literal=True).fill_null(False)
            )
        else:
            matches = df.head(0)
    else:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "Provide gl_code or fsli_group.",
            "data": None,
            "artifacts": [],
        }

    if matches.is_empty():
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": "No matching variance record found.",
            "data": None,
            "artifacts": [],
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(matches)} matching variance record(s).",
        "data": matches.row(0, named=True) if len(matches) == 1 else matches.to_dicts(),
    }


_DELTA_TYPES = ("new", "removed", "zero-movement", "reclassified", "material-variance")

_RECLASS_COLS = ("main_head", "sub_head_1", "sub_head_2")

def _apply_text_filter__chat_query_comparison(rows: list, needle: str, keys: tuple) -> list:
    if not needle:
        return rows
    n = needle.lower()
    return [r for r in rows if any(n in str(r.get(k, "") or "").lower() for k in keys)]

def _resolve_materiality_threshold(materiality_file, materiality_tier):
    """Mirrors run_comparison_variance.py's own materiality_file -> thresholds resolution
    (backend/tools/comparison/run_comparison_variance.py) so this stays the one convention
    for reading materiality.json, not a second one invented here."""
    if not materiality_file or not Path(materiality_file).exists():
        return None
    with open(materiality_file, "r", encoding="utf-8") as f:
        mat_data = json.load(f)
    thresholds = mat_data.get("thresholds", {})
    return float(thresholds.get(materiality_tier, 0.0)) or None

def _delta_new_removed(structural_delta_file, which, canonical_tb_file, materiality_file, materiality_tier, filter_, limit):
    path = require_file(structural_delta_file, "Structural delta (structural_delta.json)")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    key = "new_ledgers" if which == "new" else "removed_ledgers"
    rows = list(data.get(key, []) or [])

    # structural_delta.json's new_ledgers/removed_ledgers carry only gl_code/gl_name (no
    # balance, verified schema) -- join against the matching period's canonical TB whenever
    # it's supplied, both to surface closing_balance (useful regardless of materiality) and
    # to make a materiality_file threshold filter possible in this same call.
    threshold = None
    if canonical_tb_file and Path(canonical_tb_file).exists():
        tb_df = pl.read_parquet(canonical_tb_file)
        if {"gl_code", "closing_balance"}.issubset(tb_df.columns):
            balances = dict(tb_df.select(["gl_code", "closing_balance"]).iter_rows())
            for r in rows:
                r["closing_balance"] = balances.get(r.get("gl_code"))

        threshold = _resolve_materiality_threshold(materiality_file, materiality_tier)
        if threshold is not None:
            rows = [r for r in rows if r.get("closing_balance") is not None and abs(r["closing_balance"]) >= threshold]

    rows = _apply_text_filter__chat_query_comparison(rows, filter_, ("gl_code", "gl_name"))
    label = f"{which} GL codes" if threshold is None else f"{which} GL codes (above {materiality_tier} materiality, {threshold:,.2f})"
    return rows[:limit], label

def _delta_zero_movement(comparison_variance_file, filter_, limit):
    path = require_file(comparison_variance_file, "Comparison variance (comparison_variance.parquet)")
    df = pl.read_parquet(path)
    matches = df.filter(pl.col("flag") == "NO_CHANGE") if "flag" in df.columns else df.head(0)
    rows = _apply_text_filter__chat_query_comparison(matches.to_dicts(), filter_, ("gl_code", "gl_name"))
    return rows[:limit], "zero-movement (identical YoY closing balance) accounts"

def _delta_material_variance(comparison_variance_file, flag, filter_, limit):
    path = require_file(comparison_variance_file, "Comparison variance (comparison_variance.parquet)")
    df = pl.read_parquet(path)
    if flag:
        matches = df.filter(pl.col("flag") == flag.upper())
    else:
        matches = df.filter(pl.col("flag").is_in(["CRITICAL", "HIGH", "MEDIUM"])) if "flag" in df.columns else df.head(0)
    matches = matches.sort("variance", descending=True) if "variance" in matches.columns else matches
    rows = _apply_text_filter__chat_query_comparison(matches.to_dicts(), filter_, ("gl_code", "gl_name"))
    return rows[:limit], f"material-variance accounts (flag={flag or 'CRITICAL/HIGH/MEDIUM'})"

def _delta_reclassified(py_canonical_tb_file, cy_canonical_tb_file, filter_, limit):
    py_path = require_file(py_canonical_tb_file, "PY canonical TB (py/canonical_tb.parquet)")
    cy_path = require_file(cy_canonical_tb_file, "CY canonical TB (cy/canonical_tb.parquet)")
    py_df = pl.read_parquet(py_path)
    cy_df = pl.read_parquet(cy_path)

    keep_cols = ["gl_code", "gl_name", "closing_balance"] + list(_RECLASS_COLS)
    py_slim = py_df.select([c for c in keep_cols if c in py_df.columns]).rename(
        {c: f"py_{c}" for c in keep_cols if c in py_df.columns and c != "gl_code"}
    )
    cy_slim = cy_df.select([c for c in keep_cols if c in cy_df.columns]).rename(
        {c: f"cy_{c}" for c in keep_cols if c in cy_df.columns and c != "gl_code"}
    )
    joined = cy_slim.join(py_slim, on="gl_code", how="inner")

    present_reclass_cols = [c for c in _RECLASS_COLS if f"py_{c}" in joined.columns and f"cy_{c}" in joined.columns]
    if not present_reclass_cols:
        return [], "reclassified GL codes (PY vs CY FSLI mapping change)"

    # fill_null before comparing: two nulls (e.g. both sub_head_2 unset) must compare equal,
    # not evaluate to a null/ambiguous mask that could silently drop or include a row.
    changed_mask = pl.any_horizontal(
        [
            pl.col(f"py_{c}").cast(pl.Utf8).fill_null("") != pl.col(f"cy_{c}").cast(pl.Utf8).fill_null("")
            for c in present_reclass_cols
        ]
    )
    changed = joined.filter(changed_mask)
    if "py_closing_balance" in changed.columns and "cy_closing_balance" in changed.columns:
        changed = changed.with_columns(
            (pl.col("cy_closing_balance") - pl.col("py_closing_balance")).alias("balance_delta")
        )
    rows = _apply_text_filter__chat_query_comparison(changed.to_dicts(), filter_, ("gl_code", "gl_name"))
    rows.sort(key=lambda r: abs(r.get("balance_delta") or 0.0), reverse=True)
    return rows[:limit], "reclassified GL codes (PY vs CY FSLI mapping change)"

@pipeline_tool("chat_query_comparison", domain="chat")
@chat_cacheable
def chat_query_comparison(
    delta_type: str,
    filter: str = None,
    flag: str = None,
    limit: int = 20,
    structural_delta_file: str = None,
    comparison_variance_file: str = None,
    py_canonical_tb_file: str = None,
    cy_canonical_tb_file: str = None,
    materiality_file: str = None,
    materiality_tier: str = "overall",
) -> dict:
    """PY-vs-CY comparison lookups. `delta_type` determines which file parameter(s) you must
    supply -- pass only what's listed, extra file params for other delta_types are ignored:
    'new' -- requires `structural_delta_file` (GL codes present in CY only). Optionally also
    supply `cy_canonical_tb_file` to include each code's real closing_balance in the results,
    and `materiality_file` (+ optional `materiality_tier`, default "overall") together with it
    to filter to only codes whose |closing_balance| is at or above that real threshold --
    this is the only way to answer "new AND above materiality" in one call; there is no other
    way to discover materiality.json's actual threshold value, so don't guess one.
    'removed' -- same as 'new' but requires `py_canonical_tb_file` instead (removed codes only
    exist in PY) for the closing_balance/materiality join.
    'zero-movement' -- requires `comparison_variance_file` (identical YoY closing balance).
    'material-variance' -- requires `comparison_variance_file` (variance flagged CRITICAL/
    HIGH/MEDIUM, optionally narrowed to one `flag`).
    'reclassified' -- requires BOTH `py_canonical_tb_file` and `cy_canonical_tb_file` (same
    GL code, different main_head/sub_head_1/sub_head_2 between PY and CY, with the balance
    delta) -- this is the one delta_type that does NOT read structural_delta_file or
    comparison_variance_file at all.
    `filter` narrows results by a case-insensitive substring on GL code/name."""
    if delta_type not in _DELTA_TYPES:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"Unknown delta_type {delta_type!r}. Valid values: {', '.join(_DELTA_TYPES)}.",
            "data": None,
            "artifacts": [],
        }

    if delta_type in ("new", "removed"):
        period_tb_file = cy_canonical_tb_file if delta_type == "new" else py_canonical_tb_file
        rows, label = _delta_new_removed(structural_delta_file, delta_type, period_tb_file, materiality_file, materiality_tier, filter, limit)
    elif delta_type == "zero-movement":
        rows, label = _delta_zero_movement(comparison_variance_file, filter, limit)
    elif delta_type == "material-variance":
        rows, label = _delta_material_variance(comparison_variance_file, flag, filter, limit)
    else:
        rows, label = _delta_reclassified(py_canonical_tb_file, cy_canonical_tb_file, filter, limit)

    if not rows:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"No matching {label} found.",
            "data": None,
            "artifacts": [],
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(rows)} {label}.",
        "data": rows,
    }


_CATEGORIES = (
    "duplicate_gl_code", "duplicate_description", "sign_anomaly",
    "sensitive_keyword", "negative_balance", "dormant", "mapping_integrity",
)

_GENERIC_DESC_KEYWORDS = ("misc", "miscellaneous", "others", "other", "sundry", "temporary", "temp", "n/a", "na", "-", "tbd")

_DUP_DESC_RE = re.compile(r"GL '([^']+)' \(([^)]*)\) and GL '([^']+)' \(([^)]*)\)")

_EXPECTED_POSITIVE_KEYWORDS = ("inventory", "stock", "receivable", "debtor", "fixed asset", "property, plant", "ppe")

def _filter_rows(rows: list, needle: str, keys: tuple) -> list:
    if not needle:
        return rows
    n = needle.lower()
    return [r for r in rows if any(n in str(r.get(k, "") or "").lower() for k in keys)]

def _category_duplicate_gl_code(layer1_findings_file, filter_, limit):
    path = require_file(layer1_findings_file, "Layer-1 findings (layer1_findings.parquet)")
    df = pl.read_parquet(path)
    matches = df.filter(pl.col("rule_id").is_in(["TB-002", "TB-003"])) if "rule_id" in df.columns else df.head(0)
    rows = _filter_rows(matches.to_dicts(), filter_, ("gl_code", "gl_name"))
    # The label flows into both the "Found N result(s) for {label}" and "No matching results
    # for {label}" messages below -- state the scope explicitly in both cases, not just when
    # results exist, since a false "clean, checked" reading on a 0-result answer is the more
    # dangerous failure mode (same class as the N12 vendor-lookup guardrail gap).
    return rows[:limit], (
        "duplicate/blank GL code (TB-002/TB-003 -- flat GL-code uniqueness only; this "
        "schema has no cost-centre/division/circle dimension, so a duplicate check cannot "
        "be scoped by one)"
    )

def _category_duplicate_description(canonical_tb_file, anomaly_findings_file, filter_, limit):
    rows = []
    if anomaly_findings_file and Path(anomaly_findings_file).exists():
        with open(anomaly_findings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for finding in data.get("findings", []) or []:
            if finding.get("rule_id") != "TB-030":
                continue
            m = _DUP_DESC_RE.search(finding.get("description", ""))
            if m:
                rows.append({
                    "check": "fuzzy_duplicate_pair", "gl_code_a": m.group(1), "gl_name_a": m.group(2),
                    "gl_code_b": m.group(3), "gl_name_b": m.group(4), "similarity": finding.get("trigger_value"),
                })
            else:
                rows.append({"check": "fuzzy_duplicate_pair", "gl_code": finding.get("account"), "description": finding.get("description")})

    if canonical_tb_file and Path(canonical_tb_file).exists():
        df = pl.read_parquet(canonical_tb_file)
        if "gl_name" in df.columns:
            generic = df.filter(
                pl.col("gl_name").cast(pl.Utf8).str.strip_chars().str.to_lowercase().is_in(list(_GENERIC_DESC_KEYWORDS))
                | pl.col("gl_name").is_null()
                | (pl.col("gl_name").cast(pl.Utf8).str.strip_chars() == "")
            )
            for r in generic.select(["gl_code", "gl_name"]).to_dicts():
                rows.append({"check": "generic_or_blank_description", **r})

    rows = _filter_rows(rows, filter_, ("gl_code", "gl_name", "gl_code_a", "gl_code_b"))
    return rows[:limit], "duplicate/generic/blank description (TB-030 + keyword scan)"

def _category_sign_anomaly(layer1_results_file, filter_, limit):
    path = require_file(layer1_results_file, "Layer-1 results (layer1_results.json)")
    with open(path, "r", encoding="utf-8") as f:
        results = json.load(f)
    matches = [r for r in results if r.get("rule") == "TB-000"]
    return matches[:limit], "sign convention contrary to normal nature (TB-000)"

def _category_sensitive_keyword(sensitive_accounts_file, sensitive_category, filter_, limit):
    path = require_file(sensitive_accounts_file, "Sensitive accounts (sensitive_accounts.parquet)")
    df = pl.read_parquet(path)
    matches = df
    if sensitive_category and "category" in df.columns:
        matches = matches.filter(pl.col("category") == sensitive_category)
    rows = _filter_rows(matches.to_dicts(), filter_, ("gl_code", "gl_name", "account"))
    label = f"sensitive accounts (category={sensitive_category})" if sensitive_category else "sensitive accounts (all categories)"
    return rows[:limit], label

def _category_negative_balance(canonical_tb_file, filter_, limit):
    df = load_canonical_tb(canonical_tb_file)
    if df is None or df.is_empty() or "report_head" not in df.columns:
        return [], "negative balances in structurally-expected-positive accounts"
    needle_expr = pl.any_horizontal(
        [pl.col("gl_name").cast(pl.Utf8).str.to_lowercase().str.contains(kw, literal=True).fill_null(False) for kw in _EXPECTED_POSITIVE_KEYWORDS]
    )
    matches = df.filter(
        (pl.col("report_head") == "Assets") & needle_expr & (pl.col("closing_balance") < 0)
    )
    rows = _filter_rows(matches.select(["gl_code", "gl_name", "report_head", "report_sub", "closing_balance"]).to_dicts(), filter_, ("gl_code", "gl_name"))
    return rows[:limit], "negative balances in structurally-expected-positive accounts (inventory/receivables/fixed assets)"

def _category_dormant(canonical_tb_file, filter_, limit):
    df = load_canonical_tb(canonical_tb_file)
    rows = dormant_rows_from_canonical(df, limit=max(limit, 50))
    rows = _filter_rows(rows, filter_, ("gl_code", "gl_name"))
    return rows[:limit], "dormant accounts (zero period movement, non-zero closing balance)"

def _category_mapping_integrity(grouping_statistics_file, filter_, limit):
    path = require_file(grouping_statistics_file, "Grouping statistics (grouping_statistics.json)")
    with open(path, "r", encoding="utf-8") as f:
        stats = json.load(f)
    dup_count = stats.get("duplicate_gls", 0)
    row = {
        "duplicate_gls_collapsed": dup_count,
        "note": (
            "grouping_ground_truth.parquet enforces one row per gl_code by construction, so a GL "
            "code cannot map to more than one FS line item in this artifact. This count is how many "
            "GL codes had multiple candidate mappings during grouping and were collapsed to the "
            "first match -- the closest available signal to a 1:many mapping concern."
            if dup_count else
            "No GL codes had multiple candidate mappings collapsed during grouping."
        ),
    }
    return [row], "GL-to-FS-line-item mapping integrity"

@pipeline_tool("chat_query_flags", domain="chat")
@chat_cacheable
def chat_query_flags(
    category: str,
    canonical_tb_file: str = None,
    layer1_findings_file: str = None,
    layer1_results_file: str = None,
    anomaly_findings_file: str = None,
    sensitive_accounts_file: str = None,
    grouping_statistics_file: str = None,
    sensitive_category: str = None,
    filter: str = None,
    limit: int = 20,
) -> dict:
    """Look up which accounts triggered a rule/flag check. `category` determines which file
    param(s) you must supply -- pass only what's listed, other file params are ignored:
    'duplicate_gl_code' -- requires `layer1_findings_file` (NOT layer1_results_file --
    that's the aggregate rule-level file, this needs the per-row findings file).
    'duplicate_description' -- requires `anomaly_findings_file`; optionally add
    `canonical_tb_file` too for the generic-term (Misc/Others/Sundry) keyword scan.
    'sign_anomaly' -- requires `layer1_results_file` (NOT layer1_findings_file -- TB-000 is
    an aggregate-only check, it never appears in the per-row findings file).
    'sensitive_keyword' -- requires `sensitive_accounts_file`; optionally narrow with
    `sensitive_category` (e.g. related_party/grant_subsidy/prior_period_item).
    'negative_balance' or 'dormant' -- requires `canonical_tb_file`.
    'mapping_integrity' -- requires `grouping_statistics_file`.
    `filter` narrows results by a case-insensitive substring on GL code/name."""
    if category not in _CATEGORIES:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"Unknown category {category!r}. Valid categories: {', '.join(_CATEGORIES)}.",
            "data": None,
            "artifacts": [],
        }

    dispatch = {
        "duplicate_gl_code": lambda: _category_duplicate_gl_code(layer1_findings_file, filter, limit),
        "duplicate_description": lambda: _category_duplicate_description(canonical_tb_file, anomaly_findings_file, filter, limit),
        "sign_anomaly": lambda: _category_sign_anomaly(layer1_results_file, filter, limit),
        "sensitive_keyword": lambda: _category_sensitive_keyword(sensitive_accounts_file, sensitive_category, filter, limit),
        "negative_balance": lambda: _category_negative_balance(canonical_tb_file, filter, limit),
        "dormant": lambda: _category_dormant(canonical_tb_file, filter, limit),
        "mapping_integrity": lambda: _category_mapping_integrity(grouping_statistics_file, filter, limit),
    }

    rows, label = dispatch[category]()

    if not rows:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"No matching results for {label}.",
            "data": None,
            "artifacts": [],
        }

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(rows)} result(s) for {label}.",
        "data": rows,
    }


_METRICS = ("balance", "pct_of_total", "count", "control_totals", "common_size")

_GL_FILTER_COLUMNS = ["report_head", "report_sub", "main_head", "sub_head_1", "sub_head_2", "account_type", "bs_pl", "gl_name"]

_FSLI_FILTER_COLUMNS = ["main_head", "sub_head_1", "sub_head_2", "node_name", "hierarchy_path"]

_COMMON_SIZE_FILTER_COLUMNS = ["main_head", "sub_head_1", "sub_head_2", "node_name", "hierarchy_path"]

_COMMON_SIZE_JOIN_KEYS = ["main_head", "sub_head_1", "sub_head_2"]

def _apply_text_filter__chat_query_fsli_table(df: pl.DataFrame, needle: str, candidate_cols: list) -> pl.DataFrame:
    if not needle:
        return df
    present = [c for c in candidate_cols if c in df.columns]
    if not present:
        return df
    n = needle.lower()
    mask = pl.any_horizontal(
        [pl.col(c).cast(pl.Utf8).str.to_lowercase().str.contains(n, literal=True).fill_null(False) for c in present]
    )
    return df.filter(mask)

def _sort_top_n(df: pl.DataFrame, by: str, sort: str, top_n) -> pl.DataFrame:
    if by in df.columns:
        df = df.sort(by, descending=(sort != "asc"))
    if top_n:
        df = df.head(int(top_n))
    return df

def _metric_count(df: pl.DataFrame, group_by):
    known_cols = set(df.columns)
    requested = [group_by] if group_by else ["gl_code", "main_head", "sub_head_1", "sub_head_2", "account_type"]
    rows = []
    for col in requested:
        if col in known_cols:
            rows.append({"dimension": col, "distinct_count": int(df[col].n_unique())})
        else:
            rows.append({"dimension": col, "distinct_count": None, "note": f"Column {col!r} does not exist in the canonical TB schema."})
    for missing_dim in ("cost_centre", "circle", "division"):
        if group_by in (None, missing_dim) and missing_dim not in known_cols:
            rows.append({"dimension": missing_dim, "distinct_count": None, "note": f"Column {missing_dim!r} does not exist in the canonical TB schema — not tracked at GL grain."})
    return rows

def _metric_balance_or_pct(df: pl.DataFrame, metric, group_by, basis, top_n, sort):
    value_col = "closing_balance"
    head_totals = {}
    if "report_head" in df.columns:
        head_totals = dict(
            df.group_by("report_head").agg(pl.col(value_col).abs().sum().alias("total")).iter_rows()
        )
    basis_total = head_totals.get(basis.strip().title()) if basis else None

    if group_by and group_by in df.columns:
        agg = df.group_by(group_by).agg([
            pl.col(value_col).sum().alias(value_col),
            pl.first("report_head").alias("report_head") if "report_head" in df.columns else pl.lit(None).alias("report_head"),
            pl.len().alias("gl_count"),
        ])
        rows = agg.to_dicts()
    else:
        keep_cols = [c for c in ("gl_code", "gl_name", "report_head", "report_sub", value_col) if c in df.columns]
        rows = df.select(keep_cols).to_dicts()

    if metric == "pct_of_total":
        for r in rows:
            denom = basis_total if basis_total else head_totals.get(str(r.get("report_head") or ""), 0.0)
            r["pct_of_total"] = round(abs(r.get(value_col, 0.0)) / denom * 100, 4) if denom else None
        rows.sort(key=lambda r: (r.get("pct_of_total") is None, -(r.get("pct_of_total") or 0.0)) if sort != "asc" else (r.get("pct_of_total") is None, r.get("pct_of_total") or 0.0))
    else:
        rows.sort(key=lambda r: abs(r.get(value_col, 0.0)), reverse=(sort != "asc"))

    if top_n:
        rows = rows[:int(top_n)]
    return rows

def _metric_control_totals(df: pl.DataFrame):
    totals = control_totals(df)
    return [totals] if totals else []

def _metric_common_size(snapshot_drilldown_file, py_snapshot_drilldown_file, filter_, top_n, sort):
    path = require_file(snapshot_drilldown_file, "Financial snapshot drilldown (snapshot_drilldown.parquet)")
    df = pl.read_parquet(path)
    df = _apply_text_filter__chat_query_fsli_table(df, filter_, _COMMON_SIZE_FILTER_COLUMNS)
    keep_cols = [c for c in ("main_head", "sub_head_1", "sub_head_2", "node_name", "hierarchy_level", "closing_balance", "percent_of_parent", "percent_of_main_head") if c in df.columns]
    df = df.select(keep_cols)

    if py_snapshot_drilldown_file and Path(py_snapshot_drilldown_file).exists():
        py_df = pl.read_parquet(py_snapshot_drilldown_file)
        join_keys = [c for c in _COMMON_SIZE_JOIN_KEYS if c in df.columns and c in py_df.columns]
        if join_keys and "percent_of_main_head" in py_df.columns:
            # fill_null before joining: polars (SQL semantics) never matches null==null, so a
            # node whose sub_head_2 is unset on both sides would otherwise silently fail to join.
            df = df.with_columns([pl.col(k).cast(pl.Utf8).fill_null("") for k in join_keys])
            py_slim = py_df.select(join_keys + ["percent_of_main_head"]).with_columns(
                [pl.col(k).cast(pl.Utf8).fill_null("") for k in join_keys]
            ).rename({"percent_of_main_head": "percent_of_main_head_py"})
            df = df.join(py_slim, on=join_keys, how="left")
            df = df.with_columns(
                (pl.col("percent_of_main_head") - pl.col("percent_of_main_head_py")).alias("percent_of_main_head_change")
            )

    df = _sort_top_n(df, "percent_of_main_head", sort, top_n)
    return df.to_dicts()

@pipeline_tool("chat_query_fsli_table", domain="chat")
@chat_cacheable
def chat_query_fsli_table(
    metric: str = "balance",
    level: str = "gl",
    filter: str = None,
    group_by: str = None,
    basis: str = None,
    top_n: int = None,
    sort: str = "desc",
    canonical_tb_file: str = None,
    fsli_summary_file: str = None,
    snapshot_drilldown_file: str = None,
    py_snapshot_drilldown_file: str = None,
) -> dict:
    """Slice/aggregate the canonical TB (level='gl') or FSLI hierarchy (level='fsli_node')
    by a filter + metric + grouping combination. metric: 'balance' (raw amounts, optionally
    top_n/sort), 'pct_of_total' (% of the matched rows' own report_head total, or of `basis`
    if given, e.g. basis='Assets'), 'count' (distinct-value counts per column, or per
    `group_by` if given), 'control_totals' (sum debit/credit/difference), or 'common_size'
    (reads snapshot_drilldown.parquet's real %-of-parent/%-of-main_head figures; supply
    py_snapshot_drilldown_file too for a PY-vs-CY common-size change). `filter` is a plain
    case-insensitive substring matched against FSLI/account-type/name columns -- it is NOT a
    query language, never pass a SQL-like expression (e.g. "main_head = 'Expenses'"); pass
    just the word itself (e.g. "Expenses"). A gl_name substring match (e.g. filter="vendor")
    only finds GL accounts whose NAME happens to contain that word -- there is no vendor,
    cost-centre, division, or circle dimension in this schema, so a match like that is never
    real vendor/cost-centre-level data. Never present such a match as if it answered a
    vendor/cost-centre/division question; state plainly that dimension does not exist here."""
    if metric not in _METRICS:
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"Unknown metric {metric!r}. Valid metrics: {', '.join(_METRICS)}.",
            "data": None,
            "artifacts": [],
        }

    if metric == "common_size":
        rows = _metric_common_size(snapshot_drilldown_file, py_snapshot_drilldown_file, filter, top_n, sort)
        label = "common-size %"
    elif level == "fsli_node":
        path = require_file(fsli_summary_file, "FSLI summary (fsli_summary.parquet)")
        df = _apply_text_filter__chat_query_fsli_table(pl.read_parquet(path), filter, _FSLI_FILTER_COLUMNS)
        df = _sort_top_n(df, "closing_balance", sort, top_n)
        rows = df.to_dicts()
        label = "FSLI node(s)"
    else:
        df = load_canonical_tb(require_file(canonical_tb_file, "Canonical TB (canonical_tb.parquet)"))
        if df is None:
            df = pl.DataFrame()
        df = _apply_text_filter__chat_query_fsli_table(df, filter, _GL_FILTER_COLUMNS)
        if metric == "count":
            rows = _metric_count(df, group_by)
        elif metric == "control_totals":
            rows = _metric_control_totals(df)
        else:
            rows = _metric_balance_or_pct(df, metric, group_by, basis, top_n, sort)
        label = f"GL row(s) [{metric}]"

    if not rows:
        hint = (
            " filter is a plain substring, not a query expression -- retry with just the "
            "bare word/phrase (e.g. \"Expenses\", not \"main_head = 'Expenses'\")."
            if filter and any(ch in filter for ch in "='\"%<>")
            else ""
        )
        return {
            "execution_status": "FAILED",
            "pipeline_status": "WARNING",
            "message": f"No matching {label} found for filter={filter!r}.{hint}",
            "data": None,
            "artifacts": [],
        }

    response = {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "artifacts": [],
        "message": f"Found {len(rows)} {label}.",
        "data": rows,
    }
    if metric == "pct_of_total" and top_n:
        pct_sum = sum(r.get("pct_of_total") or 0.0 for r in rows)
        response["combined_share_pct"] = round(pct_sum, 4)
    return response




