"""
Sector theme overlays — reading an Appendix D cluster in the entity's own vocabulary.

THE PROBLEM THIS SOLVES
-----------------------
Appendix D names six clusters in deliberately general terms: "Asset and capitalisation
risk", "Estimate and reporting-quality risk". That generality is correct for a registry
that must cover every entity in the corpus, and it is wrong on a matrix row an audit team
works from. For an exploration-and-production company, "Asset and capitalisation risk" is
two different audit problems — the depletion and impairment of PRODUCING assets, and the
capitalise-versus-expense boundary on EXPLORATORY ones — and a single row named after the
generic cluster tells the team neither.

So a theme overlay does one of two things to a cluster, for one business model:

  RENAME   the cluster keeps its signals and gets the sector's own name for the risk.
  SPLIT    the cluster's signals are PARTITIONED into two or more themes, each of which
           becomes its own matrix row.

WHY SPLITTING IS LEGITIMATE AND NOT A §10.1 VIOLATION
-----------------------------------------------------
§10.1 requires clustering and de-duplication: "one integrated theme rather than repetitive
findings". A split does not create repetition — it separates signals that rest on DIFFERENT
underlying facts and would be tested by different procedures with different specialists.
Under successful-efforts accounting, a depletion charge tested against reserves produced and
an exploratory well tested against the capitalisation policy share no population, no
evidence request and no specialist. Reporting them as one theme is the failure §10.1 is
guarding against, not the compliance with it.

The partition is what keeps it honest, and it is enforced at import: the themes for a
cluster must cover EVERY one of that cluster's signals exactly once. A signal cannot be
dropped (which would silently narrow the cluster) and cannot appear twice (which would be
the repetition §10.1 forbids).

EVERYTHING HERE IS PROPOSED
---------------------------
The specification names no sector themes. Appendix B and C describe sector characteristics
and §13 describes the sector lens, but the vocabulary below is drafted, not lifted, and
every entry is tagged PROPOSED and listed by `python -m fdr contract --gaps`. A theme
changes how a risk is NAMED and FRAMED for an entire business model, so it is audit-side
authoring exactly as `clusters.yaml` is (ROADMAP M2).

WHAT AN OVERLAY MAY NEVER DO
----------------------------
Change what fires. A theme is vocabulary and framing over signals that a rule already
decided. It cannot add a signal, suppress one, alter a threshold or change a severity —
those live in `signals.py`, `rules.py` and `thresholds.py`, and a sector that needs a
different THRESHOLD needs a suppression or a derivation overlay (`derivations.py`), not a
name. Keeping the two apart is what stops a naming layer from quietly becoming a second,
unreviewed rule engine.
"""
from __future__ import annotations
from dataclasses import dataclass

from . import clusters as CL
from . import signals as SG

SPEC = "SPEC"
PROPOSED = "PROPOSED"


@dataclass(frozen=True)
class SectorTheme:
    id: str
    business_models: frozenset[str]
    cluster_id: str                       # the Appendix D cluster this re-frames
    theme: str                            # the entity-vocabulary name for the risk
    signals: tuple[str, ...]              # the cluster signals THIS theme carries
    framing: str                          # why the cluster reads this way in this sector
    specialist: tuple[str, ...] = ()      # sector-specific referral, added to App F's
    origin: str = PROPOSED

    def covers(self, signal_id: str) -> bool:
        return signal_id in self.signals


# =====================================================================================
# Petroleum / oil and gas.
#
# `OV-OG` in `derivations.py` already establishes WHY these reads differ under
# successful-efforts accounting; these themes are how that difference is NAMED on the
# matrix. The two must stay consistent — the derivation overlay changes the arithmetic,
# the theme overlay changes the words, and neither may do the other's job.
# =====================================================================================

