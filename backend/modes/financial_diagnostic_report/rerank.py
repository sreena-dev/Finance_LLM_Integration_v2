"""Cross-encoder reranking with BAAI/bge-reranker-v2-m3.

WHY A CROSS-ENCODER AND NOT JUST THE RETRIEVAL SCORE
----------------------------------------------------
The retrieval arms score a passage without ever looking at it beside the
question: the vector arm compares two independently-made embeddings, and the
keyword arm counts term overlap. Both are cheap and both are easily fooled by a
passage that shares vocabulary with the question and answers something else —
a governance section that happens to say "revenue" and "listed" ranks well
against a revenue question while containing no figure at all.

A cross-encoder reads the question and the passage TOGETHER and scores the pair.
That is more expensive by construction, which is why the arms cast wide and this
cuts down: gather ~24 candidates cheaply, rank them properly, keep 7.

It also supplies something the retrieval scores cannot — an absolute threshold.
Cosine similarity is only comparable within one query, but this model's logit
has a principled zero point: relevant pairs land positive, irrelevant ones
negative. Measured on this corpus with the local model: a matching statement
table scored +5.4 against a revenue question, an unrelated board-meeting
passage -11.0. So a candidate below `rerank_min_logit` is dropped as not
evidence, and a question with NO candidate above it abstains instead of being
answered from whatever ranked least badly.

THE MODEL IS LOCAL AND OPTIONAL
-------------------------------
It loads from the local Hugging Face cache — no reranker service is running
(`rerank.internal:8002` does not resolve), and this mode should not depend on
one appearing. First load costs ~20s and happens once per process, off the
request path when `warm()` is called at startup.

If torch/transformers are absent or the weights are not cached, `rerank`
returns the fused order unchanged and says so. A missing reranker degrades
ranking quality; it must never take the answer path down.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from . import config as CFG
from . import fusion as FUSE

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

os.environ.setdefault("USE_TF", "0")          # never pull in TensorFlow
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")   # we batch ourselves

def _quantise_enabled() -> bool:
    """int8 quantisation, on unless explicitly disabled."""
    return (os.environ.get("FDR_RERANK_QUANTISE", "").strip().lower()
            not in ("0", "false", "no", "off"))


_lock = threading.Lock()
_tokenizer = None
_model = None
_state: dict[str, Any] = {"loaded": False, "available": None, "reason": None}


def status() -> dict[str, Any]:
    """Passive read — never triggers the slow load. For the health route."""
    return {"model": MODEL_NAME, "loaded": _state["loaded"],
            "available": _state["available"], "reason": _state["reason"]}


def _load() -> bool:
    global _tokenizer, _model
    if _state["loaded"]:
        return bool(_state["available"])
    with _lock:
        if _state["loaded"]:
            return bool(_state["available"])
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            # This runs on CPU inside the gateway, so it gets every core. Torch
            # defaults to physical cores only, which left roughly a fifth of the
            # machine idle during the one stage that dominates this path.
            torch.set_num_threads(os.cpu_count() or 4)

            # `local_files_only` so a machine with no internet — or a cache miss
            # — fails fast into graceful degradation instead of hanging on a
            # download inside a request.
            _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
            _model = AutoModelForSequenceClassification.from_pretrained(
                MODEL_NAME, local_files_only=True)
            _model.eval()

            # Dynamic int8 quantisation of the linear layers — measured here at
            # roughly 2x on this model and this CPU, which matters because
            # reranking is the slowest stage of the retrieval path by a wide
            # margin. It changes scores in the third decimal; the threshold this
            # feeds is set at 1.0-unit granularity, so the ranking it produces is
            # unaffected. Guarded because the API is deprecated in newer torch:
            # if it goes away, the unquantised model is simply used.
            if _quantise_enabled():
                try:
                    _model = torch.quantization.quantize_dynamic(
                        _model, {torch.nn.Linear}, dtype=torch.qint8)
                except Exception as exc:  # noqa: BLE001
                    logger.info("FDR reranker not quantised (%s); using full precision",
                                type(exc).__name__)

            _state["available"], _state["reason"] = True, None
            logger.info("FDR reranker loaded: %s", MODEL_NAME)
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the answer
            _state["available"] = False
            _state["reason"] = f"{type(exc).__name__}: {exc}"
            logger.warning("FDR reranker unavailable (%s); keeping fused order",
                           _state["reason"])
        finally:
            _state["loaded"] = True
    return bool(_state["available"])


def warm() -> bool:
    """Load the weights now, so no user pays the ~20s first-load cost."""
    return _load()


def _passage(item: dict) -> str:
    """What the cross-encoder is shown for a candidate.

    The title is prepended because it is the strongest single relevance signal
    in this corpus — "Balance Sheet as at 31 March 2025" identifies a passage
    that its first 400 characters of pipe-delimited numbers do not.
    """
    title = (item.get("title") or "").strip()
    body = (item.get("content") or "").strip()
    return f"{title}\n{body}" if title else body


def rerank(query: str, items: list[dict], *, top_k: int | None = None
           ) -> tuple[list[dict], dict[str, Any]]:
    """Order `items` by relevance to `query` and drop what is not evidence.

    Returns the kept items — each carrying `rerank_score` — and a report of what
    the reranker did, which travels into the response so a thin answer can be
    explained rather than guessed at.
    """
    settings = CFG.load()
    top_k = top_k or settings.max_evidence
    report: dict[str, Any] = {"applied": False, "model": MODEL_NAME,
                              "candidates": len(items), "kept": len(items[:top_k]),
                              "dropped_below_threshold": 0,
                              "min_logit": settings.rerank_min_logit, "reason": None}

    if not items:
        return [], {**report, "kept": 0, "reason": "no candidates"}
    if not settings.rerank_enabled:
        return items[:top_k], {**report, "reason": "disabled by configuration"}
    if not _load():
        return items[:top_k], {**report, "reason": _state["reason"] or "unavailable"}

    import torch

    pool = items[: settings.rerank_pool]
    try:
        with torch.inference_mode():
            encoded = _tokenizer(                                    # type: ignore[misc]
                [query] * len(pool), [_passage(i) for i in pool],
                padding=True, truncation=True,
                max_length=settings.rerank_max_len, return_tensors="pt")
            scores = _model(**encoded).logits.view(-1).float().tolist()  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        return items[:top_k], {**report, "reason": f"scoring failed ({type(exc).__name__})"}

    # BLEND, do not replace. Attaching the logit and re-sorting on the FUSED
    # score plus a squashed relevance term keeps what retrieval established —
    # that a chunk is a primary statement, that its title matches, that three
    # independent arms found it — and lets the cross-encoder refine the order
    # rather than overrule it. A large logit gap still decides the ranking; a
    # small one no longer discards the domain signal.
    for item, score in zip(pool, scores):
        item["rerank_score"] = round(score, 3)

    blended = FUSE.blend_rerank(list(pool), weight=settings.rerank_blend)

    # The THRESHOLD is applied to the raw logit, not to the blended score. The
    # logit is the only quantity here with a principled zero point, and it is
    # what makes "nothing is relevant enough to answer from" a decidable
    # question rather than a percentile of whatever was retrieved.
    above = [i for i in blended if (i.get("rerank_score") or 0) >= settings.rerank_min_logit]
    kept = above[:top_k]
    best = max(scores) if scores else None

    return kept, {**report, "applied": True, "kept": len(kept),
                  "blended": True, "blend_weight": settings.rerank_blend,
                  "dropped_below_threshold": len(blended) - len(above),
                  "best_logit": round(best, 3) if best is not None else None}
