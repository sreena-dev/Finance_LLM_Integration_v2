"""Chart-of-accounts / management FSLI grouping upload (client-format rule C7).

Parses a small client-supplied spreadsheet mapping GL codes/names to FSLI
groups into a ``{gl_code_or_name: fsli_label}`` override dict, which
``map_accounts()`` uses in place of the keyword engine wherever a match is
found. Reuses the same workbook-reading utility as the trial-balance parser
(handles both real Excel and HTML-table-as-.xls ERP exports).

Supports two real-world layouts, auto-detected:

1. **Flat two-column** lookup table — one row per account, a code/name column
   and a separate FSLI/group column on the same header row.
2. **Line-item-heading** layout — the FSLI/group name appears as its own row
   (blank in the code column), with member accounts listed underneath until
   the next heading row. No group column exists at all in this layout.

Real client exports vary too much in column headers/layout for keyword
matching alone to generalize (confirmed by testing against real files), so
detection tries, in priority order:

0. **Trial-balance value matching** (primary, when the caller supplies the
   accounts of the trial balance this grouping file applies to) — a column
   whose actual cell values are mostly real GL codes/names already present
   in that trial balance *is* the code/name column, regardless of its
   header text or whether it has one at all.
1. **Flat two-column** lookup table — one row per account, a code/name column
   and a separate FSLI/group column, identified via header keywords.
2. **Line-item-heading** layout — the FSLI/group name appears as its own row
   (blank in the code column), with member accounts listed underneath until
   the next heading row. No group column exists at all in this layout.

If none of these can be confidently detected, ``GroupingParseError`` is
raised carrying a raw-row ``.preview`` of the file so the caller can offer a
manual column-mapping fallback instead of a dead-end error.
"""

from __future__ import annotations

import pandas as pd

from yukta_rag.trial_balance.trial_balance import _read_workbook, preview_workbook

_CODE_HEADER_KWS = ("account code", "acc code", "gl code", "g/l acct", "g/l code",
                    "ledger code", "a/c code", "account no", "acc no", "a/c no", "code")
_NAME_HEADER_KWS = ("account name", "gl name", "ledger name", "name", "ledger",
                    "description", "particulars")
_GROUP_HEADER_KWS = ("fsli", "group", "classification", "category", "line item",
                     "schedule iii head", "schedule", "head", "mapping", "map",
                     "financial statement line")

_CODE_FALLBACK_KWS = ("code", "account", "ledger", "particular")
_GROUP_FALLBACK_KWS = ("group", "class", "item", "head", "map", "categ")

_HEADER_SCAN_ROWS = 20

# Trial-balance value-matching thresholds (§0 above) — a column qualifies as
# the code/name column only if a meaningful share of its values are real
# accounts, not a handful of coincidental matches.
_MIN_MATCH_HITS = 3
_MIN_MATCH_RATE = 0.3

# Cardinality heuristic for the FSLI/group column once code/name are known —
# a real grouping column repeats far more than a per-row code/name column.
_MAX_GROUP_DISTINCT_RATIO = 0.5


class GroupingParseError(ValueError):
    """Raised when neither the flat nor line-item-heading layout can be
    confidently detected. Carries a raw-row preview so the caller can offer
    a manual column-mapping fallback instead of a dead end."""

    def __init__(self, message: str, preview: dict | None = None):
        super().__init__(message)
        self.preview = preview


def _norm_headers(row: list) -> list[str]:
    return [str(v).strip() if pd.notna(v) else "" for v in row]


def _find_col(headers: list[str], kws: tuple[str, ...], exclude: set[int] = frozenset()) -> int | None:
    for i, h in enumerate(headers):
        if i in exclude:
            continue
        low = (h or "").strip().lower()
        if any(k in low for k in kws):
            return i
    return None


