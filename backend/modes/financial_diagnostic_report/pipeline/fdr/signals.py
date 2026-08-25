"""
FDR signal registry — the closed set of warning signs Layers 1-4 must produce.

WHY THIS MODULE EXISTS
----------------------
FDR Appendix D names, for each of the six risk clusters, the signals that compose it.
That is the requirement specification for every diagnostic in Layers 1-4: a diagnostic no
cluster consumes is out of scope, and a signal every cluster needs is mandatory. Until it
is written down in one place, the layers get built by intuition and the two failure modes
are invisible.

This module declares the signals. `clusters.py` declares what consumes them, and validates
at import that the two agree — no orphan signals, no dangling references.

WHAT IS NOT HERE
----------------
Two things, each with its own module, and the separation is deliberate:

  the RULE          `rules.py` — the deterministic function that decides whether a signal
                    fires, and the only place a verdict or a number is formed.
  the DERIVATION    `derivations.py` — how the figure is formed and where in the annual
                    report its inputs live. A signal declares WHAT it is; the derivation
                    declares HOW it is computed and from which note or schedule.

`inputs` below is the contract both of them answer to, and `derivations.py` validates at
import that its required inputs are exactly this set — `readiness()` reports coverage from
this registry, so a divergence would make the coverage report describe a contract nobody
implements.

Nothing in this file may become a runtime model call. Signal emission is deterministic
Python over panel facts (finance-core P1). The LLM's only job downstream is narration.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .assertions import ASSERTIONS  # noqa: F401  (re-exported for callers)

# ---- vocabularies ----------------------------------------------------------------

FACE = "FACE"        # derivable from the face of BS / P&L / Cash Flow via the panel
NOTE = "NOTE"        # needs note-level extraction (harder; abstains until M-note lands)
AVAILABILITY = frozenset({FACE, NOTE})

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
SEVERITIES = frozenset({HIGH, MEDIUM, LOW})

# Business models (spec §5.1) used by `suppressed_for`. Kept as bare strings here; the
# full library with value drivers is Appendix B and lands with M2 `sectors`.
BUSINESS_MODELS = frozenset({
    "manufacturing", "trading", "mining", "power_utilities", "petroleum",
    "infrastructure_epc", "ports_airports", "transport_logistics", "telecom",
    "real_estate_construction", "services", "banking", "nbfc", "insurance",
    "autonomous_body", "research_education", "healthcare", "corpus_holding",
})

# Framework profile ids (ratio-engine design §7.4).
#
# SCOPE, DECIDED 2026-08-05: COMPANIES ONLY.
# ------------------------------------------
# `common_format` covers Autonomous Bodies and statutory corporations, which report under
# the Uniform Format of Accounts — Income and Expenditure Account, Corpus/Capital Fund,
# Receipts and Payments — and not Schedule III at all. It is IN THE VOCABULARY and OUT OF
# SCOPE, deliberately: the corpus holds 50 entities and every one of them is a company.
# Measured 2026-08-04: zero "Receipts and Payments" statements, zero Corpus/Capital Fund
# balance sheets. Building AB node maps, identities and signals now would mean writing a
# rulebook nobody can falsify against a single real filing.
#
# What that means in practice:
#   - no AB-specific canonical keys, identities or signals are to be written
#   - `autonomous_body`, `corpus_holding` and `research_education` stay in BUSINESS_MODELS
#     because the classifier must still be able to REFUSE to call an entity a company
#   - the three Schedule III divisions below are all real and all present in the corpus:
#     Div II (Ind AS) is the bulk, Div III (NBFC) is ~7 entities, Div I (AS) the remainder
#
# Reopen this when AB filings are actually ingested — not before.
FRAMEWORKS = frozenset({"sch3_div1", "sch3_div2", "sch3_div3", "common_format"})

# The frameworks work is actually targeted at. Anything outside this is vocabulary only.
IN_SCOPE_FRAMEWORKS = frozenset({"sch3_div1", "sch3_div2", "sch3_div3"})

_AUDIT_TODO = "NEEDS_AUDIT_AUTHORING"


@dataclass(frozen=True)
class Signal:
    """One warning sign. Frozen: the set is fixed by the specification, like Schedule III's."""
    id: str
    layer: int                       # 1-4; which layer produces it
    title: str                       # Appendix D's own wording where it has one
    spec_basis: str                  # the sentence in the spec this implements
    inputs: tuple[str, ...]          # canonical figures the rule will need
    window: int                      # years of comparable data required (>=3 for any trend)
    availability: str                # FACE | NOTE
    default_severity: str            # PROPOSED severity of the risk; audit review pending
    suppressed_for: frozenset[str] = frozenset()        # business models where this is normal
    not_applicable_frameworks: frozenset[str] = frozenset()
    alt_explanations: tuple[str, ...] = ()              # §2.2 / §10.3; () = to be authored
    notes: tuple[str, ...] = ()

    @property
    def needs_audit_authoring(self) -> bool:
        """True while the non-error explanations for this signal are unwritten (§2.2)."""
        return not self.alt_explanations

    @property
    def is_trend(self) -> bool:
        return self.window >= 3


