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
   header text or whether it has one at all. Values are compared via
   ``norm_key`` and ``code_variants``, so an entity-prefixed or zero-padded
   code (``GAIL/1010010`` vs ``1010010`` vs ``0001010010``) still matches.
1. **Flat two-column** lookup table — one row per account, a code/name column
   and a separate FSLI/group column, identified via header keywords.
2. **Line-item-heading** layout — the FSLI/group name appears as its own row
   (blank in the code column), with member accounts listed underneath until
   the next heading row. No group column exists at all in this layout.
3. **Structural** — read the layout off the shape of the data, ignoring header
   text entirely: the grouping column is the one whose values repeat (shared by
   many accounts), the identifier column the one that is near-unique per row.
   This is what handles headers no keyword list anticipates; a real file headed
   ``Parent | Line Item`` has distinct-value ratios of 0.002 against 1.0.

Passes 1-3 are guesses, so each is accepted only on evidence that it addresses
the supplied trial balance (``_accepts``) — "at least one row matched" is not
enough, because a long sheet of unrelated data will contain a few strings that
look like account names. A confident wrong grouping is worse than the manual
mapper: it silently mis-classifies the whole run.

If none of these can be confidently detected, ``GroupingParseError`` is
raised carrying a raw-row ``.preview`` of the file so the caller can offer a
manual column-mapping fallback instead of a dead-end error.
"""

from __future__ import annotations

import re

import pandas as pd

from yukta_rag.trial_balance.trial_balance import _read_workbook, preview_workbook

_CODE_HEADER_KWS = ("account code", "acc code", "gl code", "g/l acct", "g/l code",
                    "ledger code", "a/c code", "account no", "acc no", "a/c no", "code")
# "line item" belongs here, not in the group list: in real exports a bare
# "Line Item" column holds the account/line name, and the column naming its
# group is a sibling ("Parent", "FSV Node", "Schedule III Head"). The long forms
# that unambiguously name an FSLI stay in _GROUP_HEADER_KWS below.
_NAME_HEADER_KWS = ("account name", "gl name", "ledger name", "name", "ledger",
                    "description", "particulars", "line item")
# Ordered specific -> loose, because _find_col returns the first keyword that hits.
# "parent"/"node"/"level"/"fsv" cover SAP FSV and Indian PSU hierarchy exports,
# where the grouping column is the parent node rather than a labelled FSLI.
_GROUP_HEADER_KWS = ("fsli", "financial statement line", "fs line item", "reporting line",
                     "schedule iii head", "schedule", "classification", "category",
                     "group", "grouping", "hierarchy", "parent", "major head",
                     "sub head", "head", "fsv", "node", "level", "mapping", "map")

_CODE_FALLBACK_KWS = ("code", "account", "ledger", "particular")
_GROUP_FALLBACK_KWS = ("group", "class", "item", "head", "map", "categ")

_HEADER_SCAN_ROWS = 20

# Trial-balance value-matching thresholds (§0 above) — a column qualifies as
# the code/name column only if the hits are meaningful rather than a handful of
# coincidences. Two ways to qualify, because grouping files come in two shapes:
#   * a small purpose-built mapping file, where most rows are real accounts
#     -> judge by the share of the COLUMN that matches (_MIN_MATCH_RATE);
#   * a large FSV/taxonomy export, where real GL rows are a minority among
#     headings, subtotals and ratio inputs -> judge by the share of the TRIAL
#     BALANCE covered (_MIN_TB_COVERAGE), which a 30%-of-column floor rejects
#     even when hundreds of genuine accounts are present.
# The coverage arm needs a higher absolute count so a few coincidental hits
# against a small trial balance can't clear it on their own.
_MIN_MATCH_HITS = 3
_MIN_MATCH_RATE = 0.3
_MIN_TB_COVERAGE = 0.10
_MIN_COVERAGE_HITS = 10

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


# Client grouping files spell the same account differently from the trial
# balance: upper-case or doubled-space names are routine (an all-caps name-only
# grouping file against a title-case TB was measured failing detection outright).
# Comparing raw strings therefore misses matches that are the same account, and
# it misses twice over: the value-matching detection below finds too few hits and
# gives up, and even a correctly parsed override then silently fails to apply in
# ``map_accounts()``, reverting the run to keyword inference with no error.
# The ``.0`` rule is a defensive guard for numeric codes read as floats — the
# current pandas read paths keep them as ints, but ERP HTML exports and older
# pandas do not.
_TRAILING_ZEROS_RE = re.compile(r"-?\d+\.0+")
_WHITESPACE_RE = re.compile(r"\s+")


def norm_key(value) -> str:
    """Canonical form for comparing a grouping-file value to a TB account.

    Strips the ``.0`` Excel appends to integral numeric codes, collapses runs of
    whitespace and case-folds. Used on *both* sides of every comparison, so
    detection (``_match_tb_columns``) and application (``map_accounts``) can
    never disagree about whether two values are the same account.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if _TRAILING_ZEROS_RE.fullmatch(text):
        text = text.split(".", 1)[0]
    return _WHITESPACE_RE.sub(" ", text).casefold()


