"""
Signal rules — ROADMAP M7-M9, for the nine face signals the corpus can currently reach.

THE CONTRACT EVERY RULE HONOURS
-------------------------------
A rule is a PURE FUNCTION of the panel. Same panel in, same verdict out, forever. No model
call, no I/O, no clock, no randomness — the FDR's §18.3 reproducibility criterion is a
property of these functions, not a promise about them.

Each returns an `Outcome` carrying:

  fired        did the condition hold
  observation  what was seen, in figures, in one sentence a reviewer can check
  trace        the arithmetic, reconstructable by hand from the panel
  confidence   how sure we are the signal is REAL AND CORRECTLY MEASURED

`confidence` is NOT severity. Severity is a property of the risk and lives in the signal
registry; confidence is a property of this measurement and is computed here from the trust
that travelled with each figure. §4.4 requires them to be independent, and the only way to
guarantee that is for them never to be computed in the same place.

ABSTAIN IS A FIRST-CLASS RESULT
-------------------------------
A rule that cannot run returns `fired=None` with the reason and the missing inputs. It
never guesses, never substitutes a default, and never treats a missing figure as zero. A
zero is a measurement; an absence is not (finance-core P4).

§9.5 IS ENFORCED STRUCTURALLY
-----------------------------
Every trend rule asks the panel for a window and abstains when the panel returns None. The
panel returns None rather than a truncated series precisely so that a rule CANNOT
accidentally present a two-point movement as a trend.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from . import thresholds as TH
from .panel import Panel, MIN_TREND_YEARS

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


# ---- why a rule could not run ------------------------------------------------------
# A CLOSED set. The prose that goes with each is built from the panel's own diagnosis, so
# it names the key and the years rather than covering both branches of a disjunction.
# These codes are what makes an abstain queryable: "which missing figure blocks the most
# diagnostics across the corpus" is a GROUP BY over this field, and was previously a
# regular expression over English.
PANEL_ABSENT = "PANEL_ABSENT"                    # no entity-year panel at all
PANEL_TOO_SHORT = "PANEL_TOO_SHORT"              # the panel itself is shorter than the window
INPUT_NEVER_BOUND = "INPUT_NEVER_BOUND"          # the figure binds in no year — binder work
INPUT_ABSENT_FROM_LATEST = "INPUT_ABSENT_FROM_LATEST"
INPUT_SERIES_BROKEN = "INPUT_SERIES_BROKEN"      # enough years exist but not consecutively
INPUT_TOO_FEW_YEARS = "INPUT_TOO_FEW_YEARS"      # not enough filings for this figure
INPUT_SCALE_UNRECONCILED = "INPUT_SCALE_UNRECONCILED"
INPUT_SINGLE_YEAR = "INPUT_SINGLE_YEAR"          # an average needs an opening balance
NOT_COMPUTABLE = "NOT_COMPUTABLE"                # inputs present; arithmetic undefined
RULE_NOT_IMPLEMENTED = "RULE_NOT_IMPLEMENTED"    # scope, not a data failure
NEEDS_NOTE_EXTRACTION = "NEEDS_NOTE_EXTRACTION"  # scope, not a data failure

# The two codes above that are SCOPE rather than DATA. The distinction matters enough to
# be named once here: a count that merges them reports the system as broken when half the
# number is work deliberately not yet done.
SCOPE_CODES = frozenset({RULE_NOT_IMPLEMENTED, NEEDS_NOTE_EXTRACTION})


@dataclass(frozen=True)
class Outcome:
    """One rule's verdict. `fired=None` means the rule could not run."""
    fired: bool | None
    observation: str = ""
    trace: str = ""
    confidence: str | None = None
    confidence_basis: str = ""
    missing: tuple[str, ...] = ()
    reason: str = ""
    evidence: tuple[str, ...] = ()
    proxies: tuple[str, ...] = ()      # stand-ins actually applied, and their effect
    code: str = ""                     # one of the codes above; "" when the rule ran
    detail: dict[str, Any] = field(default_factory=dict)   # per-key state and years

    @property
    def ran(self) -> bool:
        return self.fired is not None


def _abstain(reason: str, missing: tuple[str, ...] = (), *,
             code: str = NOT_COMPUTABLE, detail: dict[str, Any] | None = None) -> Outcome:
    return Outcome(fired=None, reason=reason, missing=missing, code=code,
                   detail=detail or {})


def _fmt_years(years: list[str]) -> str:
    return ", ".join(years) if years else "—"


def _needs(panel: Panel, keys: tuple[str, ...], n: int = MIN_TREND_YEARS) -> Outcome | None:
    """Diagnose why `keys` cannot form an `n`-year window, or None if they can.

    Every trend rule opens with this. The returned reason names the failing key and the
    years it is missing, so the abstain is a work instruction rather than a shrug — and
    the code and detail make the same fact queryable across the corpus.
    """
    diag = panel.diagnose(keys, n)
    bad = {k: d for k, d in diag.items() if d["state"] != Panel.OK}
    if not bad:
        return None

    # The panel being too short is a fact about the ENTITY's filing history, not about any
    # one figure, so it is reported once rather than blamed on whichever key was asked first.
    if len(panel.years) < n:
        return _abstain(
            f"The panel holds {len(panel.years)} comparable year(s) "
            f"({_fmt_years(list(panel.years))}); §9.5 requires {n} before any trend is "
            f"presented, and forbids presenting a two-point movement as a trend.",
            tuple(bad), code=PANEL_TOO_SHORT, detail=diag)

    # Report the most actionable state present. A key that binds nowhere is binder work; a
    # key with a one-year hole is an extraction fix on one filing — a different job.
    order = (Panel.NEVER_BOUND, Panel.ABSENT_FROM_LATEST, Panel.BROKEN_BY_GAP,
             Panel.SCALE_UNRECONCILED, Panel.TOO_FEW_YEARS)
    codes = {Panel.NEVER_BOUND: INPUT_NEVER_BOUND,
             Panel.ABSENT_FROM_LATEST: INPUT_ABSENT_FROM_LATEST,
             Panel.BROKEN_BY_GAP: INPUT_SERIES_BROKEN,
             Panel.SCALE_UNRECONCILED: INPUT_SCALE_UNRECONCILED,
             Panel.TOO_FEW_YEARS: INPUT_TOO_FEW_YEARS}
    state = next(s for s in order if any(d["state"] == s for d in bad.values()))
    hit = {k: d for k, d in bad.items() if d["state"] == state}
    names = ", ".join(sorted(hit))

    if state == Panel.NEVER_BOUND:
        why = (f"{names} could not be bound in any year of the panel "
               f"({_fmt_years(list(panel.years))}). This is a fact-layer gap, not a "
               f"disclosure gap.")
    elif state == Panel.ABSENT_FROM_LATEST:
        why = (f"{names} is not bound in the latest year ({panel.latest}), so a series "
               f"ending at this filing cannot be formed. A trend that stops before the "
               f"current year is not a statement about it.")
    elif state == Panel.BROKEN_BY_GAP:
        gaps = "; ".join(f"{k} missing in {_fmt_years(d['missing_years'])}"
                         for k, d in sorted(hit.items()) if d["missing_years"])
        why = (f"{names} is bound in enough years, but not consecutively — {gaps}. A gap "
               f"is not a trend; it is two trends.")
    elif state == Panel.SCALE_UNRECONCILED:
        why = (f"{names} carries a guessed scale that contradicts the rest of the window, "
               f"so the series was refused rather than risk a hundred-fold movement "
               f"manufactured out of a unit error.")
    else:
        why = "; ".join(
            f"{k} is bound in {d['series_len']} consecutive year(s) "
            f"({_fmt_years(d['years_present'])}), short of the {n} required"
            for k, d in sorted(hit.items()))

    return _abstain(f"Not computed. {why}", tuple(sorted(bad)),
                    code=codes[state], detail=diag)


# ---- confidence -------------------------------------------------------------------

_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2}


def _cap(confidence: str, ceiling: str) -> str:
    """Lower a confidence to a ceiling. Never raises one — a cap can only cost.

    Applied wherever a proxy stood in for a disclosed line. The measurement may be
    perfectly sound arithmetic on figures that are not quite the ones the derivation
    names, and that distinction is exactly what confidence is for.
    """
    return confidence if _ORDER[confidence] <= _ORDER[ceiling] else ceiling

