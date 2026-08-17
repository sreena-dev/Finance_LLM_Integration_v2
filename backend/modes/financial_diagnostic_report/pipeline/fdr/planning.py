"""
The §10.3 planning package — authored content, per risk cluster.

WHAT THIS MODULE IS
-------------------
§10.3 requires every significant cluster to carry a full planning package: affected
assertions, inherent/significant risk, control implications, planning significance, a
candidate audit response (nature / timing / extent), specialist referral, evidence request,
alternative explanations and confidence. `clusters.py` already holds the parts Appendix D
and Appendix F give verbatim. This module holds the rest.

PROVENANCE IS TRACKED, BECAUSE SOME OF THIS IS PROPOSED
-------------------------------------------------------
Appendix F is described in the spec as representative, not exhaustive, and it has no row
at all for the working-capital cluster. Candidate responses are not tabulated anywhere in
the specification. Rather than leave the principal deliverable structurally broken, the
missing content is authored here and every field is tagged:

    SPEC      lifted from the specification (App D / App F / §13 / §20)
    PROPOSED  drafted here, pending audit sign-off

`python -m fdr contract --gaps` lists everything still PROPOSED. Nothing is silently
presented as though the specification said it.

THE RESPONSES ARE CANDIDATES, NOT A PLAN (§10.5)
------------------------------------------------
"The FDR proposes; the team decides." Nothing in this module sets scope, fixes materiality
or decides strategy, and the renderer says so on every matrix row.
"""
from __future__ import annotations
from dataclasses import dataclass

from . import clusters as CL

SPEC = "SPEC"
PROPOSED = "PROPOSED"

# §11.2 requires "a manageable shortlist sized to the audit team's capacity". Capacity is
# effort, not a count of themes, so each cluster carries a RELATIVE effort weight on a 1-5
# scale and the shortlist is filled against a capacity expressed in the same units.
#
# These weights are PROPOSED and deliberately relative, not hours. An hours estimate would
# be a fabricated number dressed as a plan: it depends on the entity's size, the team's
# composition and the prior year's coverage, none of which the FDR sees. A relative weight
# says only "this theme costs about twice that one", which is a claim the content actually
# supports and a reviewer can correct.
EFFORT_SCALE = (
    "1-5 relative planning effort. 1 = a small externally-confirmable population; "
    "3 = substantive testing over a routine population; 5 = entity-specific detail testing "
    "with a specialist referral. Relative only — never hours, and never a budget."
)

# §2.4 / §10.6 materiality dimensions.
VALUE, NATURE, CONTEXT, PUBLIC_INTEREST = "value", "nature", "context", "public_interest"


@dataclass(frozen=True)
class Response:
    """Candidate nature / timing / extent (§10.3, §10.5)."""
    nature: str
    timing: str
    extent: str
    source: str = PROPOSED


@dataclass(frozen=True)
class PlanningContent:
    cluster_id: str
    inherent_risk: str                       # HIGH | MEDIUM | LOW — before controls
    inherent_risk_basis: str
    significant_risk: bool                   # §10.3 — warrants special audit attention
    significant_risk_basis: str
    management_judgement_exposure: str       # HIGH | MEDIUM | LOW — a §11.1 priority dimension
    response: Response
    planning_significance: str
    admits_on: frozenset[str]                # §10.6 dimensions that can admit this cluster
    by_nature: bool                          # §11.2 override — elevated even when small
    by_nature_basis: str = ""
    effort_weight: int = 3                   # 1-5 relative planning effort; see below
    effort_basis: str = ""
    extra_evidence: tuple[str, ...] = ()     # beyond Appendix F
    extra_evidence_source: str = PROPOSED
    control_implications: str = ""           # fills the App D gap where clusters.py has none
    control_implications_source: str = PROPOSED


