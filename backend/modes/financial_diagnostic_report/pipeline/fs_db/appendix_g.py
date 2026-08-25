"""
FDR Appendix G — the sixteen diagnostics, as a CLOSED, FIXED-SHAPE block.

WHY THIS MODULE EXISTS (it is not a convenience wrapper)
--------------------------------------------------------
`computations.py` computes the figures and `ratio_pipeline.py` binds their inputs. What
was missing is the same guarantee `schedule3.py` already provides for the eleven
statutory ratios: that the ANSWER SHAPE is deterministic too.

Letting the LLM assemble this set produced, on consecutive runs of the same filing:

  - the entity's OWN disclosed ratios copied in place of the engine's (ONGC prints
    Return on Capital Employed 26.54% on its own capital-employed base against this
    engine's 12.41%, and because the filing's table carries a `[n]` citation and the
    computed block does not, the disclosed figure was preferred AND labelled a
    "Computed figure");
  - rows silently dropped whenever the filing's own ratio table did not happen to
    contain them, so an available diagnostic read as unavailable;
  - a different label template every run ("Computed figure: 1.3984 x [COMPUTED
    FIGURES]"), because the review prompt asks for per-statement labels and for a
    bracket citation after every figure, and a ratio table triggers both.

None of those are fixable by instructing the model harder — they are what happens when a
closed set is rendered by prose. Appendix G's membership is fixed by the specification,
exactly like Schedule III's is fixed by law, so it is rendered here instead: same sixteen
rows, same order, every time, with no LLM in the numeric path. An unavailable row states
its own reason in its own cell and is never blank, nil, dropped, or back-filled from the
filing's own ratio note.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

from . import computations as C
from . import ratio_pipeline as RP


@dataclass
class AppendixGRow:
    no: int
    diagnostic: str                  # the specification's own wording for the row
    key: str                         # catalog key that satisfies it
    value: float | None
    unit: str
    status: str                      # OK | ABSTAIN | MISSING
    formula: str
    trace: str
    reason: str = ""                 # why it abstained, in words a reviewer can act on
    notes: list[str] = field(default_factory=list)
    extraction_fault: bool = False
    # ^ the abstain is a column-shifted / dropped extraction, NOT a non-disclosure by
    #   the entity. Surfaced separately because the two need opposite follow-up: one is
    #   a re-extraction ticket, the other is a question for the auditee.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppendixGReport:
    doc_id: str
    flavor: str
    rows: list[AppendixGRow] = field(default_factory=list)
    computed: int = 0
    statements: dict = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        return d


def analyse(doc_id: str, flavor: str = "standalone", embed_fn=None) -> AppendixGReport:
    """The fixed-shape Appendix G block for one document. Sixteen rows, always."""
    rep = RP.compute_from_doc(doc_id, flavor=flavor, embed_fn=embed_fn)
    by = {c.key: c for c in rep.computed + rep.abstained}

    rows: list[AppendixGRow] = []
    for no, name, key in C.APPENDIX_G:
        c = by.get(key)
        if c is None:
            # Only reachable if APPENDIX_G names a key the catalog lost — the tests
            # assert against exactly this, so it should never appear in production.
            rows.append(AppendixGRow(no, name, key, None, "", "MISSING", "", "",
                                     reason="no formula for this diagnostic in the catalog"))
            continue
        reason = ""
        if c.status != "OK":
            reason = c.trace[len("ABSTAIN: "):] if c.trace.startswith("ABSTAIN: ") else c.trace
        rows.append(AppendixGRow(
            no, name, key, c.result if c.status == "OK" else None, c.unit, c.status,
            c.formula, c.trace, reason, list(c.notes),
            extraction_fault=any("EXTRACTION FAULT" in n for n in c.notes)))

    out = AppendixGReport(
        doc_id=doc_id, flavor=flavor, rows=rows,
        computed=sum(1 for r in rows if r.status == "OK"),
        statements={k: bool(v.bound) for k, v in rep.binding.items()})

    cf = rep.binding.get("CF")
    recon = next((v for v in (cf.validations if cf else [])
                  if v.get("check") == "cash_flow_reconciles"), None)
    if recon and recon.get("status") == "PASS":
        out.caveats.append(
            "Cash flow tie-out PASSED (operating + investing + financing = net change in "
            "cash), so the Accruals Ratio and Free Cash Flow rest on an operating-cash-flow "
            "figure confirmed by the statement's own identity.")
    elif any(r.key in ("accruals_ratio", "free_cash_flow") and r.status == "OK" for r in rows):
        out.caveats.append(
            "Cash flow tie-out did NOT run (not all three activity subtotals bound), so the "
            "cash-flow-based diagnostics below carry no independent confirmation of the "
            "operating-cash-flow figure.")
    if any(r.extraction_fault for r in rows):
        out.caveats.append(
            "One or more rows abstained because of an EXTRACTION fault, not a disclosure "
            "gap — the figure is present in the filing but landed outside the primary "
            "period column. These are re-extraction tickets, not audit findings.")
    return out


_NOT_A_FINDING = (
    "A row marked *not computed* is a limitation of this engine or of the table "
    "extraction. It is **not** a deficiency of the entity, not a disclosure failure and "
    "not an audit finding — it carries no risk rating and needs nothing from management."
)


def render_markdown(rep: AppendixGReport) -> str:
    """The user-facing answer. Emitted VERBATIM, with no LLM in the path, so the same
    filing always produces the same sixteen rows in the same order — no per-statement
    label prefixes, no invented citation tags, nothing dropped."""
    head = (f"**FDR Appendix G — the sixteen diagnostics — {rep.doc_id.replace('_', ' ')} "
            f"({rep.flavor})**\n\n"
            f"Computed **{rep.computed} of 16** from the filing's own statements. "
            f"Balance sheet: {'available' if rep.statements.get('BS') else '**not available**'}"
            f" · Statement of Profit and Loss: "
            f"{'available' if rep.statements.get('PL') else '**not available**'}"
            f" · Cash Flow Statement: "
            f"{'available' if rep.statements.get('CF') else '**not available**'}\n")
    L = [head,
         "| # | Diagnostic | Value | Status / why not available |",
         "|---|---|---|---|"]
    for r in rep.rows:
        if r.status == "OK":
            val = f"**{r.value:,.4f}** {r.unit}".strip()
            note = "computed"
        else:
            val = "**not computed**"
            note = (("**extraction fault — figure is in the filing** · " if r.extraction_fault
                     else "") + r.reason)
            note = note.replace("|", "/")[:400]
        L.append(f"| {r.no} | {r.diagnostic} | {val} | {note} |")

    L.append("\n**How each computed figure was derived**\n")
    for r in rep.rows:
        if r.status == "OK":
            L.append(f"- **{r.diagnostic}** — {r.formula}  \n  `{r.trace}`")

    caveats = list(rep.caveats)
    for r in rep.rows:
        for n in r.notes:
            if "PARTIAL COVERAGE" in n or "EXTRACTION FAULT" in n:
                caveats.append(f"**{r.diagnostic}** — {n}")
    if caveats:
        L.append("")
        for c in caveats:
            L.append(f"> {c}")
    L.append("")
    L.append(f"> {_NOT_A_FINDING}")
    return "\n".join(L)


def render_text(rep: AppendixGReport) -> str:
    """Plain-text equivalent for the CLI."""
    L = [f"FDR APPENDIX G — FORMULA LIBRARY   {rep.doc_id}  ({rep.flavor})",
         f"{rep.computed} of {len(rep.rows)} computed",
         "=" * 78]
    for r in rep.rows:
        if r.status == "OK":
            L.append(f"{r.no:2}. {r.diagnostic:34} {r.value:>16,.4f} {r.unit}")
        else:
            L.append(f"{r.no:2}. {r.diagnostic:34} {'—':>16} {r.status}")
            L.append(f"      {r.reason[:200]}")
    return "\n".join(L)
