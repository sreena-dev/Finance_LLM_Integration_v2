"""The page pipeline: ordering, progress and failure isolation under concurrency.

Every heavy dependency (docling, RapidOCR, the model endpoint) is replaced by a
fake with a random delay, so the tests exercise only the orchestration: pages
flow through the stages concurrently, and none of that may show in the output.
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import pipeline, second_read  # noqa: E402
from app.layout import PageLayout  # noqa: E402
from app.llm_client import LLMResult  # noqa: E402
from app.tabletypes import TableRegion  # noqa: E402
from tests._helpers import BALANCE_SHEET, make_tokens  # noqa: E402

TABLES_PER_PAGE = {1: 2, 2: 0, 3: 1, 4: 2}
STAGES = ["convert", "vlm", "verify", "identify"]


class Env:
    """Installs the fakes and runs an extraction."""

    def __init__(self, monkeypatch, seed=0, vlm=True, fail_ocr_for=(), delays=True):
        self.rng = random.Random(seed)
        self.delays = delays
        self.fail_ocr_for = set(fail_ocr_for)
        self.progress: list[tuple[str, str, float]] = []
        self.partials: list[list[dict]] = []
        self.layout_in_flight = 0
        self.layout_max = 0
        self.lock = threading.Lock()
        self.vlm = vlm

        monkeypatch.setattr(pipeline.Config, "PARTIAL_RESULT_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(pipeline.layout_mod.ENGINE, "analyse", self.analyse)
        monkeypatch.setattr(pipeline.ocr, "read_region", self.read_region)
        monkeypatch.setattr(pipeline.second_read, "read_row", self.read_row)
        monkeypatch.setattr(pipeline.structure, "CLIENT", self)

    def sleep(self):
        if self.delays:
            time.sleep(self.rng.uniform(0, 0.02))

    # -- fakes -----------------------------------------------------------------
    def analyse(self, image, page_no):
        with self.lock:
            self.layout_in_flight += 1
            self.layout_max = max(self.layout_max, self.layout_in_flight)
        self.sleep()
        with self.lock:
            self.layout_in_flight -= 1
        regions = [
            TableRegion(page_no, (50.0, 50.0 + 400 * i, 1500.0, 400.0 + 400 * i), title=f"Table {page_no}.{i}", index_on_page=i)
            for i in range(TABLES_PER_PAGE[page_no])
        ]
        return PageLayout(page_no=page_no, markdown=f"## Statement page {page_no}\n\nSome narrative text for page {page_no}.", regions=regions)

    def read_region(self, image, bbox, page_no, table_no, pad=12.0):
        self.sleep()
        if table_no in self.fail_ocr_for:
            raise RuntimeError("ocr exploded")
        return make_tokens(BALANCE_SHEET, table_no=table_no, page_no=page_no)

    def configured(self):
        return True

    def chat(self, kind, prompt, images=None, **kw):
        self.sleep()
        return LLMResult(json.dumps({
            "columns": [{"id": 1, "role": "note"}, {"id": 2, "role": "value"}, {"id": 3, "role": "value"}],
            "rows": [{"id": i, "kind": k, "join": None}
                     for i, k in enumerate(["header", "heading", "item", "item", "total"])],
            "moves": [],
        }), True)

    def read_row(self, image, y0, y1, x0, x1, pad, doc_id, label):
        self.sleep()
        r = round((y0 - 100) / 60)
        return second_read.RowRead(list(BALANCE_SHEET[r][2]))

    # -- run -------------------------------------------------------------------
    def run(self):
        images = {p: np.zeros((60, 60), np.uint8) for p in TABLES_PER_PAGE}
        notes: list[str] = []
        tracker = pipeline._Progress(lambda s, m, f: self.progress.append((s, m, f)), len(images))
        ex = pipeline._Extraction(
            "up_test", "f.pdf", images, self.vlm, tracker,
            lambda doc, tables: self.partials.append(tables), notes,
        )
        ex.run(sorted(images))
        return ex, notes


def snapshot(ex):
    return [(n, ex.records[n].table_id, ex.records[n].table_md, ex.records[n].page_ocr_start) for n in sorted(ex.records)]


def test_tables_are_numbered_in_page_then_position_order_whatever_finishes_first(monkeypatch):
    ex, _ = Env(monkeypatch, seed=1).run()
    assert sorted(ex.records) == [1, 2, 3, 4, 5]
    assert [ex.records[n].page_ocr_start for n in sorted(ex.records)] == [1, 1, 3, 4, 4]
    assert ex.records[1].table_id == "up_test_t1" and ex.records[5].table_id == "up_test_t5"


def test_output_is_identical_across_random_completion_orders(monkeypatch):
    first = snapshot(Env(monkeypatch, seed=1).run()[0])
    second = snapshot(Env(monkeypatch, seed=2).run()[0])
    sequential = snapshot(Env(monkeypatch, seed=3, delays=False).run()[0])
    assert first == second == sequential


def test_progress_never_goes_backwards_and_ends_with_every_stage_complete(monkeypatch):
    env = Env(monkeypatch, seed=4)
    env.run()
    stages = [STAGES.index(s) for s, _, _ in env.progress]
    fractions = [f for _, _, f in env.progress]
    assert stages == sorted(stages)
    assert fractions == sorted(fractions)
    assert fractions[-1] < 0.95                       # identify has not started yet
    assert env.progress[-1][0] == "identify"          # but every earlier stage is done


def test_only_one_layout_analysis_is_ever_in_flight(monkeypatch):
    env = Env(monkeypatch, seed=5)
    env.run()
    assert env.layout_max == 1


def test_a_page_with_no_tables_still_gets_its_text_and_image(monkeypatch):
    ex, _ = Env(monkeypatch, seed=6).run()
    assert "Some narrative text for page 2" in ex.page_markdown[2]
    assert sorted(ex.page_images) == [1, 2, 3, 4]


def test_a_failing_table_is_dropped_with_a_note_and_the_rest_carry_on(monkeypatch):
    env = Env(monkeypatch, seed=7, fail_ocr_for={2})
    ex, notes = env.run()
    assert sorted(ex.records) == [1, 3, 4, 5]
    assert any("Table 2 on page 1 could not be processed" in n for n in notes)
    assert env.progress[-1][0] == "identify"          # and progress still completed


def test_partial_snapshots_are_in_document_order_and_end_complete(monkeypatch):
    env = Env(monkeypatch, seed=8)
    env.run()
    assert env.partials
    for snap in env.partials:
        ids = [t["table_id"] for t in snap]
        assert ids == sorted(ids, key=lambda i: int(i.rsplit("_t", 1)[1]))
    assert len(env.partials[-1]) == 5


def test_without_the_vision_model_every_figure_is_ocr_only_and_flags_nothing(monkeypatch):
    ex, _ = Env(monkeypatch, seed=9, vlm=False).run()
    for record in ex.records.values():
        assert record.findings == []
        assert "1,00,000" in record.table_md


def test_second_read_budget_caps_calls_and_leaves_the_rest_ocr_only(monkeypatch):
    monkeypatch.setattr(pipeline.Config, "SECOND_READ_MAX_ROWS", 3)
    env = Env(monkeypatch, seed=10)
    calls = []
    orig = env.read_row
    monkeypatch.setattr(pipeline.second_read, "read_row", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    ex, _ = env.run()
    assert len(calls) == 3 and ex.rows_skipped > 0
    assert len(ex.records) == 5
