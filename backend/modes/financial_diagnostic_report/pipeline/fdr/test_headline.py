"""
Hermetic regression over the executive dashboard. No DB, no network, no model.

    python -m fdr.test_headline

Three properties are under test, and they fail in different ways:

  DECLARED   every tile states a unit, a salience direction, a basis and a formula, and
             the registry is entity-agnostic — no tile in the core is gated on a business
             model, and every overlay tile is gated on one.

  COMPUTED   the arithmetic is right, and — the part that actually bites — every way of
             NOT computing produces the correct state and a reason. A dashboard is easy
             to make right on the entity where everything binds; the whole design exists
             for the entity where it does not.

  DELIVERED  the tiles reach the reader: on the report object, in the §14.3 JSON payload,
             and in the rendered block 2 — including the unbound ones, which are the ones
             a renderer silently drops.

The panels below are built by hand rather than read from any corpus, so this suite proves
the module and not the state of a database.
"""
from __future__ import annotations

import json

from . import headline as HL
from . import panel as PN
from .assemble import build_report
from .model import BusinessProfile
from .panel import Panel, PanelCell
from .render import render
from .safe_language import lint

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- panel fixtures ----------------------------------------------------------------

def _panel(series: dict[str, dict[str, float]], *, years: tuple[str, ...],
           verdict: str = "CONFIRMED", unit_conf: str = "HIGH") -> Panel:
    """A panel from {key: {year: value}}, with every cell fully trusted by default."""
    cells: dict[tuple[str, str], PanelCell] = {}
    for key, by_year in series.items():
        for y, v in by_year.items():
            cells[(key, y)] = PanelCell(
                value=float(v), fy_label=y, period_end=f"{y[-2:]}-03-31",
                unit_confidence=unit_conf, verify_verdict=verdict, unit_scale="lakh")
    return Panel("TEST", PN.STANDALONE, years, cells,
                 gate={"status": "OK", "trend_capable": len(years) >= 3})


Y3 = ("FY2022-23", "FY2023-24", "FY2024-25")

# A complete entity: every core tile computes, and the movements are chosen so that one
# tile of each salience declaration lands on each side of the attention band.
FULL = _panel({
    "revenue":                   {"FY2022-23": 1_000.0, "FY2023-24": 1_200.0,
                                  "FY2024-25": 1_100.0},
    "pat":                       {"FY2022-23": 100.0, "FY2023-24": 120.0,
                                  "FY2024-25": 60.0},
    "ocf":                       {"FY2022-23": 180.0, "FY2023-24": 200.0,
                                  "FY2024-25": 150.0},
    "total_assets":              {"FY2022-23": 5_000.0, "FY2023-24": 5_200.0,
                                  "FY2024-25": 5_400.0},
    "depreciation":              {"FY2022-23": 300.0, "FY2023-24": 320.0,
                                  "FY2024-25": 340.0},
    "finance_costs":             {"FY2022-23": 40.0, "FY2023-24": 42.0,
                                  "FY2024-25": 63.0},
    "trade_receivables":         {"FY2022-23": 100.0, "FY2023-24": 110.0,
                                  "FY2024-25": 220.0},
    "long_term_borrowings":      {"FY2022-23": 400.0, "FY2023-24": 420.0,
                                  "FY2024-25": 500.0},
    "short_term_borrowings":     {"FY2022-23": 100.0, "FY2023-24": 100.0,
                                  "FY2024-25": 100.0},
    "total_equity":              {"FY2022-23": 5_000.0, "FY2023-24": 5_200.0,
                                  "FY2024-25": 5_000.0},
    "total_current_assets":      {"FY2022-23": 1_400.0, "FY2023-24": 1_400.0,
                                  "FY2024-25": 1_400.0},
    "total_current_liabilities": {"FY2022-23": 1_000.0, "FY2023-24": 1_000.0,
                                  "FY2024-25": 1_000.0},
    "total_tax":                 {"FY2022-23": 30.0, "FY2023-24": 36.0,
                                  "FY2024-25": 30.0},
    "pbt":                       {"FY2022-23": 130.0, "FY2023-24": 156.0,
                                  "FY2024-25": 90.0},
}, years=Y3)


