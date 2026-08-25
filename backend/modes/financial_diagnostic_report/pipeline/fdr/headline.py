"""
The executive dashboard — block 2's headline reads, computed for any entity.

WHAT THIS IS
------------
Ten figures a reviewer looks at before reading anything else: what the entity earned, what
it turned into cash, what it owns, what it owes, and how hard its two largest non-cash and
financing lines are working. Each is one number, one movement, and the year that movement
is measured against.

It is NOT a diagnostic layer. Nothing here fires, rates, ranks or concludes. `rules.py`
remains the only place a verdict is formed and `signals.py` the only place severity lives.
A tile is a FIGURE with its provenance attached — the dashboard equivalent of reading the
face of the statements, which §2.1 puts before interpretation rather than after it.

WHY A DECLARATIVE REGISTRY AND NOT TEN FUNCTIONS
------------------------------------------------
Every tile is a sum of canonical keys over a sum of canonical keys. Written as a registry,
the dashboard is entity-agnostic by construction: there is no per-entity code to write, no
sector special case to maintain, and adding a tile is one entry rather than one function
plus one render branch. It mirrors `derivations.py` deliberately — same shape, and the
same split between an entity-agnostic core and sector overlays gated on the business model.

THE TWO RULES THE TEMPLATE TAUGHT US
------------------------------------
1. SALIENCE IS DECLARED, NEVER INFERRED FROM SIGN. A dashboard that colours every rise
   favourably is wrong about half its own tiles: receivables rising while revenue falls is
   the adverse case, and profit falling is the adverse case, and the two have opposite
   signs. So each tile states IN ADVANCE which direction is adverse for it, and the tiles
   whose direction carries no read say that instead of guessing. The mark is a reading aid
   for a human, never a finding, and it is never combined into a score.

2. A MOVEMENT IS LABELLED WITH THE YEAR IT IS MEASURED AGAINST. §9.5 forbids presenting a
   two-point movement as a trend, and a bare "-3.87%" on a tile is exactly that
   presentation. Every delta here carries `comparison_year`, `movement_label` cannot be
   rendered without it, and `series_years` says how much history stands behind the figure.
   The word "trend" appears nowhere in this module's output, because nothing in it is one.

WHY AN UNBOUND TILE STILL RENDERS
---------------------------------
The same §4.4 reason a cluster that went quiet is not a clean cluster: a dashboard that
silently drops the tile it could not compute reads as a complete dashboard. So a tile that
cannot be computed comes back with a state from `Panel.diagnose`'s own vocabulary and the
reason that goes with it — NEVER_BOUND is binder work, BROKEN_BY_GAP is one filing's
extraction, ABSENT_FROM_LATEST is a different job again — and the renderer shows it as
unbound rather than omitting it.

UNITS
-----
Values are in INR lakh throughout, because that is what `value_inr_lakh` normalises every
figure in the panel to. Presenting them in millions or crore is a display decision and
lives in `format_value`, at the last possible moment, where it cannot reach a computation.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from . import thresholds as TH
from .panel import Panel

VERSION = "fdr-headline-1.0.0"

# ---- how a tile's value is written -------------------------------------------------
CURRENCY = "currency"    # a rupee amount, held in INR lakh
RATIO = "ratio"          # a pure number: 1.40, 0.03
PERCENT = "percent"      # held as a fraction, written as a percentage
TIMES = "times"          # a cover or multiple: 2.05x
UNITS = frozenset({CURRENCY, RATIO, PERCENT, TIMES})

# ---- which direction is adverse, declared per tile ---------------------------------
# Never inferred. See rule 1 in the module docstring. NEUTRAL is not a hedge: it is the
# correct answer for a figure whose movement carries no read on its own, and stating it
# stops a renderer from inventing one.
ADVERSE_UP = "ADVERSE_WHEN_RISING"
ADVERSE_DOWN = "ADVERSE_WHEN_FALLING"
NEUTRAL = "DIRECTION_NEUTRAL"
SALIENCE = frozenset({ADVERSE_UP, ADVERSE_DOWN, NEUTRAL})

# ---- how a movement is expressed ---------------------------------------------------
# A RATE's movement is stated in percentage points, not as a percentage of a percentage.
# `rules._pp` exists for the same reason: 23.84% moving to 24.13% is +0.29 percentage
# points, and writing it as "+1.21%" invites it to be read as a rate of change of the
# business rather than of the ratio.
RELATIVE = "relative"
POINTS = "percentage_points"

# ---- the attention band ------------------------------------------------------------
# NOT a threshold in the `thresholds.py` sense: nothing here decides whether a diagnostic
# fires, and that module's contract is explicitly about firing. This decides only whether
# a movement is worth a reviewer's eye on first read. The relative band is deliberately
# THE SAME NUMBER as the signal layer's material-movement trigger, read from that module
# rather than restated, so the dashboard and the diagnostics cannot drift apart.
ATTENTION_RELATIVE = TH.get("material_movement")
ATTENTION_POINTS = 0.05
ATTENTION_BASIS = (
    f"A movement is marked for attention at {ATTENTION_RELATIVE:.0%} relative — the signal "
    f"layer's own material-movement trigger, read from thresholds.py so the two cannot "
    f"drift — or {ATTENTION_POINTS * 100:.0f} percentage points for a rate. The mark is a "
    f"reading aid, not a finding, and the direction it reads as adverse is declared per "
    f"tile in advance rather than taken from the sign."
)

# ---- states a tile can be in -------------------------------------------------------
# The first five are `Panel.diagnose`'s own vocabulary, reused rather than restated so a
# reader who has read block 9 already knows what they mean.
OK = Panel.OK
NEVER_BOUND = Panel.NEVER_BOUND
ABSENT_FROM_LATEST = Panel.ABSENT_FROM_LATEST
BROKEN_BY_GAP = Panel.BROKEN_BY_GAP
TOO_FEW_YEARS = Panel.TOO_FEW_YEARS
SCALE_UNRECONCILED = Panel.SCALE_UNRECONCILED
VALUE_ONLY = "VALUE_ONLY"            # the figure is bound; no comparable prior year
NOT_COMPUTABLE = "NOT_COMPUTABLE"    # inputs bound; the arithmetic is undefined
NOT_APPLICABLE = "NOT_APPLICABLE"    # the framework does not use the concept
NEEDS_BINDER = "NEEDS_BINDER"        # no LineSpec binds this key in any filing, ever
NO_PANEL = "NO_PANEL"                # no entity-year panel was built

COMPUTED_STATES = frozenset({OK, VALUE_ONLY})


@dataclass(frozen=True)
class Tile:
    """One headline read, declared as data.

    `numerator` and `denominator` are sums of canonical panel keys. A tile with no
    denominator is a level (revenue, profit); one with a denominator is a ratio formed
    from the same year's figures on both sides.
    """
    id: str
    label: str
    unit: str
    salience: str
    basis: str                                   # why this figure is on the first screen
    numerator: tuple[str, ...]
    denominator: tuple[str, ...] = ()
    # A second, DECLARED read of the same figure — "operating cash flow as a multiple of
    # profit". Rendered beside the tile, never instead of it.
    context_over: tuple[str, ...] = ()
    context_label: str = ""
    business_models: frozenset[str] = frozenset()          # empty = core, every entity
    not_applicable_frameworks: frozenset[str] = frozenset()
    attention: float = 0.0                       # 0.0 = the module default for this kind
    # Set when no LineSpec in the fact layer binds this tile's keys in any filing. The
    # tile is still declared — that is what makes the gap enumerable via `work_items()`
    # instead of an absence nobody can query.
    binder_required: str = ""
    notes: tuple[str, ...] = ()

    @property
    def is_core(self) -> bool:
        return not self.business_models

    @property
    def keys(self) -> tuple[str, ...]:
        """Every key this tile reads, including the context read. Order-preserving."""
        return tuple(dict.fromkeys(self.numerator + self.denominator + self.context_over))

    @property
    def required_keys(self) -> tuple[str, ...]:
        """The keys without which there is no tile. The context read is not one of them."""
        return tuple(dict.fromkeys(self.numerator + self.denominator))

    @property
    def delta_kind(self) -> str:
        return POINTS if self.unit == PERCENT else RELATIVE

    @property
    def formula(self) -> str:
        num = " + ".join(self.numerator)
        if not self.denominator:
            return num
        den = " + ".join(self.denominator)
        return f"({num}) / ({den})" if len(self.numerator) > 1 else f"{num} / ({den})"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "unit": self.unit,
                "salience": self.salience, "basis": self.basis,
                "formula": self.formula, "delta_kind": self.delta_kind,
                "numerator": list(self.numerator),
                "denominator": list(self.denominator),
                "context_over": list(self.context_over),
                "context_label": self.context_label,
                "business_models": sorted(self.business_models),
                "not_applicable_frameworks": sorted(self.not_applicable_frameworks),
                "binder_required": self.binder_required,
                "core": self.is_core, "notes": list(self.notes)}


# ===================================================================================
# The core dashboard — ten tiles, every entity, every Schedule III division that uses
# the concept. Nothing sector-specific appears here by design: a dashboard shaped
# around one industry inherits that industry's blind spots, which is how a
# capital-intensive entity's two largest non-cash and financing charges went missing
# from the report this module was specified against.
# ===================================================================================

_CORE: tuple[Tile, ...] = (
    Tile(
        id="H01", label="Revenue from operations", unit=CURRENCY,
        salience=ADVERSE_DOWN,
        basis="The top line every other read is scaled against. Its movement is what makes "
              "a receivables or margin movement interpretable rather than merely large.",
        numerator=("revenue",),
    ),
    Tile(
        id="H02", label="Profit for the year", unit=CURRENCY,
        salience=ADVERSE_DOWN,
        basis="The reported result. Placed on the first screen so the cash-flow tile beside "
              "it can be read against it rather than in isolation.",
        numerator=("pat",),
    ),
    Tile(
        id="H03", label="Operating cash flow", unit=CURRENCY,
        salience=ADVERSE_DOWN,
        basis="Whether the reported result arrived as cash. Read against profit rather than "
              "alone: the same rupee figure means different things at twice profit and at "
              "two-fifths of it, and the multiple is the read, not the level.",
        numerator=("ocf",),
        context_over=("pat",), context_label="of profit for the year",
    ),
    Tile(
        id="H04", label="Total assets", unit=CURRENCY,
        salience=NEUTRAL,
        basis="The size of the balance sheet, as the scale reference for everything else. "
              "Growth is neither favourable nor adverse on its own — this is the "
              "denominator that makes the rest of the dashboard proportionate.",
        numerator=("total_assets",),
    ),
    Tile(
        id="H05", label="Depreciation, depletion and amortisation", unit=CURRENCY,
        salience=NEUTRAL,
        basis="The largest estimate-driven charge in any capital-intensive entity, and the "
              "one a sector-shaped dashboard omits first. Its direction carries no read on "
              "its own: a rise is capital expenditure reaching service, a revised useful "
              "life, an impairment, or a depletion base moving — leads to four different "
              "places, none of them adverse by arithmetic.",
        numerator=("depreciation",),
    ),
    Tile(
        id="H06", label="Finance costs", unit=CURRENCY,
        salience=ADVERSE_UP,
        basis="What the entity's funding costs, stated rather than inferred from the "
              "debt-equity tile. A finance cost moving against a flat borrowing base is the "
              "observation S15 is built on, and the dashboard should not be the last place "
              "a reader learns the charge exists.",
        numerator=("finance_costs",),
    ),
    Tile(
        id="H07", label="Trade receivables", unit=CURRENCY,
        salience=ADVERSE_UP,
        basis="The working-capital line that most often moves against revenue. Declared "
              "adverse-when-rising: this is the tile that shows salience cannot be taken "
              "from the sign, because a rise here is the concern and a rise in revenue two "
              "tiles above it is not.",
        numerator=("trade_receivables",),
    ),
    Tile(
        id="H08", label="Debt-equity", unit=RATIO,
        salience=ADVERSE_UP,
        basis="Leverage in one number, computed from the face of the balance sheet rather "
              "than lifted from the entity's own ratio note, so it means the same thing "
              "across entities that compute it differently.",
        numerator=("long_term_borrowings", "short_term_borrowings"),
        denominator=("total_equity",),
        notes=("Both borrowing legs must be bound in a year for that year to count. A year "
               "in which only one binds would report half the debt as all of it, so the "
               "year is dropped instead — an unbound key is not a zero (P4).",),
    ),
    Tile(
        id="H09", label="Current ratio", unit=RATIO,
        salience=ADVERSE_DOWN,
        basis="Short-term coverage, from the face of the balance sheet. Stated so a reader "
              "can see for themselves whether solvency is a theme worth audit effort "
              "before the cluster list argues it either way.",
        numerator=("total_current_assets",),
        denominator=("total_current_liabilities",),
        # Division III balance sheets are unclassified — there is no current / non-current
        # split to divide. That is a concept the framework does not use, not a figure that
        # could not be found (P8, ratio design §7.5).
        not_applicable_frameworks=frozenset({"sch3_div3"}),
    ),
    Tile(
        id="H10", label="Effective tax rate", unit=PERCENT,
        salience=NEUTRAL,
        basis="Tax expense over profit before tax. Neither direction is adverse: the rate "
              "moves with deferred-tax recognition, exempt income and prior-year "
              "adjustments as readily as with anything of audit interest, so the tile "
              "reports the level and the movement and interprets neither.",
        numerator=("total_tax",),
        denominator=("pbt",),
    ),
)


# ===================================================================================
# Sector overlays — gated on the business model, exactly as `derivations.overlays_for`
# gates a modified derivation. These are the figures that dominate a particular model's
# balance sheet and would mislead by their absence from ITS dashboard.
#
# All three are declared and none is bindable today: no LineSpec in `fs_db/binding.py`
# reaches them, and the contingent-claims read is note-level, which the fact layer does
# not extract at all. They are declared anyway rather than left out, because a declared
# gap is a queryable work item (`work_items()`) and an undeclared one is silence — the
# same reason `derivations.py` keeps `cwip` and `ocf_before_wc` in its input lists.
# ===================================================================================

_OVERLAY: tuple[Tile, ...] = (
    Tile(
        id="H51", label="Oil and gas assets, tangible", unit=CURRENCY,
        salience=NEUTRAL,
        basis="Under successful-efforts accounting the producing asset base is depleted on "
              "a unit-of-production basis against reserves, which makes it the carrying "
              "amount the whole estimate complex rests on. Generic net fixed assets does "
              "not separate it out.",
        numerator=("oil_and_gas_assets",),
        business_models=frozenset({"petroleum", "mining"}),
        binder_required="No LineSpec binds a separate oil-and-gas producing-asset line; the "
                        "figure currently sits inside net_fixed_assets. A balance-sheet "
                        "binder in fs_db/binding.py is required before this tile computes.",
    ),
    Tile(
        id="H52", label="Non-current provisions", unit=CURRENCY,
        salience=ADVERSE_UP,
        basis="Decommissioning, site restoration and mine closure are long-dated and "
              "discount-rate sensitive, and for these models they are among the largest "
              "liabilities on the balance sheet.",
        numerator=("provisions_non_current",),
        business_models=frozenset({"petroleum", "mining", "power_utilities",
                                   "infrastructure_epc"}),
        binder_required="No LineSpec binds non-current provisions; the line is absorbed "
                        "into total_non_current_liabilities. A balance-sheet binder in "
                        "fs_db/binding.py is required before this tile computes.",
    ),
    Tile(
        id="H53", label="Contingent claims not provided for", unit=CURRENCY,
        salience=ADVERSE_UP,
        basis="Material by nature and by public interest even when correctly disclosed "
              "rather than provided. For these models the arbitration and claims register "
              "routinely exceeds several years of profit.",
        numerator=("contingent_liabilities",),
        business_models=frozenset({"petroleum", "mining", "power_utilities",
                                   "infrastructure_epc", "ports_airports", "telecom"}),
        binder_required="Note-level. The figure exists only in the contingent-liability "
                        "note, which the fact layer does not extract; derivations.py models "
                        "a note_inputs path and nothing populates it.",
    ),
)


TILES: tuple[Tile, ...] = _CORE + _OVERLAY
BY_ID: dict[str, Tile] = {t.id: t for t in TILES}
CORE_TILES: tuple[Tile, ...] = _CORE
OVERLAY_TILES: tuple[Tile, ...] = _OVERLAY


def tiles_for(business_model: str | None) -> tuple[Tile, ...]:
    """The core dashboard, plus any overlay tile this business model calls for.

    Mirrors `derivations.overlays_for`. With no model formed the core is still the right
    answer, because not one of the ten depends on knowing what the entity does. What the
    business model changes is INTERPRETATION, and §2.1 keeps that out of this module.
    """
    return _CORE + tuple(t for t in _OVERLAY
                         if business_model and business_model in t.business_models)


# ===================================================================================
# The computed tile.
# ===================================================================================

@dataclass(frozen=True)
class TileValue:
    """One tile, computed — or not, carrying its own reason why not."""
    tile_id: str
    label: str
    unit: str
    state: str
    year: str = ""                      # the year the value is measured at
    value: float | None = None          # INR lakh for CURRENCY; a fraction for PERCENT
    delta: float | None = None          # relative movement, or points for a rate
    delta_kind: str = RELATIVE
    comparison_year: str = ""           # the year the movement is measured AGAINST
    salience: str = NEUTRAL
    attention: bool = False             # adverse-direction movement, past the band
    direction: str = ""                 # "rose" | "fell" | "unchanged" | ""
    context_value: float | None = None  # the declared second read (e.g. cover multiple)
    context_label: str = ""
    series_years: int = 0               # consecutive years standing behind the figure
    reason: str = ""                    # mandatory whenever the tile did not compute
    formula: str = ""
    basis: str = ""
    core: bool = True
    # The trust that travelled with the inputs. NOT a confidence rating: a tile is a
    # figure, not a diagnostic, and inventing a diagnostic confidence for a raw figure
    # would put an assessment in the report that nobody ever made (§4.4).
    inputs_verified: bool = True        # every input satisfied its statement's identity
    scale_read: bool = True             # every input's scale was read, not inferred
    trust_note: str = ""

    def __post_init__(self) -> None:
        if self.state not in COMPUTED_STATES and not self.reason:
            raise ValueError(f"{self.tile_id}: state {self.state} requires a reason (§4.4)")
        if self.state in COMPUTED_STATES and self.value is None:
            raise ValueError(f"{self.tile_id}: state {self.state} carries no value")
        if self.delta is not None and not self.comparison_year:
            raise ValueError(
                f"{self.tile_id}: a movement with no comparison year is a two-point "
                f"movement dressed as a trend, which §9.5 forbids by name.")

    @property
    def computed(self) -> bool:
        return self.state in COMPUTED_STATES

    @property
    def movement_label(self) -> str:
        """The movement, ALWAYS carrying the year it is measured against.

        This is the §9.5 guarantee at the presentation layer. A delta rendered without its
        comparison year is a two-point movement dressed as a trend, and the only reliable
        way to stop that happening is for the formatter to be incapable of producing one.
        """
        if self.delta is None or not self.comparison_year:
            return ""
        if self.delta == 0:
            # "+0.00%" invites a reader to look for the movement it is reporting. There
            # isn't one, and saying so is shorter and truer than printing a signed zero.
            return f"unchanged vs {self.comparison_year}"
        suffix = "pp" if self.delta_kind == POINTS else "%"
        return f"{self.delta * 100:+.2f}{suffix} vs {self.comparison_year}"

    def display(self, scale: str = "lakh") -> str:
        return format_value(self.value, self.unit, scale)

    def context_display(self) -> str:
        if self.context_value is None:
            return ""
        return f"{self.context_value:,.2f}x {self.context_label}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id, "label": self.label, "unit": self.unit,
            "state": self.state, "year": self.year, "value": self.value,
            "value_unit": "INR lakh" if self.unit == CURRENCY else self.unit,
            "delta": self.delta, "delta_kind": self.delta_kind,
            "comparison_year": self.comparison_year,
            "movement_label": self.movement_label,
            "salience": self.salience, "attention": self.attention,
            "direction": self.direction,
            "context_value": self.context_value, "context_label": self.context_label,
            "series_years": self.series_years, "reason": self.reason,
            "formula": self.formula, "basis": self.basis, "core": self.core,
            "inputs_verified": self.inputs_verified, "scale_read": self.scale_read,
            "trust_note": self.trust_note,
        }


# ---- presentation ------------------------------------------------------------------
# The panel normalises every figure to INR lakh, so that is what the arithmetic is in and
# what `value` carries. An entity that presents in millions or crore is a display choice
# and is applied here, where it cannot reach a computation.
LAKH, MILLION, CRORE = "lakh", "million", "crore"
SCALES: dict[str, float] = {LAKH: 1.0, MILLION: 0.1, CRORE: 0.01}


def format_value(value: float | None, unit: str, scale: str = LAKH) -> str:
    if value is None:
        return "—"
    if unit == CURRENCY:
        try:
            factor = SCALES[scale]
        except KeyError:
            raise KeyError(f"unknown presentation scale {scale!r}; "
                           f"known: {', '.join(sorted(SCALES))}") from None
        return f"{value * factor:,.0f}"
    if unit == PERCENT:
        return f"{value * 100:.2f}%"
    if unit == TIMES:
        return f"{value:,.2f}x"
    return f"{value:,.2f}"


# ---- computation -------------------------------------------------------------------

def _sum_series(panel: Panel, keys: tuple[str, ...]) -> list[tuple[str, float]] | None:
    """One contiguous series that is the sum of several keys, over the years all cover.

    Deliberately a local implementation of the same intersection rule `rules._sum_series`
    applies, rather than an import: the signal rules are a pure function of the panel and
    must not acquire a dependency on the dashboard, and the dashboard must not reach into
    a rule's private helper. The rule itself is one sentence and holds in both places —
    take only the years every key covers, and never treat an unbound key as zero (P4).
    """
    if not keys:
        return None
    parts: list[list[tuple[str, float]]] = []
    for k in keys:
        s = panel.series(k)
        if s is None:
            return None
        parts.append(s)
    maps = [dict(p) for p in parts]
    common: set[str] = set(maps[0])
    for m in maps[1:]:
        common &= set(m)
    out = [(y, sum(m[y] for m in maps)) for y, _ in parts[0] if y in common]
    return out or None


def _combine(num: list[tuple[str, float]],
             den: list[tuple[str, float]] | None) -> list[tuple[str, float]] | None:
    """Numerator over denominator, year by year, over the years both cover.

    A year whose denominator is zero is DROPPED rather than carried as an infinity or a
    zero: the ratio is undefined there, and an undefined value is not a measurement.
    """
    if den is None:
        return num
    dm = dict(den)
    out = [(y, v / dm[y]) for y, v in num if y in dm and dm[y] != 0]
    return out or None


def _trust(panel: Panel, keys: tuple[str, ...], years: tuple[str, ...]
           ) -> tuple[bool, bool, str]:
    """What travelled with the inputs — verification and scale, reported, never scored."""
    cells = [c for c in (panel.cell(k, y) for k in keys for y in years) if c is not None]
    if not cells:
        return False, False, "No input carried a trust record."
    unverified = [c for c in cells if c.verify_verdict != "CONFIRMED"]
    inferred = [c for c in cells if c.unit_confidence != "HIGH"]
    if not unverified and not inferred:
        return True, True, ""
    parts = []
    if unverified:
        parts.append(f"{len(unverified)} of {len(cells)} input figure(s) came from a "
                     f"statement that did not satisfy its own arithmetic identity")
    if inferred:
        parts.append(f"{len(inferred)} of {len(cells)} carried a scale inferred from the "
                     f"filing rather than read from the statement")
    return not unverified, not inferred, "; ".join(parts) + "."


def _unbound(tile: Tile, state: str, reason: str) -> TileValue:
    return TileValue(tile_id=tile.id, label=tile.label, unit=tile.unit, state=state,
                     salience=tile.salience, delta_kind=tile.delta_kind, reason=reason,
                     formula=tile.formula, basis=tile.basis, core=tile.is_core,
                     context_label=tile.context_label,
                     inputs_verified=False, scale_read=False)


def _blocked(panel: Panel, tile: Tile) -> TileValue | None:
    """Why this tile has no current figure, in the panel's own vocabulary, or None."""
    diag = panel.diagnose(tile.required_keys, 1)
    bad = {k: d for k, d in diag.items() if d["state"] != Panel.OK}
    if not bad:
        return None

    # Report the most actionable state present, in the same priority order `rules._needs`
    # uses — a key that binds nowhere is binder work, a key with a hole in one year is one
    # filing's extraction, and those are different jobs for different people.
    order = (Panel.NEVER_BOUND, Panel.ABSENT_FROM_LATEST, Panel.SCALE_UNRECONCILED,
             Panel.BROKEN_BY_GAP, Panel.TOO_FEW_YEARS)
    state = next(s for s in order if any(d["state"] == s for d in bad.values()))
    names = ", ".join(sorted(k for k, d in bad.items() if d["state"] == state))

    if state == Panel.NEVER_BOUND:
        why = (f"{names} could not be bound in any year of the panel. This is a fact-layer "
               f"gap, not a disclosure gap.")
        if tile.binder_required:
            why += f" {tile.binder_required}"
    elif state == Panel.ABSENT_FROM_LATEST:
        why = (f"{names} is not bound in the latest year ({panel.latest}). A figure from an "
               f"earlier filing is not a statement about this one, so none is shown.")
    elif state == Panel.SCALE_UNRECONCILED:
        why = (f"{names} carries a guessed scale that contradicts the rest of the window, "
               f"so the figure was refused rather than risk a hundred-fold movement "
               f"manufactured out of a unit error.")
    else:
        why = f"{names} is not available for the latest year of the panel."
    return _unbound(tile, state, why)


