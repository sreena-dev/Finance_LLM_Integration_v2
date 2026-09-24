"""Structured processing observability -- `StageDuration`/`RecoveryStats`,
threaded from `pipeline.py` through `emit.build_result` into
`DocumentQuality`. Answers "how long did each stage take, and how much of
each recovery mechanism actually ran and worked" in a form a caller can
query, where before this only free-text `notes` strings could gesture at it.

`pipeline.run()` itself is not exercised here -- it needs docling, which this
fast venv does not have -- so this covers the plumbing pipeline.py's new code
feeds into: `emit.build_result`'s new optional parameters, correctly
defaulted, and `DocumentQuality.as_dict()`'s new fields.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_processing_report.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import emit                                        # noqa: E402
from app.models import Identification, RecoveryStats, StageDuration  # noqa: E402


def _build(**overrides):
    kwargs = dict(
        doc_id="doc_test", filename="test.pdf", data=b"%PDF-1.4",
        identification=Identification(), qualities=[], tables=[], texts=[],
        vlm_used=False, notes=[],
    )
    kwargs.update(overrides)
    return emit.build_result(**kwargs)


def test_omitting_the_new_params_defaults_to_empty_not_none():
    result = _build()
    q = result.quality
    assert q.stage_durations == []
    assert q.rescue_stats == RecoveryStats(0, 0, 0)
    assert q.disagreement_stats == RecoveryStats(0, 0, 0)
    assert q.gap_fill_stats == RecoveryStats(0, 0, 0)


def test_stage_durations_and_recovery_stats_pass_through():
    durations = [StageDuration("render", 0.5), StageDuration("convert", 42.1)]
    rescue = RecoveryStats(requested=5, attempted=3, succeeded=2)
    result = _build(
        stage_durations=durations,
        rescue_stats=rescue,
        disagreement_stats=RecoveryStats(requested=1, attempted=1, succeeded=0),
        gap_fill_stats=RecoveryStats(requested=4, attempted=4, succeeded=4),
    )
    q = result.quality
    assert q.stage_durations == durations
    assert q.rescue_stats is rescue
    assert q.disagreement_stats.succeeded == 0
    assert q.gap_fill_stats.succeeded == 4


def test_document_quality_as_dict_carries_the_new_fields():
    result = _build(
        stage_durations=[StageDuration("verify", 1.25)],
        rescue_stats=RecoveryStats(requested=2, attempted=2, succeeded=1),
    )
    d = result.quality.as_dict()
    assert d["stage_durations"] == [{"stage": "verify", "duration_seconds": 1.25}]
    assert d["rescue_stats"] == {"requested": 2, "attempted": 2, "succeeded": 1}
    assert d["disagreement_stats"] == {"requested": 0, "attempted": 0, "succeeded": 0}
    assert d["gap_fill_stats"] == {"requested": 0, "attempted": 0, "succeeded": 0}


def test_recovery_stats_defaults_are_all_zero():
    stats = RecoveryStats()
    assert stats.requested == 0
    assert stats.attempted == 0
    assert stats.succeeded == 0
    assert stats.as_dict() == {"requested": 0, "attempted": 0, "succeeded": 0}
