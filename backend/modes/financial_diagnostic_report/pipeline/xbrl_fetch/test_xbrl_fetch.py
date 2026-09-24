"""
Tests for `xbrl_fetch.py` and `xbrl_concepts.py`. The registry checks are hermetic
(no DB); the live check only runs when `XBRLAS_PG_PASSWORD` is set in the environment,
so this file never fails in a context with no DB access — it skips, not errors.

Run from pipeline/: python -m xbrl_fetch.test_xbrl_fetch
"""
from __future__ import annotations
import os

from .xbrl_concepts import METRICS, all_concepts, BY_ID


def test_registry_has_no_duplicate_ids():
    ids = [m.id for m in METRICS]
    assert len(ids) == len(set(ids))


def test_all_concepts_covers_every_metric_numerator_and_denominator():
    concepts = set(all_concepts())
    for m in METRICS:
        for c in m.concepts:
            assert c in concepts, f"{m.id} numerator concept {c!r} missing from all_concepts()"
        for c in m.denominator_concepts:
            assert c in concepts, f"{m.id} denominator concept {c!r} missing from all_concepts()"


def test_all_concepts_has_no_duplicates():
    concepts = all_concepts()
    assert len(concepts) == len(set(concepts))


def test_derived_metric_declares_a_denominator():
    for m in METRICS:
        if m.kind == "derived":
            assert m.denominator_concepts, f"{m.id} is 'derived' but has no denominator"


def test_by_id_matches_metrics_tuple():
    assert set(BY_ID) == {m.id for m in METRICS}


def test_borrowings_sums_two_concepts():
    m = BY_ID["X17"]
    assert set(m.concepts) == {"BorrowingsCurrent", "BorrowingsNoncurrent"}


# ---------------------------------------------------------------------------------
# Live check — only runs with real DB access, and only checks shape/speed, not
# specific values (those are the golden fixture in test_xbrl_dashboard.py, which
# runs with no DB at all so it can be part of every test run).
# ---------------------------------------------------------------------------------

def test_live_fetch_matches_expected_row_shape():
    if not os.environ.get("XBRLAS_PG_PASSWORD"):
        print("SKIP (no XBRLAS_PG_PASSWORD in environment — no DB access)")
        return
    from . import xbrl_fetch as F

    doc = ("101_F92854371_20240229_ZA4X_U40102DL2008PTC177307_"
           "Instance_Ntpc BHEL_2022-23.xml")
    rows = F.fetch_doc_metrics(doc)
    assert rows, "expected rows for a known-good filing"
    for r in rows[:1]:
        assert set(r) >= {"concept_name", "fy_start", "fy_end", "value_numeric", "unit"}
        assert r["unit"] == "INR"

    meta = F.fetch_doc_meta(doc)
    assert meta["entity_cin"] == "U40102DL2008PTC177307"
    assert meta["company_name"] == "NTPC BHEL POWER PROJECTS PRIVATE LIMITED"

    missing = F.fetch_doc_meta("this-doc-id-does-not-exist.xml")
    assert missing is None


if __name__ == "__main__":
    import sys
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    sys.exit(1 if failures else 0)