def _detect_cols(headers: list[str]) -> tuple[int | None, int | None, int | None]:
    """Resolve (code_col, name_col, group_col) for one candidate header row,
    trying the specific keyword lists first and only falling back to the
    loose single-token lists for whichever role is still unresolved."""
    code_col = _find_col(headers, _CODE_HEADER_KWS)
    name_col = _find_col(headers, _NAME_HEADER_KWS, exclude={code_col} if code_col is not None else set())
    group_col = _find_col(headers, _GROUP_HEADER_KWS)

    claimed = {c for c in (code_col, name_col, group_col) if c is not None}
    if code_col is None:
        code_col = _find_col(headers, _CODE_FALLBACK_KWS, exclude=claimed)
        if code_col is not None:
            claimed.add(code_col)
    if name_col is None:
        name_col = _find_col(headers, _CODE_FALLBACK_KWS, exclude=claimed)
        if name_col is not None:
            claimed.add(name_col)
    if group_col is None:
        group_col = _find_col(headers, _GROUP_FALLBACK_KWS, exclude=claimed)

    return code_col, name_col, group_col


def _find_grouping_header(df: pd.DataFrame) -> tuple[int, int | None, int | None, int | None] | None:
    """Scan the first rows for a flat-format header (Pass A): a row where a
    group column AND at least one of code/name resolve. Returns
    (header_row_index, code_col, name_col, group_col) or None."""
    limit = min(_HEADER_SCAN_ROWS, len(df))
    for i in range(limit):
        headers = _norm_headers(df.iloc[i].tolist())
        code_col, name_col, group_col = _detect_cols(headers)
        if group_col is not None and (code_col is not None or name_col is not None):
            return i, code_col, name_col, group_col
    return None


def _find_heading_format_header(df: pd.DataFrame) -> tuple[int, int, int] | None:
    """Scan the first rows for a heading-format header (Pass B): a row where
    both a code column and a name column resolve, with no group column
    required. Returns (header_row_index, code_col, name_col) or None."""
    limit = min(_HEADER_SCAN_ROWS, len(df))
    for i in range(limit):
        headers = _norm_headers(df.iloc[i].tolist())
        code_col, name_col, _group_col = _detect_cols(headers)
        if code_col is not None and name_col is not None:
            return i, code_col, name_col
    return None


def _cell(row: list, col: int | None) -> str:
    if col is None or col >= len(row) or not pd.notna(row[col]):
        return ""
    return str(row[col]).strip()


def _parse_flat(df: pd.DataFrame, header_row: int, code_col: int | None,
                 name_col: int | None, group_col: int) -> dict[str, str]:
    override: dict[str, str] = {}
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i].tolist()
        group = _cell(row, group_col)
        if not group:
            continue
        code = _cell(row, code_col)
        name = _cell(row, name_col)
        if code:
            override[code] = group
        if name:
            override[name] = group
    return override


def _parse_heading_format(df: pd.DataFrame, header_row: int, code_col: int,
                           name_col: int) -> dict[str, str]:
    override: dict[str, str] = {}
    current_heading: str | None = None
    heading_rows_seen = 0
    data_rows_seen = 0
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i].tolist()
        code = _cell(row, code_col)
        name = _cell(row, name_col)
        if not code and not name:
            continue
        if not code and name:
            current_heading = name
            heading_rows_seen += 1
            continue
        if current_heading is None:
            continue
        override[code] = current_heading
        if name:
            override[name] = current_heading
        data_rows_seen += 1

    if heading_rows_seen < 1 or data_rows_seen < 1 or heading_rows_seen >= data_rows_seen:
        return {}
    return override


def _build_tb_sets(tb_accounts: list[dict] | None) -> tuple[set[str], set[str]]:
    """Real account codes/names from the trial balance this grouping file
    applies to, normalized exactly as ``map_accounts()`` normalizes them
    (bare ``.strip()``, no case-folding) so a column that matches here is
    guaranteed to actually hit once the override dict is applied for real."""
    codes: set[str] = set()
    names: set[str] = set()
    for a in (tb_accounts or []):
        code = a.get("code")
        if code:
            codes.add(str(code).strip())
        name = a.get("name")
        if name:
            names.add(str(name).strip())
    return codes, names


