"""
Hermetic regression over run persistence and the population queries. No DB, no network —
the store is a temporary SQLite file built from rows written here.

WHAT THESE CHECKS ARE FOR
-------------------------
Reports used to live in an in-process dict and vanish on restart, on the reasoning that
they regenerate cheaply from the facts. That is true and beside the point: the comparison
ACROSS runs is information that never existed. "Which figure blocks the most diagnostics"
and "which entities raise anything at all" are not answerable one report at a time.

Two properties matter more than the rest and are asserted first:
  - the population is counted over the LATEST run per entity, so tuning a threshold on one
    entity five times does not make that entity outvote the other forty-nine;
  - a blocked input is counted in BOTH units, because "blocks 40 diagnostics on 4 entities"
    and "blocks 4 diagnostics on 40 entities" are different pieces of work.

Run: python -m fdr.test_query
"""
from __future__ import annotations
import tempfile
from pathlib import Path

from .facts_store import FactsStore
from .model import BLOCKED_DATA, BLOCKED_SCOPE, ABSTAIN, FIRED, NOT_FIRED

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def _sig(sid: str, cluster: str, status: str, *, code: str | None = None,
         blocked: str | None = None, missing: tuple[str, ...] = ()) -> dict:
    return {"signal_id": sid, "cluster_id": cluster, "status": status,
            "reason_code": code, "blocked_by": blocked, "severity": None,
            "confidence": None, "missing_inputs": missing}


def _store(tmp: Path) -> FactsStore:
    return FactsStore(tmp / "t.sqlite3")


def test_a_run_survives_being_written_and_reopened() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        with _store(p) as st:
            st.save_run("r1", "ENT", status="COMPLETE", started_at="2026-08-05T10:00:00",
                        finished_at="2026-08-05T10:00:01", versions={"v": "1"},
                        coverage={"c": 1}, report={"entity": "ENT"})
        with _store(p) as st:                       # a different connection entirely
            got = st.get_run("r1")
        check(got is not None and got["report"]["entity"] == "ENT",
              "a persisted run could not be read back after the store was reopened")


def test_population_counts_the_latest_run_per_entity_only() -> None:
    """An entity re-run while a threshold is tuned must not outvote the others."""
    with tempfile.TemporaryDirectory() as d:
        with _store(Path(d)) as st:
            for i, ts in enumerate(("2026-08-01T00:00:00", "2026-08-02T00:00:00",
                                    "2026-08-03T00:00:00")):
                rid = f"old{i}"
                st.save_run(rid, "NOISY", status="COMPLETE", started_at=ts,
                            finished_at=ts, versions={}, coverage={}, report={})
                st.save_signal_results(rid, "NOISY", [_sig("S01", "RC-WC", FIRED)])
            st.save_run("q", "QUIET", status="COMPLETE", started_at="2026-08-01T00:00:00",
                        finished_at="x", versions={}, coverage={}, report={})
            st.save_signal_results("q", "QUIET", [_sig("S01", "RC-WC", NOT_FIRED)])
            pop = st.population()
        check(pop["entities_with_a_run"] == 2,
              f"expected 2 entities, got {pop['entities_with_a_run']} — earlier runs of the "
              f"same entity are being counted again")
        check(pop["by_status"].get(FIRED) == 1,
              f"the three NOISY runs should contribute one FIRED, got "
              f"{pop['by_status'].get(FIRED)}")


