"""
LLM row PROPOSER (Layer 2b) — prompt, payload and response shape. Transport injected.

WHAT THIS ADDRESSES, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------------
The rule layer misses a line when a filing names it something no pattern anticipated.
That is the ONE failure mode a language model is genuinely better at than a regex, and
it is the only one this module is allowed to touch. Alignment, sign, hierarchy and scale
defects — which is what the corpus's documented failures actually are — stay with the
deterministic layers, where they can be seen and proven.

IT NEVER READS A FIGURE
-----------------------
The model is shown row LABELS and returns a ROW INDEX. The caller reads
`row.values[period]` itself. Three consequences, all load-bearing:

  * a figure cannot be corrupted in transit — no transcription, sign, comma or scale
    error is REPRESENTABLE here, let alone likely;
  * provenance (label, role, section, table, page) is recovered from the row, so an
    LLM-sourced fact is exactly as traceable as a rule-bound one, which is the whole
    requirement `numeric_guard` enforces on the answer side;
  * withholding the amounts means the model cannot see which choice would make the
    balance sheet tie, so it cannot fit its answer to the identity that judges it.
    Together with the no-retry rule in `Resolver._llm_pass`, that closes the resample-
    until-it-passes loop which `BindingReport.spent_checks` exists to prevent elsewhere.

THE PROMPT LIVES HERE, NOT IN THE TRANSPORT ADAPTER
---------------------------------------------------
`facts.pipeline_fingerprint()` content-hashes the modules that determine what a fact IS,
so a cached extraction is bypassed whenever extraction logic changes. A prompt determines
what a fact is every bit as much as a regex does. Left in the adapter it would sit outside
that hash, and editing it would silently change every extracted figure while every cached
fact still looked current — the exact invalidation bug the fingerprint was built to make
impossible. So: prompt here (this file is in `_PIPELINE_MODULES`), transport injected.

Stdlib-only. Imports nothing from `rag/`.
"""
from __future__ import annotations
import json
import re
from typing import Callable

ProposeFn = Callable[[str], str]          # prompt -> raw model text

PROMPT_VERSION = "llm-bind-1"

# The ONLY abstention codes a proposal may be attempted for. Every other code in
# binding.py names a defect a model would MASK rather than fix:
#
#   ROLE_MISMATCH / VALUE_NULL / HIERARCHY_UNRESOLVED
#       a label DID match. The gap is in the role classifier or the hierarchy reader,
#       and papering over it hides a parser bug that affects every other filing too.
#   AMBIGUOUS_UNSCOPED / SECTION_CRITICAL
#       a sibling spec claims the same pattern (`^borrowings\b` is both long- and
#       short-term). Here a wrong pick is not a miss — it is a WRONG NUMBER silently
#       bound to the wrong key, feeding the funding cluster. Never offered.
#   NO_SECTION_TAGGED / SECTION_EMPTY
#       scoping already failed, so there is no section left to constrain the choice.
#       A guess stacked on a structural failure has nothing holding it down.
ELIGIBLE_REASONS = frozenset({"PATTERN_NO_MATCH", "SEMANTIC_BELOW_THRESHOLD"})

_INSTRUCTIONS = """You map financial-statement line items to canonical keys.

You are given the rows of one {kind} statement — index, printed label, row role and
section only — and a list of canonical keys that need a row.

For each key, choose the ONE row whose label denotes that concept, or null.

Rules:
- Return the row INDEX. Never return an amount, a number, or a computed value.
- If no row denotes the key, return null. A wrong row is far worse than null.
- Never choose the same row for two different keys.
- Judge the printed label only. You are not shown amounts and must not infer them.
- Indian filings use Schedule III wording; match the concept, not the exact words.

Reply with JSON only, no prose, no code fence:
{{"proposals":[{{"key":"<key>","row":<index or null>,"confidence":"high|low","why":"<max 8 words>"}}]}}
"""

_JSON_RE = re.compile(r"\{.*\}", re.S)


def build_prompt(kind: str, keys: list[dict], rows: list[dict]) -> str:
    """One prompt for a whole statement — not one per key.

    Batching is not only cheaper: seeing every gap at once is what lets the model honour
    "never choose the same row for two keys", which per-key calls cannot express.
    """
    out = [_INSTRUCTIONS.format(kind=kind), "ROWS:"]
    for r in rows:
        sec = f"  <{r['section']}>" if r.get("section") else ""
        out.append(f"  {r['idx']}: {r['label']}  [{r['role']}]{sec}")
    out += ["", "KEYS:"]
    for k in keys:
        out.append(f"  {k['key']}: {k['concept']}")
    return "\n".join(out)


def parse_proposals(raw: str, valid_keys: set[str], n_rows: int) -> list[dict]:
    """Shape-validate the reply. Malformed entries are DROPPED, never repaired.

    A proposal that does not parse is evidence the model did not understand the table;
    reconstructing what it "meant" invents an intent that was never expressed, and the
    whole point of this layer is that its output be judged rather than trusted.
    """
    if not raw:
        return []
    m = _JSON_RE.search(raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for p in data.get("proposals") or []:
        if not isinstance(p, dict):
            continue
        key, row = p.get("key"), p.get("row")
        if key not in valid_keys or key in seen:
            continue
        # `isinstance(True, int)` is True in Python, so a JSON `true` would otherwise be
        # accepted as row 1 — a silent bind to whatever happens to sit there.
        if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row < n_rows:
            continue                       # null, out of range, or "12" as a string
        seen.add(key)
        out.append({"key": key, "row": row,
                    "confidence": "high" if p.get("confidence") == "high" else "low",
                    "why": str(p.get("why") or "")[:60]})
    return out