def _match_tb_columns(df: pd.DataFrame, tb_codes: set[str], tb_names: set[str]
                       ) -> tuple[int | None, int | None]:
    """Identify (code_col, name_col) by counting exact-value hits against the
    real trial balance's account codes/names — works regardless of header
    text, or even whether a header row exists at all."""
    n_rows = len(df)
    best_code_col, best_code_hits = None, 0
    best_name_col, best_name_hits = None, 0
    for c in range(df.shape[1]):
        non_blank = 0
        code_hits = 0
        name_hits = 0
        for i in range(n_rows):
            val = df.iat[i, c]
            if not pd.notna(val):
                continue
            s = str(val).strip()
            if not s:
                continue
            non_blank += 1
            if s in tb_codes:
                code_hits += 1
            if s in tb_names:
                name_hits += 1
        if non_blank == 0:
            continue
        if (code_hits >= _MIN_MATCH_HITS and code_hits / non_blank >= _MIN_MATCH_RATE
                and code_hits > best_code_hits):
            best_code_col, best_code_hits = c, code_hits
        if (name_hits >= _MIN_MATCH_HITS and name_hits / non_blank >= _MIN_MATCH_RATE
                and name_hits > best_name_hits):
            best_name_col, best_name_hits = c, name_hits
    if best_name_col is not None and best_name_col == best_code_col:
        best_name_col = None  # a column can't be both; code match wins
    return best_code_col, best_name_col


def _keyword_group_col_anywhere(df: pd.DataFrame, exclude: set[int]) -> tuple[int, int] | None:
    """Best-effort: scan the first rows for a recognizable FSLI/group header —
    used only after code/name columns are already known via value-matching.
    Returns ``(header_row_index, group_col)`` so the caller can skip that row
    when parsing data, or ``None``."""
    limit = min(_HEADER_SCAN_ROWS, len(df))
    for i in range(limit):
        headers = _norm_headers(df.iloc[i].tolist())
        col = _find_col(headers, _GROUP_HEADER_KWS, exclude=exclude)
        if col is not None:
            return i, col
    return None


def _looks_like_header_row(df: pd.DataFrame, row_idx: int, code_col: int | None,
                            name_col: int | None, tb_codes: set[str], tb_names: set[str]) -> bool:
    """True if the matched code/name columns hold non-account text on this
    row — i.e. it's almost certainly a header/title row, not real data."""
    row = df.iloc[row_idx].tolist()
    code_val = _cell(row, code_col)
    name_val = _cell(row, name_col)
    if code_col is not None and code_val and code_val not in tb_codes:
        return True
    if name_col is not None and name_val and name_val not in tb_names:
        return True
    return False


def _cardinality_group_col(df: pd.DataFrame, exclude: set[int]) -> int | None:
    """Fallback FSLI/group column guess: among the remaining columns, the one
    whose values repeat the most (lowest distinct/non-blank ratio) — a real
    grouping column is shared by many accounts, unlike a per-row code/name."""
    best_col, best_ratio = None, None
    for c in range(df.shape[1]):
        if c in exclude:
            continue
        values = [str(df.iat[i, c]).strip() for i in range(len(df))
                  if pd.notna(df.iat[i, c]) and str(df.iat[i, c]).strip()]
        if len(values) < 2:
            continue
        distinct = len(set(values))
        if distinct < 2:
            continue
        ratio = distinct / len(values)
        if ratio <= _MAX_GROUP_DISTINCT_RATIO and (best_ratio is None or ratio < best_ratio):
            best_col, best_ratio = c, ratio
    return best_col


def _parse_via_tb_match(df: pd.DataFrame, tb_codes: set[str], tb_names: set[str]) -> dict[str, str]:
    """§0 — detect columns by matching real trial-balance values, then look
    for a group column among the rest (keyword header, else cardinality),
    falling back to line-item-heading if no group column is found."""
    code_col, name_col = _match_tb_columns(df, tb_codes, tb_names)
    if code_col is None and name_col is None:
        return {}
    exclude = {c for c in (code_col, name_col) if c is not None}

    # Matching doesn't need a header row to work, but if row 0's matched
    # columns hold non-account text (the usual case — a real header), skip
    # it during parsing so it doesn't leak into the override as a bogus row.
    row0_is_header = len(df) > 0 and _looks_like_header_row(df, 0, code_col, name_col, tb_codes, tb_names)
    header_row = 0 if row0_is_header else -1

    kw = _keyword_group_col_anywhere(df, exclude)
    if kw is not None:
        kw_header_row, group_col = kw
        override = _parse_flat(df, max(header_row, kw_header_row), code_col, name_col, group_col)
        if override:
            return override

    group_col = _cardinality_group_col(df, exclude)
    if group_col is not None:
        override = _parse_flat(df, header_row, code_col, name_col, group_col)
        if override:
            return override

    if code_col is not None and name_col is not None:
        override = _parse_heading_format(df, header_row, code_col, name_col)
        if override:
            return override
    return {}


