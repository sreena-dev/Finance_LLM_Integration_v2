"""
The Input Quality Grade (§4.3) — how far this dataset can be leaned on.

WHAT IT GRADES, AND WHAT IT DOES NOT
------------------------------------
It grades the DATASET, never the entity. An E is a statement about extraction, filings and
arithmetic integrity; it says nothing about how the company is run. Nothing here reads a
figure's VALUE, so no movement, ratio or balance can influence the letter — the grade must
not become a diagnostic wearing a letter's clothes.

THE RULE THAT DECIDES WHAT IS ELIGIBLE
--------------------------------------
GRADE ONLY WHAT VARIES BETWEEN ENTITIES.

Note-level extraction is not built, so eight signals are unavailable for every entity in
the corpus identically. Folding that into the grade would hand all 346 entities the same
letter, and a letter every entity shares carries no information — it would cost confidence
everywhere while discriminating nowhere. Constant limitations belong in the §4.4 validation
note, which already states them, and in block 9, which lists what each blocked diagnostic
needs. What lands here is only what differs per entity: what was read, what was confirmed,
what had to be inferred, and how much history stands behind it.

WHY THE WORST COMPONENT WINS, AND NOTHING IS AVERAGED
-----------------------------------------------------
A weighted average lets five sound dimensions outvote one broken one: a clean five-year
panel would absorb 18 figures that failed their statement identity and still print A. That
is the same netting error S16's derivation refuses — one provision class reversed while
another is charged nets to nothing, and nothing is not nothing.

So every component issues a CEILING and the grade is the worst ceiling any of them issued.
One broken thing cannot be outvoted. It also makes the grade explainable in a sentence —
"C, because one filing failed to read" — rather than requiring a reader to reconstruct a
weighted sum before they can challenge it.

WHY THE BANDS LIVE HERE AND NOT IN `thresholds.py`
--------------------------------------------------
That module scopes itself, in its own docstring, to numbers that decide whether a
DIAGNOSTIC FIRES. A grade band decides no such thing. Mixing the two would make the firing
table unreadable for the audit reviewer it exists for. The bands below carry the same
discipline — declared, sourced, versioned — in their own table.

WHAT THE GRADE IS ALLOWED TO DO
-------------------------------
Exactly one thing: cap diagnostic confidence (§4.3 — "a low grade caps the confidence of
every diagnostic"). It never changes whether a signal fires. §4.4 keeps confidence and
severity independent, so a D-grade entity with a genuine breach still raises its cluster —
reported as important if true, not yet dependable.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any

from .model import HIGH, MEDIUM, LOW

VERSION = "fdr-grading-1.0.0"

# ---- the ladder --------------------------------------------------------------------
A, B, C, D, E = "A", "B", "C", "D", "E"
GRADES = (A, B, C, D, E)
_ORDER = {g: i for i, g in enumerate(GRADES)}      # lower index is better

# What each letter permits a diagnostic to claim. E does not cap confidence — it withdraws
# reliance altogether, which is a different statement and is rendered as one.
CEILING: dict[str, str | None] = {A: HIGH, B: HIGH, C: MEDIUM, D: LOW, E: None}

MEANING: dict[str, str] = {
    A: "Complete and confirmed. Every figure served was read at a stated scale and agreed "
       "with its statement's own identity.",
    B: "Usable with minor qualification. Nothing material is missing; something was "
       "inferred rather than read, or a component of the package was not bound.",
    C: "Material gaps a reviewer must weigh before leaning on any diagnostic. The figures "
       "that survived are still figures; there are fewer of them than there should be, or "
       "they rest on a shorter history.",
    D: "Indicative only. Enough of the dataset failed, or is absent, that a diagnostic "
       "resting on it cannot carry more than LOW confidence.",
    E: "Not usable. No diagnostic in this run may be relied on for any purpose.",
}

SPEC = "SPEC"
PROPOSED = "PROPOSED"


@dataclass(frozen=True)
class Band:
    """One cut in one component's ladder. Same discipline as `thresholds.Threshold`."""
    key: str
    value: float
    unit: str
    basis: str
    origin: str = PROPOSED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_BANDS: tuple[Band, ...] = (
    # ---- integrity: the share of extracted figures that survived the trust gate -----
    Band("integrity_a", 0.98, "share",
         "At or above 98% confirmed, with nothing contradicted, the dataset is whole: the "
         "residue is figures no identity check happened to cover, not figures that failed "
         "one. §4.3 grades arithmetic consistency and cross-statement agreement."),
    Band("integrity_b", 0.90, "share",
         "Nine in ten figures confirmed is a sound dataset with a visible edge. It is the "
         "point at which the exceptions are still few enough to be listed and chased "
         "individually rather than described statistically."),
    Band("integrity_c", 0.75, "share",
         "Below three quarters confirmed, the unconfirmed residue is no longer an edge "
         "case; a diagnostic drawing several inputs is then more likely than not to touch "
         "one. §4.3 bars a high-confidence conclusion resting on a failed check."),

    # ---- extraction: figures whose SCALE was inferred rather than read --------------
    Band("inferred_scale_b", 0.02, "share",
         "A scale inferred from the filing's modal banner rather than read from the "
         "statement is usually right and occasionally wrong by a factor of a thousand. Up "
         "to one figure in fifty is a qualification; beyond that it is a condition."),
    Band("inferred_scale_c", 0.10, "share",
         "Above a tenth of figures carrying an inferred scale, the risk is no longer "
         "isolated to a figure — it is a property of the read, and §4.3 requires low "
         "extraction confidence to be disclosed and to lower what rests on it."),

    # ---- panel: comparable years standing behind every trend diagnostic ------------
    Band("panel_years_a", 5.0, "years",
         "Five comparable years is the window the trend rules are written against, and it "
         "leaves headroom for one bad filing without falling under §9.5.", SPEC),
    Band("panel_years_b", 4.0, "years",
         "Four years supports every trend diagnostic with one year of headroom."),
    Band("panel_years_min", 3.0, "years",
         "§9.5 requires three comparable years before any trend is presented, and forbids "
         "presenting a two-point movement as a trend. AT the minimum is not a defect, but "
         "it has no headroom: one missing filing takes the entity below the floor.", SPEC),
)