# Namespace separators an ERP puts in front of a GL code. Deliberately excludes
# "-", which appears *inside* real account codes often enough ("2001-SUB",
# "1001-01") that splitting on it would invent variants like "01" that collide
# with unrelated accounts.
_CODE_SEPARATORS = ("/", "\\", ":", "|")
_MIN_VARIANT_LEN = 3


def code_variants(value) -> set[str]:
    """Every form a GL code might legitimately be written in, for matching only.

    A trial balance carrying ``GAIL/1010010`` and a grouping file carrying
    ``1010010`` or ``0001010010`` mean the same account, but no two of those
    strings are equal — so literal matching finds none of them, and the
    value-matching detection that is supposed to work without trusting headers
    silently finds zero hits. Every GAIL TB code is entity-prefixed this way.

    Returns the canonical form plus the segment after any namespace separator
    and the zero-unpadded forms. Used for *matching* only; never to merge two
    trial-balance accounts (see ``_build_tb_sets``, which drops any variant that
    two different accounts share).
    """
    base = norm_key(value)
    if not base:
        return set()
    out = {base}
    for sep in _CODE_SEPARATORS:
        if sep in base:
            tail = base.rsplit(sep, 1)[-1].strip()
            if len(tail) >= _MIN_VARIANT_LEN:
                out.add(tail)
    for v in list(out):
        # SAP pads GL codes with leading zeros; only unpad pure digit strings so
        # an alphanumeric key like "0AB12" keeps its meaning.
        if v.startswith("0") and v.isdigit():
            unpadded = v.lstrip("0")
            if len(unpadded) >= _MIN_VARIANT_LEN:
                out.add(unpadded)
    return out


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


def _assign(override: dict[str, str], value: str, group: str) -> None:
    """Record ``value -> group`` under its raw, canonical and code-variant forms.

    Keeping the raw key preserves exact-match behaviour for any caller that
    looks the override up verbatim; the ``norm_key`` alias is what lets
    ``map_accounts()`` still hit when the trial balance spells the same account
    slightly differently (different case or spacing); the ``code_variants``
    aliases cover an entity prefix or zero padding on the file's side.

    Variant aliases never overwrite an existing entry — a variant is a weaker,
    derived form, so where two rows disagree the directly-written key wins.
    """
    if not value:
        return
    override[value] = group
    canonical = norm_key(value)
    if canonical and canonical != value:
        override[canonical] = group
    for variant in code_variants(value):
        override.setdefault(variant, group)


def _parse_flat(df: pd.DataFrame, header_row: int, code_col: int | None,
                 name_col: int | None, group_col: int) -> dict[str, str]:
    override: dict[str, str] = {}
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i].tolist()
        group = _cell(row, group_col)
        if not group:
            continue
        _assign(override, _cell(row, code_col), group)
        _assign(override, _cell(row, name_col), group)
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
        _assign(override, code, current_heading)
        _assign(override, name, current_heading)
        data_rows_seen += 1

    if heading_rows_seen < 1 or data_rows_seen < 1 or heading_rows_seen >= data_rows_seen:
        return {}
    return override


def _build_tb_sets(tb_accounts: list[dict] | None) -> tuple[set[str], set[str]]:
    """Matchable codes/names from the trial balance this grouping file applies to.

    Codes are indexed under every form in ``code_variants`` so an entity-prefixed
    or zero-padded code still matches; names use ``norm_key`` only, since a
    namespace split makes no sense for prose. A variant produced by two different
    accounts is ambiguous and is dropped rather than allowed to match either —
    otherwise a code like ``1001-01`` could lend its tail to an unrelated row.
    """
    variant_owners: dict[str, set[str]] = {}
    names: set[str] = set()
    for a in (tb_accounts or []):
        canonical = norm_key(a.get("code"))
        if canonical:
            for variant in code_variants(canonical):
                variant_owners.setdefault(variant, set()).add(canonical)
        name = norm_key(a.get("name"))
        if name:
            names.add(name)
    codes = {v for v, owners in variant_owners.items() if len(owners) == 1}
    return codes, names


