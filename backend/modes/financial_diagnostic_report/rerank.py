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

TWO BACKENDS: A SERVICE FIRST, THE LOCAL MODEL SECOND
-----------------------------------------------------
This module was written when no reranker service existed. One does now, at
`RERANKER_BASE_URL` — the same endpoint the Financial Statement mode has been
using all along — so it is preferred: no 2.2GB download, no ~20s first load, and
no cross-encoder competing for the gateway's CPU.

The service returns a SIGMOID-NORMALISED score in (0, 1); the local model
returns a raw logit, and `rerank_min_logit` is calibrated on the logit because
that is the only quantity here with a principled zero point. So the score is
converted back rather than the threshold re-tuned, which keeps the two backends
interchangeable and the abstain gate meaning the same thing either way. See
`_to_logit`.

One caveat worth knowing: the endpoint reports its model only as the alias
`bge-reranker`, so it cannot be confirmed byte-identical to the
`bge-reranker-v2-m3` the threshold was calibrated against. Measured against it,
a matching passage scored +1.02 and an unrelated board-meeting passage -9.48,
against the +5.4 / -11.0 recorded below for the local model — same sign, same
order of magnitude, same decision at this threshold. Tune `FDR_RERANK_MIN_LOGIT`
if a deployment's endpoint disagrees; `provenance.rerank.backend` says which one
produced any given score.

The local model remains the fallback and loads from the local Hugging Face cache.
With a service configured, `warm()` does NOT load it — standing 2.2GB up behind
an endpoint that is already answering wastes startup and memory.

If torch/transformers are absent or the weights are not cached, `rerank`
returns the fused order unchanged and says so. A missing reranker degrades
ranking quality; it must never take the answer path down.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import urllib.error
import urllib.request
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
_remote_state: dict[str, Any] = {"available": None, "reason": None}


