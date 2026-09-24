"""The report, assembled one block at a time.

WHY BLOCKS ARE THE UNIT, AND NOT THE WHOLE REPORT
-------------------------------------------------
`engine.evaluate()` produces the entire §14.3 payload in one pass, so a route
could simply return all of it and let the browser render what it likes. Three
things make that the wrong shape here:

  LATENCY.    Reading an entity's filings costs a second or so per filing and
              dominates everything else; the arithmetic after it is sub-
              millisecond. A reader should see the first block the moment the
              read lands rather than waiting for the last one to be worded.
  NARRATION.  The blocks that will carry generated prose (the business profile,
              the cluster themes) each need a model pass. Those are seconds
              apiece, they are independent of one another, and they are the
              reason this module hands work to a pool instead of a loop.
  CONTEXT.    A narrated block is given ITS OWN slice and nothing else. Feeding
              a model the whole report to write one paragraph is how a paragraph
              about coverage ends up quoting a ratio from block 4.

So a block is: a slice of the evaluated payload, a builder that is pure and
fast, and — later, for some of them — one model call that sees only that slice.

WHAT A BUILDER MAY AND MAY NOT DO
---------------------------------
A builder is a pure function of `Evaluated`. It may reshape, label and word what
the pipeline computed. It may NOT compute a diagnostic, re-derive a figure, or
decide anything the audit spine did not already decide — `pipeline/fdr/` owns
every judgement, and a second place that judged would be a second place to audit.

Wording lives here rather than in the browser for the same reason
`adapter._stage_message` does: one phrasing of each statement, whatever is
reading it — the screen, the download, or a later export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .engine import Evaluated, ensure_importable

# Bumped when a builder's OUTPUT changes shape or wording. It travels on every
# block so a downloaded report can be tied back to the code that worded it.
REPORT_VERSION = "fdr-report-1.0.0"


# ---------------------------------------------------------------------------
# Wording helpers
# ---------------------------------------------------------------------------

_FLAVOUR_MEANING = {
    "standalone": "The entity's own figures, excluding subsidiaries and joint ventures.",
    "consolidated": "The group's combined figures, including subsidiaries and joint "
                    "ventures.",
}


def _period_label(periods: list[str]) -> str:
    """"FY2020-21 – FY2024-25", or the single year, or an honest blank."""
    if not periods:
        return "no comparable period"
    if len(periods) == 1:
        return periods[0]
    return f"{periods[0]} – {periods[-1]}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many}"


# ---------------------------------------------------------------------------
# Block 1 — Business profile (coverage, limitations and the §5 interpretive lens)
# ---------------------------------------------------------------------------

def _framework_component(coverage: dict[str, Any]) -> dict[str, Any]:
    """The one §4.3 measure this block reports, pulled out of the grade.

    The grade itself is computed from five measures and takes the worst. Block 1
    deliberately reports only this one, so it reports THIS component's own
    ceiling and never the overall letter — printing a letter while hiding four
    of the five things that set it would be a worse answer than printing none.
    """
    for comp in coverage.get("grade_components") or ():
        if str(comp.get("name", "")).lower().startswith("framework"):
            return comp
    return {}


def _framework_field(detection: dict[str, Any] | None) -> dict[str, Any]:
    """The reporting-framework row, whether or not the filing stated one.

    Both outcomes are real answers and are shaped the same way, so the interface
    renders one row either way. What separates them is `state`: a framework that
    was read carries the sentence it was read from, and one that was not carries
    what that leaves unresolved.
    """
    if not detection or not detection.get("determined"):
        return {
            "value": None,
            "state": "not_stated",
            "label": "Not stated in the filing",
            "detail": (detection or {}).get("meaning") or (
                "The filing does not state which accounting framework it follows, so "
                "format-specific checks proceed on the presentation as printed."),
            "evidence": "",
            "confidence": None,
        }
    return {
        "value": detection.get("framework"),
        "state": "read",
        "label": detection.get("label") or "",
        "detail": detection.get("meaning") or "",
        "basis": detection.get("basis") or "",
        "evidence": detection.get("evidence") or "",
        "source": {"doc_id": detection.get("doc_id") or "",
                   "page": detection.get("page")},
        "confidence": detection.get("confidence"),
        "mixed": detection.get("mixed") or "",
    }


def _business_profile_field(future: Any) -> dict[str, Any]:
    """The §5 interpretive lens, waited on and shaped for the block.

    Blocking here is deliberate and safe: this function runs INSIDE block 1's
    own pool-thread worker, so waiting for the future blocks only that thread —
    every other block's worker keeps running. A missing future (context built
    without one — the hermetic tests do this) degrades to "not formed" rather
    than raising, the same discipline as every other optional enrichment here.
    """
    if future is None:
        return {"formed": False,
                "reason": "Not built for this run.", "fields": [],
                "citations": [], "grounded_figures": []}
    try:
        profile = future.result()
    except Exception as exc:  # noqa: BLE001 - optional enrichment, never fatal
        return {"formed": False,
                "reason": f"Could not be drafted this run ({type(exc).__name__}).",
                "fields": [], "citations": [], "grounded_figures": []}
    return profile or {"formed": False, "reason": "No result was produced.",
                       "fields": [], "citations": [], "grounded_figures": []}


def _build_coverage(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 1 — what was read, what kind of entity produced it, and what that
    limits.

    Scoped to the five things that can be answered from a historical corpus with
    no live enrichment behind it. Anything that would need a classifier, a peer
    set or a live feed is absent rather than approximated.
    """
    cov = ev.payload.get("coverage") or {}
    facts = ev.payload.get("fact_coverage") or {}
    periods = list(cov.get("periods") or [])
    fw = _framework_component(cov)

    business_model = (fw.get("figures") or {}).get("business_model")
    model_confidence = (fw.get("figures") or {}).get("model_confidence")
    ceiling = fw.get("ceiling") or "E"

    # What THIS component alone permits a later diagnostic to claim. Read from
    # the vendored grading table rather than restated here: a second copy of the
    # letter-to-confidence mapping is a second thing to keep in step, and the
    # one that drifts is always the copy. The run's effective cap can be lower
    # still, if another measure graded worse — hence the wording below, which
    # stays true either way.
    from fdr import grading as GR
    cap = GR.CEILING.get(ceiling)

    # §5.1 asks for two different things under one name: the coarse LEGAL FORM
    # (read deterministically — see `entity_type.py`) and the fine-grained
    # BUSINESS MODEL (manufacturing, petroleum, utilities... — genuinely needs
    # judgement, still unbuilt). This block reports whichever of the two it has.
    et = ctx.get("entity_type") or {}
    entity_type_known = bool(et.get("determined"))

    if business_model and entity_type_known:
        headline = f"{et['label']} · {business_model}"
        detail = fw.get("detail") or ""
        state = "formed"
    elif entity_type_known:
        headline = et["label"]
        state = "partial"
        detail = (
            f"{et['meaning']} What kind of business it runs — manufacturing, "
            f"petroleum, utilities and the rest of the §5.1 set — has not been "
            f"classified. Until that is done, the diagnostics in this report can "
            f"measure but not interpret: whether a figure is a concern or an "
            f"ordinary feature of how this entity operates depends on knowing "
            f"what kind of business it runs, not only what kind of body it is."
        )
    else:
        headline = "Not yet established"
        state = "not_formed"
        detail = (
            "This entity has not been classified — neither the kind of business it "
            "runs nor the kind of body it is has been determined from its filings. "
            "Until that is done, the diagnostics in this report can measure but not "
            "interpret: whether a figure is a concern or an ordinary feature of how "
            "this entity operates depends on knowing what kind of entity it is."
        )

    flavour = str(cov.get("flavor") or "unknown").lower()
    filings = int(facts.get("filings_found") or 0)
    comparable = int(cov.get("comparable_years") or 0)

    return {
        "entity": {
            "name": cov.get("entity") or ev.entity_id,
            "filings_read": filings,
            "filings_label": _plural(filings, "filing", "filings"),
            "period_label": _period_label(periods),
            "periods": periods,
            "comparable_years": comparable,
            "comparable_label": _plural(comparable, "comparable year",
                                        "comparable years"),
        },
        "statement_flavour": {
            "value": flavour.capitalize(),
            "key": flavour,
            "meaning": _FLAVOUR_MEANING.get(flavour,
                                            "The basis of preparation was not recorded "
                                            "for this run."),
        },
        # Read from the filing's own words by `framework.detect`, which quotes the
        # sentence it read. Undetermined stays undetermined: the entity that does
        # not state its framework is the one an invented answer would hurt.
        "reporting_framework": _framework_field(ctx.get("framework")),
        "framework_entity_type": {
            "state": state,
            "headline": headline,
            "detail": detail,
            "business_model": business_model,
            "model_confidence": model_confidence,
            "entity_type": et.get("entity_type") if entity_type_known else None,
            "entity_type_confidence": et.get("confidence"),
            "entity_type_evidence": et.get("evidence") or "",
            "entity_type_source": {"doc_id": et.get("doc_id") or "",
                                   "page": et.get("page")} if entity_type_known else None,
            "component_ceiling": ceiling,
            "confidence_cap": cap,
        },
        # The interpretive lens (§5) — Model, Revenue, Cost, Financing, Value
        # drivers, Inherent-risk map. Folded into this block rather than given
        # its own, at product request. Waits on `business_profile_future`
        # (submitted to the block pool before this builder ever ran) so the
        # OTHER blocks are not held up behind the one model call in the report.
        "business_profile": _business_profile_field(ctx.get("business_profile_future")),
        "why_it_matters": (
            "Everything in this report is a lead for audit attention, not a finding. "
            "How far each one can be leaned on is limited by what could be read from "
            "the filings, so a serious-looking signal resting on a weak input is "
            "reported as important-if-true rather than as something established. "
            "Because this entity has not been classified, no diagnostic that follows "
            "carries an interpretation: each states what it measured and leaves the "
            "judgement of whether it matters where it belongs — with you."
        ),
    }


