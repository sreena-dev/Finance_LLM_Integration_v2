"""Step 0 — column-role resolution and dual-mode ingestion for the TB
validation module.

Two independent ingestion paths feed one shared internal shape
(``ParsedTBTable``/``TBRow``):

- ``ingest_from_bytes(data)`` — PRIMARY, full-fidelity path for a freshly
  uploaded raw file. Resolves all 9 semantic roles (ledger_code, ledger_name,
  group, company_code, currency, opening_balance, closing_balance, debit,
  credit) generically via keyword/header-role matching — adapted from the
  same fuzzy-matching PATTERN already built for the grouping-file parser
  (``yukta_rag.audit.audit_grouping``: ``_find_col``/keyword-tuple scanning
  over a window of candidate header rows), generalized from 3 roles to 9.
  Applies the exact 6-step ``parse_amount`` to every raw cell, so malformed
  source values (the actual thing TB-004 exists to catch) are never lost to
  a downstream parser's silent coercion.
- ``ingest_from_doc_id(doc_id)`` — CONVENIENCE path reusing the existing,
  UNMODIFIED ``yukta_rag.trial_balance.trial_balance.get_trial_balance()``.
  Only the already-parsed (float) data is available this way, so
  ``company_code`` and raw formula text are marked unavailable rather than
  guessed — this is what makes testing against already-stored real TBs
  possible without needing a fresh re-upload, at a disclosed fidelity cost.

Nothing in this file imports from ``yukta_rag.audit`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from yukta_rag.tb_validation.tbv_parsing import is_total_row, parse_amount
from yukta_rag.trial_balance.trial_balance import _read_workbook, get_trial_balance

_HEADER_SCAN_ROWS = 30

# one keyword tuple per role — substring/keyword-containment matching, same
# primitive as audit_grouping.py's _find_col, generalized to 9 roles.
_ROLE_HEADER_KWS: dict[str, tuple[str, ...]] = {
    "ledger_code": ("account code", "acc code", "gl code", "g/l acct", "g/l code",
                    "ledger code", "a/c code", "account no", "acc no", "a/c no", "code"),
    "ledger_name": ("account name", "gl name", "ledger name", "description",
                    "particulars", "narration", "name", "ledger"),
    "group": ("fsli", "group", "classification", "category", "schedule iii head",
              "schedule", "head", "nature", "class", "type"),
    "company_code": ("company code", "co code", "co cd", "cocd", "company"),
    "currency": ("currency", "crcy", "curr."),
    "opening_balance": ("opening balance", "opening bal", "op. balance", "op bal",
                        "brought forward", "b/f", "opening"),
    "closing_balance": ("closing balance", "closing bal", "cl. balance", "cl bal",
                        "carried forward", "c/f", "closing", "balance"),
    "debit": ("debit", "dr"),
    "credit": ("credit", "cr"),
}
# roles matched in this priority order so e.g. "Opening Balance" doesn't get
# claimed by the generic "balance" fallback inside "closing_balance" first.
_ROLE_MATCH_ORDER = ("ledger_code", "ledger_name", "company_code", "currency",
                     "opening_balance", "closing_balance", "debit", "credit", "group")

REQUIRED_ROLES = ("ledger_code", "closing_balance")
ALL_ROLES = tuple(_ROLE_HEADER_KWS.keys())


@dataclass
class TBRow:
    row_number: int  # 1-based position within the sheet, for traceability
    ledger_code: str | None
    ledger_name: str | None
    group: str | None = None
    company_code: str | None = None
    currency: str | None = None
    opening_balance: float | None = None   # signed, debit-positive
    closing_balance: float | None = None   # signed, debit-positive
    debit: float | None = None             # unsigned magnitude
    credit: float | None = None            # unsigned magnitude
    opening_unparseable: bool = False
    closing_unparseable: bool = False
    debit_unparseable: bool = False
    credit_unparseable: bool = False
    formula_cells: dict[str, str] = field(default_factory=dict)  # role -> formula text


@dataclass
class ParsedTBTable:
    label: str
    filename: str | None
    doc_id: str | None
    period_label: str | None
    rows: list[TBRow]
    excluded_total_rows: int
    roles_resolved: dict[str, bool]
    has_raw_access: bool   # True only for ingest_from_bytes (raw-string/company/formula fidelity)
    source_mode: str        # "raw_bytes" | "doc_id"
    sheet_name: str | None = None    # for Layer 4's raw re-read, raw_bytes mode only
    header_row: int | None = None    # 0-based row index of the resolved header, raw_bytes mode only
    role_cols: dict[str, int] = field(default_factory=dict)  # role -> column index, raw_bytes mode only
    has_genuine_movement: bool = True  # False when debit/credit are a derived sign-split of a
    # single closing-balance column, not real period movement (doc_id mode only — confirmed
    # live on GAIL FY23-24: parse_report.has_movement=False, yet p1_debit/p1_credit are always
    # populated as a synthetic split of p1_net, which would make TB-005 fail on every single
    # row if trusted as real movement). Always True for ingest_from_bytes, where debit/credit
    # only resolve at all when a real source column was found for them.


def _norm_headers(row: list) -> list[str]:
    return [str(v).strip() if pd.notna(v) else "" for v in row]


def _find_col(headers: list[str], kws: tuple[str, ...], exclude: set[int]) -> int | None:
    for i, h in enumerate(headers):
        if i in exclude:
            continue
        low = (h or "").strip().lower()
        if any(k in low for k in kws):
            return i
    return None


def _resolve_roles_for_row(headers: list[str]) -> dict[str, int]:
    """Resolve as many of the 9 roles as possible from one header row,
    highest-priority role first so a column is never double-claimed."""
    claimed: set[int] = set()
    resolved: dict[str, int] = {}
    for role in _ROLE_MATCH_ORDER:
        col = _find_col(headers, _ROLE_HEADER_KWS[role], claimed)
        if col is not None:
            resolved[role] = col
            claimed.add(col)
    return resolved


def _find_best_header_row(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Scan candidate rows, score by how many roles resolve (required roles
    weighted higher), return the best (row_index, {role: col_index})."""
    best_row, best_cols, best_score = -1, {}, -1
    limit = min(_HEADER_SCAN_ROWS, len(df))
    for i in range(limit):
        headers = _norm_headers(df.iloc[i].tolist())
        cols = _resolve_roles_for_row(headers)
        score = len(cols) + sum(5 for r in REQUIRED_ROLES if r in cols)
        if score > best_score:
            best_row, best_cols, best_score = i, cols, score
    return best_row, best_cols