def _tile(values, tile_id: str):
    return next(v for v in values if v.tile_id == tile_id)


# ---- declared ----------------------------------------------------------------------

def test_registry_is_entity_agnostic() -> None:
    """The core must not be gated on anything the entity happens to be."""
    for t in HL.CORE_TILES:
        check(t.is_core, f"{t.id} is in the core but gated on {sorted(t.business_models)}")
        check(not t.binder_required,
              f"{t.id} is in the core but declares it cannot be bound — a core tile that "
              f"never computes is not a core tile")
    for t in HL.OVERLAY_TILES:
        check(bool(t.business_models),
              f"{t.id} is an overlay with no business model to gate it, so it would show "
              f"on every entity")


def test_every_tile_declares_what_the_renderer_must_not_infer() -> None:
    for t in HL.TILES:
        check(t.salience in HL.SALIENCE, f"{t.id}: salience {t.salience!r} not declared")
        check(t.unit in HL.UNITS, f"{t.id}: unit {t.unit!r} not declared")
        check(bool(t.basis.strip()), f"{t.id}: no basis")
        check(bool(t.formula.strip()), f"{t.id}: no formula")


def test_rates_move_in_points_and_amounts_move_relatively() -> None:
    """Subtracting one rate from another does not give a rate (see `rules._pp`)."""
    for t in HL.TILES:
        want = HL.POINTS if t.unit == HL.PERCENT else HL.RELATIVE
        check(t.delta_kind == want, f"{t.id}: delta kind {t.delta_kind} for a {t.unit}")


def test_overlay_gating_matches_the_business_model() -> None:
    core = {t.id for t in HL.CORE_TILES}
    check({t.id for t in HL.tiles_for(None)} == core,
          "an unformed business model should still get the whole core and no overlay")
    petro = {t.id for t in HL.tiles_for("petroleum")}
    check(core < petro, "petroleum should add overlay tiles to the core")
    check("H51" in petro, "the oil-and-gas overlay tile is not gated onto petroleum")
    check("H51" not in {t.id for t in HL.tiles_for("telecom")},
          "the oil-and-gas overlay tile leaked onto an unrelated model")


def test_declared_but_unbindable_tiles_are_enumerable() -> None:
    ids = {i for i, _, _ in HL.work_items()}
    check(ids == {t.id for t in HL.TILES if t.binder_required},
          "work_items() disagrees with the registry")
    for _i, _l, need in HL.work_items():
        check(bool(need.strip()), "a work item that does not say what it needs is not one")


# ---- computed ----------------------------------------------------------------------

def test_levels_and_ratios_are_arithmetically_right() -> None:
    v = HL.compute(FULL)
    check(abs(_tile(v, "H01").value - 1_100.0) < 1e-9, "H01 revenue level wrong")
    # (500 + 100) / 5000
    check(abs(_tile(v, "H08").value - 0.12) < 1e-9, "H08 debt-equity wrong")
    # 1400 / 1000
    check(abs(_tile(v, "H09").value - 1.4) < 1e-9, "H09 current ratio wrong")
    # 30 / 90
    check(abs(_tile(v, "H10").value - (30.0 / 90.0)) < 1e-9, "H10 tax rate wrong")


def test_movement_is_one_year_and_carries_its_comparison_year() -> None:
    """§9.5 at the presentation layer: a delta must be unrenderable without its year."""
    for t in HL.compute(FULL):
        if t.delta is None:
            continue
        check(t.comparison_year == "FY2023-24",
              f"{t.tile_id}: compares against {t.comparison_year!r}, not the prior year")
        check(t.comparison_year in t.movement_label,
              f"{t.tile_id}: movement label omits the comparison year")
        check("trend" not in t.movement_label.lower(),
              f"{t.tile_id}: a one-year movement is labelled as a trend")
    rev = _tile(HL.compute(FULL), "H01")
    check(abs(rev.delta - (1_100.0 - 1_200.0) / 1_200.0) < 1e-9, "H01 delta wrong")


