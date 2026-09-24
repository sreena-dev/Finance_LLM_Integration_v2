"""The Business Profile — §5's interpretive lens, grounded and generated.

WHERE THIS SITS
----------------
Every diagnostic in this report reads through a business understanding formed
BEFORE any ratio is interpreted (§2.1) — the profile is what forms it. This is
the one place in the whole mode where a model writes prose about the entity's
substance rather than reporting a computed figure, because business-model
classification is judgement over messy text, not arithmetic (§5.1).

That does not mean the model may say what it likes. Two disciplines, from the
same playbook as `disclosure.py`:

  GROUNDED FIGURES ARE COMPUTED, NEVER GENERATED. Total equity, borrowings,
  depreciation — every number in the profile is read straight off the panel,
  the same trusted figures the rest of the report uses, and handed to the
  model as an AUTHORITATIVE block it may quote but never recompute or invent
  (the same contract `ratio_pipeline.computed_block` already uses for the
  narrative-generation path elsewhere in this codebase).

  QUALITATIVE CLAIMS ARE RETRIEVED, NEVER RECALLED. What the entity actually
  does, how it prices, what its segments are — every one of those sentences
  must trace to a passage retrieved from THIS entity's OWN filing. §18.2 is
  explicit: the model must not import outside information about a named
  entity from memory. A well-known PSU's business is not common knowledge
  here; it is either in the filing or it is not stated.

THE SIX FIELDS
--------------
Model, Revenue, Cost, Financing, Value drivers, Inherent-risk map — §5.1's
business-model classification, §5.2's revenue/cost/financing structure and
value drivers, and §5.3's sector inherent-risk expectations, in one card. A
field with nothing to ground it says so; it is never left silently blank,
which would read as "nothing to say" rather than "not found".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from . import clients
from . import config as CFG
from . import retrieval as RET
from . import synthesis as SYN

logger = logging.getLogger(__name__)

VERSION = "fdr-business-profile-1.0.0"

FIELDS: tuple[str, ...] = ("model", "revenue", "cost", "financing",
                          "value_drivers", "inherent_risk_map")
FIELD_LABEL: dict[str, str] = {
    "model": "Model", "revenue": "Revenue", "cost": "Cost",
    "financing": "Financing", "value_drivers": "Value drivers",
    "inherent_risk_map": "Inherent-risk map",
}
_HEADER_RE = {f: re.compile(rf"^{re.escape(FIELD_LABEL[f])}\s*:\s*", re.I)
             for f in FIELDS}

# Fixed retrieval phrasings, not the user's words — this block runs once per
# entity with no question behind it. Grouped by the field(s) each grounds;
# VALUE DRIVERS and the risk map share a query because both read the same
# operational narrative (segment notes, MD&A).
#
# Phrased as NATURAL QUESTIONS, not keyword strings — measured against the live
# reranker (a cross-encoder, `BAAI/bge-reranker-v2-m3`), not assumed. A
# keyword-soup phrasing ("principal business activities nature of operations
# corporate information incorporated") retrieved 24 candidates for ONGC and
# kept ZERO of them — every one scored below the relevance floor, because a
# cross-encoder trained on query-passage pairs reads a keyword string as an
# entirely different kind of thing than the questions it was tuned on. The
# same retrieval against a genuine question ("What does the company do and
# what business is it engaged in?") kept 4, at a strongly positive score.
# Verified per query below against a live entity before being fixed here —
# these are not a guess.
_QUERIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("model",),
     "What does the company do and what business is it engaged in?"),
    (("revenue",),
     "How does the company earn its revenue and how does it set prices for "
     "what it sells?"),
    (("cost", "inherent_risk_map"),
     "What are the significant accounting estimates and judgements used in "
     "preparing the financial statements?"),
    (("financing",),
     "How is the company funded — what is its capital structure of equity "
     "and borrowings?"),
    (("value_drivers",),
     "What is discussed in the management discussion and analysis about the "
     "business?"),
    (("inherent_risk_map",),
     "What are the major risks and uncertainties facing the company?"),
)

# The figures this profile is allowed to STATE as fact — every one read off the
# panel the rest of the report already trusts. `label` is what the prompt calls
# it; `keys` is summed for a composite (debt = long-term + short-term borrowings).
_GROUNDED: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Total equity", "total_equity", ("total_equity",)),
    ("Total borrowings", "total_borrowings",
     ("long_term_borrowings", "short_term_borrowings")),
    ("Net fixed assets / investment book", "net_fixed_assets", ("net_fixed_assets",)),
    ("Capital expenditure on PPE", "capex_ppe", ("capex_ppe",)),
    ("Depreciation, depletion and amortisation", "depreciation", ("depreciation",)),
    ("Revenue from operations", "revenue", ("revenue",)),
)

_SYSTEM = (
    "You are drafting the Business Profile section of an audit-planning report, "
    "for ONE named entity. This section exists so that every later diagnostic can "
    "be read against a real understanding of the business, per Section 5 of the "
    "specification you are following.\n\n"
    "You will be given two kinds of material, and NOTHING ELSE may inform your "
    "answer:\n"
    "- GROUNDED FIGURES: computed, verified numbers from the audited statements. "
    "You may QUOTE these exactly. You must NEVER recompute, adjust, round "
    "differently, or derive a number that is not given to you here.\n"
    "- RETRIEVED PASSAGES: numbered excerpts from THIS entity's own annual "
    "report. Every qualitative claim you make must be supported by one of these "
    "and cited [n]. If a passage does not exist for something, do not guess or "
    "recall it from general knowledge — say plainly that it is not stated in the "
    "material provided.\n\n"
    "Write exactly six fields, each starting on its own line with the exact "
    "label below, a colon, then one to three sentences:\n"
    "Model: what the entity does and how — business model, operating structure.\n"
    "Revenue: how revenue is earned and priced — market, tariff, administered, "
    "grant, fee, or a hybrid — citing the specific policy.\n"
    "Cost: the cost structure — what dominates it, citing the grounded figures "
    "where they are relevant.\n"
    "Financing: how the entity is funded — equity, debt, government support — "
    "citing the grounded figures for the balance and the mix.\n"
    "Value drivers: the handful of variables that actually explain this "
    "entity's results — volume, price, tariff, utilisation, reserves, and "
    "similar, specific to what the filing describes.\n"
    "Inherent-risk map: where risk is LIKELY TO CONCENTRATE given this business "
    "model — the kind of areas §5.3 asks for (valuation, estimates, "
    "dependency), not a conclusion about any specific figure.\n\n"
    "You are NEVER to use anything you may already know about this entity from "
    "training. If the retrieved passages do not cover a field, write that field "
    "as: \"Not stated in the material available.\" Do not issue an opinion, "
    "conclude misstatement, fraud, distress or inefficiency, or predict "
    "failure. Do not state a peer benchmark or sector norm unless it is in the "
    "passages with its own source. Confidence is never a fabricated number."
)


@dataclass
class Profile:
    fields: dict[str, str]                 # field -> prose, always all six keys
    citations: list[dict]                  # the Sources drawer, same shape as disclosure.py
    grounded: list[dict]                   # the figures actually offered, with their source
    doc_id: str
    fy: str
    formed: bool                           # False only when nothing could be retrieved at all
    reason: str = ""
    lint_applied: list[str] | None = None
    groundedness: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "formed": self.formed, "reason": self.reason,
            "fields": [{"key": k, "label": FIELD_LABEL[k], "text": self.fields.get(k, "")}
                      for k in FIELDS],
            "citations": self.citations, "grounded_figures": self.grounded,
            "doc_id": self.doc_id, "fy": self.fy,
            "groundedness": self.groundedness, "version": VERSION,
        }


def _grounded_figures(panel: Any, year: str) -> tuple[str, list[dict]]:
    """The authoritative figures block, and the citation each figure carries.

    Mirrors `ratio_pipeline.computed_block`'s contract on purpose: figures the
    model may quote, never derive. A composite (total borrowings) sums its legs
    itself, deterministically, the same arithmetic `headline.py` already trusts
    — it does not ask the model to add two numbers either.
    """
    lines: list[str] = []
    grounded: list[dict] = []
    for label, out_key, keys in _GROUNDED:
        cells = [panel.cell(k, year) for k in keys]
        if any(c is None for c in cells):
            continue
        total = sum(c.value for c in cells)
        lines.append(f"- {label}: {total:,.2f} (INR lakh)")
        grounded.append({
            "label": label, "key": out_key, "value": total,
            "sources": [{"canonical_key": k, "row_label": c.source_label,
                        "doc_id": c.doc_id, "table_id": c.table_id, "page": c.page}
                       for k, c in zip(keys, cells)],
        })
    block = ("GROUNDED FIGURES (" + year + ", INR lakh, from the audited statements):\n"
            + ("\n".join(lines) if lines else "(none bound for this entity)"))
    return block, grounded


def _retrieve_all(doc_id: str) -> tuple[dict[str, list[dict]], list[dict], dict[str, Any]]:
    """Every field's evidence, gathered once per fixed query. NO RERANKING.

    THE CROSS-ENCODER IS DELIBERATELY SKIPPED ON THIS PATH, AND HERE IS THE
    ARITHMETIC THAT DECIDED IT. `RET.gather` already fuses every retrieval arm
    (keyword, semantic, table-title, identity-anchor) by Reciprocal Rank
    Fusion and MMR-dedupes the result — `deduped` at the end of `gather()` is
    not a raw candidate dump, it is already ranked. `RR.rerank` refines that
    ranking with a cross-encoder, which is real quality — but it is a LOCAL
    model that claims every CPU core for its forward pass
    (`torch.set_num_threads(os.cpu_count())`, once, at load), so it cannot be
    parallelised away: six calls measured at 37.3s run one after another and
    36.7s run concurrently on six threads, the same core-seconds either way.
    Six gather calls, by contrast, are I/O and measured at 3.4s in parallel
    against 13.1s in sequence. Dropping rerank and keeping gather concurrent
    turns a ~41s retrieval stage into single-digit seconds, at the cost of the
    cross-encoder's extra precision — an explicit trade for this block, where
    the generation prompt and `SYN.check_groundedness` downstream are the
    backstop against a noisier top-k, not a reason to skip the trade.

    Returns (field -> its evidence list, the flat numbered source list every
    field cites into, the combined retrieval diagnostics).
    """
    from concurrent.futures import ThreadPoolExecutor

    top_k = CFG.load().max_evidence

    by_field: dict[str, list[dict]] = {f: [] for f in FIELDS}
    all_sources: list[dict] = []
    diag: dict[str, Any] = {"queries": []}
    seen_ids: set[str] = set()

    with ThreadPoolExecutor(max_workers=len(_QUERIES),
                            thread_name_prefix="fdr-bp-gather") as pool:
        # Submitted in `_QUERIES` order and collected in that same order (not
        # `as_completed`) so the result — and therefore every source's citation
        # number — is identical run to run whatever order the sockets actually
        # return in. Reproducibility (§16) is a property of the OUTPUT, not of
        # how fast any one query happened to answer.
        futures = [pool.submit(RET.gather, doc_id, query) for _, query in _QUERIES]
        gathered = [f.result() for f in futures]

    for (target_fields, query), (candidates, _report) in zip(_QUERIES, gathered):
        evidence = candidates[:top_k]
        diag["queries"].append({"query": query, "fields": list(target_fields),
                                "candidates": len(candidates), "kept": len(evidence)})
        for item in evidence:
            key = f"{item.get('kind')}:{item.get('id')}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            all_sources.append(item)
            index = len(all_sources)
            for f in target_fields:
                by_field[f].append({**item, "_source_index": index})
    return by_field, all_sources, diag


def _parse_fields(text: str) -> dict[str, str]:
    """Split the model's response into the six named fields.

    A field the model omitted, or answered outside the expected shape, is left
    empty rather than guessed at from surrounding text — an empty field is
    handled explicitly by the caller; a wrongly-merged one would not be.
    """
    lines = (text or "").splitlines()
    out: dict[str, list[str]] = {f: [] for f in FIELDS}
    current: str | None = None
    for line in lines:
        matched = next((f for f in FIELDS if _HEADER_RE[f].match(line.strip())), None)
        if matched:
            current = matched
            out[current].append(_HEADER_RE[current].sub("", line.strip(), count=1))
        elif current is not None and line.strip():
            out[current].append(line.strip())
    return {f: " ".join(v).strip() for f, v in out.items()}


def build(entity_id: str, panel: Any, year: str, *, doc_id: str = "", fy: str = "") -> Profile:
    """The whole profile: retrieve, ground, generate, validate.

    Never raises on an unreachable model or a silent filing — both are real
    states this report has to be able to say plainly, not crash on. `formed`
    is False only when retrieval found nothing at all to work from; a partial
    result (some fields grounded, others honestly "not stated") is still
    `formed`, because that partiality IS the honest answer for this entity.
    """
    resolved = RET.resolve_doc(entity_id, "principal business activities") \
        if not doc_id else {"doc_id": doc_id, "company": entity_id}
    if resolved is None:
        return Profile({}, [], [], "", fy, formed=False,
                       reason=f"No filings are held for '{entity_id}' in the corpus.")
    target_doc = resolved["doc_id"]
    target_fy = fy or RET.fy_label(resolved)

    grounded_block, grounded = _grounded_figures(panel, year)
    by_field, all_sources, retrieval_diag = _retrieve_all(target_doc)

    if not all_sources:
        return Profile({f: "Not stated in the material available." for f in FIELDS},
                       [], grounded, target_doc, target_fy, formed=bool(grounded),
                       reason="No passages in this filing matched any of the business-"
                              "profile queries; only the grounded figures are shown.")

    passages = "\n".join(
        f"[SOURCE {i}] ({'TABLE' if s.get('kind') == 'table' else 'TEXT'}) "
        f"{s.get('title') or 'source'}\n{(s.get('content') or '')[:1200]}\n"
        for i, s in enumerate(all_sources, 1))
    prompt = (f"ENTITY: {entity_id.replace('_', ' ')}\n\n{grounded_block}\n\n"
             f"RETRIEVED PASSAGES FROM THIS ENTITY'S OWN {target_fy} FILING:\n{passages}")

    try:
        raw = clients.chat([{"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": prompt}], temperature=0.0)
    except clients.EndpointError as exc:
        return Profile({}, [], grounded, target_doc, target_fy, formed=False,
                       reason=f"The generation endpoint is unavailable ({exc.detail}), so "
                              f"the business profile could not be drafted this run. The "
                              f"grounded figures below are unaffected — they need no model.")

    text, invalid = SYN.strip_invalid_citations(raw, len(all_sources))
    text, lint_applied = SYN.lint(text)
    fields = _parse_fields(text)

    cited = SYN.cited_indices(text)
    citations = SYN.sources_payload(all_sources, {"doc_id": target_doc, "company": entity_id,
                                                   "fy_end": None}, cited)
    for c in citations:
        c["fy"] = target_fy

    groundedness = SYN.check_groundedness(
        "the business profile for " + entity_id, text, all_sources)

    missing = [f for f in FIELDS if not fields.get(f)]
    for f in missing:
        fields[f] = "Not stated in the material available."

    reason = ""
    if invalid:
        reason = (f"{len(invalid)} citation(s) pointed at sources never supplied and were "
                  f"removed.")
    if missing:
        reason += (f" {len(missing)} field(s) had no passage to ground them: "
                   f"{', '.join(FIELD_LABEL[f] for f in missing)}.").strip()

    return Profile(fields, citations, grounded, target_doc, target_fy, formed=True,
                   reason=reason.strip(), lint_applied=lint_applied or None,
                   groundedness=groundedness)
