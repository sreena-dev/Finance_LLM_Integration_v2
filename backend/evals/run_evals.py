"""Batch evaluation job: score recent traces and write the results to Phoenix.

Run it after traffic, not during it:

    docker compose exec backend python -m evals.run_evals --limit 200
    docker compose exec backend python -m evals.run_evals --dry-run --limit 20

Batch rather than inline, deliberately. Every judged metric is an extra LLM call
against the same endpoint the product generates with, so scoring inline would
add that latency and that load to every user request — on a mode that already
takes ~30s, and against an endpoint shared with live traffic. Run out of band,
the evaluation costs the user nothing and can be re-run over history when a
metric changes.

Scores are attached to the trace's root span as Phoenix annotations, so they
show up per-run in the UI and aggregate across a project.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Import as a package whether invoked as `-m evals.run_evals` or by path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.metrics import CODE_METRICS, JUDGED_METRICS, build_judge  # noqa: E402
from evals.phoenix_api import PhoenixAPI, annotation  # noqa: E402
from evals.traces import Trace, assemble  # noqa: E402

logger = logging.getLogger("evals")


def _load_state(path: str) -> dict[str, list[str]]:
    """Which metrics have already been written, per span id."""
    try:
        import json

        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - a corrupt state file must not block scoring
        logger.warning("Could not read state file %s (%s) — treating everything as unscored.", path, exc)
        return {}


def _save_state(path: str, state: dict[str, list[str]]) -> None:
    try:
        import json
        from pathlib import Path as _Path

        _Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=0, sort_keys=True)
    except Exception as exc:  # noqa: BLE001 - scores are already in Phoenix; state is an optimisation
        logger.warning("Could not write state file %s (%s) — the next run may re-score.", path, exc)


def _load_env() -> None:
    """Load .env when run outside the container (inside, compose provides it)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    backend = Path(__file__).resolve().parent.parent
    for candidate in (backend / ".env", backend.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def evaluate_trace(trace: Trace, judges: dict, skip: set[str]) -> list[dict]:
    """Score one trace. Returns annotation payloads for its root span."""
    out: list[dict] = []

    for metric in CODE_METRICS:
        if metric.name in skip:
            continue
        try:
            result = metric.fn(trace)
        except Exception as exc:  # noqa: BLE001 - one bad metric must not sink the run
            logger.warning("  code metric %s failed: %s", metric.name, exc)
            continue
        if result is None:
            continue
        score, label, explanation = result
        out.append(annotation(trace.root_span_id, metric.name, label=label, score=score,
                              explanation=explanation, annotator_kind="CODE"))

    for metric in JUDGED_METRICS:
        if metric.name in skip:
            continue
        evaluator = judges.get(metric.name)
        if evaluator is None:
            continue
        payload = metric.build_input(trace)
        if payload is None:
            # Not applicable to this trace (e.g. faithfulness with no retrieval).
            continue
        try:
            scores = evaluator.evaluate(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  judge %s failed on %s: %s", metric.name, trace.trace_id[:8], exc)
            continue
        for score in scores:
            out.append(annotation(trace.root_span_id, metric.name,
                                  label=getattr(score, "label", None),
                                  score=getattr(score, "score", None),
                                  explanation=getattr(score, "explanation", None),
                                  annotator_kind="LLM"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Score Phoenix traces and write annotations back.")
    parser.add_argument("--project", default=os.environ.get("PHOENIX_PROJECT_NAME", "Integrated_v1"))
    parser.add_argument("--limit", type=int, default=200, help="Max spans to pull (newest first).")
    parser.add_argument("--max-traces", type=int, default=0, help="Cap traces scored (0 = no cap).")
    parser.add_argument("--only", default="", help="Comma-separated metric names to run.")
    parser.add_argument("--skip", default="", help="Comma-separated metric names to skip.")
    parser.add_argument("--code-only", action="store_true", help="Deterministic metrics only — no LLM calls.")
    parser.add_argument("--rescore", action="store_true", help="Re-score traces that already have annotations.")
    parser.add_argument("--dry-run", action="store_true", help="Print scores; write nothing to Phoenix.")
    parser.add_argument("--state-file",
                        default=os.environ.get("ARTHA_EVAL_STATE_FILE", "/var/lib/artha/evals-scored.json"),
                        help="Records which spans have been scored, so re-runs skip them.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    _load_env()

    api = PhoenixAPI()
    logger.info("Phoenix: %s | project: %s", api.base_url, args.project)

    project_id = api.project_id(args.project)
    spans = list(api.iter_spans(project_id, limit=args.limit))
    traces = [t for t in assemble(spans) if t.is_evaluable]
    logger.info("Pulled %d spans -> %d evaluable trace(s).", len(spans), len(traces))

    if args.max_traces:
        traces = traces[: args.max_traces]

    all_names = {m.name for m in CODE_METRICS} | {m.name for m in JUDGED_METRICS}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if args.only:
        keep = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = keep - all_names
        if unknown:
            parser.error(f"unknown metric(s): {', '.join(sorted(unknown))}. Known: {', '.join(sorted(all_names))}")
        skip |= all_names - keep
    if args.code_only:
        skip |= {m.name for m in JUDGED_METRICS}

    # Already-scored spans are skipped by default: the judged metrics are the
    # expensive part, and overlapping windows would otherwise re-pay for them
    # and stack duplicate scores on the same span.
    #
    # Tracked in a local state file rather than by asking Phoenix what it
    # already has. The read-back endpoint (GET .../spans/annotations) does not
    # exist on Phoenix 19.x — the request falls through to the SPA and returns
    # HTML — so server-side dedupe is not available here. The state file also
    # keeps this correct if the job is ever pointed at a Phoenix that prunes.
    state = _load_state(args.state_file)
    if not args.rescore and traces:
        before = len(traces)
        wanted = all_names - skip
        traces = [t for t in traces if not (set(state.get(t.root_span_id, [])) >= wanted)]
        if before != len(traces):
            logger.info("Skipping %d already-scored trace(s); --rescore to redo them.", before - len(traces))

    if not traces:
        logger.info("Nothing to score.")
        return 0

    judges: dict = {}
    if not args.code_only:
        try:
            from phoenix.evals import metrics as pxmetrics

            llm = build_judge()
            for metric in JUDGED_METRICS:
                if metric.name in skip:
                    continue
                judges[metric.name] = getattr(pxmetrics, metric.evaluator_cls)(llm=llm)
            logger.info("Judges ready: %s", ", ".join(sorted(judges)) or "(none)")
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not build the LLM judges (%s: %s) — running code metrics only.",
                         type(exc).__name__, exc)

    payloads: list[dict] = []
    tally: dict[str, list[float]] = defaultdict(list)
    labels: dict[str, Counter] = defaultdict(Counter)

    for i, trace in enumerate(traces, 1):
        logger.info("[%d/%d] %s  %s", i, len(traces), trace.trace_id[:12],
                    (trace.question or "").replace("\n", " ")[:70])
        anns = evaluate_trace(trace, judges, skip)
        for ann in anns:
            name, result = ann["name"], ann["result"]
            if result.get("score") is not None:
                tally[name].append(result["score"])
            if result.get("label"):
                labels[name][result["label"]] += 1
            logger.info("     %-20s %-16s %s", name, result.get("label", ""),
                        f"{result['score']:.2f}" if result.get("score") is not None else "")
        payloads.extend(anns)

    print("\n" + "=" * 72)
    print(f"{'metric':<22}{'n':>5}{'mean':>9}   labels")
    print("-" * 72)
    for name in sorted(set(tally) | set(labels)):
        scores = tally.get(name, [])
        mean = f"{sum(scores)/len(scores):.2f}" if scores else "-"
        top = ", ".join(f"{k}={v}" for k, v in labels[name].most_common(3))
        print(f"{name:<22}{len(scores):>5}{mean:>9}   {top}")
    print("=" * 72)

    if args.dry_run:
        print(f"\ndry run — {len(payloads)} annotation(s) not written")
        return 0

    written = api.log_annotations(payloads)
    if written:
        # Record every metric that was *attempted*, not only those that produced
        # a score. Many metrics legitimately do not apply to a given trace —
        # faithfulness needs retrieved context, tool_selection needs tool calls —
        # and recording only what was written would leave those traces
        # permanently short of the full set, so every future run would re-score
        # them and pay for the LLM judges again.
        for trace in traces:
            names = set(state.get(trace.root_span_id, [])) | (all_names - skip)
            state[trace.root_span_id] = sorted(names)
        _save_state(args.state_file, state)
    print(f"\nwrote {written}/{len(payloads)} annotation(s) to project {args.project}")
    print(f"view: {api.base_url}/projects")
    return 0 if written == len(payloads) else 1


if __name__ == "__main__":
    raise SystemExit(main())
