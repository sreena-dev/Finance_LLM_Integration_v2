"""
The eleven Schedule III mandatory ratios as a FIXED-SHAPE disclosure block.

Why this module exists
----------------------
`ratio_pipeline.computed_block()` hands the LLM a flat list of whatever the catalog
managed to compute — 23 ratios for one filing, 9 for another — with nothing marking
which of them are the Schedule III eleven. Asked for "the 11 Schedule III mandatory
ratios", the model then had to choose the set itself, and chose differently per
entity: BPCL got 9 ratios of which only 2 were Schedule III mandatory (the other 9
mandatory ones silently vanished), while ONGC got a flat refusal even though 6 of
its 11 were sitting in the prompt. Same code, same temperature, opposite answers.

The rule this package already enforces for arithmetic — the LLM never computes —
is extended here to selection: **the LLM never chooses the set either.** This
module returns exactly ELEVEN rows, in Schedule III order, for every document in
the corpus. A ratio that cannot be computed occupies its row and states WHY.

Never a silent gap
------------------
A blank is indistinguishable from a zero, and both are indistinguishable from
"we could not read it". So every non-computed row carries a `reason_code` that
separates causes an auditor must treat differently:

  TIE_OUT_WITHHELD   the figure WAS found, but a tie-out it takes part in failed,
                     so the statement does not internally reconcile. The figure is
                     deliberately withheld. The most serious code here — it points
                     at the filing/extraction, not at our coverage.
  STATEMENT_ABSENT   the statement the figure lives on is not in the source at all
                     (BPCL 2024-25 has no standalone P&L in the KB). Distinct from
                     "not disclosed": the company did publish it, we do not have it.
  UNBOUND            the statement IS present and the figure belongs on its face,
                     but no line matched. A binder/coverage gap on our side.
  NEEDS_NOTE_BINDER  in the filing, but only in a Note / Cash Flow / SoCE table
                     that this engine has no binder for yet.
  NOT_DISCLOSED      Schedule III does not require the figure anywhere, on the face
                     or in the notes (credit sales, credit purchases). Permanent,
                     and NOT a defect — proxying revenue for credit sales would be
                     the actual error.
  ANALYST_PARAM      an engagement parameter the analyst supplies, not a reported
                     line (ROI's return amount).
  FRAMEWORK_MISMATCH the entity does not present the classification this ratio needs.
                     NBFCs report under Schedule III DIVISION III, whose balance sheet
                     is ordered Financial / Non-Financial and has no current versus
                     non-current split at all — so a Current Ratio is not merely
                     unbound for PFC or REC, it is not a figure their format produces.
                     Calling that UNBOUND would blame our binder for a framework
                     difference and invite someone to "fix" it by proxying the wrong
                     lines.
  NOT_APPLICABLE     the ratio's derivation does not fit this filing's cost
                     structure (guard tripped, e.g. negative cost of sales).

Deterministic and dependency-light: same document in, byte-identical block out.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

from . import computations as C
from . import ratio_pipeline as RP
from .binding import BindingReport

# Schedule III, Division II, note (i)-(xi) — the official order and the official
# caption for each. `key` indexes this package's catalog.
SCHEDULE_III: tuple[tuple[str, str, str], ...] = (
    ("i",    "current_ratio",         "Current Ratio"),
    ("ii",   "debt_to_equity",        "Debt-Equity Ratio"),
    ("iii",  "dscr",                  "Debt Service Coverage Ratio"),
    ("iv",   "roe",                   "Return on Equity (ROE)"),
    ("v",    "inventory_turnover",    "Inventory Turnover Ratio"),
    ("vi",   "receivables_turnover",  "Trade Receivables Turnover Ratio"),
    ("vii",  "payables_turnover",     "Trade Payables Turnover Ratio"),
    ("viii", "capital_turnover",      "Net Capital Turnover Ratio"),
    ("ix",   "net_profit_margin",     "Net Profit Ratio"),
    ("x",    "roce",                  "Return on Capital Employed (ROCE)"),
    ("xi",   "roi",                   "Return on Investment (ROI)"),
)

# Severity order for reporting when several inputs are missing for different
# reasons: the most actionable / most serious cause names the row.
_CODE_RANK = {
    "TIE_OUT_WITHHELD": 0,
    "STATEMENT_ABSENT": 1,
    "FRAMEWORK_MISMATCH": 2,
    "UNBOUND": 3,
    "NEEDS_NOTE_BINDER": 4,
    "NOT_DISCLOSED": 5,
    "ANALYST_PARAM": 6,
    "NOT_APPLICABLE": 7,
}
_CODE_TEXT = {
    "TIE_OUT_WITHHELD": "figure found but WITHHELD — a tie-out it participates in failed, "
                        "so the statement does not internally reconcile",
    "STATEMENT_ABSENT": "the statement carrying this figure is not present in the source "
                        "for this entity/flavour",
    "UNBOUND": "the statement is present and this figure belongs on its face, but no line "
               "could be reliably mapped to it",
    "FRAMEWORK_MISMATCH": "this entity presents its balance sheet under Schedule III "
                          "Division III (Financial / Non-Financial order, as NBFCs do), "
                          "which has no current versus non-current classification — the "
                          "figure this ratio needs is not one that format reports",
    "NEEDS_NOTE_BINDER": C.TIER_REASON[C.NOTE],
    "NOT_DISCLOSED": C.TIER_REASON[C.NOT_DISCLOSED],
    "ANALYST_PARAM": C.TIER_REASON[C.PARAM],
    "NOT_APPLICABLE": "the ratio's derivation does not fit this filing's reported structure",
}

# Inputs that exist ONLY in the Division II (current/non-current) presentation.
_DIVISION_II_ONLY = frozenset({"current_assets", "current_liabilities"})

_DIV3_MARKERS = ("financial assets", "non-financial assets", "financial liabilities",
                 "non-financial liabilities")
_DIV2_MARKERS = ("current assets", "non-current assets", "current liabilities")


def detect_division(bs_pt) -> str:
    """'II' | 'III' | 'unknown' from the balance sheet's own section headers.

    Reported as an inference, never as certainty: a wrong framework call invalidates
    every format-specific ratio, so an ambiguous sheet stays 'unknown' and the rows
    fall back to UNBOUND rather than claiming a framework difference that would
    excuse a real coverage gap.
    """
    if bs_pt is None:
        return "unknown"
    heads = [(r.label or "").strip().lower() for r in bs_pt.rows if r.role == "header"]
    from .binding import _strip, _strip_bare_ordinal
    heads = [_strip_bare_ordinal(_strip(h)) for h in heads]
    div3 = sum(any(h.startswith(m) for m in _DIV3_MARKERS) for h in heads)
    div2 = sum(any(h.startswith(m) for m in _DIV2_MARKERS) for h in heads)
    if div3 >= 2 and div2 == 0:
        return "III"
    if div2 >= 2:
        return "II"
    return "unknown"

# Which statement each canonical ratio input is bound FROM. Derived from the binding
# registry rather than hand-listed, so a new LineSpec is covered automatically.
def _input_statement_map() -> dict[str, str]:
    from .binding import REGISTRY
    by_spec = {s.key: s.statement for s in REGISTRY}
    out = {k: by_spec[k] for k in by_spec}
    for canon, spec_key in RP._RENAMED_INPUTS.items():        # current_assets <- total_current_assets
        if spec_key in by_spec:
            out[canon] = by_spec[spec_key]
    out["ebit"] = "PL"                                        # PBT + finance costs
    for canon, base in (("avg_total_assets", "total_assets"), ("avg_inventory", "inventories"),
                        ("avg_receivables", "trade_receivables"), ("avg_payables", "trade_payables")):
        if base in by_spec:
            out[canon] = by_spec[base]
    return out


_INPUT_STMT = _input_statement_map()


@dataclass
class Sch3Row:
    """One of the eleven rows. Always present, computed or not."""
    sl: str                                  # i .. xi
    key: str
    caption: str                             # the Schedule III caption
    formula: str
    unit: str
    value: float | None = None
    prior: float | None = None
    change_pct: float | None = None
    variance_flag: bool = False              # >=25% YoY -> Schedule III explanation required
    status: str = "ABSTAIN"                  # OK | ABSTAIN
    reason_code: str | None = None
    reason: str = ""
    missing_inputs: list[str] = field(default_factory=list)
    inputs_used: dict = field(default_factory=dict)
    trace: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sch3Report:
    doc_id: str
    flavor: str
    statements: dict = field(default_factory=dict)     # {"BS": bool, "PL": bool}
    division: str = "unknown"                          # Schedule III II | III | unknown
    rows: list[Sch3Row] = field(default_factory=list)  # ALWAYS 11
    computed: int = 0
    withheld: int = 0
    checks_not_run: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"doc_id": self.doc_id, "flavor": self.flavor,
                "statements": self.statements, "schedule_iii_division": self.division,
                "coverage": {"computed": self.computed, "of": len(self.rows),
                             "withheld": self.withheld},
                "rows": [r.to_dict() for r in self.rows],
                "checks_not_run": self.checks_not_run, "caveats": self.caveats}


def _contradicted_keys(rep) -> set[str]:
    """Canonical input names whose bound line failed a tie-out, so `trusted()` (and
    therefore `rep.inputs`) deliberately excludes them. These must be reported as
    WITHHELD, never as merely missing — the distinction is the whole point."""
    spec_to_canon = {v: k for k, v in RP._RENAMED_INPUTS.items()}
    out: set[str] = set()
    for stmt in ("BS", "PL"):
        br = rep.binding.get(stmt) or BindingReport(stmt)
        for key, ln in br.bound.items():
            if ln.contradicted:
                out.add(spec_to_canon.get(key, key))
    return out


def _classify(inp: str, statements: dict, withheld: set[str], division: str = "unknown") -> str:
    if inp in withheld:
        return "TIE_OUT_WITHHELD"
    tier = C.INPUT_SOURCE.get(inp, C.FACE)
    if tier == C.NOT_DISCLOSED:
        return "NOT_DISCLOSED"
    if tier == C.PARAM:
        return "ANALYST_PARAM"
    if tier == C.EXTERNAL:
        return "NOT_DISCLOSED"
    if tier == C.NOTE:
        return "NEEDS_NOTE_BINDER"
    stmt = _INPUT_STMT.get(inp)                 # FACE tier
    if stmt and not statements.get(stmt, False):
        return "STATEMENT_ABSENT"
    if division == "III" and inp in _DIVISION_II_ONLY:
        return "FRAMEWORK_MISMATCH"
    return "UNBOUND"


def _missing_for(spec, available: dict) -> list[str]:
    """Inputs this spec needed and did not get. Computed from the spec itself, not
    parsed out of the abstain message, so the reason can never drift from the cause."""
    missing = [k for k in spec.required if available.get(k) is None]
    for grp in spec.any_of:
        if not any(available.get(k) is not None for k in grp):
            missing += [k for k in grp if available.get(k) is None]
    seen, out = set(), []
    for k in missing:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _prior_inputs(rep) -> dict:
    """Build the comparative-period input set from each bound line's `prior` column,
    so prior-year ratios (and hence the Schedule III >=25% variance test) come from
    the same binder as the current year. Period AVERAGES need a third year and are
    therefore left absent — the catalog's own abstain machinery handles that, which
    is why this is safe rather than approximate."""
    spec_to_canon = {v: k for k, v in RP._RENAMED_INPUTS.items()}
    out: dict = {}
    for stmt in ("BS", "PL"):
        br = rep.binding.get(stmt) or BindingReport(stmt)
        for key, ln in br.trusted().items():
            if ln.prior is None:
                continue
            out[spec_to_canon.get(key, key)] = ln.prior
    pbt, fin = out.get("pbt"), out.get("finance_costs")
    if pbt is not None and fin is not None:
        out["ebit"] = pbt + fin
    return out


def analyse(doc_id: str, flavor: str = "standalone", embed_fn=None) -> Sch3Report:
    """The fixed-shape Schedule III block for one document. Eleven rows, always."""
    rep = RP.compute_from_doc(doc_id, flavor=flavor, embed_fn=embed_fn)

    # Statement availability, using the SAME selectors the ratio pipeline used, so
    # "absent" here means absent to the engine that needed it.
    bs = RP._statement_by_title(doc_id, "balance sheet",
                                "(restat|reconcil|five year|segment|summaris|summariz)",
                                "BS", flavor)
    pl = RP._statement_by_title(doc_id, "statement of profit|profit and loss",
                                "(retained|comprehensive|restat|reconcil|segment|five year"
                                "|summaris|summariz)", "PL", flavor)
    statements = {"BS": bs is not None, "PL": pl is not None}
    division = detect_division(bs)

    out = Sch3Report(doc_id=doc_id, flavor=flavor, statements=statements, division=division)
    withheld_inputs = _contradicted_keys(rep)

    by_key = {c.key: c for c in rep.computed}
    ab_key = {c.key: c for c in rep.abstained}

    # Prior-year ratios for the >=25% variance test.
    prior_vals: dict[str, float] = {}
    pin = _prior_inputs(rep)
    if pin:
        for comp in C.run_ratios(pin, [k for _, k, _ in SCHEDULE_III]):
            if comp.status == "OK" and comp.result is not None:
                prior_vals[comp.key] = comp.result
    else:
        out.checks_not_run.append(
            "Schedule III >=25% variance test — no comparative column bound for this filing")

    for sl, key, caption in SCHEDULE_III:
        spec = C.CATALOG_BY_KEY.get(key)
        if spec is None:                     # catalog drift guard; never silently skip a row
            out.rows.append(Sch3Row(sl, key, caption, "(not in catalog)", "",
                                    reason_code="UNBOUND",
                                    reason="this ratio is not present in the engine catalog"))
            continue
        row = Sch3Row(sl, key, caption, spec.formula, spec.unit)
        comp = by_key.get(key) or ab_key.get(key)

        if comp is not None and comp.status == "OK":
            row.status, row.value, row.trace = "OK", comp.result, comp.trace
            row.inputs_used = {k: v for k, v in comp.inputs.items() if v is not None}
            p = prior_vals.get(key)
            if p is not None:
                row.prior = p
                if p != 0:
                    row.change_pct = round((comp.result - p) / abs(p) * 100, 2)
                    row.variance_flag = abs(row.change_pct) >= C.SCH3_VARIANCE_THRESHOLD * 100
            out.computed += 1
        else:
            missing = _missing_for(spec, rep.inputs)
            row.missing_inputs = missing
            if not missing:
                # required inputs all present but the spec still abstained -> a guard
                # tripped (zero/negative denominator, structural mismatch).
                row.reason_code = "NOT_APPLICABLE"
                row.reason = _CODE_TEXT["NOT_APPLICABLE"]
                row.trace = comp.trace if comp else ""
            else:
                codes = {m: _classify(m, statements, withheld_inputs, division)
                         for m in missing}
                lead = min(codes.values(), key=lambda c: _CODE_RANK.get(c, 99))
                row.reason_code = lead
                named = [m for m, c in codes.items() if c == lead]
                row.reason = f"{_CODE_TEXT[lead]} — {', '.join(named)}"
                if lead == "TIE_OUT_WITHHELD":
                    out.withheld += 1
                # If other causes also apply, say so rather than implying one cause.
                others = sorted({c for c in codes.values() if c != lead},
                                key=lambda c: _CODE_RANK.get(c, 99))
                for oc in others:
                    also = [m for m, c in codes.items() if c == oc]
                    row.reason += f"; also {oc.lower().replace('_',' ')}: {', '.join(also)}"
        out.rows.append(row)

    assert len(out.rows) == len(SCHEDULE_III), "the Schedule III block must always be 11 rows"

    if division == "III":
        out.caveats.append(
            "Balance sheet is presented under Schedule III Division III (Financial / "
            "Non-Financial order, used by NBFCs). Ratios requiring a current versus "
            "non-current split are reported FRAMEWORK_MISMATCH, not nil — that split "
            "is not part of this presentation.")
    elif division == "unknown":
        out.caveats.append(
            "Schedule III division could not be inferred from the balance-sheet headers; "
            "format-specific ratios are reported on their inputs alone.")
    for stmt, present in statements.items():
        if not present:
            out.caveats.append(
                f"{'Balance Sheet' if stmt=='BS' else 'Statement of Profit and Loss'} "
                f"({flavor}) is NOT present in the source for {doc_id} — every ratio "
                f"depending on it is reported as STATEMENT_ABSENT, not as nil.")
    if out.withheld:
        out.caveats.append(
            f"{out.withheld} ratio(s) withheld because a balance-sheet/P&L tie-out FAILED. "
            f"A ratio built on a statement that does not reconcile is worse than no ratio.")
    out.caveats.append("Recomputed by a deterministic engine from the filing's own statements. "
                       "Where the entity discloses its own ratio note, recomputed and disclosed "
                       "figures should be compared before any conclusion is drawn.")
    return out


# ------------------------------------------------------------------ rendering
def render_text(rep: Sch3Report) -> str:
    """The authoritative fixed-shape block. Used for the CLI and injected verbatim
    into the LLM prompt, so the model narrates it instead of selecting it."""
    L = [f"THE ELEVEN SCHEDULE III MANDATORY RATIOS — {rep.doc_id} ({rep.flavor})",
         f"Coverage: {rep.computed} of {len(rep.rows)} recomputed from the filing's statements."
         + (f"  {rep.withheld} withheld (failed tie-out)." if rep.withheld else ""),
         "Statements available to the engine: "
         + ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in rep.statements.items())
         + f"   |   inferred Schedule III division: {rep.division}",
         "",
         "These values are AUTHORITATIVE and deterministic. Do NOT recompute, adjust or",
         "derive any number. Report every row, including the ones that could not be",
         "computed, with its stated reason. Never present a missing value as nil or zero.",
         ""]
    for r in rep.rows:
        if r.status == "OK":
            v = f"{r.value:,.4f} {r.unit}".strip()
            line = f"  ({r.sl}) {r.caption} = {v}"
            if r.change_pct is not None:
                line += f"   [prior {r.prior:,.4f}, change {r.change_pct:+.2f}%" \
                        + (" — >=25%, Schedule III explanation required]" if r.variance_flag else "]")
            L.append(line)
            L.append(f"        formula: {r.formula}")
            L.append(f"        trace  : {r.trace}")
        else:
            L.append(f"  ({r.sl}) {r.caption} = NOT AVAILABLE  [{r.reason_code}]")
            L.append(f"        formula: {r.formula}")
            L.append(f"        reason : {r.reason}")
    if rep.checks_not_run:
        L.append("")
        for c in rep.checks_not_run:
            L.append(f"  CHECK NOT RUN: {c}")
    L.append("")
    for c in rep.caveats:
        L.append(f"  NOTE: {c}")
    return "\n".join(L)


def render_markdown(rep: Sch3Report) -> str:
    """The user-facing answer. Emitted VERBATIM, with no LLM in the path, so the same
    filing always produces the same eleven rows in the same order. A row that could
    not be computed says so in its own cell — it is never blank, nil or dropped."""
    head = (f"**The eleven Schedule III mandatory ratios — {rep.doc_id.replace('_',' ')} "
            f"({rep.flavor})**\n\n"
            f"Recomputed **{rep.computed} of 11** from the filing's own statements"
            + (f"; **{rep.withheld} withheld** (failed tie-out)" if rep.withheld else "")
            + f". Balance sheet: {'available' if rep.statements.get('BS') else '**not available**'}"
            + f" · Statement of Profit and Loss: "
            + f"{'available' if rep.statements.get('PL') else '**not available**'}"
            + f" · inferred Schedule III division: {rep.division}\n")
    L = [head,
         "| # | Ratio | Value | Prior yr | Change | Status / why not available |",
         "|---|---|---|---|---|---|"]
    for r in rep.rows:
        if r.status == "OK":
            val = f"**{r.value:,.4f}** {r.unit}".strip()
            prior = f"{r.prior:,.4f}" if r.prior is not None else "—"
            if r.change_pct is None:
                chg, note = "—", "computed"
            else:
                chg = f"{r.change_pct:+.2f}%"
                note = ("**≥25% — Schedule III explanation required**"
                        if r.variance_flag else "computed")
        else:
            val, prior, chg = "**not available**", "—", "—"
            note = f"`{r.reason_code}` — {r.reason}"
        L.append(f"| {r.sl} | {r.caption} | {val} | {prior} | {chg} | {note} |")
    L.append("")
    for r in rep.rows:
        if r.status == "OK":
            L.append(f"- **{r.caption}** — {r.formula}  \n  `{r.trace}`")
    if rep.checks_not_run:
        L.append("")
        for c in rep.checks_not_run:
            L.append(f"> **Check not run:** {c}")
    L.append("")
    for c in rep.caveats:
        L.append(f"> {c}")
    return "\n".join(L)
