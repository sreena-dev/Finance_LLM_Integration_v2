"""
Block 2 — the executive dashboard, built directly from `as_db` rows.

A pure function: raw fetch rows in, the exact JSON shape the frontend already renders
out. No panel, no trust scoring, no fact-store — see the package docstring in
`__init__.py` for why that machinery does not apply to tagged XBRL data.

THE WIRE CONTRACT THIS MATCHES
-------------------------------
Traced from `backend/modes/financial_diagnostic_report/report.py`'s `_tile_field()`
and `_build_dashboard()`, which is what `frontend/.../FdrAnalysis.jsx`'s
`DashboardBlock` actually reads. Matching these field names means the existing
component renders this payload with no frontend changes:

  tile:  id, label, computed, value, display, unit_label, movement_label, direction,
         attention, context_display, series_years, reason, movement_reason, citations,
         formula, formula_values
  block: tiles, computed_count, total_count, flagged_ids, scale_label, lede

WHAT IS DIFFERENT FROM THE PDF-PATH VERSION, AND WHY
------------------------------------------------------
- `citations` cite `doc_id` + `concept_name` + `fs_type`, not a PDF `table_id`/`page` —
  that is the true provenance for an XBRL fact; there is no page to point to.
- `series_years` is always 1 or 2, never more — one filing carries at most a current
  and one prior comparative. It is reported honestly rather than padded.
- There is no SCALE_SUSPECT decimal-error heuristic: `headline.py`'s version exists to
  catch OCR/transcription errors across loosely-verified PDF extractions. An XBRL
  `value_numeric` is tagged and exact; a "suspiciously round-tripping" ratio here would
  be a real business event, not an extraction artefact, and inventing a detector for an
  error class that cannot occur in this data would be exactly the "fabricated trust"
  this package's docstring says to avoid.
"""
from __future__ import annotations
from datetime import date
from typing import Any

from .xbrl_concepts import (
    METRICS, Metric, CURRENCY, PERCENT, TIMES,
    ADVERSE_UP, ADVERSE_DOWN, ATTENTION_RELATIVE, ATTENTION_POINTS, EXTREME_MOVEMENT_RATIO,
)

_UNIT_LABEL = {PERCENT: "", TIMES: "×"}
_CRORE = 10_000_000  # 1 crore = 1,00,00,000 INR
_LAKH = 100_000      # 1 lakh = 1,00,000 INR


def detect_scale(rows: list[dict[str, Any]]) -> str:
    """
    Determines whether to present the filing in 'lakh' or 'crore'.
    If the maximum absolute monetary figure across the filing is below 1 crore
    (10,000,000 INR) but greater than 0, scale as 'lakh' to prevent small entities
    from displaying as 0.00 INR crore. Otherwise, scale as 'crore'.
    """
    currency_vals = [
        abs(r["value_numeric"]) for r in rows
        if r.get("value_numeric") is not None and r.get("unit") == "INR"
    ]
    max_val = max(currency_vals, default=0.0)
    if 0 < max_val < _CRORE:
        return "lakh"
    return "crore"


def _fy_label(fy_start: date | None, fy_end: date | None) -> str:
    """Same spelling as pipeline/fdr/source.py's `_fy_label` — 'FY2022-23'."""
    if fy_end is None:
        return "unknown"
    start_year = fy_start.year if fy_start is not None else fy_end.year - 1
    return f"FY{start_year}-{str(fy_end.year)[-2:]}"


def _format(value: float | None, unit: str, scale: str = "crore") -> str:
    if value is None:
        return "—"
    if unit == CURRENCY:
        divisor = _LAKH if scale == "lakh" else _CRORE
        return f"{value / divisor:,.2f}"
    if unit == PERCENT:
        return f"{value * 100:.2f}%"
    if unit == TIMES:
        return f"{value:,.2f}x"
    return f"{value:,.2f}"


def _index_by_concept(rows: list[dict[str, Any]]) -> dict[str, dict[date, dict[str, Any]]]:
    """concept_name -> {fy_end: row}. A duplicate (fy_end, concept) pair (should not
    happen — verified 0 occurrences corpus-wide when has_dimensions=false) keeps the
    first row seen rather than raising, so one bad filing cannot break every other."""
    out: dict[str, dict[date, dict[str, Any]]] = {}
    for r in rows:
        bucket = out.setdefault(r["concept_name"], {})
        bucket.setdefault(r["fy_end"], r)
    return out