def status() -> dict[str, Any]:
    """Passive read — never triggers the slow load. For the health route.

    Reports BOTH backends, because "the reranker is unavailable" is a different
    operational problem depending on which one was expected to serve.
    """
    settings = CFG.load()
    return {"model": MODEL_NAME,
            "backend": "service" if settings.rerank_url else "local",
            "service_url": settings.rerank_url or None,
            "service_available": _remote_state["available"],
            "service_reason": _remote_state["reason"],
            "loaded": _state["loaded"],
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
    """Get the reranker ready off the request path.

    With a service configured this is a cheap reachability probe, and the local
    weights are deliberately NOT loaded: downloading or loading 2.2GB to stand
    behind an endpoint that is already answering wastes startup time and memory,
    and on a machine with no cached copy it produced a scary-looking
    "couldn't connect to huggingface.co" warning for a mode that was in fact
    fine. Only a deployment with no service falls through to the local model,
    which is the case the ~20s pre-load exists for.
    """
    settings = CFG.load()
    if settings.rerank_url:
        ok = _score_remote("warm-up", ["warm-up"]) is not None
        if ok:
            logger.info("FDR reranker: using the service at %s (model %s)",
                        settings.rerank_url, settings.rerank_model)
        else:
            logger.warning(
                "FDR reranker service at %s did not answer (%s); the local "
                "cross-encoder will be tried per request.",
                settings.rerank_url, _remote_state["reason"])
        return ok
    return _load()


# ---------------------------------------------------------------------------
# The reranker SERVICE
#
# The module was written assuming no reranker service existed ("rerank.internal
# :8002 does not resolve"). One does, at RERANKER_BASE_URL, running the same
# bge-reranker the local path loads -- and the Financial Statement mode has been
# using it all along. Preferring it removes a 2.2GB model download, ~20s of
# first-load, and the CPU cost of running a cross-encoder inside the gateway.
# ---------------------------------------------------------------------------

# The service returns a SIGMOID-NORMALISED relevance score in (0, 1); the local
# model returns a raw logit. Everything downstream -- and `rerank_min_logit` in
# particular -- is calibrated on the logit, which is the only quantity here with
# a principled zero point. So the score is converted back rather than the
# threshold being re-tuned, and the two backends stay interchangeable.
#
# Measured against this endpoint, which is what makes the equivalence more than
# an assumption:
#     "Revenue from operations ... 16,292.97 crore"   0.734    -> +1.02
#     "Statement of Profit and Loss ..."              0.557    -> +0.23
#     "The Board met four times during the year"      0.000076 -> -9.48
# The module's own calibration note records -11.0 for an unrelated board-meeting
# passage against the local model. Same shape, same decision at the same
# threshold.
_EPS = 1e-9

# The service enforces the model's real limit -- 512 tokens for the (query,
# passage) PAIR -- and rejects the whole batch with HTTP 400 when any pair
# exceeds it. Probed directly against this endpoint: 2,000 characters per
# passage passed, 4,000 came back "maximum context length is 512 tokens.
# However, you requested 777 tokens".
#
# The local path truncates with the real tokenizer at `rerank_max_len`; over
# HTTP there is no tokenizer, so the budget is estimated in characters. 2.5
# chars/token rather than the usual ~4, because these passages are financial
# tables -- digits, pipes and currency symbols tokenize far worse than prose,
# and under-estimating here means a 400 for the entire batch rather than one
# clipped passage.
# The window belongs to the SERVER, so the budget is derived from it rather than
# from `rerank_max_len` -- that setting exists to bound CPU cost on the local
# path, and spending less of the window than the service allows is free quality
# thrown away when it is the service answering.
# The window belongs to the SERVER, so the budget is derived from it rather than
# from `rerank_max_len` -- that setting bounds CPU cost on the local path, and
# spending less of the window than the service allows is free quality thrown
# away when it is the service answering.
_SERVER_TOKEN_LIMIT = 512
_RESERVED_TOKENS = 48        # query, separators and special tokens
_CHARS_PER_TOKEN = 3.9       # measured on this corpus, see below
_QUERY_CHARS = 400
_MIN_PASSAGE_CHARS = 200
_RETRY_SHRINK = 0.75         # gentle: halving overshoots straight past the signal

# MEASURED against this endpoint on real ONGC candidates asking for the leases
# accounting policy. Passages here have a median of ~4,400 characters and the
# answer is rarely in the opening lines:
#
#     budget   best logit   passing the -1.0 gate
#      440       -2.57              0
#     1000       -2.44              0
#     1400       -2.90              0
#     1800       -0.64              1
#     2200      HTTP 400 -- over 512 tokens
#
# A conservative budget does not merely clip the tail here: it discards the
# signal and turns an answerable question into an abstain, which is the worst
# possible failure for this module because an abstain is supposed to MEAN
# something. So the budget sits just inside where 2,200 failed, and the retry
# below shrinks gently rather than halving -- halving from 1,800 lands at 900,
# back in the dead zone, and would trade a 400 for a silent wrong answer.
#
# 3.9 chars/token is well under prose's ~4.5 because financial tables are digits,
# pipes and currency symbols, which tokenize far worse.


def _passage_budget() -> int:
    """Characters per passage that should fit the service's window.

    `FDR_RERANK_PASSAGE_CHARS` overrides it outright, for a deployment whose
    reranker has a different window than the one measured here.
    """
    override = os.environ.get("FDR_RERANK_PASSAGE_CHARS", "").strip()
    if override.isdigit() and int(override) > 0:
        return max(_MIN_PASSAGE_CHARS, int(override))
    usable = max(1, _SERVER_TOKEN_LIMIT - _RESERVED_TOKENS)
    return max(_MIN_PASSAGE_CHARS, int(usable * _CHARS_PER_TOKEN))


def _to_logit(score: float) -> float:
    """Inverse sigmoid. Clamped so a saturated 0.0 or 1.0 cannot blow up."""
    p = min(1.0 - _EPS, max(_EPS, float(score)))
    return math.log(p / (1.0 - p))


def _score_remote(query: str, passages: list[str]) -> list[float] | None:
    """Logits from the reranker service, or None if it cannot be used.

    Returns None rather than raising: an unreachable reranker must cost ranking
    quality, never the answer. The caller then tries the local model.
    """
    settings = CFG.load()
    base = (settings.rerank_url or "").strip().rstrip("/")
    if not base:
        return None
    if not base.startswith("http"):
        base = f"http://{base}"

    budget = _passage_budget()
    short_query = query[:_QUERY_CHARS]

    body = None
    # Halve and retry on an overflow, down to a floor. The character estimate
    # above is a heuristic over text this module does not control, so it will
    # occasionally be wrong; one cheap retry turns that from a lost reranking
    # into a slightly more clipped one. The Financial Statement mode's client
    # does the same against this same endpoint.
    for attempt in range(3):
        docs = [p[:budget] for p in passages]
        payload = {
            "model": settings.rerank_model,
            "query": short_query,
            # Both keys, because reranker servers disagree about which one they
            # want and sending both satisfies either.
            "documents": docs,
            "texts": docs,
        }
        request = urllib.request.Request(
            f"{base}/rerank", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.rerank_timeout) as response:
                body = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            overflow = exc.code == 400 and "context length" in detail.lower()
            if overflow and budget > _MIN_PASSAGE_CHARS and attempt < 2:
                budget = max(_MIN_PASSAGE_CHARS, int(budget * _RETRY_SHRINK))
                logger.info("FDR reranker: passage too long, retrying at %d chars", budget)
                continue
            _remote_state["reason"] = f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}"
            logger.warning("FDR reranker service rejected the batch (%s); falling back",
                           _remote_state["reason"])
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            _remote_state["reason"] = f"{type(exc).__name__}: {exc}"
            logger.warning("FDR reranker service unavailable (%s); falling back",
                           _remote_state["reason"])
            return None

    if body is None:
        return None

    results = body.get("results")
    if not isinstance(results, list) or not results:
        _remote_state["reason"] = "reranker returned no 'results' array"
        logger.warning("FDR reranker service returned an unexpected shape; falling back")
        return None

    # Results come back ordered by relevance and carry their original index, so
    # they are scattered back into input order for the caller to zip against.
    logits = [None] * len(passages)
    for entry in results:
        try:
            index = int(entry["index"])
            logits[index] = _to_logit(entry["relevance_score"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue

    if any(v is None for v in logits):
        _remote_state["reason"] = "reranker scored only some candidates"
        logger.warning("FDR reranker service scored %d of %d candidates; falling back",
                       sum(v is not None for v in logits), len(passages))
        return None

    _remote_state["available"], _remote_state["reason"] = True, None
    return logits  # type: ignore[return-value]


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

    pool = items[: settings.rerank_pool]
    passages = [_passage(i) for i in pool]

    # THE SERVICE FIRST, the local model second, fused order last.
    #
    # Both run the same bge-reranker and both are converted to the same logit
    # scale, so which one answered changes latency and nothing else about the
    # decision. The service is preferred because it needs no 2.2GB download, no
    # ~20s first load, and no cross-encoder running on the gateway's CPU.
    scores = _score_remote(query, passages)
    backend = "service"

    if scores is None:
        backend = "local"
        if not _load():
            reason = _remote_state["reason"] or _state["reason"] or "unavailable"
            return items[:top_k], {**report, "reason": reason}

        import torch

        try:
            with torch.inference_mode():
                encoded = _tokenizer(                                # type: ignore[misc]
                    [query] * len(pool), passages,
                    padding=True, truncation=True,
                    max_length=settings.rerank_max_len, return_tensors="pt")
                scores = _model(**encoded).logits.view(-1).float().tolist()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            return items[:top_k], {**report, "reason": f"scoring failed ({type(exc).__name__})"}

    report["backend"] = backend

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