def _markdown_coverage(payload: dict[str, Any]) -> str:
    """Block 1, for the downloadable report."""
    e = payload["entity"]
    fw = payload["framework_entity_type"]
    fl = payload["statement_flavour"]
    rf = payload["reporting_framework"]

    lines = [
        "## 1. Business profile",
        "",
        f"**Entity** — {e['name']}",
        f"{e['filings_label']} read, covering {e['period_label']} "
        f"({e['comparable_label']}).",
        "",
        f"**Statement flavour** — {fl['value']}",
        fl["meaning"],
        "",
        f"**Reporting framework** — {rf['label']}",
        rf["detail"],
    ]
    if rf.get("evidence"):
        # The quoted sentence travels into the file. A framework stated without
        # the words it was read from is a claim the reader cannot check.
        lines += ["", f"> {rf['evidence']}"]
        src = rf.get("source") or {}
        if src.get("doc_id"):
            page = f", page {src['page']}" if src.get("page") else ""
            lines.append(f">")
            lines.append(f"> — {src['doc_id']}{page}")
    if rf.get("mixed"):
        lines += ["", f"**{rf['mixed']}**"]
    lines += [
        "",
        f"**Framework and entity type** — {fw['headline']}",
        fw["detail"],
    ]
    if fw.get("entity_type_evidence"):
        lines += ["", f"> {fw['entity_type_evidence']}"]
        src = fw.get("entity_type_source") or {}
        if src.get("doc_id"):
            page = f", page {src['page']}" if src.get("page") else ""
            lines.append(">")
            lines.append(f"> — {src['doc_id']}{page}")

    # Not formed this run is left out of the download entirely, matching the
    # screen (`BusinessProfileCard`): a generation-endpoint outage is an
    # operational fact about the tool, not audit content, and the download
    # must not disagree with what the screen showed by printing a line the
    # screen suppressed.
    bp = payload.get("business_profile") or {}
    if bp.get("formed") and bp.get("fields"):
        lines += ["", "### The interpretive lens", ""]
        for f in bp["fields"]:
            lines.append(f"**{f['label']}** — {f['text']}")
        if bp.get("citations"):
            lines += ["", "Sources:"]
            for c in bp["citations"]:
                if c.get("cited"):
                    lines.append(f"- [{c['n']}] {c.get('citation', '')}")
        if bp.get("reason"):
            lines += ["", f"_{bp['reason']}_"]

    lines += [
        "",
        "### Why this matters for planning",
        "",
        payload["why_it_matters"],
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block 2 — Executive dashboard
# ---------------------------------------------------------------------------

# `TileValue.state` (pipeline/fdr/headline.py) is closed vocabulary the audit
# spine owns. This block WORDS each state for a reader rather than reproducing
# its computation — the arithmetic already ran inside `engine.evaluate()`; only
# the sentence is written here.
_TILE_STATE_WORDING: dict[str, str] = {
    "NEVER_BOUND": "This figure has never been extracted from any of this "
                   "entity's filings.",
    "ABSENT_FROM_LATEST": "This figure is not available for the latest year — a "
                          "figure from an earlier filing is not a statement about "
                          "the current one.",
    "SCALE_UNRECONCILED": "The scale carried by this figure contradicts the rest "
                          "of the window, so it was withheld rather than risk a "
                          "movement manufactured out of a unit error.",
    "BROKEN_BY_GAP": "This figure is not available for the latest year of the panel.",
    "TOO_FEW_YEARS": "Not enough consecutive years are bound to form this figure.",
    "VALUE_ONLY": "Bound for this year, with no comparable prior year to measure "
                  "a movement against.",
    "NOT_COMPUTABLE": "The inputs are bound, but not in a form the arithmetic can use.",
    "NOT_APPLICABLE": "This entity's balance-sheet format does not use the "
                      "distinction this figure divides.",
    "NEEDS_BINDER": "Nothing yet extracts this figure from any filing — it has no "
                    "line to read from.",
    "NO_PANEL": "No multi-year panel was built for this entity.",
}

# Non-currency units, which carry no scale question at all.
_TILE_UNIT_LABEL = {"currency": "INR lakh", "ratio": "", "percent": "", "times": "×"}

# A currency figure is shown in whatever scale ITS OWN source statement used — see
# `_tile_field` — never forced into one common unit across the report. `format_value`
# already does the arithmetic (the panel normalises to lakh internally regardless);
# this only chooses which of the three labels matches the scale actually rendered.
_CURRENCY_SCALE_LABEL = {"lakh": "INR lakh", "million": "INR million", "crore": "INR crore"}


def _tile_citations(tile_id: str, year: str, panel: Any) -> list[dict[str, Any]]:
    """Where each figure a tile reads came from — one entry per canonical key.

    Read straight off the panel cell the tile's own arithmetic used, not
    re-derived: `PanelCell.table_id`/`.page` are the binder's own record of
    which row it read (`fs_db/binding.py`), carried through unchanged. A tile
    that sums several keys (H08's debt-equity draws on three) cites all of
    them, because a reader checking the figure needs every row it came from,
    not just the first.

    A cell whose source was not recorded is stated as such rather than
    omitted — the same reason an unbound tile is drawn rather than dropped:
    silence here would read as nothing to check, when the true state is that
    this one component cannot yet be checked against a page.
    """
    from fdr import headline as HL

    tile = HL.BY_ID.get(tile_id)
    if tile is None or panel is None:
        return []
    out = []
    for key in tile.required_keys:
        cell = panel.cell(key, year)
        out.append({
            "key": key,
            "value": cell.value if cell else None,
            "row_label": cell.source_label if cell else "",
            "doc_id": cell.doc_id if cell else "",
            "table_id": cell.table_id if cell else "",
            "page": cell.page if cell else None,
            "located": bool(cell and cell.table_id),
        })
    return out


def _tile_field(t: dict[str, Any], panel: Any = None) -> dict[str, Any]:
    """One dashboard tile, worded for a reader.

    Every field the frontend needs to draw the tile without re-deriving
    anything: the formatted value, the movement (already carrying its
    comparison year — §9.5), whether it earns the attention mark, and — when it
    did not compute — the reason in a full sentence rather than the bare state
    code the pipeline uses internally.
    """
    from fdr import headline as HL

    unit, state = t.get("unit"), t.get("state")
    computed = state in ("OK", "VALUE_ONLY", "SCALE_SUSPECT")

    # Presented in whatever scale the SOURCE STATEMENT actually used — never forced
    # into one common unit. An entity that reports in crore is read in crore; one
    # that reports in million stays in million. A reviewer checking this tile
    # against the printed page should see the same number the filing shows, not a
    # conversion they have to reverse in their head first.
    scale = HL.LAKH
    if computed and unit == HL.CURRENCY and t.get("year"):
        tile_spec = HL.BY_ID.get(t.get("tile_id"))
        primary_key = tile_spec.numerator[0] if tile_spec and tile_spec.numerator else None
        cell = panel.cell(primary_key, t["year"]) if primary_key and panel else None
        if cell and cell.unit_scale in HL.SCALES:
            scale = cell.unit_scale
    display = HL.format_value(t.get("value"), unit, scale) if computed else "—"
    unit_label = (_CURRENCY_SCALE_LABEL.get(scale, _TILE_UNIT_LABEL.get(unit, ""))
                 if unit == HL.CURRENCY else _TILE_UNIT_LABEL.get(unit, ""))

    out = {
        "id": t["tile_id"],
        "label": t["label"],
        "computed": computed,
        "value": t.get("value") if computed else None,
        "display": display,
        "unit_label": unit_label,
        "movement_label": t.get("movement_label") or "",
        "direction": t.get("direction") or "",
        "attention": bool(t.get("attention")),
        "context_display": (f"{t['context_value']:,.2f}× {t['context_label']}".strip()
                            if t.get("context_value") is not None else ""),
        "series_years": t.get("series_years") or 0,
    }
    if not computed:
        out["reason"] = (t.get("reason")
                         or _TILE_STATE_WORDING.get(state, "This figure was not computed."))
    elif state == "SCALE_SUSPECT" and t.get("reason"):
        # The value is real and shown; only the movement is withheld, and for a
        # reason that matters — a suspected transcription error, not the ordinary
        # "no comparable year exists yet" case (VALUE_ONLY), which needs no note at
        # all: an entity's first panel year is not a finding.
        out["movement_reason"] = t["reason"]
    if computed and t.get("year"):
        out["citations"] = _tile_citations(t["tile_id"], t["year"], panel)
    return out


def _build_dashboard(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 2 — ten figures computed straight off the bound statements.

    NO MODEL AND NO RETRIEVAL. Every tile is a pure formula over figures the
    fact layer already bound and verified (`pipeline/fdr/headline.py`), which
    ran once inside `engine.evaluate()` — this builder only words the result. A
    tile that could not be formed says exactly why in the pipeline's own closed
    vocabulary, never a guess standing in for a number.
    """
    # H08 (debt-equity) and H09 (current ratio) are withheld from this block — not
    # because the formula is wrong, but because a RATIO's year-on-year movement is
    # exactly the figure a cross-year scale mismatch corrupts worst (see
    # `_SCALE_SUSPECT_UNITS` below): a ratio is two figures divided, so a bad scale
    # on either leg moves it, and a reader has no face-value sanity check the way
    # they do for a rupee figure. They stay in the pipeline's own contract
    # (`python -m fdr`) and can return here once cross-year scale settlement is
    # trusted for ratios specifically, not only for levels.
    _WITHHELD_TILES = frozenset({"H08", "H09"})
    tiles_raw = [t for t in (ev.payload.get("headline") or [])
                if t.get("tile_id") not in _WITHHELD_TILES]
    tiles = [_tile_field(t, ev.panel) for t in tiles_raw]

    computed = [t for t in tiles if t["computed"]]
    flagged = [t for t in tiles if t["attention"]]

    return {
        "tiles": tiles,
        "computed_count": len(computed),
        "total_count": len(tiles),
        "flagged_ids": [t["id"] for t in flagged],
        "scale_label": "INR lakh",
        "lede": (
            "The shape of the entity in ten figures, read from the face of the "
            "statements before anything is interpreted. Each movement is measured "
            "against the single prior year named beside it — one year's change, "
            "not a trend."
            if tiles else
            "No figures could be computed for this entity in this run."
        ),
    }


def _markdown_dashboard(payload: dict[str, Any]) -> str:
    """Block 2, for the downloadable report — a plain table, one row per tile."""
    lines = ["## 2. Executive dashboard", "", payload["lede"], ""]
    if payload["tiles"]:
        lines += ["| Figure | Value | Movement |", "|---|---|---|"]
        for t in payload["tiles"]:
            if t["computed"]:
                value = f"{t['display']} {t['unit_label']}".strip()
                if t.get("movement_reason"):
                    move = f"withheld — {t['movement_reason']}"
                else:
                    move = t["movement_label"] or "—"
                    if t["context_display"]:
                        move += f" · {t['context_display']}"
                mark = " ⚠" if t["attention"] else ""
                lines.append(f"| {t['label']}{mark} | {value} | {move} |")
            else:
                lines.append(f"| {t['label']} | not computed | {t['reason']} |")
        lines.append("")
        cited = [t for t in payload["tiles"] if t.get("citations")]
        if cited:
            lines.append("### Sources")
            lines.append("")
            for t in cited:
                for c in t["citations"]:
                    if c["located"]:
                        page = f", page {c['page']}" if c.get("page") else ""
                        lines.append(f"- **{t['label']}** — \"{c['row_label']}\" "
                                     f"({c['doc_id']}{page})")
                    else:
                        lines.append(f"- **{t['label']}** — {c['key']}: computed from "
                                     f"other bound figures, no single printed row")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block 4 — Financial health summary: structure & performance, interpreted
# ---------------------------------------------------------------------------
#
# NO NEW DIAGNOSTIC IS COMPUTED HERE. Two kinds of material, both already
# produced elsewhere in the pipeline, are narrated together:
#
#   STRUCTURE   plain single-year arithmetic over panel cells that are already
#               bound and verified (asset composition, liquidity, funding mix).
#               Single-year, so it carries none of the cross-year scale risk
#               `headline.py`'s SCALE_SUSPECT guard exists for — every figure
#               in one sentence comes from the same filing, the same year.
#   PERFORMANCE the SAME year-on-year tiles Block 2 already computed and
#               gated (H01/H02/H05/H06), plus the Layer 3/4 rule engine's own
#               wording (`rules.py` — S13's DuPont decomposition, S04's cash-
#               vs-profit read), reused verbatim rather than re-derived.

def _find_tile_field(ev: Evaluated, tile_id: str) -> dict[str, Any] | None:
    """One headline tile, worded the same way Block 2 words it.

    Reused rather than recomputed so a movement withheld as SCALE_SUSPECT on
    the dashboard cannot quietly reappear, un-gated, in this block's prose.
    """
    for t in ev.payload.get("headline") or ():
        if t.get("tile_id") == tile_id:
            return _tile_field(t, ev.panel)
    return None


def _all_signals(ev: Evaluated) -> dict[str, dict[str, Any]]:
    """Every Layer 2-4 signal the run evaluated, keyed by id, cluster attached.

    A signal can be a contributing member of more than one cluster in
    principle; the first cluster it is found under is the one credited, which
    only matters for the theme label shown alongside it.
    """
    out: dict[str, dict[str, Any]] = {}
    for cluster in ev.payload.get("risk_clusters") or ():
        for s in cluster.get("contributing_signals") or ():
            sid = s.get("signal_id")
            if sid and sid not in out:
                out[sid] = {**s, "cluster_theme": cluster.get("theme") or "",
                           "cluster_id": cluster.get("cluster_id") or ""}
    return out


def _structure_narrative(panel: Any, year: str) -> tuple[str, list[dict[str, Any]]]:
    """Asset composition, liquidity and funding mix — one year, plain arithmetic."""
    from fdr import headline as HL

    sentences: list[str] = []
    citations: list[dict[str, Any]] = []

    def cite(key: str, cell: Any) -> None:
        citations.append({"key": key, "row_label": cell.source_label, "doc_id": cell.doc_id,
                          "table_id": cell.table_id, "page": cell.page,
                          "located": bool(cell.table_id)})

    def scale_of(cell: Any) -> str:
        return cell.unit_scale if cell.unit_scale in HL.SCALES else HL.LAKH

    total_assets = panel.cell("total_assets", year)
    nfa = panel.cell("net_fixed_assets", year)
    if total_assets and total_assets.value is not None:
        scale = scale_of(total_assets)
        unit_label = _CURRENCY_SCALE_LABEL.get(scale, "INR lakh")
        piece = (f"The asset base totals {HL.format_value(total_assets.value, HL.CURRENCY, scale)} "
                 f"{unit_label}")
        if nfa and nfa.value is not None and total_assets.value:
            pct = nfa.value / total_assets.value * 100
            piece += (f", of which {HL.format_value(nfa.value, HL.CURRENCY, scale)} {unit_label} "
                      f"({pct:.1f}%) is held in net fixed assets")
            cite("net_fixed_assets", nfa)
        sentences.append(piece + ".")
        cite("total_assets", total_assets)

    ca = panel.cell("total_current_assets", year)
    cl = panel.cell("total_current_liabilities", year)
    if ca and cl and ca.value is not None and cl.value is not None:
        scale = scale_of(ca)
        unit_label = _CURRENCY_SCALE_LABEL.get(scale, "INR lakh")
        net = ca.value - cl.value
        position = "a comfortable net-current-asset position" if net >= 0 else \
                   "a net current-liability position"
        gap_word = "surplus" if net >= 0 else "shortfall"
        sentences.append(
            f"Liquidity is {position} (current assets "
            f"{HL.format_value(ca.value, HL.CURRENCY, scale)} {unit_label} vs current "
            f"liabilities {HL.format_value(cl.value, HL.CURRENCY, scale)} {unit_label}, a "
            f"{gap_word} of {HL.format_value(abs(net), HL.CURRENCY, scale)} {unit_label}).")
        cite("total_current_assets", ca)
        cite("total_current_liabilities", cl)

    eq = panel.cell("total_equity", year)
    ltb = panel.cell("long_term_borrowings", year)
    stb = panel.cell("short_term_borrowings", year)
    if eq and eq.value is not None:
        debt = (ltb.value if ltb and ltb.value is not None else 0.0) + \
               (stb.value if stb and stb.value is not None else 0.0)
        total_cap = eq.value + debt
        if total_cap:
            scale = scale_of(eq)
            unit_label = _CURRENCY_SCALE_LABEL.get(scale, "INR lakh")
            eq_pct = eq.value / total_cap * 100
            eq_disp = HL.format_value(eq.value, HL.CURRENCY, scale)
            if debt / total_cap < 0.05:
                sentences.append(
                    f"Funding is overwhelmingly equity ({eq_disp} {unit_label}, "
                    f"{eq_pct:.0f}% of equity plus borrowings); borrowings are immaterial "
                    f"to the capital structure.")
            else:
                debt_disp = HL.format_value(debt, HL.CURRENCY, scale)
                sentences.append(
                    f"Funding is {eq_pct:.0f}% equity ({eq_disp} {unit_label}) and "
                    f"{100 - eq_pct:.0f}% borrowings ({debt_disp} {unit_label}).")
            cite("total_equity", eq)
            if ltb and ltb.value is not None:
                cite("long_term_borrowings", ltb)
            if stb and stb.value is not None:
                cite("short_term_borrowings", stb)

    return " ".join(sentences), citations


def _prose_movement(t: dict[str, Any]) -> str:
    """`movement_label` ("-12.13% vs FY2023-24") read as prose ("fell 12.13% vs FY2023-24").

    The number and comparison year are exactly what the tile already carries —
    only the sign is swapped for the `direction` word the same tile computed,
    so a reader gets a sentence rather than a signed percentage to parse.
    """
    label = t.get("movement_label") or ""
    if not label or label.startswith("unchanged"):
        return label
    direction = t.get("direction") or ""
    if direction in ("rose", "fell") and label[0] in "+-":
        return f"{direction} {label[1:]}"
    return f"moved {label}"


def _performance_narrative(ev: Evaluated) -> tuple[str, list[dict[str, Any]]]:
    """Profit movement, its legs, its decomposition and its cash backing.

    Every movement claim is a tile Block 2 already computed and gated; every
    interpretive sentence is a rule outcome `rules.py` already reasoned and
    worded (§8.2 — decompose before flagging). Nothing here is generated."""
    sentences: list[str] = []
    citations: list[dict[str, Any]] = []
    signals = _all_signals(ev)

    pat_t = _find_tile_field(ev, "H02")
    if pat_t and pat_t["computed"]:
        if pat_t.get("movement_reason"):
            sentences.append(f"Profit for the year is {pat_t['display']} {pat_t['unit_label']}; "
                             f"its movement is withheld — {pat_t['movement_reason']}.")
        elif pat_t.get("movement_label"):
            sentences.append(f"Profit for the year {_prose_movement(pat_t)} to "
                             f"{pat_t['display']} {pat_t['unit_label']}.")
        else:
            sentences.append(f"Profit for the year is {pat_t['display']} {pat_t['unit_label']}.")
        citations += pat_t.get("citations") or []

    leg_bits = []
    for tile_id, name in (("H01", "revenue"), ("H05", "depreciation"), ("H06", "finance costs")):
        t = _find_tile_field(ev, tile_id)
        if t and t["computed"] and t.get("movement_label") and not t.get("movement_reason"):
            leg_bits.append(f"{name} {_prose_movement(t)}")
            citations += t.get("citations") or []
    if leg_bits:
        sentences.append("Over the same year, " + "; ".join(leg_bits) + ".")

    dupont = signals.get("S13")
    if dupont and dupont.get("status") in ("FIRED", "NOT_FIRED") and dupont.get("observation"):
        sentences.append(f"Decomposed: {dupont['observation']}")

    cash_quality = signals.get("S04")
    if cash_quality and cash_quality.get("status") in ("FIRED", "NOT_FIRED") \
            and cash_quality.get("observation"):
        sentences.append(cash_quality["observation"])

    other_income = signals.get("S19") or signals.get("S18")
    if other_income and other_income.get("status") == "FIRED" and other_income.get("observation"):
        sentences.append(f"A caveat on the above: {other_income['observation']}")

    return " ".join(sentences), citations


def _build_health(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 4 — the entity's shape and its latest year's performance, read
    together rather than as ten unconnected tiles."""
    panel = ev.panel
    year = getattr(panel, "latest", None) if panel else None
    if not panel or not year:
        empty = {"text": "", "citations": []}
        return {"formed": False,
               "reason": "No entity-year panel was built for this run.",
               "structure": empty, "performance": empty}

    structure_text, structure_cites = _structure_narrative(panel, year)
    performance_text, performance_cites = _performance_narrative(ev)
    formed = bool(structure_text or performance_text)

    return {
        "formed": formed,
        "reason": "" if formed else
                  "Not enough bound figures to summarise structure or performance this run.",
        "structure": {
            "text": structure_text or
                   "Not enough bound figures to describe the asset, liquidity or funding "
                   "structure this run.",
            "citations": structure_cites,
        },
        "performance": {
            "text": performance_text or
                   "Not enough bound figures to describe this year's performance.",
            "citations": performance_cites,
        },
    }


def _markdown_health(payload: dict[str, Any]) -> str:
    """Block 4, for the downloadable report."""
    st = payload.get("structure") or {}
    pf = payload.get("performance") or {}
    lines = [
        "## 4. Financial health summary — structure & performance, interpreted",
        "",
        "### Structure", "",
        st.get("text") or "Not built for this run.", "",
        "### Performance (decomposed)", "",
        pf.get("text") or "Not built for this run.", "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block 5 — Key trends & structural drift
# ---------------------------------------------------------------------------
#
# Every card is one Layer 2-4 signal `rules.py` actually evaluated for this
# entity (`Outcome.ran`) — FIRED or NOT_FIRED, never ABSTAIN, SUPPRESSED or
# NOT_APPLICABLE, which belong to the coverage story, not the trend story.
# All evaluated signals are shown, whichever way they read: a movement below
# the firing threshold is still a direction worth watching, and hiding it
# would be the same silent cherry-picking §15.2 forbids for suppression.

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, None: 3}


def _build_trends(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 5 — the structural and performance signals the rule engine
    (§7-§9, `pipeline/fdr/rules.py`) actually evaluated this run, worded from
    its own observation and trace rather than recomputed here."""
    from fdr import signals as SG

    all_signals = _all_signals(ev)
    cards = []
    for sid, s in all_signals.items():
        if s.get("status") not in ("FIRED", "NOT_FIRED"):
            continue
        reg = SG.BY_ID.get(sid)
        cards.append({
            "signal_id": sid,
            "title": reg.title if reg else sid,
            "cluster": s.get("cluster_theme") or "",
            "fired": s.get("status") == "FIRED",
            "severity": s.get("severity"),
            "confidence": s.get("confidence"),
            "observation": s.get("observation") or "",
            "trace": s.get("trace") or "",
            "evidence": list(s.get("evidence") or ()),
        })
    cards.sort(key=lambda c: (0 if c["fired"] else 1, _SEVERITY_ORDER.get(c["severity"], 3)))

    fired = [c for c in cards if c["fired"]]
    not_evaluated = sum(1 for s in all_signals.values()
                        if s.get("status") not in ("FIRED", "NOT_FIRED"))

    return {
        "cards": cards,
        "fired_count": len(fired),
        "observed_count": len(cards),
        "not_evaluated_count": not_evaluated,
        "lede": (
            f"{len(fired)} of {len(cards)} evaluated checks crossed the threshold this "
            f"run. Every evaluated check is shown, whichever way it read — a movement "
            f"under threshold is still a direction worth watching, not a clean bill."
            if cards else
            "No structural or performance check could be evaluated for this entity in "
            "this run."
        ),
    }


def _markdown_trends(payload: dict[str, Any]) -> str:
    """Block 5, for the downloadable report — one entry per evaluated signal."""
    lines = ["## 5. Key trends & structural drift", "", payload["lede"], ""]
    for c in payload.get("cards") or ():
        mark = " ⚠" if c["fired"] else ""
        theme = f" · {c['cluster']}" if c.get("cluster") else ""
        lines.append(f"**{c['title']}{mark}** ({c['signal_id']}{theme})")
        lines.append("")
        lines.append(c["observation"])
        if c.get("trace"):
            lines.append("")
            lines.append(f"_{c['trace']}_")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block 6 — Risk clusters with interactions
# ---------------------------------------------------------------------------
#
# NOTHING IS COMPUTED HERE, INCLUDING THE INTERACTIONS. Clustering (M10),
# interaction resolution (§10.2, `pipeline/fdr/interactions.py`) and
# prioritisation (M11, `pipeline/fdr/planning.py` / `priority.py`) all ran
# once inside `engine.evaluate()`, on the same panel and the same signal
# results Block 5 already words. `ClusterPackage` carries the FULL §10.3
# planning package — alt explanations, affected assertions, the nature/
# timing/extent response, the evidence request, the interactions that bear
# on it — this builder only reshapes and orders it for the block.

def _cluster_signal_cards(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    """A cluster's contributing signals, worded the same way Block 5 words
    them — same registry lookup, same fields, so a signal reads identically
    wherever in the report it appears."""
    from fdr import signals as SG

    cards = []
    for s in cluster.get("contributing_signals") or ():
        sid = s.get("signal_id")
        reg = SG.BY_ID.get(sid)
        cards.append({
            "signal_id": sid,
            "title": reg.title if reg else sid,
            "status": s.get("status"),
            "fired": s.get("status") == "FIRED",
            "severity": s.get("severity"),
            "confidence": s.get("confidence"),
            "observation": s.get("observation") or "",
            "trace": s.get("trace") or "",
            "evidence": list(s.get("evidence") or ()),
            "reason": s.get("reason") or "",
        })
    return cards


_CLUSTER_SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, None: 3}


def _build_clusters(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 6 — the planning package for every clustered theme.

    The report-level interaction note and the interaction-complex callout
    (`risk_interactions`'s own `note`/`complexes`) are deliberately not
    surfaced here — they restate, in denser and more technical prose, exactly
    what each affected cluster's own `interactions` list already states on
    its own card. One paragraph per fact, not two.
    """
    raw_clusters = ev.payload.get("risk_clusters") or ()

    cards = []
    for c in raw_clusters:
        raised = c.get("status") == "RAISED"
        cards.append({
            "cluster_id": c["cluster_id"],
            "theme": c.get("theme") or "",
            "status": c.get("status"),
            "raised": raised,
            "reason": c.get("reason") or "",
            "severity_band": c.get("inherent_risk"),
            "significant_risk": c.get("significant_risk"),
            "confidence": c.get("diagnostic_confidence"),
            "confidence_basis": c.get("confidence_basis") or "",
            "priority_rank": c.get("priority_rank"),
            "priority_reasoning": c.get("priority_reasoning") or "",
            "interpretation_withheld": c.get("interpretation_withheld") or "",
            "anchor": list(c.get("anchor") or ()),
            "contributing_signals": _cluster_signal_cards(c),
            "alt_explanations": list(c.get("alt_explanations") or ()),
            "affected_assertions": list(c.get("affected_assertions") or ()),
            "regularity_matters": list(c.get("regularity_matters") or ()),
            "control_implications": c.get("control_implications") or "",
            "planning_significance": c.get("planning_significance") or "",
            "recommended_response": c.get("recommended_response") or {},
            "specialist_referral": list(c.get("specialist_referral") or ()),
            "evidence_request": list(c.get("evidence_request") or ()),
            "materiality_basis": list(c.get("materiality_basis") or ()),
            "unevaluated_signals": list(c.get("unevaluated_signals") or ()),
            "corroboration": c.get("corroboration") or "",
            # LIVE/LATENT edges only — DORMANT ones are the pipeline's own
            # bookkeeping, not something worth this card's space (see
            # `InteractionMap.statements_for`).
            "interactions": list(c.get("interactions") or ()),
        })

    # Raised first, then by the priority engine's own rank, then by severity —
    # never by which cluster happens to have the most to say. A thin RAISED
    # theme still outranks a thick ABSTAIN one; that ordering IS the report's
    # first triage.
    def _sort_key(c: dict[str, Any]) -> tuple[int, int, int]:
        rank = c["priority_rank"] if c["priority_rank"] is not None else 999
        return (0 if c["raised"] else 1, rank,
                _CLUSTER_SEVERITY_RANK.get(c["severity_band"], 3))
    cards.sort(key=_sort_key)

    raised_count = sum(1 for c in cards if c["raised"])

    return {
        "clusters": cards,
        "raised_count": raised_count,
        "total_count": len(cards),
        "lede": (
            f"{raised_count} of {len(cards)} clustered themes raised this run, "
            f"drawn together from the underlying signals so related evidence "
            f"is read as one problem rather than several. Each carries a "
            f"severity band and a confidence stamp, kept separate — a "
            f"high-severity theme resting on a thin input is a lead worth "
            f"watching, not a settled finding."
            if cards else
            "No clustered themes could be evaluated for this entity in this run."
        ),
    }


def _markdown_clusters(payload: dict[str, Any]) -> str:
    """Block 6, for the downloadable report — the full planning package per
    raised theme, and a one-line reason for every theme that did not raise."""
    lines = ["## 6. Risk clusters with interactions", "", payload["lede"], ""]

    for c in payload.get("clusters") or ():
        mark = " ⚠" if c["raised"] else ""
        rank = f" · priority {c['priority_rank']}" if c.get("priority_rank") else ""
        lines.append(f"### {c['theme']}{mark} ({c['cluster_id']}{rank})")
        lines.append("")
        if not c["raised"]:
            lines.append(c["reason"] or "Not raised this run.")
            lines.append("")
            continue

        if c.get("severity_band") or c.get("confidence"):
            lines.append(f"Severity: {c.get('severity_band') or '—'} · "
                         f"Confidence: {c.get('confidence') or '—'}")
            lines.append("")
        if c.get("interpretation_withheld"):
            lines += [f"_{c['interpretation_withheld']}_", ""]

        fired_or_read = [s for s in c["contributing_signals"]
                         if s["status"] in ("FIRED", "NOT_FIRED")]
        if fired_or_read:
            # Fired first, and marked as such — this is the list a reader scans to see
            # WHY a cluster raised, and a signal that ran clean sitting next to one that
            # fired, unmarked, reads as if either one could be the reason (IRCTC: S10
            # correctly did not fire on a FALLING share, S11 did fire and is why RC-CAP
            # raised, and an unmarked list let the two look interchangeable).
            fired_or_read.sort(key=lambda s: 0 if s["fired"] else 1)
            lines.append("**Contributing signals**")
            for s in fired_or_read:
                tag = " — FIRED" if s["fired"] else ""
                lines.append(f"- {s['title']} ({s['signal_id']}){tag}: {s['observation']}")
            lines.append("")

        if c.get("alt_explanations"):
            lines.append("**Plausible alternative explanations**")
            for a in c["alt_explanations"]:
                lines.append(f"- {a}")
            lines.append("")

        if c.get("affected_assertions"):
            lines.append("**Affected assertions** — " + ", ".join(c["affected_assertions"]))
            lines.append("")

        resp = c.get("recommended_response") or {}
        if resp:
            lines.append("**Candidate audit response (nature · timing · extent)**")
            if resp.get("nature"):
                lines.append(f"- Nature: {resp['nature']}")
            if resp.get("timing"):
                lines.append(f"- Timing: {resp['timing']}")
            if resp.get("extent"):
                lines.append(f"- Extent: {resp['extent']}")
            lines.append("")

        if c.get("evidence_request"):
            lines.append("**Evidence request** — a management explanation is a lead to "
                         "test, not evidence")
            for e in c["evidence_request"]:
                lines.append(f"- {e}")
            lines.append("")

        if c.get("interactions"):
            lines.append("**Interactions**")
            for i in c["interactions"]:
                lines.append(f"- {i}")
            lines.append("")

        if c.get("priority_reasoning"):
            lines += [f"_{c['priority_reasoning']}_", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block 7 — Audit-planning matrix, the principal deliverable
# ---------------------------------------------------------------------------
#
# NOTHING IS COMPUTED HERE. §10.4 calls this matrix the report's actual
# deliverable — everything in blocks 1-6 is supporting material for this one
# table. Every row is a cluster Block 6 already carries, condensed; every
# field is read straight off `ClusterPackage` and the priority engine's own
# `Priority` (`pipeline/fdr/priority.py`), ranked once inside
# `engine.evaluate()`.
#
# THE RISK CATEGORY IS NOT A JUDGEMENT MADE HERE. "Significant risk" is
# `ClusterPackage.significant_risk`; "significant BY NATURE" is the priority
# engine's own `by_nature` flag — both drawn from the planning knowledge
# base's per-THEME classification (`planning.py`), the same one Block 6's
# materiality basis already reads, never inferred from this run's numbers.
# Neither is available until a theme actually raises, which is why a table
# built honestly from real entities is often short or empty — see the lede.

def _risk_category(significant_risk: bool | None, by_nature: bool) -> str:
    if significant_risk and by_nature:
        return "Significant by nature"
    if significant_risk:
        return "Significant risk"
    return "Inherent risk"


def _build_matrix(ev: Evaluated, ctx: dict[str, Any]) -> dict[str, Any]:
    """Block 7 — one row per clustered theme that actually raised this run,
    ranked by the priority engine, with the full N·T·E response condensed."""
    raw_clusters = {c["cluster_id"]: c for c in (ev.payload.get("risk_clusters") or ())}
    priorities = ev.payload.get("priorities") or ()

    rows = []
    for p in priorities:
        cid = p.get("cluster_id")
        c = raw_clusters.get(cid)
        if not c or c.get("status") != "RAISED":
            continue
        resp = c.get("recommended_response") or {}
        rows.append({
            "rank": p.get("rank"),
            "cluster_id": cid,
            "theme": c.get("theme") or "",
            "affected_assertions": list(c.get("affected_assertions") or ()),
            "risk_category": _risk_category(c.get("significant_risk"), bool(p.get("by_nature"))),
            "response_nature": resp.get("nature") or "",
            "response_timing": resp.get("timing") or "",
            "response_extent": resp.get("extent") or "",
            "specialist_referral": list(c.get("specialist_referral") or ()),
            "diagnostic_confidence": c.get("diagnostic_confidence"),
            "corroboration": c.get("corroboration") or "standalone",
        })
    rows.sort(key=lambda r: r["rank"] if r["rank"] is not None else 999)

    # §11.2's own closing caveat, entity-type aware: a report on a non-government
    # entity has no C&AG relationship to name, and inventing one for every entity
    # would be exactly the kind of unearned specificity §17 exists to prohibit.
    et = ctx.get("entity_type") or {}
    is_government = bool(et.get("determined")) and et.get("entity_type") == "GOVERNMENT_COMPANY"
    strategy_owner = ("the audit team and the C&AG's risk-based methodology"
                      if is_government else "the audit team's own risk-based methodology")

    return {
        "rows": rows,
        "raised_count": len(rows),
        "lede": (
            "The candidate priorities for audit planning, ranked by the priority "
            "engine and drawn only from the clustered themes that actually raised "
            "this run — nothing here is proposed on a theme this run could not "
            "evaluate or that came back clean."
            if rows else
            "No clustered theme raised this run, so no candidate priority is "
            "proposed — an empty matrix is the honest answer here, not a "
            "placeholder for one."
        ),
        "closing_note": (
            f"The FDR proposes; the audit team decides. This matrix is planning "
            f"intelligence that feeds the plan — it is not the plan, does not set "
            f"scope or materiality, and does not decide strategy. That belongs to "
            f"{strategy_owner}."
        ),
    }


def _markdown_matrix(payload: dict[str, Any]) -> str:
    """Block 7, for the downloadable report — the matrix as a plain table."""
    lines = ["## 7. Audit-planning matrix — principal deliverable", "",
             payload["lede"], ""]
    if payload["rows"]:
        lines += ["| # | Cluster | Assertions | Risk | Response (N/T/E) | Specialist | "
                  "Conf. | Corrob. |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in payload["rows"]:
            assertions = ", ".join(r["affected_assertions"]) or "—"
            response = " / ".join(p for p in (r["response_nature"], r["response_timing"],
                                              r["response_extent"]) if p) or "—"
            specialist = ", ".join(r["specialist_referral"]) or "—"
            lines.append(f"| {r['rank']} | {r['theme']} ({r['cluster_id']}) | {assertions} | "
                        f"{r['risk_category']} | {response} | {specialist} | "
                        f"{r['diagnostic_confidence'] or '—'} | {r['corroboration']} |")
        lines.append("")
    lines += [payload["closing_note"], ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockSpec:
    """One block of the report.

    `narrated` is declared rather than inferred, because it decides how the
    block is scheduled: an unnarrated block is arithmetic and returns at once,
    while a narrated one costs a model call and is worth running beside its
    siblings. Nothing is narrated yet; the field exists so that turning one on
    is a flag rather than a refactor of the runner.
    """
    id: str
    number: int
    title: str
    # (evaluation, context) -> payload. `context` carries the enrichment the
    # adapter fetched once for the whole report — reads that are not part of the
    # audit spine and that no builder should be issuing for itself, because a
    # builder that does I/O is a builder that can be slow, can fail, and cannot
    # be tested without a database.
    builder: Callable[[Evaluated, dict[str, Any]], dict[str, Any]]
    to_markdown: Callable[[dict[str, Any]], str]
    narrated: bool = False


BLOCKS: tuple[BlockSpec, ...] = (
    BlockSpec("coverage", 1, "Business profile",
              _build_coverage, _markdown_coverage),
    BlockSpec("dashboard", 2, "Executive dashboard",
              _build_dashboard, _markdown_dashboard),
    BlockSpec("health", 4, "Financial health summary — structure & performance, "
             "interpreted", _build_health, _markdown_health),
    BlockSpec("trends", 5, "Key trends & structural drift",
              _build_trends, _markdown_trends),
    BlockSpec("clusters", 6, "Risk clusters with interactions",
              _build_clusters, _markdown_clusters),
    BlockSpec("matrix", 7, "Audit-planning matrix — principal deliverable",
              _build_matrix, _markdown_matrix),
)

BY_ID: dict[str, BlockSpec] = {b.id: b for b in BLOCKS}


def manifest() -> list[dict[str, Any]]:
    """What the report will contain, before any of it is built.

    The browser asks for this first so it can lay out the blocks it is about to
    receive — a reader watching one block arrive should be able to see how many
    are still coming, rather than guessing whether the report has finished.
    """
    return [{"id": b.id, "number": b.number, "title": b.title, "narrated": b.narrated}
            for b in BLOCKS]


def build(block_id: str, ev: Evaluated,
          context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one block. Raises `KeyError` for an unknown id.

    The vendored tree is put on `sys.path` here rather than left to the caller.
    It needs no database — `ensure_importable` is explicitly the half of the
    setup that does not — and doing it here means a builder can be exercised
    from a test with a stubbed evaluation, which is how the wording in these
    blocks is pinned without a corpus behind it.

    `context` defaults to empty so a builder handles a missing enrichment the
    same way it handles one that came back undetermined: a report is still owed
    when an optional read fails, and the block says which it was.
    """
    ensure_importable()
    spec = BY_ID[block_id]
    return {
        "id": spec.id,
        "number": spec.number,
        "title": spec.title,
        "payload": spec.builder(ev, context or {}),
        "report_version": REPORT_VERSION,
    }


def to_markdown(entity_id: str, blocks: list[dict[str, Any]],
                versions: dict[str, Any] | None = None) -> str:
    """The downloadable report, assembled from blocks already built.

    Built from the same payloads the screen rendered, never from a second pass
    over the evaluation — a download that could disagree with the screen it was
    taken from is worse than no download.
    """
    out = [f"# Financial Diagnostic Report — {entity_id}",
           "",
           "Audit-planning intelligence. Leads for audit attention — not an audit "
           "opinion, and not the audit plan.",
           ""]
    for block in sorted(blocks, key=lambda b: b.get("number", 99)):
        spec = BY_ID.get(block.get("id", ""))
        if spec is None:
            continue
        out.append(spec.to_markdown(block["payload"]))

    out += ["---", "",
            "These are candidate priorities for the audit team's decision. The team "
            "decides the audit strategy, scope and materiality; this report informs "
            "but does not decide the plan.", ""]
    if versions:
        out.append("| Version stamp | |")
        out.append("|---|---|")
        for key, value in sorted(versions.items()):
            out.append(f"| {key} | {value} |")
        out.append(f"| report | {REPORT_VERSION} |")
        out.append("")
    return "\n".join(out)