def _cell(row: list, col: int | None) -> object:
    if col is None or col >= len(row):
        return None
    v = row[col]
    return v if pd.notna(v) else None


def ingest_from_bytes(data: bytes, label: str | None = None) -> ParsedTBTable:
    """Primary, full-fidelity ingestion path — resolves all 9 roles directly
    from the raw workbook and applies the exact 6-step number parser to
    every numeric cell."""
    sheets = _read_workbook(data)
    best_table: ParsedTBTable | None = None
    for _sheet_name, df in sheets.items():
        if df.empty or len(df) < 2:
            continue
        header_row, cols = _find_best_header_row(df)
        roles_resolved = {r: (r in cols) for r in ALL_ROLES}
        if not all(roles_resolved[r] for r in REQUIRED_ROLES):
            # keep scanning other sheets for one that resolves the required roles;
            # remember the best attempt so far in case none do.
            if best_table is None:
                best_table = ParsedTBTable(
                    label=label or "table", filename=label, doc_id=None, period_label=None,
                    rows=[], excluded_total_rows=0, roles_resolved=roles_resolved,
                    has_raw_access=True, source_mode="raw_bytes")
            continue

        rows: list[TBRow] = []
        excluded = 0
        for i in range(header_row + 1, len(df)):
            raw_row = df.iloc[i].tolist()
            code = _cell(raw_row, cols.get("ledger_code"))
            name = _cell(raw_row, cols.get("ledger_name"))
            if code is None and name is None:
                continue
            if is_total_row(code, name):
                excluded += 1
                continue
            opening_val, opening_bad = ((None, False) if "opening_balance" not in cols
                                        else parse_amount(_cell(raw_row, cols["opening_balance"])))
            closing_val, closing_bad = ((None, False) if "closing_balance" not in cols
                                        else parse_amount(_cell(raw_row, cols["closing_balance"])))
            debit_val, debit_bad = ((None, False) if "debit" not in cols
                                    else parse_amount(_cell(raw_row, cols["debit"])))
            credit_val, credit_bad = ((None, False) if "credit" not in cols
                                      else parse_amount(_cell(raw_row, cols["credit"])))
            rows.append(TBRow(
                row_number=i + 1,
                ledger_code=str(code).strip() if code is not None else None,
                ledger_name=str(name).strip() if name is not None else None,
                group=(str(_cell(raw_row, cols.get("group"))).strip()
                      if _cell(raw_row, cols.get("group")) is not None else None),
                company_code=(str(_cell(raw_row, cols.get("company_code"))).strip()
                             if _cell(raw_row, cols.get("company_code")) is not None else None),
                currency=(str(_cell(raw_row, cols.get("currency"))).strip()
                         if _cell(raw_row, cols.get("currency")) is not None else None),
                opening_balance=opening_val, closing_balance=closing_val,
                debit=debit_val, credit=credit_val,
                opening_unparseable=opening_bad, closing_unparseable=closing_bad,
                debit_unparseable=debit_bad, credit_unparseable=credit_bad,
            ))
        table = ParsedTBTable(
            label=label or _sheet_name, filename=label, doc_id=None, period_label=None,
            rows=rows, excluded_total_rows=excluded, roles_resolved=roles_resolved,
            has_raw_access=True, source_mode="raw_bytes",
            sheet_name=_sheet_name, header_row=header_row, role_cols=dict(cols))
        return table

    if best_table is not None:
        return best_table
    return ParsedTBTable(
        label=label or "table", filename=label, doc_id=None, period_label=None,
        rows=[], excluded_total_rows=0, roles_resolved={r: False for r in ALL_ROLES},
        has_raw_access=True, source_mode="raw_bytes")


