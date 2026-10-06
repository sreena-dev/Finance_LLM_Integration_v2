"""Auditor-tunable thresholds: defaults stay put, a saved override changes behaviour, and
nothing out of range or unknown can be persisted. No database needed."""
from __future__ import annotations
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from . import xbrl_signal_thresholds as TH
from . import xbrl_threshold_registry as RT
from . import xbrl_threshold_store as STORE
from . import xbrl_trend_thresholds as TT
from . import xbrl_trends as XT
from . import xbrl_risk_signals as XRS

HERE = Path(__file__).parent


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FDR_THRESHOLD_STORE", str(tmp_path / "thr.json"))
    STORE._cache.update(mtime=None, data=None)
    yield
    STORE._cache.update(mtime=None, data=None)


# ----------------------------------------------------------------- defaults are untouched
def test_registry_defaults_equal_the_values_written_in_code():
    for t in TH.all_thresholds():
        assert RT.entry(f"signal.{t.key}").default == t.value
    base = TT.TrendThresholds()
    for key, e in RT.REGISTRY.items():
        if key.startswith("trend.") and hasattr(base, key[6:]):
            assert e.default == getattr(base, key[6:])


def test_no_override_means_defaults_everywhere():
    assert TH.get("current_ratio_floor").value == 1.0
    assert TT.THRESHOLDS.CWIP_DRIFT_THRESHOLD == 0.05
    assert RT.value("risk.rec_rev_flag") == 0.25
    assert RT.snapshot()["overridden_count"] == 0


def test_every_registered_key_is_actually_read_by_the_code():
    """A slider for a number nothing consults would mislead; the registry must not carry one."""
    src = {p.name: p.read_text(encoding="utf-8") for p in HERE.glob("xbrl_*.py")
           if not p.name.startswith(("xbrl_threshold", "xbrl_trend_thresholds"))}
    blob = "\n".join(src.values())
    dead = []
    for key in RT.REGISTRY:
        scope, name = key.split(".", 1)
        patterns = {
            "signal": [rf"['\"]{re.escape(name)}['\"]"],
            "trend": [rf"thresholds\.{re.escape(name)}\b", rf"['\"]{re.escape(key)}['\"]"],
            "risk": [rf"['\"]{re.escape(key)}['\"]"],
            "health": [rf"['\"]{re.escape(key)}['\"]"],
        }[scope]
        if not any(re.search(p, blob) for p in patterns):
            dead.append(key)
    assert not dead, f"registered but never read: {dead}"


# ------------------------------------------------------------------------- validation
def test_unknown_key_and_out_of_range_are_rejected_and_nothing_is_saved():
    with pytest.raises(RT.ThresholdError):
        RT.apply_display_values({"signal.current_ratio_floor": 2.0, "nope.nothing": 1}, "t")
    with pytest.raises(RT.ThresholdError):
        RT.apply_display_values({"signal.current_ratio_floor": 99}, "t")        # max 5
    with pytest.raises(RT.ThresholdError):
        RT.apply_display_values({"signal.current_ratio_floor": float("nan")}, "t")
    assert not Path(STORE._path()).exists()                                      # all-or-nothing
    assert TH.get("current_ratio_floor").value == 1.0


def test_value_equal_to_default_removes_the_override():
    RT.apply_display_values({"signal.payables_growth_gap_pp": 20}, "t")
    assert STORE.override("signal.payables_growth_gap_pp") == 20
    RT.apply_display_values({"signal.payables_growth_gap_pp": 15}, "t")          # back to default
    assert STORE.override("signal.payables_growth_gap_pp") is None


def test_units_roundtrip_percent_pp_negative_and_rupees():
    RT.apply_display_values({
        "signal.ocf_to_pat_floor": 70,                  # shown as %, stored 0.70
        "trend.COVERAGE_DRIFT_THRESHOLD": 30,           # shown as a 30 % fall, stored -0.30
        "risk.cwip_significant_amount": 500,            # shown in crore, stored in rupees
    }, "t")
    assert TH.get("ocf_to_pat_floor").value == pytest.approx(0.70)
    assert TT.THRESHOLDS.COVERAGE_DRIFT_THRESHOLD == pytest.approx(-0.30)
    assert RT.value("risk.cwip_significant_amount") == pytest.approx(5e9)
    snap = {i["key"]: i for i in RT.snapshot()["items"]}
    assert snap["signal.ocf_to_pat_floor"]["value"] == 70 and snap["signal.ocf_to_pat_floor"]["overridden"]
    assert snap["trend.COVERAGE_DRIFT_THRESHOLD"]["value"] == 30


