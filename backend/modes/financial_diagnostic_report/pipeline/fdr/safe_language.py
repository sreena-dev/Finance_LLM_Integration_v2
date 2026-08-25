"""
§17 safe-language lint — enforced on the output, not requested of the model.

§17.2 forbids wording that converts a diagnostic into a finding, an opinion or a
prediction, or that attributes intent to any person. Asking a model nicely does not
achieve that; checking the rendered text does. The lint runs over every FDR before it is
released, including the ones the model did not write, because a phrase copied from a
filing or a template is exactly as prohibited as one it invented.

The lint is a HARD FAIL, not a warning. A report that fails is not published.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# §17.2, verbatim, plus the intent-attribution verbs §17.1 exists to replace.
PROHIBITED: tuple[tuple[str, str], ...] = (
    (r"\bproves?\b", "converts a diagnostic into proof"),
    (r"\bconfirms\b", "asserts confirmation the FDR cannot give"),
    (r"\bcertif(?:ies|y|ied)\b", "the FDR certifies nothing"),
    # "Guarantee" is prohibited in its PROMISE sense — the FDR guarantees nothing. It is not
    # prohibited as the name of a financial instrument: a financial guarantee contract is
    # defined by Ind AS 109, guarantees given for subsidiaries are a disclosed contingent
    # liability, and RC-CONT exists precisely to raise them. Without this carve-out the
    # cluster cannot name its own subject matter, and the pressure is then to describe it in
    # vaguer words — which defeats §17's purpose rather than serving it.
    #
    # The carve-out is deliberately narrow: the instrument sense is admitted only when the
    # word is qualified as an instrument (financial / bank / corporate / performance) or is
    # doing what an instrument does (given, issued, invoked, or named in an agreement). Every
    # other use — "this guarantees", "guarantees that the balance is recoverable" — still
    # fails, which is the use §17.2 is actually about.
    (r"\b(?<!financial )(?<!bank )(?<!corporate )(?<!performance )guarantees?\b"
     r"(?!\s+(?:given|issued|invoked|agreement|agreements|for\b|on behalf of))",
     "a guarantee is not a lead"),
    (r"\bwill fail\b", "prediction of failure is prohibited"),
    (r"\bis fraudulent\b", "no conclusion of fraud"),
    (r"\bis insolvent\b", "no conclusion of insolvency"),
    (r"\b(?:is|are) financially distressed\b", "no conclusion of distress"),
    (r"\bmanipulat(?:ed|ion|ing)\b", "attributes intent"),
    (r"\bdiverted\b", "attributes intent; use 'possible-diversion-risk indicator' (§17.1)"),
    (r"\bdeliberately\b", "attributes intent"),
    (r"\bwindow[- ]dress(?:ed|ing)\b", "attributes intent"),
    (r"\bconcealed\b", "attributes intent"),
    (r"\bfraud(?:ulently)?\s+(?:committed|perpetrated)\b", "concludes fraud"),
)

# §17.2 — "the model never states a fabricated probability or percentage of risk."
PROBABILITY = (
    (r"\b\d{1,3}(?:\.\d+)?%\s+(?:probability|likelihood|chance|risk)\b",
     "fabricated probability of risk"),
    (r"\bprobability of (?:default|failure|distress)\b",
     "fabricated probability"),
)


@dataclass(frozen=True)
class Violation:
    line: int
    text: str
    pattern: str
    why: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.why} — {self.text.strip()[:90]!r}"


# A prohibited phrase does not stop being prohibited because the renderer wrapped it, and a
# PERMITTED one does not become prohibited for the same reason. Matching line by line does
# both: "guarantees given" straddling a wrap reads as a bare "guarantees" on the first line
# and trips a rule whose carve-out the full phrase satisfies. So each line is matched with
# the following one appended, giving every pattern the context the sentence actually has,
# while the violation is still reported against the line it starts on.
_WRAP_LOOKAHEAD_LINES = 1


def lint(text: str) -> list[Violation]:
    out: list[Violation] = []
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        window = " ".join(lines[n - 1: n + _WRAP_LOOKAHEAD_LINES])
        for pat, why in PROHIBITED + PROBABILITY:
            # The phrase must BEGIN on this line, or it would be reported twice — once here
            # and once on the line it wraps onto.
            m = re.search(pat, window, flags=re.I)
            if m and m.start() < len(line):
                out.append(Violation(line=n, text=line, pattern=pat, why=why))
    return out


def assert_safe(text: str) -> None:
    """Raise if the text would violate §17.2. Called before any FDR is released."""
    from . import tracing as T
    with T.span("safe-language gate", kind="GUARDRAIL",
                input={"chars": len(text)}) as sp:
        bad = lint(text)
        sp.set("fdr.lint.violations", len(bad))
        sp.set("fdr.lint.passed", not bad)
        for v in bad:
            sp.event("violation", line=v.line, why=v.why, text=v.text.strip()[:120])
        sp.set_output("PASS" if not bad else f"FAIL ({len(bad)})")
    if bad:
        raise ValueError(
            "safe-language lint failed (§17.2) — report not released:\n"
            + "\n".join(f"  {v}" for v in bad)
        )
