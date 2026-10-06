"""Structural drift must be measured between periods that HAVE a common-size basis.

Regression: a filing's earliest period is often opening-balance-only (equity but no
total assets). Its shares were computed as 0.0, so every drift equalled the latest
period's static share (e.g. CWIP "drifted +6.59 pp" when it moved +1.30 pp).
"""
from __future__ import annotations
import datetime as dt
import pytest

from . import xbrl_trends as XT

D = {y: dt.date(y, 3, 31) for y in (2023, 2024, 2025)}


def _rows(year, **facts):
    return [{"concept_name": k, "fy_start": None, "fy_end": D[year], "value_numeric": v} for k, v in facts.items()]


def _series(with_base_assets: bool):
    rows = _rows(2023, Equity=300.0, **({"Assets": 1000.0, "CapitalWorkInProgress": 10.0} if with_base_assets else {}))
    rows += _rows(2024, Assets=1000.0, CapitalWorkInProgress=52.9, PropertyPlantAndEquipment=500.0)   # 5.29 %
    rows += _rows(2025, Assets=1000.0, CapitalWorkInProgress=65.9, PropertyPlantAndEquipment=520.0)   # 6.59 %
    return XT.assemble_time_series(rows)


def test_drift_skips_period_without_assets():
    cs = XT.compute_common_size_and_drift(_series(with_base_assets=False))
    assert cs["periods"][0]["defined"] is False
    assert cs["periods"][0]["asset_mix"]["cwip_share"] is None          # not 0.0
    assert cs["drift_base_period"] == "2024-03-31" and cs["drift_end_period"] == "2025-03-31"
    assert cs["drifts"]["asset_cwip_share"] == pytest.approx(1.30)      # NOT 6.59
    assert cs["drifts"]["asset_ppe_share"] == pytest.approx(2.0)


def test_drift_uses_first_period_when_it_has_assets():
    cs = XT.compute_common_size_and_drift(_series(with_base_assets=True))
    assert cs["drift_base_period"] == "2023-03-31"
    assert cs["drifts"]["asset_cwip_share"] == pytest.approx(6.59 - 1.0)


def test_no_drift_when_fewer_than_two_periods_have_assets():
    rows = _rows(2023, Equity=300.0) + _rows(2025, Assets=1000.0, CapitalWorkInProgress=65.9)
    cs = XT.compute_common_size_and_drift(XT.assemble_time_series(rows))
    assert cs["drifts"] == {} and "not computed" in cs["drift_note"]


def test_s09_below_threshold_does_not_fire_on_a_phantom_drift():
    series = _series(with_base_assets=False)          # true drift +1.30 pp, threshold 5 pp
    cs = XT.compute_common_size_and_drift(series)
    sigs = XT.evaluate_trend_signals(series, cs, XT.compute_dupont_decomposition(series))
    assert not any(s["signal_id"] == "S09" for s in sigs)


def test_s09_tile_quotes_the_same_periods_as_the_drift():
    rows = _rows(2023, Equity=300.0)
    rows += _rows(2024, Assets=1000.0, CapitalWorkInProgress=52.9)       # 5.29 %
    rows += _rows(2025, Assets=1000.0, CapitalWorkInProgress=125.0)      # 12.50 %  -> +7.21 pp
    series = XT.assemble_time_series(rows)
    cs = XT.compute_common_size_and_drift(series)
    s09 = next(s for s in XT.evaluate_trend_signals(series, cs, XT.compute_dupont_decomposition(series))
               if s["signal_id"] == "S09")
    hl = next(h for h in cs["highlights"] if h["item"].startswith("Capital Work"))
    assert "+7.21 pp" in s09["observation"] and hl["drift_pp"] == pytest.approx(7.21)
    assert "2024-03-31" in s09["observation"] and "2025-03-31" in s09["observation"]