def test_blocking_inputs_reports_both_units() -> None:
    """`signals` is coverage lost; `entities` is how widespread the gap is."""
    with tempfile.TemporaryDirectory() as d:
        with _store(Path(d)) as st:
            # ocf: 1 entity, 3 diagnostics.  pat: 3 entities, 1 diagnostic each.
            st.save_run("a", "A", status="COMPLETE", started_at="1", finished_at="1",
                        versions={}, coverage={}, report={})
            st.save_signal_results("a", "A", [
                _sig("S04", "RC-WC", ABSTAIN, code="INPUT_SERIES_BROKEN",
                     blocked=BLOCKED_DATA, missing=("ocf",)),
                _sig("S06", "RC-REC", ABSTAIN, code="INPUT_SERIES_BROKEN",
                     blocked=BLOCKED_DATA, missing=("ocf",)),
                _sig("S18", "RC-EST", ABSTAIN, code="INPUT_SERIES_BROKEN",
                     blocked=BLOCKED_DATA, missing=("ocf",)),
            ])
            for e in ("B", "C", "D"):
                st.save_run(e, e, status="COMPLETE", started_at="1", finished_at="1",
                            versions={}, coverage={}, report={})
                st.save_signal_results(e, e, [
                    _sig("S13", "RC-FUND", ABSTAIN, code="INPUT_NEVER_BOUND",
                         blocked=BLOCKED_DATA, missing=("pat",))])
            rows = {r["canonical_key"]: r for r in st.blocking_inputs()}
        check(rows["ocf"]["signals"] == 3 and rows["ocf"]["entities"] == 1,
              f"ocf should be 3 signals / 1 entity, got {rows.get('ocf')}")
        check(rows["pat"]["signals"] == 3 and rows["pat"]["entities"] == 3,
              f"pat should be 3 signals / 3 entities, got {rows.get('pat')}")


def test_scope_blocked_signals_are_excluded_from_the_fix_list() -> None:
    """A diagnostic nobody has built yet is not an extraction ticket, so it must not
    appear on a list whose whole purpose is 'what do I fix in the binder next'."""
    with tempfile.TemporaryDirectory() as d:
        with _store(Path(d)) as st:
            st.save_run("r", "E", status="COMPLETE", started_at="1", finished_at="1",
                        versions={}, coverage={}, report={})
            st.save_signal_results("r", "E", [
                _sig("S21", "RC-DEP", ABSTAIN, code="NEEDS_NOTE_EXTRACTION",
                     blocked=BLOCKED_SCOPE, missing=("unspent_grant_balance",)),
                _sig("S01", "RC-WC", ABSTAIN, code="INPUT_NEVER_BOUND",
                     blocked=BLOCKED_DATA, missing=("trade_payables",)),
            ])
            keys = {r["canonical_key"] for r in st.blocking_inputs()}
        check("trade_payables" in keys, "a DATA-blocked key is missing from the fix list")
        check("unspent_grant_balance" not in keys,
              "a SCOPE-blocked key appears on the binder fix list, which will send someone "
              "hunting for a caption that no parser reads")


def test_the_register_is_fully_enumerable_and_filterable() -> None:
    """§16 — one query returns every signal with its status. That single property is what
    makes 'nothing was fabricated' testable rather than aspirational."""
    with tempfile.TemporaryDirectory() as d:
        with _store(Path(d)) as st:
            st.save_run("r", "E", status="COMPLETE", started_at="1", finished_at="1",
                        versions={}, coverage={}, report={})
            st.save_signal_results("r", "E", [
                _sig("S01", "RC-WC", FIRED),
                _sig("S02", "RC-WC", ABSTAIN, code="INPUT_NEVER_BOUND",
                     blocked=BLOCKED_DATA, missing=("trade_payables",)),
                _sig("S03", "RC-WC", NOT_FIRED),
            ])
            check(len(st.query_signals()) == 3, "the register did not return every signal")
            check(len(st.query_signals(status=FIRED)) == 1, "status filter is wrong")
            check(len(st.query_signals(blocked_by=BLOCKED_DATA)) == 1,
                  "blocked_by filter is wrong")
            check(len(st.query_signals(reason_code="INPUT_NEVER_BOUND")) == 1,
                  "reason_code filter is wrong")
            check(st.query_signals(signal_id="S02")[0]["missing"] == ["trade_payables"],
                  "missing inputs did not round-trip through the store")


def test_rewriting_a_run_replaces_rather_than_duplicates() -> None:
    with tempfile.TemporaryDirectory() as d:
        with _store(Path(d)) as st:
            st.save_run("r", "E", status="COMPLETE", started_at="1", finished_at="1",
                        versions={}, coverage={}, report={})
            st.save_signal_results("r", "E", [_sig("S01", "RC-WC", FIRED)])
            st.save_signal_results("r", "E", [_sig("S01", "RC-WC", NOT_FIRED)])
            rows = st.query_signals()
        check(len(rows) == 1 and rows[0]["status"] == NOT_FIRED,
              f"re-saving a run duplicated or failed to replace its signals: {rows}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print("  -", f)
        return 1
    print(f"OK - {len(tests)} checks passed over run persistence and population queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
