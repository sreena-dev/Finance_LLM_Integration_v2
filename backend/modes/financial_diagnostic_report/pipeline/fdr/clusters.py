"""
FDR Appendix D — the six risk clusters, as machine-readable data.

A cluster is a set of related signals consolidated into ONE coherent audit theme (§10.1).
The FDR does not present a long list of disconnected flags: it clusters, de-duplicates,
and distinguishes a weak single signal from a strong multi-signal cluster.

This module is the requirement specification for Layers 1-4. `_validate()` asserts at
import that:
  - every signal a cluster claims exists in `signals.py`;
  - every signal in `signals.py` is consumed by at least one cluster (no orphan diagnostics);
  - every cluster is REACHABLE — it has at least one FACE signal, so it is not structurally
    dead until note-level extraction lands.

Evidence requests and specialist referrals come from Appendix F. Where Appendix F has no
row for a cluster, the field is left empty and flagged rather than invented — the same
abstain-don't-guess discipline the numeric layers follow (finance-core P3). Appendix F is
described in the spec as representative, not exhaustive, so gaps are expected and are
audit-authoring work, not engineering work.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from . import assertions as A
from . import signals as S
from .signals import SIGNALS, FACE, Signal


@dataclass(frozen=True)
class Cluster:
    id: str
    theme: str                               # Appendix D's own wording
    signals: tuple[str, ...]                 # contributing signal ids
    assertions: frozenset[str]               # App D column 3, mapped to assertions.py
    regularity_matters: frozenset[str] = frozenset()
    evidence: tuple[str, ...] = ()           # Appendix F; () = not covered by App F
    specialist: tuple[str, ...] = ()         # Appendix F; () = none indicated
    control_implications: str = ""           # §10.3
    notes: tuple[str, ...] = ()
    # Non-empty ONLY for a cluster with no face-derivable signal, stating which schedules
    # it needs and why it is still worth declaring. `_validate` demands one rather than
    # letting a structurally unreachable cluster pass silently — see the reachability check.
    note_only_basis: str = ""
    # PROPOSED for the extension clusters; the Appendix D six carry no origin because the
    # specification is their origin.
    origin: str = "SPEC"

    @property
    def needs_audit_authoring(self) -> bool:
        return not self.evidence or not self.control_implications


CLUSTERS: tuple[Cluster, ...] = (

    Cluster(
        id="RC-WC",
        theme="Working-capital and liquidity stress",
        signals=("S01", "S02", "S03", "S04"),
        assertions=frozenset({A.COMPLETENESS, A.VALUATION_AND_ALLOCATION}),
        evidence=(),
        specialist=(),
        control_implications="",
        notes=(
            "Appendix F has NO row for this cluster — the nearest are 'Receivable quality' "
            "and 'Borrowings / solvency', which belong to other clusters. Evidence requests "
            "and control implications are audit-authoring work (ROADMAP M2).",
            "App D assertion column reads 'Completeness of liabilities; valuation' — the "
            "completeness point is specifically about unrecorded liabilities.",
        ),
    ),

    Cluster(
        id="RC-REC",
        theme="Revenue and receivable quality",
        signals=("S05", "S06", "S07", "S08"),
        assertions=frozenset({A.OCCURRENCE, A.CUTOFF, A.VALUATION_AND_ALLOCATION}),
        evidence=(
            "Receivables ageing",
            "Balance confirmations",
            "Subsequent receipts",
            "Provisioning policy",
            "Related-party register, pricing basis, board approvals and confirmations (S08)",
        ),
        specialist=("legal (where balances are disputed)", "tax (related-party pricing)"),
        control_implications="Controls over revenue cut-off, credit approval, ageing review "
                             "and the periodic assessment of recoverability.",
        notes=(
            "App D uses 'Occurrence', which §10.3's assertion list does not name — see "
            "assertions.py for why both are carried.",
            "S05 is SUPPRESSED for power utilities (§20 worked example). A cluster whose only "
            "firing signal is suppressed must not be raised.",
        ),
    ),

    Cluster(
        id="RC-CAP",
        theme="Asset and capitalisation risk",
        signals=("S09", "S10", "S11", "S12"),
        assertions=frozenset({A.EXISTENCE, A.VALUATION_AND_ALLOCATION, A.ACCURACY}),
        evidence=(
            "Project-cost records",
            "Capitalisation policy",
            "Project completion status",
        ),
        specialist=("engineering",),
        control_implications="Controls over capitalisation cut-off, project monitoring, "
                             "periodic impairment assessment and the review of assets not "
                             "yet put to use.",
        notes=(
            "The §20 infrastructure-EPC example is this cluster: three individually mild "
            "signals (S09, S10, S15) that together exceed any one of them. It is the "
            "canonical test of §10.1 clustering.",
        ),
    ),

    Cluster(
        id="RC-FUND",
        theme="Funding and solvency risk",
        signals=("S13", "S14", "S15"),
        assertions=frozenset({A.COMPLETENESS, A.CLASSIFICATION}),
        evidence=(
            "Loan agreements",
            "Covenant status",
            "Repayment and default records",
        ),
        specialist=(),
        control_implications="Controls over borrowing authorisation, covenant monitoring, "
                             "and the classification of current maturities.",
        notes=(
            "S15 (finance cost not moving with borrowings) also feeds RC-CAP through "
            "borrowing-cost capitalisation — a reinforcing interaction under §10.2.",
        ),
    ),

    Cluster(
        id="RC-EST",
        theme="Estimate and reporting-quality risk",
        signals=("S16", "S17", "S18", "S19"),
        assertions=frozenset({A.VALUATION_AND_ALLOCATION, A.ACCURACY}),
        evidence=(
            "The estimate model and its assumptions",
            "Staging / categorisation basis where applicable",
            "Basis for each reversal",
            "Sensitivity analysis",
        ),
        specialist=("valuation", "actuarial"),
        control_implications="Controls over the setting, review and approval of estimates, "
                             "and over changes in accounting estimates and their disclosure.",
        notes=(
            "§9.3 and §17.1 wording discipline is strictest here: these are estimate-quality "
            "risk indicators warranting assumption testing, with NO inference about intent.",
        ),
    ),

    Cluster(
        id="RC-DEP",
        theme="Government-dependency and grant risk",
        signals=("S20", "S21", "S22"),
        assertions=frozenset({A.COMPLETENESS, A.CLASSIFICATION}),
        regularity_matters=frozenset({A.CONDITION_COMPLIANCE}),
        evidence=(
            "Sanction orders",
            "Utilisation certificates",
            "Grant-condition compliance records",
        ),
        specialist=(),
        control_implications="Controls over grant recognition, utilisation monitoring against "
                             "sanctioned purpose, and reporting to the sanctioning authority.",
        notes=(
            "App D's 'condition compliance' is a REGULARITY matter, not an assertion — carried "
            "separately so it is neither lost nor mislabelled.",
            "Material by nature under §2.4; the §11.2 by-nature override applies even at low value.",
        ),
    ),

    # =================================================================================
    # EXTENSION CLUSTERS — NOT PART OF APPENDIX D. See the S23-S27 block in `signals.py`
    # for the standing of this material and ROADMAP §16 decision 3 for the decision it
    # awaits. Both are listed by `python -m fdr contract --gaps`.
    # =================================================================================

    Cluster(
        id="RC-CONT",
        theme="Contingent liability and claims exposure",
        signals=("S23", "S24"),
        assertions=frozenset({A.COMPLETENESS, A.PRESENTATION, A.CLASSIFICATION}),
        regularity_matters=frozenset({A.PROPRIETY}),
        evidence=(
            "Litigation and arbitration register, with the amount claimed and the stage reached",
            "External counsel confirmations",
            "Financial guarantee agreements and the sanctions authorising them",
            "Management's assessment of probable / possible / remote, and its basis",
        ),
        specialist=("legal",),
        control_implications="Controls over the identification and periodic reassessment of "
                             "claims, over the provision-versus-disclosure judgement, and over "
                             "the authorisation of financial guarantees.",
        note_only_basis=(
            "Contingent liabilities are disclosed in a note and appear nowhere on the face of "
            "the statements, so this cluster has no face-derivable signal and cannot fire until "
            "the contingent-liability and financial guarantee schedules are extracted. It is "
            "declared anyway because the alternative is worse: without it the theme cannot be "
            "expressed at all, and a matter that is material BY NATURE — a disclosed, "
            "unprovided claim — has nowhere to go in the matrix."
        ),
        origin="PROPOSED",
        notes=(
            "The by-nature override in `priority.py` is what makes this cluster rank correctly: "
            "the exposure is admitted on nature and public interest, not on value.",
            "§17.1 applies with full force — the FDR must never suggest that a matter disclosed "
            "as contingent should have been provided. That is the audit team's judgement.",
        ),
    ),

    Cluster(
        id="RC-INV",
        theme="Investment concentration and income dependency",
        signals=("S25", "S26", "S27"),
        assertions=frozenset({A.VALUATION_AND_ALLOCATION, A.OCCURRENCE, A.CLASSIFICATION}),
        evidence=(
            "Investment schedule, by instrument and counterparty",
            "Fair-value hierarchy and the basis of level 3 valuations",
            "Related-party disclosures for group holdings and dividends",
            "Board approvals for material investment decisions",
        ),
        specialist=("valuation (for unlisted or level 3 holdings)",),
        control_implications="Controls over investment authorisation, over the periodic "
                             "revaluation of unquoted holdings, and over the recognition of "
                             "investment income in the correct period.",
        origin="PROPOSED",
        notes=(
            "S27 is the face-derivable member, so the cluster is reachable today; S25 and S26 "
            "sharpen it once the investment schedule is extracted.",
            "Distinct from RC-EST: that cluster asks whether an estimate is dependable, this "
            "asks whether the RESULT depends on a book the entity does not operate.",
        ),
    ),
)

BY_ID: dict[str, Cluster] = {c.id: c for c in CLUSTERS}


def clusters_for(signal_id: str) -> tuple[str, ...]:
    """Which clusters consume this signal. A signal may feed more than one."""
    return tuple(c.id for c in CLUSTERS if signal_id in c.signals)


# ---- §10.2 risk-interaction matrix ------------------------------------------------
# "Risks reinforce or offset one another … a reinforcing interaction raises the cluster's
# planning priority; an offsetting one is stated and moderates it." Recorded as data so
# the interaction is auditable, not an emergent property of a scoring function.
#
# WHY `mechanism` IS A SEPARATE FIELD FROM `basis`
# ------------------------------------------------
# `basis` says the interaction exists and cites the specification. `mechanism` names the
# ONE thing both clusters rest on — the shared balance, the shared cash flow, the shared
# estimate. That is the field an auditor plans against: two themes that reinforce through
# the same receivable balance are tested together with one population, and two that merely
# co-occur are not. Without the mechanism the interaction is a claim of correlation, which
# is exactly the "reinforcing cluster far more serious than any one of its parts" that
# §10.2 asks the model to justify rather than assert.
#
# These are STATIC declarations — the matrix of interactions that CAN arise. Which of them
# is actually live in a given report depends on what fired, and that resolution belongs to
# `interactions.py`, never here.

REINFORCES = "REINFORCES"
OFFSETS = "OFFSETS"
INTERACTION_KINDS = frozenset({REINFORCES, OFFSETS})


@dataclass(frozen=True)
class Interaction:
    """One declared §10.2 edge between two clusters. Undirected: `a` and `b` are symmetric."""
    a: str
    b: str
    kind: str            # REINFORCES | OFFSETS
    mechanism: str       # the shared driver both clusters rest on
    basis: str           # why the specification says these interact

    @property
    def pair(self) -> frozenset[str]:
        return frozenset({self.a, self.b})

    def involves(self, cluster_id: str) -> bool:
        return cluster_id in (self.a, self.b)

    def other(self, cluster_id: str) -> str:
        if cluster_id == self.a:
            return self.b
        if cluster_id == self.b:
            return self.a
        raise KeyError(f"{cluster_id} is not part of interaction {self.a}/{self.b}")


INTERACTIONS: tuple[Interaction, ...] = (

    Interaction(
        a="RC-WC", b="RC-FUND", kind=REINFORCES,
        mechanism="One cash shortfall. The operating cash flow that does not cover the "
                  "working-capital cycle is the same shortfall the additional borrowing "
                  "funds, so the two themes are two readings of a single position.",
        basis="Weak operating cash flow with rising leverage and a receivables build-up is "
              "the spec's own example of a reinforcing cluster far more serious than any "
              "one part (§10.2).",
    ),
    Interaction(
        a="RC-WC", b="RC-REC", kind=REINFORCES,
        mechanism="One balance. The receivable that is not collected is the working capital "
                  "that is not released; a single ageing and cut-off population tests both.",
        basis="Receivables-driven liquidity stress: the same underlying balance drives both.",
    ),
    Interaction(
        a="RC-FUND", b="RC-CAP", kind=REINFORCES,
        mechanism="One finance charge, in two places. Borrowing cost capitalised into the "
                  "asset base sits in the funding structure and in the carrying amount at "
                  "the same time, so an error in the capitalisation rate moves both (S15).",
        basis="Borrowing-cost capitalisation links solvency to capitalisation risk (S15).",
    ),
    Interaction(
        a="RC-EST", b="RC-REC", kind=REINFORCES,
        mechanism="One balance carries both the recoverability estimate and the revenue it "
                  "arose from, so the provisioning judgement and the receivable quality "
                  "cannot be tested independently of each other.",
        basis="Provisioning judgement sits on the receivable balance.",
    ),

    Interaction(
        a="RC-WC", b="RC-DEP", kind=OFFSETS,
        mechanism="Committed, continuing government support is a funding source the "
                  "liquidity diagnostics do not see, so it can explain a shortfall the "
                  "working-capital signals read as stress.",
        basis="A liquidity signal offset by committed, continuing government support is "
              "stated and moderated — but the offset is itself a dependency observation, "
              "not a clean bill (§10.2).",
    ),
)


def interactions_for(cluster_id: str) -> tuple[Interaction, ...]:
    """Every declared edge touching this cluster, reinforcing and offsetting alike."""
    return tuple(i for i in INTERACTIONS if i.involves(cluster_id))


# ---- import-time validation -------------------------------------------------------
def _validate() -> None:
    seen: set[str] = set()
    for c in CLUSTERS:
        if c.id in seen:
            raise ValueError(f"duplicate cluster id {c.id}")
        seen.add(c.id)
        if not c.signals:
            raise ValueError(f"{c.id}: a cluster with no signals cannot fire")
        for sid in c.signals:
            if sid not in S.BY_ID:
                raise ValueError(f"{c.id}: references unknown signal {sid}")
        bad = c.assertions - A.ASSERTIONS
        if bad:
            raise ValueError(f"{c.id}: unknown assertion(s) {sorted(bad)}")
        bad = c.regularity_matters - A.REGULARITY_MATTERS
        if bad:
            raise ValueError(f"{c.id}: unknown regularity matter(s) {sorted(bad)}")
        # Reachability: a cluster with only NOTE signals is dead until note extraction lands.
        # It may still be declared — some audit themes genuinely live entirely in the notes —
        # but only with an explicit basis saying which schedules it waits on. The point of the
        # check is that unreachability must be a STATED decision, never an accident nobody
        # notices until the matrix comes back short.
        if not any(S.BY_ID[sid].availability == FACE for sid in c.signals):
            if not c.note_only_basis:
                raise ValueError(
                    f"{c.id}: no FACE-derivable signal — the cluster is structurally "
                    f"unreachable in v1. Either add a face signal or set `note_only_basis` "
                    f"to record deliberately that it waits on note extraction."
                )

    orphans = [s.id for s in SIGNALS if not clusters_for(s.id)]
    if orphans:
        raise ValueError(
            f"signals consumed by no cluster: {orphans}. Per ROADMAP §5, a diagnostic no "
            f"cluster consumes is out of scope — either add it to a cluster (an audit "
            f"decision) or remove it from the registry."
        )

    known = set(BY_ID)
    pairs: dict[frozenset[str], str] = {}
    for i in INTERACTIONS:
        if i.a not in known or i.b not in known:
            raise ValueError(f"interaction references unknown cluster: {i.a} / {i.b}")
        if i.a == i.b:
            raise ValueError(f"cluster {i.a} cannot interact with itself")
        if i.kind not in INTERACTION_KINDS:
            raise ValueError(f"interaction {i.a}/{i.b}: unknown kind {i.kind!r}")
        if not i.mechanism.strip() or not i.basis.strip():
            raise ValueError(
                f"interaction {i.a}/{i.b}: an edge without a stated mechanism and basis is "
                f"an assertion of correlation, which §10.2 does not permit"
            )
        # One pair, one relationship. A pair declared both reinforcing and offsetting would
        # let the priority engine raise and moderate the same cluster on the same evidence,
        # and the output could not say which the audit team should believe.
        if i.pair in pairs:
            raise ValueError(
                f"interaction {i.a}/{i.b} is declared twice (already {pairs[i.pair]}). "
                f"A cluster pair carries exactly one relationship."
            )
        pairs[i.pair] = i.kind


_validate()