def compute_tile(tile: Tile, panel: Panel | None, *,
                 framework: str | None = None) -> TileValue:
    """One tile against one panel. Never raises; an inability to compute is a state.

    Order matters and is the same as `evaluate_signal`'s: applicability first, because a
    concept the framework does not have is not a missing figure; then whether the fact
    layer can reach it at all; then the arithmetic.
    """
    if panel is None:
        return _unbound(tile, NO_PANEL,
                        "No entity-year panel was built, so no figure could be read.")

    if framework and framework in tile.not_applicable_frameworks:
        return _unbound(
            tile, NOT_APPLICABLE,
            f"The entity reports under {framework}, whose balance-sheet format does not use "
            f"the classification this figure divides. A concept the framework does not have "
            f"is not a figure that could not be found.")

    if tile.binder_required:
        return _unbound(tile, NEEDS_BINDER, tile.binder_required)

    blocked = _blocked(panel, tile)
    if blocked is not None:
        return blocked

    num = _sum_series(panel, tile.numerator)
    den = _sum_series(panel, tile.denominator) if tile.denominator else None
    if num is None or (tile.denominator and den is None):
        return _unbound(tile, NOT_COMPUTABLE,
                        f"{tile.formula} could not be formed: its inputs are bound, but not "
                        f"in a year they all cover.")

    series = _combine(num, den)
    if not series:
        return _unbound(tile, NOT_COMPUTABLE,
                        f"{tile.formula} is undefined in every year the panel covers — the "
                        f"denominator is zero. An undefined value is not a measurement.")

    year, value = series[-1]
    if year != panel.latest:
        return _unbound(tile, ABSENT_FROM_LATEST,
                        f"The most recent year this figure can be formed for is {year}, not "
                        f"the latest filing year ({panel.latest}). A figure from an earlier "
                        f"filing is not a statement about the current one.")

    verified, scale_ok, trust_note = _trust(panel, tile.required_keys, (year,))

    context = None
    if tile.context_over:
        ctx = _sum_series(panel, tile.context_over)
        base = dict(ctx).get(year) if ctx else None
        if base:                              # a zero base makes the multiple meaningless
            context = value / base

    common = dict(
        tile_id=tile.id, label=tile.label, unit=tile.unit, year=year, value=value,
        salience=tile.salience, delta_kind=tile.delta_kind, formula=tile.formula,
        basis=tile.basis, core=tile.is_core, series_years=len(series),
        context_value=context, context_label=tile.context_label,
        inputs_verified=verified, scale_read=scale_ok, trust_note=trust_note,
    )

    if len(series) < 2:
        return TileValue(
            state=VALUE_ONLY,
            reason=(f"The figure is bound for {year}, but no comparable prior year exists "
                    f"in an unbroken run ending at it, so no movement is shown. A movement "
                    f"measured against a year that is not adjacent is not a movement."),
            **common)

    prior_year, prior = series[-2]
    if prior == 0:
        return TileValue(
            state=VALUE_ONLY,
            reason=(f"The {prior_year} figure is zero, so a movement against it is "
                    f"undefined rather than large. The level is shown; the movement is not."),
            **common)

    delta = (value - prior) / abs(prior) if tile.delta_kind == RELATIVE else value - prior
    band = tile.attention or (ATTENTION_POINTS if tile.delta_kind == POINTS
                              else ATTENTION_RELATIVE)
    adverse = ((tile.salience == ADVERSE_UP and delta > 0)
               or (tile.salience == ADVERSE_DOWN and delta < 0))

    return TileValue(
        state=OK, delta=delta, comparison_year=prior_year,
        direction="rose" if delta > 0 else "fell" if delta < 0 else "unchanged",
        attention=bool(adverse and abs(delta) >= band),
        **common)


