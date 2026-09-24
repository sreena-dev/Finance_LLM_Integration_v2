"""
The entity-year panel — ROADMAP M6, and the thing every Layer 2/3/4 signal reads.

WHAT IT IS
----------
A panel is one entity, one flavour, N consecutive years, keyed by canonical concept:

    panel.get("total_assets", "FY2024-25")  ->  4516527.58 (normalised to INR lakh)

`evaluate.py` has declared the `PanelLike` protocol since M3 and abstained because nothing
satisfied it. This satisfies it. No other module changes shape.

THE COMPARABILITY GATE — WHY A PANEL CAN REFUSE TO EXIST
--------------------------------------------------------
Putting five years side by side is only meaningful if they ARE comparable. Four things
break comparability, and each is checked rather than assumed:

  C1 flavour       standalone and consolidated are never mixed. A consolidated year in a
                   standalone panel silently changes what every ratio measures.
  C2 continuity    the years must be consecutive. A gap is not a trend; it is two trends.
  C3 scale         every figure arrives already normalised to INR lakh by the fact layer.
                   Figures whose scale was GUESSED from the filing mode are still served —
                   a uniform guess is a constant factor and cancels out of every growth
                   rate and ratio — but `series()` refuses a window where a guess
                   contradicts the scales that were actually read. See `fs_db/units.py`.
  C4 restatement   a restated comparative and an originally-reported figure are different
                   measurements of the same year. Recorded when known, and surfaced.

§9.5 is the hard rule the panel enforces on behalf of every trend signal: FEWER THAN
THREE COMPARABLE YEARS MEANS TREND DIAGNOSTICS ARE NOT RUN. They are never computed on a
short series and a two-point movement is never presented as a trend. `is_trend_capable`
is what a rule asks; `window()` returns None rather than a truncated series.

WHAT THE PANEL DOES NOT DO
--------------------------
It does not interpret, threshold, rate or decide. It answers "what was this figure in
that year, and can I trust it" — nothing else. Every judgement lives in `rules.py`, and
every judgement about SEVERITY lives in the registry, not here.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

STANDALONE, CONSOLIDATED = "standalone", "consolidated"

# §9.5 — the minimum series length before any trend statistic may be presented.
MIN_TREND_YEARS = 3


@dataclass(frozen=True)
class PanelCell:
    """One figure, with the trust that came with it from the fact layer."""
    value: float
    fy_label: str
    period_end: str
    unit_confidence: str
    verify_verdict: str
    unit_scale: str = ""
    source_label: str = ""
    doc_id: str = ""
    table_id: str = ""
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "fy": self.fy_label, "period_end": self.period_end,
                "unit_confidence": self.unit_confidence, "unit_scale": self.unit_scale,
                "verify_verdict": self.verify_verdict,
                "source": self.source_label, "doc_id": self.doc_id,
                "table_id": self.table_id, "page": self.page}


@dataclass
class Panel:
    """N years of one entity's canonical figures, in INR lakh throughout."""
    entity_id: str
    flavor: str
    years: tuple[str, ...] = ()                       # FY labels, oldest first
    cells: dict[tuple[str, str], PanelCell] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    # ---- the PanelLike protocol --------------------------------------------------

    def get(self, key: str, year: str) -> float | None:
        c = self.cells.get((key, year))
        return None if c is None else c.value

    # ---- everything a rule needs beyond a single figure --------------------------

    def cell(self, key: str, year: str) -> PanelCell | None:
        return self.cells.get((key, year))

    def has(self, key: str, year: str) -> bool:
        return (key, year) in self.cells

    @property
    def latest(self) -> str | None:
        return self.years[-1] if self.years else None

    @property
    def is_trend_capable(self) -> bool:
        """§9.5 — three comparable years, or no trend statistic at all."""
        return len(self.years) >= MIN_TREND_YEARS

    def series(self, key: str) -> list[tuple[str, float]] | None:
        """The longest run of CONSECUTIVE years ending at the latest available.

        Not all-or-nothing across the whole panel, and not every year that happens to
        carry the key either. Both extremes are wrong:

          - requiring the key in every panel year discards a perfectly good four-year
            series because the fifth, oldest filing bound one line differently;
          - accepting every year that has the key would silently compare FY2021 with
            FY2024 across a hole, presenting two disjoint runs as one trend.

        So: walk back from the most recent year and stop at the first gap. What comes
        back is always contiguous, always ends at the latest data, and is None when the
        key is absent from the latest year — because a trend that stops two years ago is
        not a statement about this filing.
        """
        out: list[tuple[str, float]] = []
        cells: list[PanelCell] = []
        for y in reversed(self.years):
            c = self.cells.get((key, y))
            if c is None:
                break
            out.append((y, c.value))
            cells.append(c)
        out.reverse()
        cells.reverse()
        if not out:
            return None

        # C3, applied where it can be applied precisely.
        #
        # A scale CHANGE across the window is not itself a problem: entities do re-present
        # from lakh to crore, `value_inr_lakh` normalises it, and a change detected at HIGH
        # confidence is simply a fact about the filings. What is dangerous is a scale that
        # was GUESSED (LOW confidence, from the filing mode) and guessed WRONG, because a
        # wrong guess is the one thing that manufactures a 100x movement out of nothing.
        #
        # So the test is not "does the scale vary" — it is "does a guess disagree with what
        # is actually known". A LOW cell that agrees with the well-resolved cells around it
        # is harmless. A LOW cell that introduces a scale nobody else in the window reports
        # is refused.
        #
        # Refusing on variation alone was the first rule here and it was wrong: IRCTC's
        # total assets are CONFIRMED in all seven years, with one genuine crore year read
        # at HIGH confidence, and the whole series was being thrown away because a single
        # neighbouring year's scale came from the filing mode.
        guessed = [c for c in cells if c.unit_confidence == "LOW" and c.unit_scale]
        if guessed:
            known = [c.unit_scale for c in cells
                     if c.unit_confidence in ("HIGH", "MEDIUM") and c.unit_scale]
            if not known:
                # Nothing in the window is well-resolved. A uniform guess is a constant
                # factor and cancels out; a varying one cannot be reconciled against
                # anything, so it is refused.
                if len({c.unit_scale for c in cells if c.unit_scale}) > 1:
                    return None
            else:
                dominant = Counter(known).most_common(1)[0][0]
                if any(c.unit_scale != dominant for c in guessed):
                    return None
        return out

    def window(self, key: str, n: int = MIN_TREND_YEARS) -> list[tuple[str, float]] | None:
        """The most recent `n` consecutive years of one key, or None.

        Returns None — never a truncated series — when fewer than `n` consecutive years
        exist. A rule that asked for three years and silently received two would present
        a two-point movement as a trend, which §9.5 forbids by name.
        """
        if n < 1:
            return None
        s = self.series(key)
        return None if (s is None or len(s) < n) else s[-n:]

    def missing(self, keys: Iterable[str], n: int = MIN_TREND_YEARS) -> tuple[str, ...]:
        """Which of `keys` cannot supply an `n`-year consecutive series.

        This is what a rule abstains on, so it has to answer the rule's actual question —
        "can I form my window?" — not the weaker "is this key present somewhere?".
        """
        return tuple(k for k in keys if self.window(k, n) is None)

    # ---- diagnosis -------------------------------------------------------------------
    # WHY A RULE MUST NOT SAY "FEWER THAN 3 YEARS, OR THE INPUTS ARE NOT BOUND"
    #
    # That disjunction covers both branches because it was cheaper to write than to ask
    # which one actually happened — and the panel knows. On a five-year ONGC panel the
    # first branch is FALSE at the moment it is emitted, so the report tells a reviewer
    # something untrue about itself. It is also unqueryable: the one question worth asking
    # across the corpus — "which missing figure blocks the most diagnostics?" — cannot be
    # answered from a sentence covering two possibilities.
    #
    # `diagnose` answers the rule's real question per key: can this key form an n-year
    # window, and if not, exactly why not and in which years.

    NEVER_BOUND = "NEVER_BOUND"                  # the key appears in no panel year
    ABSENT_FROM_LATEST = "ABSENT_FROM_LATEST"    # present, but not in the current filing
    BROKEN_BY_GAP = "BROKEN_BY_GAP"              # enough years exist, but not consecutively
    TOO_FEW_YEARS = "TOO_FEW_YEARS"              # the history itself is shorter than n
    SCALE_UNRECONCILED = "SCALE_UNRECONCILED"    # a guessed scale contradicts the window
    OK = "OK"

    def diagnose(self, keys: Iterable[str], n: int = MIN_TREND_YEARS) -> dict[str, dict]:
        """Per key: whether it can form an `n`-year window, and precisely why not."""
        out: dict[str, dict] = {}
        want = list(self.years[-n:]) if len(self.years) >= n else list(self.years)
        for k in keys:
            present = [y for y in self.years if (k, y) in self.cells]
            s = self.series(k)
            run = len(s) if s else 0
            if not present:
                state = self.NEVER_BOUND
            elif s is None and self.latest and (k, self.latest) not in self.cells:
                state = self.ABSENT_FROM_LATEST
            elif s is None:
                # Cells exist and reach the latest year, so the refusal came from the
                # scale guard in `series` — a materially different fix from a binding gap.
                state = self.SCALE_UNRECONCILED
            elif run >= n:
                state = self.OK
            elif len(present) >= n:
                state = self.BROKEN_BY_GAP
            else:
                state = self.TOO_FEW_YEARS
            out[k] = {
                "state": state,
                "years_present": present,
                "series_len": run,
                "missing_years": [y for y in want if (k, y) not in self.cells],
            }
        return out

    def coverage(self) -> dict[str, Any]:
        keys = sorted({k for k, _ in self.cells})
        trendable = [k for k in keys if self.window(k, MIN_TREND_YEARS) is not None]
        latest = [k for k in keys if self.latest and self.has(k, self.latest)]
        return {"entity_id": self.entity_id, "flavor": self.flavor,
                "years": list(self.years), "keys_total": len(keys),
                "keys_trendable": len(trendable), "keys_trendable_list": trendable,
                "keys_latest_year": len(latest),
                "trend_capable": self.is_trend_capable, "gate": self.gate,
                "notes": list(self.notes)}