THEMES: tuple[SectorTheme, ...] = (

    SectorTheme(
        id="ST-OG-DEPL",
        business_models=frozenset({"petroleum", "mining"}),
        cluster_id="RC-CAP",
        theme="Reserve-linked depletion and impairment estimate quality",
        signals=("S11", "S12"),
        framing=(
            "Producing assets are depleted on a unit-of-production basis against reserves, "
            "not written down over a disclosed useful life, so the depreciation read (S11) "
            "is a test of the reserve estimate as much as of the charge. Impairment (S12) "
            "rests on the same reserve base and on a price deck. Both are judgement over a "
            "very large carrying amount, and both are tested against the reserves report — "
            "which is why they belong together and apart from the exploratory-well theme."
        ),
        specialist=("reservoir engineer", "valuation specialist"),
    ),

    SectorTheme(
        id="ST-OG-EXPL",
        business_models=frozenset({"petroleum", "mining"}),
        cluster_id="RC-CAP",
        theme="Exploratory-well capitalisation and CWIP ageing",
        signals=("S09", "S10"),
        framing=(
            "Under successful efforts, wells pending determination sit capitalised while "
            "their outcome is unresolved — a capitalise-versus-expense boundary that the "
            "generic CWIP read does not describe. The audit question is the well STATUS and "
            "the timing of write-off, tested against the capitalisation policy and the "
            "ageing schedule, not against a construction programme."
        ),
        specialist=("reservoir engineer",),
    ),

    SectorTheme(
        id="ST-OG-PRICE",
        business_models=frozenset({"petroleum"}),
        cluster_id="RC-DEP",
        theme="Administered-price and government-support dependency",
        signals=("S20", "S21", "S22"),
        framing=(
            "Realisation is set by notified floor and ceiling prices rather than negotiated "
            "with the customer, so the dependency read is on the PRICING MECHANISM and the "
            "entity's exposure to a change in it, not on grant receipts alone. Royalty and "
            "cost-recovery matters in dispute with the Government are part of the same "
            "exposure and carry a regularity lens."
        ),
        specialist=("regulatory",),
    ),

    # ---- power utilities -------------------------------------------------------------
    # The §20 worked example. S05 is already SUPPRESSED for this model in `signals.py`;
    # the theme states what the cluster IS about once the suppressed signal is set aside,
    # so a reader is not left with a theme named after a diagnostic that was not run.
    SectorTheme(
        id="ST-PU-REG",
        business_models=frozenset({"power_utilities"}),
        cluster_id="RC-REC",
        theme="Regulated receivable and tariff-recovery quality",
        signals=("S05", "S06", "S07", "S08"),
        framing=(
            "A tariff-based utility carries regulated receivables and regulatory deferral "
            "balances by design, so the level of receivables is not itself the signal — the "
            "question is recovery against the entity's own established pattern and the "
            "status of the regulatory asset. S05 is suppressed for this model (§13.1, §20) "
            "and the suppression is stated on the row rather than left silent."
        ),
        specialist=("regulatory",),
    ),

    # ---- infrastructure / EPC ---------------------------------------------------------
    # §20's other worked example: three individually mild signals that together exceed any
    # one of them. The theme names what the combination is ABOUT.
    SectorTheme(
        id="ST-EPC-PROJ",
        business_models=frozenset({"infrastructure_epc", "real_estate_construction"}),
        cluster_id="RC-CAP",
        theme="Project cost recovery and capital work ageing",
        signals=("S09", "S10", "S11", "S12"),
        framing=(
            "Long-cycle contract work concentrates the risk in whether capitalised project "
            "cost will be recovered from the contract, so CWIP ageing, the non-current-other "
            "balance and the capitalisation boundary are ONE question about project "
            "recoverability, tested against the project register and completion status."
        ),
        specialist=("engineering",),
    ),
)


# ---- indices -------------------------------------------------------------------------

BY_ID: dict[str, SectorTheme] = {t.id: t for t in THEMES}


def themes_for(business_model: str | None, cluster_id: str) -> tuple[SectorTheme, ...]:
    """The themes that re-frame this cluster for this business model, in declared order."""
    if not business_model:
        return ()
    return tuple(t for t in THEMES
                 if business_model in t.business_models and t.cluster_id == cluster_id)


def theme_for_signals(business_model: str | None, cluster_id: str,
                      signal_ids: frozenset[str]) -> SectorTheme | None:
    """The single theme that covers these fired signals, or None.

    Returns None where the fired signals straddle two themes of a split: a row that spans
    a partition is not one theme, and naming it after either half would misdescribe it.
    The caller then keeps the Appendix D cluster name, which is always a truthful — if
    general — description of the whole cluster.
    """
    hits = [t for t in themes_for(business_model, cluster_id)
            if signal_ids & set(t.signals)]
    return hits[0] if len(hits) == 1 else None


def gaps() -> tuple[str, ...]:
    """Themes still awaiting audit sign-off — everything PROPOSED."""
    return tuple(f"{t.id} ({t.cluster_id} -> {t.theme!r} for "
                 f"{', '.join(sorted(t.business_models))})"
                 for t in THEMES if t.origin != SPEC)


def _validate() -> None:
    seen: set[str] = set()
    for t in THEMES:
        if t.id in seen:
            raise ValueError(f"duplicate theme id {t.id}")
        seen.add(t.id)
        if t.cluster_id not in CL.BY_ID:
            raise ValueError(f"{t.id}: unknown cluster {t.cluster_id}")
        bad = t.business_models - SG.BUSINESS_MODELS
        if bad:
            raise ValueError(f"{t.id}: unknown business model(s) {sorted(bad)}")
        if not t.signals:
            raise ValueError(f"{t.id}: a theme with no signals names nothing")
        unknown = set(t.signals) - set(CL.BY_ID[t.cluster_id].signals)
        if unknown:
            raise ValueError(
                f"{t.id}: signals {sorted(unknown)} are not in {t.cluster_id}. A theme may "
                f"only re-frame signals the cluster already consumes — adding one here would "
                f"change what the system flags without touching the signal registry."
            )
        if not t.framing:
            raise ValueError(f"{t.id}: no framing — a renamed cluster must say why")

    # THE PARTITION INVARIANT. For each (business model, cluster) the declared themes must
    # cover every one of that cluster's signals EXACTLY ONCE. Under-coverage silently drops
    # a diagnostic from the entity's matrix; double-coverage produces the repetitive
    # findings §10.1 exists to prevent.
    for model in SG.BUSINESS_MODELS:
        for cluster in CL.CLUSTERS:
            ts = themes_for(model, cluster.id)
            if not ts:
                continue                     # no overlay: the cluster keeps its own name
            covered: list[str] = [s for t in ts for s in t.signals]
            if len(covered) != len(set(covered)):
                dupes = sorted({s for s in covered if covered.count(s) > 1})
                raise ValueError(
                    f"{model}/{cluster.id}: signal(s) {dupes} appear in more than one theme")
            if set(covered) != set(cluster.signals):
                missing = sorted(set(cluster.signals) - set(covered))
                raise ValueError(
                    f"{model}/{cluster.id}: themes do not partition the cluster — "
                    f"{missing} covered by no theme. Every signal must land in exactly one "
                    f"theme or the entity's matrix silently loses it."
                )


_validate()