BAND: dict[str, float] = {b.key: b.value for b in _BANDS}
BANDS: tuple[Band, ...] = _BANDS


# ---- one graded dimension ----------------------------------------------------------

@dataclass
class Component:
    """One §4.3 measure, the ceiling it issues, and the figures behind it.

    `detail` is not decoration. A grade a reader cannot act on is a grade they can only
    accept or ignore, so every component states the count that produced it and, where it
    cost something, what it would take to lift it.
    """
    name: str
    measure: str            # the §4.3 row this implements
    ceiling: str            # A-E
    detail: str
    figures: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Grade:
    """The letter, why it is that letter, and the one thing it is permitted to do."""
    letter: str
    confidence_ceiling: str | None
    components: tuple[Component, ...]
    binding: tuple[str, ...]        # which component(s) set the letter
    basis: str
    version: str = VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        d["meaning"] = MEANING[self.letter]
        return d


def _worst(letters: list[str]) -> str:
    return max(letters, key=lambda g: _ORDER[g])


def cap(confidence: str | None, letter: str) -> str | None:
    """Lower a measured confidence to what the grade permits. Never raises one.

    Mirrors `rules._cap` deliberately: a cap can only ever cost. A signal that already
    capped itself for a proxy is not capped twice for the same reason — this is a `min`
    against the grade's ceiling, not a second deduction.
    """
    if confidence is None:
        return None
    ceiling = CEILING[letter]
    if ceiling is None:
        return None
    order = {LOW: 0, MEDIUM: 1, HIGH: 2}
    return confidence if order[confidence] <= order[ceiling] else ceiling


# ---- the components ----------------------------------------------------------------

