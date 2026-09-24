"""Hermetic tests for block assembly — no database, no network, no model.

Run from `backend/`:
    python -m modes.financial_diagnostic_report.test_report

WHAT IS WORTH PINNING HERE
--------------------------
The builders judge nothing — every verdict is made in `pipeline/fdr/` and read
back out here. So these tests are not about whether a diagnostic is right; they
are about the three ways this layer could quietly misrepresent one:

  * printing a grade letter whose components it is not showing;
  * wording a missing thing as though it had been assessed and passed;
  * letting the download and the screen say different things.

The evaluation is faked with a stub rather than read from Postgres, so this
suite runs anywhere and fails only for a reason in this file's own subject.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from . import report as REP


# ---------------------------------------------------------------------------
# A stand-in for `engine.Evaluated`. Only the fields the builders read.
# ---------------------------------------------------------------------------

@dataclass
class StubEvaluated:
    entity_id: str
    payload: dict[str, Any]
    panel: Any = None


def _payload(*, business_model=None, model_confidence=None, ceiling="C",
             flavor="standalone", periods=("FY2022-23", "FY2023-24", "FY2024-25"),
             filings=5) -> dict[str, Any]:
    return {
        "coverage": {
            "entity": "TEST_ENTITY",
            "periods": list(periods),
            "flavor": flavor,
            "comparable_years": len(periods),
            "input_quality_grade": "D",          # deliberately WORSE than the
            "grade_ceiling": "LOW",              # framework component below
            "grade_components": [
                {"name": "Data integrity", "measure": "Data Integrity Score",
                 "ceiling": "D", "detail": "most figures failed", "figures": {}},
                {"name": "Framework and entity type",
                 "measure": "Framework and entity-type confidence",
                 "ceiling": ceiling, "detail": "the component's own words",
                 "figures": {"business_model": business_model,
                             "model_confidence": model_confidence}},
            ],
        },
        "fact_coverage": {"filings_found": filings},
        "versions": {"pipeline": "test"},
    }


def _framework_ctx(framework="IND_AS", confidence="HIGH", evidence="the filing's words",
                   mixed="") -> dict[str, Any]:
    """A stand-in for what `framework.detect` hands the builder."""
    return {"framework": {
        "framework": framework, "label": "Ind AS", "confidence": confidence,
        "basis": "Read from the filing, which names Ind AS.",
        "meaning": "Prepared under the Indian Accounting Standards.",
        "evidence": evidence, "doc_id": "TEST_2024_2025", "page": 67,
        "years_checked": 3, "mixed": mixed, "determined": framework != "UNDETERMINED",
    }}


def _entity_type_ctx(entity_type="GOVERNMENT_COMPANY", confidence="HIGH",
                     evidence="is a Government Company as defined under section "
                             "2(45)") -> dict[str, Any]:
    """A stand-in for what `entity_type.detect` hands the builder."""
    determined = entity_type != "UNDETERMINED"
    return {"entity_type": {
        "entity_type": entity_type if determined else "UNDETERMINED",
        "label": "Government company" if determined else "Not stated in the filing",
        "confidence": confidence if determined else None,
        "basis": "Read from the filing, which cites section 2(45).",
        "meaning": "A company within the meaning of section 2(45).",
        "evidence": evidence if determined else "",
        "doc_id": "TEST_2024_2025" if determined else "",
        "page": 171 if determined else None,
        "years_checked": 3, "determined": determined,
    }}


def _build(context: dict[str, Any] | None = None, **kw) -> dict[str, Any]:
    ev = StubEvaluated("TEST_ENTITY", _payload(**kw))
    return REP.build("coverage", ev, context)["payload"]


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

def test_reports_only_the_component_it_shows() -> list[str]:
    """The overall grade is set by five measures; this block shows one.

    So it must report THAT component's ceiling and must not print the overall
    letter — a letter whose other four components are hidden is one a reader can
    only accept or ignore, never challenge.
    """
    out = _build(ceiling="C")
    fw = out["framework_entity_type"]
    failures = []
    if fw["component_ceiling"] != "C":
        failures.append(f"expected the component's own ceiling C, got "
                        f"{fw['component_ceiling']!r}")
    if fw["confidence_cap"] != "MEDIUM":
        failures.append(f"a C component permits MEDIUM; block said "
                        f"{fw['confidence_cap']!r}")

    # The stub grades the run D overall (set by data integrity, which this block
    # does not show). Checked field by field rather than by scanning the text,
    # because prose legitimately contains these letters — "follow" contains
    # "low", and a substring scan reports that as a leak.
    if "input_quality_grade" in out or "grade_ceiling" in out:
        failures.append("the block carries the overall grade, whose other "
                        "components it does not show")
    for key, value in fw.items():
        if key in ("detail", "headline", "cap_sentence"):
            continue                      # prose, checked by its own suite
        if value in ("D", "LOW"):
            failures.append(f"framework_entity_type[{key!r}] reports the overall "
                            f"grade ({value}) rather than the component's own")
    return failures


def test_unformed_classification_is_not_worded_as_a_pass() -> list[str]:
    """An entity nobody classified must not read like one that was checked and
    found ordinary. This is the §4.4 distinction the whole report turns on."""
    out = _build(business_model=None)
    fw = out["framework_entity_type"]
    failures = []
    if fw["state"] != "not_formed":
        failures.append(f"state should be not_formed, got {fw['state']!r}")
    if fw["business_model"] is not None:
        failures.append("a business model was invented for an unclassified entity")
    text = (fw["headline"] + " " + fw["detail"]).lower()
    for banned in ("confirmed", "verified", "compliant", "satisfactory", "no issues"):
        if banned in text:
            failures.append(f"unformed classification worded as a pass: {banned!r}")
    if "not" not in fw["headline"].lower():
        failures.append(f"headline does not say the thing is absent: {fw['headline']!r}")
    return failures


def test_formed_classification_is_carried_through() -> list[str]:
    """Both halves present — legal form read AND business model classified —
    is the only state allowed to call itself "formed"."""
    ctx = {**_entity_type_ctx(), **_framework_ctx()}
    out = _build(ctx, business_model="Petroleum", model_confidence="HIGH", ceiling="A")
    fw = out["framework_entity_type"]
    failures = []
    if fw["state"] != "formed":
        failures.append(f"state should be formed, got {fw['state']!r}")
    if "Petroleum" not in fw["headline"] or "Government company" not in fw["headline"]:
        failures.append(f"headline should name both halves, got {fw['headline']!r}")
    if fw["confidence_cap"] != "HIGH":
        failures.append(f"an A component should permit HIGH, got "
                        f"{fw['confidence_cap']!r}")
    return failures


def test_entity_type_alone_is_partial_not_formed() -> list[str]:
    """The legal form was read; the business model was not. That is real
    progress and must not collapse back to "Not yet established" — but it is
    not the fully-classified state either, since interpretation downstream is
    still withheld until the business model is known too."""
    ctx = _entity_type_ctx()
    out = _build(ctx, business_model=None)
    fw = out["framework_entity_type"]
    failures = []
    if fw["state"] != "partial":
        failures.append(f"state should be partial, got {fw['state']!r}")
    if fw["headline"] != "Government company":
        failures.append(f"headline should name the legal form alone, got "
                        f"{fw['headline']!r}")
    if fw["entity_type_confidence"] != "HIGH":
        failures.append(f"entity-type confidence not carried: "
                        f"{fw.get('entity_type_confidence')!r}")
    if "section 2(45)" not in fw.get("entity_type_evidence", ""):
        failures.append("the entity-type quote did not travel with the reading")
    if "not been classified" not in fw["detail"] and "not" not in fw["detail"].lower():
        failures.append("partial state does not say the business model is still open")
    return failures


def test_no_context_is_not_formed() -> list[str]:
    """No enrichment arrived at all (both optional reads failed) — must read
    exactly as it did before either detector existed, not crash and not guess."""
    out = _build(None, business_model=None)
    fw = out["framework_entity_type"]
    return ([] if fw["state"] == "not_formed" and fw["headline"] == "Not yet established"
            else [f"missing context did not degrade to not_formed: {fw['state']!r}"])


def test_framework_read_from_the_filing_is_carried_with_its_evidence() -> list[str]:
    """A framework the reader cannot check is a claim, not a reading.

    So the detection travels with the sentence it was read from and the page it
    sits on — the same discipline the diagnostics hold themselves to.
    """
    out = _build(_framework_ctx(evidence="in conformity with the Indian Accounting "
                                         "Standards prescribed under section 133"))
    rf = out["reporting_framework"]
    failures = []
    if rf["state"] != "read":
        failures.append(f"state should be read, got {rf['state']!r}")
    if rf["value"] != "IND_AS":
        failures.append(f"framework not carried through: {rf['value']!r}")
    if "section 133" not in rf.get("evidence", ""):
        failures.append("the quoted sentence did not travel with the reading")
    if not (rf.get("source") or {}).get("doc_id"):
        failures.append("the reading cites no filing")
    if rf.get("confidence") != "HIGH":
        failures.append(f"confidence not carried: {rf.get('confidence')!r}")
    return failures


def test_framework_not_stated_is_absent_not_assessed() -> list[str]:
    """The entity whose filing does not say must not get a guess.

    Two ways in: the detector returned undetermined, or the enrichment failed
    and no context arrived at all. Both must word themselves as absent, and
    neither may borrow a framework from anywhere.
    """
    failures = []
    for label, ctx in (("undetermined", _framework_ctx(framework="UNDETERMINED")),
                       ("no context at all", None)):
        rf = _build(ctx)["reporting_framework"]
        if rf["value"] is not None:
            failures.append(f"{label}: a framework was invented: {rf['value']!r}")
        if rf["state"] != "not_stated":
            failures.append(f"{label}: state should be not_stated, got {rf['state']!r}")
        if "not" not in rf["label"].lower():
            failures.append(f"{label}: label does not read as absent: {rf['label']!r}")
        if rf.get("evidence"):
            failures.append(f"{label}: evidence quoted for a framework never read")
    return failures


def test_a_framework_change_inside_the_window_is_surfaced() -> list[str]:
    """A transition mid-panel breaks comparability and must not be smoothed over."""
    warning = "Filings in this window do not agree on the framework"
    rf = _build(_framework_ctx(mixed=warning))["reporting_framework"]
    return ([] if rf.get("mixed") == warning
            else ["a mixed-framework window was not surfaced on the block"])


def test_flavour_is_explained_not_just_named() -> list[str]:
    """"Standalone" is a term of art. The block states what it excludes."""
    failures = []
    for flavor, must_mention in (("standalone", "exclud"), ("consolidated", "includ")):
        out = _build(flavor=flavor)
        fl = out["statement_flavour"]
        if fl["value"] != flavor.capitalize():
            failures.append(f"{flavor}: value was {fl['value']!r}")
        if must_mention not in fl["meaning"].lower():
            failures.append(f"{flavor}: meaning does not say what it covers — "
                            f"{fl['meaning']!r}")
    # An unrecognised flavour must not silently borrow another one's meaning.
    out = _build(flavor="unknown")
    if "exclud" in out["statement_flavour"]["meaning"].lower():
        failures.append("an unknown flavour was described as if it were standalone")
    return failures


def test_period_label_survives_a_short_series() -> list[str]:
    failures = []
    one = _build(periods=("FY2024-25",))["entity"]
    if one["period_label"] != "FY2024-25":
        failures.append(f"single year rendered as {one['period_label']!r}")
    if one["comparable_label"] != "1 comparable year":
        failures.append(f"singular not used: {one['comparable_label']!r}")
    none = _build(periods=())["entity"]
    if "no comparable" not in none["period_label"]:
        failures.append(f"empty series rendered as {none['period_label']!r}")
    return failures


def test_download_says_what_the_screen_says() -> list[str]:
    """The markdown is built from the same payload the screen renders.

    Pinned by asserting the wording actually travels, because the way these two
    drift apart is a second copy of a sentence in the exporter.
    """
    ev = StubEvaluated("TEST_ENTITY", _payload())
    block = REP.build("coverage", ev, _framework_ctx(evidence="the quoted sentence"))
    text = REP.to_markdown("TEST_ENTITY", [block], {"pipeline": "test"})
    payload = block["payload"]
    failures = []
    for label, sentence in (
        ("framework detail", payload["framework_entity_type"]["detail"]),
        ("flavour meaning", payload["statement_flavour"]["meaning"]),
        ("framework note", payload["reporting_framework"]["detail"]),
        ("quoted evidence", payload["reporting_framework"]["evidence"]),
        ("why it matters", payload["why_it_matters"]),
    ):
        if sentence not in text:
            failures.append(f"the download omits the {label} the screen shows")
    if "not an audit opinion" not in text:
        failures.append("the download drops the standing caveat")
    if "fdr-report-" not in text:
        failures.append("the download carries no report version stamp")
    return failures


def test_manifest_matches_the_registry() -> list[str]:
    failures = []
    manifest = REP.manifest()
    if len(manifest) != len(REP.BLOCKS):
        failures.append("the manifest and the registry disagree on how many blocks exist")
    for entry in manifest:
        if entry["id"] not in REP.BY_ID:
            failures.append(f"manifest lists an unbuildable block: {entry['id']!r}")
    numbers = [e["number"] for e in manifest]
    if numbers != sorted(numbers):
        failures.append(f"blocks are not in reading order: {numbers}")
    return failures


# ---------------------------------------------------------------------------
# Block 2 — Executive dashboard
# ---------------------------------------------------------------------------

def _tile(tile_id="H01", label="Revenue from operations", state="OK", **kw) -> dict:
    """A stand-in for one `headline.TileValue.to_dict()` entry."""
    base = {
        "tile_id": tile_id, "label": label, "unit": "currency", "state": state,
        "year": "FY2024-25", "value": 1000.0, "value_unit": "INR lakh",
        "delta": 0.05, "delta_kind": "relative", "comparison_year": "FY2023-24",
        "movement_label": "+5.00% vs FY2023-24", "salience": "ADVERSE_WHEN_FALLING",
        "attention": False, "direction": "rose", "context_value": None,
        "context_label": "", "series_years": 3, "reason": "", "formula": "revenue",
        "basis": "the top line", "core": True, "inputs_verified": True,
        "scale_read": True, "trust_note": "",
    }
    base.update(kw)
    return base


def _dash_ev(tiles: list[dict]) -> StubEvaluated:
    payload = _payload()
    payload["headline"] = tiles
    return StubEvaluated("TEST_ENTITY", payload)


def _build_dashboard(tiles: list[dict]) -> dict[str, Any]:
    return REP.build("dashboard", _dash_ev(tiles))["payload"]


def test_computed_tile_carries_a_formatted_value_and_its_movement() -> list[str]:
    out = _build_dashboard([_tile()])
    t = out["tiles"][0]
    failures = []
    if not t["computed"]:
        failures.append("an OK-state tile was not reported as computed")
    if t["display"] != "1,000":
        failures.append(f"value not formatted through headline.format_value: "
                        f"{t['display']!r}")
    if "FY2023-24" not in t["movement_label"]:
        failures.append("the movement dropped its comparison year (§9.5)")
    return failures


def test_uncomputed_tile_states_why_in_a_full_sentence() -> list[str]:
    """A tile that could not be formed must say so in words, never just a bare
    state code — the code is internal vocabulary, the reason is the report."""
    out = _build_dashboard([_tile(state="NEVER_BOUND", value=None, delta=None,
                                  comparison_year="", movement_label="",
                                  reason="cost_of_materials_consumed could not be "
                                        "bound in any year.")])
    t = out["tiles"][0]
    failures = []
    if t["computed"]:
        failures.append("a NEVER_BOUND tile was reported as computed")
    if t["display"] != "—":
        failures.append(f"an uncomputed tile carries a display value: {t['display']!r}")
    if "cost_of_materials_consumed" not in t.get("reason", ""):
        failures.append("the pipeline's own reason was dropped rather than carried through")
    return failures


def test_uncomputed_tile_falls_back_to_worded_state_with_no_reason() -> list[str]:
    """Even when the pipeline supplies no free-text reason, the state code
    itself must never reach the reader bare."""
    out = _build_dashboard([_tile(state="NEEDS_BINDER", value=None, delta=None,
                                  comparison_year="", movement_label="", reason="")])
    reason = out["tiles"][0]["reason"]
    return ([] if "NEEDS_BINDER" not in reason and len(reason) > 10
            else [f"a bare state code reached the reader: {reason!r}"])


def test_attention_mark_and_context_travel_through() -> list[str]:
    # H08/H09 are withheld from this block entirely (see `_WITHHELD_TILES` in
    # report.py) — a real ratio tile with a context read that stays in the block.
    out = _build_dashboard([_tile(tile_id="H03", label="Operating cash flow",
                                  value=7301024.3, attention=True,
                                  context_value=2.05, context_label="of profit for the year")])
    t = out["tiles"][0]
    failures = []
    if not t["attention"]:
        failures.append("the attention flag was dropped")
    if "2.05" not in t["context_display"] or "profit" not in t["context_display"]:
        failures.append(f"the declared second read did not travel: "
                        f"{t['context_display']!r}")
    return failures


def test_counts_and_flags_are_summarised_correctly() -> list[str]:
    tiles = [_tile(tile_id="H01"), _tile(tile_id="H02", attention=True),
             _tile(tile_id="H03", state="NEVER_BOUND", value=None, delta=None,
                   comparison_year="", movement_label="", reason="not bound")]
    out = _build_dashboard(tiles)
    failures = []
    if out["computed_count"] != 2:
        failures.append(f"computed_count wrong: {out['computed_count']}")
    if out["total_count"] != 3:
        failures.append(f"total_count wrong: {out['total_count']}")
    if out["flagged_ids"] != ["H02"]:
        failures.append(f"flagged_ids wrong: {out['flagged_ids']}")
    return failures


def test_no_headline_in_the_payload_is_an_empty_dashboard_not_a_crash() -> list[str]:
    out = REP.build("dashboard", StubEvaluated("TEST_ENTITY", _payload()))["payload"]
    failures = []
    if out["tiles"] != []:
        failures.append("tiles invented from nothing")
    if out["computed_count"] != 0 or out["total_count"] != 0:
        failures.append("counts not zero for an empty dashboard")
    if "No figures" not in out["lede"]:
        failures.append("an empty dashboard does not say so plainly")
    return failures


@dataclass
class _FakeCell:
    value: float
    source_label: str
    doc_id: str
    table_id: str
    page: Any = None
    unit_scale: str = "lakh"


class _FakePanel:
    """A stand-in for `fdr.panel.Panel` — just enough to answer `.cell(key, year)`."""
    def __init__(self, cells: dict[tuple[str, str], _FakeCell]):
        self._cells = cells

    def cell(self, key: str, year: str):
        return self._cells.get((key, year))


def test_citation_names_the_printed_row_and_page() -> list[str]:
    """H01 draws on one key (`revenue`) — the simplest case: one figure, one
    citation, naming the row it was read from and where."""
    panel = _FakePanel({("revenue", "FY2024-25"): _FakeCell(
        value=13784629.0, source_label="Revenue from operations",
        doc_id="ONGC_2024_2025", table_id="ONGC_2024_2025_tbl_0153", page=273)})
    ev = StubEvaluated("TEST_ENTITY", {**_payload(),
                       "headline": [_tile(tile_id="H01", label="Revenue from operations",
                                          numerator_key="revenue", year="FY2024-25")]},
                       panel=panel)
    out = REP.build("dashboard", ev)["payload"]
    cites = out["tiles"][0]["citations"]
    failures = []
    if len(cites) != 1:
        return [f"expected 1 citation for a single-key tile, got {len(cites)}"]
    c = cites[0]
    if not c["located"]:
        failures.append("a cell with a real table_id was reported as not located")
    if c["row_label"] != "Revenue from operations":
        failures.append(f"row label not carried through: {c['row_label']!r}")
    if c["page"] != 273:
        failures.append(f"page not carried through: {c['page']!r}")
    return failures


def test_citation_for_a_ratio_tile_names_every_leg() -> list[str]:
    """H10 (effective tax rate) draws on TWO keys. Every one must get its own
    citation — a reader checking a ratio needs all its rows, not the first.
    (H08/H09, the corpus's other multi-key tiles, are withheld from this block
    entirely — see `_WITHHELD_TILES` in report.py — so this is the case to test.)"""
    panel = _FakePanel({
        ("total_tax", "FY2024-25"): _FakeCell(
            111494.96, "Total Tax Expense (VIII)", "OVL_2023_2024",
            "OVL_2023_2024_tbl_0200", 295),
        ("pbt", "FY2024-25"): _FakeCell(
            467598.20, "Profit Before Tax (V-VI)", "OVL_2023_2024",
            "OVL_2023_2024_tbl_0200", 295),
    })
    ev = StubEvaluated("TEST_ENTITY", {**_payload(),
                       "headline": [_tile(tile_id="H10", label="Effective tax rate",
                                          unit="percent", year="FY2024-25")]}, panel=panel)
    out = REP.build("dashboard", ev)["payload"]
    keys = {c["key"] for c in out["tiles"][0]["citations"]}
    expected = {"total_tax", "pbt"}
    return ([] if keys == expected else
            [f"ratio tile did not cite every leg: got {keys}, expected {expected}"])


def test_uncomputed_tile_carries_no_citation() -> list[str]:
    """A figure that was not computed has nothing to point to — no citation
    list invented for a value that does not exist."""
    out = _build_dashboard([_tile(state="NEVER_BOUND", value=None, delta=None,
                                  comparison_year="", movement_label="", reason="x")])
    return ([] if "citations" not in out["tiles"][0]
            else ["an uncomputed tile carries a citations field"])


def test_unlocated_cell_is_stated_not_hidden() -> list[str]:
    """A figure the panel holds but with no recorded table (arithmetic-derived,
    not a printed row) must say so, never silently drop from the list."""
    panel = _FakePanel({("revenue", "FY2024-25"): _FakeCell(
        value=100.0, source_label="derived", doc_id="X", table_id="", page=None)})
    ev = StubEvaluated("TEST_ENTITY", {**_payload(),
                       "headline": [_tile(tile_id="H01", numerator_key="revenue",
                                          year="FY2024-25")]}, panel=panel)
    out = REP.build("dashboard", ev)["payload"]
    cites = out["tiles"][0]["citations"]
    return ([] if len(cites) == 1 and cites[0]["located"] is False
            else [f"an untraceable cell was not stated as such: {cites}"])


def test_ordinary_value_only_carries_no_note_at_all() -> list[str]:
    """An entity's first panel year, or a zero prior — VALUE_ONLY, the ordinary
    structural case — must not print a "movement withheld" note. That treatment
    is reserved for SCALE_SUSPECT, the one state that means something is actually
    wrong, not merely that history is short."""
    out = _build_dashboard([_tile(state="VALUE_ONLY", delta=None, comparison_year="",
                                  movement_label="", reason="The figure is bound for "
                                  "FY2024-25, but no comparable prior year exists in "
                                  "an unbroken run ending at it.")])
    t = out["tiles"][0]
    failures = []
    if not t["computed"]:
        failures.append("VALUE_ONLY must still read as computed — the level is real")
    if "movement_reason" in t:
        failures.append(f"an ordinary VALUE_ONLY tile printed a note: {t['movement_reason']!r}")
    return failures


def test_scale_suspect_carries_its_note_and_stays_computed() -> list[str]:
    """The one state that SHOULD print a note — a suspected transcription error —
    still shows the level (it may well be the correct one) and marks itself
    computed, distinct from a tile that produced nothing at all."""
    out = _build_dashboard([_tile(state="SCALE_SUSPECT", value=173423.41, delta=None,
                                  comparison_year="", movement_label="",
                                  reason="FY2023-24's figure moves 12.03x from "
                                  "FY2022-23 and then 0.126x to FY2024-25.")])
    t = out["tiles"][0]
    failures = []
    if not t["computed"] or t["value"] != 173423.41:
        failures.append(f"SCALE_SUSPECT must still show its level: computed="
                        f"{t['computed']}, value={t['value']}")
    if t.get("movement_reason") != ("FY2023-24's figure moves 12.03x from FY2022-23 "
                                    "and then 0.126x to FY2024-25."):
        failures.append(f"the suspect reason did not travel: {t.get('movement_reason')!r}")
    return failures


def test_no_tile_ever_carries_a_trust_note() -> list[str]:
    """Dropped entirely from the dashboard's output — too technical for a reader
    who is not auditing the extraction pipeline itself. Checked on a tile that
    used to carry one, to prove the field is gone rather than merely unexercised."""
    out = _build_dashboard([_tile(inputs_verified=False, scale_read=False,
                                  trust_note="1 of 1 input figure(s) came from a "
                                             "statement that did not satisfy its own "
                                             "arithmetic identity.")])
    return ([] if "trust_note" not in out["tiles"][0]
            else ["trust_note reached the tile output"])


def test_currency_tile_displays_in_its_own_source_scale() -> list[str]:
    """A figure from a filing presented in crore is shown in crore, not silently
    forced into lakh — the reader checking it against the printed page should see
    the same number the filing shows."""
    failures = []
    for scale, label, stored_lakh, expected_display in (
        ("crore", "INR crore", 777_928.4, "7,779"),      # 777,928.4 lakh = 7,779.284 crore
        ("million", "INR million", 523_129.9, "52,313"), # 523,129.9 lakh = 52,312.99 million
        ("lakh", "INR lakh", 173_423.41, "173,423"),
    ):
        panel = _FakePanel({("revenue", "FY2024-25"): _FakeCell(
            value=stored_lakh, source_label="Revenue from Operations", doc_id="X",
            table_id="X_tbl_1", page=1, unit_scale=scale)})
        ev = StubEvaluated("TEST_ENTITY", {**_payload(),
                           "headline": [_tile(tile_id="H01", value=stored_lakh,
                                              year="FY2024-25")]}, panel=panel)
        t = REP.build("dashboard", ev)["payload"]["tiles"][0]
        if t["unit_label"] != label:
            failures.append(f"{scale}: unit_label {t['unit_label']!r}, expected {label!r}")
        if t["display"] != expected_display:
            failures.append(f"{scale}: display {t['display']!r}, expected "
                            f"{expected_display!r}")
    return failures


def test_debt_equity_and_current_ratio_are_withheld_from_the_block() -> list[str]:
    """H08 and H09 are not shown in this block at all — a ratio's year-on-year
    movement is exactly what a cross-year scale mismatch corrupts worst, and a
    reader has no face-value sanity check for a ratio the way they do for a
    rupee figure. Confirmed absent even when the pipeline supplies them."""
    tiles = [_tile(tile_id=tid, label=lbl, unit="ratio")
             for tid, lbl in (("H08", "Debt-equity"), ("H09", "Current ratio"))]
    tiles.append(_tile(tile_id="H01"))     # a tile that SHOULD survive, for contrast
    out = _build_dashboard(tiles)
    ids = {t["id"] for t in out["tiles"]}
    failures = []
    if "H08" in ids or "H09" in ids:
        failures.append(f"a withheld tile reached the block: {ids}")
    if "H01" not in ids:
        failures.append("an unrelated tile was dropped along with the withheld ones")
    if out["total_count"] != 1:
        failures.append(f"total_count should not count withheld tiles: {out['total_count']}")
    return failures


def test_dashboard_download_matches_the_screen() -> list[str]:
    ev = _dash_ev([_tile(), _tile(tile_id="H03", state="NEVER_BOUND", value=None,
                        delta=None, comparison_year="", movement_label="",
                        reason="never bound anywhere")])
    block = REP.build("dashboard", ev)
    text = REP.to_markdown("TEST_ENTITY", [block], {})
    failures = []
    if "1,000" not in text:
        failures.append("a computed value is missing from the download")
    if "never bound anywhere" not in text:
        failures.append("an uncomputed tile's reason is missing from the download")
    return failures


def main() -> int:
    suites = (
        ("shows only the component it reports", test_reports_only_the_component_it_shows),
        ("unformed is not worded as a pass", test_unformed_classification_is_not_worded_as_a_pass),
        ("formed classification carries through", test_formed_classification_is_carried_through),
        ("entity type alone is partial, not formed", test_entity_type_alone_is_partial_not_formed),
        ("no context degrades to not_formed", test_no_context_is_not_formed),
        ("framework reading carries its evidence",
         test_framework_read_from_the_filing_is_carried_with_its_evidence),
        ("framework not stated is absent, not assessed",
         test_framework_not_stated_is_absent_not_assessed),
        ("mixed-framework window is surfaced",
         test_a_framework_change_inside_the_window_is_surfaced),
        ("flavour is explained", test_flavour_is_explained_not_just_named),
        ("short series still labels", test_period_label_survives_a_short_series),
        ("download matches the screen", test_download_says_what_the_screen_says),
        ("manifest matches the registry", test_manifest_matches_the_registry),
        ("computed tile carries value and movement",
         test_computed_tile_carries_a_formatted_value_and_its_movement),
        ("uncomputed tile states why", test_uncomputed_tile_states_why_in_a_full_sentence),
        ("uncomputed tile never shows a bare state code",
         test_uncomputed_tile_falls_back_to_worded_state_with_no_reason),
        ("attention mark and context travel through",
         test_attention_mark_and_context_travel_through),
        ("counts and flags summarised correctly",
         test_counts_and_flags_are_summarised_correctly),
        ("no headline is an empty dashboard, not a crash",
         test_no_headline_in_the_payload_is_an_empty_dashboard_not_a_crash),
        ("citation names row and page", test_citation_names_the_printed_row_and_page),
        ("ratio tile cites every leg", test_citation_for_a_ratio_tile_names_every_leg),
        ("uncomputed tile has no citation", test_uncomputed_tile_carries_no_citation),
        ("unlocated cell stated, not hidden", test_unlocated_cell_is_stated_not_hidden),
        ("ordinary value-only carries no note", test_ordinary_value_only_carries_no_note_at_all),
        ("scale-suspect carries its note", test_scale_suspect_carries_its_note_and_stays_computed),
        ("no tile ever carries a trust note", test_no_tile_ever_carries_a_trust_note),
        ("currency tile displays in its own scale",
         test_currency_tile_displays_in_its_own_source_scale),
        ("debt-equity and current ratio withheld",
         test_debt_equity_and_current_ratio_are_withheld_from_the_block),
        ("dashboard download matches the screen", test_dashboard_download_matches_the_screen),
    )
    total = 0
    for name, suite in suites:
        failures = suite()
        total += len(failures)
        if failures:
            print(f"FAIL  {name}")
            for line in failures:
                print(f"        {line}")
        else:
            print(f"ok    {name}")
    print()
    print("FAILED" if total else f"PASSED — {len(suites)} suites")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