def test_a_rate_moves_in_percentage_points() -> None:
    t = _tile(HL.compute(FULL), "H10")
    want = (30.0 / 90.0) - (36.0 / 156.0)
    check(abs(t.delta - want) < 1e-9, "H10 delta is not a percentage-point difference")
    check(t.movement_label.endswith("vs FY2023-24") and "pp" in t.movement_label,
          f"H10 movement should be in points: {t.movement_label!r}")


def test_salience_is_declared_not_taken_from_the_sign() -> None:
    """The property the CA template's receivables tile exists to demonstrate."""
    v = HL.compute(FULL)
    rec, rev = _tile(v, "H07"), _tile(v, "H01")
    check(rec.delta > 0 and rec.attention,
          "receivables doubled and was not marked — a rise here is the adverse case")
    check(rev.delta < 0 and rev.attention is False,
          "revenue fell 8.3%, inside the attention band, and should not be marked")
    pat = _tile(v, "H02")
    check(pat.delta < 0 and pat.attention,
          "profit halved and was not marked — a fall here is the adverse case")
    # Two tiles, same sign, opposite readings. If salience were inferred from the sign
    # this check could not pass.
    check(rec.delta > 0 and _tile(v, "H04").delta > 0
          and _tile(v, "H04").attention is False,
          "total assets rose and must not be marked — its direction is declared neutral")


def test_a_neutral_tile_is_never_marked_however_large_the_movement() -> None:
    p = _panel({"depreciation": {"FY2022-23": 10.0, "FY2023-24": 10.0,
                                 "FY2024-25": 1_000.0}}, years=Y3)
    t = _tile(HL.compute(p), "H05")
    check(t.state == HL.OK and t.delta > 90, "H05 should have computed a large movement")
    check(t.attention is False,
          "a tile whose direction is declared neutral must never be marked, however "
          "large the movement — the mark means 'adverse direction', not 'big'")


def test_the_cover_read_is_computed_against_its_declared_base() -> None:
    t = _tile(HL.compute(FULL), "H03")
    check(abs(t.context_value - (150.0 / 60.0)) < 1e-9, "H03 cover multiple wrong")
    check("2.50x" in t.context_display(), f"H03 cover not displayed: {t.context_display()!r}")


def test_a_gap_in_one_year_leaves_the_level_and_removes_the_movement() -> None:
    """The measured ONGC case: ocf bound in FY22-23 and FY24-25, missing FY23-24."""
    p = _panel({"ocf": {"FY2022-23": 100.0, "FY2024-25": 150.0},
                "pat": {"FY2022-23": 50.0, "FY2023-24": 55.0, "FY2024-25": 60.0}},
               years=Y3)
    t = _tile(HL.compute(p), "H03")
    check(t.state == HL.VALUE_ONLY, f"H03 state {t.state}, expected VALUE_ONLY")
    check(t.value == 150.0, "the latest figure should still be shown")
    check(t.delta is None and t.movement_label == "",
          "a series broken by a gap must show no movement — a gap is two series")
    check(t.series_years == 1, f"H03 series_years {t.series_years}, expected 1")
    check(bool(t.reason), "VALUE_ONLY must say why no movement is shown")


def test_a_key_that_binds_nowhere_reports_binder_work() -> None:
    p = _panel({"revenue": {y: 100.0 for y in Y3}}, years=Y3)
    t = _tile(HL.compute(p), "H05")
    check(t.state == HL.NEVER_BOUND, f"H05 state {t.state}, expected NEVER_BOUND")
    check("depreciation" in t.reason, "the reason must name the key that is missing")
    check(t.computed is False and t.value is None, "an unbound tile carries no value")