def _integrity(cov: dict[str, Any]) -> Component:
    total = int(cov.get("facts_total") or 0)
    served = int(cov.get("facts_trusted") or 0)
    contra = int(cov.get("withheld_contradicted") or 0)
    figs = {"confirmed": served, "extracted": total, "contradicted": contra}

    if total == 0:
        return Component("Data integrity", "Data Integrity Score", E,
                         "No figure was extracted, so no arithmetic identity could be run. "
                         "There is nothing to grade.", figs)

    share = served / total
    if contra == 0 and share >= BAND["integrity_a"]:
        return Component("Data integrity", "Data Integrity Score", A,
                         f"{served} of {total} figures confirmed against their statement's "
                         f"own identity; none contradicted.", figs)
    if share >= BAND["integrity_b"]:
        return Component("Data integrity", "Data Integrity Score", B,
                         f"{served} of {total} figures confirmed; {contra} withheld as "
                         f"CONTRADICTED — the statement they came from failed its identity "
                         f"check, so every figure from it was withheld rather than served "
                         f"with a caveat.", figs)
    if share >= BAND["integrity_c"]:
        return Component("Data integrity", "Data Integrity Score", C,
                         f"Only {served} of {total} figures ({share:.0%}) survived the "
                         f"trust gate; {contra} contradicted. A diagnostic drawing several "
                         f"inputs is likely to touch one that did not.", figs)
    return Component("Data integrity", "Data Integrity Score", D,
                     f"{served} of {total} figures ({share:.0%}) confirmed. Most of this "
                     f"dataset did not survive its own arithmetic.", figs)


def _extraction(cov: dict[str, Any]) -> Component:
    total = int(cov.get("facts_total") or 0)
    inferred = int(cov.get("unit_promotions") or 0)
    dropped = int(cov.get("withheld_no_scale") or 0)
    figs = {"scale_inferred": inferred, "withheld_no_scale": dropped, "extracted": total}

    if total == 0:
        return Component("Extraction", "Extraction confidence", E,
                         "Nothing was extracted.", figs)
    if dropped:
        return Component("Extraction", "Extraction confidence", C,
                         f"{dropped} figure(s) were withheld outright because no scale "
                         f"could be resolved for them. A figure whose magnitude is unknown "
                         f"is not a figure, so it was dropped rather than guessed.", figs)
    share = inferred / total
    if inferred == 0:
        return Component("Extraction", "Extraction confidence", A,
                         "Every figure carried a scale read from its own statement.", figs)
    if share <= BAND["inferred_scale_b"]:
        return Component("Extraction", "Extraction confidence", B,
                         f"{inferred} of {total} figures carried a scale inferred from the "
                         f"filing's modal banner rather than read from the statement they "
                         f"sit on.", figs)
    if share <= BAND["inferred_scale_c"]:
        return Component("Extraction", "Extraction confidence", C,
                         f"{inferred} of {total} figures ({share:.0%}) carried an inferred "
                         f"scale. That is no longer isolated to a figure.", figs)
    return Component("Extraction", "Extraction confidence", D,
                     f"{inferred} of {total} figures ({share:.0%}) carried an inferred "
                     f"scale. The magnitude of this dataset rests on inference.", figs)


_CORE = ("balance_sheet", "profit_loss", "cash_flow")
_CORE_NAME = {"balance_sheet": "Balance Sheet",
              "profit_loss": "Statement of Profit and Loss",
              "cash_flow": "Cash Flow Statement"}


def _completeness(cov: dict[str, Any]) -> Component:
    got = set(cov.get("statements") or ())
    missing_core = [s for s in _CORE if s not in got]
    soce = "statement_of_equity" in got
    figs = {"received": sorted(got), "missing_core": missing_core, "soce": soce}

    if len(missing_core) == len(_CORE):
        return Component("Completeness", "Financial-statement completeness", E,
                         "None of the three primary statements was bound.", figs)
    if missing_core:
        names = ", ".join(_CORE_NAME[s] for s in missing_core)
        return Component("Completeness", "Financial-statement completeness", D,
                         f"Not bound: {names}. §4.4 — every diagnostic reading that "
                         f"statement is unavailable, not merely weakened.", figs)
    if not soce:
        return Component("Completeness", "Financial-statement completeness", B,
                         "Balance Sheet, Statement of Profit and Loss and Cash Flow "
                         "Statement all bound; the Statement of Changes in Equity was not. "
                         "No signal in the contract reads it, so nothing is blocked — but "
                         "the package is short of what §4.3 counts as complete.", figs)
    return Component("Completeness", "Financial-statement completeness", A,
                     "All four primary statements bound.", figs)