def test_reset_restores_defaults_and_history_records_who_changed_what():
    RT.apply_display_values({"signal.short_term_debt_share": 55}, "asha")
    meta = STORE.meta()
    assert meta["updated_by"] == "asha" and meta["history"][-1]["changes"][0]["from"] == 40
    RT.reset(None, "asha")
    assert TH.get("short_term_debt_share").value == 0.40 and RT.snapshot()["overridden_count"] == 0


def test_corrupt_store_degrades_to_defaults_not_an_error():
    Path(STORE._path()).write_text("{ not json", encoding="utf-8")
    STORE._cache.update(mtime=None, data=None)
    assert TH.get("current_ratio_floor").value == 1.0
    assert RT.snapshot()["overridden_count"] == 0


# ------------------------------------------- a saved value changes what the engine does
FY = dt.date(2025, 3, 31)


def _facts(**kv):
    return [{"concept_name": k, "fy_start": None, "fy_end": FY, "value_numeric": v} for k, v in kv.items()]


def test_override_changes_a_block6_signal():
    rows = _facts(Assets=1000.0, Equity=500.0, RevenueFromOperations=1000.0, TradeReceivablesCurrent=200.0)
    sigs, _ = XRS.detect_signals_and_metrics(rows, [])
    assert not any(s["id"] == "SIG_REC_CONCENTRATION" for s in sigs)            # 20 % < default 25 %
    RT.apply_display_values({"risk.rec_rev_flag": 15}, "t")
    sigs, _ = XRS.detect_signals_and_metrics(rows, [])
    assert any(s["id"] == "SIG_REC_CONCENTRATION" for s in sigs)                # now 20 % > 15 %


def test_override_changes_the_trend_tile_and_its_highlight():
    D = {y: dt.date(y, 3, 31) for y in (2024, 2025)}
    rows = [{"concept_name": k, "fy_start": None, "fy_end": D[y], "value_numeric": v}
            for y, kv in ((2024, dict(Assets=1000.0, CapitalWorkInProgress=52.9)),
                          (2025, dict(Assets=1000.0, CapitalWorkInProgress=65.9)))      # +1.30 pp
            for k, v in kv.items()]
    series = XT.assemble_time_series(rows)

    def s09():
        cs = XT.compute_common_size_and_drift(series)
        sigs = XT.evaluate_trend_signals(series, cs, XT.compute_dupont_decomposition(series))
        return [s for s in sigs if s["signal_id"] == "S09"], cs

    tiles, cs = s09()
    assert tiles == [] and cs["highlights"] == []                              # below the 5 pp default
    RT.apply_display_values({"trend.CWIP_DRIFT_THRESHOLD": 1.0}, "t")
    tiles, cs = s09()
    assert len(tiles) == 1 and "+1.30 pp" in tiles[0]["observation"]
    assert any(h["item"].startswith("Capital Work") for h in cs["highlights"])


def test_threshold_labels_in_block6_follow_the_override():
    from . import xbrl_risk_deterministic as DET
    assert DET._rc02_metrics({"trade_rec": 1.0, "rev": 10.0})[-1]["value"] == ">25%"
    RT.apply_display_values({"risk.rec_rev_flag": 15}, "t")
    assert DET._rc02_metrics({"trade_rec": 1.0, "rev": 10.0})[-1]["value"] == ">15%"


def test_report_note_lists_overrides_only_when_there_are_some():
    assert RT.applied_overrides() == []
    RT.apply_display_values({"signal.government_support_share": 25}, "t")
    ov = RT.applied_overrides()
    assert len(ov) == 1 and ov[0]["value"] == 25 and ov[0]["default"] == 15
