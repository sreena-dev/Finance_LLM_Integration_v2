"""Trial-balance Excel parsing, normalization and storage.

A trial balance (TB) lists every general-ledger account with its debit/credit
balance. Unlike the PDF corpus this is STRUCTURED NUMERIC data, so it is parsed
into a canonical JSON shape and stored as-is (no embeddings) in the local
``finance_uploads`` database — reusing the connection/schema helpers in
``yukta_rag.uploads.uploads``. All arithmetic later happens in ``tb_tools`` (Python),
never in the LLM.

Layouts vary a lot, so parsing is heuristic and self-describing: every parse
returns a ``parse_report`` stating exactly which columns/periods were detected
so a mis-parse is visible to the caller rather than silent.
"""

from __future__ import annotations

import hashlib
import io
import json
import re

import pandas as pd

from yukta_rag.core.db import get_connection
from yukta_rag.core.pii import mask_pii
from yukta_rag.fdr.fdr_docprops import extract_xlsx_properties

# ---------------------------------------------------------------------------
# Column detection
#
# Real trial balances range from a plain [Account, Debit, Credit] sheet to SAP
# exports with a two-row header (group labels "Opening/Transaction/Closing
# Balance" over "Debit/Credit" sub-labels) and identifier columns whose headers
# are blank. Detection therefore: (1) finds the header row, merging a Debit/Credit
# sub-row into it; (2) maps each column to an identifier role (name/code/type/
# company) or a money role; (3) infers the name/code column from the data when
# the header is blank; (4) picks which money columns are the account BALANCE,
# preferring a closing balance over period movements.
# ---------------------------------------------------------------------------

# identifier roles. Checked in this order so the most specific wins: a
# "Company Code" is company (not code), an "Account Type" is type (not name).
_COMPANY_KWS = ("company code", "co code", "co cd", "cocd", "company")
_CURRENCY_KWS = ("currency", "crcy", "curr.", "curr ")
_TYPE_KWS = ("account type", "acct type", "gl type", "type", "group", "category",
             "class", "nature", "classification")
_CODE_KWS = ("account code", "acc code", "gl code", "g/l acct", "g/l code", "g/l",
             "gl acct", "ledger code", "a/c code", "account no", "acc no", "a/c no",
             "acct", "a/c", "code")
_NAME_KWS = ("particular", "account name", "gl description", "description",
             "short text", "narration", "account", "ledger", "head of account",
             "name", "head", "text")

_DEBIT_RE = re.compile(r"\bdebit\b|\bdr\b")
_CREDIT_RE = re.compile(r"\bcredit\b|\bcr\b")
_OPENING_RE = re.compile(r"opening|carry\s*forward|carryforward|brought\s*forward|b/f|op\.?\s*bal")
_CLOSING_RE = re.compile(r"closing|accumulated|cumulative|carried\s*forward|c/f|cl\.?\s*bal")
_TXN_RE = re.compile(r"transaction|movement|during|rept|report|period|turnover")
_BALANCE_RE = re.compile(r"balance|amount")

_YEAR_RE = re.compile(r"(?:fy\s*)?(19|20)\d{2}(?:\s*[-/]\s*\d{2,4})?", re.I)
_PRIOR_RE = re.compile(r"\b(prior|previous|last\s*year|py|comparative)\b", re.I)
_CURRENT_RE = re.compile(r"\b(current|this\s*year|cy)\b", re.I)

_TOTAL_PREFIXES = ("total", "grand total", "sub total", "subtotal", "net total")