# =================================================================================
# The twenty-two signals of Appendix D.
#
# `window`:  1 = a point-in-time state; 2 = a year-on-year movement; 3+ = a trend, and
#            §9.5 forbids computing it on a shorter series ("a two-point movement is
#            never presented as a trend").
# `inputs`:  canonical keys. Those already bound by fs_db are named to match; those with
#            no binder today are marked in `notes` so M6/M7 knows what it must add.
# =================================================================================

SIGNALS: tuple[Signal, ...] = (

    # ---- cluster: working-capital and liquidity stress ---------------------------
    Signal(
        id="S01",
        layer=4,
        title="Deteriorating cash-conversion cycle",
        spec_basis="App D; §9.2 — working capital read as an interacting system",
        inputs=("trade_receivables", "inventories", "trade_payables", "revenue",
                "cost_of_materials_consumed"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "A deliberate change in credit terms to win volume.",
            "A shift in sales mix toward slower-paying customer segments.",
            "Year-end timing of a large receipt or payment rather than a structural change.",
        ),
    ),
    Signal(
        id="S02",
        layer=2,
        title="Payables funding growth",
        spec_basis="§7 funding structure — 'funding of operations through suppliers or "
                   "delayed payments is a working-capital-stress signal'",
        inputs=("trade_payables", "cost_of_materials_consumed", "revenue",
                "trade_receivables", "inventories"),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        alt_explanations=(
            "Negotiated extension of supplier credit on commercial terms.",
            "A large capital purchase on credit falling either side of the year end.",
        ),
        notes=("Cross-check MSME disclosure where the entity reports one — overdue MSME dues "
               "are a regularity matter, not only a liquidity one.",),
    ),
    Signal(
        id="S03",
        layer=2,
        title="Net current-liability position",
        spec_basis="§7 liquidity structure — 'net current-liability positions and reliance "
                   "on rolling short-term finance'",
        inputs=("total_current_assets", "total_current_liabilities"),
        window=1,
        availability=FACE,
        default_severity=HIGH,
        not_applicable_frameworks=frozenset({"sch3_div3"}),   # no current/non-current split
        alt_explanations=(
            "Normal for an entity funded by continuous budgetary release rather than working capital.",
            "A single large current maturity of long-term debt due for refinancing.",
        ),
        notes=("total_current_liabilities binds 17.5% today — this signal is blocked on the "
               "structural-derivation work (ratio design §8/3d).",),
    ),
    Signal(
        id="S04",
        layer=4,
        title="Weak or negative operating cash flow",
        spec_basis="§9.1 cash conversion — 'read against the business model, not mechanically'",
        inputs=("ocf", "pat"),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        suppressed_for=frozenset({"infrastructure_epc"}),
        alt_explanations=(
            "Growth-phase build-out where working capital is being funded ahead of revenue.",
            "A one-off settlement or arrears payment in the period.",
        ),
        notes=("§9.1 explicitly warns against reading this mechanically; suppression for a "
               "growth-phase builder is the spec's own example.",),
    ),

    # ---- cluster: revenue and receivable quality ---------------------------------
    Signal(
        id="S05",
        layer=2,
        title="Receivables outpacing revenue",
        spec_basis="App D; §7 asset mix — 'a receivables-heavy balance sheet points to "
                   "specific audit areas'",
        inputs=("trade_receivables", "revenue"),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        suppressed_for=frozenset({"power_utilities"}),
        alt_explanations=(
            "Tariff-based or regulated receivables normal to the business model (§20 worked example).",
            "A change in customer mix toward government or institutional buyers with longer cycles.",
            "Revenue recognised near the year end under a milestone contract.",
        ),
        notes=("The §20 power-utility example turns on NOT flagging this for a tariff-based "
               "utility; instead test whether the trend exceeds the sector-normal pattern.",),
    ),
    Signal(
        id="S06",
        layer=4,
        title="Accruals-heavy earnings",
        spec_basis="§9.1 earnings quality — accruals ratio and its trend",
        inputs=("pat", "ocf", "total_assets"),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        alt_explanations=(
            "A large non-cash regulatory or grant income recognised under the framework.",
            "A genuine build-out of working capital in a growth year.",
        ),
    ),
    Signal(
        id="S07",
        layer=3,
        title="Period-end revenue concentration",
        spec_basis="App D, revenue/receivable quality cluster",
        inputs=("contract_assets", "unbilled_revenue", "quarterly_revenue"),
        window=1,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(),
        notes=("Needs interim or segment data the annual report rarely carries. Expect ABSTAIN "
               "in v1; the coverage note must say so rather than the cluster going quiet.",),
    ),
    Signal(
        id="S08",
        layer=2,
        title="Related-party receivable concentration",
        spec_basis="§7 concentration — 'concentration is a risk in its own right and feeds "
                   "the dependency lens'",
        inputs=("trade_receivables", "related_party_receivables", "related_party_provision"),
        window=1,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(
            "Normal intra-government trading for a CPSU selling to another public entity.",
        ),
        notes=("Material by nature under §2.4 even at low value — the by-nature override in "
               "§11.2 applies.",),
    ),

    # ---- cluster: asset and capitalisation risk ----------------------------------
    Signal(
        id="S09",
        layer=2,
        title="Ageing capital work-in-progress",
        spec_basis="App D; §7 asset mix — 'a rising CWIP share points to specific audit areas'",
        inputs=("cwip", "net_fixed_assets", "cwip_ageing_buckets", "cwip_overdue_projects"),
        window=2,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(
            "A long-gestation project proceeding to plan.",
            "A project halted by an external approval or land dispute, already disclosed.",
        ),
        notes=("The CWIP *share* and its drift are FACE-derivable (see S10 sibling logic); "
               "the *ageing* that makes it a signal is note-level.",),
    ),
    Signal(
        id="S10",
        layer=2,
        title="Rising non-current-other share",
        spec_basis="§7 asset mix — 'a rising non-current-other or CWIP share'; §9.1 "
                   "expense and capitalisation behaviour",
        inputs=("other_non_current_assets", "total_assets"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Reclassification following a change in presentation.",
            "A genuine long-term deposit or advance placed in the period.",
        ),
        notes=("Depends on the common-size residual bucket being materialised rather than "
               "dropped (structure design §6).",),
    ),
    Signal(
        id="S11",
        layer=3,
        title="Depreciation not moving with the asset base",
        spec_basis="App D, asset/capitalisation cluster",
        inputs=("depreciation", "net_fixed_assets"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Assets added late in the year, so a part-year charge.",
            "A revision of useful lives disclosed in the period (links to S17).",
            "A large fully-depreciated cohort still in use.",
        ),
    ),
    Signal(
        id="S12",
        layer=4,
        title="Impairment timing",
        spec_basis="§9.3 reporting-behaviour indicators — 'impairment timing … read strictly "
                   "as risk signals requiring corroboration, never as conclusions about intent'",
        inputs=("net_fixed_assets", "impairment_charge", "impairment_reversal",
                "cgu_assumptions", "discount_rate"),
        window=3,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(),
        notes=("§9.3 wording discipline is mandatory here — no inference about intent.",),
    ),

    # ---- cluster: funding and solvency risk --------------------------------------
    Signal(
        id="S13",
        layer=3,
        title="Leverage-driven return on equity",
        spec_basis="§8.2 — 'an improving ROE driven entirely by rising leverage is not an "
                   "improvement in performance; it is a solvency and borrowing-disclosure lead'",
        inputs=("pat", "revenue", "total_assets", "total_equity"),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        alt_explanations=(
            "A deliberate, disclosed recapitalisation or buy-back.",
            "A one-off equity reduction from an actuarial or fair-value reserve movement.",
        ),
        notes=("Produced by the DuPont decomposition, not by an ROE threshold. The "
               "decomposition is what moves this from a performance story to a solvency one "
               "— i.e. it changes which cluster the signal joins.",),
    ),
    Signal(
        id="S14",
        layer=2,
        title="Short-term funding of long-term assets",
        spec_basis="§7 liability and capital structure — 'short-term funding of long-term assets'",
        inputs=("total_non_current_assets", "total_equity", "long_term_borrowings",
                "short_term_borrowings"),
        window=2,
        availability=FACE,
        default_severity=HIGH,
        not_applicable_frameworks=frozenset({"sch3_div3"}),
        alt_explanations=(
            "Bridge finance pending a disclosed long-term drawdown.",
            "Sanctioned but undrawn long-term facilities available at the year end.",
        ),
        notes=("Cross-dimensional: needs the liquidity position AND the movement in "
               "non-current assets in the same year.",),
    ),
    Signal(
        id="S15",
        layer=3,
        title="Finance cost not moving with borrowings",
        spec_basis="App D, funding/solvency cluster",
        inputs=("finance_costs", "long_term_borrowings", "short_term_borrowings"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Borrowing-cost capitalisation into qualifying assets (links to the capitalisation cluster).",
            "Borrowings drawn or repaid late in the year.",
            "A shift in the fixed/floating or currency mix.",
        ),
        notes=("Capitalised borrowing cost is the most common benign explanation AND a "
               "capitalisation-risk lead — the interaction is the point (§10.2).",),
    ),

    # ---- cluster: estimate and reporting-quality risk -----------------------------
    Signal(
        id="S16",
        layer=4,
        title="Provision volatility and reversals",
        spec_basis="§9.3 estimate quality — 'consistency, volatility, reversals and sensitivity'",
        inputs=("provisions_opening", "provisions_charge", "provisions_reversal",
                "provisions_closing", "provision_class"),
        window=3,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(
            "Settlement of a long-running dispute in the entity's favour.",
            "A genuine change in the underlying exposure.",
        ),
        notes=("§17.1 safe wording is mandatory: an estimate-quality risk indicator, with no "
               "inference about intent.",),
    ),
    Signal(
        id="S17",
        layer=4,
        title="Useful-life changes",
        spec_basis="§9.3 reporting-behaviour indicators",
        inputs=("useful_life_disclosure", "estimate_change_effect"),
        window=2,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(
            "A technical reassessment supported by an engineering review.",
            "Alignment to a revised Schedule II or regulatory life.",
        ),
    ),
    Signal(
        id="S18",
        layer=4,
        title="Non-cash gains",
        spec_basis="§9.1 recurring vs one-off earnings",
        inputs=("other_income", "pbt", "ocf"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Fair-value movement on investments required by the framework.",
            "A write-back of a provision no longer required (links to S16).",
        ),
    ),
    Signal(
        id="S19",
        layer=3,
        title="Other-income sustainability",
        spec_basis="§9.1 — 'the sustainability of other income'",
        inputs=("other_income", "total_income"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        suppressed_for=frozenset({"corpus_holding", "nbfc", "banking", "insurance"}),
        alt_explanations=(
            "Interest on a large cash balance held for a disclosed purpose.",
            "Normal for a corpus-holding body whose income IS investment income.",
        ),
    ),

    # ---- cluster: government-dependency and grant risk ----------------------------
    Signal(
        id="S20",
        layer=4,
        title="High government-support dependency",
        spec_basis="§3.3 dependency lens; §9.3; Appendix G — (grants + subsidies + budgetary "
                   "support) / total income",
        inputs=("total_income",),
        window=3,
        availability=FACE,
        default_severity=HIGH,
        alt_explanations=(
            "Structural to the entity type — a grant-funded body is dependent by design; the "
            "signal is a change in the degree, not its existence.",
        ),
        notes=("Reported as an index with its components shown, never a bare score (§9.4).",),
    ),
    Signal(
        id="S21",
        layer=2,
        title="Unspent-grant build-up",
        spec_basis="§4.1 grant and unspent-grant balances; App D dependency cluster",
        inputs=("unspent_grant_balance", "grants_received", "grants_utilised"),
        window=3,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(
            "A grant released late in the year with a permitted carry-forward.",
            "A multi-year scheme in an early phase.",
        ),
        notes=("§17.1: phrase as a utilisation and possible-diversion-RISK indicator requiring "
               "utilisation-certificate and sanction-order corroboration. Never 'diverted'.",),
    ),
    Signal(
        id="S22",
        layer=1,
        title="Administered-pricing reliance",
        spec_basis="§3.3; §5.2 revenue model — administered / tariff-based / subsidy-supported",
        inputs=("revenue_by_pricing_basis", "revenue_model"),
        window=1,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Inherent to the sector; the lead is the degree of reliance and its change, not "
            "the fact of it.",
        ),
        notes=("Layer 1 output — falls out of business understanding (M5), not of a "
               "computation. It conditions how S05 and S20 are read.",),
    ),

    # =================================================================================
    # EXTENSION SIGNALS — S23 TO S27. NOT PART OF APPENDIX D.
    #
    # Everything above this line is the closed Appendix D set. Everything below is
    # PROPOSED and awaits the audit-side decision recorded as ROADMAP §16 decision 3
    # ("Appendix D additions — extend clusters.yaml, or leave out of scope?").
    #
    # WHY THEY WERE ADDED
    # -------------------
    # Two audit themes that a reference FDR raises have no home in the six Appendix D
    # clusters at all, so the engine could not express them however much data it was
    # given: contingent-liability and arbitration exposure, and investment concentration
    # with the income dependency that comes with it. For a Government company both are
    # ordinary planning matters — a disclosed-but-unprovided contractor claim is material
    # by NATURE under §2.4 even when it is quantitatively small, which is precisely the
    # case §11.2's by-nature override exists for.
    #
    # THE STANDING OF THESE FIVE IS DIFFERENT FROM THE TWENTY-TWO
    # -----------------------------------------------------------
    # Appendix D signals are specification. These are a proposal. `python -m fdr contract
    # --gaps` lists them under that heading and the renderer marks any cluster built from
    # them, so nothing reaches an audit team presented as though the spec had asked for it.
    # If the audit side declines them, deleting this block and the two clusters in
    # `clusters.py` returns the system exactly to the Appendix D set.
    # =================================================================================

    Signal(
        id="S23",
        layer=2,
        title="Contingent liabilities large relative to net worth",
        spec_basis="EXTENSION (ROADMAP §16 decision 3). Nearest spec anchor: §2.4 materiality "
                   "by nature, and §10.6's public-interest dimension.",
        inputs=("contingent_liabilities_total", "total_equity"),
        window=2,
        availability=NOTE,
        default_severity=HIGH,
        alt_explanations=(
            "Claims well defended and assessed as remote, correctly disclosed rather than provided.",
            "A single large industry-wide tax or duty matter pending appellate resolution.",
            "Long-running matters carried at full claim value because no reliable estimate "
            "of the outflow can be made — which is the required treatment, not a deficiency.",
        ),
        notes=("The signal is the RATIO and its movement, never the existence of contingent "
               "liabilities: every entity of scale has them. §17.1 wording applies — this is "
               "an exposure to plan for, never an assertion that a provision is missing.",),
    ),
    Signal(
        id="S24",
        layer=2,
        title="Financial guarantees issued for group entities",
        spec_basis="EXTENSION (ROADMAP §16 decision 3). Nearest spec anchor: §2.4 propriety "
                   "and the related-party emphasis in App D's receivable-quality cluster.",
        inputs=("financial_guarantees_group", "total_equity"),
        window=2,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Financial guarantees given within an approved group treasury policy and disclosed.",
            "A financial guarantee supporting a subsidiary's project finance, sanctioned by the board.",
        ),
        notes=("A propriety and regularity lens applies for a Government company: the "
               "sanction for it, and the terms on which it was given, matter as much as its size.",),
    ),
    Signal(
        id="S25",
        layer=2,
        title="Investment concentration in total assets",
        spec_basis="EXTENSION (ROADMAP §16 decision 3). Nearest spec anchor: §7 structural "
                   "composition — what the asset base is actually made of.",
        inputs=("investments_total", "total_assets"),
        window=3,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(
            "A holding company whose business model IS the investment book — in which case "
            "the concentration is the business, and the lead is its valuation basis instead.",
            "Strategic government-directed holdings the entity does not choose or trade.",
        ),
        suppressed_for=frozenset({"corpus_holding", "banking", "nbfc", "insurance"}),
        notes=("Suppressed where holding investments IS the business model — flagging it there "
               "is the false positive §15.2 warns about.",),
    ),
    Signal(
        id="S26",
        layer=4,
        title="Fair-value carrying volatility through OCI",
        spec_basis="EXTENSION (ROADMAP §16 decision 3). Nearest spec anchor: §9.3 estimate "
                   "quality — how dependable the reported carrying amount is.",
        inputs=("oci_fair_value_movement", "total_equity"),
        window=3,
        availability=NOTE,
        default_severity=MEDIUM,
        alt_explanations=(
            "Ordinary market movement on listed holdings, observable and independently priced.",
            "A single large revaluation on first-time designation at fair value.",
        ),
        notes=("The lead is UNOBSERVABLE inputs, not volatility as such: a listed holding's "
               "swing is priced by a market, an unlisted one's is priced by a model.",),
    ),
    Signal(
        id="S27",
        layer=3,
        title="Investment income dependency",
        spec_basis="EXTENSION (ROADMAP §16 decision 3). Nearest spec anchor: §8 performance "
                   "decomposition — operating versus non-operating result.",
        inputs=("other_income", "pbt", "revenue"),
        window=3,
        availability=FACE,
        default_severity=MEDIUM,
        alt_explanations=(
            "A cash-rich entity earning ordinary treasury returns on surplus funds.",
            "Dividend income from strategic subsidiaries, recurring and predictable.",
            "A one-off gain on disposal that should not be read as a trend.",
        ),
        notes=("The FACE-derivable member of RC-INV, and what makes that cluster reachable "
               "before note extraction lands. Deliberately distinct from S19: S19 asks whether "
               "other income is SUSTAINABLE, this asks how much of the result DEPENDS on it.",),
    ),
)