def compute(panel: Panel | None, *, business_model: str | None = None,
            framework: str | None = None) -> tuple[TileValue, ...]:
    """The whole dashboard for one entity. Order is the registry's, never the figures'.

    Sorting by size or by movement would make the dashboard's shape depend on the entity,
    and a reviewer comparing two reports would be reading two different dashboards. The
    registry order is fixed, so the third tile is always operating cash flow.
    """
    return tuple(compute_tile(t, panel, framework=framework)
                 for t in tiles_for(business_model))


# ---- introspection -----------------------------------------------------------------

def manifest() -> dict[str, Any]:
    """What travels on the run manifest (§18.3 reproducibility).

    A change to a tile changes the first screen of every report for every entity, so the
    run records which registry produced it — exactly as it records the derivation and
    threshold sets.
    """
    return {"version": VERSION, "core": len(_CORE), "overlays": len(_OVERLAY),
            "attention_relative": ATTENTION_RELATIVE,
            "attention_points": ATTENTION_POINTS,
            "needs_binder": sorted(t.id for t in TILES if t.binder_required)}


def work_items() -> tuple[tuple[str, str, str], ...]:
    """Tiles declared and not yet bindable, as (id, label, what is required).

    Enumerable rather than narrated, for the same reason `mandatory_schedules()` is: the
    one question worth asking across the corpus — what would buy the most coverage — is a
    query over this, and is otherwise a reading task over prose.
    """
    return tuple((t.id, t.label, t.binder_required) for t in TILES if t.binder_required)


