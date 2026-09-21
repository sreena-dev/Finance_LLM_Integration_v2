"""Step 4: ambiguous-vocabulary staged narrowing.

True staged narrowing (main_head -> sub_head_1 -> sub_head_2 in separate
calls, each stage shown only the current branch's own options) for rows
whose vocabulary has proven unreliable under the cheap single-call path in
classification.py. Cross-branch contamination is structurally impossible
here -- the model never sees another branch's options at any stage.

Ported from TB_normalization_v1's core/staged_narrowing.py.
"""

from __future__ import annotations

import json
import logging

from modes.trial_balance.pipeline.tools._shared import Standard
from modes.trial_balance.pipeline.tools.classify_examples import STAGE1_FEW_SHOT
from modes.trial_balance.pipeline.tools.llm_call import read_content
from modes.trial_balance.pipeline.tools.taxonomy_repository import build_taxonomy_tree, derive_account_type, snap_to_taxonomy
from modes.trial_balance.pipeline.tools.tb_models import MatchResult

logger = logging.getLogger(__name__)

STAGE1_SYSTEM_PROMPT = """You are a financial audit assistant classifying General Ledger \
(GL) accounts into a closed Schedule III taxonomy (Companies Act 2013). This is STAGE 1 of \
a 3-stage classification: you are ONLY picking the main_head (and its bs_pl) for each \
account -- sub_head_1 and sub_head_2 are decided in later stages you will not see.

You MUST choose ONLY from the main_head options given below -- copy main_head and bs_pl \
VERBATIM from one option, never invent new wording. Use the GL name and, if given, a \
"hint" (a label from the company's own grouping workbook) as evidence.

IMPORTANT: every account you see here was routed to this stage specifically because its GL \
name contains wording that reliably signals liability nature on its own -- "payable" (e.g. \
"Security Deposit Payable", "Payable to X"), an abbreviated "Liab" (e.g. "Deferred IT Liab"), \
"EMD [Payable]", or "retention ... pay[able]". The "hint" is pulled from a section label in \
the company's own workbook and is occasionally filed under the wrong section (e.g. a payable \
account mistakenly placed under an "Other current assets" heading). When the hint conflicts \
with what one of these specific words says the account is, trust the GL name's own wording \
over the hint for that word.

If nothing plausibly fits, use null for main_head and bs_pl -- never force a weak guess.

Respond with ONLY a JSON array, one object per input row, in any order. Each object MUST \
include "gl_code" copied VERBATIM from the input row -- this is how your answer gets \
matched back to the right row. Keys: gl_code, main_head, bs_pl. No prose, no markdown \
fences."""

STAGE2_SYSTEM_PROMPT = """You are STAGE 2 of a 3-stage Schedule III classification. Every \
row below has ALREADY been assigned a main_head (not shown to you) -- you are ONLY picking \
the sub_head_1 for each account, choosing EXCLUSIVELY from the options list given below \
(every option in that list already belongs to the row's chosen main_head). Copy sub_head_1 \
VERBATIM from that list -- never invent new wording, never pick anything not in the list. \
If nothing plausibly fits, use null -- never force a weak guess.

Respond with ONLY a JSON array, one object per input row, in any order. Each object MUST \
include "gl_code" copied VERBATIM from the input row. Keys: gl_code, sub_head_1. No prose, \
no markdown fences."""

STAGE3_SYSTEM_PROMPT = """You are STAGE 3 (final) of a 3-stage Schedule III classification. \
Every row below has ALREADY been assigned a main_head and sub_head_1 (not shown to you) -- \
you are ONLY picking sub_head_2, choosing EXCLUSIVELY from the options list given below \
(every option in that list already belongs to the row's chosen sub_head_1). Copy sub_head_2 \
VERBATIM from that list -- never invent new wording, never pick a value that "sounds right" \
but isn't literally in this list. If nothing fits well, prefer an "Other ..." item from this \
SAME list if one exists; if nothing plausibly fits at all, use null -- never force a weak \
guess.

Respond with ONLY a JSON array, one object per input row, in any order. Each object MUST \
include "gl_code" copied VERBATIM from the input row. Keys: gl_code, sub_head_2. No prose, \
no markdown fences."""


def _call_and_match(messages: list, llm_client) -> dict:
    """Calls the LLM and returns {gl_code: selection}, matched by the
    LLM-echoed gl_code -- never by position."""
    try:
        response = llm_client.generate(messages, max_tokens=2000, temperature=0)
        content = read_content(response)
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        result = json.loads(cleaned.strip())
    except Exception:
        return {}
    if not isinstance(result, list):
        return {}
    return {str(sel["gl_code"]): sel for sel in result if isinstance(sel, dict) and sel.get("gl_code")}


def _stage1_pick_main_head(rows: list, standard: Standard, batch_size: int, llm_client) -> dict:
    options = [{"main_head": n["main_head"], "bs_pl": n["bs_pl"]} for n in build_taxonomy_tree(standard)]
    few_shot = STAGE1_FEW_SHOT[standard]
    picks: dict = {}
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        input_rows = [{"gl_code": r.gl_code, "gl_name": r.gl_name, "hint": r.hint} for r in chunk]
        messages = [
            {"role": "system", "content": STAGE1_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"main_head options:\n{json.dumps(options)}\n\n"
                    f"Worked examples (selection mechanic, not exhaustive):\n{json.dumps(few_shot)}\n\n"
                    f"Classify these GL rows:\n{json.dumps(input_rows)}"
                ),
            },
        ]
        picks.update(_call_and_match(messages, llm_client))
    return picks