def _norm(v) -> str:
    """Normalize a header cell to lowercase, single-spaced text ('' for NaN)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def _id_role(cell: str) -> str | None:
    """Map a header cell to an identifier role (company/type/code/name), else None."""
    if not cell:
        return None
    if any(k in cell for k in _COMPANY_KWS):
        return "company"
    if cell in ("crcy", "currency", "curr") or any(k in cell for k in _CURRENCY_KWS):
        return "currency"
    if any(k in cell for k in _TYPE_KWS):
        return "type"
    if any(k in cell for k in _CODE_KWS):
        return "code"
    if any(k in cell for k in _NAME_KWS):
        return "name"
    return None


def _money_role(cell: str) -> str | None:
    """Map a (possibly two-row-merged) header cell to a money role, else None.

    Returns one of: closing_net / closing_debit / closing_credit /
    opening_net / opening_debit / opening_credit / txn_debit / txn_credit /
    debit / credit / balance.
    """
    if not cell:
        return None
    side = "debit" if _DEBIT_RE.search(cell) else "credit" if _CREDIT_RE.search(cell) else None
    if _OPENING_RE.search(cell):
        return f"opening_{side}" if side else "opening_net"
    if _CLOSING_RE.search(cell):
        return f"closing_{side}" if side else "closing_net"
    if _TXN_RE.search(cell) and _BALANCE_RE.search(cell) is None:
        return f"txn_{side}" if side else None
    if _BALANCE_RE.search(cell):
        return side or "balance"        # "Debit Balance" -> debit; bare "Balance" -> net
    return side                          # bare "Debit"/"Credit" columns


# scale hints often live in a title/notes cell, e.g. "(Amt. in Rs.)" / "Rs. in lakh".
_SCALE_PATTERNS = [
    ("crore", re.compile(r"\bin\s+crore|\bcr\.?\b|crores?", re.I)),
    ("lakh", re.compile(r"\bin\s+lakh|lakhs?|lacs?", re.I)),
    ("million", re.compile(r"\bin\s+million|millions?|\bmn\b", re.I)),
    ("thousand", re.compile(r"\bin\s+thousand|thousands?|\b000s?\b|\bin\s+'?000", re.I)),
    ("units", re.compile(r"\bin\s+rs\.?\b|\bin\s+rupees|\bamt\.?\s+in\s+rs", re.I)),
]


def _detect_scale(df: pd.DataFrame, header_idx: int) -> str:
    """Best-effort scale from title/notes rows around the header; 'unknown' if unseen."""
    texts = []
    for i in range(0, min(header_idx + 2, len(df))):
        texts.append(" ".join(_norm(v) for v in df.iloc[i].tolist() if _norm(v)))
    blob = " ".join(texts)
    for name, pat in _SCALE_PATTERNS:
        if pat.search(blob):
            return name
    return "unknown"


def _ffill(cells: list[str]) -> list[str]:
    """Forward-fill blank cells with the previous non-blank (spans merged headers)."""
    out, last = [], ""
    for c in cells:
        if c:
            last = c
        out.append(last)
    return out


def _score_row(cells: list[str]) -> tuple[int, int]:
    """Return (id_role_count, money_role_count) for a candidate header row."""
    ids = {_id_role(c) for c in cells}
    ids.discard(None)
    money = sum(1 for c in cells if _money_role(c))
    return len(ids), money


def _find_header(df: pd.DataFrame) -> tuple[int, bool] | None:
    """Find the header row within the first 80 rows.

    Returns ``(row_index, two_row)`` where ``two_row`` means the following row
    holds Debit/Credit sub-labels that belong to this header. None if no row
    looks like a TB header (needs >= 2 money columns).
    """
    best, best_key = None, (-1, -1)
    limit = min(80, len(df))
    for i in range(limit):
        cells = [_norm(v) for v in df.iloc[i].tolist()]
        n_ids, n_money = _score_row(cells)
        # a sub-row of only Debit/Credit shouldn't outrank the group row above it
        key = (n_money + n_ids * 2, n_ids)
        if n_money >= 2 and key > best_key:
            best, best_key = i, key
    if best is None:
        return None
    # two-row header: the next row is mostly debit/credit sub-labels
    two_row = False
    if best + 1 < len(df):
        nxt = [_norm(v) for v in df.iloc[best + 1].tolist()]
        dc = sum(1 for c in nxt if _DEBIT_RE.search(c) or _CREDIT_RE.search(c))
        if dc >= 2:
            two_row = True
    return best, two_row


# ---------------------------------------------------------------------------
# Numeric / code parsing
# ---------------------------------------------------------------------------

_NUM_CLEAN_RE = re.compile(r"[,\s₹$]")


def _to_num(v) -> float:
    """Parse a cell to float. Handles commas, currency marks, and (parens)=negative."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"-", "--"}:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = _NUM_CLEAN_RE.sub("", s.strip("()"))
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return -n if neg else n


