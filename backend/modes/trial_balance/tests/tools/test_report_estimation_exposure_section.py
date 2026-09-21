"""Regression test for the Section 13 estimation-exposure key-path bug: the report builder
read `est.get('total_exposure', 0)` at the JSON's top level, but build_estimation_exposure
(risk.py) always writes the real number nested under `summary.total_exposure` -- the top-level
key never existed, so the note silently rendered "Total estimation exposure: 0." regardless of
the real figure, directly contradicting Section 10's estimation-exposure text (which reads the
correct nested path) in the same document."""

import json

from modes.trial_balance.pipeline.tools._shared import _write_phase4_sections


class _StubDoc:
    def add_heading(self, *args, **kwargs):
        pass


def test_section_13_reads_nested_summary_total_exposure(tmp_path):
    (tmp_path / "estimation_exposure.json").write_text(json.dumps({
        "methodology": "keyword screen",
        "summary": {"accounts_flagged": 35, "total_exposure": 3458112515.37},
        "accounts": [],
        "generated_at": "2026-09-16T15:06:13",
    }))

    notes = []
    _write_phase4_sections(
        doc=_StubDoc(),
        out_dir=tmp_path,
        manifest={},
        add_body=lambda *a, **k: None,
        add_note=lambda text, *a, **k: notes.append(text),
        add_table=lambda *a, **k: None,
        add_bullets=lambda *a, **k: None,
        safe_fmt=lambda v: f"{v:,.0f}",
    )

    exposure_notes = [n for n in notes if n.startswith("Total estimation exposure:")]
    assert len(exposure_notes) == 1
    assert exposure_notes[0] == "Total estimation exposure: 3,458,112,515."


def test_section_13_defaults_to_zero_when_summary_key_missing(tmp_path):
    (tmp_path / "estimation_exposure.json").write_text(json.dumps({
        "methodology": "keyword screen",
        "summary": {"accounts_flagged": 0},
        "accounts": [],
    }))

    notes = []
    _write_phase4_sections(
        doc=_StubDoc(),
        out_dir=tmp_path,
        manifest={},
        add_body=lambda *a, **k: None,
        add_note=lambda text, *a, **k: notes.append(text),
        add_table=lambda *a, **k: None,
        add_bullets=lambda *a, **k: None,
        safe_fmt=lambda v: f"{v:,.0f}",
    )

    assert "Total estimation exposure: 0." in notes