def test_a_figure_missing_from_the_latest_year_is_not_backfilled() -> None:
    p = _panel({"revenue": {"FY2022-23": 100.0, "FY2023-24": 120.0}}, years=Y3)
    t = _tile(HL.compute(p), "H01")
    check(t.state == HL.ABSENT_FROM_LATEST,
          f"H01 state {t.state}, expected ABSENT_FROM_LATEST")
    check(t.value is None,
          "a prior year's figure must never be presented as the current one")


def test_a_half_bound_composite_drops_the_year_rather_than_zero_filling() -> None:
    """P4 — an unbound key is not a zero. Half the debt reported as all of it is worse
    than no debt-equity ratio at all."""
    p = _panel({
        "long_term_borrowings": {y: 400.0 for y in Y3},
        "short_term_borrowings": {"FY2022-23": 100.0, "FY2023-24": 100.0},
        "total_equity": {y: 5_000.0 for y in Y3},
    }, years=Y3)
    t = _tile(HL.compute(p), "H08")
    check(t.value is None or t.value != 400.0 / 5_000.0,
          "H08 reported long-term borrowings alone as total debt")
    check(t.state in (HL.ABSENT_FROM_LATEST, HL.NOT_COMPUTABLE, HL.VALUE_ONLY),
          f"H08 state {t.state} does not reflect a half-bound composite")


def test_a_zero_denominator_is_undefined_not_computed() -> None:
    p = _panel({"total_tax": {y: 30.0 for y in Y3},
                "pbt": {y: 0.0 for y in Y3}}, years=Y3)
    t = _tile(HL.compute(p), "H10")
    check(t.state == HL.NOT_COMPUTABLE, f"H10 state {t.state}, expected NOT_COMPUTABLE")


def test_a_zero_base_shows_the_level_and_refuses_the_movement() -> None:
    p = _panel({"revenue": {"FY2022-23": 0.0, "FY2023-24": 0.0,
                            "FY2024-25": 500.0}}, years=Y3)
    t = _tile(HL.compute(p), "H01")
    check(t.state == HL.VALUE_ONLY and t.value == 500.0,
          f"H01 state {t.state} — a zero base makes the movement undefined, not infinite")
    check(t.delta is None, "a movement from zero must not be reported")


def test_a_framework_gap_is_not_a_missing_figure() -> None:
    t = _tile(HL.compute(FULL, framework="sch3_div3"), "H09")
    check(t.state == HL.NOT_APPLICABLE, f"H09 state {t.state}, expected NOT_APPLICABLE")
    check("sch3_div3" in t.reason, "the reason must name the framework")
    # Every other tile still computes: applicability is per tile, not per report.
    check(_tile(HL.compute(FULL, framework="sch3_div3"), "H01").computed,
          "one inapplicable tile disabled the rest of the dashboard")


def test_overlay_tiles_declare_the_binder_they_need() -> None:
    v = HL.compute(FULL, business_model="petroleum")
    for tid in ("H51", "H52", "H53"):
        t = _tile(v, tid)
        check(t.state == HL.NEEDS_BINDER, f"{tid} state {t.state}, expected NEEDS_BINDER")
        check(bool(t.reason), f"{tid} must say what binding it needs")


def test_no_panel_is_a_state_not_a_crash() -> None:
    v = HL.compute(None)
    check(len(v) == len(HL.CORE_TILES), "the core should still be declared with no panel")
    check(all(t.state == HL.NO_PANEL for t in v), "expected NO_PANEL throughout")


def test_a_weak_input_is_reported_rather_than_scored() -> None:
    p = _panel({"revenue": {y: 100.0 * (i + 1) for i, y in enumerate(Y3)}},
               years=Y3, verdict="UNVERIFIED", unit_conf="LOW")
    t = _tile(HL.compute(p), "H01")
    check(t.computed, "a weak trust record must not stop the figure being reported")
    check(t.inputs_verified is False and bool(t.trust_note),
          "a figure from an unverified statement must say so")


