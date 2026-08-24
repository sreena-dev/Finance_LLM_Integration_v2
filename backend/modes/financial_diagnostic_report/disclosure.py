"""Answer a question from the filing's own text: retrieve, rerank, generate, verify.

WHERE THIS SITS
---------------
This is the second of the mode's two answer paths, and the only one with a model
in it. It runs when the question is not about a diagnostic — an accounting
policy, the registered office, an auditor's remark, anything the statements did
not bind. The diagnostics never come through here: a signal verdict is computed
from a panel and is not something to be retrieved or narrated.

THE ORDER OF THE STAGES IS THE DESIGN
-------------------------------------
    resolve filing   which year is being asked about (entity comes from the picker)
    gather           several arms, wide, optimising for recall
    rerank           the cross-encoder, the only stage that judges relevance
    ABSTAIN GATE     nothing above threshold -> answer without calling the model
    generate         narrate the surviving sources, citing each claim
    validate         strip citations pointing at sources that do not exist
    verify           check the answer against the sources it was given

The abstain gate in the middle is the important one. A model handed an empty or
irrelevant source list will still produce fluent text; the way to prevent an
ungrounded answer is not to ask for one. So when the reranker keeps nothing, no
generation happens at all — and the response says what was searched and why it
came back empty.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from . import clients
from . import config as CFG
from . import expansion as EXP
from . import rerank as RR
from . import retrieval as RET
from . import synthesis as SYN

logger = logging.getLogger(__name__)


def _abstain(message: str, doc: dict | None, diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "abstained", "text": message, "sources": [],
            "provenance": _provenance(doc, diagnostics), "rows": []}


def _provenance(doc: dict | None, diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Everything needed to judge the answer, including what went wrong.

    `degraded` and the rerank report travel with the answer on purpose. A thin
    result caused by the embedding endpoint being down and a thin result caused
    by the filing genuinely not discussing the subject look identical in the
    prose, and the reader has to be able to tell them apart.
    """
    out: dict[str, Any] = {
        "source": "live:finance_llm",
        "path": "retrieval",
        **diagnostics,
    }
    if doc:
        out.update({
            "entity_id": doc.get("company"),
            "doc_id": doc.get("doc_id"),
            "fy": RET.fy_label(doc),
            "year_basis": doc.get("year_source"),
        })
    return out