def ingest_from_doc_id(doc_id: str) -> ParsedTBTable:
    """Convenience ingestion path — reuses the existing, UNMODIFIED
    get_trial_balance() and adapts its already-parsed fields into the same
    shape. company_code and raw formula text are unavailable this way and
    are marked as such, never fabricated."""
    tb = get_trial_balance(doc_id)
    if tb is None:
        return ParsedTBTable(
            label=doc_id, filename=None, doc_id=doc_id, period_label=None,
            rows=[], excluded_total_rows=0, roles_resolved={r: False for r in ALL_ROLES},
            has_raw_access=False, source_mode="doc_id")

    accounts = tb.get("accounts", [])
    has_opening = any("opening_net" in a for a in accounts)
    has_group = any(a.get("type") for a in accounts)
    has_currency = any(a.get("currency") for a in accounts)
    # trial_balance.py always populates p1_debit/p1_credit, even for a file whose only
    # balance source was a single combined closing-balance column — in that case they're
    # a sign-split of the closing balance itself, not real period movement (confirmed live:
    # parse_report.has_movement=False yet p1_debit/p1_credit are always present). Only trust
    # them as genuine movement when the original parse actually found movement columns.
    has_genuine_movement = bool(tb.get("parse_report", {}).get("has_movement"))
    roles_resolved = {
        "ledger_code": True, "ledger_name": True,
        "group": has_group, "company_code": False, "currency": has_currency,
        "opening_balance": has_opening, "closing_balance": True,
        "debit": True, "credit": True,
    }

    rows: list[TBRow] = []
    excluded = 0
    for i, a in enumerate(accounts):
        code, name = a.get("code"), a.get("name")
        if is_total_row(code, name):
            excluded += 1
            continue
        rows.append(TBRow(
            row_number=a.get("source_row", i + 1),
            ledger_code=str(code).strip() if code is not None else None,
            ledger_name=str(name).strip() if name is not None else None,
            group=a.get("type"),
            company_code=None,
            currency=a.get("currency"),
            opening_balance=a.get("opening_net"),
            closing_balance=a.get("p1_net"),
            # Prefer genuine period movement (movement_debit/credit) over the
            # sign-split of the closing balance (p1_debit/credit) when the parser
            # captured real debit/credit columns.
            debit=a.get("movement_debit", a.get("p1_debit")),
            credit=a.get("movement_credit", a.get("p1_credit")),
        ))

    periods = tb.get("periods") or []
    return ParsedTBTable(
        label=tb.get("filename") or doc_id, filename=tb.get("filename"), doc_id=doc_id,
        period_label=(periods[0] if periods else None),
        rows=rows, excluded_total_rows=excluded, roles_resolved=roles_resolved,
        has_raw_access=False, source_mode="doc_id", has_genuine_movement=has_genuine_movement)