def _confidence(panel: Panel, keys: tuple[str, ...], years: tuple[str, ...]) -> tuple[str, str]:
    """How much this measurement can be leaned on — App H, computed from the inputs.

    The weakest input governs. A diagnostic is no more dependable than the least
    dependable figure in it, and averaging would let one strong input launder a weak one.
    """
    cells = [panel.cell(k, y) for k in keys for y in years]
    cells = [c for c in cells if c is not None]
    if not cells:
        return LOW, "No input carried a trust record."

    if any(c.verify_verdict == "CONTRADICTED" for c in cells):
        return LOW, ("At least one input came from a statement whose own arithmetic "
                     "identity failed.")
    unconfirmed = [c for c in cells if c.verify_verdict != "CONFIRMED"]
    weak_unit = [c for c in cells if c.unit_confidence != "HIGH"]

    if not unconfirmed and not weak_unit:
        return HIGH, ("Every input came from a statement that satisfied its own "
                      "arithmetic identity, at a scale read from the filing itself.")
    if unconfirmed and weak_unit:
        return LOW, (f"{len(unconfirmed)} input(s) unconfirmed by any identity and "
                     f"{len(weak_unit)} carried an inferred scale.")
    if unconfirmed:
        return MEDIUM, (f"{len(unconfirmed)} of {len(cells)} inputs could not be "
                        f"confirmed by an arithmetic identity.")
    return MEDIUM, (f"{len(weak_unit)} of {len(cells)} inputs carried a scale inferred "
                    f"rather than read from the statement.")


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _pp(v: float) -> str:
    """A DIFFERENCE between two rates, in percentage points.

    Subtracting one growth rate from another does not give a growth rate, and printing
    the result with a % sign invites it to be read as one — 70.5% less 16.5% is 54.0
    percentage points of divergence, not a 54% divergence. The distinction is the
    difference between a figure a reviewer can interpret and one they cannot.
    """
    return f"{v * 100:+.1f}pp"


def _growth(series: list[tuple[str, float]]) -> float | None:
    """Total relative growth across the window. None when the base is unusable.

    A zero or negative base makes a growth rate meaningless rather than large, so it
    abstains instead of returning an enormous number that would fire every threshold.
    """
    first, last = series[0][1], series[-1][1]
    if first <= 0:
        return None
    return (last - first) / first


def _avg_by_year(panel: Panel, key: str) -> dict[str, float]:
    """Average of opening and closing balance, per year, for a balance-sheet key.

    A flow divided by a point-in-time balance is not a rate. Accruals over total assets,
    an implied depreciation rate and an implied borrowing rate all put a P&L or cash-flow
    figure over a balance, so all three need the average — and averaging costs a year,
    because the oldest year of a series carries no opening balance inside it.

    Returns only the years an average can actually be formed for. The alternative — a
    silent fallback to the closing balance — would put two different denominators in one
    series and call the result a trend.
    """
    s = panel.series(key)
    if not s or len(s) < 2:
        return {}
    return {s[i][0]: (s[i - 1][1] + s[i][1]) / 2.0 for i in range(1, len(s))}


def _sum_series(panel: Panel, keys: tuple[str, ...]) -> list[tuple[str, float]] | None:
    """One series that is the sum of several keys, over the years they all cover.

    Total borrowings is long-term plus short-term; a year in which only one of them is
    bound would silently report half the debt, so the intersection is taken rather than
    treating an unbound key as zero (P4: an absence is not a measurement).
    """
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
    out: list[tuple[str, float]] = [
        (y, sum(m[y] for m in maps)) for y, _ in parts[0] if y in common
    ]
    return out or None


def _tail(series: list[tuple[str, float]], n: int) -> list[tuple[str, float]] | None:
    """The most recent `n` years of an already-contiguous series, or None if too short."""
    return series[-n:] if len(series) >= n else None


# ---- the rules ---------------------------------------------------------------------

def s01_cash_conversion_cycle(panel: Panel) -> Outcome:
    """Deteriorating cash-conversion cycle (Layer 4, RC-WC). §9.2.

    The payables leg is the one that needs care. DPO is properly computed on PURCHASES,
    and an Indian annual report almost never discloses purchases as a line. Cost of
    materials consumed is the practical denominator, adjusted for the change in
    inventories where that line is bound, since purchases = consumption + closing stock -
    opening stock. Both the substitution and its effect are stated, and the diagnostic is
    capped at MEDIUM: it is sound arithmetic on a figure that is not quite the one the
    derivation names.
    """
    keys = ("trade_receivables", "inventories", "trade_payables", "revenue",
            "cost_of_materials_consumed")
    missing = panel.missing(keys)
    if missing:
        return _needs(panel, keys)
    raw = {k: panel.window(k) for k in keys}
    if any(v is None for v in raw.values()):
        return _needs(panel, keys)
    win: dict[str, list[tuple[str, float]]] = {k: v for k, v in raw.items() if v is not None}

    years = tuple(y for y, _ in win["revenue"])
    chg = panel.window("changes_in_inventories")
    purch = panel.window("purchases_of_stock_in_trade")

    proxies: list[str] = []
    if purch is not None:
        basis = "purchases of stock-in-trade, as disclosed"
    elif chg is not None:
        basis = "cost of materials consumed adjusted for the change in inventories"
        proxies.append(
            "Purchases (the payables-days denominator) -> cost of materials consumed "
            "adjusted for the change in inventories. Purchases are not disclosed as a "
            "separate line. Caps this diagnostic at MEDIUM.")
    else:
        basis = "cost of materials consumed, unadjusted"
        proxies.append(
            "Purchases (the payables-days denominator) -> cost of materials consumed, "
            "unadjusted. Neither purchases nor the change in inventories is disclosed as "
            "a separate line, so payables days are overstated where stock is building and "
            "understated where it is drawing down. Caps this diagnostic at MEDIUM.")

    def purchases(i: int) -> float | None:
        if purch is not None:
            return purch[i][1]
        cogs = win["cost_of_materials_consumed"][i][1]
        # `changes_in_inventories` is presented as a DECREASE in stock being positive
        # income, so purchases = consumption - (decrease) = consumption + increase.
        return cogs - chg[i][1] if chg is not None else cogs

    def legs(i: int) -> tuple[float, float, float] | None:
        rev, cogs, p = win["revenue"][i][1], win["cost_of_materials_consumed"][i][1], purchases(i)
        if rev <= 0 or cogs <= 0 or p is None or p <= 0:
            return None
        return (win["trade_receivables"][i][1] / rev * 365,
                win["inventories"][i][1] / cogs * 365,
                win["trade_payables"][i][1] / p * 365)

    per_year = [(years[i], legs(i)) for i in range(len(years))]
    usable = [(y, l) for y, l in per_year if l is not None]
    if len(usable) < 2:
        return _abstain("Revenue, cost of materials or the purchases proxy is zero or "
                        "negative in too many window years, so a cycle in days cannot be "
                        "formed.", ())

    (y0, (dso0, dio0, dpo0)), (y1, (dso1, dio1, dpo1)) = usable[0], usable[-1]
    first, last = dso0 + dio0 - dpo0, dso1 + dio1 - dpo1
    delta = last - first
    limit = TH.get("ccc_deterioration_days")

    conf, cbasis = _confidence(panel, keys, years)
    if proxies:
        conf = _cap(conf, MEDIUM)
        cbasis += (" Capped at MEDIUM because the payables leg rests on a proxy for "
                   "purchases (stated against this signal).")

    return Outcome(
        fired=delta > limit,
        observation=(f"The cash-conversion cycle moved from {first:.0f} days in {y0} to "
                     f"{last:.0f} days in {y1}, a change of {delta:+.0f} days. The legs moved "
                     f"DSO {dso0:.0f}->{dso1:.0f}, inventory days {dio0:.0f}->{dio1:.0f}, "
                     f"payables days {dpo0:.0f}->{dpo1:.0f} — §9.2 reads these as one system, "
                     f"because a lengthening cycle can come from any of the three."),
        trace=("CCC = DSO + DIO - DPO, where DSO = receivables/revenue x 365, "
               "DIO = inventories/cost of goods sold x 365, DPO = payables/purchases x 365 "
               f"(purchases basis: {basis}). By year: "
               + "; ".join(f"{y} {l[0] + l[1] - l[2]:.0f}d "
                           f"({l[0]:.0f}+{l[1]:.0f}-{l[2]:.0f})" for y, l in usable)
               + f". Change {delta:+.0f}d against a +{limit:.0f}d threshold."),
        confidence=conf, confidence_basis=cbasis, proxies=tuple(proxies))


