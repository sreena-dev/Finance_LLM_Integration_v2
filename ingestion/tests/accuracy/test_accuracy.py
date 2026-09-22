"""The gate: an extraction change must not make the corpus worse.

Skipped unless ``INGEST_ACCURACY=1``. It runs real documents through the real
pipeline -- minutes per file, and it needs the ingestion service up (or the full
docling stack for ``--mode inproc``) -- so it has no business in the default
``pytest`` run alongside tests that finish in milliseconds.

    # service on 12102, from ingestion/
    INGEST_ACCURACY=1 venv/Scripts/python -m pytest tests/accuracy -q

The asymmetry between the three assertions is the whole design:

``WRONG`` may never rise.
    A wrong figure is the only outcome that makes this tool dangerous. An
    auditor who is told nothing checks the statement; one who is told 8 when
    the page says 24,91,771.03 may not. This is a hard gate, and it is not
    tradeable against gains elsewhere -- a change that recovers thirty figures
    and invents one is a bad change.

``CORRECT`` may not fall, and ``MISSING`` may not rise.
    Regression guards, but softer in kind: they say the change did not quietly
    stop extracting things that used to work.

``WITHHELD`` is deliberately ungated.
    It is the safe failure, and it moves in both directions for good reasons.
    Driving it down is the point of the work; a change that converts WITHHELD
    into CORRECT is exactly what is wanted, and one that converts MISSING into
    WITHHELD is also an improvement -- silent loss became disclosed loss.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

from tests.accuracy.harness import Outcome                      # noqa: E402
from tests.accuracy.runner import (                             # noqa: E402
    BASELINE,
    load_all_cases,
    report,
    score_case,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("INGEST_ACCURACY") != "1",
    reason="set INGEST_ACCURACY=1 to run the corpus accuracy gate (slow; needs the service)",
)

MODE = os.environ.get("INGEST_ACCURACY_MODE", "http")


def _baseline() -> dict[str, int]:
    if not os.path.exists(BASELINE):
        pytest.skip(f"no baseline at {BASELINE}; run `runner.py score-all --write-baseline`")
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


def test_corpus_accuracy_has_not_regressed():
    cases = load_all_cases()
    if not cases:
        pytest.skip("no case files")

    totals = {o.value: 0 for o in Outcome}
    lines = []
    for case in cases:
        card = score_case(case, MODE)
        lines.append(report(case.name, card))
        for key, value in card.as_dict().items():
            totals[key] += value

    detail = "\n".join(lines)
    base = _baseline()

    # Hard gate. Stated first and separately so a failure names the right thing.
    assert totals[Outcome.WRONG.value] <= base.get(Outcome.WRONG.value, 0), (
        f"WRONG rose from {base.get(Outcome.WRONG.value, 0)} to "
        f"{totals[Outcome.WRONG.value]} — a figure is being emitted that the "
        f"printed page contradicts. This is not tradeable against gains "
        f"elsewhere.\n{detail}"
    )
    assert totals[Outcome.CORRECT.value] >= base.get(Outcome.CORRECT.value, 0), (
        f"CORRECT fell from {base.get(Outcome.CORRECT.value, 0)} to "
        f"{totals[Outcome.CORRECT.value]}\n{detail}"
    )
    assert totals[Outcome.MISSING.value] <= base.get(Outcome.MISSING.value, 0), (
        f"MISSING rose from {base.get(Outcome.MISSING.value, 0)} to "
        f"{totals[Outcome.MISSING.value]} — figures are being silently dropped\n{detail}"
    )


def test_every_case_file_has_at_least_one_verified_expectation():
    """A case of nothing but proposals scores nothing and passes vacuously."""
    for case in load_all_cases():
        assert any(e.verified for e in case.expectations), (
            f"{case.name} has no verified expectations — it would pass without "
            f"testing anything. Check its figures against the printed page and "
            f"set verified: true."
        )