CONTENT: tuple[PlanningContent, ...] = (

    PlanningContent(
        cluster_id="RC-WC",
        effort_weight=3,
        effort_basis=(
            "Substantive testing over a routine but wide population: an unrecorded-liabilities search and cut-off work across the payables ledger. No specialist."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "Liquidity stress creates a direct incentive to defer recognition of liabilities "
            "and to classify short-term obligations favourably. The completeness assertion "
            "over liabilities is the one most exposed."
        ),
        significant_risk=False,
        significant_risk_basis=(
            "Not inherently a significant risk: the balances are routine and not estimate-"
            "driven. It becomes one where a net current-liability position coincides with "
            "reliance on rolling short-term finance, because the going-concern question then "
            "attaches — which is the FSA specification's review, corroborated here, not repeated."
        ),
        management_judgement_exposure="LOW",
        response=Response(
            nature="Analytical procedures over the working-capital cycle year on year; a search "
                   "for unrecorded liabilities; tests of detail over the payables cut-off; "
                   "review of post-year-end payments against period-end balances.",
            timing="Substantive focus at the reporting date, because the risk is cut-off and "
                   "completeness at that date; interim analytics to set the expectation.",
            extent="Extend the sample across the year-end cut-off window. Where payment-run data "
                   "is obtainable, prefer full-population analytics over sampling.",
            source=PROPOSED,
        ),
        planning_significance=(
            "Working-capital stress is where a going-concern question, a liabilities-"
            "completeness question and a supplier-payment regularity question converge. It "
            "usually warrants effort disproportionate to the balances involved."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT}),
        by_nature=False,
        extra_evidence=(
            "Ageing of trade payables, with the MSME split where the entity reports one",
            "Post-year-end payment listing covering the cut-off window",
            "Sanctioned and undrawn short-term facilities at the reporting date",
            "Board or management papers on liquidity and rolling finance",
        ),
        extra_evidence_source=PROPOSED,
        control_implications=(
            "Controls over the recording of goods and services received near the period end, "
            "over the authorisation and monitoring of short-term borrowing, and over the "
            "periodic review of the payables ageing including statutory dues."
        ),
        control_implications_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-REC",
        effort_weight=4,
        effort_basis=(
            "Confirmations carry an external dependency and a fallback path when replies do not arrive, and the population needs stratifying by age and counterparty."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "Revenue is presumed a fraud risk area under the risk-assessment standards, and "
            "receivable recoverability is estimate-driven. Both assertions sit on the same "
            "balance, so a single misstatement can affect occurrence and valuation together."
        ),
        significant_risk=True,
        significant_risk_basis=(
            "Revenue recognition and the recoverability provision are judgemental and "
            "material in most entities; where a related-party concentration is also present, "
            "the transactions are non-routine as well."
        ),
        management_judgement_exposure="HIGH",
        response=Response(
            nature="Cut-off testing either side of the reporting date; external confirmation of "
                   "significant and related-party balances; testing of the expected-credit-loss "
                   "or provisioning basis against the ageing; review of credit notes and "
                   "reversals after the year end.",
            timing="Cut-off work at the reporting date; confirmations initiated early enough to "
                   "allow alternative procedures where replies are not received.",
            extent="Stratify by age and counterparty; cover related-party and government "
                   "balances in full rather than by sample, given the by-nature exposure.",
            source=PROPOSED,
        ),
        planning_significance=(
            "The cluster combines an occurrence question on revenue with a valuation question "
            "on the resulting receivable. Where the two are addressed separately, a receivable "
            "that should never have been recognised is tested only for recoverability."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "A related-party or government-counterparty concentration is material by nature "
            "under §2.4 even at low value, and the §11.2 override applies."
        ),
        extra_evidence=(
            "Revenue recognition policy and its application to the significant contract types",
            "Credit notes and sales reversals recorded after the reporting date",
        ),
        extra_evidence_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-CAP",
        effort_weight=5,
        effort_basis=(
            "Project-level tests of detail against records held outside the finance function, plus an engineering referral to corroborate completion status."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "Capitalisation converts an expense into an asset, so it affects the reported "
            "result and the balance sheet in the same movement. In capital-intensive public "
            "sector entities the amounts are usually the largest on the balance sheet."
        ),
        significant_risk=True,
        significant_risk_basis=(
            "Whether costs of a revenue nature have been capitalised, and whether long-held "
            "capital work-in-progress remains recoverable, are judgemental determinations "
            "affecting material balances."
        ),
        management_judgement_exposure="HIGH",
        response=Response(
            nature="Project-level tests of detail over additions and over transfers out of "
                   "capital work-in-progress; assessment of the capitalisation policy against "
                   "the costs actually capitalised; corroboration of physical completion "
                   "status; review of the impairment assessment for long-held projects.",
            timing="Interim testing of additions is efficient; completion status and impairment "
                   "must be assessed as at the reporting date.",
            # "deliberately" was reworded here: §17.2's intent-attribution rule is about the
            # entity, but the lint is a plain word match and a strict lint that occasionally
            # forces a rewrite is the right trade in this domain. Rewording is safer than
            # teaching the gate to recognise exceptions.
            extent="Select projects by value and by age, purposively including projects "
                   "carried forward across more than one reporting period.",
            source=PROPOSED,
        ),
        planning_significance=(
            "The §20 infrastructure example is this cluster: three individually mild signals "
            "that together justify project-level testing and an engineering referral. The "
            "cluster is worth more attention than any of its parts would earn alone."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=False,
        extra_evidence=(
            "Capital work-in-progress ageing, by project",
            "Board or competent-authority sanction for each significant project",
            "Physical progress or completion certificates from the project authority",
        ),
        extra_evidence_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-FUND",
        effort_weight=2,
        effort_basis=(
            "A small, externally confirmable population with documentary evidence readily available; the work is bounded and largely predictable."
        ),
        inherent_risk="MEDIUM",
        inherent_risk_basis=(
            "Borrowings are routine balances with external evidence readily available, so the "
            "risk of undetected misstatement is lower — but classification between current and "
            "non-current, and the completeness of covenant and default disclosure, are not."
        ),
        significant_risk=False,
        significant_risk_basis=(
            "The balances are confirmable and not estimate-driven. Elevate where a covenant "
            "breach or repayment default is indicated, because the consequence is a "
            "reclassification of the whole facility and a going-concern question."
        ),
        management_judgement_exposure="LOW",
        response=Response(
            nature="Direct confirmation of borrowings with lenders; agreement of terms, "
                   "covenants and repayment schedules to the loan agreements; recomputation of "
                   "the current / non-current split; analytical reconciliation of the finance "
                   "cost to the average borrowing.",
            timing="Confirmations at the reporting date; covenant status assessed as at that "
                   "date and up to the date of the audit report.",
            extent="Cover all significant facilities rather than sampling; the population is "
                   "small and externally confirmable.",
            source=PROPOSED,
        ),
        planning_significance=(
            "A return improvement driven by leverage is a solvency story presented as a "
            "performance one (§8.2). Reading it correctly redirects effort from the income "
            "statement to borrowings, disclosure and covenant compliance."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT}),
        by_nature=False,
        extra_evidence=(
            "Reconciliation of the finance cost to average borrowings, with capitalised "
            "borrowing cost identified separately",
            "Sanction letters and terms for facilities drawn during the period",
        ),
        extra_evidence_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-EST",
        effort_weight=4,
        effort_basis=(
            "Assumption testing plus a retrospective review of prior estimates, and a valuation or actuarial referral for the material balances."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "Estimates are the balances least constrained by external evidence, and the "
            "reporting-behaviour indicators in §9.3 attach to exactly these balances."
        ),
        significant_risk=True,
        significant_risk_basis=(
            "Estimation uncertainty and the degree of management judgement are, on their own, "
            "the characteristics of a significant risk under the risk-assessment standards."
        ),
        management_judgement_exposure="HIGH",
        response=Response(
            nature="Testing of the assumptions and the method against the requirements of the "
                   "framework; retrospective review of prior-period estimates against outcomes; "
                   "testing of the basis for each significant reversal; independent "
                   "recomputation or a range where the assumptions permit.",
            timing="After the reporting date, once the estimate is final; the retrospective "
                   "review can begin at the planning stage and informs the risk assessment.",
            extent="Cover each estimate that is individually material, plus every reversal "
                   "above the working materiality level regardless of the balance it sits in.",
            source=PROPOSED,
        ),
        planning_significance=(
            "The retrospective review is the highest-value procedure here: a pattern of "
            "estimates settling consistently in one direction is a risk indicator that no "
            "single-year test can produce. It is a risk indicator only — §9.3 and §17.1 forbid "
            "any inference about intent."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT}),
        by_nature=False,
        extra_evidence=(
            "Prior-period estimates with their eventual outcomes, for retrospective review",
            "Minutes or approvals recording the basis for each significant reversal",
        ),
        extra_evidence_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-DEP",
        effort_weight=3,
        effort_basis=(
            "Scheme-by-scheme testing against sanctions and utilisation certificates: documentary rather than judgemental, but it cannot be done in aggregate."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "Grant income recognition depends on conditions being satisfied, which is a "
            "determination made by the entity receiving the grant. Utilisation against "
            "sanctioned purpose is a regularity question that the financial statements alone "
            "do not answer."
        ),
        significant_risk=True,
        significant_risk_basis=(
            "Condition compliance and utilisation carry a regularity and propriety dimension "
            "beyond the amount involved, and the §3.3 dependency lens applies to the entity's "
            "financial position as a whole."
        ),
        management_judgement_exposure="MEDIUM",
        response=Response(
            nature="Agreement of grants recognised to sanction orders and their conditions; "
                   "testing of utilisation against the sanctioned purpose; reconciliation of "
                   "unspent balances to utilisation certificates; assessment of whether "
                   "continued support is committed or assumed.",
            timing="Throughout, as sanctions and releases occur; the unspent-balance "
                   "reconciliation is performed at the reporting date.",
            extent="Cover each scheme or sanction individually rather than in aggregate — "
                   "conditions attach per sanction, and an aggregate test cannot detect a "
                   "breach on one of them.",
            source=PROPOSED,
        ),
        planning_significance=(
            "This is the cluster where the public-sector lenses bite hardest. A grant used "
            "outside its conditions is material by nature at any value, and dependency on "
            "continued support is a planning consideration for every other cluster in the "
            "report."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "Regularity, propriety and public interest all attach: an unsanctioned outflow or "
            "a grant used against its conditions is material by nature even at small value "
            "(§2.4), and the §11.2 override applies."
        ),
        extra_evidence=(
            "Scheme-wise reconciliation of grants received, utilised and unspent",
            "Correspondence with the sanctioning authority on carry-forward or extension",
        ),
        extra_evidence_source=PROPOSED,
    ),

    # ---- EXTENSION clusters. Every field here is PROPOSED, the cluster itself included.
    PlanningContent(
        cluster_id="RC-CONT",
        effort_weight=3,
        effort_basis=(
            "A bounded population - the litigation register is a list, not a ledger - but it "
            "carries an external dependency on counsel confirmations and a legal referral, and "
            "the judgement being tested is management's own assessment."
        ),
        inherent_risk="HIGH",
        inherent_risk_basis=(
            "The provision-versus-disclosure boundary is a judgement made by management about "
            "matters where management is the party in dispute, and the completeness of the "
            "register cannot be established from within the finance function."
        ),
        significant_risk=True,
        significant_risk_basis=(
            "Warrants special audit attention: the exposure is typically large relative to net "
            "worth, the measurement is judgemental, and the completeness and presentation "
            "assertions are exposed at the same time."
        ),
        management_judgement_exposure="HIGH",
        response=Response(
            nature="Read the litigation and arbitration register against the disclosure; obtain "
                   "external counsel confirmations directly; test the probable / possible / "
                   "remote classification against the evidence for the largest matters; inspect "
                   "guarantee agreements and the sanctions authorising them.",
            timing="Substantive at the reporting date and extended to the date of the audit "
                   "report - a matter's status changes after the year end, so the "
                   "subsequent-events window is part of the test rather than an addendum to it.",
            extent="Every matter above planning materiality individually, plus a sample of the "
                   "remainder directed at COMPLETENESS rather than measurement: the risk is a "
                   "matter absent from the register, which sampling the register cannot find. "
                   "Corroborate against legal expenditure and board minutes.",
            source=PROPOSED,
        ),
        planning_significance=(
            "A disclosed but unprovided claim is material by nature for a Government company "
            "even when quantitatively small: it engages propriety and public interest, and it is "
            "a classic matter on which supplementary audit differs from the statutory auditor. "
            "Effort here is rarely proportionate to the amounts recognised, because nothing is "
            "recognised."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "Propriety and public interest attach directly (2.4): a financial guarantee given "
            "without sanction, or a disclosure that understates the exposure, is material by "
            "nature at any value. The 11.2 override applies."
        ),
        extra_evidence=(
            "Legal expenditure analysis, as an independent route to matters not in the register",
            "Board and committee minutes for the year and the subsequent-events period",
            "Prior-year contingent liabilities traced to their outcome - the retrospective "
            "review that tests how reliable management's assessments have proved",
        ),
        extra_evidence_source=PROPOSED,
        control_implications=(
            "Controls over the identification and periodic reassessment of claims, over the "
            "provision-versus-disclosure judgement and its documentation, and over the "
            "authorisation of financial guarantees within delegated powers."
        ),
        control_implications_source=PROPOSED,
    ),

    PlanningContent(
        cluster_id="RC-INV",
        effort_weight=3,
        effort_basis=(
            "Quoted holdings are externally confirmable and cheap to test; the effort "
            "concentrates on the unquoted tail, which needs a valuation referral. The weight "
            "assumes a mixed book and should be re-read once the level split is known."
        ),
        inherent_risk="MEDIUM",
        inherent_risk_basis=(
            "Existence and income occurrence are readily corroborated for quoted holdings. The "
            "exposure is the valuation of unquoted holdings, where the inputs are unobservable "
            "and the entity chooses the model."
        ),
        significant_risk=False,
        significant_risk_basis=(
            "Not inherently a significant risk where the book is quoted and liquid. It becomes "
            "one where unquoted or level 3 holdings are material, or where the investments are "
            "in related parties - at which point valuation and related-party pricing converge."
        ),
        management_judgement_exposure="MEDIUM",
        response=Response(
            nature="Confirm holdings directly with the depository or registrar; agree quoted "
                   "valuations to independent market data; for unquoted holdings test the "
                   "valuation model, its inputs and its consistency with the prior year; "
                   "analytical review of investment income against the holdings that produced it.",
            timing="Existence at the reporting date; valuation work planned early enough to "
                   "accommodate a specialist, because a late valuation referral is what turns "
                   "this from a routine area into a reporting-deadline problem.",
            extent="Full coverage of the quoted book by confirmation, which is inexpensive. "
                   "Unquoted holdings individually where material, with income tested against "
                   "the holding that generated it rather than in aggregate.",
            source=PROPOSED,
        ),
        planning_significance=(
            "Where a substantial share of assets sits in investments and a substantial share of "
            "the result comes from their income, the reported performance is not primarily a "
            "measure of the operating business. That matters for planning because the effort "
            "implied by the operating segments understates what the balance sheet needs, and "
            "because a related-party investment book raises pricing and propriety questions an "
            "ordinary treasury book does not."
        ),
        admits_on=frozenset({VALUE, NATURE, CONTEXT}),
        by_nature=False,
        extra_evidence=(
            "Investment schedule by instrument, counterparty and fair-value level",
            "Independent price evidence for quoted holdings at the reporting date",
            "Valuation models and inputs for unquoted holdings, with the prior-year comparison",
            "Related-party disclosures covering group holdings and dividends received",
        ),
        extra_evidence_source=PROPOSED,
        control_implications=(
            "Controls over investment authorisation within delegated powers, over the periodic "
            "revaluation of unquoted holdings, and over the recognition of investment income in "
            "the correct period."
        ),
        control_implications_source=PROPOSED,
    ),
)