def s03_net_current_liabilities(panel: Panel) -> Outcome:
    """Net current-liability position (Layer 2, RC-WC). §7 liquidity structure."""
    y = panel.latest
    if y is None:
        return _abstain("The panel has no years.", code=PANEL_ABSENT)
    ca, cl = panel.get("total_current_assets", y), panel.get("total_current_liabilities", y)
    if ca is None or cl is None:
        return _needs(panel, ("total_current_assets", "total_current_liabilities"), 1)
    if cl <= 0:
        return _abstain("Current liabilities are zero or negative; the position cannot "
                        "be formed.")
    ratio = ca / cl
    floor = TH.get("current_ratio_floor")
    conf, cbasis = _confidence(panel, ("total_current_assets", "total_current_liabilities"), (y,))

    # Current maturities of long-term debt are DISCLOSED here, never added. Under the Ind
    # AS Schedule III format they are already presented within current financial
    # liabilities, so adding them again would double-count; under some presentations they
    # are shown separately. The filing settles which, and this rule does not guess — it
    # states the amount and what it would mean if it sits outside the printed total.
    cm = panel.get("current_maturities_ltd", y)
    maturities = ""
    if cm is not None and cm > 0:
        alt = ca / (cl + cm)
        maturities = (
            f" Current maturities of long-term debt of {_fmt(cm)} lakh are disclosed for "
            f"{y}. They are not added to the printed current-liabilities total, which "
            f"already includes them under the Ind AS Schedule III presentation. Were they "
            f"presented outside it in this filing, the ratio would be {alt:.2f} — the "
            f"borrowings note settles which, and is the evidence to pull.")

    return Outcome(
        fired=ratio < floor,
        observation=(f"At {y}, current assets of {_fmt(ca)} lakh stand against current "
                     f"liabilities of {_fmt(cl)} lakh — a current ratio of {ratio:.2f}"
                     + (f", a net current-liability position of {_fmt(cl - ca)} lakh."
                        if ratio < floor else ".")
                     + maturities),
        trace=(f"current ratio = {_fmt(ca)} / {_fmt(cl)} = {ratio:.2f}; fires below "
               f"{floor:.2f}. Schedule III current / non-current split as presented."
               + (f" Current maturities of long-term debt {_fmt(cm)} shown separately, "
                  f"not added." if cm is not None and cm > 0 else "")),
        confidence=conf, confidence_basis=cbasis,
        evidence=("Borrowings note — presentation of current maturities of long-term debt",)
                 if cm is not None and cm > 0 else ())


def s04_weak_operating_cash_flow(panel: Panel) -> Outcome:
    """Weak or negative operating cash flow (Layer 4, RC-WC). §9.1."""
    ocf, pat = panel.window("ocf"), panel.window("pat")
    if ocf is None or pat is None:
        return _needs(panel, ("ocf", "pat"))
    years = tuple(y for y, _ in ocf)
    limit = TH.get("ocf_to_pat_weak")

    negative = [y for y, v in ocf if v < 0]
    ratios = [(y, o / p) for (y, o), (_, p) in zip(ocf, pat) if p > 0]
    weak = [y for y, r in ratios if r < limit]
    fired = bool(negative) or len(weak) >= 2

    conf, cbasis = _confidence(panel, ("ocf", "pat"), years)
    detail = (f"operating cash flow was negative in {', '.join(negative)}"
              if negative else
              f"operating cash flow covered profit below {_pct(limit)} in "
              f"{', '.join(weak)}" if weak else
              "operating cash flow covered reported profit throughout the window")

    # The second half of the derivation: OCF BEFORE working-capital changes against PAT.
    # It is what separates an earnings problem from a working-capital problem, and it is a
    # line inside the indirect-method reconciliation rather than on the face of the
    # statement. Where it is not bound, the attribution is reported as not formed — not
    # quietly dropped, because "OCF is weak" and "we could not say why" are two facts.
    pre = panel.window("ocf_before_wc")
    proxies: list[str] = []
    if pre is not None:
        pre_ratios = [(y, v / p) for (y, v), (_, p) in zip(pre, pat) if p > 0]
        attribution = (
            " Before working-capital changes, operating cash flow covered profit at "
            + "; ".join(f"{y} {r:.2f}" for y, r in pre_ratios)
            + " — a gap between the two series points at working capital, and its absence "
              "points at earnings.")
    else:
        attribution = (
            " Operating cash flow before working-capital changes is not bound, so this run "
            "does not attribute the weakness between earnings and working capital. The "
            "sub-total sits in the indirect-method reconciliation of the cash flow "
            "statement.")
        proxies.append(
            "Operating cash flow before working-capital changes -> not substituted; the "
            "earnings-versus-working-capital attribution is reported as not formed rather "
            "than approximated.")

    return Outcome(
        fired=fired,
        observation=(f"Across {years[0]}–{years[-1]}, {detail}. Latest: OCF "
                     f"{_fmt(ocf[-1][1])} lakh against profit {_fmt(pat[-1][1])} lakh."
                     + attribution),
        trace=("OCF/PAT by year: "
               + "; ".join(f"{y} {r:.2f}" for y, r in ratios)
               + f". Fires on any negative OCF, or below {limit:.2f} in two or more years."),
        confidence=conf, confidence_basis=cbasis, proxies=tuple(proxies),
        evidence=("Cash flow statement — 'operating profit before working capital changes' "
                  "in the indirect-method reconciliation",) if pre is None else ())


def s05_receivables_outpacing_revenue(panel: Panel) -> Outcome:
    """Receivables outpacing revenue (Layer 2/3, RC-REC). App D; §7 asset mix."""
    rec, rev = panel.window("trade_receivables"), panel.window("revenue")
    if rec is None or rev is None:
        return _needs(panel, ("trade_receivables", "revenue"))
    gr, gv = _growth(rec), _growth(rev)
    if gr is None or gv is None:
        return _abstain("Opening receivables or revenue is zero or negative, so growth "
                        "cannot be formed.")
    gap = gr - gv
    limit = TH.get("receivables_growth_gap")
    years = tuple(y for y, _ in rec)
    conf, cbasis = _confidence(panel, ("trade_receivables", "revenue"), years)

    # The DSO trend is reported alongside the growth gap, not instead of it. A gap can be
    # manufactured by a single weak base year; a DSO that lengthens every year cannot.
    dso = [(y, r / v * 365) for (y, r), (_, v) in zip(rec, rev) if v > 0]
    dso_note = ""
    if len(dso) >= 2:
        d0, d1 = dso[0][1], dso[-1][1]
        dso_note = (f" Days sales outstanding moved {d0:.0f} -> {d1:.0f} days over the same "
                    f"window ({d1 - d0:+.0f} days), which is the level behind the growth "
                    f"rates.")

    return Outcome(
        fired=gap > limit,
        observation=(f"Over {years[0]}–{years[-1]} trade receivables grew {_pct(gr)} "
                     f"while revenue grew {_pct(gv)} — a gap of {_pp(gap)}." + dso_note),
        trace=(f"receivables {_fmt(rec[0][1])} -> {_fmt(rec[-1][1])} ({_pct(gr)}); "
               f"revenue {_fmt(rev[0][1])} -> {_fmt(rev[-1][1])} ({_pct(gv)}); "
               f"gap {_pp(gap)} against a {limit * 100:.1f}pp threshold. "
               "DSO = receivables/revenue x 365 by year: "
               + "; ".join(f"{y} {d:.0f}d" for y, d in dso) + "."),
        confidence=conf, confidence_basis=cbasis)


