"""Hermetic tests for the trend-capture ContextVar and the section-9.3 pairing.

No database, no model, no network — mirrors test_entity_resolution.py's shape.

Run:  python -m pytest modes/financial_statement/test_trend_capture.py
      python modes/financial_statement/test_trend_capture.py   (no pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python test_trend_capture.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from modes.financial_statement import trend_capture as TC  # noqa: E402
from modes.financial_statement import trend_pairs as TP  # noqa: E402


class _FakeTrendAnalysisTools:
    """Stands in for the vendored TrendAnalysisTools class for install()."""

    @staticmethod
    def _format_statement_trend(statement_label, rows, years_sorted, discrepancies):
        return f"rendered:{statement_label}:{len(rows)}"


# ---------------------------------------------------------------------------
# install() contract
# ---------------------------------------------------------------------------

def test_install_rebinds_without_changing_return_value():
    original = _FakeTrendAnalysisTools._format_statement_trend
    TC.install(_FakeTrendAnalysisTools)
    try:
        out = _FakeTrendAnalysisTools._format_statement_trend(
            "Balance Sheet", [{"label": "x", "cells": [], "yoy": [], "cagr": None, "significant": False}],
            [2023, 2024], [],
        )
        assert out == "rendered:Balance Sheet:1"
    finally:
        _FakeTrendAnalysisTools._format_statement_trend = staticmethod(original)


def test_install_rejects_a_renamed_method():
    class Wrong:
        pass

    try:
        TC.install(Wrong)
        raise AssertionError("expected TrendCaptureContractError")
    except TC.TrendCaptureContractError:
        pass


def test_install_rejects_a_resignatured_method():
    class Wrong:
        @staticmethod
        def _format_statement_trend(only_one_arg):
            return ""

    try:
        TC.install(Wrong)
        raise AssertionError("expected TrendCaptureContractError")
    except TC.TrendCaptureContractError:
        pass


# ---------------------------------------------------------------------------
# Capture behaviour
# ---------------------------------------------------------------------------

def test_no_capture_active_is_a_pure_no_op():
    """Calling the wrapped function with no begin() active must not error and
    must not populate anything a later begin()/collect() would see."""
    original = _FakeTrendAnalysisTools._format_statement_trend
    TC.install(_FakeTrendAnalysisTools)
    try:
        out = _FakeTrendAnalysisTools._format_statement_trend(
            "P&L", [{"label": "revenue", "cells": [1.0], "yoy": [], "cagr": None, "significant": False}],
            [2024], [],
        )
        assert out == "rendered:P&L:1"

        token = TC.begin()
        captured = TC.collect(token)
        assert captured == []
    finally:
        _FakeTrendAnalysisTools._format_statement_trend = staticmethod(original)


def test_two_sequential_calls_in_one_request_both_land_in_the_list():
    original = _FakeTrendAnalysisTools._format_statement_trend
    TC.install(_FakeTrendAnalysisTools)
    try:
        token = TC.begin()
        _FakeTrendAnalysisTools._format_statement_trend(
            "Balance Sheet", [{"label": "ppe", "cells": [1.0], "yoy": [], "cagr": None, "significant": False}],
            [2024], [],
        )
        _FakeTrendAnalysisTools._format_statement_trend(
            "P&L", [{"label": "revenue", "cells": [2.0], "yoy": [], "cagr": None, "significant": False}],
            [2024], [],
        )
        captured = TC.collect(token)
        assert [c["statement_label"] for c in captured] == ["Balance Sheet", "P&L"]
    finally:
        _FakeTrendAnalysisTools._format_statement_trend = staticmethod(original)


def test_empty_rows_are_not_captured():
    """_format_statement_trend's own early-return case (no rows found) must
    not add a useless entry — nothing for a chart to draw from it."""
    original = _FakeTrendAnalysisTools._format_statement_trend
    TC.install(_FakeTrendAnalysisTools)
    try:
        token = TC.begin()
        _FakeTrendAnalysisTools._format_statement_trend("Cash Flow", [], [2024], [])
        assert TC.collect(token) == []
    finally:
        _FakeTrendAnalysisTools._format_statement_trend = staticmethod(original)


def test_a_fresh_begin_after_collect_starts_empty():
    original = _FakeTrendAnalysisTools._format_statement_trend
    TC.install(_FakeTrendAnalysisTools)
    try:
        token1 = TC.begin()
        _FakeTrendAnalysisTools._format_statement_trend(
            "Balance Sheet", [{"label": "x", "cells": [1.0], "yoy": [], "cagr": None, "significant": False}],
            [2024], [],
        )
        assert len(TC.collect(token1)) == 1

        token2 = TC.begin()
        assert TC.collect(token2) == []
    finally:
        _FakeTrendAnalysisTools._format_statement_trend = staticmethod(original)


# ---------------------------------------------------------------------------
# trend_pairs — divergence detection
# ---------------------------------------------------------------------------

def _row(label, yoy):
    return {"label": label, "cells": [], "yoy": yoy, "cagr": None, "significant": False}


def test_revenue_flat_receivables_spiking_flags_at_the_right_year():
    """The MTNL-shaped case verified earlier this session: revenue +1.2%,
    trade receivables +60% — a textbook section-9.3 divergence."""
    rows = [
        _row("Revenue from Operations", [(50.0, 1.2)]),
        _row("Trade Receivables", [(510.0, 60.0)]),
    ]
    divergences = TP.find_divergences([2023, 2024], rows)
    assert len(divergences) == 1
    d = divergences[0]
    assert d["year"] == 2024
    assert d["delta_pct"] > TP.DEFAULT_THRESHOLD_PCT
    assert "revenue" in d["line_a"].lower()
    assert "receivable" in d["line_b"].lower()


def test_no_divergence_when_both_move_together():
    rows = [
        _row("Revenue from Operations", [(50.0, 10.0)]),
        _row("Trade Receivables", [(20.0, 12.0)]),
    ]
    assert TP.find_divergences([2023, 2024], rows) == []


def test_missing_pair_side_is_silently_skipped():
    """No trade receivables line present at all — must not error, must not
    fabricate a comparison against nothing."""
    rows = [_row("Revenue from Operations", [(50.0, 10.0)])]
    assert TP.find_divergences([2023, 2024], rows) == []


def test_none_percentage_never_compared():
    """A year with only an absolute figure (no prior-year value to diff
    against) must not be treated as a 0% or missing divergence."""
    rows = [
        _row("Revenue from Operations", [(None, None)]),
        _row("Trade Receivables", [(None, None)]),
    ]
    assert TP.find_divergences([2023, 2024], rows) == []


def test_annotate_never_mutates_the_input_entries():
    entry = {
        "statement_label": "P&L",
        "years_sorted": [2023, 2024],
        "rows": [
            _row("Revenue from Operations", [(50.0, 1.2)]),
            _row("Trade Receivables", [(510.0, 60.0)]),
        ],
    }
    before = dict(entry)
    out = TP.annotate([entry])
    assert entry == before  # original untouched
    assert len(out[0]["divergences"]) == 1


if __name__ == "__main__":
    import inspect

    tests = [obj for name, obj in list(globals().items())
             if name.startswith("test_") and inspect.isfunction(obj)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