BY_ID: dict[str, PlanningContent] = {c.cluster_id: c for c in CONTENT}


def evidence_for(cluster_id: str) -> tuple[tuple[str, str], ...]:
    """Evidence requests with provenance: (request, SPEC|PROPOSED)."""
    out = [(e, SPEC) for e in CL.BY_ID[cluster_id].evidence]
    pc = BY_ID.get(cluster_id)
    if pc:
        out += [(e, pc.extra_evidence_source) for e in pc.extra_evidence]
    return tuple(out)


def control_implications_for(cluster_id: str) -> tuple[str, str]:
    """(text, provenance) — clusters.py first, this module's draft where it is empty."""
    spec = CL.BY_ID[cluster_id].control_implications
    if spec:
        return spec, SPEC
    pc = BY_ID.get(cluster_id)
    return (pc.control_implications, pc.control_implications_source) if pc else ("", PROPOSED)


def proposed_items() -> tuple[str, ...]:
    """Everything awaiting audit sign-off. Surfaced by `python -m fdr contract --gaps`."""
    out: list[str] = []
    for pc in CONTENT:
        if pc.response.source == PROPOSED:
            out.append(f"{pc.cluster_id}  candidate audit response (nature/timing/extent) "
                       f"— drafted here; the specification tabulates none")
        if pc.extra_evidence and pc.extra_evidence_source == PROPOSED:
            out.append(f"{pc.cluster_id}  {len(pc.extra_evidence)} evidence request(s) beyond "
                       f"Appendix F")
        _, src = control_implications_for(pc.cluster_id)
        if src == PROPOSED:
            out.append(f"{pc.cluster_id}  control implications — no Appendix F row")
    return tuple(out)