def s06_accruals_heavy_earnings(panel: Panel) -> Outcome:
    """Accruals-heavy earnings (Layer 4, RC-REC). §9.1 accruals ratio.

    The denominator is AVERAGE total assets, not the closing balance. (PAT - OCF) is a
    flow over the year and total assets is a position at one instant; dividing the first
    by the second is not a rate, and it biases every growing entity downward. Averaging
    costs a year — the oldest year of the series has no opening balance inside it — so
    the rule reports on the years it can actually form rather than mixing two
    denominators in one series and calling the result a trend.
    """
    pat, ocf = panel.window("pat"), panel.window("ocf")
    if pat is None or ocf is None or panel.series("total_assets") is None:
        return _needs(panel, ("pat", "ocf", "total_assets"))
    years = tuple(y for y, _ in pat)
    limit = TH.get("accruals_ratio_high")

    avg = _avg_by_year(panel, "total_assets")
    if not avg:
        return _abstain("Total assets are bound for a single year only, so the average "
                        "balance the accruals ratio divides by cannot be formed.",
                        ("total_assets",), code=INPUT_SINGLE_YEAR)

    ratios = []
    for (y, p), (_, o) in zip(pat, ocf):
        a = avg.get(y)
        if a is None or a <= 0:
            continue
        ratios.append((y, (p - o) / a))
    if not ratios:
        return _abstain("No window year has both a profit figure and an average total-asset "
                        "balance; the oldest year of a series carries no opening balance.",
                        ("total_assets",))

    dropped = [y for y in years if y not in {x for x, _ in ratios}]
    high = [y for y, r in ratios if r > limit]
    conf, cbasis = _confidence(panel, ("pat", "ocf", "total_assets"), years)
    return Outcome(
        fired=len(high) >= 2,
        observation=(f"The accruals ratio exceeded {_pct(limit)} in "
                     f"{len(high)} of {len(ratios)} years"
                     + (f" ({', '.join(high)})" if high else "")
                     + f"; latest {_pct(ratios[-1][1])}."
                     + (f" {', '.join(dropped)} carries no opening balance inside the "
                        f"series, so no averaged ratio is formed for it."
                        if dropped else "")),
        trace=("accruals ratio = (PAT - OCF) / average total assets, the average being of "
               "opening and closing balances: "
               + "; ".join(f"{y} ({_fmt(dict(pat)[y])} - {_fmt(dict(ocf)[y])}) / "
                           f"{_fmt(avg[y])} = {_pct(r)}" for y, r in ratios)
               + f". Fires above {_pct(limit)} in two or more years."),
        confidence=conf, confidence_basis=cbasis)


def s13_leverage_driven_roe(panel: Panel) -> Outcome:
    """Leverage-driven return on equity (Layer 3, RC-FUND). §8.2 — decompose first.

    This is the specification's own worked example, and the rule that proves the FDR
    decomposes before it flags: an ROE improvement is only interesting once DuPont says
    WHAT drove it, and a leverage-driven gain is reclassified from a performance story
    to a solvency one — which changes the cluster it joins.
    """
    keys = ("pat", "revenue", "total_assets", "total_equity")
    raw = {k: panel.window(k) for k in keys}
    if any(v is None for v in raw.values()):
        return _needs(panel, keys)
    win: dict[str, list[tuple[str, float]]] = {k: v for k, v in raw.items() if v is not None}
    years = tuple(y for y, _ in win["pat"])

    def dupont(i: int) -> tuple[float, float, float, float] | None:
        pat, rev = win["pat"][i][1], win["revenue"][i][1]
        ta, te = win["total_assets"][i][1], win["total_equity"][i][1]
        if rev <= 0 or ta <= 0 or te <= 0:
            return None
        margin, turnover, mult = pat / rev, rev / ta, ta / te
        return margin, turnover, mult, margin * turnover * mult

    a, b = dupont(0), dupont(-1)
    if a is None or b is None:
        return _abstain("Revenue, total assets or equity is zero or negative in a window "
                        "year, so ROE cannot be decomposed.")
    (m0, t0, e0, roe0), (m1, t1, e1, roe1) = a, b
    if roe0 <= 0:
        return _abstain("Opening return on equity is zero or negative; an improvement "
                        "cannot be attributed.")

    # §8.2 asks for each factor to be traced SEPARATELY across the years, not only at the
    # endpoints. An endpoint comparison cannot tell a steady leverage build from a single
    # year's spike that has since unwound, and those are different planning stories.
    per_year = []
    for i, y in enumerate(years):
        f = dupont(i)
        if f is not None:
            per_year.append((y, f))
    factor_trace = "; ".join(
        f"{y} margin {_pct(m)} x turnover {t:.2f} x multiplier {e:.2f} = ROE {_pct(r)}"
        for y, (m, t, e, r) in per_year)

    improvement = (roe1 - roe0) / abs(roe0)
    conf, cbasis = _confidence(panel, keys, years)
    if improvement < TH.get("roe_improvement"):
        return Outcome(
            fired=False,
            observation=(f"Return on equity moved from {_pct(roe0)} to {_pct(roe1)} over "
                         f"{years[0]}–{years[-1]} — no material improvement to attribute."),
            trace=(f"DuPont by year — {factor_trace}. Relative change in ROE "
                   f"{_pct(improvement)} against a {_pct(TH.get('roe_improvement'))} "
                   f"threshold."),
            confidence=conf, confidence_basis=cbasis)

    # Attribute the improvement across the three DuPont factors, in log space so the
    # multiplicative decomposition is additive and the shares actually sum to one.
    import math
    contrib = {}
    for name, x0, x1 in (("margin", m0, m1), ("turnover", t0, t1), ("leverage", e0, e1)):
        contrib[name] = math.log(x1 / x0) if x0 > 0 and x1 > 0 else 0.0
    total = sum(abs(v) for v in contrib.values()) or 1.0
    lev_share = contrib["leverage"] / total if contrib["leverage"] > 0 else 0.0

    flat = TH.get("operating_drift_tolerance")
    operating_flat = (abs(m1 / m0 - 1) < flat) and (abs(t1 / t0 - 1) < flat)
    fired = lev_share > TH.get("equity_multiplier_share") or (
        operating_flat and e1 > e0)

    return Outcome(
        fired=fired,
        observation=(f"Return on equity rose from {_pct(roe0)} to {_pct(roe1)} over "
                     f"{years[0]}–{years[-1]}. Net margin moved {_pct(m0)}->{_pct(m1)} and "
                     f"asset turnover {t0:.2f}->{t1:.2f}, while the equity multiplier moved "
                     f"{e0:.2f}->{e1:.2f}. {_pct(lev_share)} of the movement is attributable "
                     f"to leverage rather than to operating performance."),
        trace=(f"DuPont: ROE = margin x turnover x equity multiplier, each factor traced "
               f"separately across the window — {factor_trace}. "
               f"Log-share attributable to leverage {_pct(lev_share)} against a "
               f"{_pct(TH.get('equity_multiplier_share'))} threshold; operating factors "
               f"{'flat' if operating_flat else 'moved'} within "
               f"{_pct(flat)}."),
        confidence=conf, confidence_basis=cbasis,
        evidence=("Statement of changes in equity — movements that change the multiplier "
                  "without changing performance (buy-back, bonus, fair-value and "
                  "actuarial reserve movements)",))