def _clean_code(v) -> str | None:
    """Normalize a code cell to a string, collapsing floats like 11010010.0 -> '11010010'."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


def _looks_text(vals: list) -> bool:
    """True if most non-empty values are non-numeric (a name-like column)."""
    seen = txt = 0
    for v in vals:
        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
            continue
        seen += 1
        s = str(v).strip()
        if _to_num(s) == 0.0 and not re.fullmatch(r"-?0*\.?0*", s):
            # not a number (and not a literal zero) -> treat as text
            if re.search(r"[a-zA-Z]", s):
                txt += 1
    return seen > 0 and txt / seen >= 0.6


# ---------------------------------------------------------------------------
# Period labelling
# ---------------------------------------------------------------------------


def _period_labels(header: list[str], above: list[str], anchor_cols: list[int]) -> list[str]:
    """Human labels per period from year tokens / current-vs-prior words."""
    labels = []
    for col in anchor_cols:
        text = " ".join(c for c in (
            above[col] if col < len(above) else "",
            header[col] if col < len(header) else "",
        ) if c)
        m = _YEAR_RE.search(text)
        if m:
            labels.append(re.sub(r"\s+", " ", m.group(0).upper().replace("FY", "FY ")).strip())
        elif _CURRENT_RE.search(text):
            labels.append("Current")
        elif _PRIOR_RE.search(text):
            labels.append("Prior")
        else:
            labels.append("")
    if len(labels) == 1:
        return [labels[0] or "Balance"]
    defaults = ["Current", "Prior"]
    return [labels[i] or (defaults[i] if i < len(defaults) else f"Period {i + 1}")
           for i in range(len(labels))]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


MAX_PERIODS = 5  # bounded cap so a mis-detected column can't blow up parsing/memory


def _resolve_periods(money: dict[int, str]) -> tuple[str, list[dict]] | None:
    """Choose which money columns are the account balance and how to read them.

    ``money`` maps column index -> money role. Returns ``(mode, specs)`` where
    each spec computes (debit, credit, net) for a row, or None if no balance
    columns are present. Precedence: a *closing* balance beats a plain balance
    beats period debit/credit beats opening — period *movements* are ignored
    whenever a balance column exists.

    Requirement 2 (multi-year-in-one-file): a file with 3+ genuinely repeated
    column-blocks (e.g. a closing-balance column per year) is already
    detected generically by ``cols(role)`` collecting every matching column —
    the only thing that used to silently drop the 3rd+ period was the
    ``[:2]`` slice here, now raised to ``MAX_PERIODS``.
    """
    def cols(role):
        return sorted(c for c, r in money.items() if r == role)

    closing_net = cols("closing_net")
    closing_d, closing_c = cols("closing_debit"), cols("closing_credit")
    balance = cols("balance")
    debit, credit = cols("debit"), cols("credit")
    opening_net = cols("opening_net")
    opening_d, opening_c = cols("opening_debit"), cols("opening_credit")

    # 1. signed closing balance column(s) -> net (one per detected period)
    if closing_net:
        c = closing_net[:MAX_PERIODS]
        return "net", [{"kind": "net", "col": col} for col in c]
    # 2. closing balance split into debit & credit columns -> net = dr - cr
    if closing_d and closing_c:
        return "closing_dc", [{"kind": "dc", "debit": closing_d[0], "credit": closing_c[0]}]
    # 3. generic signed balance column(s)
    if balance:
        return "net", [{"kind": "net", "col": col} for col in balance[:MAX_PERIODS]]
    # 4. plain debit & credit pair(s) (a simple ledger trial balance)
    if debit and credit:
        n = min(len(debit), len(credit), MAX_PERIODS)
        return "debit_credit", [{"kind": "dc", "debit": debit[i], "credit": credit[i]}
                                for i in range(n)]
    # 5. last resort: opening balance
    if opening_net:
        return "net", [{"kind": "net", "col": opening_net[0]}]
    if opening_d and opening_c:
        return "opening_dc", [{"kind": "dc", "debit": opening_d[0], "credit": opening_c[0]}]
    return None


def _spec_values(spec: dict, row: list) -> tuple[float, float]:
    """Compute (debit, credit) for one account row under a period spec."""
    if spec["kind"] == "net":
        col = spec["col"]
        net = _to_num(row[col]) if col < len(row) else 0.0
        return (net, 0.0) if net >= 0 else (0.0, -net)
    d = _to_num(row[spec["debit"]]) if spec["debit"] < len(row) else 0.0
    c = _to_num(row[spec["credit"]]) if spec["credit"] < len(row) else 0.0
    return d, c


def _parse_sheet(df: pd.DataFrame) -> dict | None:
    """Parse one sheet into the canonical TB structure, or None if not a TB."""
    found = _find_header(df)
    if found is None:
        return None
    header_idx, two_row = found

    header = [_norm(v) for v in df.iloc[header_idx].tolist()]
    above = [_norm(v) for v in df.iloc[header_idx - 1].tolist()] if header_idx > 0 else []
    data_start = header_idx + (2 if two_row else 1)

    # For a two-row header, merge the forward-filled group row with the sub-row so
    # each column reads e.g. "closing balance debit".
    if two_row:
        sub = [_norm(v) for v in df.iloc[header_idx + 1].tolist()]
        top = _ffill(header)
        width = max(len(header), len(sub))
        merged = []
        for i in range(width):
            raw_top = header[i] if i < len(header) else ""   # pre-ffill (real label here)
            sub_i = sub[i] if i < len(sub) else ""
            # A group label is only inherited into a column that has its own
            # sub-label; a spillover column with neither stays blank (avoids a
            # phantom "closing balance" over a trailing empty column).
            if not raw_top and not sub_i:
                merged.append("")
                continue
            top_i = top[i] if i < len(top) else ""
            merged.append(" ".join(x for x in (top_i, sub_i) if x).strip())
    else:
        merged = header

    # identifier roles (skip a column already claimed by a money role)
    money: dict[int, str] = {}
    id_roles: dict[int, str] = {}
    for col, cell in enumerate(merged):
        mr = _money_role(cell)
        if mr:
            money[col] = mr
        else:
            ir = _id_role(cell)
            if ir:
                id_roles[col] = ir

    name_col = next((c for c, r in id_roles.items() if r == "name"), None)
    code_col = next((c for c, r in id_roles.items() if r == "code"), None)
    type_col = next((c for c, r in id_roles.items() if r == "type"), None)
    currency_col = next((c for c, r in id_roles.items() if r == "currency"), None)

    resolved = _resolve_periods(money)
    if resolved is None:
        return None
    mode, specs = resolved

    money_used = {s.get("col") for s in specs} | {s.get("debit") for s in specs} | {s.get("credit") for s in specs}
    money_used.discard(None)

    # Opening + movement columns (kept for the opening + movement = closing check,
    # §3/§6 of the audit spec). Only meaningful for a single-period export.
    def _mcols(role):
        return sorted(c for c, r in money.items() if r == role)
    op_net, op_d, op_c = _mcols("opening_net"), _mcols("opening_debit"), _mcols("opening_credit")
    txn_d, txn_c = _mcols("txn_debit"), _mcols("txn_credit")
    # When a single closing/accumulated balance was chosen as the net source but the
    # sheet ALSO carries bare debit/credit columns (e.g. SAP "Sum of Debit 1- 12" /
    # "Sum of Credit 1- 12" full-year totals), those are the period MOVEMENT — not a
    # sign-split of the balance. Without this they were silently ignored, leaving
    # movement/opening unavailable. Only fires when a net column exists alongside
    # unused bare debit+credit, so plain debit/credit-only TBs (no net column) are
    # unaffected. Does not touch p1_net/p1_debit/p1_credit.
    if mode == "net" and not (txn_d and txn_c):
        bare_d = [c for c in _mcols("debit") if c not in money_used]
        bare_c = [c for c in _mcols("credit") if c not in money_used]
        if bare_d and bare_c:
            txn_d, txn_c = bare_d, bare_c
            money_used |= {txn_d[0], txn_c[0]}  # now used for movement, not "ignored"
    has_opening = bool(op_net or (op_d and op_c))
    has_movement = bool(txn_d and txn_c)

    def _opening(row):
        """Returns (net, debit, credit) — the debit/credit split is kept
        (not just their difference) so opening-vs-closing variance can be
        shown as absolute figures per FSLI group."""
        if op_net:
            v = _to_num(row[op_net[0]]) if op_net[0] < len(row) else 0.0
            return v, None, None
        if op_d and op_c:
            d = _to_num(row[op_d[0]]) if op_d[0] < len(row) else 0.0
            c = _to_num(row[op_c[0]]) if op_c[0] < len(row) else 0.0
            return d - c, d, c
        return None, None, None

    def _movement(row):
        """Returns (net, debit, credit) — the gross debit/credit split is kept
        (not just their difference) so whole-file-movement checks (spec E3/E6
        gross offsetting-activity) can compare debit-side vs credit-side
        activity, not just the net change."""
        if txn_d and txn_c:
            d = _to_num(row[txn_d[0]]) if txn_d[0] < len(row) else 0.0
            c = _to_num(row[txn_c[0]]) if txn_c[0] < len(row) else 0.0
            return d - c, d, c
        return None, None, None

    # Infer name/code from the data when the header didn't label them (blank
    # headers): the leftmost text column is the name; a remaining code-ish column
    # is the code. Only consider columns not used for money.
    sample = [df.iloc[r].tolist() for r in range(data_start, min(data_start + 150, len(df)))]
    non_money = [c for c in range(len(merged))
                 if c not in money and c not in money_used]
    if name_col is None:
        for c in non_money:
            if _looks_text([r[c] for r in sample if c < len(r)]):
                name_col = c
                break
    if code_col is None:
        for c in non_money:
            if c == name_col:
                continue
            vals = [r[c] for r in sample if c < len(r)]
            nonempty = [str(v).strip() for v in vals
                        if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip()]
            if nonempty and sum(1 for s in nonempty if " " not in s) / len(nonempty) >= 0.8:
                code_col = c
                break
    if name_col is None and code_col is None:
        return None
    label_col = name_col if name_col is not None else code_col

    anchor = [s.get("col", s.get("debit")) for s in specs]
    periods = _period_labels(merged, above, anchor)
    n_periods = len(specs)

    accounts: list[dict] = []
    opening_derived = False
    for i in range(data_start, len(df)):
        row = df.iloc[i].tolist()
        name = (str(row[label_col]).strip() if label_col is not None
                and label_col < len(row) and pd.notna(row[label_col]) else "")
        code = _clean_code(row[code_col]) if code_col is not None and code_col < len(row) else None
        if not name and not code:
            continue  # blank spacer row
        low = (name or code or "").lower()
        if any(low.startswith(t) for t in _TOTAL_PREFIXES):
            continue  # total/footer row

        acct_type = (str(row[type_col]).strip() if type_col is not None
                     and type_col < len(row) and pd.notna(row[type_col]) else None)
        currency = (str(row[currency_col]).strip() if currency_col is not None
                    and currency_col < len(row) and pd.notna(row[currency_col]) else None)

        # ``source_row`` is the 1-based worksheet row -> finding lineage (§16 acceptance).
        rec = {"code": code, "name": name or code, "type": acct_type or None,
               "currency": currency or None, "source_row": int(i) + 1}
        for p, spec in enumerate(specs):
            d, c = _spec_values(spec, row)
            rec[f"p{p + 1}_debit"] = round(d, 2)
            rec[f"p{p + 1}_credit"] = round(c, 2)
            rec[f"p{p + 1}_net"] = round(d - c, 2)
        op, op_d_val, op_c_val = _opening(row)
        mv, mv_d, mv_c = _movement(row)
        if op is not None:
            rec["opening_net"] = round(op, 2)
            if op_d_val is not None:
                rec["opening_debit"] = round(op_d_val, 2)
                rec["opening_credit"] = round(op_c_val, 2)
        if mv is not None:
            rec["movement_net"] = round(mv, 2)
            rec["movement_debit"] = round(mv_d, 2)
            rec["movement_credit"] = round(mv_c, 2)
            # No real opening column, but closing + movement are known ->
            # opening = closing - movement (closing = opening + debit - credit).
            if op is None:
                rec["opening_net"] = round(rec.get("p1_net", 0.0) - mv, 2)
                opening_derived = True
        accounts.append(rec)

    if not accounts:
        return None

    def _hdr(c):
        return merged[c] if c is not None and c < len(merged) else None

    # dominant currency from the currency column (if any)
    currency = "unknown"
    if currency_col is not None:
        vals = [str(a.get("currency")).strip().upper() for a in accounts if a.get("currency")]
        if vals:
            currency = max(set(vals), key=vals.count)

    parse_report = {
        "header_row": int(header_idx),
        "two_row_header": two_row,
        "balance_mode": mode,
        "balance_source": [
            (_hdr(s["col"]) if s["kind"] == "net"
             else f"{_hdr(s['debit'])} / {_hdr(s['credit'])}") for s in specs
        ],
        "columns": {
            "name": _hdr(name_col),
            "code": _hdr(code_col),
            "type": _hdr(type_col),
            "currency": _hdr(currency_col),
        },
        "currency": currency,
        "scale": _detect_scale(df, header_idx),
        "has_opening": has_opening,
        "opening_derived": opening_derived,
        "has_movement": has_movement,
        "n_periods": n_periods,
        "has_type_column": type_col is not None,
        "n_accounts": len(accounts),
        "ignored_money_columns": sorted(
            _hdr(c) for c, r in money.items() if c not in money_used and _hdr(c)
        ),
    }
    return {"periods": periods, "accounts": accounts, "parse_report": parse_report}


def _read_workbook(data: bytes) -> dict[str, pd.DataFrame]:
    """Sheet name -> raw (header-less) DataFrame, for both real Excel files and the
    common ERP-export case of an HTML ``<table>`` saved with an .xls/.xlsx extension
    (SAP, Tally and various PSU-custom exports do this routinely — it's a legacy
    Excel-compatibility convention, not a real Excel file, and pandas can't open it
    as one)."""
    try:
        return pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
    except ValueError as exc:
        if "Excel file format cannot be determined" not in str(exc):
            raise
        try:
            tables = pd.read_html(io.BytesIO(data), header=None)
        except Exception:
            raise exc from None  # the original pandas message is the more useful one
        return {f"Table {i + 1}": t for i, t in enumerate(tables)}


def _diagnose_sheet_failure(df: pd.DataFrame) -> str:
    """Best-effort human-readable reason one sheet didn't parse as a TB, for the
    error message only — mirrors _parse_sheet's own logic read-only, never affects
    actual parsing, so it's safe to be approximate."""
    found = _find_header(df)
    if found is None:
        return ("no row in the first 25 looks like a trial-balance header (need at "
                "least 2 columns recognizable as Debit/Credit/Balance-type)")
    header_idx, two_row = found
    header = [_norm(v) for v in df.iloc[header_idx].tolist()]
    header_preview = ", ".join(h for h in header if h)[:200] or "(blank labels)"
    money = {c: r for c, r in ((c, _money_role(h)) for c, h in enumerate(header)) if r}
    if not money:
        return (f"row {header_idx + 1} looked most header-like ({header_preview}) but "
                "no column resolved to a debit/credit/balance role")
    resolved = _resolve_periods(money)
    if resolved is None:
        return (f"row {header_idx + 1} ({header_preview}) has money-like columns "
                f"({', '.join(sorted(set(money.values())))}) but they don't form a "
                "coherent debit/credit-or-balance period structure")
    return (f"row {header_idx + 1} ({header_preview}) parsed but no column could be "
            "identified as the account name or account code")


