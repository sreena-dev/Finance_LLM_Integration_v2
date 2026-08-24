"""
Deterministic arithmetic validators (req 3 / Part B). Pure functions over ParsedTable
— same inputs → same numbers, every result carries a reproducible trace. No LLM.

Checks:
  bs_equation      Total Assets == Total Equity & Liabilities            (HIGH signal)
  footing          each subtotal == sum of the leaf lines in its block   (clean flat blocks)
  note_to_face     face line value == its note schedule's total          (req 3a "assoc. notes")
  cash_flow_recon  opening + net change == closing cash                  (Ind AS 7)

Ambiguous structures ABSTAIN (reported, queued for human) rather than risk a false alarm.
"""
from __future__ import annotations
import re
from .config import ARITH_ABS_TOL, ARITH_REL_TOL
from .models import ParsedTable, Finding
from . import repository as R


def _close(expected: float, got: float) -> tuple[bool, float]:
    diff = got - expected
    tol = ARITH_ABS_TOL + ARITH_REL_TOL * abs(expected)
    return abs(diff) <= tol, diff


def _src(pt: ParsedTable, extra: dict | None = None) -> dict:
    s = {"table_id": pt.table_id, "page": pt.page, "statement": pt.statement}
    if extra:
        s.update(extra)
    return {k: v for k, v in s.items() if v is not None}


# ---- balance-sheet equation ------------------------------------------------
def check_balance_sheet_equation(bs: ParsedTable, doc_id: str) -> list[Finding]:
    assets = bs.find(r"^\s*total assets\b", r"\btotal assets\s*\(", r"^\s*total assets")
    eqliab = bs.find(r"total equity and liab", r"total equity & liab",
                     r"total equity.*liabilit", r"^\s*total liabilities and equity")
    if assets and not eqliab:
        # assets total present but no equity+liab grand total → the extracted BS table
        # is almost certainly truncated at the bottom. Surface it as a data-quality flag.
        return [Finding(tag="RISK_FLAG", check="bs_equation", area="balance_sheet",
                        status="FAIL", severity="MEDIUM",
                        observation="BS 'Total Equity & Liabilities' grand total not captured — "
                                    "the extracted table appears truncated; equation not verifiable",
                        evidence=["check the source page for the 'Total Equity and Liabilities' line",
                                  "re-extract the full balance sheet (bottom rows missing)"],
                        source=_src(bs))]
    if not assets or not eqliab:
        return [Finding(tag="COVERAGE", check="bs_equation", area="balance_sheet",
                        status="ABSTAIN", severity="NONE",
                        observation="could not locate both 'Total Assets' and 'Total Equity & Liabilities' rows",
                        evidence=["verify BS footer labels in the source table"], source=_src(bs))]
    out: list[Finding] = []
    for p in bs.periods:
        a, e = assets.values.get(p), eqliab.values.get(p)
        if a is None or e is None:
            continue
        ok, diff = _close(a, e)
        out.append(Finding(
            tag="COVERAGE" if ok else "FINDING", check="bs_equation", area="balance_sheet",
            status="PASS" if ok else "FAIL", severity="NONE" if ok else "HIGH",
            observation=(f"Balance sheet {'balances' if ok else 'does NOT balance'} for {p}"),
            trace=f"Total Assets {a:,.2f} vs Total Equity & Liabilities {e:,.2f} → diff {diff:,.2f}",
            gap=None if ok else diff,
            evidence=[] if ok else [
                f"reconcile Total Assets ({a:,.2f}) to Total Equity & Liabilities ({e:,.2f}) for {p}",
                "check for an omitted/duplicated line or a note-to-face misposting"],
            source=_src(bs, {"period": p})))
    return out


# ---- footing (subtotal == sum of its leaf lines) ---------------------------
def check_footing(pt: ParsedTable) -> list[Finding]:
    out: list[Finding] = []
    sum_idx = [i for i, r in enumerate(pt.rows) if r.role in ("sum", "total")]
    for i in sum_idx:
        prev = max([j for j in sum_idx if j < i], default=-1)
        block = pt.rows[prev + 1:i]
        leaves = [r for r in block if r.role == "line" and r.has_values()]
        srow = pt.rows[i]
        if len(leaves) < 2:                     # nothing to independently foot (e.g. grand total)
            continue
        for p in pt.periods:
            expected = srow.values.get(p)
            if expected is None:
                continue
            parts = [r.values.get(p) for r in leaves if r.values.get(p) is not None]
            if len(parts) < 2:
                continue
            got = round(sum(parts), 2)
            ok, diff = _close(expected, got)
            if ok:
                out.append(Finding(
                    tag="COVERAGE", check="footing", area=(srow.label or "subtotal"),
                    status="PASS", severity="NONE",
                    observation=f"'{srow.label}' foots for {p}",
                    trace=f"Σ {len(parts)} lines = {got:,.2f} = stated {expected:,.2f}",
                    source=_src(pt, {"period": p, "row": srow.label})))
            else:
                out.append(Finding(
                    tag="FINDING", check="footing", area=(srow.label or "subtotal"),
                    status="FAIL", severity="MEDIUM",
                    observation=f"'{srow.label}' does not foot for {p}",
                    trace=f"Σ {len(parts)} lines = {got:,.2f} vs stated {expected:,.2f} → diff {diff:,.2f}",
                    gap=diff,
                    evidence=[f"re-add the {len(parts)} lines under '{srow.label}' for {p}",
                              "check for a missed line or a mis-keyed figure in this block"],
                    source=_src(pt, {"period": p, "row": srow.label})))
    return out


