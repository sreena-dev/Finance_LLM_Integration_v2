"""Running a real PDF through the real pipeline, and scoring what comes back.

Two execution paths, because the two are useful in different situations:

``http`` (default)
    Drives the running service exactly as the gateway does -- ``POST /ingest``,
    then poll. This is the honest end-to-end path: it exercises the deployed
    code with the deployed configuration, and it needs nothing installed
    locally beyond ``requests``. It is also the only path available from the
    lightweight test venv, which carries no docling.

``inproc``
    Calls ``pipeline.run`` directly. Faster to iterate on and gives a stack
    trace instead of a job status when something breaks, but it needs the full
    ingestion dependencies (docling, torch, an OCR engine) and a reachable
    vision model.

Neither runs under a normal ``pytest``. A single document takes minutes and
needs services up; see ``test_accuracy.py`` for the gate.

Usage::

    # bootstrap an expectation file from what the pipeline currently emits
    venv/Scripts/python -m tests.accuracy.runner propose \\
        data/SK-SPSU-SSLSA-010/2022-23/SK-...pdf > tests/accuracy/cases/sk_2022_23.yaml

    # score one case, or every case
    venv/Scripts/python -m tests.accuracy.runner score tests/accuracy/cases/sk_2022_23.yaml
    venv/Scripts/python -m tests.accuracy.runner score-all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_INGESTION = os.path.dirname(os.path.dirname(_HERE))
_REPO = os.path.dirname(_INGESTION)
sys.path.insert(0, _INGESTION)

from tests.accuracy.harness import (                     # noqa: E402
    Expectation,
    Outcome,
    Scorecard,
    propose,
    score,
)

#: Where the sample filings live. Same anchor `tests/fixtures/regenerate.py`
#: uses, so a case file's `pdf:` path reads the same in both places.
DATA = os.path.join(_REPO, "data")
CASES = os.path.join(_HERE, "cases")
BASELINE = os.path.join(_HERE, "baseline.json")

DEFAULT_BASE_URL = os.environ.get("INGEST_BASE_URL", "http://127.0.0.1:12102")
#: A 78-page annual report is the long pole, and escalation will lengthen it.
POLL_TIMEOUT = float(os.environ.get("INGEST_ACCURACY_TIMEOUT", "1800"))


@dataclass
class Case:
    name: str
    pdf: str                       # relative to data/
    expectations: list[Expectation]

    @property
    def pdf_path(self) -> str:
        return os.path.join(DATA, self.pdf)


def load_case(path: str) -> Case:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Case(
        name=raw.get("name") or os.path.splitext(os.path.basename(path))[0],
        pdf=raw["pdf"],
        expectations=[Expectation.from_dict(e) for e in (raw.get("expectations") or [])],
    )


def load_all_cases() -> list[Case]:
    if not os.path.isdir(CASES):
        return []
    return [
        load_case(os.path.join(CASES, name))
        for name in sorted(os.listdir(CASES))
        if name.endswith((".yaml", ".yml"))
    ]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def run_via_http(pdf_path: str, base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    """Submit to the running service and wait for the result."""
    import requests

    with open(pdf_path, "rb") as fh:
        response = requests.post(
            f"{base_url}/ingest",
            files={"file": (os.path.basename(pdf_path), fh, "application/pdf")},
            timeout=300,
        )
    response.raise_for_status()
    job_id = response.json()["job_id"]

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        poll = requests.get(f"{base_url}/ingest/{job_id}", timeout=60)
        poll.raise_for_status()
        body = poll.json()
        status = body.get("status")
        if status == "done":
            return body["result"]
        if status == "error":
            raise RuntimeError(f"ingestion failed: {body.get('error')}")
    raise TimeoutError(f"{os.path.basename(pdf_path)} did not finish in {POLL_TIMEOUT}s")


def run_in_process(pdf_path: str) -> dict[str, Any]:
    """Call the pipeline directly. Needs the full ingestion dependencies."""
    from app import pipeline

    with open(pdf_path, "rb") as fh:
        data = fh.read()
    return pipeline.run(data, os.path.basename(pdf_path)).as_dict()


def execute(pdf_path: str, mode: str = "http") -> dict[str, Any]:
    if mode == "inproc":
        return run_in_process(pdf_path)
    return run_via_http(pdf_path)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(name: str, card: Scorecard) -> str:
    counts = card.as_dict()
    lines = [
        f"{name}: {counts['CORRECT']} correct, {counts['WRONG']} WRONG, "
        f"{counts['WITHHELD']} withheld, {counts['MISSING']} missing "
        f"(of {card.scored} verified)"
    ]
    for result in card.results:
        if result.outcome is Outcome.CORRECT:
            continue
        lines.append(
            f"    {result.outcome.value:9s} {result.expectation.row_label!r}"
            f" — {result.detail or ''}"
        )
    return "\n".join(lines)


def score_case(case: Case, mode: str = "http") -> Scorecard:
    return score(execute(case.pdf_path, mode), case.expectations)


def _cmd_propose(args) -> int:
    result = execute(args.pdf, args.mode)
    payload = {
        "name": os.path.splitext(os.path.basename(args.pdf))[0],
        "pdf": os.path.relpath(args.pdf, DATA).replace(os.sep, "/"),
        # Deliberately loud. A proposal records what the system DID, not what
        # the page SAYS; scoring one unchecked would certify current bugs.
        "_note": "Every entry is verified:false until a human checks it against "
                 "the printed page. Unverified entries are not scored.",
        "expectations": propose(result),
    }
    yaml.safe_dump(payload, sys.stdout, sort_keys=False, allow_unicode=True)
    return 0


def _cmd_score(args) -> int:
    case = load_case(args.case)
    card = score_case(case, args.mode)
    print(report(case.name, card))
    return 1 if card.count(Outcome.WRONG) else 0


def _cmd_score_all(args) -> int:
    cases = load_all_cases()
    if not cases:
        print(f"no case files in {CASES}")
        return 0
    totals: dict[str, int] = {o.value: 0 for o in Outcome}
    failed = 0
    for case in cases:
        try:
            card = score_case(case, args.mode)
        except Exception as exc:                        # noqa: BLE001
            print(f"{case.name}: ERROR — {exc}")
            failed = 1
            continue
        print(report(case.name, card))
        for key, value in card.as_dict().items():
            totals[key] += value
    print()
    print(f"TOTAL: {totals}")
    if args.write_baseline:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(totals, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"baseline written to {BASELINE}")
    return 1 if (totals[Outcome.WRONG.value] or failed) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("http", "inproc"), default="http")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose", help="dump candidate expectations for a PDF")
    p.add_argument("pdf")
    p.set_defaults(func=_cmd_propose)

    p = sub.add_parser("score", help="score one case file")
    p.add_argument("case")
    p.set_defaults(func=_cmd_score)

    p = sub.add_parser("score-all", help="score every case file")
    p.add_argument("--write-baseline", action="store_true")
    p.set_defaults(func=_cmd_score_all)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