def _llm_auto_parse_workbook(data: bytes, xls: dict[str, pd.DataFrame]) -> dict | None:
    """Use the LLM to auto-detect columns and header row when rule-based detection fails."""
    try:
        from yukta_rag.core.llm import build_llm
        client = build_llm(temperature=0.1, max_tokens=1024)
    except Exception:
        return None

    previews = {}
    for sname, df in xls.items():
        if df is None or df.empty:
            continue
        rows = []
        for i in range(min(45, len(df))):
            row = [
                str(v).strip()
                if pd.notna(v) and str(v).strip() not in ("nan", "NaN", "None", "")
                else ""
                for v in df.iloc[i].tolist()[:15]
            ]
            rows.append(row)
        previews[sname] = rows

    if not previews:
        return None

    prompt = f"""You are an expert financial data analyst AI. We have an uploaded Excel Trial Balance where standard rule-based parsing couldn't identify the table structure because of custom header rows, metadata banners, or non-standard column names.
Here is a preview of the first up to 45 rows of each worksheet:
{json.dumps(previews, indent=2)}

Please analyze this workbook to locate the Trial Balance data table and its columns.
Return strictly valid JSON matching this schema:
{{
  "sheet_name": "Sheet1",
  "header_row": 8,
  "account_col": 1,
  "debit_col": 2,
  "credit_col": 3,
  "balance_col": null,
  "code_col": 0
}}
Notes:
- `header_row` must be the 0-based row index of the table header where columns like Account Name / Particulars / Debit / Credit / Balance are located. Ignore any query/metadata header rows above the table.
- `account_col` is the 0-based column index containing the Account Name/Description/Particulars.
- Provide EITHER both `debit_col` and `credit_col` (if separate Debit and Credit columns exist), OR `balance_col` (if only a single net Balance/Amount column exists). The other(s) should be null.
- `code_col` is optional (0-based index of Account Code/No. column, or null).
Return ONLY the JSON object without any markdown code block fences or explanations."""

    try:
        resp = client.generate(messages=[
            {"role": "system", "content": "You are a financial AI assistant that returns strict JSON only."},
            {"role": "user", "content": prompt}
        ])
        content = resp.content or ""
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        mapping = json.loads(m.group(0))
        sheet_name = mapping.get("sheet_name")
        header_row = mapping.get("header_row")
        account_col = mapping.get("account_col")
        if sheet_name not in xls or header_row is None or account_col is None:
            return None
        parsed = parse_trial_balance_with_map(
            data,
            sheet_name=str(sheet_name),
            header_row=int(header_row),
            account_col=int(account_col),
            debit_col=int(mapping["debit_col"]) if mapping.get("debit_col") is not None else None,
            credit_col=int(mapping["credit_col"]) if mapping.get("credit_col") is not None else None,
            balance_col=int(mapping["balance_col"]) if mapping.get("balance_col") is not None else None,
            code_col=int(mapping["code_col"]) if mapping.get("code_col") is not None else None,
        )
        parsed["auto_mapped_by_llm"] = True
        return parsed
    except Exception:
        return None