def _summed_series(by_concept: dict[str, dict[date, dict]],
                    concepts: tuple[str, ...]) -> list[tuple[date, date | None, float]]:
    """(fy_end, fy_start, value) for every period ALL of `concepts` cover — mirrors
    `headline.py::_sum_series`'s "only years every key covers" rule. A period where
    only one of several summed concepts is present is dropped entirely rather than
    treating the missing one as zero, which would understate a real total (e.g.
    reporting only current borrowings as if non-current borrowings were nil)."""
    if not concepts:
        return []
    period_sets = []
    for c in concepts:
        bucket = by_concept.get(c)
        if not bucket:
            return []
        period_sets.append(set(bucket))
    common = set.intersection(*period_sets)
    if not common:
        return []
    out = []
    for fy_end in sorted(common):
        total = sum(by_concept[c][fy_end]["value_numeric"] for c in concepts)
        fy_start = by_concept[concepts[0]][fy_end]["fy_start"]
        out.append((fy_end, fy_start, float(total)))
    return out


def _citations(concepts: tuple[str, ...], doc_id: str, fy_end: date,
                by_concept: dict[str, dict[date, dict]], role: str = "") -> list[dict[str, Any]]:
    out = []
    for c in concepts:
        row = by_concept.get(c, {}).get(fy_end)
        out.append({
            "key": c,
            "value": float(row["value_numeric"]) if row else None,
            "doc_id": doc_id,
            "concept_name": c,
            "located": row is not None,
            "role": role,
        })
    return out


def _sum_formula(label: str, concepts: tuple[str, ...]) -> str:
    """'label = concept' or 'label = concept1 + concept2' for a plain summed tile."""
    return f"{label} = " + " + ".join(concepts)


def _ratio_formula(label: str, numerator: tuple[str, ...], denominator: tuple[str, ...]) -> str:
    num = " + ".join(numerator)
    den = " + ".join(denominator)
    return f"{label} = ({num}) ÷ ({den})"


