"""Run-level metrics: any report or dashboard surfacing a bare mapped %
must pair it with enough of these to be read correctly -- a low mapped %
on a company with unusual vocabulary is not automatically a regression; it
may be the Validation Gate correctly refusing to trust something it
shouldn't.

Ported from TB_normalization_v1's core/metrics.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modes.trial_balance.pipeline.tools.tb_models import MAPPED, UNMAPPED, UNMATCHED


@dataclass(frozen=True)
class RunMetrics:
    total_rows: int
    mapped_count: int
    unmapped_count: int
    unmatched_count: int
    mapped_pct: float
    resolved_at_step_counts: dict = field(default_factory=dict)
    staged_narrowing_triggered_count: int = 0
    repair_attempted_count: int = 0
    repair_succeeded_count: int = 0


def compute_metrics(rows: list) -> RunMetrics:
    total = len(rows)
    mapped = sum(1 for r in rows if r.mapped_status == MAPPED)
    unmapped = sum(1 for r in rows if r.mapped_status == UNMAPPED)
    unmatched = sum(1 for r in rows if r.mapped_status == UNMATCHED)

    step_counts: dict = {}
    staged_count = 0
    repair_attempted = 0
    repair_succeeded = 0
    for row in rows:
        step_counts[row.trace.resolved_at_step] = step_counts.get(row.trace.resolved_at_step, 0) + 1
        if row.trace.staged_narrowing_triggered:
            staged_count += 1
        if row.trace.repair_attempted:
            repair_attempted += 1
            if row.trace.repair_succeeded:
                repair_succeeded += 1

    return RunMetrics(
        total_rows=total, mapped_count=mapped, unmapped_count=unmapped, unmatched_count=unmatched,
        mapped_pct=round(100.0 * mapped / total, 2) if total else 0.0,
        resolved_at_step_counts=step_counts, staged_narrowing_triggered_count=staged_count,
        repair_attempted_count=repair_attempted, repair_succeeded_count=repair_succeeded,
    )