def parse_trial_balance(data: bytes) -> dict:
    """Parse an Excel trial balance from raw bytes.

    Reads every sheet header-less and picks the first sheet that parses as a TB.
    If rule-based keyword detection fails across all sheets, invokes LLM auto-mapping
    so custom or ERP-exported files are recognized without manual UI intervention.
    Returns ``{sheet, periods, accounts, parse_report}``. Raises ValueError if no
    sheet looks like a trial balance.
    """
    xls = _read_workbook(data)
    tried, reasons = [], []
    for sheet_name, df in xls.items():
        if df is None or df.empty:
            continue
        parsed = _parse_sheet(df)
        if parsed is not None:
            parsed["sheet"] = sheet_name
            return parsed
        tried.append(sheet_name)
        try:
            reasons.append(f"{sheet_name!r}: {_diagnose_sheet_failure(df)}")
        except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
            pass

    # Rule-based detection failed -> try LLM auto-mapping before giving up
    llm_parsed = _llm_auto_parse_workbook(data, xls)
    if llm_parsed is not None:
        return llm_parsed

    detail = " — ".join(reasons) if reasons else f"sheets seen: {tried or list(xls)}"
    raise ValueError(
        "no worksheet looked like a trial balance (need an account column plus "
        f"debit/credit or balance columns). {detail}"
    )