def _compute_metric(m: Metric, by_concept: dict[str, dict[date, dict]],
                     doc_id: str, scale: str = "crore") -> dict[str, Any]:
    scale_label = "INR lakh" if scale == "lakh" else "INR crore"
    unit_label = scale_label if m.unit == CURRENCY else _UNIT_LABEL.get(m.unit, "")
    out: dict[str, Any] = {
        "id": m.id, "label": m.label, "unit_label": unit_label,
    }

    if m.kind == "derived":
        num_series = _summed_series(by_concept, m.concepts)
        den_series = _summed_series(by_concept, m.denominator_concepts)
        num_by_year = {fy: v for fy, _, v in num_series}
        den_by_year = {fy: v for fy, _, v in den_series}
        common = sorted(set(num_by_year) & set(den_by_year), reverse=True)
        # A non-positive denominator makes the ratio meaningless (e.g. effective tax
        # rate on a pre-tax loss), not merely small — dropped rather than shown, per
        # `xbrl_concepts.Metric` field intent.
        common = [fy for fy in common if den_by_year[fy] > 0]
        if not common:
            out.update(computed=False, value=None, display="—",
                       reason="Not computable for this filing — either the underlying "
                              "figures are absent, or the denominator is zero or "
                              "negative, which makes the ratio meaningless rather "
                              "than merely small.")
            return out
        latest = common[0]
        num_val = num_by_year[latest]
        den_val = den_by_year[latest]
        value = num_val / den_val
        out.update(
            computed=True, value=value, display=_format(value, m.unit, scale=scale),
            attention=False, direction="", series_years=len(common), movement_label="",
            formula=_ratio_formula(m.label, m.concepts, m.denominator_concepts),
            formula_values=(
                f"({num_val:,.2f}) ÷ ({den_val:,.2f}) = {_format(value, m.unit, scale=scale)}"
            ),
            citations=(
                _citations(m.concepts, doc_id, latest, by_concept, role="numerator")
                + _citations(m.denominator_concepts, doc_id, latest, by_concept, role="denominator")
            ),
        )
        return out

    series = _summed_series(by_concept, m.concepts)
    if not series:
        reason = ("This filing does not report a value for this concept — a "
                  "structural absence, not an extraction gap.") if m.absence_is_legitimate \
                 else "No value for this concept was found in this filing."
        out.update(computed=False, value=None, display="—", reason=reason)
        return out

    series.sort(key=lambda t: t[0], reverse=True)   # latest fy_end first
    (fy_end, fy_start, value), *rest = series
    year_label = _fy_label(fy_start, fy_end)

    common_fields = dict(
        computed=True, value=value, display=_format(value, m.unit, scale=scale),
        series_years=len(series),
        citations=_citations(m.concepts, doc_id, fy_end, by_concept),
        formula=_sum_formula(m.label, m.concepts),
        formula_values=(
            (" + ".join(f"{by_concept[c][fy_end]['value_numeric']:,.2f}" for c in m.concepts))
            + f" = {_format(value, m.unit, scale=scale)}"
        ),
    )

    if m.context_over:
        ctx_series = _summed_series(by_concept, m.context_over)
        ctx_val = next((v for fy, _, v in ctx_series if fy == fy_end), None)
        if ctx_val:
            out["context_display"] = f"{value / ctx_val:,.2f}× {m.context_label}"

    if not rest:
        out.update(**common_fields, attention=False, direction="", movement_label="",
                    reason=f"Bound for {year_label}; no comparable prior year exists "
                           f"in this filing, so no movement is shown.")
        return out

    prior_fy_end, prior_fy_start, prior_value = rest[0]
    prior_label = _fy_label(prior_fy_start, prior_fy_end)

    if prior_value == 0:
        out.update(**common_fields, attention=False, direction="", movement_label="",
                    reason=f"The {prior_label} figure is zero, so a movement against "
                           f"it is undefined rather than large. The level is shown; "
                           f"the movement is not.")
        return out

    delta = (value - prior_value) / abs(prior_value)

    # A non-zero but very small prior-year base produces the same distortion as a zero
    # one, just less obviously: dividing by something tiny turns an ordinary absolute
    # change into an enormous percentage that is arithmetically correct but not a
    # meaningful "movement" in the way a percentage normally communicates one. Rather
    # than guess what counts as "small" (which would vary by entity and metric), the
    # ceiling is applied to the RESULT — once a percentage would be this extreme, the
    # percentage itself is the tell, regardless of why. The actual prior-year figure and
    # the absolute change are still shown, so nothing is hidden — only the misleading
    # percentage is withheld, exactly as it already is for the prior_value == 0 case
    # above, which this extends rather than replaces.
    if m.unit == CURRENCY and abs(delta) > EXTREME_MOVEMENT_RATIO:
        divisor = _LAKH if scale == "lakh" else _CRORE
        prior_scaled = prior_value / divisor
        change_scaled = (value - prior_value) / divisor
        out.update(
            **common_fields, attention=False, direction="", movement_label="",
            reason=(f"The {prior_label} figure ({prior_scaled:,.2f} {scale_label}) is too small "
                    f"relative to {year_label}'s for a percentage to be meaningful — the "
                    f"raw calculation would show {delta * 100:+.2f}%, which reflects how "
                    f"small the starting point was, not a real business swing. The level "
                    f"is shown; the change in absolute terms is {change_scaled:+,.2f} {scale_label}, "
                    f"and the percentage movement is withheld."))
        return out

    direction = "rose" if delta > 0 else "fell" if delta < 0 else "unchanged"
    band = ATTENTION_POINTS if m.unit == PERCENT else ATTENTION_RELATIVE
    adverse = ((m.salience == ADVERSE_UP and delta > 0)
               or (m.salience == ADVERSE_DOWN and delta < 0))
    movement_label = (f"unchanged vs {prior_label}" if delta == 0
                      else f"{delta * 100:+.2f}% vs {prior_label}")

    out.update(**common_fields, direction=direction,
                attention=bool(adverse and abs(delta) >= band),
                movement_label=movement_label)
    return out


def build_dashboard(rows: list[dict[str, Any]], doc_id: str, scale: str | None = None) -> dict[str, Any]:
    """Block 2, for one filing. `rows` is `xbrl_fetch.fetch_doc_metrics()`'s output."""
    chosen_scale = scale or detect_scale(rows)
    scale_label = "INR lakh" if chosen_scale == "lakh" else "INR crore"
    by_concept = _index_by_concept(rows)
    tiles = [_compute_metric(m, by_concept, doc_id, scale=chosen_scale) for m in METRICS]

    computed = [t for t in tiles if t["computed"]]
    flagged = [t for t in tiles if t.get("attention")]

    return {
        "tiles": tiles,
        "computed_count": len(computed),
        "total_count": len(tiles),
        "flagged_ids": [t["id"] for t in flagged],
        "scale_label": scale_label,
        "lede": (
            "The shape of the entity in these figures, read directly from its filed "
            "XBRL statements before anything is interpreted. Each movement is "
            "measured against the single prior year carried in this filing — one "
            "year's change, not a trend."
            if tiles else
            "No figures could be computed for this filing."
        ),
    }