def s15_finance_cost_vs_borrowings(panel: Panel) -> Outcome:
    """Finance cost not moving with borrowings (Layer 3, RC-FUND). App D.

    The primary measure is the IMPLIED RATE — finance cost over average total borrowings
    — because that is the figure a reviewer can hold against the interest-rate ranges the
    borrowings note discloses. The growth divergence is reported with it: a stable implied
    rate while both series move fast is a different reading from a rate that jumps, and
    the divergence alone cannot tell them apart.

    Every reconciling item on the derivation is stated on the output, because each of them
    puts cost into finance charges that services no borrowing, and any one can produce the
    whole divergence on its own.
    """
    fc = panel.window("finance_costs")
    lt = panel.window("long_term_borrowings")
    st = panel.window("short_term_borrowings")
    if fc is None or lt is None or st is None:
        return _needs(panel, ("finance_costs", "long_term_borrowings",
                                       "short_term_borrowings"))
    years = tuple(y for y, _ in fc)
    debt = [(y, lt[i][1] + st[i][1]) for i, (y, _) in enumerate(fc)]

    ta = panel.get("total_assets", years[-1])
    if ta and debt[-1][1] / ta < TH.get("borrowings_floor_share"):
        return _abstain("Borrowings are below 1% of total assets; a divergence ratio on "
                        "so small a base would be rounding, not a signal.")

    g_fc, g_debt = _growth(fc), _growth(debt)
    if g_fc is None or g_debt is None:
        return _abstain("Opening finance cost or borrowings is zero or negative, so a "
                        "divergence cannot be formed.")
    gap = g_fc - g_debt                       # signed; the direction is part of the reading
    limit = TH.get("finance_cost_divergence")
    conf, cbasis = _confidence(panel, ("finance_costs", "long_term_borrowings",
                                       "short_term_borrowings"), years)
    direction = "faster than" if g_fc > g_debt else "more slowly than"

    # Implied rate on the average borrowing balance. Needs one year more than the window,
    # so it is reported where it can be formed and its absence is stated where it cannot.
    combined = _sum_series(panel, ("long_term_borrowings", "short_term_borrowings"))
    implied: list[tuple[str, float]] = []
    if combined is not None and len(combined) >= 2:
        fc_by_year = dict(fc)
        for i in range(1, len(combined)):
            y = combined[i][0]
            avg = (combined[i - 1][1] + combined[i][1]) / 2.0
            if y in fc_by_year and avg > 0:
                implied.append((y, fc_by_year[y] / avg))

    # ---- integrity gate, BEFORE anything is reported ------------------------------
    #
    # §4.3: a failed integrity check bars a conclusion resting on the affected figure.
    # Both checks below say the same thing in two ways — the finance-cost numerator is
    # not interest on the borrowings denominator — and in both cases the DIVERGENCE is
    # contaminated by exactly the same items as the rate. So the whole signal abstains.
    # Reporting "finance cost outgrew borrowings" on a numerator that is mostly not
    # interest on those borrowings is not a weaker diagnostic; it is a wrong one.
    #
    # Naming the contamination in prose and reporting the number anyway was the first
    # version of this rule, and it was wrong: on ONGC it published an implied rate of 61%
    # and 63% as "the figure to hold against the interest-rate ranges disclosed in the
    # borrowings note", and that reached rank 1. No entity borrows at 61%.
    y_last = years[-1]
    leases = sum(v for v in (panel.get("lease_liabilities_nc", y_last),
                             panel.get("lease_liabilities_cl", y_last)) if v is not None)
    debt_last = debt[-1][1]
    lease_ratio = TH.get("lease_to_borrowings_ratio")
    if leases > 0 and debt_last > 0 and leases / debt_last > lease_ratio:
        return _abstain(
            f"Integrity check failed, so no rate or divergence is reported. Lease "
            f"liabilities of {_fmt(leases)} lakh are {leases / debt_last:.1f}x the "
            f"borrowings of {_fmt(debt_last)} lakh at {y_last}. Interest on lease "
            f"liabilities under Ind AS 116 sits inside finance cost while the lease "
            f"liability sits outside the borrowings denominator, so the ratio measures a "
            f"cost against a balance that does not carry it. The finance-cost note, split "
            f"between interest on borrowings, interest on lease liabilities and unwinding "
            f"of discount, is the evidence that would let this run.",
            ())

    ceiling = TH.get("implied_rate_ceiling")
    if implied and max(r for _, r in implied) > ceiling:
        worst_y, worst = max(implied, key=lambda x: x[1])
        return _abstain(
            f"Integrity check failed, so no rate or divergence is reported. The implied "
            f"borrowing rate computes to {_pct(worst)} in {worst_y}, above the "
            f"{_pct(ceiling)} bound on a credible borrowing cost. No entity borrows at "
            f"that price, so the finance-cost numerator is not interest on the borrowings "
            f"denominator. The usual causes, in order of size for a capital-intensive "
            f"entity: unwinding of the discount on decommissioning and site-restoration "
            f"provisions, which sits in finance cost and services no borrowing; interest "
            f"on lease liabilities; exchange differences on foreign-currency borrowings; "
            f"or borrowings bound at the wrong scale or from the wrong line. The "
            f"finance-cost note decomposition and the borrowings note are the evidence "
            f"that would let this run. The growth divergence is NOT reported either, "
            f"because it rests on the same contaminated numerator.",
            ())

    proxies: list[str] = []
    if implied:
        rate_txt = (" Implied borrowing rate — finance cost over average total borrowings — "
                    + "; ".join(f"{y} {_pct(r)}" for y, r in implied)
                    + ". This is the figure to hold against the interest-rate ranges "
                      "disclosed in the borrowings note; no rate is assumed here.")
        rate_swing = max(r for _, r in implied) - min(r for _, r in implied)
    else:
        rate_txt = (" The implied borrowing rate could not be formed: an average borrowing "
                    "balance needs one year more than the trend window, and the series does "
                    "not reach it.")
        rate_swing = None
        proxies.append(
            "Implied rate on average total borrowings -> not formed; the reading rests on "
            "the growth divergence alone, which cannot separate a stable rate on fast-"
            "growing debt from a changing rate.")
        conf = _cap(conf, MEDIUM)
        cbasis += (" Capped at MEDIUM because the implied-rate measure could not be formed "
                   "and only the growth divergence remains.")

    lease = any(panel.get(k, years[-1]) is not None
                for k in ("lease_liabilities_nc", "lease_liabilities_cl"))

    return Outcome(
        fired=abs(gap) > limit,
        observation=(f"Over {years[0]}–{years[-1]} finance cost moved {_pct(g_fc)} while "
                     f"total borrowings moved {_pct(g_debt)} — finance cost grew "
                     f"{direction} the debt it services, a divergence of {_pp(gap)}."
                     + rate_txt
                     + (f" The implied rate itself moved across a band of "
                        f"{_pp(rate_swing)}." if rate_swing is not None else "")
                     + (" Lease liabilities are recognised and are NOT inside the borrowings "
                        "denominator, while interest on them sits inside finance cost — "
                        "eliminate that before reading the divergence." if lease else "")),
        trace=(f"finance cost {_fmt(fc[0][1])} -> {_fmt(fc[-1][1])} ({_pct(g_fc)}); "
               f"borrowings (long + short) {_fmt(debt[0][1])} -> {_fmt(debt[-1][1])} "
               f"({_pct(g_debt)}); divergence {_pp(gap)} against a "
               f"{limit * 100:.1f}pp threshold. "
               "Implied rate = finance cost / average total borrowings: "
               + ("; ".join(f"{y} {_pct(r)}" for y, r in implied) if implied
                  else "not formed") + "."),
        confidence=conf, confidence_basis=cbasis, proxies=tuple(proxies),
        evidence=(
            "Finance cost note — the 'less: amount capitalised' deduction",
            "Borrowings note — interest rates, terms of repayment and the maturity profile",
            "Provisions note — unwinding of discount on decommissioning and site restoration",
        ))


def s18_non_cash_gains(panel: Panel) -> Outcome:
    """Non-cash gains (Layer 4, RC-EST). §9.1 recurring vs one-off.

    The derivation names fair-value gains, write-backs, exchange gains and disposal
    profits over PROFIT BEFORE TAX. Those four items live in the other-income note, not on
    the face of the statement, so total other income stands in as the OUTER BOUND on the
    non-cash component — it cannot be smaller than the part of itself that is non-cash.
    The measure therefore overstates, since interest and dividend income are cash and sit
    inside the proxy, and it is capped and labelled accordingly.

    The denominator is PBT rather than PAT: tax is a consequence of the result, and
    putting a pre-tax income item over a post-tax profit mixes the two.
    """
    oi, pbt, ocf = (panel.window("other_income"), panel.window("pbt"), panel.window("ocf"))
    if oi is None or pbt is None or ocf is None:
        return _needs(panel, ("other_income", "pbt", "ocf"))
    years = tuple(y for y, _ in oi)
    limit = TH.get("other_income_share_of_pbt")

    y, o, p, c = years[-1], oi[-1][1], pbt[-1][1], ocf[-1][1]
    if p <= 0:
        return _abstain("Profit before tax is zero or negative in the latest year, so a "
                        "share of profit cannot be formed.")
    share = o / p
    # The concern is other income carrying the result while cash does not follow it.
    cash_follows = c >= p
    conf, cbasis = _confidence(panel, ("other_income", "pbt", "ocf"), years)
    conf = _cap(conf, MEDIUM)
    cbasis += (" Capped at MEDIUM: the non-cash components are not separately bound, so "
               "total other income stands in as an upper bound on them.")

    by_year = "; ".join(f"{y_} {_pct(v / dict(pbt)[y_])}"
                        for y_, v in oi if dict(pbt).get(y_, 0.0) > 0)
    return Outcome(
        fired=share > limit and not cash_follows,
        observation=(f"In {y}, other income of {_fmt(o)} lakh represents {_pct(share)} of "
                     f"profit before tax of {_fmt(p)} lakh, while operating cash flow was "
                     f"{_fmt(c)} lakh — cash {'does' if cash_follows else 'does not'} "
                     f"follow the reported result. This is an UPPER BOUND on the non-cash "
                     f"component: the fair-value, write-back, exchange and disposal items "
                     f"the derivation names are inside this figure alongside interest and "
                     f"dividend income, which are cash."),
        trace=(f"other income / PBT = {_fmt(o)} / {_fmt(p)} = {_pct(share)} against a "
               f"{_pct(limit)} threshold; fires only where OCF ({_fmt(c)}) is below PBT. "
               f"By year: {by_year}."),
        confidence=conf, confidence_basis=cbasis,
        proxies=("Fair-value gains + write-backs + exchange gains + disposal profits -> "
                 "total other income, as the outer bound. The item-by-item split is in the "
                 "other-income note and is not bound. Caps this diagnostic at MEDIUM and "
                 "makes the share an overstatement.",),
        evidence=(
            "Other income note — the item-by-item breakdown",
            "Cash flow statement — the non-cash adjustments in the indirect-method "
            "reconciliation, which carry the same items and are required to agree",
        ))