# ---------------------------------------------------------------------------
# Manual column mapping (bypass auto-detection)
# ---------------------------------------------------------------------------


def preview_workbook(data: bytes) -> dict:
    """Return a lightweight preview of every sheet for the column-mapper UI.

    Each sheet entry exposes the first 12 raw rows as string arrays so the
    browser can render a scrollable preview and the user can identify which
    row is the header and which column is which.
    """
    xls = _read_workbook(data)
    sheets = []
    for sheet_name, df in xls.items():
        if df is None or df.empty:
            continue
        n_rows, n_cols = df.shape
        preview_rows = []
        for i in range(min(12, n_rows)):
            row = df.iloc[i].tolist()
            preview_rows.append([
                str(v).strip()
                if pd.notna(v) and str(v).strip() not in ("nan", "NaN", "None", "")
                else ""
                for v in row
            ])
        sheets.append({
            "name": sheet_name,
            "n_rows": int(n_rows),
            "n_cols": int(n_cols),
            "rows": preview_rows,
        })
    if not sheets:
        raise ValueError("no readable sheets found in this file")
    return {"sheets": sheets}


def parse_trial_balance_with_map(
    data: bytes,
    sheet_name: str,
    header_row: int,           # 0-based row index
    account_col: int,
    debit_col: int | None = None,
    credit_col: int | None = None,
    balance_col: int | None = None,
    code_col: int | None = None,
) -> dict:
    """Parse a TB using an explicit user-supplied column mapping.

    Bypasses all keyword-based auto-detection so any column names — including
    non-English, custom ERP labels, or unnamed columns — work without changes.

    Exactly one of (debit_col + credit_col) or balance_col must be supplied.
    For balance_col: positive values are treated as debits, negative as credits.
    Raises ValueError when the mapping produces no usable data rows.
    """
    xls = _read_workbook(data)
    if sheet_name not in xls:
        avail = ", ".join(repr(k) for k in xls)
        raise ValueError(f"sheet {sheet_name!r} not found. Available sheets: {avail}")
    df = xls[sheet_name]
    if df is None or df.empty:
        raise ValueError(f"sheet {sheet_name!r} is empty")

    has_dc = debit_col is not None and credit_col is not None
    has_bal = balance_col is not None
    if not has_dc and not has_bal:
        raise ValueError(
            "supply either both debit + credit columns, or a single net-balance column"
        )

    n_rows = len(df)
    if not (0 <= header_row < n_rows):
        raise ValueError(
            f"header row {header_row + 1} is out of range (sheet has {n_rows} rows)"
        )

    raw_header = df.iloc[header_row].tolist()
    header = [
        str(v).strip() if pd.notna(v) and str(v).strip() not in ("nan", "None") else ""
        for v in raw_header
    ]
    above = (
        [str(v).strip() if pd.notna(v) else "" for v in df.iloc[header_row - 1].tolist()]
        if header_row > 0 else []
    )
    data_start = header_row + 1

    anchor = debit_col if has_dc else balance_col
    periods = _period_labels(
        [_norm(h) for h in header],
        [_norm(h) for h in above],
        [anchor] if anchor is not None else [0],
    )
    if not periods or not any(periods):
        periods = ["Balance"]

    def _hdr(c: int | None) -> str | None:
        return header[c] if c is not None and 0 <= c < len(header) else None

    accounts: list[dict] = []
    for i in range(data_start, n_rows):
        row = df.iloc[i].tolist()

        # Account name
        name = ""
        if account_col < len(row) and pd.notna(row[account_col]):
            s = str(row[account_col]).strip()
            if s not in ("nan", "None", ""):
                name = s

        # Account code (optional)
        code = None
        if code_col is not None and code_col < len(row):
            code = _clean_code(row[code_col])

        if not name and not code:
            continue  # blank spacer row
        low = (name or code or "").lower()
        if any(low.startswith(t) for t in _TOTAL_PREFIXES):
            continue  # total / footer row

        if has_dc:
            d = _to_num(row[debit_col]) if debit_col < len(row) else 0.0
            c = _to_num(row[credit_col]) if credit_col < len(row) else 0.0
        else:
            net = _to_num(row[balance_col]) if balance_col < len(row) else 0.0
            d, c = (net, 0.0) if net >= 0 else (0.0, -net)

        rec = {
            "code": code,
            "name": name or code,
            "type": None,
            "currency": None,
            "source_row": int(i) + 1,
            "p1_debit": round(d, 2),
            "p1_credit": round(c, 2),
            "p1_net": round(d - c, 2),
        }
        accounts.append(rec)

    if not accounts:
        raise ValueError(
            "no account rows found with this mapping — check the header row number "
            "and column selections, and ensure data rows start right after the header"
        )

    parse_report = {
        "header_row": int(header_row),
        "two_row_header": False,
        "balance_mode": "debit_credit" if has_dc else "net",
        "balance_source": [
            f"{_hdr(debit_col)} / {_hdr(credit_col)}" if has_dc
            else (_hdr(balance_col) or "balance")
        ],
        "columns": {
            "name": _hdr(account_col),
            "code": _hdr(code_col),
            "type": None,
            "currency": None,
        },
        "currency": "unknown",
        "scale": _detect_scale(df, header_row),
        "has_opening": False,
        "has_movement": False,
        "n_periods": 1,
        "has_type_column": False,
        "n_accounts": len(accounts),
        "ignored_money_columns": [],
        "mapped_by_user": True,  # signals downstream that columns were user-assigned
    }
    return {
        "sheet": sheet_name,
        "periods": periods,
        "accounts": accounts,
        "parse_report": parse_report,
    }