def _validate() -> None:
    known = set(CL.BY_ID)
    seen = set()
    for pc in CONTENT:
        if pc.cluster_id not in known:
            raise ValueError(f"planning content for unknown cluster {pc.cluster_id}")
        if pc.cluster_id in seen:
            raise ValueError(f"duplicate planning content for {pc.cluster_id}")
        seen.add(pc.cluster_id)
        if pc.inherent_risk not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError(f"{pc.cluster_id}: bad inherent risk {pc.inherent_risk!r}")
        if pc.by_nature and not pc.by_nature_basis:
            raise ValueError(f"{pc.cluster_id}: a by-nature override must state its basis (§11.2)")
        if not pc.admits_on:
            raise ValueError(f"{pc.cluster_id}: admits on no materiality dimension (§10.6)")
        if not 1 <= pc.effort_weight <= 5:
            raise ValueError(f"{pc.cluster_id}: effort weight {pc.effort_weight} outside 1-5")
        if not pc.effort_basis.strip():
            raise ValueError(f"{pc.cluster_id}: an effort weight with no stated basis is a "
                             f"number nobody can challenge (§16)")
        for f in (pc.response.nature, pc.response.timing, pc.response.extent,
                  pc.planning_significance, pc.inherent_risk_basis,
                  pc.significant_risk_basis):
            if not f.strip():
                raise ValueError(f"{pc.cluster_id}: an empty planning field would render as "
                                 f"a dash and read as 'nothing to do'")
    missing = known - seen
    if missing:
        raise ValueError(
            f"no planning content for {sorted(missing)}. Every cluster must carry a full "
            f"§10.3 package — a cluster that can be raised but not planned is useless."
        )


_validate()