def s19_other_income_sustainability(panel: Panel) -> Outcome:
    """Other-income sustainability (Layer 3/4, RC-EST). §9.1."""
    oi, ti = panel.window("other_income"), panel.window("total_income")
    if oi is None or ti is None:
        return _needs(panel, ("other_income", "total_income"))
    years = tuple(y for y, _ in oi)
    if ti[-1][1] <= 0:
        return _abstain("Total income is zero or negative in the latest year.")

    share = oi[-1][1] / ti[-1][1]
    g_oi, g_ti = _growth(oi), _growth(ti)
    if g_oi is None or g_ti is None:
        return _abstain("Opening other income or total income is zero or negative.")
    gap = g_oi - g_ti
    s_lim, g_lim = (TH.get("other_income_share_of_income"),
                    TH.get("other_income_growth"))
    conf, cbasis = _confidence(panel, ("other_income", "total_income"), years)

    # The derivation's primary ratio is other income over PBT, with the share of total
    # income as supporting context. PBT is optional in the panel, so it is reported where
    # bound and its absence is stated rather than substituted.
    pbt = panel.window("pbt")
    pbt_txt = ""
    if pbt is not None and pbt[-1][1] > 0:
        pbt_txt = (f" Against profit before tax it is {_pct(oi[-1][1] / pbt[-1][1])}, which "
                   f"is the ratio that shows how much of the result the stream carries.")

    return Outcome(
        fired=share > s_lim and gap > g_lim,
        observation=(f"Other income is {_pct(share)} of total income in {years[-1]} and "
                     f"grew {_pct(g_oi)} over {years[0]}–{years[-1]} against total income "
                     f"growth of {_pct(g_ti)}." + pbt_txt
                     + " The recurring / non-recurring split — interest and dividend "
                       "against write-backs and disposals — is not formed in this run: it "
                       "needs the other-income note line by line, and a large recurring "
                       "stream and a large one-off are the same number with different "
                       "planning consequences."),
        trace=(f"share = {_fmt(oi[-1][1])} / {_fmt(ti[-1][1])} = {_pct(share)} "
               f"(threshold {_pct(s_lim)}); growth gap {_pp(gap)} "
               f"(threshold {g_lim * 100:.1f}pp). Both must hold."
               + (f" Other income / PBT = {_pct(oi[-1][1] / pbt[-1][1])}."
                  if pbt is not None and pbt[-1][1] > 0 else
                  " Other income / PBT not formed: PBT is not bound across the window.")),
        confidence=conf, confidence_basis=cbasis,
        proxies=("Recurring / non-recurring split of other income -> not substituted; the "
                 "split is reported as not formed rather than approximated.",),
        evidence=("Other income note — the item-by-item breakdown, split between interest "
                  "and dividend income and non-recurring write-backs and disposals",))


def s02_payables_funding_growth(panel: Panel) -> Outcome:
    """Payables funding growth (Layer 2, RC-WC). §7 funding structure.

    Two tests, and the second is what makes this different from S01. (a) Payables growing
    faster than revenue AND faster than the receivables-plus-inventory the business needed
    to fund: an entity expanding on supplier credit rather than on its own working
    capital. (b) Payables days rising while DSO and inventory days are flat — the pattern
    that separates supplier funding from a general expansion of the working-capital cycle.
    Either fires; both firing is the strong case.
    """
    keys = ("trade_payables", "cost_of_materials_consumed", "revenue",
            "trade_receivables", "inventories")
    raw = {k: panel.window(k) for k in keys}
    if any(v is None for v in raw.values()):
        return _needs(panel, keys)
    win: dict[str, list[tuple[str, float]]] = {k: v for k, v in raw.items() if v is not None}
    years = tuple(y for y, _ in win["trade_payables"])

    # (a) growth comparison
    need = [(y, win["trade_receivables"][i][1] + win["inventories"][i][1])
            for i, y in enumerate(years)]
    g_ap = _growth(win["trade_payables"])
    g_rev = _growth(win["revenue"])
    g_need = _growth(need)
    if g_ap is None or g_rev is None or g_need is None:
        return _abstain("Opening payables, revenue or funded working capital is zero or "
                        "negative, so a growth comparison cannot be formed.")
    limit = TH.get("payables_growth_gap")
    outpaces = (g_ap - g_rev > limit) and (g_ap - g_need > limit)

    # (b) days comparison — payables days rising while the other two legs are flat
    def days(num: list[tuple[str, float]], den: list[tuple[str, float]]) -> list[tuple[str, float]]:
        return [(y, n / d * 365) for (y, n), (_, d) in zip(num, den) if d > 0]

    dpo = days(win["trade_payables"], win["cost_of_materials_consumed"])
    dso = days(win["trade_receivables"], win["revenue"])
    dio = days(win["inventories"], win["cost_of_materials_consumed"])
    rise = TH.get("payables_days_rise")
    flat = TH.get("working_capital_days_flat")
    pattern = False
    d_dpo = d_dso = d_dio = None
    if len(dpo) >= 2 and len(dso) >= 2 and len(dio) >= 2:
        d_dpo = dpo[-1][1] - dpo[0][1]
        d_dso = dso[-1][1] - dso[0][1]
        d_dio = dio[-1][1] - dio[0][1]
        pattern = d_dpo > rise and abs(d_dso) < flat and abs(d_dio) < flat

    conf, cbasis = _confidence(panel, keys, years)
    conf = _cap(conf, MEDIUM)
    cbasis += (" Capped at MEDIUM because payables days rest on cost of materials as a "
               "proxy for purchases, which the annual report does not disclose separately.")

    pattern_txt = (
        f" Payables days moved {d_dpo:+.0f} while DSO moved {d_dso:+.0f} and inventory days "
        f"{d_dio:+.0f}" + (" — supplier credit lengthening while the rest of the cycle held, "
                           "which is funding rather than expansion."
                           if pattern else ", so the days pattern alone does not carry it.")
        if d_dpo is not None else "")

    return Outcome(
        fired=outpaces or pattern,
        observation=(f"Over {years[0]}–{years[-1]} trade payables grew {_pct(g_ap)} against "
                     f"revenue growth of {_pct(g_rev)} and growth of {_pct(g_need)} in the "
                     f"receivables and inventory they fund." + pattern_txt),
        trace=(f"(a) payables {_fmt(win['trade_payables'][0][1])} -> "
               f"{_fmt(win['trade_payables'][-1][1])} ({_pct(g_ap)}); revenue {_pct(g_rev)}; "
               f"receivables + inventories {_pct(g_need)}; fires when payables exceed BOTH "
               f"by more than {limit * 100:.1f}pp. "
               f"(b) payables days = payables / cost of goods sold x 365: "
               + "; ".join(f"{y} {d:.0f}d" for y, d in dpo)
               + f"; fires on a rise above {rise:.0f}d while DSO and inventory days each "
                 f"move less than {flat:.0f}d."),
        confidence=conf, confidence_basis=cbasis,
        proxies=("Purchases (the payables-days denominator) -> cost of materials consumed. "
                 "Caps this diagnostic at MEDIUM.",),
        evidence=(
            "Trade payables ageing schedule — the Schedule III mandatory schedule",
            "Disclosure of dues to micro and small enterprises, including interest payable "
            "under section 22 of the MSMED Act, 2006 — a regularity matter in its own right",
        ))