def _mask_account_pii(accounts: list[dict]) -> None:
    """Mask PAN/GSTIN/bank-account-shaped substrings in account name/code,
    in place, once at ingest (spec OUT-04) — before storage, so every
    downstream consumer already sees the masked version."""
    for a in accounts:
        if a.get("name"):
            a["name"] = mask_pii(a["name"])
        if a.get("code"):
            a["code"] = mask_pii(str(a["code"]))


def ingest_trial_balance_mapped(
    filename: str,
    data: bytes,
    sheet_name: str,
    header_row: int,
    account_col: int,
    debit_col: int | None = None,
    credit_col: int | None = None,
    balance_col: int | None = None,
    code_col: int | None = None,
) -> dict:
    """Parse with an explicit column mapping and store in the DB (idempotent per file content)."""
    parsed = parse_trial_balance_with_map(
        data, sheet_name, header_row, account_col,
        debit_col=debit_col, credit_col=credit_col,
        balance_col=balance_col, code_col=code_col,
    )
    _mask_account_pii(parsed["accounts"])
    doc_id = "TB-" + hashlib.sha1(data).hexdigest()[:16]
    doc_properties = extract_xlsx_properties(filename, data)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_TB, {
                "doc_id": doc_id,
                "filename": filename[:255],
                "sheet": str(parsed.get("sheet") or "")[:255],
                "periods": json.dumps(parsed["periods"]),
                "parsed": json.dumps(parsed),
                "doc_properties": json.dumps(doc_properties),
            })
        conn.commit()
    finally:
        conn.close()

    return {
        "doc_id": doc_id,
        "filename": filename,
        "sheet": parsed.get("sheet"),
        "periods": parsed["periods"],
        "accounts": parsed["parse_report"]["n_accounts"],
        "parse_report": parsed["parse_report"],
    }


# ---------------------------------------------------------------------------
# Storage (server finance_llm DB, port 5478 — reuses core.db.get_connection)
# ---------------------------------------------------------------------------
#
# Parsed trial-balance input lives on the server ``finance_llm`` database in the
# ``tb_input_data`` table (structured numeric JSON, no embeddings) — separate
# from the local docker DB that holds uploaded PDFs.