def parse_grouping_file(data: bytes, tb_accounts: list[dict] | None = None) -> dict[str, str]:
    """Return ``{code_or_name: fsli_label}`` from the first usable sheet.

    When ``tb_accounts`` (the accounts of the trial balance this grouping
    file applies to) is supplied, tries trial-balance value-matching first —
    the most reliable signal, since it doesn't depend on header wording at
    all. Falls back to the flat two-column layout, then the line-item-heading
    layout, each via header-keyword detection scanning multiple candidate
    header rows. Raises ``GroupingParseError`` (a ``ValueError`` subclass
    carrying a ``.preview`` of the raw rows) when nothing can be confidently
    detected — the caller should surface the preview to the user rather than
    silently proceeding.

    When ``tb_accounts`` is supplied but value-matching finds nothing, a
    keyword-based guess is only accepted if it produces at least one entry
    that actually corresponds to a real account in that trial balance —
    otherwise a file whose columns merely *look* labelled right (e.g. a
    header containing the word "code" or "item" for unrelated reasons) could
    be confidently misparsed even though it has nothing to do with this TB.
    """
    sheets = _read_workbook(data)
    tb_codes, tb_names = _build_tb_sets(tb_accounts)
    tb_all = tb_codes | tb_names

    for _sheet_name, df in sheets.items():
        if df.empty or len(df) < 2:
            continue

        if tb_codes or tb_names:
            override = _parse_via_tb_match(df, tb_codes, tb_names)
            if override:
                return override

        flat = _find_grouping_header(df)
        if flat is not None:
            header_row, code_col, name_col, group_col = flat
            override = _parse_flat(df, header_row, code_col, name_col, group_col)
            if override and (not tb_all or set(override) & tb_all):
                return override

        heading = _find_heading_format_header(df)
        if heading is not None:
            header_row, code_col, name_col = heading
            override = _parse_heading_format(df, header_row, code_col, name_col)
            if override and (not tb_all or set(override) & tb_all):
                return override

    message = (
        "Could not find both a GL code/account-name column and an FSLI/group column "
        "in the uploaded grouping file. Expected headers like 'Account Code'/'Account "
        "Name' and 'FSLI'/'Group'/'Classification' (or a layout where each FSLI heading "
        "is its own row with member accounts listed underneath it).")
    if tb_codes or tb_names:
        message += (
            " None of this file's columns contain account codes or names that match "
            "the selected trial balance — confirm this is the right file, or map "
            "columns manually below.")
    raise GroupingParseError(message, preview=preview_workbook(data))


def parse_grouping_file_with_map(data: bytes, sheet_name: str, header_row: int,
                                   code_col: int | None, name_col: int | None,
                                   group_col: int | None,
                                   heading_mode: bool = False) -> dict[str, str]:
    """Apply an explicit, user-supplied column mapping (manual fallback path)
    — no auto-detection, used after auto-detection in ``parse_grouping_file``
    has failed and the user has confirmed which columns mean what."""
    sheets = _read_workbook(data)
    if sheet_name not in sheets:
        raise ValueError(f"sheet {sheet_name!r} not found in workbook")
    df = sheets[sheet_name]

    if heading_mode:
        if code_col is None or name_col is None:
            raise ValueError("heading-mode mapping requires both an account code and account name column")
        override: dict[str, str] = {}
        current_heading: str | None = None
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i].tolist()
            code = _cell(row, code_col)
            name = _cell(row, name_col)
            if not code and not name:
                continue
            if not code and name:
                current_heading = name
                continue
            if current_heading is None:
                continue
            override[code] = current_heading
            if name:
                override[name] = current_heading
        if not override:
            raise ValueError("no accounts could be assigned to a heading with the selected columns")
        return override

    if group_col is None:
        raise ValueError("an FSLI/group column is required unless heading-mode is selected")
    if code_col is None and name_col is None:
        raise ValueError("at least one of account code or account name column is required")
    override = _parse_flat(df, header_row, code_col, name_col, group_col)
    if not override:
        raise ValueError("no rows could be mapped with the selected columns")
    return override