def answer(entity_id: str, query: str, *,
           progress: Callable[[str, dict], None] | None = None,
           on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Answer one narrative question about one entity, with citations."""
    emit = progress or (lambda *_a, **_k: None)
    settings = CFG.load()
    started = time.perf_counter()

    if not settings.retrieval_configured:
        raise clients.EndpointError(
            "retrieval",
            "no embedding or generation endpoint configured, so questions outside the "
            "diagnostics cannot be answered. Set EMBEDDING_BASE_URL and "
            "GENERATION_BASE_URL.")

    emit("resolve", {"entity_id": entity_id})
    doc = RET.resolve_doc(entity_id, query)
    if doc is None:
        raise LookupError(f"No filings are held for '{entity_id}' in the corpus.")
    emit("resolved", {"doc_id": doc["doc_id"], "fy": RET.fy_label(doc)})

    emit("retrieve", {"doc_id": doc["doc_id"]})
    candidates, retrieval_report = RET.gather(doc["doc_id"], query)
    emit("retrieved", {"candidates": len(candidates)})

    emit("rerank", {"candidates": len(candidates)})
    evidence, rerank_report = RR.rerank(query, candidates)
    emit("reranked", {"kept": len(evidence), "applied": rerank_report.get("applied")})

    # ---- adaptive expansion: only when the first pass came back weak --------
    #
    # LLM reformulation costs a round trip and helps a minority of questions, so
    # it is not run on every query — it is run when the cheap pass has actually
    # failed, which is the only time it can change the outcome. Deterministic
    # synonym expansion has already run inside `gather`; this is the second tier.
    if not evidence and settings.adaptive_expansion:
        emit("expand", {"reason": "first pass found no usable evidence"})
        variants = EXP.llm_variants(query)
        if variants:
            retry_candidates, retry_report = RET.gather(
                doc["doc_id"], " ".join([query, *variants]))
            retry_evidence, retry_rerank = RR.rerank(query, retry_candidates)
            retrieval_report["adaptive"] = {
                "variants": variants, "candidates": len(retry_candidates),
                "kept": len(retry_evidence)}
            if retry_evidence:
                evidence, rerank_report = retry_evidence, retry_rerank
                retrieval_report = {**retry_report,
                                    "adaptive": retrieval_report["adaptive"]}
                emit("reranked", {"kept": len(evidence),
                                  "applied": rerank_report.get("applied")})
        else:
            retrieval_report["adaptive"] = {"variants": [], "reason": "expansion unavailable"}

    diagnostics = {"retrieval": retrieval_report, "rerank": rerank_report}

    # ---- the abstain gate -------------------------------------------------
    if not evidence:
        searched = retrieval_report.get("candidates", 0)
        degraded = retrieval_report.get("degraded") or []
        if searched == 0:
            message = (
                f"Nothing in **{entity_id.replace('_', ' ')}**'s {RET.fy_label(doc)} filing "
                f"matched that question.\n\nThe filing was searched across its statement "
                f"tables and its narrative text; no passage came back. This is a search "
                f"result, not a statement about the entity — the filing may discuss the "
                f"subject in terms the search did not match.")
        else:
            message = (
                f"**Not answerable from {entity_id.replace('_', ' ')}'s "
                f"{RET.fy_label(doc)} filing.**\n\n{searched} passages were considered and "
                f"none was judged relevant enough to answer from. Nothing is generated "
                f"without a source, so no answer is offered rather than one assembled "
                f"from the least irrelevant passage.")
        if degraded:
            message += ("\n\n⚠ Retrieval ran degraded: " + "; ".join(degraded)
                        + ". The result above may be thin for that reason rather than "
                          "because the filing is silent.")
        diagnostics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return _abstain(message, doc, diagnostics)

    # ---- generate ---------------------------------------------------------
    emit("generate", {"sources": len(evidence)})
    _, included = SYN.build_prompt(query, evidence)
    raw = SYN.generate(query, evidence, on_token=on_token)

    if raw.strip().startswith(SYN.ABSTAIN_TOKEN) or raw.strip() == SYN.ABSTAIN_TOKEN:
        diagnostics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return _abstain(
            f"**Not stated in {entity_id.replace('_', ' ')}'s {RET.fy_label(doc)} "
            f"filing.**\n\n{len(included)} relevant passages were read and none contains "
            f"the answer. Reporting that gap is the answer; inventing the figure or the "
            f"statement would not be.",
            doc, diagnostics)

    text, invalid = SYN.strip_invalid_citations(raw, len(included))

    # Post-generation wording pass. Extends `intent.py`'s refusal rules rather
    # than duplicating them: that layer refuses a question ASKING for a
    # prohibited conclusion, this one softens a prohibited PHRASING in the answer
    # to a legitimate question. What it changed is recorded, never silent.
    text, lint_applied = SYN.lint(text)
    if lint_applied:
        diagnostics["lint"] = lint_applied
    if invalid:
        # Not hidden. A model citing sources that were never supplied is a
        # quality signal about that answer, and the reader should see it.
        logger.warning("FDR disclosure answer cited non-existent sources: %s", invalid)
        diagnostics["invalid_citations"] = invalid

    cited = SYN.cited_indices(text)
    if not cited:
        text += ("\n\n⚠ No source was cited for this answer. Treat it as unverified and "
                 "check the sources below before relying on it.")

    emit("verify", {"cited": len(cited)})
    grounded = SYN.check_groundedness(query, text, included)
    diagnostics["groundedness"] = grounded
    if grounded.get("checked") and grounded.get("grounded") is False:
        text += (f"\n\n⚠ **Verification failed.** A second check against these same "
                 f"sources did not confirm every claim above"
                 + (f" — {grounded['reason']}" if grounded.get("reason") else "")
                 + ". Read it against the sources before relying on it.")

    diagnostics["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return {
        "kind": "disclosure",
        "text": text,
        "sources": SYN.sources_payload(included, doc, cited),
        "provenance": _provenance(doc, diagnostics),
        "rows": [],
    }