# ---- note-to-face (req 3a: face line == its note schedule total) -----------
_TOTAL_ROW_RE = re.compile(r"total", re.I)


_NAMED_TOTAL_RE = re.compile(r"^\s*(grand\s+)?total\b", re.I)


def _note_total_row(note_pt: ParsedTable):
    named = [r for r in note_pt.rows if r.role in ("sum", "total") and r.has_values()
             and _NAMED_TOTAL_RE.match(r.label or "")]
    if named:
        return named[-1]                       # grand total is the last named 'Total …' row
    totals = [r for r in note_pt.rows if r.role == "total" and r.has_values()]
    return totals[-1] if totals else None


def _match_period(face_p: str, note_periods: list[str], idx: int) -> str | None:
    if face_p in note_periods:
        return face_p
    return note_periods[idx] if idx < len(note_periods) else None


def check_note_to_face(face: ParsedTable, doc_id: str, flavor: str = "standalone",
                       max_notes: int = 60) -> list[Finding]:
    """Tie each face line to its note schedule's total — but only when the match is
    UNAMBIGUOUS (exactly one note table for that number, within flavor, whose title
    shares a noun with the line, and with a clean total row). Otherwise ABSTAIN:
    a wrong-table 'mismatch' is worse than an honest 'couldn't confirm'."""
    idx = R.note_index(doc_id, flavor)
    out: list[Finding] = []
    seen = 0
    for r in face.rows:
        if r.role != "line" or not r.note:
            continue
        seen += 1
        if seen > max_notes:
            break
        cands = idx.get(str(r.note).upper())
        reason = None
        if not cands:
            reason = f"note {r.note} schedule not located"
        elif len(cands) > 1:
            reason = f"note {r.note} is ambiguous ({len(cands)} candidate schedules)"
        elif not R._token_overlap(r.label, cands[0].get("title") or ""):
            reason = f"note {r.note} title does not match '{r.label}' — not tied (avoids mis-match)"
        if reason:
            out.append(Finding(tag="COVERAGE", check="note_to_face", area=r.label,
                               status="ABSTAIN", severity="NONE", observation=reason,
                               evidence=[f"manually locate/confirm Note {r.note} for '{r.label}'"],
                               source=_src(face, {"note": r.note, "row": r.label})))
            continue
        note_pt = R.parse_note(cands[0])
        trow = _note_total_row(note_pt)
        if trow is None:
            out.append(Finding(tag="COVERAGE", check="note_to_face", area=r.label,
                               status="ABSTAIN", severity="NONE",
                               observation=f"no clear total row in Note {r.note} schedule",
                               source=_src(note_pt, {"note": r.note})))
            continue
        for k, p in enumerate(face.periods):
            fv = r.values.get(p)
            if fv is None:
                continue
            np_ = _match_period(p, note_pt.periods, k)
            nv = trow.values.get(np_) if np_ else None
            if nv is None:
                continue
            ok, diff = _close(fv, nv)
            out.append(Finding(
                tag="COVERAGE" if ok else "RISK_FLAG", check="note_to_face",
                area=r.label, status="PASS" if ok else "FAIL",
                severity="NONE" if ok else "MEDIUM",
                observation=(f"'{r.label}' ties to Note {r.note}" if ok
                             else f"'{r.label}' does NOT tie to Note {r.note} total (provisional)"),
                trace=f"face {fv:,.2f} vs Note {r.note} total {nv:,.2f} -> diff {diff:,.2f} ({p})",
                gap=None if ok else diff,
                evidence=[] if ok else [
                    f"reconcile '{r.label}' ({fv:,.2f}) to Note {r.note} total ({nv:,.2f}) for {p}"],
                source=_src(face, {"note": r.note, "row": r.label, "period": p,
                                   "note_table_id": note_pt.table_id})))
    return out


# ---- cash-flow reconciliation (Ind AS 7) -----------------------------------
def check_cash_flow(cf: ParsedTable) -> list[Finding]:
    net = cf.find(r"net (increase|decrease|change).*cash", r"net cash flow")
    opening = cf.find(r"cash.*equivalents? at the beginning", r"opening.*cash", r"at the beginning of the (year|period)")
    closing = cf.find(r"cash.*equivalents? at the end", r"closing.*cash", r"at the end of the (year|period)")
    if not (net and opening and closing):
        missing = [n for n, v in [("net change", net), ("opening", opening), ("closing", closing)] if not v]
        return [Finding(tag="COVERAGE", check="cash_flow_recon", area="cash_flow",
                        status="ABSTAIN", severity="NONE",
                        observation=f"cash-flow reconciliation not checkable — missing: {', '.join(missing)}",
                        source=_src(cf))]
    out: list[Finding] = []
    for p in cf.periods:
        o, n, c = opening.values.get(p), net.values.get(p), closing.values.get(p)
        if None in (o, n, c):
            continue
        ok, diff = _close(c, o + n)
        out.append(Finding(
            tag="COVERAGE" if ok else "FINDING", check="cash_flow_recon", area="cash_flow",
            status="PASS" if ok else "FAIL", severity="NONE" if ok else "HIGH",
            observation=(f"cash-flow reconciles for {p}" if ok else f"cash-flow does NOT reconcile for {p}"),
            trace=f"opening {o:,.2f} + net {n:,.2f} = {o + n:,.2f} vs closing {c:,.2f} → diff {diff:,.2f}",
            gap=None if ok else diff,
            evidence=[] if ok else [f"reconcile opening+net to closing cash for {p}"],
            source=_src(cf, {"period": p})))
    return out