# ---- construction ------------------------------------------------------------------

def _fy_sort_key(fy: str) -> tuple:
    """Order FY labels chronologically without assuming one spelling.

    'FY2024-25', 'FY2024-2025' and '2024-25' all sort by their leading year.
    """
    digits = "".join(ch if ch.isdigit() else " " for ch in fy).split()
    return (int(digits[0]) if digits else 0, fy)


def build(rows: list[dict[str, Any]], *, entity_id: str, flavor: str = STANDALONE,
          max_years: int = 5) -> Panel:
    """Assemble a panel from stored facts, applying the comparability gate.

    `rows` come from `FactsStore.facts(..., trusted_only=True)`, so C3 (scale) is already
    satisfied on entry. This applies C1, C2 and C4 and records what it dropped.
    """
    notes: list[str] = []

    # C1 — one flavour only. Mixing them changes what every figure measures.
    rows = [r for r in rows if r.get("flavor") == flavor]
    if not rows:
        return Panel(entity_id, flavor, gate={"status": "EMPTY",
                     "reason": f"no trusted facts for flavour {flavor!r}"})

    by_year: dict[str, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["fy_label"], []).append(r)
    all_years = sorted(by_year, key=_fy_sort_key)

    # C2 — the years must be consecutive. A gap makes two series, not one.
    run = _longest_consecutive(all_years)
    if len(run) < len(all_years):
        dropped = [y for y in all_years if y not in run]
        notes.append(
            f"Non-consecutive filing years present ({', '.join(dropped)} dropped). A gap "
            f"in the series is two trends, not one, so only the longest consecutive run "
            f"is used.")

    years = tuple(run[-max_years:])

    cells: dict[tuple[str, str], PanelCell] = {}
    for y in years:
        for r in by_year[y]:
            key = (r["canonical_key"], y)
            v = r.get("value_inr_lakh")
            if v is None:
                continue
            # C4 — a duplicate key within one year means two statements disagreed. Keep
            # the stronger verdict and say so rather than letting insertion order decide.
            prev = cells.get(key)
            if prev is not None and prev.verify_verdict == "CONFIRMED" \
                    and r["verify_verdict"] != "CONFIRMED":
                continue
            cells[key] = PanelCell(
                value=float(v), fy_label=y, period_end=r["period_end"],
                unit_confidence=r["unit_confidence"], verify_verdict=r["verify_verdict"],
                unit_scale=r.get("unit_scale") or "",
                source_label=r.get("source_label") or "", doc_id=r.get("doc_id") or "",
                table_id=r.get("table_id") or "", page=r.get("page"))

    gate = {
        "status": "OK" if len(years) >= MIN_TREND_YEARS else "SHORT_SERIES",
        "years_available": len(all_years),
        "years_used": len(years),
        "trend_capable": len(years) >= MIN_TREND_YEARS,
        "reason": ("" if len(years) >= MIN_TREND_YEARS else
                   f"{len(years)} comparable year(s); Section 9.5 requires "
                   f"{MIN_TREND_YEARS} before any trend statistic is presented, and "
                   f"forbids presenting a two-point movement as a trend."),
    }
    return Panel(entity_id, flavor, years, cells, gate, tuple(notes))


def _longest_consecutive(years: list[str]) -> list[str]:
    """The longest run of consecutive FY labels, preferring the most recent."""
    if not years:
        return []
    nums = [_fy_sort_key(y)[0] for y in years]
    best_i = best_len = 0
    i = 0
    while i < len(years):
        j = i
        while j + 1 < len(years) and nums[j + 1] == nums[j] + 1:
            j += 1
        if (j - i + 1) >= best_len:            # >= prefers the later run on a tie
            best_i, best_len = i, j - i + 1
        i = j + 1
    return years[best_i:best_i + best_len]