BY_ID: dict[str, Signal] = {s.id: s for s in SIGNALS}


# ---- import-time validation ------------------------------------------------------
def _validate() -> None:
    seen: set[str] = set()
    for s in SIGNALS:
        if s.id in seen:
            raise ValueError(f"duplicate signal id {s.id}")
        seen.add(s.id)
        if s.layer not in (1, 2, 3, 4):
            raise ValueError(f"{s.id}: layer must be 1-4 (Layer 5 consumes, never produces)")
        if s.availability not in AVAILABILITY:
            raise ValueError(f"{s.id}: bad availability {s.availability!r}")
        if s.default_severity not in SEVERITIES:
            raise ValueError(f"{s.id}: bad severity {s.default_severity!r}")
        if s.window < 1:
            raise ValueError(f"{s.id}: window must be >= 1")
        if not s.inputs:
            raise ValueError(f"{s.id}: no inputs declared — the panel cannot be built for it")
        bad = s.suppressed_for - BUSINESS_MODELS
        if bad:
            raise ValueError(f"{s.id}: unknown business model(s) {sorted(bad)}")
        bad = s.not_applicable_frameworks - FRAMEWORKS
        if bad:
            raise ValueError(f"{s.id}: unknown framework(s) {sorted(bad)}")


_validate()