def s10_non_current_other_share(panel: Panel) -> Outcome:
    """Rising non-current-other share (Layer 2, RC-CAP). §7 asset mix.

    The signal is the DRIFT in the share, not its level. A residual bucket that grows as a
    proportion of the balance sheet is where amounts go when they do not fit anywhere
    else, and the composition of the note is what turns a share movement into a lead.
    """
    onca = panel.window("other_non_current_assets")
    ta = panel.window("total_assets")
    if onca is None or ta is None:
        return _needs(panel, ("other_non_current_assets", "total_assets"))
    years = tuple(y for y, _ in onca)

    ofa = panel.window("other_financial_assets_nc")
    proxies: list[str] = []
    if ofa is not None:
        parts = [(y, v + ofa[i][1]) for i, (y, v) in enumerate(onca)]
        basis = "other non-current assets plus other non-current financial assets"
    else:
        parts = list(onca)
        basis = "other non-current assets alone"
        proxies.append(
            "Other non-current assets + other non-current financial assets -> other "
            "non-current assets alone. The second line is presented separately under the "
            "Ind AS format and is not bound, so the share is a LOWER BOUND. The direction "
            "of the drift is unaffected; its size is understated.")

    shares = [(y, v / ta[i][1]) for i, (y, v) in enumerate(parts) if ta[i][1] > 0]
    if len(shares) < 2:
        return _abstain("Total assets are zero or negative in too many window years for a "
                        "common-sized share to be formed.")
    drift = shares[-1][1] - shares[0][1]
    limit = TH.get("non_current_other_drift")

    conf, cbasis = _confidence(panel, ("other_non_current_assets", "total_assets"), years)
    if proxies:
        conf = _cap(conf, MEDIUM)
        cbasis += (" Capped at MEDIUM: the share is a lower bound, one of its two "
                   "components not being bound.")

    return Outcome(
        fired=drift > limit,
        observation=(f"The share of total assets held as {basis} moved from "
                     f"{_pct(shares[0][1])} in {shares[0][0]} to {_pct(shares[-1][1])} in "
                     f"{shares[-1][0]} — a drift of {drift * 100:+.1f} percentage points."),
        trace=(f"common-sized share = {basis} / total assets: "
               + "; ".join(f"{y} {_pct(s)}" for y, s in shares)
               + f". Drift {drift * 100:+.1f}pp against a {limit * 100:.1f}pp threshold."),
        confidence=conf, confidence_basis=cbasis, proxies=tuple(proxies),
        evidence=("Other non-current assets note — the composition, which is what turns a "
                  "share movement into a lead",))


def s11_depreciation_vs_asset_base(panel: Panel) -> Outcome:
    """Depreciation not moving with the asset base (Layer 3, RC-CAP). App D.

    The derivation is depreciation over AVERAGE GROSS BLOCK, held against the useful lives
    disclosed in the accounting policy. Gross block lives in the PPE movement table and is
    not bound by the fact layer, so the average NET block stands in — and that substitution
    changes what the number means, not merely how precise it is. A net-block denominator
    drifts upward as an asset base ages, so the implied rate it produces is not comparable
    to a disclosed useful life. Only the TREND is read, the level is not, and the rule says
    so on its own output.
    """
    dep = panel.window("depreciation")
    if dep is None or panel.series("net_fixed_assets") is None:
        return _needs(panel, ("depreciation", "net_fixed_assets"))
    years = tuple(y for y, _ in dep)

    gross = panel.series("gross_block")
    proxies: list[str] = []
    if gross is not None and len(gross) >= 2:
        avg = {gross[i][0]: (gross[i - 1][1] + gross[i][1]) / 2.0
               for i in range(1, len(gross))}
        basis = "average gross block, as disclosed"
        comparable = True
    else:
        avg = _avg_by_year(panel, "net_fixed_assets")
        basis = "average NET block, standing in for gross block"
        comparable = False
        proxies.append(
            "Average gross block -> average net block. The gross carrying amount is in the "
            "PPE movement table, not on the face of the balance sheet. The implied rate is "
            "therefore NOT comparable to a disclosed useful life; only its trend is read. "
            "Caps this diagnostic at MEDIUM.")

    if not avg:
        return _abstain("The asset base is bound for a single year only, so the average "
                        "balance the implied rate divides by cannot be formed.",
                        ("net_fixed_assets",), code=INPUT_SINGLE_YEAR)

    # CWIP carries no depreciation and is excluded from the base where it is bound.
    cwip_avg = _avg_by_year(panel, "cwip")
    rates: list[tuple[str, float]] = []
    for y, d in dep:
        base = avg.get(y)
        if base is None:
            continue
        base -= cwip_avg.get(y, 0.0)
        if base > 0:
            rates.append((y, d / base))
    if len(rates) < 2:
        return _abstain("Too few years carry both a depreciation charge and an averaged "
                        "asset base for a trend in the implied rate to be formed.",
                        ("net_fixed_assets",))

    r0, r1 = rates[0][1], rates[-1][1]
    if r0 <= 0:
        return _abstain("The opening implied depreciation rate is zero or negative.")
    relative = (r1 - r0) / r0
    limit = TH.get("implied_depreciation_drift")

    conf, cbasis = _confidence(panel, ("depreciation", "net_fixed_assets"), years)
    if proxies:
        conf = _cap(conf, MEDIUM)
        cbasis += (" Capped at MEDIUM: the denominator is a net-block proxy for gross "
                   "block, so the level of the implied rate is not interpretable.")

    return Outcome(
        fired=relative < -limit,
        observation=(f"The implied depreciation rate moved from {_pct(r0)} in {rates[0][0]} "
                     f"to {_pct(r1)} in {rates[-1][0]}, a relative change of "
                     f"{_pct(relative)}, on a base of {basis}."
                     + ("" if comparable else
                        " Because the denominator is the net block, this rate is NOT "
                        "comparable to the useful lives disclosed in the accounting policy "
                        "— the comparison the derivation calls for needs the gross carrying "
                        "amount from the PPE movement table.")),
        trace=(f"implied rate = depreciation and amortisation / {basis}, CWIP excluded from "
               f"the base where bound: "
               + "; ".join(f"{y} {_pct(r)}" for y, r in rates)
               + f". Fires on a relative FALL greater than {_pct(limit)}."),
        confidence=conf, confidence_basis=cbasis, proxies=tuple(proxies),
        evidence=(
            "PPE movement table — gross carrying amount, additions, disposals and "
            "accumulated depreciation",
            "Accounting policy — useful lives adopted, and whether they follow Schedule II "
            "or a technical assessment",
        ))


def s14_short_term_funding_of_long_term_assets(panel: Panel) -> Outcome:
    """Short-term funding of long-term assets (Layer 2, RC-FUND). §7 capital structure.

    Two tests. (a) Non-current assets growing faster than the long-term funding available
    to carry them — equity plus non-current borrowings — means the difference was funded
    short. (b) Current borrowings, INCLUDING current maturities of long-term debt, as a
    share of total debt, and the movement in that share.

    Window is two years, not one: this needs the funding movement and the asset movement
    in the same period, and neither is a state.
    """
    keys = ("total_non_current_assets", "total_equity", "long_term_borrowings",
            "short_term_borrowings")
    raw = {k: panel.window(k, 2) for k in keys}
    if any(v is None for v in raw.values()):
        return _needs(panel, keys, 2)
    win: dict[str, list[tuple[str, float]]] = {k: v for k, v in raw.items() if v is not None}
    y0, y1 = win["total_non_current_assets"][0][0], win["total_non_current_assets"][-1][0]

    d_nca = (win["total_non_current_assets"][-1][1]
             - win["total_non_current_assets"][0][1])
    lt_funding = [(win["total_equity"][i][1] + win["long_term_borrowings"][i][1])
                  for i in (0, -1)]
    d_lt = lt_funding[1] - lt_funding[0]
    shortfall = d_nca - d_lt

    limit = TH.get("long_term_funding_shortfall")
    funded_short = d_nca > 0 and shortfall > limit * d_nca

    # (b) short debt share, including current maturities of long-term debt.
    cm = [panel.get("current_maturities_ltd", y) or 0.0 for y in (y0, y1)]
    short_debt = [win["short_term_borrowings"][i][1] + cm[j]
                  for j, i in enumerate((0, -1))]
    total_debt = [short_debt[j] + win["long_term_borrowings"][i][1]
                  for j, i in enumerate((0, -1))]
    s0 = short_debt[0] / total_debt[0] if total_debt[0] > 0 else None
    s1 = short_debt[1] / total_debt[1] if total_debt[1] > 0 else None
    share_limit = TH.get("short_debt_share")
    share_high = s1 is not None and s1 > share_limit

    conf, cbasis = _confidence(panel, keys, (y0, y1))
    cm_note = (" Current maturities of long-term debt are included in current borrowings "
               "for this share, per the derivation."
               if any(v > 0 for v in cm) else
               " Current maturities of long-term debt are not separately bound, so current "
               "borrowings alone carry the share and it is understated.")
    if not any(v > 0 for v in cm):
        conf = _cap(conf, MEDIUM)
        cbasis += (" Capped at MEDIUM: current maturities of long-term debt are not bound, "
                   "so the short-debt share is a lower bound.")

    share_txt = (f" Current borrowings are {_pct(s1)} of total debt in {y1}, from "
                 f"{_pct(s0)} in {y0}." if s0 is not None and s1 is not None else
                 " The short-debt share could not be formed: total debt is zero in a "
                 "comparison year.")

    return Outcome(
        fired=funded_short or share_high,
        observation=(f"Between {y0} and {y1} non-current assets moved {_fmt(d_nca)} lakh "
                     f"while equity plus non-current borrowings moved {_fmt(d_lt)} lakh — "
                     f"a difference of {_fmt(shortfall)} lakh that long-term funding did "
                     f"not carry." + share_txt + cm_note),
        trace=(f"(a) change in non-current assets {_fmt(d_nca)} against change in "
               f"(equity + non-current borrowings) {_fmt(d_lt)}; shortfall {_fmt(shortfall)}, "
               f"fires above {_pct(limit)} of the asset movement. "
               f"(b) (current borrowings + current maturities) / total debt: "
               f"{y0} {_pct(s0) if s0 is not None else 'n/a'}, "
               f"{y1} {_pct(s1) if s1 is not None else 'n/a'}; fires above "
               f"{_pct(share_limit)}."),
        confidence=conf, confidence_basis=cbasis,
        evidence=("Borrowings note — the maturity profile, current maturities of long-term "
                  "debt, and any sanctioned but undrawn long-term facility",))