def _panel_depth(cov: dict[str, Any], years: int) -> Component:
    failed = int(cov.get("filings_failed") or 0)
    figs = {"comparable_years": years, "filings_failed": failed}

    if years == 0:
        return Component("Panel depth", "Financial-statement completeness", E,
                         "No comparable year was formed; there is no panel to grade.", figs)
    if years < BAND["panel_years_min"]:
        return Component("Panel depth", "Financial-statement completeness", D,
                         f"{years} comparable year(s). §9.5 requires three before any trend "
                         f"may be presented, so every trend diagnostic is unavailable "
                         f"regardless of what else is sound.", figs)

    if years >= BAND["panel_years_a"]:
        ceiling, why = A, (f"{years} consecutive comparable years — the full window the "
                           f"rules are written against.")
    elif years >= BAND["panel_years_b"]:
        ceiling, why = B, (f"{years} consecutive comparable years; one year of headroom "
                           f"above the §9.5 floor.")
    else:
        ceiling, why = C, (f"{years} comparable years — exactly the §9.5 floor, with no "
                           f"headroom. One missing filing would take this entity below it.")

    if failed:
        return Component("Panel depth", "Financial-statement completeness",
                         _worst([ceiling, C]),
                         f"{why} {failed} filing(s) were found but could not be read, so "
                         f"the window is shorter than the corpus could support.", figs)
    return Component("Panel depth", "Financial-statement completeness", ceiling, why, figs)


def _framework(model: str | None, confidence: str | None) -> Component:
    figs = {"business_model": model, "model_confidence": confidence}
    measure = "Framework and entity-type confidence"
    if not model:
        return Component("Framework and entity type", measure, C,
                         "No business model was formed. §2.1 already withholds "
                         "interpretation on this ground; the grade records it as a property "
                         "of the inputs as well, because without one a diagnostic cannot be "
                         "told apart from a normal feature of the entity.", figs)
    if confidence == HIGH:
        return Component("Framework and entity type", measure, A,
                         f"Classified as {model} with HIGH confidence.", figs)
    if confidence == MEDIUM:
        return Component("Framework and entity type", measure, B,
                         f"Classified as {model}, MEDIUM confidence. A misread framework "
                         f"invalidates format-specific reads (§4.3).", figs)
    return Component("Framework and entity type", measure, C,
                     f"Classified as {model}, but only at "
                     f"{confidence or 'unstated'} confidence.", figs)


# ---- the grade ---------------------------------------------------------------------

def grade(coverage: dict[str, Any] | None, *, comparable_years: int = 0,
          business_model: str | None = None,
          model_confidence: str | None = None) -> Grade:
    """Grade a run's inputs. Pure: a dict in, a letter out, no I/O and no figure read.

    `coverage` is `source.SourceReport.to_dict()`. It is taken as a plain mapping rather
    than the dataclass so this module stays independent of how the facts were fetched —
    the same reason `fdr` does not reach into `fs_db` anywhere else.
    """
    if not coverage:
        comps = (Component("Inputs", "Input Quality Grade", E,
                           "No coverage was recorded for this run, so the inputs cannot be "
                           "graded. This is a fact about the run, not about the entity.",
                           {}),)
        return Grade(E, CEILING[E], comps, ("Inputs",),
                     "NOT ASSESSED — no coverage was recorded for this run, so the inputs "
                     "cannot be graded. No diagnostic may be relied on.")

    comps = (
        _integrity(coverage),
        _extraction(coverage),
        _completeness(coverage),
        _panel_depth(coverage, comparable_years),
        _framework(business_model, model_confidence),
    )
    letter = _worst([c.ceiling for c in comps])
    binding = tuple(c.name for c in comps if c.ceiling == letter)

    ceiling = CEILING[letter]
    effect = (f"Diagnostic confidence is capped at {ceiling}." if ceiling
              else "No diagnostic in this run may be relied on.")
    basis = (f"{MEANING[letter]} Set by: {', '.join(binding)} — the grade is the WORST of "
             f"its components, never their average, so one failed dimension cannot be "
             f"outvoted by the rest. {effect}")

    return Grade(letter, ceiling, comps, binding, basis)


def manifest() -> dict[str, Any]:
    """What travels on the run manifest (§18.3) — a band change re-grades every entity."""
    return {"version": VERSION,
            "bands": {b.key: {"value": b.value, "unit": b.unit, "origin": b.origin}
                      for b in _BANDS},
            "ceilings": {g: CEILING[g] for g in GRADES}}