def test_order_is_the_registrys_not_the_figures() -> None:
    ids = [t.tile_id for t in HL.compute(FULL)]
    check(ids == [t.id for t in HL.CORE_TILES],
          "the dashboard reordered itself; two entities would not be comparable")


def test_presentation_scale_is_display_only() -> None:
    t = _tile(HL.compute(FULL), "H01")
    check(t.value == 1_100.0, "the stored value must stay in INR lakh")
    check(t.display(HL.LAKH) == "1,100", f"lakh display {t.display(HL.LAKH)!r}")
    check(t.display(HL.MILLION) == "110", f"million display {t.display(HL.MILLION)!r}")
    check(t.display(HL.CRORE) == "11", f"crore display {t.display(HL.CRORE)!r}")


# ---- delivered ---------------------------------------------------------------------

def _report(panel=FULL, model: str | None = None, framework: str | None = None):
    profile = BusinessProfile(model=model, model_confidence="LOW" if model else None,
                              basis="test fixture") if model else BusinessProfile()
    return build_report("Test Entity", Y3, comparable_years=len(Y3),
                        business_profile=profile, framework=framework, panel=panel)


def test_the_report_carries_the_dashboard() -> None:
    rep = _report()
    check(len(rep.headline) == len(HL.CORE_TILES),
          f"report carries {len(rep.headline)} tiles, expected {len(HL.CORE_TILES)}")


def test_the_json_payload_carries_every_tile_including_the_unbound() -> None:
    p = _panel({"revenue": {y: 100.0 for y in Y3}}, years=Y3)
    payload = json.loads(json.dumps(_report(p).to_dict(), ensure_ascii=False))
    tiles = payload.get("headline")
    check(isinstance(tiles, list) and len(tiles) == len(HL.CORE_TILES),
          "the §14.3 payload does not carry the whole dashboard")
    unbound = [t for t in tiles if t["state"] != HL.OK]
    check(bool(unbound), "expected unbound tiles in this fixture")
    for t in unbound:
        check(bool(t["reason"]), f"{t['tile_id']} is unbound in the payload with no reason")
    for t in tiles:
        if t["delta"] is not None:
            check(bool(t["comparison_year"]),
                  f"{t['tile_id']} carries a movement with no comparison year")


def test_the_dashboard_reaches_the_rendered_report() -> None:
    text = render(_report())
    for t in HL.CORE_TILES:
        check(t.label[:44] in text, f"{t.id} does not appear in the rendered report")
    check(text.index("EXECUTIVE DASHBOARD") < text.index("Raised clusters"),
          "the tiles must render above the triage list")
    check("vs FY2023-24" in text, "no movement carries its comparison year in the render")


def test_an_unbound_tile_is_rendered_not_dropped() -> None:
    p = _panel({"revenue": {y: 100.0 for y in Y3}}, years=Y3)
    text = render(_report(p))
    check("Not computed" in text, "the unbound section did not render")
    check("Trade receivables" in text,
          "an unbound tile vanished from the render — a dashboard missing a tile reads "
          "as a complete one (§4.4)")


def test_the_render_passes_the_safe_language_gate() -> None:
    for model in (None, "petroleum"):
        bad = lint(render(_report(model=model)))
        check(not bad, f"§17.2 lint failed for model={model}: "
                       + "; ".join(str(v) for v in bad[:3]))


def test_the_run_manifest_records_the_tile_registry() -> None:
    rep = _report()
    check(rep.versions.get("headline") == HL.VERSION,
          "a tile change alters every report's first screen and must be versioned (§18.3)")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed over {len(HL.TILES)} tiles "
          f"({len(HL.CORE_TILES)} core, {len(HL.OVERLAY_TILES)} sector overlay)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