def s20_government_dependency(panel: Panel) -> Outcome:
    """High government-support dependency (Layer 4, RC-DEP). §3.3, Appendix G.

    The index the derivation names is (grants + subsidies + budgetary support) / total
    income, with the three components shown separately. Only one of the three is bound —
    the government-grant line in the cash flow statement — and grants RECEIVED in cash are
    not grants RECOGNISED in income, while subsidy presented inside revenue is not
    captured at all. So this runs as an explicitly PARTIAL index, capped at LOW, with its
    single component named. §9.4: reported with its components, never as a bare score.
    """
    ti = panel.window("total_income")
    if ti is None:
        return _needs(panel, ("total_income",))
    years = tuple(y for y, _ in ti)

    grant = panel.window("government_grant_cf")
    if grant is None:
        return _abstain(
            "No component of the dependency index is bound. The index needs grants "
            "recognised in income (Ind AS 20 note), subsidies (revenue and other income "
            "notes) and budgetary support; none is extracted, and the cash-flow grant line "
            "that would stand in for them is not bound either. Nothing is substituted and "
            "no index is reported.",
            ("government_grant_cf", "grants_in_income", "subsidies", "budgetary_support"))

    idx = [(y, abs(grant[i][1]) / ti[i][1]) for i, y in enumerate(years) if ti[i][1] > 0]
    if not idx:
        return _abstain("Total income is zero or negative in every window year.")

    limit = TH.get("dependency_index_high")
    latest = idx[-1][1]
    drift = idx[-1][1] - idx[0][1]
    rise = TH.get("dependency_index_rise")

    conf, cbasis = _confidence(panel, ("total_income", "government_grant_cf"), years)
    conf = _cap(conf, LOW)
    cbasis += (" Capped at LOW: one of the index's three components is bound, and it is a "
               "cash-flow receipt rather than the amount recognised in income.")

    return Outcome(
        fired=latest > limit or drift > rise,
        observation=(f"Government support represents {_pct(latest)} of total income in "
                     f"{idx[-1][0]}, from {_pct(idx[0][1])} in {idx[0][0]} — a movement of "
                     f"{drift * 100:+.1f} percentage points. This is a PARTIAL index: its "
                     f"one measured component is the government grant in the cash flow "
                     f"statement. Grants recognised in income, subsidies presented within "
                     f"revenue, and budgetary support are not measured, so the true "
                     f"dependency is at least this and may be materially more."),
        trace=("dependency index = (grants + subsidies + budgetary support) / total income; "
               "measured here as |government grant, cash flow| / total income: "
               + "; ".join(f"{y} {_pct(v)}" for y, v in idx)
               + f". Fires above {_pct(limit)}, or on a rise above "
                 f"{rise * 100:.1f} percentage points."),
        confidence=conf, confidence_basis=cbasis,
        proxies=("Grants + subsidies + budgetary support recognised in income -> the "
                 "government-grant line of the cash flow statement, alone. Caps this "
                 "diagnostic at LOW and makes the index a partial, understated measure.",),
        evidence=(
            "Ind AS 20 government grants note — grants recognised in the statement of "
            "profit and loss, and grants deducted from an expense",
            "Revenue and other income notes — subsidy presented as part of revenue",
        ))


# ---- the registry the evaluator reads ---------------------------------------------

def s27_investment_income_dependency(panel: Panel) -> Outcome:
    """Investment income dependency (Layer 3, RC-INV). EXTENSION — not Appendix D.

    Distinct from S19, and the difference is the point. S19 asks whether other income is
    SUSTAINABLE — is it growing faster than the business that is supposed to produce it.
    This asks how much of the reported result DEPENDS on it: an entity whose other income is
    flat, unremarkable and equal to two thirds of profit before tax raises nothing under S19
    and is still an entity whose result is mostly not operating.

    PERSISTENCE, NOT A SINGLE YEAR. A disposal gain puts one year over any threshold, and
    §9.1 requires the recurring and the one-off to be told apart. So the share must hold in
    at least two years of the window. That is a weak test of recurrence and it is honest
    about being one: the strong test needs the other-income note line by line, which this
    rule states it did not have.
    """
    oi, pbt = panel.window("other_income"), panel.window("pbt")
    if oi is None or pbt is None:
        return _needs(panel, ("other_income", "pbt"))
    years = tuple(y for y, _ in oi)

    # A year with a non-positive PBT has no meaningful share — a ratio over a loss is not a
    # dependency, it is a sign change. Those years are excluded and the exclusion is stated,
    # because silently dropping them would let a loss-making year masquerade as coverage.
    usable = [(y, v / p) for (y, v), (_, p) in zip(oi, pbt) if p > 0]
    excluded = [y for (y, _), (_, p) in zip(oi, pbt) if p <= 0]
    if len(usable) < 2:
        return _abstain(
            "Profit before tax is zero or negative in all but "
            f"{len(usable)} year(s) of {years[0]}–{years[-1]}, so the dependency share "
            "cannot be formed on a comparable basis. Where the operating result is itself "
            "negative, the whole of other income already exceeds it — which is a finding for "
            "the performance layer, not a ratio for this one.")

    lim = TH.get("investment_income_share_of_pbt")
    need = int(TH.get("investment_income_persistence_years"))
    over = [(y, sh) for y, sh in usable if sh > lim]
    conf, cbasis = _confidence(panel, ("other_income", "pbt"), years)

    latest_y, latest_sh = usable[-1]
    excl_txt = (f" {', '.join(excluded)} excluded: profit before tax is not positive."
                if excluded else "")

    return Outcome(
        fired=len(over) >= need,
        observation=(
            f"Other income is {_pct(latest_sh)} of profit before tax in {latest_y}, and "
            f"exceeds {_pct(lim)} in {len(over)} of the {len(usable)} comparable years."
            + excl_txt
            + " On that basis a substantial part of the reported result is not produced by "
              "the operating business. Which part of it is recurring — interest and dividend "
              "against disposal gains and write-backs — is not formed in this run: it needs "
              "the other-income note line by line, and the planning consequence of a "
              "recurring stream and a one-off gain are not the same."),
        trace=(f"share = other_income / pbt, per year: "
               + "; ".join(f"{y} {_pct(sh)}" for y, sh in usable)
               + f". Threshold {_pct(lim)} in at least {need} year(s); "
                 f"met in {len(over)}."),
        confidence=conf, confidence_basis=cbasis,
        proxies=("Recurring / non-recurring split of other income -> not substituted. "
                 "Persistence across years stands in for it, and is a weaker test.",),
    )


RULES: dict[str, Callable[[Panel], Outcome]] = {
    "S01": s01_cash_conversion_cycle,
    "S02": s02_payables_funding_growth,
    "S03": s03_net_current_liabilities,
    "S04": s04_weak_operating_cash_flow,
    "S05": s05_receivables_outpacing_revenue,
    "S06": s06_accruals_heavy_earnings,
    "S10": s10_non_current_other_share,
    "S11": s11_depreciation_vs_asset_base,
    "S13": s13_leverage_driven_roe,
    "S14": s14_short_term_funding_of_long_term_assets,
    "S15": s15_finance_cost_vs_borrowings,
    "S18": s18_non_cash_gains,
    "S19": s19_other_income_sustainability,
    "S20": s20_government_dependency,
    "S27": s27_investment_income_dependency,
}

IMPLEMENTED = frozenset(RULES)


def run(signal_id: str, panel: Panel) -> Outcome | None:
    """Run one rule. None when no rule exists yet for that signal."""
    fn = RULES.get(signal_id)
    return None if fn is None else fn(panel)