def _qualifies(hits: int, non_blank: int, n_tb: int,
                min_hits: int = _MIN_MATCH_HITS) -> bool:
    """Whether a hit count is strong enough to trust — see the threshold comments
    at the top of this module for why there are two ways to qualify.

    ``min_hits`` guards the rate arm against small-sample noise. Picking a column
    out of a wide sheet needs the default floor, because 1-2 coincidental hits in
    a long column say nothing. Judging a whole *file* uses ``min_hits=1``: there
    is no coincidence story for a two-row file whose every row names a real
    account, and a deliberate partial grouping is legitimate.
    """
    if hits < min_hits or non_blank == 0:
        return False
    if hits / non_blank >= _MIN_MATCH_RATE:
        return True
    return (hits >= _MIN_COVERAGE_HITS and n_tb > 0
            and hits / n_tb >= _MIN_TB_COVERAGE)


def _match_tb_columns(df: pd.DataFrame, tb_codes: set[str], tb_names: set[str],
                       n_tb: int = 0) -> tuple[int | None, int | None]:
    """Identify (code_col, name_col) by counting value hits against the real
    trial balance's account codes/names — works regardless of header text, or
    even whether a header row exists at all.

    Codes are compared through ``code_variants`` so an entity prefix or zero
    padding on either side still matches; names through ``norm_key``. ``n_tb``
    is the trial balance's account count, used by the coverage arm of
    ``_qualifies``."""
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
            s = norm_key(val)
            if not s:
                continue
            non_blank += 1
            if code_variants(val) & tb_codes:
                code_hits += 1
            if s in tb_names:
                name_hits += 1
        if non_blank == 0:
            continue
        if _qualifies(code_hits, non_blank, n_tb) and code_hits > best_code_hits:
            best_code_col, best_code_hits = c, code_hits
        if _qualifies(name_hits, non_blank, n_tb) and name_hits > best_name_hits:
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
    code_val = norm_key(_cell(row, code_col))
    name_val = norm_key(_cell(row, name_col))
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


def _parse_via_tb_match(df: pd.DataFrame, tb_codes: set[str], tb_names: set[str],
                         n_tb: int = 0) -> dict[str, str]:
    """§0 — detect columns by matching real trial-balance values, then look
    for a group column among the rest (keyword header, else cardinality),
    falling back to line-item-heading if no group column is found."""
    code_col, name_col = _match_tb_columns(df, tb_codes, tb_names, n_tb)
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


def _accepts(df: pd.DataFrame, ident_cols: tuple[int | None, ...],
              tb_codes: set[str], tb_names: set[str], n_tb: int) -> bool:
    """Evidence gate for a *guessed* layout: do the identifier columns actually
    address this trial balance?

    Keyword and structural detection both guess, and "at least one row matched"
    is far too weak a check on a guess — a 500-row sheet of unrelated data will
    coincidentally contain a few strings that look like account names, and would
    then be accepted as a grouping and silently mis-classify the run. This
    applies the same two-armed test as value-matching (``_qualifies``), counting
    a row as a hit when any of its identifier cells names a real account:

      * a purpose-built mapping file -> most of its rows are real accounts;
      * a large FSV/taxonomy export  -> few of its rows are, but it still covers
        a real share of the trial balance.

    A file that clears neither is the wrong file for this trial balance, and the
    manual mapper is a better answer than a confident wrong one.
    """
    cols = [c for c in ident_cols if c is not None]
    if not cols:
        return False
    hits = non_blank = 0
    for i in range(len(df)):
        row_has_value = row_matched = False
        for c in cols:
            if c >= df.shape[1]:
                continue
            val = df.iat[i, c]
            if not pd.notna(val) or not str(val).strip():
                continue
            row_has_value = True
            if code_variants(val) & tb_codes or norm_key(val) in tb_names:
                row_matched = True
        non_blank += row_has_value
        hits += row_matched
    return _qualifies(hits, non_blank, n_tb, min_hits=1)


def _column_values(df: pd.DataFrame, col: int) -> list[str]:
    return [str(df.iat[i, col]).strip() for i in range(len(df))
            if pd.notna(df.iat[i, col]) and str(df.iat[i, col]).strip()]


