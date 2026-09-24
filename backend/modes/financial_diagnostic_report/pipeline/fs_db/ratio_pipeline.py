"""
End-to-end ratio pipeline: parsed statements -> BIND -> DERIVE ratio inputs ->
COMPUTE (deterministic catalog) -> Report.

Every ratio-input figure is either a directly-bound trusted line, or DERIVED by
plain Python from trusted lines (ebit = pbt + finance_costs; averages = (cur+prior)/2).
An input that cannot be trusted is simply absent -> the catalog ABSTAINS that ratio
with a reason. No LLM, no guessed number. `embed_fn` (optional) enables the semantic
binding fallback and is injected by the caller, so this module imports nothing from `rag/`.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, replace

from . import computations as C
from .binding import Resolver, BindingReport, BoundLine, EmbedFn
from .llm_bind import ProposeFn
from .models import ParsedTable

# Placeholder names `md_parser` invents for columns whose header it could not read.
_SYNTHETIC_PERIOD = re.compile(r"^col_\d+$", re.I)


@dataclass
class RatioReport:
    entity: str | None
    period: str | None
    inputs: dict = field(default_factory=dict)            # canonical -> value
    input_provenance: dict = field(default_factory=dict)  # canonical -> (source, detail)
    computed: list = field(default_factory=list)          # OK Computations
    abstained: list = field(default_factory=list)         # ABSTAIN Computations
    binding: dict = field(default_factory=dict)           # statement -> BindingReport
    def to_dict(self) -> dict:
        return {
            "entity": self.entity, "period": self.period,
            "inputs": self.inputs, "input_provenance": self.input_provenance,
            "computed": [c.to_dict() for c in self.computed],
            "abstained": [c.to_dict() for c in self.abstained],
        }


# Canonical inputs bound 1:1 from a trusted face line of the BS / P&L. NOTE:
# `cash_and_cash_equivalents` and `trade_receivables` are bound by the resolver (confirmed
# live on ONGC) but were once missing from this list — a real bug that silently starved
# cash_ratio and basic_defense_interval of data that was actually available.
_DIRECT_INPUTS = (
    "revenue", "other_income", "total_income", "total_expenses", "pbt", "total_tax", "pat",
    "depreciation", "total_assets", "total_equity", "equity_share_capital",
    "reserves_and_surplus", "inventories", "cash_and_cash_equivalents", "trade_receivables",
    "net_fixed_assets", "finance_costs", "cost_of_materials_consumed",
    "purchases_of_stock_in_trade", "changes_in_inventories", "long_term_borrowings",
    "short_term_borrowings",
    # added with the sheet-mapped line specs: Other Expenses (S.No 29 expense ratios +
    # Basic Defense Interval), Trade Payables, Current Investments
    "sga_expenses", "trade_payables", "marketable_securities",
    # Debt-definition components (FDR Appendix G ratio 3). Bound separately so the debt
    # definition actually applied is stated in the trace rather than assumed.
    "lease_liabilities_nc", "lease_liabilities_cl", "current_maturities_ltd",
    # THE SAME BUG AS THE ONE RECORDED ABOVE, FOUND THE SAME WAY.
    #
    # These four are bound by the resolver and pass `trusted()`, and were absent from
    # this tuple — so they were computed, validated, and then dropped on the floor.
    # Measured across the 337 entity-years in the corpus, `total_non_current_assets` and
    # `other_non_current_assets` bound on ZERO of them, which read as a binder gap and
    # was nothing of the kind: the figures existed the whole time and had nowhere to go.
    #
    # Two Appendix-D signals depend on them and could therefore never fire for any
    # entity — S14 (short-term funding of long-term assets) and S10 (rising
    # non-current-other share). The other two complete the balance-sheet structure that
    # the FDR's own structure layer reads.
    "total_non_current_assets", "other_non_current_assets",
    "total_non_current_liabilities", "total_equity_and_liabilities",
)
# Cash Flow Statement inputs (FDR Appendix G ratios 14 and 15). Bound from a third
# statement, so they arrive through their own BindingReport, not the BS/PL pair.
_CF_INPUTS = (
    "ocf", "icf", "fcf_financing", "net_change_in_cash",
    "capex_ppe", "capex_intangibles", "capex_exploration", "government_grant_cf",
)
# Bound under a different key than the line spec's (canonical input <- LineSpec key).
# `disclosed_eps` is the EPS figure PRINTED on the face of the P&L. It is deliberately not
# called "eps": the catalog has an `eps` RATIO that recomputes EPS from PAT and the share
# count, and one name for both the disclosed figure and the recomputed one invites exactly
# the substitution an audit tool must never make — reporting the entity's own number as if
# the engine had verified it. Keeping them apart is also what makes the SRS §8 "EPS
# recompute" tie-out expressible: recomputed vs disclosed, as two separate figures.
_RENAMED_INPUTS = {"current_assets": "total_current_assets",
                   "current_liabilities": "total_current_liabilities",
                   "disclosed_eps": "eps"}
_DERIVED_INPUTS = ("ebit", "avg_total_assets", "avg_inventory", "avg_receivables",
                   "avg_payables", "avg_equity")

# Every canonical input this pipeline can EVER produce. Exposed so `computations.py` —
# which is stdlib-only and imports nothing from `fs_db` — can be cross-checked against it
# in the tests: a ratio whose inputs are absent from this set can never fire for any
# entity, and `computations.INPUT_SOURCE` has to keep saying so.
EMITTED_INPUTS = (frozenset(_DIRECT_INPUTS) | set(_RENAMED_INPUTS)
                  | set(_DERIVED_INPUTS) | set(_CF_INPUTS))


def _avg(cur: float | None, prior: float | None) -> tuple[float | None, str]:
    if cur is None:
        return None, ""
    if prior is None:
        return cur, "closing basis (no prior period)"
    return (cur + prior) / 2.0, "average (opening+closing)/2"


def build_ratio_inputs(bs: BindingReport, pl: BindingReport,
                       cf: BindingReport | None = None) -> tuple[dict, dict]:
    """Map trusted bound lines -> the canonical inputs the catalog expects, deriving
    ebit and period averages (capital_employed etc. are derived inside
    computations.py from raw bound inputs). Returns (inputs, provenance)."""
    B = {**bs.trusted(), **pl.trusted(), **(cf.trusted() if cf else {})}
    inp: dict = {}
    prov: dict = {}

    def direct(canon: str, key: str | None = None):
        ln = B.get(key or canon)
        if ln and ln.value is not None:
            inp[canon] = ln.value
            prov[canon] = (ln.provenance, ln.label)

    for c in _DIRECT_INPUTS:
        direct(c)
    for c in _CF_INPUTS:
        direct(c)
    for canon, key in _RENAMED_INPUTS.items():
        direct(canon, key)

    # derived: EBIT = PBT + finance costs (sheet Note 5)
    pbt, fin = B.get("pbt"), B.get("finance_costs")
    if pbt and fin and pbt.value is not None and fin.value is not None:
        inp["ebit"] = pbt.value + fin.value
        prov["ebit"] = ("derived", f"pbt {pbt.value:,.2f} + finance_costs {fin.value:,.2f}")

    # derived averages (need the comparative column)
    for canon, key in (("avg_total_assets", "total_assets"),
                       ("avg_inventory", "inventories"),
                       ("avg_receivables", "trade_receivables"),
                       ("avg_payables", "trade_payables"),
                       # FDR Appendix G defines ROE on AVERAGE equity, and the mapping
                       # sheet's Legend says so explicitly ("require BOTH the current
                       # year and prior year Balance Sheet"). `_avg` falls back to the
                       # closing figure when no comparative column parsed, and records
                       # that fallback in the provenance rather than hiding it.
                       ("avg_equity", "total_equity")):
        ln = B.get(key)
        if ln:
            a, how = _avg(ln.value, ln.prior)
            if a is not None:
                inp[canon] = a
                prov[canon] = ("derived", f"{how} of {key}")
    return inp, prov


def compute_from_parsed(bs_pt: ParsedTable | None, pl_pt: ParsedTable | None,
                        entity: str | None = None, period: str | None = None,
                        only: list[str] | None = None,
                        embed_fn: EmbedFn | None = None,
                        cf_pt: ParsedTable | None = None,
                        propose_fn: ProposeFn | None = None,
                        llm_shadow: bool = True) -> RatioReport:
    r = Resolver(embed_fn=embed_fn, propose_fn=propose_fn, llm_shadow=llm_shadow)
    bs = r.bind_statement(bs_pt, "BS")
    pl = r.bind_statement(pl_pt, "PL")
    # `cf_pt` is keyword-and-last on purpose: every existing caller passes BS and PL
    # positionally and must keep working untouched, with the cash-flow ratios simply
    # abstaining when no cash flow statement is supplied.
    cf = r.bind_statement(cf_pt, "CF")
    inputs, prov = build_ratio_inputs(bs, pl, cf)

    comps = C.run_ratios(inputs, keys=only)
    # An input the extraction misplaced is NOT an input the entity failed to disclose, and
    # a reviewer acts on those two very differently: one is a re-extraction ticket, the
    # other is a question for the auditee. `_detect_column_shift` separates them, so pass
    # the finding through to every abstain that names the affected input.
    shifted = {k: v for rep_ in (bs, pl, cf) for k, v in rep_.shifted.items()}
    if shifted:
        for c in comps:
            if c.status != "ABSTAIN":
                continue
            for key, ev in shifted.items():
                if key in c.trace:
                    c.notes.append(
                        f"{key}: EXTRACTION FAULT, not a disclosure gap — the row "
                        f"\"{ev['label'][:60]}\" is present with "
                        f"{', '.join(f'{p}={v:,.2f}' for p, v in ev['values_found_in'].items())} "
                        f"but its \"{ev['empty_period']}\" cell is empty. Re-extract this "
                        f"table; the figure is in the source.")

    rep = RatioReport(entity=entity, period=period, inputs=inputs, input_provenance=prov,
                      binding={"BS": bs, "PL": pl, "CF": cf})
    for c in comps:
        (rep.computed if c.status == "OK" else rep.abstained).append(c)
    return rep


def _header_of(md: str) -> str:
    for ln in (md or "").splitlines():
        if ln.strip().startswith("|"):
            return ln.strip()
    return ""


def _body_of(md: str) -> list[str]:
    """Every pipe row of `md` after its header (separator rows included; the parser
    drops them). Used to append a continuation chunk onto its parent table."""
    seen_header, out = False, []
    for ln in (md or "").splitlines():
        if not ln.strip().startswith("|"):
            continue
        if not seen_header:
            seen_header = True
            continue
        out.append(ln)
    return out


# Opening rows a balance sheet has and nothing else does.
_BS_OPENER_RX = re.compile(r"\|\s*(i\.?\s*)?assets\s*\||\|\s*\(?1\)?\s*\|?\s*non[- ]current assets",
                           re.I)


_PERIOD_CELL_RX = re.compile(r"as at|as of|year ended|period ended|for the year|\b(19|20)\d{2}\b",
                             re.I)
# A statement is COMPLETE once its closing rows are present. Joining stops there, which
# is what keeps a following note table (which can share the very same period columns)
# from being swallowed: we only ever append while something is demonstrably missing.
_COMPLETE_RX = {
    "BS": (re.compile(r"\|\s*total equity and liabilit", re.I),
           re.compile(r"\|\s*total current liabilit", re.I)),
    "PL": (re.compile(r"\|\s*(total comprehensive income|earnings per)", re.I),
           re.compile(r"\|\s*profit (for the|after tax)", re.I)),
    # A cash flow statement is complete only once it reaches its closing balance —
    # the three activity subtotals alone can all sit in a first chunk while the
    # reconciliation to closing cash (which `cash_flow_reconciles` needs) is overleaf.
    "CF": (re.compile(r"\|\s*cash and cash equivalents at the (end|close)", re.I),
           re.compile(r"\|\s*net (increase|decrease|change)[^|]{0,40}cash", re.I)),
}


def _period_cells(md: str) -> list[str]:
    """The period-bearing header cells, e.g. ['As at March 31, 2021', 'As at March 31,
    2020*']. Compared instead of the raw header line because a continuation chunk is
    routinely extracted with an extra leading blank column, which makes the header
    strings differ while the statement is plainly the same one."""
    head = _header_of(md)
    cells = [c.strip() for c in head.strip().strip("|").split("|")]
    return [c for c in cells if c and _PERIOD_CELL_RX.search(c)]


def _first_period_idx(md: str) -> int | None:
    cells = [c.strip() for c in _header_of(md).strip().strip("|").split("|")]
    for i, c in enumerate(cells):
        if c and _PERIOD_CELL_RX.search(c):
            return i
    return None


def _shift_rows(lines: list[str], delta: int, src_ncol: int) -> list[str]:
    """Re-align a chunk's rows by `delta` columns (>0 inserts leading blanks, <0 drops
    leading cells). Chunks of one statement are frequently extracted with DIFFERENT
    widths — ONGC 2020-21's consolidated Assets half has a leading unnamed ordinal column
    its Equity-and-Liabilities half lacks. Concatenated raw, every row of the second half
    lands one column left of the header describing it, so labels are read out of the
    wrong cell and the whole liabilities side fails to bind.

    Only rows that actually USE the source layout (`src_ncol` cells) are shifted. Within
    one chunk the rows are ragged: "| (2) | (b) Provisions | 24 | ... |" carries the
    leading ordinal while "| (c) Deferred tax liabilities (net) | 25 | ... |" omits it,
    so a blanket shift would strip the label off exactly the rows that never had it."""
    out = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if delta and len(cells) == src_ncol:
            cells = ([""] * delta + cells) if delta > 0 else cells[-delta:]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _ncol_of(md: str) -> int:
    return len(_header_of(md).strip().strip("|").split("|"))


def _join(base_md: str, other_md: str) -> str:
    """Append `other_md`'s rows to `base_md`, aligned to base's column layout."""
    b, o = _first_period_idx(base_md), _first_period_idx(other_md)
    delta = (b - o) if (b is not None and o is not None) else 0
    return base_md + "\n" + "\n".join(
        _shift_rows(_body_of(other_md), delta, _ncol_of(other_md)))


def _looks_complete(md: str, kind: str) -> bool:
    """Whole statement, not merely a chunk with closing rows. For a balance sheet BOTH
    ends must be present: the Equity-and-Liabilities half carries "Total equity and
    liabilities" on its own, so a closing-rows-only test calls it complete while the
    entire Assets side is still missing (ONGC 2020-21 consolidated, where the titled
    chunk IS that second half)."""
    closed = any(rx.search(md or "") for rx in _COMPLETE_RX.get(kind, ()))
    if kind == "BS":
        return closed and bool(_BS_OPENER_RX.search(md or ""))
    return closed


def _with_continuations(doc_id: str, primary: dict, kind: str = "BS",
                        max_chunks: int = 4) -> tuple[str, list[str]]:
    """A statement that runs over a PDF page break is stored as SEVERAL table_chunks,
    and only the first carries the title — so a title query alone silently returns
    half a statement. Confirmed on ONGC 2024-25, where the standalone balance sheet
    ends mid-way through non-current liabilities in tbl_0151 and the whole current-
    liabilities block (incl. Total current liabilities and short-term borrowings)
    sits in the untitled tbl_0152. That truncation abstained 9 ratios and made
    Debt-to-Equity/Debt-to-Total-Assets compute on long-term borrowings only.

    Continuation chunks are identified STRUCTURALLY, not by guesswork: the next
    table_ids in the same document, taken only while the chunk is untitled (or
    repeats the same title) AND its header row is byte-identical to the primary's
    (same Particulars/Note/period columns). A different statement never satisfies
    both. The join is then proved by the balance-sheet tie-outs in `binding.py`
    (assets_split / liabilities_split / balance_sheet_balances), so a wrong join
    surfaces as a FAILED check rather than a silently wrong ratio.
    Returns (joined_markdown, [table_ids_joined], [markdown_of_each_chunk_in_order]).

    The third return value exists because concatenating the markdown is NOT enough. Two
    chunks of one statement can carry different physical column counts — ONGC FY2023-24's
    cash flow prints its first half with a stepped-out subtotal column and its second half
    without — and a single column profile over the concatenation cannot describe both. The
    caller therefore re-parses the chunks individually and merges the parsed ROWS; the
    joined markdown is retained for the completeness and structure tests, which only read
    text."""
    from . import db
    md, joined = primary["table_md"], [primary["table_id"]]
    pieces = [primary["table_md"]]
    if _looks_complete(md, kind):
        return md, joined, pieces     # nothing missing — never append to a whole statement
    periods = _period_cells(md)
    if not periods:
        return md, joined, pieces
    # BACKWARD first. The chunk that carries the title is not always the statement's
    # FIRST chunk: ONGC 2020-21's consolidated balance sheet is titled on its Equity and
    # Liabilities half, with the whole Assets side sitting in an untitled chunk before
    # it. Joining only forward left every asset-based ratio abstaining. Expand back only
    # while the assets opener is still missing, so a complete statement is never extended.
    if kind == "BS" and not _BS_OPENER_RX.search(md):
        prev = db.query(
            """SELECT table_id, table_md FROM table_chunks
                 WHERE doc_id=%(d)s AND table_md IS NOT NULL AND table_id < %(t)s
                 ORDER BY table_id DESC LIMIT %(n)s""",
            {"d": doc_id, "t": primary["table_id"], "n": max_chunks})
        for r in prev:
            if _period_cells(r["table_md"]) != periods:
                break
            md = _join(r["table_md"], md)      # earlier chunk becomes the header/base
            joined.insert(0, r["table_id"])
            pieces.insert(0, r["table_md"])
            if _BS_OPENER_RX.search(r["table_md"]):
                break                 # assets side recovered — stop reaching back
    rows = db.query(
        """SELECT table_id, table_title, table_md
             FROM table_chunks
            WHERE doc_id=%(d)s AND table_md IS NOT NULL AND table_id > %(t)s
            ORDER BY table_id ASC LIMIT %(n)s""",
        {"d": doc_id, "t": primary["table_id"], "n": max_chunks})
    for r in rows:
        # Title is NOT a reliable continuation signal: the second half of a statement may
        # be untitled (ONGC 2020-21) or carry a page banner scraped as its title
        # ("MAKING A STRATEGIC MOVE", ONGC 2019-20). The period columns are the signal.
        if _period_cells(r["table_md"]) != periods:
            break                     # first non-continuation ends the run
        if not _body_of(r["table_md"]):
            break
        md = _join(md, r["table_md"])
        joined.append(r["table_id"])
        pieces.append(r["table_md"])
        if _looks_complete(md, kind):
            break                     # statement is now whole — stop before the notes
    return md, joined, pieces


# A statement is only reliably TAGGED with its flavor when the entity publishes both.
# Most unlisted PSUs publish standalone statements only and print a bare "Balance Sheet as
# at March 31, 2025" — RailTel, MECON, IOCL, IRFC, HUDCO, IFCI, IREDA, OMDC and Dakshin
# Gujarat Vij all do. Requiring the word "standalone" in the title therefore matched NOTHING
# for 12 of 40 sampled companies, and every ratio abstained on filings that plainly contain
# a balance sheet.
#
# So flavor is now ASYMMETRIC, which is how filings actually print: a consolidated statement
# always says "consolidated", while a standalone one frequently says nothing at all. Hence
# consolidated REQUIRES the word and standalone merely EXCLUDES it.
_CONSOLIDATED_RX = r"consolidat"

# Relaxing the filter alone is NOT safe — it lets notes and five-year digests win the title
# match (measured: IOCL's P&L slot went to "2. Summarised Statement of Profit and Loss:",
# IREDA's to the note "A. Tax recognized in Statement of Profit and Loss", Madras
# Fertilizers' balance sheet to a 6-row "Balance Sheet Items", Kerala's to a 3-row
# trifurcated summary). So every candidate is parsed and scored on STRUCTURE, and the best
# one wins. Structure is checked on the primary chunk only, before continuations are joined.
_BS_CLOSER_RX = re.compile(r"\|\s*total equity and liabilit|\|\s*total\s*\|", re.I)
_PL_REVENUE_RX = re.compile(r"^\(?[ivx0-9]*\)?\.?\s*(revenue from operation|turnover)", re.I)
_PL_PROFIT_RX = re.compile(r"^\(?[ivx0-9]*\)?\.?\s*profit\b.*\b(before tax|for the|after tax)", re.I)
_PL_TOTALS_RX = re.compile(r"^total (income|expenses)\b", re.I)
# A half-statement is a legitimate primary: ONGC 2020-21's consolidated balance sheet is
# titled on its Equity-and-Liabilities chunk with the whole Assets side in an untitled chunk
# BEFORE it, which `_with_continuations` recovers by reaching backward. So the content
# signals below only ADD score — they are never requirements — and the only hard rejections
# are the two that no genuine statement chunk can fail.
_MIN_VALUED_ROWS = 8


def _is_a_different_statement(pt, md: str, kind: str) -> bool:
    """True when the candidate is plainly one of the OTHER primary statements.

    The scorer below only ever ADDS points for the markers of the statement being sought,
    which is safe while every candidate is at least the right KIND of table. Once the
    candidate query matches on `section` as well as `table_title` that stops holding: in
    this corpus `section` frequently carries the heading of the FOLLOWING table, so a
    balance sheet is routinely offered as a P&L candidate. Where the filing's real P&L was
    never extracted, that balance sheet is then the ONLY candidate and wins on row count
    alone — BPCL FY2023-24 selected its standalone balance sheet as the Statement of Profit
    and Loss. Nothing downstream would notice: the P&L specs would simply bind whatever
    balance-sheet rows their patterns happened to match.

    So identification has to be able to say NO, not merely to prefer. A candidate is
    rejected when it carries the structural markers of a different statement and none of
    its own — an asymmetry that leaves genuinely combined presentations (a P&L continuing
    into other comprehensive income) untouched, because those still carry their own markers.
    """
    labels = [r.label or "" for r in pt.rows]
    has_bs = bool(_BS_OPENER_RX.search(md)) or bool(_BS_CLOSER_RX.search(md))
    has_pl = any(_PL_REVENUE_RX.search(l) or _PL_PROFIT_RX.search(l)
                 or _PL_TOTALS_RX.search(l) for l in labels)
    has_cf = any(re.search(r"(operating|investing|financing) activities", l, re.I)
                 for l in labels)
    if kind == "PL":
        return (has_bs or has_cf) and not has_pl
    if kind == "BS":
        return (has_pl or has_cf) and not has_bs
    if kind == "CF":
        return not has_cf                 # a cash flow statement without a single activity
    return False                          # block is unreachable for the three kinds above


def _score_statement(pt, md: str, kind: str, title: str, flav: str) -> float | None:
    """Rank one candidate. Returns None to REJECT, else a score (higher is better)."""
    valued = [r for r in pt.rows if any(v is not None for v in r.values.values())]
    if not pt.periods or len(valued) < _MIN_VALUED_ROWS:
        return None                       # a note stub or an unparsed table, not a statement
    if _is_a_different_statement(pt, md, kind):
        return None
    score = min(len(valued), 40) / 10.0
    if kind == "BS":
        score += 3 if _BS_OPENER_RX.search(md) else 0
        score += 3 if re.search(r"\|\s*(ii\.?\s*)?equity and liabilit", md, re.I) else 0
        score += 2 if _BS_CLOSER_RX.search(md) else 0
    elif kind == "CF":
        # Score the three Ind AS 7 activity blocks directly. Without this a cash flow
        # candidate was ranked on P&L cues it can never have, leaving only the row
        # count and the flavor bonus to separate the real statement from a note.
        labels = [r.label or "" for r in pt.rows]
        for rx in (r"operating activities", r"investing activities", r"financing activities"):
            score += 3 if any(re.search(rx, l, re.I) for l in labels) else 0
        score += 2 if any(re.search(r"cash and cash equivalents at the (end|close)", l, re.I)
                          for l in labels) else 0
    else:
        labels = [r.label or "" for r in pt.rows]
        score += 4 if any(_PL_REVENUE_RX.search(l) for l in labels) else 0
        score += 3 if any(_PL_PROFIT_RX.search(l) for l in labels) else 0
        score += 2 if any(_PL_TOTALS_RX.search(l) for l in labels) else 0
    # An explicitly flavor-tagged title is the strongest signal there is when present, so it
    # still wins outright — this keeps every filing that DOES tag its flavor (ONGC, GAIL Gas,
    # BPCL …) selecting exactly what it selected before.
    if re.search(flav, title or "", re.I):
        score += 6
    # More period columns than a statement has means a five-year/summary digest.
    if len(pt.periods) > 3:
        score -= 4
    return score


def _merge_chunkwise(concatenated, pieces: list[str], joined: list[str],
                     statement: str, page):
    """Re-parse each chunk on ITS OWN column layout, then merge the parsed rows.

    WHY THE CONCATENATION IS NOT ENOUGH
    -----------------------------------
    `_join` aligns chunks by shifting the continuation's cells so its first period column
    lines up with the primary's. That is the right correction for a chunk extracted with an
    extra leading blank column, and it is not sufficient when the chunks differ in how many
    physical columns EACH PERIOD occupies. ONGC FY2023-24's cash flow is exactly that case:
    the first chunk prints line items and stepped-out subtotals in two columns per year, the
    second prints one. Profiled as a single grid, the two layouts contradict each other, the
    per-period column grouping in `md_parser` is refuted by its own conflict test, and every
    subtotal in the first chunk — `Net cash generated by operating activities` among them —
    is read into the wrong year.

    Parsing each chunk separately gives each its own honest column profile. The rows are then
    merged on period NAME, which is safe precisely because `_with_continuations` only ever
    joins chunks whose period header cells are identical.

    Falls back to the concatenated parse whenever the pieces do not agree on period names,
    or would yield fewer figures than the concatenation already produced — this may only
    improve on what is there.
    """
    from .md_parser import parse_table_md

    parts = []
    for piece, tid in zip(pieces, joined):
        p = parse_table_md(piece, table_id=tid, statement=statement, page=page)
        if p.rows and p.periods:
            parts.append(p)
    if len(parts) < 2:
        return concatenated

    base = parts[0]
    canonical = [c for c in base.periods if not _SYNTHETIC_PERIOD.match(str(c))]
    if not canonical:
        return concatenated
    for p in parts[1:]:
        if not set(canonical) & set(p.periods):
            return concatenated       # names disagree — the merge would be positional guesswork

    # EVERY row, in order, with nothing dropped. Deduplicating identical (label, values)
    # pairs was tried and silently broke the balance sheet: Schedule III repeats blank and
    # section rows, `binding.py` derives parent/child depth from the row SEQUENCE, and
    # collapsing those repeats merged sibling blocks — `trade_payables` and
    # `net_fixed_assets`, both of which are sums over children, stopped binding on a filing
    # where they had bound before. The chunks cannot overlap in the first place: each is
    # parsed with its own header row consumed as a header, so no row is read twice.
    rows = []
    for p in parts:
        for row in p.rows:
            vals = {c: row.values.get(c) for c in canonical}
            rows.append(replace(row, idx=len(rows), values=vals))

    # Compare the two readings ON THE SAME PERIODS. A raw figure count would compare a
    # 2-column reading against a 4-column one and always prefer the wider — which is the
    # misfiled reading this function exists to replace, since its extra "periods" are the
    # split halves of the real ones. Restricted to the periods the filing actually named,
    # the question is the honest one: which reading recovers more figures for those years?
    def _figures_in(pt, periods: list[str]) -> int:
        return sum(1 for row in pt.rows for p in periods
                   if row.values.get(p) is not None)

    merged = replace(base, rows=rows, periods=canonical,
                     warnings=list(base.warnings) + [
                         "chunks re-parsed individually and merged on period name; each "
                         "chunk's own column layout was honoured"])
    if _figures_in(merged, canonical) < _figures_in(concatenated, canonical):
        return concatenated
    return merged


def _statement_by_title(doc_id: str, include_rx: str, exclude_rx: str, statement: str,
                        flavor: str = "standalone"):
    """Select a primary statement by TITLE + STRUCTURE, then append any continuation chunks
    it was split into. We deliberately do NOT key off `financial_stmt_type`: in finance_llm
    it is mislabeled for some filings (e.g. ONGC's real P&L is tagged 'balance_sheet', and a
    retained-earnings statement is tagged 'profit_loss'), which silently feeds the wrong
    table into the ratios."""
    from . import db
    from .md_parser import parse_table_md
    consolidated = str(flavor).lower().startswith("consolidat")
    flav = "consolidated" if consolidated else "standalone"
    rows = db.query(
        """SELECT table_id, table_title, page_pdf_start, page_ocr_start, table_md
             FROM table_chunks
            WHERE doc_id=%(d)s AND table_md IS NOT NULL
              AND table_title ~* %(inc)s AND table_title !~* %(exc)s
              AND (%(cons)s = (table_title ~* %(crx)s))
            ORDER BY length(table_title) ASC, table_id ASC LIMIT 25""",
        # ^ TITLE ONLY, DELIBERATELY. Matching `section` as well was tried, because on some
        # filings the title is mis-attributed (every typed statement in BPCL FY2023-24
        # carries the PRECEDING table's heading, so its balance sheet is titled "STANDALONE
        # BALANCE SHEET" while its `section` reads "STANDALONE STATEMENT OF PROFIT AND
        # LOSS"). Measured over the 346-filing corpus it made coverage WORSE, and the
        # mis-attribution is why: `section` in this corpus is largely a FORWARD reference —
        # the heading of the table that FOLLOWS — so matching on it offers the table BEFORE
        # each statement, and the flavour bonus then scores those decoys as highly as the
        # statement itself. `total_assets` lost 11 filings, `ocf` 14 and `trade_receivables`
        # 12 before the change was backed out.
        #
        # The filings whose title is mis-attributed are a corpus defect to fix in the corpus.
        # What DID survive from the experiment is `_is_a_different_statement`, which was
        # written to contain the damage and is worth keeping on its own merits: it stops a
        # balance sheet ever being selected as a P&L, however the candidate was found.
        # ^ length ASC then table_id ASC survives as the TIEBREAK among equally-scored
        # candidates. A statement split mid-way often repeats its title on BOTH chunks
        # (ONGC's consolidated balance sheet: tbl_0264 = Assets, tbl_0265 = Equity and
        # Liabilities, identical titles). Without this the second half could win, and since
        # continuations are only ever joined FORWARD the assets side was never read at all.
        {"d": doc_id, "inc": include_rx, "exc": exclude_rx,
         "cons": consolidated, "crx": _CONSOLIDATED_RX})
    scored = []
    for n, cand in enumerate(rows):
        pt = parse_table_md(cand["table_md"], table_id=cand["table_id"], statement=statement)
        s = _score_statement(pt, cand["table_md"], statement, cand["table_title"], flav)
        if s is not None:
            scored.append((-s, n, cand))          # n preserves the SQL tiebreak order
    if scored:
        rows = [min(scored)[2]]
    else:
        rows = _statement_by_content(doc_id, statement, flav, include_rx, exclude_rx)
        if not rows:
            return None
    r = rows[0]
    # The PDF page is the citation an auditor can actually turn to; the OCR page is
    # what most filings in this corpus actually have. Falling back the same way
    # `repository._page` does — most tables here carry only `page_ocr_start`, and
    # citing neither is a worse answer than citing the one that exists.
    page = r["page_pdf_start"] or r.get("page_ocr_start")
    md, joined, pieces = _with_continuations(doc_id, r, statement)
    pt = parse_table_md(md, table_id=r["table_id"], statement=statement, page=page)
    if len(joined) > 1:
        pt = _merge_chunkwise(pt, pieces, joined, statement, page)
        pt.warnings.append(f"statement assembled from {len(joined)} table chunks: "
                           f"{', '.join(joined)}")
    if r.get("_by_content"):
        pt.warnings.append(f"statement located by CONTENT, not title ({r['table_id']} is "
                           f"untitled); anchored to the {flav} Statement of Profit and Loss")
    return pt


def _statement_by_content(doc_id: str, statement: str, flav: str,
                          include_rx: str, exclude_rx: str) -> list[dict]:
    """Last resort when a statement carries NO title. ONGC 2020-21's standalone balance
    sheet is tbl_0092 + tbl_0093, both with table_title NULL, so the title query returns
    nothing and every balance-sheet ratio abstains on an annual report that plainly
    contains one.

    We do not guess from content alone — content decides WHICH table, the correctly
    titled Statement of Profit and Loss of the SAME flavor decides WHERE to look. A
    balance sheet is printed immediately before its P&L, so we scan back a few chunks
    from that anchor and take the nearest one whose rows open like a balance sheet.
    Whatever this returns still has to satisfy the balance-sheet tie-outs."""
    if statement != "BS":
        return []
    from . import db
    anchor = db.query(
        """SELECT table_id FROM table_chunks
            WHERE doc_id=%(d)s AND table_md IS NOT NULL
              AND table_title ~* 'statement of profit|profit and loss'
              AND table_title ~* %(flav)s AND table_title !~* %(exc)s
            ORDER BY table_id ASC LIMIT 1""",
        {"d": doc_id, "flav": flav, "exc": exclude_rx})
    if not anchor:
        return []
    near = db.query(
        """SELECT table_id, table_title, page_pdf_start, page_ocr_start, table_md
             FROM table_chunks
            WHERE doc_id=%(d)s AND table_md IS NOT NULL AND table_id < %(a)s
              AND (table_title IS NULL OR table_title !~* %(exc)s)
            ORDER BY table_id DESC LIMIT 4""",
        {"d": doc_id, "a": anchor[0]["table_id"], "exc": exclude_rx})
    for r in near:                      # nearest first, walking back from the P&L
        if _BS_OPENER_RX.search(r["table_md"]) and _period_cells(r["table_md"]):
            r["_by_content"] = True
            return [r]
    return []


def movements(rep: "RatioReport") -> list[dict]:
    """Deterministic YoY movement for every trusted bound line that has a prior
    period. delta = current - prior; pct = delta / |prior| * 100. Pure arithmetic."""
    out = []
    for stmt in ("BS", "PL", "CF"):
        for key, ln in rep.binding.get(stmt, BindingReport(stmt)).trusted().items():
            if ln.value is None or ln.prior in (None, 0):
                continue
            delta = ln.value - ln.prior
            out.append({"key": key, "label": ln.label, "prior": ln.prior,
                        "current": ln.value, "delta": round(delta, 2),
                        "pct": round(delta / abs(ln.prior) * 100, 2)})
    return out


def computed_block(rep: "RatioReport", movers: list[dict] | None = None) -> str:
    """The authoritative block injected into the LLM prompt: every ratio & movement
    the deterministic engine produced, plus the list it could NOT compute (which the
    LLM must NOT calculate itself)."""
    movers = movements(rep) if movers is None else movers
    lines = ["COMPUTED FIGURES — deterministic Python from the cited statements.",
             "These are AUTHORITATIVE. Do NOT recompute, adjust, or derive new numbers.",
             # The block used to assert authority only against the MODEL's own arithmetic.
             # The other way it gets bypassed is a filing's OWN ratio disclosure: ONGC's
             # "Financial Highlights" table prints Return on Capital Employed 26.54% on
             # its own capital-employed base against this engine's 12.41%, and because
             # that table carries a [n] citation and this block does not, it was copied
             # in preference and labelled a computed figure. An entity's self-reported
             # ratio presented as engine-verified is the single worst output this tool
             # can produce, so the precedence is stated here as well as in the prompt.
             "PRECEDENCE: these values OUTRANK any ratio printed in a source table,",
             "including the filing's own 'Financial Highlights' / ratio-disclosure note,",
             "which is often computed on a different definition. Never substitute the",
             "entity's self-reported ratio for one given here; never back-fill a ratio",
             "absent here by reading it out of such a table. If a disclosed figure",
             "differs materially from the computed one, report the computed value, cite",
             "the disclosed one beside it, and flag the divergence as an observation.",
             "", "Ratios:"]
    # Tag the Schedule III eleven. Without this the block is an undifferentiated list,
    # and a question naming that set ("the 11 mandatory ratios") leaves the model to
    # guess which rows belong to it — it guessed differently per entity, listing 9
    # non-Schedule-III ratios for one filing and refusing outright for another.
    for c in rep.computed:
        tag = "  [SCHEDULE III MANDATORY]" if c.mandatory_sch3 else ""
        lines.append(f"  - {c.name} = {c.result:,.4f} {c.unit}   [{c.trace}]{tag}")
    if not rep.computed:
        lines.append("  (none could be computed from the available statements)")
    lines.append("\nYear-on-year movements (current vs prior):")
    for m in movers[:24]:
        lines.append(f"  - {m['label']}: {m['prior']:,.2f} -> {m['current']:,.2f} "
                     f"(delta {m['delta']:,.2f}, {m['pct']:+.2f}%)")
    if rep.abstained:
        # WHY each one abstained, not just its name. Handing the model a bare list of
        # names with "inputs unavailable" made it invent the cause, and the causes are
        # not interchangeable: a Schedule III concept this filing genuinely does not
        # report is a different animal from a figure our extraction dropped, and both
        # differ from a limit of this engine. Observed live: the model turned three
        # abstained working-capital ratios into a fabricated "Significant Finding —
        # Missing Efficiency Metrics ... Risk level: Potentially material" against the
        # ENTITY, and asked management to supply a methodology. That is a defect of
        # ours reported as a deficiency of theirs, which is the worst direction for an
        # audit tool to get wrong.
        lines.append("\nNOT COMPUTED — with the reason each one abstained:")
        for c in sorted(rep.abstained, key=lambda x: x.name):
            why = c.trace[len("ABSTAIN: "):] if c.trace.startswith("ABSTAIN: ") else c.trace
            lines.append(f"  - {c.name}: {why[:400]}")
            for n in c.notes:
                if "EXTRACTION FAULT" in n:
                    lines.append(f"      {n[:300]}")
        lines.append(
            "\nHOW TO REPORT A 'NOT COMPUTED' ROW — this is a limitation of THIS ENGINE "
            "or of the extraction, NOT a deficiency of the entity and NOT an audit "
            "finding. State 'not computed from the available statements' with the reason "
            "given above, and STOP THERE. Do NOT raise it as a finding, risk, "
            "observation or matter requiring management response; do NOT assign it a "
            "risk level or materiality; do NOT ask management to supply it; do NOT "
            "calculate it yourself. The entity has not failed to disclose anything "
            "merely because a row here is blank.")
    return "\n".join(lines)


def computed_values(rep: "RatioReport", movers: list[dict] | None = None) -> list[float]:
    """Every deterministic number, for the numeric guard: ratio results, bound input
    figures, and movement current/prior/deltas."""
    movers = movements(rep) if movers is None else movers
    vals: list[float] = [c.result for c in rep.computed if c.result is not None]
    vals += [v for v in rep.inputs.values() if isinstance(v, (int, float))]
    for m in movers:
        vals += [m["prior"], m["current"], m["delta"], m["pct"]]
    return vals


def compute_from_doc(doc_id: str, flavor: str = "standalone",
                     only: list[str] | None = None,
                     embed_fn: EmbedFn | None = None,
                     propose_fn: ProposeFn | None = None,
                     llm_shadow: bool = True) -> RatioReport:
    """Production entry: pull the parsed statements from finance_llm (by title), then compute."""
    # "summaris|summariz" excludes the multi-year digest tables ("2. Summarised Statement of
    # Profit and Loss:", IOCL) that the relaxed flavor filter otherwise lets compete.
    bs = _statement_by_title(doc_id, "balance sheet",
                             "(restat|reconcil|five year|segment|summaris|summariz)", "BS", flavor)
    pl = _statement_by_title(doc_id, "statement of profit|profit and loss",
                             "(retained|comprehensive|restat|reconcil|segment|five year"
                             "|summaris|summariz)", "PL", flavor)
    # "note|schedule" keeps the cash-flow NOTES (e.g. "Note 41: Cash flow from financing
    # activities — reconciliation of liabilities") from competing with the statement itself.
    cf = _statement_by_title(doc_id, "cash flow",
                             "(restat|reconcil|segment|five year|summaris|summariz"
                             "|note|schedule)", "CF", flavor)
    return compute_from_parsed(bs, pl, entity=doc_id, period=flavor, only=only,
                               embed_fn=embed_fn, cf_pt=cf, propose_fn=propose_fn,
                               llm_shadow=llm_shadow)
