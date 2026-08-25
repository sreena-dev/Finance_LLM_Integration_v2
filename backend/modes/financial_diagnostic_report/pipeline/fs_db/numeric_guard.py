"""
Numeric guard — anti-hallucination check on an LLM answer (finance-core: the LLM
never computes). Every NUMBER in the answer must trace to either:
  - a figure printed in a SOURCE table (the LLM quoted it), or
  - a value the deterministic engine COMPUTED (a ratio, movement, or bound input).
Anything material that matches neither is an LLM-invented / self-computed figure
-> flagged. The guard REPORTS (it does not silently rewrite): the caller decides
whether to append a warning, strip a claim, or abstain.

Deterministic, stdlib-only, imports nothing from rag/.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .md_parser import parse_num

_CITE = re.compile(r"\[[\d,\s]+\]")                       # citation markers [1], [2, 3]
_FYRANGE = re.compile(r"\b(?:19|20)\d{2}\s*[-/]\s*\d{2,4}\b")   # FY 2024-25 / 2023-24
# commas only as proper 3-digit groups, so a trailing punctuation comma ("24,") isn't
# swallowed into the number.
_NUM = re.compile(r"\(?-?₹?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?")
_PCT = re.compile(r"\s*(%|per\s?cent|percentage\s*point|pp\b|basis\s*point|bps)", re.I)
_MULT = re.compile(r"\s*(x\b|times\b)", re.I)


@dataclass
class NumberCheck:
    text: str
    value: float
    kind: str            # figure | percent | multiple | integer | year
    material: bool
    status: str          # ok | unverified
    grounded_by: str     # source | computed | trivial | none


@dataclass
class GuardResult:
    clean: bool
    n_numbers: int
    checks: list[NumberCheck] = field(default_factory=list)
    unverified: list[NumberCheck] = field(default_factory=list)

    def summary(self) -> str:
        if self.clean:
            return f"numeric check PASSED — {self.n_numbers} figures, all trace to sources or computed values"
        vals = ", ".join(sorted({c.text.strip() for c in self.unverified})[:8])
        return (f"numeric check FLAGGED {len(self.unverified)} figure(s) not traceable to a "
                f"source cell or a deterministic computation: {vals}")

    def to_dict(self) -> dict:
        return {"clean": self.clean, "n_numbers": self.n_numbers,
                "unverified": [{"text": c.text.strip(), "value": c.value, "kind": c.kind}
                               for c in self.unverified]}


def source_values(mds: list[str]) -> list[float]:
    """Every numeric cell across the evidence tables' markdown."""
    out: list[float] = []
    for md in mds or []:
        for tok in re.split(r"[|\n]", md or ""):
            v = parse_num(tok.strip())
            if v is not None:
                out.append(v)
    return out


def _match(v: float, refs: list[float], tol_abs: float, tol_rel: float) -> bool:
    """Magnitude match (sign-agnostic: prose parens/flow-sign shouldn't matter),
    tolerant of the model's rounding via tol_abs + tol_rel*|ref|."""
    av = abs(v)
    for w in refs:
        aw = abs(w)
        if abs(av - aw) <= tol_abs + tol_rel * aw:
            return True
    return False


def _scan(text: str):
    text = _CITE.sub(" ", text)
    text = _FYRANGE.sub(" ", text)          # kill FY ranges before number scan
    for m in _NUM.finditer(text):
        raw = m.group(0)
        val = parse_num(raw)
        if val is None:
            continue
        tail = text[m.end():m.end() + 16]
        pct = bool(_PCT.match(tail))
        mult = bool(_MULT.match(tail))
        digits = re.sub(r"[^\d.]", "", raw).lstrip("0") or "0"
        intlen = len(digits.split(".")[0])
        has_dec = "." in raw
        has_comma = "," in raw
        yield raw, val, pct, mult, has_dec, has_comma, intlen


def guard(answer: str, sources: list[float], computed: list[float]) -> GuardResult:
    checks: list[NumberCheck] = []
    for raw, val, pct, mult, has_dec, has_comma, intlen in _scan(answer):
        is_year = (not has_dec and not pct and not mult and intlen == 4 and 1900 <= val <= 2100)
        if is_year:
            kind, material = "year", False
        elif pct:
            kind, material = "percent", True
        elif mult:
            kind, material = "multiple", True
        elif has_dec or has_comma or intlen >= 4:
            kind, material = "figure", True
        else:
            kind, material = "integer", False

        if not material:
            checks.append(NumberCheck(raw, val, kind, False, "ok", "trivial"))
            continue
        if kind in ("percent", "multiple"):
            # ratios are never legitimately a raw source cell; they must match a value
            # the deterministic engine COMPUTED. Tight absolute tolerance for rounding
            # (25.83 vs 25.8334) so an LLM-derived ratio (e.g. a prior-year margin) is caught.
            gb = "computed" if _match(val, computed, 0.15, 0.0) else "none"
        else:  # a monetary figure: quoted source cell or a deterministic input/derived value
            if _match(val, sources, 1.0, 0.002):
                gb = "source"
            elif _match(val, computed, 1.0, 0.002):
                gb = "computed"
            else:
                gb = "none"
        checks.append(NumberCheck(raw, val, kind, True,
                                  "ok" if gb != "none" else "unverified", gb))

    unver = [c for c in checks if c.status == "unverified"]
    return GuardResult(clean=not unver, n_numbers=len(checks), checks=checks, unverified=unver)