def keys_required() -> tuple[str, ...]:
    """Every canonical key the dashboard reads. Used by the coverage view and the tests."""
    return tuple(sorted({k for t in TILES for k in t.keys}))


def summary(values: tuple[TileValue, ...]) -> dict[str, Any]:
    """A count of what the dashboard could and could not say, for the tracing layer."""
    return {
        "tiles": len(values),
        "computed": sum(1 for v in values if v.computed),
        "with_movement": sum(1 for v in values if v.delta is not None),
        "attention": [v.tile_id for v in values if v.attention],
        "unbound": {v.tile_id: v.state for v in values if not v.computed},
    }


# ---- import-time validation --------------------------------------------------------

def _validate() -> None:
    from . import signals as SG

    seen: set[str] = set()
    for t in TILES:
        if t.id in seen:
            raise ValueError(f"duplicate tile id {t.id}")
        seen.add(t.id)
        if t.unit not in UNITS:
            raise ValueError(f"{t.id}: unknown unit {t.unit!r}")
        if t.salience not in SALIENCE:
            raise ValueError(
                f"{t.id}: unknown salience {t.salience!r}. Every tile must declare which "
                f"direction is adverse for it, or declare that neither is — the renderer "
                f"must never be left to infer it from the sign.")
        if not t.numerator:
            raise ValueError(f"{t.id}: a tile with no numerator computes nothing")
        if not t.basis:
            raise ValueError(
                f"{t.id}: no basis. A figure on the first screen with no stated reason for "
                f"being there is a figure nobody can argue with.")
        if t.context_over and not t.context_label:
            raise ValueError(f"{t.id}: a context read must say what it is a multiple of")
        if set(t.numerator) & set(t.denominator):
            raise ValueError(f"{t.id}: a key on both sides of the ratio cancels itself")
        if t.attention < 0:
            raise ValueError(f"{t.id}: a negative attention band would mark everything")
        for m in sorted(t.business_models):
            if m not in SG.BUSINESS_MODELS:
                raise ValueError(f"{t.id}: unknown business model {m!r}")
        for fw in sorted(t.not_applicable_frameworks):
            if fw not in SG.FRAMEWORKS:
                raise ValueError(f"{t.id}: unknown framework {fw!r}")


_validate()
