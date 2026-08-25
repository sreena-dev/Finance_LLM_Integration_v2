"""
Typed contracts for the flow. Dependency-light dataclasses, JSON-serialisable,
so they flow from parser → checks → report unchanged.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

# ---- Finding taxonomy ------------------------------------------------------
# tag:      FINDING (established defect) | RISK_FLAG (signal) | COVERAGE (checked ok /
#           limitation) | INFO
# severity: HIGH | MEDIUM | LOW | NONE
# status:   FAIL | PASS | ABSTAIN  (ABSTAIN = could not check reliably → human)


@dataclass
class Row:
    """One parsed table_md row."""
    idx: int
    label: str
    note: str | None
    role: str                      # header | line | sum | total
    values: dict[str, float | None]  # period -> value
    raw: list[str] = field(default_factory=list)

    def has_values(self) -> bool:
        return any(v is not None for v in self.values.values())


@dataclass
class ParsedTable:
    table_id: str | None
    statement: str | None
    periods: list[str]
    rows: list[Row]
    label_col: int | None = None
    note_col: int | None = None
    serial_col: int | None = None
    value_cols: list[int] = field(default_factory=list)
    unit: str | None = None
    page: int | None = None
    warnings: list[str] = field(default_factory=list)

    def find(self, *patterns: str):
        """First row whose (normalised) label matches any regex, most-specific first."""
        import re
        for pat in patterns:
            rx = re.compile(pat, re.I)
            for r in self.rows:
                if r.label and rx.search(r.label):
                    return r
        return None

    def sums(self) -> list[Row]:
        return [r for r in self.rows if r.role in ("sum", "total")]

    def lines(self) -> list[Row]:
        return [r for r in self.rows if r.role == "line"]


@dataclass
class CanonicalFact:
    """Atomic, computable fact — the unit the arithmetic core runs on (req 3)."""
    doc_id: str
    table_id: str | None
    statement: str | None      # balance_sheet | profit_loss | ... | note
    line_item: str
    note: str | None
    period: str
    value: float
    role: str
    unit: str | None = None
    page: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    tag: str                    # FINDING | RISK_FLAG | COVERAGE | INFO
    check: str                  # e.g. "bs_equation"
    area: str
    observation: str
    status: str = "FAIL"        # FAIL | PASS | ABSTAIN
    severity: str = "MEDIUM"
    trace: str | None = None    # reproducible calc: figures + formula
    gap: float | None = None    # signed difference expected-vs-stated
    evidence: list[str] = field(default_factory=list)  # where to look (req 8)
    source: dict = field(default_factory=dict)         # doc/table/note/page lineage
    standard_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocQuality:
    doc_id: str
    ocr_pages: int | None = None
    pdf_pages: int | None = None
    total_tables: int | None = None
    total_chunks: int | None = None
    verdict: str = "OK"          # OK | WARN | FAIL
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StatementRecall:
    statement: str
    present: bool
    table_ids: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    line_count: int = 0
    unit: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FlowResult:
    doc_id: str
    company: str | None = None
    period: str | None = None
    quality: dict | None = None
    coverage: list[dict] = field(default_factory=list)
    recall: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    facts_count: int = 0
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
