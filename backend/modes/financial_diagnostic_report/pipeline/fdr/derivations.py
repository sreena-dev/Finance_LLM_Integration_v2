"""
How each signal is DERIVED, and where in the annual report its inputs live.

WHY THIS MODULE EXISTS
----------------------
`signals.py` says WHAT the twenty-two Appendix D signals are and which clusters consume
them. `rules.py` says whether one FIRED. Neither says how the figure was formed, which
schedule of the annual report it came from, or what stood in for a line the entity did not
disclose separately. That gap has two costs, and both of them land on the reviewer:

  - an ABSTAIN reads as "the system could not do it" when it should read "here is the
    derivation, here is the note to pull, and here is why it could not run this time";
  - a proxy is invisible. Payables days computed on cost of materials rather than on
    purchases is a defensible approximation and an indefensible silence.

So the derivation is declared as data, travels on every SignalResult whatever its status,
and renders in the report. A reviewer can check the arithmetic against the source without
reading a line of Python — which is what §16 explainability actually asks for.

WHAT A DERIVATION IS NOT
------------------------
It is not the rule. It does not threshold, fire, rate or decide anything. It is the stated
method and its provenance; `rules.py` remains the only place a verdict is formed, and the
only place a number is computed.

THE TWO-YEAR PROBLEM (§9.5, §11.1)
----------------------------------
An annual report carries the current period and one comparative — two points. Persistence
is a §11.1 priority dimension and a two-point movement is never a trend, so every trend
derivation below needs the two or three PRIOR annual reports as well. `window_years`
records how many, and the panel refuses a short series rather than truncating one.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from . import clusters as CL
from . import signals as SG
from .signals import FACE, NOTE

VERSION = "fdr-derivations-1.0.0"

SPEC = "SPEC"           # the specification or Schedule III states this derivation
PROPOSED = "PROPOSED"   # drafted here from audit practice, pending audit sign-off

# ---- where in the annual report ----------------------------------------------------
BS = "Balance Sheet"
PL = "Statement of Profit and Loss"
CFS = "Cash Flow Statement"
SOCE = "Statement of Changes in Equity"
NOTES = "Notes to the financial statements"
SCH3 = "Schedule III mandatory schedule"
POLICY = "Significant accounting policies / judgements and estimates"
MDA = "Management Discussion and Analysis / Board's Report"
ASSURANCE = "Auditor's report / CARO / C&AG comments"
EXTERNAL = "Outside the annual report"

PLACES = frozenset({BS, PL, CFS, SOCE, NOTES, SCH3, POLICY, MDA, ASSURANCE, EXTERNAL})

# Kinds of entry.
SIGNAL = "SIGNAL"            # derives one of the twenty-two Appendix D signals
SUPPORTING = "SUPPORTING"    # corroborates a cluster; not itself an Appendix D signal
CROSS = "CROSS_CUTTING"      # a read that conditions several derivations at once
KINDS = frozenset({SIGNAL, SUPPORTING, CROSS})


@dataclass(frozen=True)
class Source:
    """One place in the annual report an input is read from."""
    place: str                  # one of PLACES
    where: str                  # the specific statement line, note or schedule
    mandatory: bool = False     # a schedule Schedule III requires, so its absence is itself a fact

    def __str__(self) -> str:
        tag = " [mandatory schedule]" if self.mandatory else ""
        return f"{self.place} — {self.where}{tag}"


@dataclass(frozen=True)
class Proxy:
    """A stand-in for a line the annual report does not disclose separately.

    A proxy is always DISCLOSED and always costs confidence. The alternative — quietly
    substituting the nearest available figure — produces a number that looks measured and
    is not, which is the one failure mode `numeric_guard` and §16 both exist to prevent.
    """
    instead_of: str
    use: str
    why: str
    confidence_effect: str = "Caps the diagnostic one level below what its inputs would " \
                             "otherwise support."

    def __str__(self) -> str:
        return f"{self.instead_of} -> {self.use} ({self.why})"


@dataclass(frozen=True)
class Derivation:
    id: str                                   # signal id, or a D-* id for supporting reads
    title: str
    kind: str
    formula: str                              # the arithmetic, stated so it can be checked
    sources: tuple[Source, ...]
    window_years: int = 1                     # 1 = point in time; 3+ = needs prior ARs
    face_inputs: tuple[str, ...] = ()         # canonical keys the rule REQUIRES
    optional_inputs: tuple[str, ...] = ()     # used when bound; absence is disclosed, not fatal
    note_inputs: tuple[str, ...] = ()         # figures only a note can give
    proxies: tuple[Proxy, ...] = ()
    reconciling_items: tuple[str, ...] = ()   # eliminate these BEFORE the signal is raised
    cluster: str = ""                         # for SUPPORTING / CROSS entries
    origin: str = PROPOSED
    notes: tuple[str, ...] = ()

    @property
    def needs_prior_reports(self) -> int:
        """How many annual reports beyond the current one this derivation needs.

        One annual report yields two comparable years, so a three-year window needs one
        prior report and a five-year window needs three.
        """
        return max(0, self.window_years - 2)

    def source_lines(self) -> tuple[str, ...]:
        return tuple(str(s) for s in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "kind": self.kind,
            "formula": self.formula,
            "sources": [{"place": s.place, "where": s.where, "mandatory": s.mandatory}
                        for s in self.sources],
            "window_years": self.window_years,
            "prior_reports_required": self.needs_prior_reports,
            "face_inputs": list(self.face_inputs),
            "optional_inputs": list(self.optional_inputs),
            "note_inputs": list(self.note_inputs),
            "proxies": [{"instead_of": p.instead_of, "use": p.use, "why": p.why,
                         "confidence_effect": p.confidence_effect} for p in self.proxies],
            "reconciling_items": list(self.reconciling_items),
            "cluster": self.cluster, "origin": self.origin,
            "notes": list(self.notes),
        }


# ===================================================================================
# The twenty-two signal derivations, cluster by cluster.
# ===================================================================================

_SIGNAL_DERIVATIONS: tuple[Derivation, ...] = (

    # ---- RC-WC · working-capital and liquidity stress ------------------------------
    Derivation(
        id="S01",
        title="Deteriorating cash-conversion cycle",
        kind=SIGNAL,
        formula="CCC = DSO + inventory days - payables days, computed per year and compared "
                "across the window. DSO = trade receivables / revenue x 365; "
                "DIO = inventories / cost of goods sold x 365; "
                "DPO = trade payables / purchases x 365.",
        sources=(
            Source(BS, "Trade receivables, inventories, trade payables"),
            Source(NOTES, "The corresponding receivables, inventories and payables notes"),
            Source(PL, "Revenue from operations; cost of materials consumed; purchases of "
                       "stock-in-trade; changes in inventories"),
        ),
        window_years=3,
        face_inputs=("trade_receivables", "inventories", "trade_payables", "revenue",
                     "cost_of_materials_consumed"),
        optional_inputs=("purchases_of_stock_in_trade", "changes_in_inventories"),
        proxies=(
            Proxy(instead_of="Purchases, as the payables-days denominator",
                  use="Cost of materials consumed, adjusted for the change in inventories "
                      "where that line is bound; otherwise cost of materials consumed alone",
                  why="Purchases are rarely disclosed as a separate line in an Indian annual "
                      "report, so the cost-of-goods-sold proxy is the practical denominator"),
        ),
        origin=SPEC,
        notes=("§9.2 requires working capital to be read as an interacting system: the three "
               "legs move against each other and a lengthening cycle can come from any of "
               "them, so all three days-figures are reported, not only the total.",),
    ),

    Derivation(
        id="S02",
        title="Payables funding growth",
        kind=SIGNAL,
        formula="Two tests, both stated. (a) The movement in trade payables against the "
                "movement in revenue and against the movement in (trade receivables + "
                "inventories) across the window. (b) Payables days rising while DSO and DIO "
                "are flat — the pattern that separates supplier funding from a general "
                "expansion of working capital.",
        sources=(
            Source(BS, "Trade payables"),
            Source(SCH3, "Trade payables ageing schedule", mandatory=True),
            Source(NOTES, "Disclosure of dues to micro and small enterprises, including "
                          "interest payable under section 22 of the MSMED Act, 2006"),
            Source(PL, "Revenue from operations; cost of materials consumed"),
            Source(BS, "Trade receivables and inventories, for the offsetting comparison"),
        ),
        window_years=3,
        face_inputs=("trade_payables", "cost_of_materials_consumed", "revenue",
                     "trade_receivables", "inventories"),
        optional_inputs=("ocf", "total_assets"),
        note_inputs=("msmed_overdue_dues", "payables_ageing_buckets"),
        origin=SPEC,
        notes=("The MSMED section 22 disclosure of delayed payment is a REGULARITY matter in "
               "its own right, not only a liquidity one, and reaches the §11.2 by-nature "
               "override even where the amount is small.",),
    ),

    Derivation(
        id="S03",
        title="Net current-liability position",
        kind=SIGNAL,
        formula="Current assets - current liabilities, on the Schedule III current / "
                "non-current split as presented, with current maturities of long-term debt "
                "stated separately so the reader can see whether they sit inside the printed "
                "current-liabilities total or outside it.",
        sources=(
            Source(BS, "Total current assets; total current liabilities, per the Schedule III "
                       "current / non-current classification"),
            Source(NOTES, "Borrowings note — current maturities of long-term debt and their "
                          "presentation"),
        ),
        window_years=1,
        face_inputs=("total_current_assets", "total_current_liabilities"),
        optional_inputs=("current_maturities_ltd",),
        origin=SPEC,
        notes=("Current maturities are DISCLOSED, never added. Under the Ind AS Schedule III "
               "format they are already presented within current financial liabilities, so "
               "adding them again would double-count. Where the presentation cannot be "
               "settled from the filing, the position is stated both ways and neither is "
               "chosen.",),
    ),

    Derivation(
        id="S04",
        title="Weak or negative operating cash flow",
        kind=SIGNAL,
        formula="OCF / PAT across the window, and separately operating cash flow BEFORE "
                "working-capital changes against PAT — the pair that separates an earnings "
                "problem from a working-capital problem.",
        sources=(
            Source(CFS, "Net cash generated from operating activities; and the sub-total "
                        "'operating profit before working capital changes' in the "
                        "indirect-method reconciliation"),
            Source(PL, "Profit for the year"),
        ),
        window_years=3,
        face_inputs=("ocf", "pat"),
        optional_inputs=("ocf_before_wc",),
        proxies=(
            Proxy(instead_of="Operating cash flow before working-capital changes",
                  use="The headline OCF alone, with the split stated as unavailable",
                  why="The pre-working-capital sub-total is a line inside the indirect-method "
                      "reconciliation and is not always bound by the fact layer",
                  confidence_effect="Does not cap confidence in the OCF/PAT reading itself; "
                                    "the attribution between earnings and working capital is "
                                    "reported as not formed."),
        ),
        origin=SPEC,
        notes=("§9.1 warns against reading this mechanically, which is why the signal is "
               "suppressed for a growth-phase infrastructure builder.",),
    ),

    # ---- RC-REC · revenue and receivable quality -----------------------------------
    Derivation(
        id="S05",
        title="Receivables outpacing revenue",
        kind=SIGNAL,
        formula="Growth in trade receivables against growth in revenue from operations over "
                "the window, reported alongside the DSO trend so a gap driven by a single "
                "year's base effect is distinguishable from a sustained lengthening.",
        sources=(
            Source(BS, "Trade receivables, both years"),
            Source(PL, "Revenue from operations, both years"),
            Source(EXTERNAL, "The two or three preceding annual reports — the current filing "
                             "gives two points, and two points are not a trend (§9.5)"),
        ),
        window_years=3,
        face_inputs=("trade_receivables", "revenue"),
        optional_inputs=("total_assets",),
        origin=SPEC,
        notes=("Suppressed for a tariff-based power utility (§20 worked example): the correct "
               "test there is whether the trend exceeds the sector-normal pattern, and no "
               "sector-normal value is assumed or fabricated.",),
    ),

    Derivation(
        id="S06",
        title="Accruals-heavy earnings",
        kind=SIGNAL,
        formula="(PAT - OCF) / AVERAGE total assets, per year across the window. The "
                "denominator is the average of opening and closing total assets, because a "
                "flow in the numerator divided by a point-in-time balance is not a rate.",
        sources=(
            Source(PL, "Profit for the year"),
            Source(CFS, "Net cash generated from operating activities"),
            Source(BS, "Total assets, opening and closing"),
        ),
        window_years=3,
        face_inputs=("pat", "ocf", "total_assets"),
        origin=SPEC,
        notes=("Averaging costs one year of the window: the oldest year has no opening "
               "balance inside the series and so yields no ratio. The rule reports on the "
               "years it can form rather than falling back to a closing-balance denominator.",),
    ),

    Derivation(
        id="S07",
        title="Period-end revenue concentration",
        kind=SIGNAL,
        formula="NOT DERIVABLE FROM THE ANNUAL REPORT ALONE. The report presents twelve "
                "months in one column; nothing in it splits revenue by period. Proxy weakly "
                "via the movement in unbilled revenue / contract assets, or use quarterly "
                "results where the entity is listed. Otherwise this is recorded as an "
                "EVIDENCE REQUEST, not computed as a signal.",
        sources=(
            Source(NOTES, "Contract assets / unbilled revenue, and their movement"),
            Source(EXTERNAL, "SEBI quarterly results, where the entity is listed"),
            Source(EXTERNAL, "Month-wise or quarter-wise sales ledger — an evidence request "
                             "on the entity, not a disclosure"),
        ),
        window_years=1,
        note_inputs=("contract_assets", "unbilled_revenue", "quarterly_revenue"),
        origin=SPEC,
        notes=("Stating this as an evidence request rather than an abstain is the honest "
               "output: the figure does not exist in the source, so no amount of extraction "
               "work would produce it.",),
    ),

    Derivation(
        id="S08",
        title="Related-party receivable concentration",
        kind=SIGNAL,
        formula="Related-party receivables / total trade receivables, together with whether "
                "any allowance is carried against those balances — a concentration with no "
                "provision against it is a different lead from one with a provision.",
        sources=(
            Source(NOTES, "Ind AS 24 related-party disclosures — the outstanding balances "
                          "section, and any provision held against related-party balances"),
            Source(BS, "Trade receivables, as the denominator"),
        ),
        window_years=1,
        face_inputs=("trade_receivables",),
        note_inputs=("related_party_receivables", "related_party_provision"),
        origin=SPEC,
        notes=("Material by nature under §2.4 even at low value: intra-government trading is "
               "normal for a CPSU, so the lead is the pricing basis and the recoverability "
               "assessment, not the existence of the balance.",),
    ),

    # ---- RC-CAP · asset and capitalisation risk ------------------------------------
    Derivation(
        id="S09",
        title="Ageing capital work-in-progress",
        kind=SIGNAL,
        formula="Share of CWIP sitting in the 'more than 2 years' and 'more than 3 years' "
                "ageing buckets, and the movement of those buckets year on year. Read "
                "together with the companion table of projects overdue against their "
                "original plan or exceeding their original cost.",
        sources=(
            Source(SCH3, "Capital work-in-progress ageing schedule — amount in CWIP for a "
                         "period of less than 1 year / 1-2 years / 2-3 years / more than 3 "
                         "years", mandatory=True),
            Source(SCH3, "CWIP whose completion is overdue or has exceeded its original cost, "
                         "with the project-wise completion schedule", mandatory=True),
            Source(SCH3, "The same pair of schedules for intangible assets under development",
                   mandatory=True),
            Source(BS, "Capital work-in-progress and property, plant and equipment, for the "
                       "share"),
        ),
        window_years=2,
        face_inputs=("cwip", "net_fixed_assets"),
        note_inputs=("cwip_ageing_buckets", "cwip_overdue_projects"),
        origin=SPEC,
        notes=("The CWIP SHARE and its drift are derivable from the face of the balance "
               "sheet; the AGEING that makes it a signal is note-level and is what the "
               "mandatory schedule supplies.",
               "For an oil and gas entity the generic ageing read must be modified — see "
               "the sector overlay entry OV-OG.",),
    ),

    Derivation(
        id="S10",
        title="Rising non-current-other share",
        kind=SIGNAL,
        formula="'Other non-current assets' plus 'other non-current financial assets' as a "
                "percentage of total assets, common-sized and compared across the window. "
                "The signal is the drift in the share, not its level.",
        sources=(
            Source(BS, "Other non-current assets; other financial assets (non-current); "
                       "total assets"),
            Source(NOTES, "The composition of the other-non-current-assets note, which is "
                          "what turns a share movement into a lead"),
        ),
        window_years=3,
        face_inputs=("other_non_current_assets", "total_assets"),
        optional_inputs=("other_financial_assets_nc",),
        proxies=(
            Proxy(instead_of="Other non-current assets plus other non-current financial assets",
                  use="Other non-current assets alone",
                  why="The two lines are presented separately under the Ind AS format and "
                      "only the first is bound today; the share is therefore a lower bound",
                  confidence_effect="Stated as a lower bound on the share. The direction of "
                                    "the drift is unaffected; its size is understated."),
        ),
        origin=SPEC,
    ),

    Derivation(
        id="S11",
        title="Depreciation not moving with the asset base",
        kind=SIGNAL,
        formula="Implied depreciation rate = depreciation and amortisation / average GROSS "
                "block, per year. The implied rate is then compared against the useful lives "
                "disclosed in the accounting policy — a rate materially below the disclosed "
                "lives is the lead, not the rate itself.",
        sources=(
            Source(NOTES, "Property, plant and equipment movement table — gross carrying "
                          "amount, additions, disposals, accumulated depreciation"),
            Source(PL, "Depreciation and amortisation expense"),
            Source(POLICY, "Depreciation policy and the useful lives adopted, and whether "
                           "they follow Schedule II or a technical assessment"),
            Source(BS, "Capital work-in-progress, which carries no depreciation and must be "
                       "excluded from the base"),
        ),
        window_years=3,
        face_inputs=("depreciation", "net_fixed_assets"),
        optional_inputs=("gross_block", "cwip"),
        proxies=(
            Proxy(instead_of="Average gross block",
                  use="Average NET block (net fixed assets), excluding CWIP where bound",
                  why="The gross carrying amount lives in the PPE movement table, not on the "
                      "face of the balance sheet, and is not bound by the fact layer today",
                  confidence_effect="Caps the diagnostic at MEDIUM. A net-block denominator "
                                    "overstates the implied rate and drifts upward as the "
                                    "asset base ages, so the LEVEL is not comparable to a "
                                    "disclosed useful life; only the trend is read."),
        ),
        origin=SPEC,
    ),

    Derivation(
        id="S12",
        title="Impairment timing",
        kind=SIGNAL,
        formula="Whether an impairment charge or reversal lags an observable trigger — a "
                "price collapse, idle capacity, a stalled project, a regulatory change — "
                "read against the cash-generating-unit assumptions and discount rates "
                "disclosed. There is no ratio here: the derivation is a sequencing test "
                "between a disclosed trigger and a disclosed charge.",
        sources=(
            Source(NOTES, "Impairment note — charge and reversal by class of asset"),
            Source(NOTES, "Cash-generating-unit disclosures: recoverable amount, basis, "
                          "discount rate, terminal growth, key assumptions and sensitivity"),
            Source(MDA, "The operational narrative that would carry the trigger — capacity "
                        "utilisation, realisations, project status"),
            Source(PL, "Exceptional items"),
        ),
        window_years=3,
        face_inputs=("net_fixed_assets",),
        note_inputs=("impairment_charge", "impairment_reversal", "cgu_assumptions",
                     "discount_rate"),
        origin=SPEC,
        notes=("§9.3 wording discipline is mandatory: this is an estimate-quality risk "
               "indicator warranting assumption testing. No inference about intent is drawn "
               "or reported.",),
    ),

    # ---- RC-FUND · funding and solvency risk ---------------------------------------
    Derivation(
        id="S13",
        title="Leverage-driven return on equity",
        kind=SIGNAL,
        formula="DuPont: ROE = net margin x asset turnover x equity multiplier, with EACH "
                "factor traced separately across every year of the window rather than only "
                "at its endpoints. The attribution of an ROE movement across the three "
                "factors is taken in log space, so a multiplicative decomposition becomes "
                "additive and the shares sum to one.",
        sources=(
            Source(PL, "Profit for the year; revenue from operations"),
            Source(BS, "Total assets; total equity"),
            Source(SOCE, "Movements in equity that change the multiplier without changing "
                         "performance — buy-back, bonus, fair-value and actuarial reserves"),
        ),
        window_years=3,
        face_inputs=("pat", "revenue", "total_assets", "total_equity"),
        origin=SPEC,
        notes=("§8.2 — always decompose before flagging. The decomposition is what moves an "
               "ROE improvement from a performance story to a solvency one, and therefore "
               "changes which cluster the signal joins. It cannot be an afterthought.",),
    ),

    Derivation(
        id="S14",
        title="Short-term funding of long-term assets",
        kind=SIGNAL,
        formula="Two tests. (a) The movement in non-current assets against the movement in "
                "(total equity + non-current borrowings): long-term assets growing faster "
                "than long-term funding means the difference was funded short. (b) Current "
                "borrowings, INCLUDING current maturities of long-term debt, as a share of "
                "total debt, and the movement in that share.",
        sources=(
            Source(BS, "Total non-current assets; total equity; non-current borrowings; "
                       "current borrowings"),
            Source(NOTES, "Borrowings note — the maturity profile, current maturities of "
                          "long-term debt, and any sanctioned but undrawn facility"),
        ),
        window_years=2,
        face_inputs=("total_non_current_assets", "total_equity", "long_term_borrowings",
                     "short_term_borrowings"),
        optional_inputs=("current_maturities_ltd", "total_current_assets",
                         "total_current_liabilities"),
        origin=SPEC,
        notes=("Cross-dimensional by construction: it needs the funding movement and the "
               "asset movement in the same year, which is why its window is two and not one.",),
    ),

    Derivation(
        id="S15",
        title="Finance cost not moving with borrowings",
        kind=SIGNAL,
        formula="Implied borrowing rate = finance cost / AVERAGE total borrowings, per year, "
                "compared against the interest-rate ranges disclosed in the borrowings note. "
                "The growth divergence between finance cost and debt is reported alongside "
                "it, because a rate that is stable while both move is a different reading "
                "from one that is not.",
        sources=(
            Source(PL, "Finance costs, and the 'less: amount capitalised' deduction within it"),
            Source(BS, "Non-current and current borrowings, opening and closing"),
            Source(NOTES, "Borrowings note — interest rates, terms of repayment, security, "
                          "and the maturity profile"),
        ),
        window_years=3,
        face_inputs=("finance_costs", "long_term_borrowings", "short_term_borrowings"),
        optional_inputs=("total_assets", "current_maturities_ltd", "lease_liabilities_nc",
                         "lease_liabilities_cl", "borrowing_cost_capitalised"),
        reconciling_items=(
            "Borrowing cost capitalised into qualifying assets, which removes cost from the "
            "profit and loss without removing debt from the balance sheet.",
            "Unwinding of the discount on decommissioning and site-restoration provisions, "
            "which sits in finance cost and services no borrowing.",
            "Exchange differences on foreign-currency borrowings regarded as an adjustment "
            "to interest cost.",
            "Interest on lease liabilities under Ind AS 116, where lease liabilities are not "
            "inside the borrowings denominator.",
        ),
        origin=SPEC,
        notes=("Every reconciling item above is to be eliminated BEFORE the divergence is "
               "raised. Capitalised borrowing cost is simultaneously the most common benign "
               "explanation here and a capitalisation-risk lead for RC-CAP — the interaction "
               "is the point (§10.2).",),
    ),

    # ---- RC-EST · estimate and reporting-quality risk ------------------------------
    Derivation(
        id="S16",
        title="Provision volatility and reversals",
        kind=SIGNAL,
        formula="Reversals / opening balance, computed PER PROVISION CLASS and per year. A "
                "netted total across classes hides the pattern the signal exists to find: "
                "one class reversed while another is charged nets to nothing and is not "
                "nothing.",
        sources=(
            Source(NOTES, "Ind AS 37 provisions movement table — opening balance, additions, "
                          "amounts used, unused amounts reversed, unwinding of discount, "
                          "closing balance, by class of provision"),
            Source(POLICY, "The judgements and estimates note, which is the entity's own "
                           "statement of where its estimate exposure lies"),
            Source(NOTES, "Contingent liabilities, for matters moving between contingent and "
                          "provided"),
        ),
        window_years=3,
        note_inputs=("provisions_opening", "provisions_charge", "provisions_reversal",
                     "provisions_closing", "provision_class"),
        origin=SPEC,
        notes=("§17.1 wording is strictest here: an estimate-quality risk indicator "
               "warranting assumption testing, with no inference about intent.",),
    ),

    Derivation(
        id="S17",
        title="Useful-life changes",
        kind=SIGNAL,
        formula="Any change in an accounting estimate disclosed under Ind AS 8, and the "
                "QUANTIFIED effect on the current year's charge that the standard requires "
                "with it. The disclosure without the quantification is itself the lead.",
        sources=(
            Source(NOTES, "Ind AS 8 change-in-accounting-estimate disclosure and its "
                          "quantified effect"),
            Source(POLICY, "Property, plant and equipment policy — useful lives, residual "
                           "values, and whether they follow Schedule II or a technical "
                           "assessment"),
            Source(NOTES, "PPE movement table, where the effect lands"),
        ),
        window_years=2,
        note_inputs=("useful_life_disclosure", "estimate_change_effect"),
        origin=SPEC,
    ),

    Derivation(
        id="S18",
        title="Non-cash gains",
        kind=SIGNAL,
        formula="Fair-value gains, provision and liability write-backs, exchange gains and "
                "profits on disposal, as a share of PROFIT BEFORE TAX. The same items appear "
                "as non-cash add-backs in the indirect-method cash-flow reconciliation, and "
                "the two presentations are required to agree.",
        sources=(
            Source(NOTES, "Other income note — the item-by-item breakdown"),
            Source(CFS, "The non-cash adjustments in the indirect-method reconciliation: "
                        "fair-value changes, liabilities written back, profit on disposal, "
                        "unrealised exchange differences"),
            Source(PL, "Profit before tax; exceptional items"),
        ),
        window_years=3,
        face_inputs=("other_income", "pbt", "ocf"),
        optional_inputs=("pat",),
        proxies=(
            Proxy(instead_of="Fair-value gains + write-backs + exchange gains + disposal "
                             "profits",
                  use="Total other income, as the outer bound on the non-cash component",
                  why="The item-by-item split lives in the other-income note and is not bound "
                      "by the fact layer today; total other income cannot be smaller than the "
                      "non-cash part of it",
                  confidence_effect="Caps the diagnostic at MEDIUM and makes the share an "
                                    "UPPER bound. Interest and dividend income are cash and "
                                    "are inside this proxy, so the measure overstates."),
        ),
        origin=SPEC,
    ),

    Derivation(
        id="S19",
        title="Other-income sustainability",
        kind=SIGNAL,
        formula="Other income / profit before tax, split into recurring (interest, dividend, "
                "rent) and non-recurring (write-backs, disposals, one-off claims). The share "
                "of total income and the growth of other income against total income are "
                "reported with it, because a large recurring stream and a large one-off are "
                "the same number and different leads.",
        sources=(
            Source(NOTES, "Other income note — the item-by-item breakdown"),
            Source(PL, "Total income; revenue from operations; profit before tax"),
        ),
        window_years=3,
        face_inputs=("other_income", "total_income"),
        optional_inputs=("pbt",),
        proxies=(
            Proxy(instead_of="The recurring / non-recurring split of other income",
                  use="The unsplit total, with the split reported as not formed",
                  why="The split requires the other-income note line by line; the face of the "
                      "profit and loss carries one figure",
                  confidence_effect="The share and its growth stand; the sustainability "
                                    "judgement itself is reported as requiring the note."),
        ),
        origin=SPEC,
        notes=("Suppressed for a corpus-holding body, an NBFC, a bank or an insurer, whose "
               "income IS investment income.",),
    ),

    # ---- RC-DEP · government-dependency and grant risk -----------------------------
    Derivation(
        id="S20",
        title="High government-support dependency",
        kind=SIGNAL,
        formula="Dependency index = (government grants recognised in income + subsidies + "
                "budgetary support) / total income, per year, with the three components "
                "shown separately. Reported as an index with its components, never as a bare "
                "score (§9.4).",
        sources=(
            Source(NOTES, "Ind AS 20 government grants note — grants recognised in the "
                          "statement of profit and loss, and grants deducted from an expense"),
            Source(NOTES, "Revenue note and other income note, where subsidy is presented as "
                          "part of revenue"),
            Source(CFS, "Government grant received, as a financing or operating line"),
            Source(PL, "Total income, as the denominator"),
        ),
        window_years=3,
        face_inputs=("total_income",),
        optional_inputs=("government_grant_cf", "other_income"),
        note_inputs=("grants_in_income", "subsidies", "budgetary_support"),
        proxies=(
            Proxy(instead_of="Grants + subsidies + budgetary support recognised in income",
                  use="The government-grant line in the cash flow statement",
                  why="The three components are presented across the revenue note, the other "
                      "income note and the Ind AS 20 note; only the cash-flow line is bound "
                      "by the fact layer today",
                  confidence_effect="Caps the diagnostic at LOW and makes the index a "
                                    "PARTIAL measure: grants received in cash and grants "
                                    "recognised in income are different figures, and subsidy "
                                    "presented inside revenue is not captured at all."),
        ),
        origin=SPEC,
        notes=("Structural to the entity type. A grant-funded body is dependent by design, so "
               "the lead is a change in the DEGREE of dependency, not its existence.",),
    ),

    Derivation(
        id="S21",
        title="Unspent-grant build-up",
        kind=SIGNAL,
        formula="The deferred-grant / unutilised-grant liability balance and its movement, "
                "and that balance as a share of grants received in the year. A balance "
                "growing faster than receipts means utilisation is lagging sanction.",
        sources=(
            Source(BS, "Deferred government grant — non-current and current portions; "
                       "other current liabilities where unutilised grant is carried there"),
            Source(NOTES, "Ind AS 20 government grants note — opening balance, received, "
                          "recognised in income, closing balance"),
            Source(CFS, "Grants received during the year"),
        ),
        window_years=3,
        note_inputs=("unspent_grant_balance", "grants_received", "grants_utilised"),
        origin=SPEC,
        notes=("§17.1 wording: a utilisation and possible-diversion-RISK indicator requiring "
               "utilisation certificates and sanction orders as corroboration. The stronger "
               "word is prohibited and the lint enforces it.",),
    ),

    Derivation(
        id="S22",
        title="Administered-pricing reliance",
        kind=SIGNAL,
        formula="The share of revenue earned at an administered, notified or tariff-set price "
                "rather than a market price. This is a Layer 1 classification read off the "
                "revenue policy and disaggregation, not a computation.",
        sources=(
            Source(POLICY, "Revenue recognition policy — the pricing basis, and any "
                           "regulatory deferral account"),
            Source(NOTES, "Ind AS 115 disaggregation of revenue, by product and by the "
                          "timing and basis of recognition"),
            Source(MDA, "The narrative on tariff orders, notified prices, subsidy schemes "
                        "and regulatory determinations"),
        ),
        window_years=1,
        note_inputs=("revenue_by_pricing_basis", "revenue_model"),
        origin=SPEC,
        notes=("Falls out of business understanding (ROADMAP M5), not out of a computation. "
               "It conditions how S05 and S20 are read rather than firing on its own.",),
    ),

    # ---- EXTENSION derivations, S23-S27. See the extension block in `signals.py`. -----
    Derivation(
        id="S23",
        title="Contingent liabilities large relative to net worth",
        kind=SIGNAL,
        formula="Total contingent liabilities disclosed / total equity, and the year-on-year "
                "movement in that ratio. Reported per CLASS where the note gives one — tax, "
                "contractual claims, financial guarantees given — because one netted figure hides "
                "which exposure is growing.",
        sources=(
            Source(NOTES, "Contingent liabilities and commitments note, by class of matter"),
            Source(NOTES, "Provisions note, for matters that moved between provided and "
                          "contingent during the year"),
            Source(BS, "Total equity, as the comparison base"),
        ),
        window_years=2,
        face_inputs=("total_equity",),
        note_inputs=("contingent_liabilities_total",),
        reconciling_items=(
            "Commitments (capital and other) are NOT contingent liabilities and must be "
            "excluded — they are contracted obligations, not possible ones.",
            "Matters where the entity is the claimant rather than the defendant.",
        ),
        origin=PROPOSED,
        notes=("The lead is the ratio and its MOVEMENT. Every entity of scale discloses "
               "contingent liabilities; their existence is not a signal.",),
    ),
    Derivation(
        id="S24",
        title="Guarantees issued for group entities",
        kind=SIGNAL,
        formula="Financial guarantees given on behalf of subsidiaries, associates and joint "
                "ventures / total equity, with the movement in the year.",
        sources=(
            Source(NOTES, "Contingent liabilities note — guarantees given"),
            Source(NOTES, "Related-party disclosures (Ind AS 24), for the counterparties"),
        ),
        window_years=2,
        face_inputs=("total_equity",),
        note_inputs=("financial_guarantees_group",),
        origin=PROPOSED,
        notes=("Carries a propriety lens for a Government company: the sanction for it, and "
               "the terms on which it was given, matter as much as its size.",),
    ),
    Derivation(
        id="S25",
        title="Investment concentration in total assets",
        kind=SIGNAL,
        formula="(Non-current investments + current investments) / total assets, as a trend. "
                "Reported with the split between quoted and unquoted, because an unquoted "
                "concentration is a valuation exposure and a quoted one largely is not.",
        sources=(
            Source(NOTES, "Investments note — non-current and current, by instrument"),
            Source(NOTES, "Fair-value hierarchy disclosure (Ind AS 113), for the level split"),
            Source(BS, "Total assets, as the comparison base"),
        ),
        window_years=3,
        face_inputs=("total_assets",),
        optional_inputs=("marketable_securities",),
        note_inputs=("investments_total",),
        proxies=(
            Proxy(instead_of="investments_total",
                  use="current investments (`marketable_securities`) alone, where the "
                      "non-current investments note cannot be read",
                  why="the current-investment line IS bound on the face for most filers, so a "
                      "partial read is available where the full one is not",
                  confidence_effect="capped at LOW, and the partial basis is stated on the row: "
                                    "it understates concentration, so it can only ever "
                                    "understate the signal"),
        ),
        origin=PROPOSED,
    ),
    Derivation(
        id="S26",
        title="Fair-value carrying volatility through OCI",
        kind=SIGNAL,
        formula="Absolute fair-value movement recognised in other comprehensive income / "
                "total equity, per year across the window. The movement is taken ABSOLUTE, because "
                "a gain reversing a prior loss is volatility, not recovery.",
        sources=(
            Source(SOCE, "Other comprehensive income — items that will not be reclassified, "
                         "fair-value changes on equity instruments"),
            Source(NOTES, "Fair-value hierarchy and the level 3 reconciliation"),
        ),
        window_years=3,
        face_inputs=("total_equity",),
        note_inputs=("oci_fair_value_movement",),
        origin=PROPOSED,
        notes=("The lead is UNOBSERVABLE inputs, not volatility as such — a listed holding's "
               "swing is priced by a market, an unlisted one's by a model.",),
    ),
    Derivation(
        id="S27",
        title="Investment income dependency",
        kind=SIGNAL,
        formula="Other income / profit before tax, as a trend, read together with other "
                "income / revenue. Where PBT is small or negative the ratio is not reported "
                "as a percentage — it is stated as 'other income exceeds the operating "
                "result', which is the finding, not a number that would divide by near-zero.",
        sources=(
            Source(PL, "Other income"),
            Source(PL, "Profit before tax"),
            Source(NOTES, "Other income note — the split between interest, dividend and "
                          "gains, which is what distinguishes recurring from one-off"),
        ),
        window_years=3,
        face_inputs=("other_income", "pbt", "revenue"),
        reconciling_items=(
            "One-off disposal gains, which must be identified from the other-income note "
            "before any trend is read.",
        ),
        origin=PROPOSED,
        notes=("Face-derivable, which is what makes RC-INV reachable before note extraction. "
               "Distinct from S19: S19 asks whether other income is SUSTAINABLE, this asks how "
               "much of the reported result DEPENDS on it.",),
    ),
)


# ===================================================================================
# Supporting derivations — corroborate a cluster, but are not Appendix D signals.
#
# These are kept OUTSIDE the signal registry on purpose. `clusters.py` validates that
# every signal is consumed by a cluster and every cluster reaches at least one face
# signal; adding an entry to that closed set changes what the system flags for every
# entity and is an audit-methodology decision (ROADMAP §16, decision 3). A supporting
# derivation carries its method and its source, is reported against its cluster, and
# raises nothing by itself.
# ===================================================================================

_SUPPORTING: tuple[Derivation, ...] = (

    Derivation(
        id="D-ECL",
        title="Provisioning adequacy against the receivables ageing",
        kind=SUPPORTING,
        cluster="RC-REC",
        formula="Allowance for expected credit loss / trade receivables sitting in the "
                "'more than 2 years' and 'more than 3 years' ageing buckets. An allowance "
                "materially below the long-aged balance is what makes a receivables "
                "concentration a valuation lead rather than a collection one.",
        sources=(
            Source(SCH3, "Trade receivables ageing schedule — outstanding for less than 6 "
                         "months / 6 months-1 year / 1-2 years / 2-3 years / more than 3 "
                         "years, split between undisputed and disputed", mandatory=True),
            Source(NOTES, "Movement in the allowance for expected credit loss, and the ECL "
                          "matrix or provisioning policy applied"),
        ),
        window_years=2,
        face_inputs=("trade_receivables",),
        note_inputs=("ecl_allowance", "receivables_ageing_buckets"),
        notes=("The disputed column of the mandatory schedule is a lead in its own right and "
               "is reported separately from the aged-undisputed balance.",),
    ),

    Derivation(
        id="D-CAPBC",
        title="Capitalisation behaviour — borrowing cost capitalised",
        kind=SUPPORTING,
        cluster="RC-CAP",
        formula="Borrowing cost capitalised / total finance cost before capitalisation, and "
                "the movement in the capitalisation rate disclosed under Ind AS 23. A rising "
                "capitalised share while CWIP ages is the interaction that matters.",
        sources=(
            Source(NOTES, "Finance cost note — the 'less: amount capitalised' deduction and "
                          "the capitalisation rate applied"),
            Source(NOTES, "Ind AS 23 disclosure of borrowing costs capitalised during the "
                          "period"),
            Source(BS, "Capital work-in-progress, where the capitalised cost lands"),
        ),
        window_years=3,
        face_inputs=("finance_costs", "cwip"),
        note_inputs=("borrowing_cost_capitalised", "capitalisation_rate"),
        notes=("Reinforces S15 and S09 simultaneously: it is the benign explanation for one "
               "and a risk lead for the other, which is why it is reported rather than used "
               "to silence either.",),
    ),

    Derivation(
        id="D-COV",
        title="Covenant and default exposure",
        kind=SUPPORTING,
        cluster="RC-FUND",
        formula="Any breach, default, restructuring, or delay in repayment of principal or "
                "interest disclosed. Not a ratio: a disclosed default is a fact, and its "
                "absence from the borrowings note while the auditor's report or CARO raises "
                "it is itself the lead.",
        sources=(
            Source(NOTES, "Borrowings note — terms of repayment, security, covenants, and "
                          "any continuing default in repayment"),
            Source(ASSURANCE, "CARO clause on default in repayment of loans or borrowings, "
                              "and on wilful-defaulter declaration"),
            Source(ASSURANCE, "The auditor's report — emphasis of matter, key audit matters, "
                              "and any material uncertainty related to going concern"),
            Source(SCH3, "Disclosure of registration of charges or satisfaction with the "
                         "Registrar of Companies", mandatory=True),
        ),
        window_years=1,
        note_inputs=("covenant_breach", "repayment_default", "caro_default_clause"),
        notes=("Under §12 the auditor's report and CARO are CORROBORATION inputs, not FDR "
               "inputs. This entry records where the corroboration is read from; it does not "
               "make the assurance documents part of the diagnostic base.",),
    ),

    Derivation(
        id="D-GRANTCOND",
        title="Grant-condition exposure",
        kind=SUPPORTING,
        cluster="RC-DEP",
        formula="The conditions attached to each grant, and any contingency arising from a "
                "condition — refund on non-utilisation, restriction on the purpose, or a "
                "reporting obligation to the sanctioning authority.",
        sources=(
            Source(NOTES, "Ind AS 20 government grants note — the conditions attached and "
                          "whether they have been met"),
            Source(NOTES, "Contingent liabilities — any condition-related refund or claim"),
            Source(ASSURANCE, "C&AG comments under section 143(6) of the Companies Act, 2013"),
        ),
        window_years=1,
        note_inputs=("grant_conditions", "grant_contingency"),
        notes=("Condition compliance is a REGULARITY matter, not a financial-statement "
               "assertion, and is carried as such on the RC-DEP package.",),
    ),
)


# ===================================================================================
# Cross-cutting reads — they condition several derivations rather than producing one.
# ===================================================================================

_CROSS: tuple[Derivation, ...] = (

    Derivation(
        id="X-SCH3RATIO",
        title="The Schedule III ratio note and its variance explanations",
        kind=CROSS,
        formula="Eleven ratios are already computed and presented by the entity, with a "
                "MANDATORY explanation of any variance above 25% against the prior year. "
                "Read the explanation as management's own attribution of the movement — a "
                "LEAD TO TEST, never evidence in itself (§2.3).",
        sources=(
            Source(SCH3, "The ratio note: current ratio, debt-equity, debt service coverage, "
                         "return on equity, inventory turnover, trade receivables turnover, "
                         "trade payables turnover, net capital turnover, net profit ratio, "
                         "return on capital employed, return on investment — with the "
                         "explanation for any variance above 25%", mandatory=True),
        ),
        window_years=2,
        origin=SPEC,
        notes=("The FDR does not re-derive this set — Appendix G is already implemented in "
               "fs_db/appendix_g.py and Appendix A forbids reproducing the FSA ratio library. "
               "What the FDR consumes is the VARIANCE EXPLANATION, which is an input no ratio "
               "engine produces.",),
    ),

    Derivation(
        id="X-MDA",
        title="Physical drivers from the MD&A and Board's Report",
        kind=CROSS,
        formula="Volumes, realisations, capacity utilisation and installed capacity — the "
                "physical quantities a margin bridge needs in order to separate a price "
                "effect from a volume effect.",
        sources=(
            Source(MDA, "Operational review — production and sales volumes, average "
                        "realisation, capacity utilisation, plant availability"),
            Source(MDA, "Segment-wise performance and outlook"),
            Source(NOTES, "Ind AS 108 segment disclosures, where the segments align"),
        ),
        window_years=2,
        origin=SPEC,
        notes=("Without these, the §8.2 price-versus-volume decomposition CANNOT RUN, and the "
               "report is required to say so rather than presenting an undecomposed margin "
               "movement as though it were attributed.",),
    ),

    Derivation(
        id="X-ASSURANCE",
        title="Auditor's report, CARO and C&AG supplementary comments",
        kind=CROSS,
        formula="Not an FDR input. Under §12 these are CORROBORATION: each FDR lead is "
                "classified against them as corroborated, extended, contradicted, "
                "independent or superseded. A contradiction is retained with both positions "
                "and their sources, and referred — it is never averaged away.",
        sources=(
            Source(ASSURANCE, "The statutory auditor's report — opinion, basis, emphasis of "
                              "matter, key audit matters, going-concern paragraph"),
            Source(ASSURANCE, "The CARO annexure, clause by clause"),
            Source(ASSURANCE, "C&AG comments under section 143(6)(b), and the management "
                              "replies to them"),
        ),
        window_years=1,
        origin=SPEC,
        notes=("Consuming these is ROADMAP M12 and is not built. The entry exists so that the "
               "boundary is explicit: they corroborate the diagnostics, they do not feed them.",),
    ),

    Derivation(
        id="X-PERSISTENCE",
        title="Persistence — why the current annual report is not enough",
        kind=CROSS,
        formula="An annual report presents the current period and one comparative: two "
                "points. Persistence is one of the nine §11.1 priority dimensions and §9.5 "
                "forbids presenting a two-point movement as a trend, so any trend derivation "
                "needs the two or three PRECEDING annual reports as well.",
        sources=(
            Source(EXTERNAL, "The two or three immediately preceding annual reports of the "
                             "same entity, on the same statement flavour"),
            Source(NOTES, "Any restatement of the comparative, which makes a restated "
                          "comparative and the originally reported figure two different "
                          "measurements of the same year"),
        ),
        window_years=3,
        origin=SPEC,
        notes=("Enforced structurally, not by convention: panel.window() returns None rather "
               "than a truncated series, so a rule CANNOT accidentally present two points as "
               "a trend.",),
    ),
)


# ===================================================================================
# Sector overlays — where a generic derivation must be modified, not merely suppressed.
# ===================================================================================

@dataclass(frozen=True)
class SectorOverlay:
    id: str
    business_models: frozenset[str]
    affects: tuple[str, ...]           # derivation ids
    instruction: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "business_models": sorted(self.business_models),
                "affects": list(self.affects), "instruction": self.instruction}


OVERLAYS: tuple[SectorOverlay, ...] = (
    SectorOverlay(
        id="OV-OG",
        business_models=frozenset({"petroleum", "mining"}),
        affects=("S09", "S11", "S12", "D-CAPBC"),
        instruction=(
            "Under successful-efforts accounting the capitalised base behaves nothing like "
            "generic CWIP: exploratory wells pending determination sit capitalised while "
            "their outcome is unresolved, and producing assets are depleted on a "
            "unit-of-production basis rather than on a useful life. So: the CWIP ageing read "
            "(S09) must separate wells pending determination from construction in progress, "
            "and the implied-depreciation read (S11) must compare the depletion charge "
            "against reserves produced rather than against a disclosed useful life. Applying "
            "the generic derivation here produces a signal that is an artefact of the "
            "accounting policy."
        ),
    ),
    SectorOverlay(
        id="OV-REG",
        business_models=frozenset({"power_utilities"}),
        affects=("S05", "S22", "X-SCH3RATIO"),
        instruction=(
            "A tariff-based utility carries regulated receivables and regulatory deferral "
            "account balances by design. S05 is suppressed for this model; the test that "
            "replaces it is whether the receivables trend exceeds the entity's own "
            "established pattern. No sector-normal value is assumed, defaulted or "
            "fabricated — where no supplied benchmark exists, the comparison is reported as "
            "unavailable."
        ),
    ),
)


# ---- indices -----------------------------------------------------------------------

DERIVATIONS: tuple[Derivation, ...] = _SIGNAL_DERIVATIONS + _SUPPORTING + _CROSS
BY_ID: dict[str, Derivation] = {d.id: d for d in DERIVATIONS}

SIGNAL_DERIVATIONS: tuple[Derivation, ...] = _SIGNAL_DERIVATIONS
SUPPORTING_DERIVATIONS: tuple[Derivation, ...] = _SUPPORTING
CROSS_CUTTING: tuple[Derivation, ...] = _CROSS


def for_signal(signal_id: str) -> Derivation | None:
    d = BY_ID.get(signal_id)
    return d if d is not None and d.kind == SIGNAL else None


def supporting_for(cluster_id: str) -> tuple[Derivation, ...]:
    return tuple(d for d in _SUPPORTING if d.cluster == cluster_id)


def overlays_for(business_model: str | None) -> tuple[SectorOverlay, ...]:
    if not business_model:
        return ()
    return tuple(o for o in OVERLAYS if business_model in o.business_models)


def formula(signal_id: str) -> str:
    d = BY_ID.get(signal_id)
    return d.formula if d else ""


def sources(signal_id: str) -> tuple[str, ...]:
    d = BY_ID.get(signal_id)
    return d.source_lines() if d else ()


def proxies(signal_id: str) -> tuple[str, ...]:
    d = BY_ID.get(signal_id)
    return tuple(str(p) for p in d.proxies) if d else ()


def manifest() -> dict[str, Any]:
    """What travels on the run manifest (§18.3 reproducibility)."""
    return {
        "version": VERSION,
        "signal_derivations": len(_SIGNAL_DERIVATIONS),
        "supporting": len(_SUPPORTING),
        "cross_cutting": len(_CROSS),
        "overlays": len(OVERLAYS),
        "proposed": sorted(d.id for d in DERIVATIONS if d.origin == PROPOSED),
    }


def mandatory_schedules() -> tuple[tuple[str, str], ...]:
    """Every Schedule III mandatory schedule a derivation reads, with the derivation.

    These do a disproportionate amount of the work — ageing, overdue projects, the ratio
    note — and their ABSENCE from a filing is itself a reportable fact, so they are
    enumerable rather than buried in prose.
    """
    out: list[tuple[str, str]] = []
    for d in DERIVATIONS:
        for s in d.sources:
            if s.mandatory:
                out.append((d.id, s.where))
    return tuple(out)


# ---- import-time validation --------------------------------------------------------

def _validate() -> None:
    seen: set[str] = set()
    for d in DERIVATIONS:
        if d.id in seen:
            raise ValueError(f"duplicate derivation id {d.id}")
        seen.add(d.id)
        if d.kind not in KINDS:
            raise ValueError(f"{d.id}: bad kind {d.kind!r}")
        if not d.formula:
            raise ValueError(f"{d.id}: a derivation with no formula states nothing")
        if not d.sources:
            raise ValueError(
                f"{d.id}: no source. A derivation without a place in the annual report "
                f"cannot be checked by a reviewer, which is the whole point of the module."
            )
        for s in d.sources:
            if s.place not in PLACES:
                raise ValueError(f"{d.id}: unknown source place {s.place!r}")
        if d.window_years < 1:
            raise ValueError(f"{d.id}: window_years must be >= 1")
        overlap = set(d.face_inputs) & set(d.optional_inputs)
        if overlap:
            raise ValueError(
                f"{d.id}: {sorted(overlap)} declared both required and optional — a rule "
                f"cannot both abstain on a key and proceed without it"
            )

    # Every Appendix D signal has exactly one derivation, and vice versa.
    declared = {d.id for d in _SIGNAL_DERIVATIONS}
    registry = set(SG.BY_ID)
    if declared != registry:
        raise ValueError(
            f"signal/derivation mismatch — no derivation for {sorted(registry - declared)}; "
            f"derivation for unknown signal {sorted(declared - registry)}. Every signal must "
            f"state how it is derived and where its inputs live."
        )

    for d in _SIGNAL_DERIVATIONS:
        sig = SG.BY_ID[d.id]
        if d.window_years != sig.window:
            raise ValueError(
                f"{d.id}: derivation window {d.window_years}y contradicts the registry's "
                f"{sig.window}y. The window is the §9.5 guarantee and must be stated once."
            )
        # The registry's `inputs` are what must exist for the diagnostic to run at all.
        # The derivation refines them into required, optional and note-level. The two must
        # agree, because `readiness()` reports missing inputs from the REGISTRY: a
        # divergence there makes the coverage report describe a contract nobody implements.
        #
        # For a FACE signal the required set is exactly the panel keys. For a NOTE signal
        # it is the panel keys plus the note-level figures an extractor would have to
        # supply — naming them is what makes "deferred to note extraction" an actionable
        # statement rather than a shrug.
        expect = set(d.face_inputs) | (set(d.note_inputs) if sig.availability == NOTE else set())
        if expect != set(sig.inputs):
            raise ValueError(
                f"{d.id}: derivation inputs {sorted(expect)} do not match the registry's "
                f"{sorted(sig.inputs)}. `readiness()` reports missing inputs from the "
                f"registry, so a divergence makes the coverage report wrong."
            )
        if sig.availability == FACE and not d.face_inputs:
            raise ValueError(f"{d.id}: declared FACE-derivable but names no face input")
        if sig.availability == NOTE and not (d.note_inputs or d.face_inputs):
            raise ValueError(f"{d.id}: declared NOTE-derivable but names no note input")

    for d in _SUPPORTING:
        if d.cluster not in CL.BY_ID:
            raise ValueError(f"{d.id}: supporting derivation names unknown cluster {d.cluster!r}")

    known = set(BY_ID)
    for o in OVERLAYS:
        bad = o.business_models - SG.BUSINESS_MODELS
        if bad:
            raise ValueError(f"{o.id}: unknown business model(s) {sorted(bad)}")
        missing = [a for a in o.affects if a not in known]
        if missing:
            raise ValueError(f"{o.id}: affects unknown derivation(s) {missing}")


_validate()
