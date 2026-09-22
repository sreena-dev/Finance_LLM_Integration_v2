import re
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

# Wave 2 Fix 3 (revised after a live re-triage caught the gap): a real EPIL run showed
# the -R/-P/-M clearing token appears in at least four distinct shapes across the same
# TB -- trailing, hyphen-delimited ("SBI- MUSCAT (US$) -R"), mid-string, hyphen-
# delimited ("UBI-R-002-FLC Sorana"), space-delimited with NO hyphen before the token at
# all ("IOB-R 2471", "CANARA BANK R"), and a letter glued directly onto a digit string
# with no delimiter ("IOB-189002000000295P"). A hyphen-only split (the original
# implementation) caught only the first two and left ~Rs 1,278cr of genuine pairs
# ungrouped on a live run. Normalizing hyphens to spaces before tokenizing, then
# removing exactly one standalone R/P/M token (any position), covers the first three
# shapes in one pass; the digit-glued-letter shape needs a separate regex transform on
# each token.
_CLEARING_TOKENS = {"R", "P", "M"}
_GLUED_TRAILING_LETTER_RE = re.compile(r"^(\d+)[RPM]$", re.IGNORECASE)

_MIN_PAIR_SIZE = 2


def _strip_clearing_suffix(gl_name: str) -> str:
    raw_tokens = str(gl_name or "").replace("-", " ").split()
    found = False
    out_tokens = []
    for tok in raw_tokens:
        if tok.upper() in _CLEARING_TOKENS:
            found = True
            continue
        m = _GLUED_TRAILING_LETTER_RE.match(tok)
        if m:
            found = True
            out_tokens.append(m.group(1))
            continue
        out_tokens.append(tok)
    if not found:
        return ""
    return " ".join(out_tokens).strip()


@pipeline_tool("build_netting_screen", domain="netting")
def build_netting_screen(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Detects -R/-P/-M clearing-suffix pairs (e.g. "SBI-MUSCAT(US$)-R" / "...-P") and
    computes each pair's NET balance -- the client's complaint was that such pairs are
    reported gross, inflating Cash and Bank from a true ~Rs 700cr to a reported
    ~Rs 52,629cr. Never nets an account that stands alone (same stripped name but no
    genuine second leg) -- requires >=2 rows under the same stripped name with opposite-
    sign closing balances before treating a group as a real clearing pair.

    Writes netting_screen.json (one entry per detected pair) and netted_balances.parquet
    (gl_code -> net_closing_balance, for a cheap downstream left-join -- see
    build_going_concern_screen's netting_file parameter)."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no clearing pairs to net.",
            "artifacts": [], "errors": [],
        }

    groups = {}
    for row in tb_df.iter_rows(named=True):
        gl_name = row.get("gl_name") or ""
        stripped = _strip_clearing_suffix(gl_name)
        if not stripped:
            continue
        try:
            balance = float(row.get("closing_balance") or 0.0)
        except (TypeError, ValueError):
            continue
        groups.setdefault(stripped, []).append({
            "gl_code": str(row.get("gl_code") or ""),
            "gl_name": gl_name,
            "closing_balance": balance,
        })

    pairs = []
    netted_rows = []
    for stripped_name, members in groups.items():
        if len(members) < _MIN_PAIR_SIZE:
            continue
        has_positive = any(m["closing_balance"] > 0 for m in members)
        has_negative = any(m["closing_balance"] < 0 for m in members)
        if not (has_positive and has_negative):
            # Same stripped name, same sign on every leg -- not a receipt/payment
            # clearing pair, just coincidentally-similar account names.
            continue

        net_balance = sum(m["closing_balance"] for m in members)
        pairs.append({
            "stripped_name": stripped_name,
            "gl_codes": [m["gl_code"] for m in members],
            "gross_balances": [round(m["closing_balance"], 2) for m in members],
            "net_balance": round(net_balance, 2),
            "suffix_pattern_matched": True,
        })
        # Only the FIRST leg carries the net balance; every other leg is zeroed. A
        # consumer that SUMS net_closing_balance across the whole TB (e.g. a cash total)
        # then gets the correct net figure without double-counting both legs -- a
        # consumer that looks up one specific gl_code still finds the pair's net exposure
        # on whichever leg it asks about only if it asks about the first; downstream
        # consumers needing per-leg detail should read netting_screen.json's "pairs"
        # directly instead.
        for i, m in enumerate(members):
            netted_rows.append({
                "gl_code": m["gl_code"],
                "net_closing_balance": round(net_balance, 2) if i == 0 else 0.0,
            })

    json_path = out_dir / "netting_screen.json"
    payload = {
        "methodology": (
            "Removes exactly one standalone R/P/M clearing token from gl_name (hyphen- or "
            "space-delimited, any position) plus a letter glued directly onto a trailing "
            "digit string, then groups by the remaining name; a group only counts as a "
            "genuine clearing pair when it has >=2 legs with opposite-sign closing "
            "balances. Presence-only or same-sign groups are left untouched -- this is a "
            "detection screen, not a general netting rule. Known residual gap: a pair "
            "sharing an IDENTICAL name with no R/P/M marker at all (e.g. two ledgers both "
            "literally named 'IOB 040802000002289') is not detected -- matching purely on "
            "identical name plus opposing sign, with no suffix requirement at all, was "
            "assessed as too high a false-positive risk to add without a case actually "
            "observed to justify it."
        ),
        "pairs_detected": len(pairs),
        "pairs": pairs,
    }
    import json as _json
    with atomic_write(json_path) as tmp:
        Path(tmp).write_text(_json.dumps(payload, indent=2, default=str), encoding="utf-8")

    artifacts = [str(json_path.resolve())]
    if netted_rows:
        parquet_path = out_dir / "netted_balances.parquet"
        write_parquet_atomic(pl.DataFrame(netted_rows), parquet_path)
        artifacts.append(str(parquet_path.resolve()))

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Detected {len(pairs)} -R/-P/-M clearing pair(s) across {len(groups)} suffix-matched name group(s).",
        "artifacts": artifacts,
        "errors": [],
    }
