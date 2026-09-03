"""
FDR — the audit-planning-intelligence layer.

Spec: `Adv Rag FDR/SRS/FDR Audit Planning Intelligence Specification v3.docx` v3.0.
Build order and rationale: `ROADMAP.md`.

Built so far — stdlib only, no database, no model call anywhere:

  M1  the signal contract
      assertions.py   the §10.3 assertion set and the Appendix D divergences
      signals.py      the 22 Appendix D signals Layers 1-4 must produce
      clusters.py     the 6 clusters that consume them, plus the §10.2 interaction matrix
                      DECLARED between them (which pairs CAN interact)

  M3  the walking skeleton — the whole pipeline, every diagnostic abstaining
      model.py         the §14.3 output contract; confidence and severity kept apart
      evaluate.py      signal evaluation; the only stage later milestones replace
      assemble.py      results -> cluster packages -> report
      render.py        the §14.1 ten blocks
      safe_language.py the §17.2 lint, run before any report is released

  M10/M11  the principal deliverable — planning packages, matrix and ranking
      planning.py     the §10.3 package per cluster: inherent risk, candidate response,
                      planning significance, materiality basis. Provenance-tagged, because
                      the specification tabulates no responses and Appendix F has gaps
      interactions.py the §10.2 matrix RESOLVED against what a run found: an interaction
                      is claimed only where both themes are raised, an unchecked
                      counterpart yields LATENT rather than a claim either way, and the
                      connected groups of reinforcing themes become risk complexes
      priority.py     the §11 ten dimensions, the by-nature override, the capacity
                      adjustment, and the reasoning §11.2 requires alongside every rank.
                      §10.2 enters here as the interaction_pressure dimension — scored in
                      the open, never as a multiplier on a finished score
      export.py       the Appendix E matrix as CSV or markdown — §10.4 requires a matrix the
                      team can use directly, which a console dump is not
      headline.py     block 2's executive dashboard: ten entity-agnostic headline reads
                      plus sector overlays, declared as a registry rather than coded per
                      entity. Salience is declared per tile, never inferred from the sign,
                      and every movement carries the year it is measured against

Observability: `tracing.py` exports OpenTelemetry spans to Arize Phoenix, project
'fdr-planning'. Opt-in (--trace or FDR_TRACE_ENABLED=1), fail-open, and when off no
OpenTelemetry package is imported at all — `fdr` stays stdlib-only.

Run:
    python -m fdr contract [--gaps]
    python -m fdr headline [--model petroleum]
    python -m fdr skeleton "Entity" FY2023-24 FY2024-25
    python -m fdr skeleton "Entity" FY2024-25 --assume S02,S05,S21   # review mode
    python -m fdr.test_registry && python -m fdr.test_skeleton && python -m fdr.test_planning

Importing this package validates the signal contract: unknown signal references, orphan
diagnostics, unreachable clusters and unknown assertions all raise at import.
"""
from . import (  # noqa: F401
    assertions, signals, clusters,
    model, evaluate, assemble, render, safe_language,
    headline, planning, priority, export, tracing,
)

__all__ = [
    "assertions", "signals", "clusters",
    "model", "evaluate", "assemble", "render", "safe_language",
    "headline", "planning", "priority", "export", "tracing",
]