_TB_INPUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tb_input_data (
    doc_id      VARCHAR(80)  PRIMARY KEY,   -- "TB-<sha1(file)[:16]>" (stable per file)
    filename    VARCHAR(255) NOT NULL,
    sheet       VARCHAR(255),               -- worksheet the TB was parsed from
    periods     JSONB        NOT NULL,      -- ["Current","Prior"] or ["Balance"]
    parsed      JSONB        NOT NULL,      -- full canonical TB structure
    uploaded_at TIMESTAMPTZ  NOT NULL DEFAULT now()
)
"""

# Additive columns for FDR company resolution (see fdr/fdr_corpus_extract.py):
# doc_properties comes from the .xlsx file's own docProps (Company/Title/etc,
# read once at upload time); company_hint is set only by an explicit user
# action (PATCH /api/trial-balances/{doc_id}/company) and always wins.
_TB_INPUT_ALTER = [
    "ALTER TABLE tb_input_data ADD COLUMN IF NOT EXISTS "
    "doc_properties JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE tb_input_data ADD COLUMN IF NOT EXISTS company_hint VARCHAR(255)",
]


def ensure_tb_input_schema() -> None:
    """Create/upgrade the ``tb_input_data`` table on the server DB if needed.

    Idempotent; safe to call on every startup. The ``finance_llm`` database
    already exists (it holds the corpora), so this is CREATE TABLE + additive
    ALTER TABLE only. Raises if the server Postgres is unreachable, for the
    caller to handle best-effort.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_TB_INPUT_SCHEMA)
            for stmt in _TB_INPUT_ALTER:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


_UPSERT_TB = """
INSERT INTO tb_input_data (doc_id, filename, sheet, periods, parsed, doc_properties)
VALUES (%(doc_id)s, %(filename)s, %(sheet)s, %(periods)s, %(parsed)s, %(doc_properties)s)
ON CONFLICT (doc_id) DO UPDATE SET
    filename = EXCLUDED.filename,
    sheet = EXCLUDED.sheet,
    periods = EXCLUDED.periods,
    parsed = EXCLUDED.parsed,
    doc_properties = EXCLUDED.doc_properties,
    uploaded_at = now()
"""


def ingest_trial_balance(filename: str, data: bytes) -> dict:
    """Parse a TB Excel and store its canonical JSON (idempotent per file content).

    ``doc_id = "TB-" + sha1(file)[:16]`` so re-uploading the same file replaces
    its row rather than duplicating. Also captures the .xlsx file's own document
    properties (Company/Title/etc, when present) as an FDR company-resolution
    signal — see ``fdr/fdr_corpus_extract.py::resolve_company_for_tb``. Returns
    a summary for the API/UI.
    """
    parsed = parse_trial_balance(data)
    _mask_account_pii(parsed["accounts"])
    doc_id = "TB-" + hashlib.sha1(data).hexdigest()[:16]
    doc_properties = extract_xlsx_properties(filename, data)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_TB, {
                "doc_id": doc_id,
                "filename": filename[:255],
                "sheet": str(parsed.get("sheet") or "")[:255],
                "periods": json.dumps(parsed["periods"]),
                "parsed": json.dumps(parsed),
                "doc_properties": json.dumps(doc_properties),
            })
        conn.commit()
    finally:
        conn.close()

    return {
        "doc_id": doc_id,
        "filename": filename,
        "sheet": parsed.get("sheet"),
        "periods": parsed["periods"],
        "accounts": parsed["parse_report"]["n_accounts"],
        "parse_report": parsed["parse_report"],
    }


def get_trial_balance(doc_id: str) -> dict | None:
    """Load a stored TB's canonical structure, or None if not found."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, sheet, parsed, doc_properties, company_hint "
                "FROM tb_input_data WHERE doc_id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    filename, sheet, parsed, doc_properties, company_hint = row
    # psycopg2 returns jsonb as a dict already; guard for text just in case
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if isinstance(doc_properties, str):
        doc_properties = json.loads(doc_properties)
    parsed["doc_id"] = doc_id
    parsed["filename"] = filename
    parsed["sheet"] = sheet
    parsed["doc_properties"] = doc_properties or {}
    parsed["company_hint"] = company_hint
    return parsed


def set_tb_company_hint(doc_id: str, company: str) -> bool:
    """Explicitly tag a stored TB with a company name (user-provided, wins over
    every automated signal). True if a row was updated."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_input_data SET company_hint = %s WHERE doc_id = %s",
                (company, doc_id),
            )
            updated = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return updated > 0


def list_trial_balances() -> list[dict]:
    """All stored trial balances, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, filename, sheet, periods, uploaded_at "
                "FROM tb_input_data ORDER BY uploaded_at DESC"
            )
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        if isinstance(d.get("periods"), str):
            d["periods"] = json.loads(d["periods"])
        out.append(d)
    return out


def delete_trial_balance(doc_id: str) -> bool:
    """Delete a stored trial balance. True if a row was removed."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tb_input_data WHERE doc_id = %s", (doc_id,))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted > 0