def _stage2_pick_sub_head_1(rows_by_main_head: dict, standard: Standard, batch_size: int, llm_client) -> dict:
    tree_by_main_head = {n["main_head"]: n for n in build_taxonomy_tree(standard)}
    picks: dict = {}
    for main_head, rows in rows_by_main_head.items():
        options = [s["sub_head_1"] for s in tree_by_main_head[main_head]["sub_heads"]]
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            input_rows = [{"gl_code": r.gl_code, "gl_name": r.gl_name, "hint": r.hint} for r in chunk]
            messages = [
                {"role": "system", "content": STAGE2_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"sub_head_1 options (all belong to main_head={main_head!r}):\n{json.dumps(options)}\n\n"
                        f"Classify these GL rows:\n{json.dumps(input_rows)}"
                    ),
                },
            ]
            picks.update(_call_and_match(messages, llm_client))
    return picks


def _stage3_pick_sub_head_2(rows_by_branch: dict, standard: Standard, batch_size: int, llm_client) -> dict:
    tree_by_main_head = {n["main_head"]: n for n in build_taxonomy_tree(standard)}
    picks: dict = {}
    for (main_head, sub_head_1), rows in rows_by_branch.items():
        sub_node = next(s for s in tree_by_main_head[main_head]["sub_heads"] if s["sub_head_1"] == sub_head_1)
        options = sub_node["sub_head_2_options"]
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            input_rows = [{"gl_code": r.gl_code, "gl_name": r.gl_name, "hint": r.hint} for r in chunk]
            messages = [
                {"role": "system", "content": STAGE3_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"sub_head_2 options (all belong to sub_head_1={sub_head_1!r}):\n{json.dumps(options)}\n\n"
                        f"Classify these GL rows:\n{json.dumps(input_rows)}"
                    ),
                },
            ]
            picks.update(_call_and_match(messages, llm_client))
    return picks


def classify_staged(rows: list, standard: Standard, llm_client, batch_size: int = 15) -> dict:
    """Returns {gl_code: MatchResult} for whichever rows resolved through
    all 3 stages to a real taxonomy entry -- a gl_code absent from the
    result stayed unresolved at some stage (never trusted through
    partially). Returns {} immediately if llm_client is None or rows is
    empty."""
    if not llm_client or not rows:
        return {}
    tree_by_main_head = {n["main_head"]: n for n in build_taxonomy_tree(standard)}

    stage1 = _stage1_pick_main_head(rows, standard, batch_size, llm_client)
    rows_by_main_head: dict = {}
    for row in rows:
        pick = stage1.get(row.gl_code)
        main_head = pick.get("main_head") if pick else None
        if not main_head or main_head not in tree_by_main_head:
            logger.warning("GL %s: staged narrowing stage 1 (main_head) failed: %r", row.gl_code, pick)
            continue
        rows_by_main_head.setdefault(main_head, []).append(row)

    stage2 = _stage2_pick_sub_head_1(rows_by_main_head, standard, batch_size, llm_client)
    rows_by_branch: dict = {}
    for main_head, group in rows_by_main_head.items():
        valid_sub_heads = {s["sub_head_1"] for s in tree_by_main_head[main_head]["sub_heads"]}
        for row in group:
            pick = stage2.get(row.gl_code)
            sub_head_1 = pick.get("sub_head_1") if pick else None
            if not sub_head_1 or sub_head_1 not in valid_sub_heads:
                logger.warning("GL %s: staged narrowing stage 2 (sub_head_1) failed: %r", row.gl_code, pick)
                continue
            rows_by_branch.setdefault((main_head, sub_head_1), []).append(row)

    stage3 = _stage3_pick_sub_head_2(rows_by_branch, standard, batch_size, llm_client)
    results: dict = {}
    for (main_head, sub_head_1), group in rows_by_branch.items():
        for row in group:
            pick = stage3.get(row.gl_code)
            sub_head_2 = pick.get("sub_head_2") if pick else None
            if not sub_head_2:
                logger.warning("GL %s: staged narrowing stage 3 (sub_head_2) failed: %r", row.gl_code, pick)
                continue
            snapped = snap_to_taxonomy(None, main_head, sub_head_1, sub_head_2, standard)
            if snapped is None:
                logger.warning(
                    "GL %s: staged narrowing picked off-taxonomy sub_head_2 %r under sub_head_1 %r",
                    row.gl_code, sub_head_2, sub_head_1,
                )
                continue
            results[row.gl_code] = MatchResult(
                bs_pl=snapped["bs_pl"], main_head=snapped["main_head"], sub_head_1=snapped["sub_head_1"],
                sub_head_2=snapped["sub_head_2"], account_type=derive_account_type(snapped["bs_pl"], snapped["main_head"], standard),
            )
    return results