def _structure_columns(df: pd.DataFrame) -> tuple[int, int] | None:
    """§3 — detect the layout from the shape of the data, ignoring header text.

    Header wording is the least reliable signal there is: this module's own
    premise is that client exports vary too much for any keyword list to
    generalize, and a file headed ``Parent | Line Item`` defeats keywords twice
    over (the group column's name is not in any list, and the name column's is).
    Structurally the two are unmistakable though — a grouping column is shared by
    many accounts while a code/name column is near-unique per row. On the real
    reported file that is a distinct-value ratio of 0.002 against 1.0.

    Picks the most-repeating column as the group and the most-unique remaining
    column as the account identifier. Whether that identifier holds codes or
    names doesn't matter: ``_assign`` stores it either way and ``map_accounts``
    tries an account's code and its name against the override.

    Returns ``(header_row, ident_col, group_col)`` or None.
    """
    if df.shape[1] < 2:
        return None
    group_col = _cardinality_group_col(df, exclude=set())
    if group_col is None:
        return None

    ident_col, best_ratio = None, -1.0
    for c in range(df.shape[1]):
        if c == group_col:
            continue
        values = _column_values(df, c)
        if len(values) < 2:
            continue
        ratio = len(set(values)) / len(values)
        if ratio > best_ratio:
            ident_col, best_ratio = c, ratio
    if ident_col is None:
        return None

    # Treat row 0 as a header when its group cell is a one-off: a real grouping
    # value is shared by many rows, a column title by none.
    group_values = _column_values(df, group_col)
    first = str(df.iat[0, group_col]).strip() if pd.notna(df.iat[0, group_col]) else ""
    header_row = 0 if first and group_values.count(first) == 1 else -1
    return header_row, ident_col, group_col


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

    Every comparison against the trial balance goes through ``norm_key``, and
    the returned dict carries both the raw cell value and its canonical form as
    keys, so ``map_accounts()`` hits even when the two files format the same
    account differently.

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
    n_tb = len(tb_accounts or [])

    def usable(override: dict[str, str], df: pd.DataFrame,
               ident_cols: tuple[int | None, ...]) -> bool:
        """A guessed layout is only accepted on real evidence that it addresses
        this trial balance — see ``_accepts``. With no trial balance to check
        against there is nothing to weigh, so any successful parse stands."""
        if not override:
            return False
        if not tb_all:
            return True
        return _accepts(df, ident_cols, tb_codes, tb_names, n_tb)

    for _sheet_name, df in sheets.items():
        if df.empty or len(df) < 2:
            continue

        if tb_codes or tb_names:
            override = _parse_via_tb_match(df, tb_codes, tb_names, n_tb)
            if override:
                return override

        flat = _find_grouping_header(df)
        if flat is not None:
            header_row, code_col, name_col, group_col = flat
            override = _parse_flat(df, header_row, code_col, name_col, group_col)
            if usable(override, df, (code_col, name_col)):
                return override

        heading = _find_heading_format_header(df)
        if heading is not None:
            header_row, code_col, name_col = heading
            override = _parse_heading_format(df, header_row, code_col, name_col)
            if usable(override, df, (code_col, name_col)):
                return override

        # Structural detection reads the layout off the shape of the data, so it
        # only runs when there is a trial balance to validate the guess against.
        # Without one there is no evidence to weigh, and turning any two-column
        # spreadsheet into a grouping is worse than offering the manual mapper.
        if tb_all:
            found = _structure_columns(df)
            if found is not None:
                header_row, ident_col, group_col = found
                override = _parse_flat(df, header_row, None, ident_col, group_col)
                if usable(override, df, (ident_col,)):
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


def count_tb_matches(override: dict[str, str], tb_accounts: list[dict] | None) -> int:
    """How many accounts of ``tb_accounts`` the override will actually classify.

    An override can parse cleanly and still match nothing — wrong file for this
    trial balance, or codes that don't correspond. That produces a run which
    looks fine but silently fell back to keyword inference, so callers surface
    this count to the user instead of letting it pass unnoticed. Returns 0 when
    no trial balance was supplied to compare against."""
    if not tb_accounts:
        return 0
    keys = {norm_key(k) for k in override} - {""}
    matched = 0
    for a in (tb_accounts or []):
        if code_variants(a.get("code")) & keys or norm_key(a.get("name")) in keys:
            matched += 1
    return matched


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
            _assign(override, code, current_heading)
            _assign(override, name, current_heading)
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
