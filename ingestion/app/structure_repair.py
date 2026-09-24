"""Rebuilding a table's row grid from where the ink actually sits.

TableFormer predicts row boundaries once and applies them to every column at
once. When it gets that wrong, it can get it wrong DIFFERENTLY per column --
verified on a real document (OD-SPSU-SO-032 2023-24 SFS, balance sheet):

    RapidOCR's own text lines, completely clean, 99%+ confidence each:
        y=423-442  "(b) Trade payables"          "4"  "18,600.00"        "34,011.00"
        y=443-460  "(c) Other current liabilities" "5" "28,65,46,137.60"  "22,69,36,581.10"
        y=459-478  "(d) Short-term provisions"    "6"  "5,22,78,867.00"   "5,11,11,345.00"

    TableFormer's own grid for the same region:
        row: "(b) Trade payables" | 4 | (blank)              | (blank)
        row: (blank)              | 5 | "18,600.00 28,65,46,137.60" | "34,011.00 22,69,36,581.10"
        row: "(c) Other current liabilities (d) Short-term provisions" | 6 | "5,22,78,867.00" | "5,11,11,345.00"

The label column drew its row boundary between "Trade payables" and "Other
current liabilities"; the value columns drew theirs between "Other current
liabilities" and "Short-term provisions" -- one row's worth of vertical
disagreement, in opposite directions, and every downstream consumer (markdown
export, `tables.parse_markdown_tables`, the enumerator-based text splitter)
only ever sees ONE row grid, already wrong, with no way to know a different
grid was available.

The OCR lines never had this problem -- they are read once, each at its own
position, with no row concept at all. Rebuilding rows directly from their
Y-position, independently for every column, sidesteps TableFormer's row
decision entirely rather than trying to repair it after the fact.

**The invariant is unchanged.** This never computes a value, never invents a
label, never guesses which of two readings is right. It relocates text OCR
already produced to the row its own Y-position says it belongs on. And it is
never trusted blind: the repaired grid replaces the original only when the
column arithmetic it produces reconciles at least as well as the original's
did -- checked with the same `verify._find_footings` every other footing
decision in this pipeline uses. A repair that does not foot is discarded, the
same as a deskew that did not straighten the page or a sharpen that did not
sharpen.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from .config import Config
from .numbers import parse_cell

logger = logging.getLogger(__name__)

#: Same table str.maketrans("0125 8" -> "olsz b") vlm_read._normalise_label
#: uses for OCR-digit/letter lookalikes -- duplicated rather than imported
#: because vlm_read already imports THIS module (row_band/multi_row_band use
#: _header_bottom/_median), so the reverse import would be circular. Any
#: change there should be mirrored here.
_LABEL_LOOKALIKES = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "2": "z"})


def _normalise_label(text: str) -> str:
    lowered = (text or "").strip().lower().translate(_LABEL_LOOKALIKES)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _column_ranges(cells, header_count: int) -> dict[int, tuple[float, float]]:
    """``col_index -> (x0, x1)`` spanning every cell TableFormer placed there.

    Built from the docling grid's OWN column assignment, not guessed --
    TableFormer's columns are reliable even where its ROWS are not, and the
    real document confirms it: label/note/value columns land in narrow,
    non-overlapping X bands.  Spanning cells (``col_end - col_start != 1``)
    are excluded so a caption row that runs across every column cannot smear
    two real columns into one wide band.
    """
    ranges: dict[int, tuple[float, float]] = {}
    for cell in cells:
        if cell.bbox is None or cell.col_end - cell.col_start != 1:
            continue
        col = cell.col_start
        if col >= header_count:
            continue
        l, _, r, _ = cell.bbox
        lo, hi = ranges.get(col, (l, r))
        ranges[col] = (min(lo, l), max(hi, r))
    return _consolidate_overlapping_bands(ranges)


def _consolidate_overlapping_bands(
    ranges: dict[int, tuple[float, float]], iou_threshold: float = 0.5,
) -> dict[int, tuple[float, float]]:
    """Defensive consolidation of two column INDICES whose bands overlap
    heavily -- distinct from every other repair in this module, which fixes
    ROW-boundary inconsistency across columns, not duplicate/overlapping
    detections within one column axis.

    Unlike the row-duplicate-box problem this mirrors (found and fixed for
    TATR, a DETR-style object detector genuinely capable of proposing two
    heavily-overlapping candidate boxes for one physical row), TableFormer
    does not produce independent per-column candidate detections the same
    way -- `_column_ranges`'s own docstring already records that its bands
    were verified non-overlapping on this corpus. So this is precautionary,
    not a fix for a confirmed active bug: kept cheap and safe (union rather
    than picking a winner, since there is no per-band confidence score to
    prefer one over the other the way TATR's row-NMS could) so it can only
    ever collapse a genuine overlap, never discard real structure.
    """
    if len(ranges) < 2:
        return ranges
    items = sorted(ranges.items(), key=lambda kv: kv[1][0])
    # Every ORIGINAL column index is preserved in the result, always --
    # collision only ever widens the band two indices share, never removes
    # an index from the map. Dropping one on collision (an earlier version
    # of this function did exactly that) would silently delete a real
    # column the instant a threshold false-positive fired -- exactly the
    # "never silently delete information" failure this gap exists to guard
    # against, not a corner case to accept.
    groups: list[list[int]] = []
    bands: list[tuple[float, float]] = []
    for col, (lo, hi) in items:
        placed = False
        for gi, (glo, ghi) in enumerate(bands):
            inter = max(0.0, min(hi, ghi) - max(lo, glo))
            union = max(hi, ghi) - min(lo, glo)
            if union > 0 and inter / union > iou_threshold:
                groups[gi].append(col)
                bands[gi] = (min(lo, glo), max(hi, ghi))
                placed = True
                break
        if not placed:
            groups.append([col])
            bands.append((lo, hi))

    result: dict[int, tuple[float, float]] = {}
    for group, band in zip(groups, bands):
        for col in group:
            result[col] = band
    return result


def _nearest_column(x_center: float, ranges: dict[int, tuple[float, float]]) -> int | None:
    best_col, best_dist = None, None
    for col, (lo, hi) in ranges.items():
        dist = 0.0 if lo <= x_center <= hi else min(abs(x_center - lo), abs(x_center - hi))
        if best_dist is None or dist < best_dist:
            best_col, best_dist = col, dist
    return best_col


def _normalise_for_dedup(cells: list[str]) -> str:
    return " ".join((c or "").strip().lower() for c in cells if (c or "").strip())


def _header_bottom(cells) -> float | None:
    """Where the table's own header ends on the page, in Y.

    Docling includes the header's own cells in ``table_cells`` at
    ``row_start == 0`` (confirmed on real output). That is a geometric fact
    about the page, not a guess at the header's text -- so it catches BOTH
    real defects seen on MH-CPSU-ITSL-048 2023-24's balance sheet: a header
    that wraps across two physical printed lines ("For the year ended" /
    "31st March 2024", which docling's OWN header text already joins into
    one string but the OCR-line reconstruction below sees as two separate
    bands), and a document CAPTION ("Balance Sheet as at 31st March.2024")
    sitting just above the header and inside the same scanned region -- an
    exact-text match against the header string catches neither, because
    neither band's text equals the header verbatim.
    """
    tops = [c.bbox[3] for c in cells if c.row_start == 0 and c.bbox is not None]
    return max(tops) if tops else None


# ---------------------------------------------------------------------------
# The number ledger and binder: "the VLM proposes, OCR disposes". Neither
# TableFormer's row grid nor the vision model's own transcription is trusted
# to state a FIGURE by itself -- see the module-level docstring's thesis.
# Every printed number in a table's region is read ONCE, here, from OCR
# geometry alone, into a ledger; a candidate table structure (docling's or
# the VLM's) is then BOUND against that same ledger, and only a bound cell's
# figure is trusted as a plain number. This is what makes "don't hallucinate
# a number" a mechanical property rather than a prompting instruction: the
# model can place a figure, but the figure itself has to already be sitting
# on the page at that position, in OCR's own independent reading of it.
# ---------------------------------------------------------------------------

#: Same bar `vlm_read.row_band`/`compare()` use to accept a label match --
#: duplicated for the same reason `_normalise_label` above is: the import
#: would be circular.
_MIN_ROW_SCORE = 0.72

#: `Config.NUMBER_BINDING_COL_MARGIN_PT`'s default is one character's width
#: at ~10pt type -- an order of magnitude smaller than every column gutter
#: measured on this corpus (84pt+, see NumberToken's docstring). Large
#: enough to absorb one character of interpolation error, small enough
#: that it can never bridge two real columns. See `assign_columns`.

#: Below this OCR-line confidence, a token is background noise, not evidence
#: -- used only to gate GAP FILLING (placing a number with no second reader
#: at all), never to refuse a BINDING (where the VLM's own placement is
#: itself the second reader).
_MIN_GAP_FILL_CONFIDENCE = 0.60

#: Tolerance for "this candidate value equals this OCR figure" -- the exact
#: bar `vlm_read.check_rescue_anchors` and the deleted `_figure_grounding_
#: counts` already used, reused verbatim rather than invented fresh.
_VALUE_MATCH_TOLERANCE = lambda v: max(0.01, abs(v) * 1e-6)  # noqa: E731


@dataclass
class NumberToken:
    """One printed figure, as OCR read it, independent of any table grid.

    ``bbox`` is TOP-LEFT page points, the same space `OcrLine`/`TableCellGeom`
    already use -- no Y-flip needed anywhere in this module (see
    `vlm_read.row_band`'s own note on why it, unlike `crop()`, never flips).

    ``interpolated`` is True when the token's own line held more than one
    numeric token, so its ``bbox`` was derived by character-offset
    interpolation across the line's bbox rather than measured directly.
    Interpolation assumes uniform advance width, which is wrong on
    proportional type -- it is adequate here only because the measured
    column gutters on this corpus (e.g. value columns spanning roughly
    x=531-653 and x=737-856 on OD-SPSU-SO-032's balance sheet, see
    `tests/test_structure_repair.py`) are on the order of 80-100pt, against
    a worst-case interpolation error of about one character width (~6pt at
    10pt type). Consumers that need real positional evidence (gap filling)
    refuse an interpolated token outright; consumers that have a second,
    independent placement to corroborate against (binding) accept it.

    A token has NO ``consumed`` field. Whether a token has been spent is
    state that belongs to ONE binding attempt, tracked by the binder in its
    own local set -- a ledger is built once from the page and then bound
    TWICE (once against docling's table, once against the VLM's), and if
    "consumed" lived on the token, scoring the first candidate would poison
    scoring the second.
    """

    value: float
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None
    interpolated: bool
    column: int | None = None


#: How much shorter than the region's own median OCR-line height a bare
#: 1-2 digit line must be before it's treated as a likely footnote marker
#: rather than a real figure. NOT calibrated against a measured real-corpus
#: case (no confirmed bug motivated this, unlike every other threshold in
#: this module) -- a conservative starting point pending real evidence.
#: Deliberately requires the marker be a genuinely SEPARATE, smaller OCR
#: detection: this pipeline's `OcrLine` carries no font-size field, so a
#: marker MERGED into the same OCR line as its neighbouring real figure
#: (sharing that line's one bbox) cannot be told apart this way at all --
#: guessing there risks the exact silent-value-loss this module refuses to
#: do everywhere else, so it is deliberately left untouched rather than
#: guessed at.
_FOOTNOTE_MARKER_HEIGHT_RATIO = 0.75


def _looks_like_footnote_marker(line, median_line_height: float) -> bool:
    """A short, small OCR line more likely a footnote reference than a
    printed figure -- see `_FOOTNOTE_MARKER_HEIGHT_RATIO`'s own comment for
    the exact, narrow signal this checks and why it stops there.
    """
    text = (line.text or "").strip()
    if not re.fullmatch(r"\d{1,2}", text):
        return False
    if median_line_height <= 0 or line.bbox is None:
        return False
    _, t, _, b = line.bbox
    return (b - t) < median_line_height * _FOOTNOTE_MARKER_HEIGHT_RATIO


def build_number_ledger(
    ocr_lines, header_bottom: float | None = None,
) -> list[NumberToken]:
    """Every numeric figure OCR read inside a table's region, with position.

    One entry per numeric TOKEN, not per line -- a line reading "18,600.00
    28,65,46,137.60" (two real, adjacent columns TableFormer's OCR-line
    reconstruction merged into one detection) yields two tokens, each with
    its own value, so a downstream binder can still tell them apart even
    though RapidOCR only ever gave them one shared bounding box between them.

    Lines wholly above ``header_bottom`` (with the same 3pt jitter tolerance
    `_cluster_rows` uses) are skipped entirely -- without this, a caption
    like "Figures as at 31st March, 2024" contributes phantom `2024`/`31`
    tokens that would otherwise show up as unaccounted-for figures in the
    half-read report, or as gap-fill candidates for cells that were never
    meant to hold them.
    """
    ledger: list[NumberToken] = []
    lines = [l for l in (ocr_lines or []) if l.bbox is not None and l.text.strip()]
    if header_bottom is not None:
        lines = [l for l in lines if l.bbox[1] >= header_bottom - 3.0]
    lines.sort(key=lambda l: (l.bbox[1], l.bbox[0]))

    heights = sorted(l.bbox[3] - l.bbox[1] for l in lines)
    median_line_height = heights[len(heights) // 2] if heights else 0.0

    for line in lines:
        if _looks_like_footnote_marker(line, median_line_height):
            continue
        text = line.text
        matches = list(re.finditer(r"\S+", text))
        numeric: list[tuple[re.Match, float]] = []
        for m in matches:
            v = parse_cell(m.group()).value
            if v is not None:
                numeric.append((m, v))
        if not numeric:
            continue

        l, t, r, b = line.bbox
        n_chars = len(text) or 1

        if len(numeric) == 1 and len(matches) == 1:
            # The whole line is this one token (plus whitespace already
            # stripped by \S+) -- take the line's own bbox untouched. This is
            # the dominant shape on this corpus: each printed figure is its
            # own OCR detection region.
            m, v = numeric[0]
            ledger.append(NumberToken(
                value=v, text=m.group(), bbox=(l, t, r, b),
                confidence=line.confidence, interpolated=False,
            ))
            continue

        for m, v in numeric:
            x0 = l + (r - l) * (m.start() / n_chars)
            x1 = l + (r - l) * (m.end() / n_chars)
            ledger.append(NumberToken(
                value=v, text=m.group(), bbox=(x0, t, x1, b),
                confidence=line.confidence, interpolated=True,
            ))

    return ledger


def assign_columns(
    ledger: list[NumberToken],
    col_bands: dict[int, tuple[float, float]],
    margin: float | None = None,
) -> None:
    """Set ``token.column`` on every token, in place. Refuses (leaves it
    ``None``) rather than guesses on anything ambiguous -- a refused token
    becomes a leftover and its cell becomes unbound, which is always the
    recoverable direction (see `bind_table`, `fill_gaps_from_ledger`).
    """
    if margin is None:
        margin = Config.NUMBER_BINDING_COL_MARGIN_PT
    for token in ledger:
        xc = (token.bbox[0] + token.bbox[2]) / 2.0
        inside = [
            col for col, (lo, hi) in col_bands.items()
            if lo - margin <= xc <= hi + margin
        ]
        if len(inside) == 1:
            col = inside[0]
            if token.interpolated:
                lo, hi = col_bands[col]
                if not (lo - margin <= token.bbox[0] and token.bbox[2] <= hi + margin):
                    continue  # refuse: interpolated span straddles the band edge
            token.column = col
            continue
        if len(inside) >= 2:
            continue  # refuse: ambiguous between two bands

        # Nothing contains it -- fall back to nearest, but only within margin,
        # and only if no second-nearest band is also within margin (equally
        # ambiguous either way).
        distances = sorted(
            (min(abs(xc - lo), abs(xc - hi)), col) for col, (lo, hi) in col_bands.items()
        )
        if not distances or distances[0][0] > margin:
            continue
        if len(distances) >= 2 and distances[1][0] <= margin:
            continue  # refuse: two bands equally close
        token.column = distances[0][1]


@dataclass
class Binding:
    """The result of binding one candidate `Table` against one number
    ledger. See `bind_table`.
    """

    bound: dict[tuple[int, int], NumberToken] = field(default_factory=dict)
    unbound: set[tuple[int, int]] = field(default_factory=set)
    unanchored_rows: set[int] = field(default_factory=set)
    leftover: list[NumberToken] = field(default_factory=list)
    row_bands: dict[int, tuple[float, float]] = field(default_factory=dict)
    col_map: dict[int, int] = field(default_factory=dict)
    #: Non-None means this binding could not be produced at all -- a
    #: plumbing failure (e.g. the column bands could not be matched to the
    #: table's own columns), NOT a low score. Callers must treat a refused
    #: binding as "no measurement": no coverage comparison, no gap filling,
    #: no new withhold reasons. Silently scoring or acting on it would let a
    #: plumbing failure blank a table.
    refused: str | None = None


def _filter_lines(ocr_lines, header_bottom: float | None = None) -> list:
    """The OCR lines a row-anchoring pass may consider, in their original
    order: geometry present, text present, and (when a header bottom is
    known) not wholly above it -- the same 3.0pt jitter tolerance
    `_cluster_rows` uses. Shared by `_anchor_lines` and its callers so the
    indices `_anchor_lines` returns always refer to the SAME list."""
    lines = [l for l in (ocr_lines or []) if l.bbox is not None and l.text.strip()]
    if header_bottom is not None:
        lines = [l for l in lines if l.bbox[1] >= header_bottom - 3.0]
    return lines


#: A row label that already matches ONE printed line this well must never be
#: widened onto its neighbour -- that is the exact mechanism by which a band
#: would swallow the row below it.
_CLEAN_SINGLE_LINE_SCORE = 0.95


def _anchor_lines(table, lines: list) -> dict[int, tuple[int, int]]:
    """``row -> (first_line, last_line)`` indices into `lines`, by matching
    each row's printed LABEL against the OCR text.

    Consume-once on lines (a schedule printing "Additions" twice must not let
    both rows anchor to one line) and a monotonicity guard (an anchor whose Y
    is not >= the previous kept anchor's is dropped -- an unanchored row is
    safe, a row anchored to the wrong line is not).

    **Two-line anchors.** A caption that wraps onto a second printed line
    ("(a) Property, Plant and Equipment" / "[and Intangible assets]") scores
    about 0.71 against EITHER line alone -- below `_MIN_ROW_SCORE` -- so
    joining such a label without this would un-anchor the very row the join
    fixed. A row is therefore also scored against two vertically consecutive
    lines concatenated, accepted only when that pair BEATS the row's best
    single-line score **and** that best single-line score is below
    `_CLEAN_SINGLE_LINE_SCORE`. The second condition is the swallow guard: a
    row that already matches one printed line cleanly is never widened onto
    its neighbour. The window is two lines; widening it widens the swallow
    risk linearly and no three-line caption is in evidence on this corpus.
    """
    if not lines:
        return {}

    order = sorted(range(len(lines)), key=lambda i: (lines[i].bbox[1], lines[i].bbox[0]))
    norms = [_normalise_label(l.text) for l in lines]

    scored: list[tuple[float, int, tuple[int, int]]] = []
    for r in range(len(table.rows)):
        label = _normalise_label(table.label(r))
        if not label:
            continue
        best_single = 0.0
        for li, other in enumerate(norms):
            if not other:
                continue
            score = 1.0 if label == other else SequenceMatcher(None, label, other).ratio()
            best_single = max(best_single, score)
            if score >= _MIN_ROW_SCORE:
                scored.append((score, r, (li, li)))
        if best_single >= _CLEAN_SINGLE_LINE_SCORE:
            continue
        for k in range(len(order) - 1):
            a, b = order[k], order[k + 1]
            if not norms[a] or not norms[b]:
                continue
            pair = norms[a] + norms[b]
            score = 1.0 if label == pair else SequenceMatcher(None, label, pair).ratio()
            if score >= _MIN_ROW_SCORE and score > best_single:
                scored.append((score, r, (a, b)))

    scored.sort(key=lambda x: -x[0])
    row_lines: dict[int, tuple[int, int]] = {}
    used_lines: set[int] = set()
    for score, r, (a, b) in scored:
        if r in row_lines or a in used_lines or b in used_lines:
            continue
        row_lines[r] = (a, b)
        used_lines.update((a, b))

    ordered = sorted(row_lines.items(), key=lambda kv: lines[kv[1][0]].bbox[1])
    kept: dict[int, tuple[int, int]] = {}
    last_y = None
    for r, (a, b) in ordered:
        top = lines[a].bbox[1]
        if last_y is not None and top < last_y:
            continue  # refuse: printed order inverted
        kept[r] = (a, b)
        last_y = lines[b].bbox[1]
    return kept


def row_bands_from_labels(
    table, ocr_lines, header_bottom: float | None = None,
) -> dict[int, tuple[float, float]]:
    """Y-bands for a table's rows, anchored on where each row's own printed
    LABEL sits in the OCR text -- the same join `vlm_read.row_band` already
    makes for one row at a time, extended to every row at once so a cell's
    binding and a rescue's crop always agree on the same region.

    The anchoring itself lives in `_anchor_lines` (consume-once, monotonic,
    two-line aware). What this adds is the band geometry: extent padded by
    0.4 of the median line height, identical to `vlm_read.row_band`'s own
    padding, and **midpoint clipping** between consecutive anchors so two
    adjacent bands never overlap and a wrapped label's band cannot swallow
    the row below it.
    """
    lines = _filter_lines(ocr_lines, header_bottom)
    if not lines:
        return {}

    heights = [l.bbox[3] - l.bbox[1] for l in lines if l.bbox[3] > l.bbox[1]]
    pad = _median(heights) * 0.4 if heights else 6.0

    anchors = _anchor_lines(table, lines)
    kept: list[tuple[int, float, float]] = sorted(
        ((r, lines[a].bbox[1], lines[b].bbox[3]) for r, (a, b) in anchors.items()),
        key=lambda x: x[1],
    )

    bands: dict[int, tuple[float, float]] = {}
    for i, (r, top, bottom) in enumerate(kept):
        band_top = top - pad
        band_bottom = bottom + pad
        if i > 0:
            prev_bottom = kept[i - 1][2]
            mid = (prev_bottom + top) / 2.0
            band_top = max(band_top, mid)
        if i < len(kept) - 1:
            next_top = kept[i + 1][1]
            mid = (bottom + next_top) / 2.0
            band_bottom = min(band_bottom, mid)
        bands[r] = (band_top, band_bottom)
    return bands


#: A printed Schedule III enumerator at the start of a caption: "(a)", "(ii)",
#: "[b]", "1.", "(1)". It is the FILING'S OWN statement that a new line item
#: starts here, which is why it is the primary guard against joining two
#: sibling line items.
_ENUMERATOR_RE = re.compile(
    r"^\s*[\(\[]?\s*(?:[ivxlcIVXLC]+|[a-hA-H]|\d{1,2})\s*[\)\]\.]"
)
#: A caption fragment that opens mid-sentence: a bracket, or a lowercase
#: letter. Indian filings capitalise the first word of every line-item
#: caption, so a lowercase opening is strong evidence of continuation.
_CONTINUATION_OPEN_RE = re.compile(r"^\s*[\[\(]|^\s*[a-z]")


def is_continuation_fragment(
    lower_label: str, upper_label: str,
    lower_x: float | None = None, upper_x: float | None = None,
) -> bool:
    """Whether `lower_label` is the wrapped tail of the caption `upper_label`
    -- the shared test behind both the deterministic wrapped-label join and
    the relaxed VLM-corroborated merge.

    True only when ALL hold:

    - `upper_label` is non-empty;
    - `lower_label` does NOT start with an enumerator -- a printed enumerator
      is the filing declaring a new line item, so this is what keeps
      ``(b) Trade payables`` apart from ``(a) Borrowings``;
    - it opens with a bracket or a lowercase letter (``[and Intangible
      assets]`` passes; ``Other current liabilities`` fails);
    - it is printed at the same X or deeper than the caption it continues
      (3.0pt OCR-jitter tolerance, as at `_cluster_rows`) -- a continuation is
      never printed outbound of its own caption;
    - it is not a total line, whatever its capitalisation.

    **Without X positions this returns False, never "text-only True".**
    Left-edge X is available on every OCR line and was used by nothing until
    this; refusing when it is missing is the safe direction, and geometry is
    the whole reason this can decide anything.
    """
    from .tables import looks_like_total

    if lower_x is None or upper_x is None:
        return False
    upper = (upper_label or "").strip()
    lower = (lower_label or "").strip()
    if not upper or not lower:
        return False
    if _ENUMERATOR_RE.match(lower):
        return False
    if not _CONTINUATION_OPEN_RE.match(lower):
        return False
    if lower_x < upper_x - 3.0:
        return False
    if looks_like_total(lower):
        return False
    return True


# ---------------------------------------------------------------------------
# Label-only rows: headings, wrapped-caption tops, and line items that lost
# their figures. Row identity on a financial statement comes from WHERE a row
# sits on the page, so these are judged from label shape and printed geometry
# -- never from the VLM, and never by trusting the grid's own row breaks.
# ---------------------------------------------------------------------------

#: A category-level enumerator: bare digits or UPPERCASE roman ("(1)", "II.").
#: On a Schedule III face this is the section level -- "(1) Shareholders
#: funds", "II. Assets" -- which is a heading, not a line item.
_CATEGORY_ENUM_RE = re.compile(r"^\s*[\(\[]?\s*(?:\d{1,2}|[IVXLC]{1,4})\s*[\)\]\.]")
#: A leaf-level enumerator: lowercase letter or lowercase roman ("(a)",
#: "(ii)"). The line-item level.
_LEAF_ENUM_RE = re.compile(r"^\s*[\(\[]?\s*(?:[a-h]|[ivxlc]{1,4})\s*[\)\]]")


def _cell_blank(row: list[str], c: int) -> bool:
    return c >= len(row) or not (row[c] or "").strip()


def _value_cells_blank(table, r: int) -> bool:
    """No value column of this row holds anything at all -- not a figure and
    not a printed nil dash. A dash is the filing STATING "nothing here"; only
    a truly empty cell is silent."""
    row = table.rows[r]
    return all(_cell_blank(row, c) for c in table.value_cols)


def _only_label(table, r: int) -> bool:
    """A row whose every cell except its label is empty -- safe to fold into
    the row below without discarding a note reference or a figure."""
    row = table.rows[r]
    return all(_cell_blank(row, c) for c in range(len(row)) if c != table.label_col)


def _label_x(lines: list, anchors: dict[int, tuple[int, int]], r: int) -> float | None:
    """Left edge of a row's printed caption -- the first anchored line's X."""
    if r not in anchors:
        return None
    return lines[anchors[r][0]].bbox[0]


def _heading_rows(table, lines: list, anchors: dict[int, tuple[int, int]]) -> set[int]:
    """Label-only rows that are section HEADINGS rather than line items.

    First rule that fires wins. Rules 1-2 are what keep a `Total` and a row
    carrying a note reference from ever being called headings -- both are
    line items whose figures have gone missing, the highest-value catches in
    the set.

    1. a total line                          -> line item
    2. carries a note reference              -> line item
    3. ends with ":"                         -> heading
    4. category enumerator ("(1)", "II.")    -> heading
    5. leaf enumerator ("(a)", "(ii)")       -> line item
    6. ALL-CAPS, >= 2 letters                -> heading  ("EQUITY AND LIABILITIES")
    7. printed shallower than the line items -> heading  (geometric backstop for
       filings that print no enumerators at all)
    8. otherwise                             -> line item
    """
    from .tables import looks_like_total

    value_xs = sorted(
        x for r in range(len(table.rows))
        if not _value_cells_blank(table, r)
        for x in [_label_x(lines, anchors, r)] if x is not None
    )
    median_x = value_xs[len(value_xs) // 2] if value_xs else None

    headings: set[int] = set()
    for r in range(len(table.rows)):
        label = (table.label(r) or "").strip()
        if not label or not _value_cells_blank(table, r):
            continue
        if looks_like_total(label):
            continue
        if table.note_col is not None and not _cell_blank(table.rows[r], table.note_col):
            continue
        if label.endswith(":"):
            headings.add(r)
            continue
        if _CATEGORY_ENUM_RE.match(label):
            headings.add(r)
            continue
        if _LEAF_ENUM_RE.match(label):
            continue
        letters = [ch for ch in label if ch.isalpha()]
        if len(letters) >= 2 and all(ch.isupper() for ch in letters):
            headings.add(r)
            continue
        x = _label_x(lines, anchors, r)
        if x is not None and median_x is not None and x < median_x - 3.0:
            headings.add(r)
    return headings


def classify_label_only_rows(table, ct) -> tuple[set[int], set[tuple[int, int]]]:
    """``(headings, lost_figure_cells)`` for a table's label-only rows.

    A **heading** is normal and silent. A **lost-figure cell** is a value
    cell of a line item whose figure the grid dropped, and today it is
    invisible: `verify.py` skips any row whose value cells are all empty
    before computing a single flag, so ``EQUITY AND LIABILITIES`` and "a line
    item whose figures vanished" are indistinguishable -- silent data loss,
    the class the accuracy harness scores as MISSING.

    A cell is reported lost only on POSITIVE evidence, of two kinds:

    - OCR read a figure inside the row's own printed band, in THAT cell's own
      value column, and the grid holds nothing there. The evidence is per
      column, so the verdict is per CELL: a row printing one year's figure and
      leaving the other year genuinely blank reports only the first.
    - the row is a `Total` line with nothing at all against it -- a statement
      never prints a bare total -- in which case every value cell is reported.

    **Deliberately NOT reported: a line item with no figure anywhere.** A
    genuinely nil caption printed with blank cells and a caption whose figures
    OCR failed to read look exactly alike, and flagging the former
    ``[unreadable]`` would assert something was printed when nothing was.
    """
    from .tables import looks_like_total

    header_bottom = _header_bottom(ct.cells) if ct.cells else None
    lines = _filter_lines(ct.ocr_lines, header_bottom)
    anchors = _anchor_lines(table, lines) if lines else {}
    headings = _heading_rows(table, lines, anchors)

    col_bands = _column_ranges(ct.cells, len(table.header)) if ct.cells else {}
    col_map = _map_table_cols_to_bands(table, col_bands) if col_bands else None
    bands = row_bands_from_labels(table, ct.ocr_lines, header_bottom)
    ledger = build_number_ledger(ct.ocr_lines, header_bottom)
    if col_map:
        assign_columns(ledger, col_bands)
    band_to_col = {band: c for c, band in (col_map or {}).items() if c in table.value_cols}

    lost: set[tuple[int, int]] = set()
    for r in range(len(table.rows)):
        label = (table.label(r) or "").strip()
        if not label or r in headings or not _value_cells_blank(table, r):
            continue
        if looks_like_total(label):
            lost.update((r, c) for c in table.value_cols)
            continue
        if r in bands and band_to_col:
            y0, y1 = bands[r]
            for tok in ledger:
                c = band_to_col.get(tok.column)
                if c is not None and y0 <= (tok.bbox[1] + tok.bbox[3]) / 2.0 <= y1:
                    lost.add((r, c))
    return headings, lost


def join_wrapped_labels(table, ocr_lines, header_bottom: float | None = None):
    """Rejoin a caption that wrapped onto a second printed line with the row
    carrying its figures -- deterministically, from the page's own geometry.

    Same return contract as `vlm_read.merge_wrapped_labels`: the new indices
    of the merged rows, and an old -> new map (a merged pair's old indices
    both map to the same new index). Mutates ``table.rows`` in place.

    Where the second reader cannot help -- unreachable, or its read unusable
    -- this is the only thing standing between a wrapped caption and a row
    that looks empty. Row identity on a page is a Y band, not a label string.

    A pair ``(r, r+1)`` joins only when ALL hold:

    1. row r has nothing but a label and row r+1 has content in a value
       column -- **direction-locked**: captions wrap downward, so an upper
       row with figures is a different defect, left to the VLM path;
    2. both rows anchor to printed lines (no anchor means no X, so refuse);
    3. `is_continuation_fragment` passes -- no enumerator, lowercase or
       bracket opening, printed no shallower than the caption above, not a
       total;
    4. the two printed lines are vertically adjacent (a gap wider than one
       line height means a blank row or a rule sits between them, which a
       wrapped caption never has);
    5. the upper row is not a section heading -- a heading must never absorb
       the line item beneath it.

    The merged row is the VALUE row's cells verbatim with the two labels
    joined in printed order. No figure is created, moved or recomputed.
    """
    n = len(table.rows)
    remap = {r: r for r in range(n)}
    if n < 2 or not table.value_cols:
        return [], remap
    lines = _filter_lines(ocr_lines, header_bottom)
    if not lines:
        return [], remap

    anchors = _anchor_lines(table, lines)
    headings = _heading_rows(table, lines, anchors)
    heights = [l.bbox[3] - l.bbox[1] for l in lines if l.bbox[3] > l.bbox[1]]
    line_height = _median(heights) or 12.0

    merged_new: list[int] = []
    new_rows: list[list[str]] = []
    r = 0
    while r < n:
        join = False
        if (
            r + 1 < n
            and _only_label(table, r) and (table.label(r) or "").strip()
            and not _value_cells_blank(table, r + 1)
            and r in anchors and (r + 1) in anchors
            and r not in headings
        ):
            upper_last = lines[anchors[r][1]]
            lower_first = lines[anchors[r + 1][0]]
            adjacent = (lower_first.bbox[1] - upper_last.bbox[3]) <= line_height
            join = adjacent and is_continuation_fragment(
                table.label(r + 1), table.label(r),
                _label_x(lines, anchors, r + 1), _label_x(lines, anchors, r),
            )
        if join:
            merged_row = list(table.rows[r + 1])
            while len(merged_row) <= table.label_col:
                merged_row.append("")
            merged_row[table.label_col] = f"{table.label(r)} {table.label(r + 1)}".strip()
            idx = len(new_rows)
            new_rows.append(merged_row)
            merged_new.append(idx)
            remap[r] = idx
            remap[r + 1] = idx
            r += 2
        else:
            remap[r] = len(new_rows)
            new_rows.append(table.rows[r])
            r += 1

    table.rows = new_rows
    return merged_new, remap


def _map_table_cols_to_bands(
    table, col_bands: dict[int, tuple[float, float]],
) -> dict[int, int] | None:
    """Map a candidate `Table`'s column indices onto TableFormer's column
    X-band indices -- two different index spaces nothing else in the
    codebase translates between, because nothing else needs both at once.

    Resolved strictly by left-to-right ORDER, never by count matching
    alone: on a Schedule III layout the label (and an optional Note) sit on
    the left and every money column sits on the right, so when there are
    more bands than the table has value columns, the table's value columns
    are mapped to the RIGHTMOST bands -- the right edge is the stable
    anchor, not the left, because label/Note columns never bind a number
    and are deliberately left unmapped.

    Returns ``None`` -- a refusal, not a zero -- when the band count is too
    small to cover the table's own value columns at all.
    """
    if not col_bands:
        return None
    bands_sorted = sorted(col_bands, key=lambda k: col_bands[k][0])
    value_cols = sorted(table.value_cols)
    if not value_cols:
        return None

    width = max([len(table.header)] + [len(r) for r in table.rows])
    if len(bands_sorted) == width:
        return {c: bands_sorted[c] for c in value_cols if c < len(bands_sorted)}

    if len(bands_sorted) >= len(value_cols):
        rightmost = bands_sorted[-len(value_cols):]
        return dict(zip(value_cols, rightmost))

    return None


#: A bare 4-digit year, 1900-2099. Deliberately not anchored to a keyword
#: ("March", "as at", "year ended") -- headers are short and already
#: date-shaped by construction (this is only ever run over VALUE-column
#: headers, never body text), so the extra keyword requirement would only
#: add missed cases, not correctness.
_YEAR_TOKEN_RE = re.compile(r"(19|20)\d{2}")


def _header_year(text: str | None) -> int | None:
    """The year printed in one header cell, or `None` if it can't be read
    with confidence.

    Refuses (returns `None`) rather than guesses when a cell carries ZERO
    or MULTIPLE year tokens -- a header with two 4-digit numbers (a year
    plus, say, a stray note/schedule reference) is exactly the kind of
    ambiguity this module refuses elsewhere rather than picking one.
    """
    if not text:
        return None
    matches = list(_YEAR_TOKEN_RE.finditer(text))
    if len(matches) != 1:
        return None
    return int(matches[0].group())


def detect_year_column_order(table) -> str | None:
    """A warning string if `table`'s value-column headers carry two
    DIFFERENT years in ASCENDING left-to-right order.

    Current year before previous year (i.e. DESCENDING left to right) is
    the printed convention this entire corpus follows -- every table this
    session's real documents produced prints the current year's column
    first (e.g. "31st March, 2024" then "31st March, 2023"). Ascending
    order the wrong way round is the exact signature of a current/
    previous-year column swap -- the precise failure mode that produced 18
    WRONG figures when a vision model was tried as the sole table reader
    this session (a real, measured column transposition on otherwise
    correctly-shaped tables, not a hypothetical case this function guards
    against speculatively).

    Deliberately a SIGNAL, not a fix: this never reorders anything itself,
    matching every other refuse-on-ambiguity function in this module --
    the caller decides what to do with a flagged table (redact, note,
    both). Returns `None` for the ordinary case of a table whose headers
    carry no positively-identified year at all ("Amount", "Rs." printed
    twice with no date) -- that is not evidence of anything, and this must
    be a no-op for it, not a forced opinion.
    """
    years = [_header_year(table.column_name(c)) for c in sorted(table.value_cols)]
    known = [(i, y) for i, y in enumerate(years) if y is not None]
    if len(known) < 2:
        return None

    for (i1, y1), (i2, y2) in zip(known, known[1:]):
        if y1 == y2:
            continue  # the same year printed twice (e.g. a restated column) -- not a swap signal
        if y1 < y2:
            return (
                f"value columns {i1} and {i2} print years {y1} then {y2}, left to right "
                f"(ascending) -- this corpus's own convention is current year before "
                f"previous year (descending), so this table's year columns may be swapped"
            )
    return None


def _norm_header(text: str | None) -> str:
    return (text or "").strip().casefold()


def detect_cross_page_continuation(prev_table, next_table) -> str | None:
    """A warning string if `next_table` looks like it holds the rows that
    spilled off the BOTTOM of `prev_table`, printed on the very next page.

    Two signals, both required, because either alone is common and means
    nothing on its own:

    1. `prev_table` doesn't end in a `Total`/`Grand Total` line -- a table
       that closed cleanly on its own page is not missing anything, however
       similar the next page's table looks.
    2. `next_table` carries the exact same value-column headers as
       `prev_table` AND has no title of its own -- a genuinely new
       statement almost always prints its own caption and its own header
       row; a spillover, by construction, has neither, since it is just
       the tail of the table above continuing under a page break.

    Deliberately a SIGNAL, not a merge: this never joins the two tables or
    moves a single figure between them, matching `detect_year_column_order`
    -- both tables are still reported exactly as extracted. A caller relying
    on either table's own printed "Total" should know that total may not be
    the true one if this fires; the real total could be sitting on the next
    page's table instead. Returns `None` whenever the page numbers aren't
    exactly adjacent, either table is empty, or the header shapes don't
    match -- this must stay a no-op for two unrelated tables that merely
    happen to sit on consecutive pages.
    """
    from .tables import looks_like_total

    if next_table.page_no != prev_table.page_no + 1:
        return None
    if not prev_table.rows or not next_table.rows:
        return None
    if looks_like_total(prev_table.label(len(prev_table.rows) - 1)):
        return None
    if next_table.title:
        return None

    prev_headers = [_norm_header(prev_table.column_name(c)) for c in sorted(prev_table.value_cols)]
    next_headers = [_norm_header(next_table.column_name(c)) for c in sorted(next_table.value_cols)]
    if not prev_headers or prev_headers != next_headers:
        return None

    return (
        f"table on page {next_table.page_no} has no title of its own and repeats the exact "
        f"same value-column headers as the table on page {prev_table.page_no}, whose last row "
        f"('{prev_table.label(len(prev_table.rows) - 1).strip()}') is not a Total line -- it "
        f"may be the continuation of that table, split by the page break"
    )


def bind_table(
    table, ledger: list[NumberToken], col_bands: dict[int, tuple[float, float]],
    ocr_lines, header_bottom: float | None = None,
) -> Binding:
    """Bind a candidate `Table`'s value cells against a number ledger.

    A cell binds when an unconsumed token exists whose assigned COLUMN
    matches the cell's own column (via `col_map`), whose Y-centre falls
    inside that ROW's band (via `row_bands_from_labels`), and whose value
    agrees with the cell's own parsed value within the same tolerance
    `vlm_read.check_rescue_anchors` uses elsewhere in this pipeline.

    A cell holding a printed dash/nil (`cell.value is None`) is skipped
    entirely, neither bound nor unbound -- a dash is a positive statement
    of "no figure here", not a claim that needs backing, and treating it as
    one would exhaust the targeted-re-read budget on a dash-heavy statement
    for no reason.

    When several unconsumed tokens in the same row/column band carry the
    SAME value, the leftmost is taken -- they are interchangeable, and
    refusing here would penalise a table for a coincidence it has no
    control over, unlike every other refusal in this module which exists
    because a real ambiguity could hide a real error.
    """
    col_map = _map_table_cols_to_bands(table, col_bands)
    if col_map is None:
        return Binding(refused="the table's columns could not be matched to TableFormer's column bands")

    assign_columns(ledger, col_bands)
    row_bands = row_bands_from_labels(table, ocr_lines, header_bottom)
    unanchored_rows = set(range(len(table.rows))) - set(row_bands)

    bound: dict[tuple[int, int], NumberToken] = {}
    unbound: set[tuple[int, int]] = set()
    consumed: set[int] = set()

    for r, (y0, y1) in row_bands.items():
        for c in table.value_cols:
            cell = table.cell(r, c)
            if cell.value is None:
                continue
            band_key = col_map.get(c)
            if band_key is None:
                unbound.add((r, c))
                continue
            tol = _VALUE_MATCH_TOLERANCE(cell.value)
            candidates = [
                i for i, tok in enumerate(ledger)
                if i not in consumed
                and tok.column == band_key
                and y0 <= (tok.bbox[1] + tok.bbox[3]) / 2.0 <= y1
                and abs(tok.value - cell.value) <= tol
            ]
            if not candidates:
                unbound.add((r, c))
                continue
            chosen = min(candidates, key=lambda i: ledger[i].bbox[0])
            consumed.add(chosen)
            bound[(r, c)] = ledger[chosen]

    for r in unanchored_rows:
        for c in table.value_cols:
            if table.cell(r, c).value is not None:
                unbound.add((r, c))

    leftover = [tok for i, tok in enumerate(ledger) if i not in consumed]
    return Binding(
        bound=bound, unbound=unbound, unanchored_rows=unanchored_rows,
        leftover=leftover, row_bands=row_bands, col_map=col_map,
    )


def coverage_score(
    binding: Binding, note_col: int | None = None,
) -> int:
    """How much of what the page actually prints this binding accounts for.

    ``len(bound) - len(unbound) - len(penalised leftover)``. Unlike a raw
    footing count (measured on a real document to reward a fragmented,
    duplicate-ridden table for having more stray values to coincidentally
    pair up -- see `vlm_read.select_structure`'s module note), this cannot
    be gamed the same way: consume-once means a duplicated figure can only
    ever back ONE cell, a misplaced figure COSTS the structure that placed
    it wrong rather than being neutral, and a figure the structure never
    accounts for at all -- a half-read table, the exact defect this design
    targets -- counts against it too. Both candidates are always scored
    against the SAME ledger, so raw integers compare directly; there is
    nothing here to normalise or tune.

    The leftover penalty excludes: tokens in the note-reference column (a
    note number is not a figure); tokens whose Y sits outside every row
    band's vertical extent (not part of this table's row structure at all,
    e.g. a page number); interpolated tokens and tokens below
    `_MIN_GAP_FILL_CONFIDENCE` (an uncertain reading of a leftover's
    POSITION should not count against a structure that never claimed it --
    penalising a guess is not evidence).
    """
    if not binding.row_bands:
        extent = None
    else:
        extent = (
            min(b[0] for b in binding.row_bands.values()),
            max(b[1] for b in binding.row_bands.values()),
        )

    value_bands = set(binding.col_map.values())
    penalised = 0
    for tok in binding.leftover:
        if tok.column is None or tok.column not in value_bands:
            continue
        if note_col is not None and tok.column == binding.col_map.get(note_col):
            continue
        if extent is not None:
            yc = (tok.bbox[1] + tok.bbox[3]) / 2.0
            if not (extent[0] <= yc <= extent[1]):
                continue
        if tok.interpolated:
            continue
        if tok.confidence is not None and tok.confidence < _MIN_GAP_FILL_CONFIDENCE:
            continue
        penalised += 1

    return len(binding.bound) - len(binding.unbound) - penalised


def propose_gap_fills(
    table, binding: Binding,
) -> list[tuple[int, int, "NumberToken"]]:
    """Propose placing a leftover token into an EMPTY cell -- PURE: nothing is
    written. A proposal is only ever a question to a second reader; see
    `vlm_read.confirm_gap_fill`. Nothing may be placed on position alone.

    Proposed only when EVERY one of these holds, each a deliberate refusal
    boundary, not a convenience:

    - the token's column maps to exactly one of the table's value columns;
    - its Y-centre sits inside exactly one row band, at least 3pt from the
      nearest edge of any OTHER band (the same jitter tolerance used
      elsewhere in this module) -- ambiguous between two rows is refused,
      not guessed;
    - **the token is NOT interpolated** -- an approximate position is never
      even worth asking about;
    - its OCR-line confidence is None or >= `_MIN_GAP_FILL_CONFIDENCE`;
    - the target cell is empty or whitespace-only -- NEVER a populated
      cell, and never a printed dash/nil, because a dash is a positive
      statement that no figure belongs there and overwriting it with a stray
      one is exactly the failure mode this whole design exists to prevent;
    - the target cell's existing text is not already a marker
      (`models.is_marker`);
    - no OTHER surviving leftover targets the same cell -- a collision
      between two candidates is resolved by proposing NEITHER.
    """
    from . import models

    row_bands = binding.row_bands
    col_map = binding.col_map
    value_col_for_band = {band: col for col, band in col_map.items()}

    targets: dict[tuple[int, int], list[NumberToken]] = {}
    for tok in binding.leftover:
        if tok.interpolated:
            continue
        if tok.confidence is not None and tok.confidence < _MIN_GAP_FILL_CONFIDENCE:
            continue
        if tok.column is None:
            continue
        c = value_col_for_band.get(tok.column)
        if c is None:
            continue

        yc = (tok.bbox[1] + tok.bbox[3]) / 2.0
        matching_rows = [
            r for r, (y0, y1) in row_bands.items() if y0 <= yc <= y1
        ]
        if len(matching_rows) != 1:
            continue
        r = matching_rows[0]
        others = [
            (y0, y1) for rr, (y0, y1) in row_bands.items() if rr != r
        ]
        if any(min(abs(yc - y0), abs(yc - y1)) < 3.0 for y0, y1 in others):
            continue

        existing = table.rows[r][c] if c < len(table.rows[r]) else ""
        if existing.strip():
            continue
        if models.is_marker(existing):
            continue

        targets.setdefault((r, c), []).append(tok)

    return [
        (r, c, toks[0]) for (r, c), toks in targets.items() if len(toks) == 1
    ]


def apply_gap_fills(
    table, fills: list[tuple[int, int, "NumberToken"]],
) -> list[tuple[int, int, "NumberToken"]]:
    """Write CONFIRMED gap fills into the table, in place; return what was
    actually placed.

    Never inserts, removes or reorders a row, so callers' row-index
    bookkeeping is undisturbed. **Re-asserts the emptiness of every target
    immediately before writing**: a proposal is made, then confirmed over the
    network, and something else may have placed a figure there in between. A
    stale proposal must never overwrite it.
    """
    from . import models

    placed: list[tuple[int, int, NumberToken]] = []
    for r, c, tok in fills:
        if r >= len(table.rows):
            continue
        row = table.rows[r]
        existing = row[c] if c < len(row) else ""
        if existing.strip() or models.is_marker(existing):
            continue
        if len(row) <= c:
            row = row + [""] * (c + 1 - len(row))
            table.rows[r] = row
        table.rows[r][c] = tok.text
        placed.append((r, c, tok))
    return placed


def _cluster_rows(ocr_lines, ranges: dict[int, tuple[float, float]],
                  header_count: int, header: list[str] | None = None,
                  header_bottom: float | None = None) -> list[list[str]] | None:
    """Every OCR line in the table, reassembled into rows by Y-position alone.

    A new row starts whenever a line's top sits more than half a text-line
    height below the current row's own top -- half rather than a full line
    because two columns whose lines are printed a point or two apart (common
    OCR jitter) must still land in the SAME row, while two genuinely
    different printed lines (typically 15-20pt apart on these documents) must
    not be merged into one.
    """
    lines = [l for l in ocr_lines if l.bbox is not None and l.text.strip()]
    if header_bottom is not None:
        # A couple of points of tolerance for OCR bbox jitter at the
        # boundary -- real gaps between the header and the first body row
        # are tens of points, so this cannot swallow a genuine row.
        lines = [l for l in lines if l.bbox[1] >= header_bottom - 3.0]
    if len(lines) < 3:
        return None
    lines.sort(key=lambda l: (l.bbox[1], l.bbox[0]))

    heights = [l.bbox[3] - l.bbox[1] for l in lines if l.bbox[3] > l.bbox[1]]
    median_height = _median(heights) or 12.0
    threshold = median_height * 0.6

    bands: list[list] = []
    band_top = None
    for line in lines:
        y0 = line.bbox[1]
        if band_top is None or y0 - band_top > threshold:
            bands.append([line])
            band_top = y0
        else:
            bands[-1].append(line)

    header_signature = _normalise_for_dedup(header) if header else None

    rows: list[list[str]] = []
    for band in bands:
        row = [""] * header_count
        by_col: dict[int, list[str]] = {}
        for line in band:
            x_center = (line.bbox[0] + line.bbox[2]) / 2.0
            col = _nearest_column(x_center, ranges)
            if col is None:
                continue
            by_col.setdefault(col, []).append(line.text.strip())
        for col, texts in by_col.items():
            row[col] = " ".join(t for t in texts if t)
        if not any(c.strip() for c in row):
            continue
        # The table's OWN header is read by OCR too -- it sits inside the same
        # table bbox `get_cells_in_bbox` was queried against -- and with no row
        # concept at all, a band built purely from Y-position cannot tell "the
        # header, read again" from "a real body row that happens to repeat the
        # column titles". Real docling output on MH-CPSU-ITSL-048 2024-25
        # reproduced the header verbatim as a spurious first row here; dropped
        # by exact match against the header this function was already given,
        # rather than guessed at with a Y-position threshold.
        if header_signature is not None and _normalise_for_dedup(row) == header_signature:
            continue
        rows.append(row)
    return rows if len(rows) >= 3 else None


def _split_header_row(markdown: str) -> tuple[list[str], int] | None:
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            from .tables import split_row
            cells = split_row(stripped)
            return cells, len(cells)
    return None


def _rows_to_markdown(header: list[str], rows: list[list[str]]) -> str:
    def pad(cells: list[str]) -> str:
        cells = list(cells) + [""] * (len(header) - len(cells))
        return "| " + " | ".join(cells) + " |"

    out = [pad(header), "| " + " | ".join(["---"] * len(header)) + " |"]
    out.extend(pad(r) for r in rows)
    return "\n".join(out)


def _coverage_of(
    markdown: str, ledger: list, col_bands: dict[int, tuple[float, float]],
    ocr_lines, header_bottom: float | None,
) -> tuple[int, int] | None:
    """``(coverage_score, bound_count)`` for one candidate markdown, or
    ``None`` on ANY refusal -- unparseable, no table, or a binding that could
    not be established. ``None`` means "no measurement", never "zero": the
    caller must then leave the table alone rather than treat a plumbing
    failure as a poor score."""
    from .tables import parse_markdown_tables

    try:
        parsed = parse_markdown_tables(markdown, 1, prefix="x")
    except Exception:
        return None
    if not parsed:
        return None
    table = parsed[0]
    binding = bind_table(table, ledger, col_bands, ocr_lines, header_bottom)
    if binding.refused:
        return None
    return coverage_score(binding, table.note_col), len(binding.bound)


def repair_from_geometry(ct):
    """Return ``ct``, or a copy whose markdown was rebuilt from OCR geometry.

    Never mutates the input. Returns it completely unchanged whenever geometry
    is missing, too sparse to cluster, either structure cannot be bound
    against the page's own OCR numbers, or the rebuild does not account for
    strictly more of the printed figures than the original -- silence and a
    return of the original table are always the safe default here.

    **The comparator is OCR coverage, not footing strength.** It used to be
    footing strength, and that was measured on a real Cash Flow statement to
    prefer the BROKEN table: a fragmented, duplicate-ridden grid scored 9
    footings against a correct rebuild's 6 (7 vs 4 even with scattered
    components filtered out), because fragmentation manufactures stray values
    that coincidentally sum. Coverage (`coverage_score`) counts each printed
    figure once, so duplication is worthless, charges a misplaced figure to
    the structure that misplaced it, and counts an unaccounted-for figure
    against the structure that omitted it -- 30 vs -34 on that same table.
    """
    if not Config.GEOMETRY_REPAIR_COVERAGE_GATE:
        return ct
    if not ct.ocr_lines or not ct.cells:
        return ct

    header_info = _split_header_row(ct.markdown)
    if header_info is None:
        return ct
    header, header_count = header_info
    if header_count < 2:
        return ct

    ranges = _column_ranges(ct.cells, header_count)
    if len(ranges) < 2:
        return ct

    header_bottom = _header_bottom(ct.cells)
    rows = _cluster_rows(ct.ocr_lines, ranges, header_count,
                         header=header, header_bottom=header_bottom)
    if rows is None:
        return ct

    candidate_markdown = _rows_to_markdown(header, rows)

    # Both structures are scored against the SAME ledger, built once: the
    # binder assigns each token its column band in place, and a ledger shared
    # by both scorings is what makes the two integers directly comparable.
    ledger = build_number_ledger(ct.ocr_lines, header_bottom)
    original = _coverage_of(ct.markdown, ledger, ranges, ct.ocr_lines, header_bottom)
    candidate = _coverage_of(candidate_markdown, ledger, ranges, ct.ocr_lines, header_bottom)
    if original is None or candidate is None:
        return ct
    # Strictly greater coverage (the status-quo bias this gate has always
    # had), AND the rebuild must not lose a figure the original had already
    # bound. Coverage is a NET score: a rebuild that binds three fewer
    # figures but leaves five fewer leftovers scores higher while actually
    # dropping data.
    if not (candidate[0] > original[0] and candidate[1] >= original[1]):
        return ct

    logger.info(
        "table on page %s: row grid rebuilt from OCR geometry "
        "(coverage %d -> %d, bound %d -> %d)",
        ct.page_no, original[0], candidate[0], original[1], candidate[1],
    )
    return replace(ct, markdown=candidate_markdown)


# ---------------------------------------------------------------------------
# A table layout analysis never found
# ---------------------------------------------------------------------------
#
# Everything above REPAIRS a table docling already located. This section covers
# the case where docling's layout model produced no `table` cluster at all --
# measured on a real filing (Startup Odisha, "Statement of Income &
# Expenditure", almost no ruling lines): 55 layout clusters, none a table, every
# cell its own tiny low-confidence `text` cluster. OCR read every figure
# correctly; nothing ever assembled them, and `emit.build_text_records` then
# discarded each one for being under 25 characters -- a silent, total loss.
#
# The rebuild below works from docling's own text items (label + position) and
# emits ordinary pipe markdown that flows through the SAME verification as any
# detected table (footing checks, withholding, the vision second read). It
# refuses rather than guesses: it only fires when several figures share a
# right-aligned column AND most of them sit on a row with a label.

#: Fewest right-aligned figures that make a value column, and fewest labelled
#: figure rows that make a table. Below either, prose with a few numbers in it
#: would qualify.
_MIN_COLUMN_FIGURES = 3
_MIN_LABELLED_ROWS = 3
#: Figures whose RIGHT edges sit within this many points share a column.
#: Right-aligned money columns on this corpus agree to a few points.
_RIGHT_EDGE_TOL_PT = 12.0
#: Share of a table's figures that must land on a labelled row.
_MIN_BOUND_FRACTION = 0.8
#: Two fragments are on one printed line when their vertical spans overlap by
#: at least this fraction of the shorter one.
_SAME_LINE_OVERLAP = 0.4
#: Docling labels whose text is never a table's label or figure.
_NOT_TABLE_TEXT = frozenset({"page_footer", "page_header", "picture"})

_FIGURE_RE = re.compile(
    r"^\s*[\(\-]?\s*(?:₹|rs\.?)?\s*\d[\d,]*(?:\.\d+)?\s*\)?\s*$", re.I
)


@dataclass
class TextFragment:
    """One docling text item, positioned in page points, TOP-LEFT origin."""

    text: str
    bbox: tuple[float, float, float, float]  # (l, t, r, b)
    label: str = "text"


@dataclass
class SynthesizedTable:
    markdown: str
    bbox: tuple[float, float, float, float]  # (l, t, r, b), TOP-LEFT origin
    title: str | None
    figures: int


def _is_figure(text: str) -> bool:
    return bool(_FIGURE_RE.match(text or "")) and parse_cell(text).value is not None


def _inside(box, outer) -> bool:
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _v_overlap(a, b) -> float:
    lo, hi = max(a[1], b[1]), min(a[3], b[3])
    shorter = min(a[3] - a[1], b[3] - b[1])
    return (hi - lo) / shorter if shorter > 0 and hi > lo else 0.0


def orphan_figures(fragments, table_boxes, picture_boxes) -> list[TextFragment]:
    """Standalone figures on a page that sit in NO detected table.

    Excludes page furniture and anything inside a picture region (stamps,
    signatures), so a page number or a seal cannot count as a lost figure.
    """
    out = []
    for f in fragments:
        if f.label in _NOT_TABLE_TEXT or not _is_figure(f.text):
            continue
        if any(_inside(f.bbox, b) for b in table_boxes):
            continue
        if any(_inside(f.bbox, b) for b in picture_boxes):
            continue
        out.append(f)
    return out


def _value_columns(figures) -> list[list[TextFragment]]:
    """Groups of >= _MIN_COLUMN_FIGURES figures sharing a right edge, left to
    right."""
    ordered = sorted(figures, key=lambda f: f.bbox[2])
    groups: list[list[TextFragment]] = []
    for f in ordered:
        if groups and f.bbox[2] - groups[-1][-1].bbox[2] <= _RIGHT_EDGE_TOL_PT:
            groups[-1].append(f)
        else:
            groups.append([f])
    return [g for g in groups if len(g) >= _MIN_COLUMN_FIGURES]


def synthesize_table(fragments, orphans, table_boxes) -> "SynthesizedTable | None":
    """Assemble a table from a page's text items, or ``None`` on any doubt."""
    columns = _value_columns(orphans)
    if not columns:
        return None

    figures = [f for col in columns for f in col]
    heights = [f.bbox[3] - f.bbox[1] for f in figures if f.bbox[3] > f.bbox[1]]
    median_h = _median(heights) or 12.0

    bands_lr = [(min(f.bbox[0] for f in col), max(f.bbox[2] for f in col)) for col in columns]
    left_of_values = min(lo for lo, _ in bands_lr)
    first_top = min(f.bbox[1] for f in figures)
    last_bottom = max(f.bbox[3] for f in figures)
    region_bottom = last_bottom + median_h * 0.5

    figure_ids = {id(f) for f in figures}
    prose = [
        f for f in fragments
        if id(f) not in figure_ids and f.label not in _NOT_TABLE_TEXT
        and not _is_figure(f.text) and f.text.strip()
    ]

    # ---- header: the nearest printed line above the first figure ---------
    def over_column(f, pad=20.0):
        return any(f.bbox[0] <= hi + pad and f.bbox[2] >= lo - pad for lo, hi in bands_lr)

    above = [f for f in prose if f.bbox[3] <= first_top + median_h * 0.25 and over_column(f)]
    header_anchor = max(above, key=lambda f: f.bbox[3], default=None)
    if header_anchor is None:
        return None
    header_line = [
        f for f in prose
        if f.bbox[3] <= first_top + median_h * 0.25
        and _v_overlap(f.bbox, header_anchor.bbox) >= _SAME_LINE_OVERLAP
    ]
    header_bottom = max(f.bbox[3] for f in header_line)
    header_top = min(f.bbox[1] for f in header_line)

    value_headers = []
    for i, (lo, hi) in enumerate(bands_lr):
        cands = [f for f in header_line if f.bbox[0] <= hi + 20.0 and f.bbox[2] >= lo - 20.0]
        value_headers.append(
            " ".join(f.text.strip() for f in sorted(cands, key=lambda f: f.bbox[0]))
            or f"Column {i + 1}"
        )
    label_header = " ".join(
        f.text.strip() for f in sorted(header_line, key=lambda f: f.bbox[0])
        if f.bbox[2] < left_of_values - 20.0
    ) or "Particulars"

    # ---- rows: labels and figures between the header and the last figure --
    labels = [
        f for f in prose
        if f.bbox[1] >= header_bottom - 1.0 and f.bbox[3] <= region_bottom
        and f.bbox[0] < left_of_values - 20.0
    ]
    if not labels:
        return None

    members = sorted(labels + figures, key=lambda f: (f.bbox[1], f.bbox[0]))
    bands: list[list[TextFragment]] = []
    span = None
    for f in members:
        if span is not None and _v_overlap(f.bbox, span) >= _SAME_LINE_OVERLAP:
            bands[-1].append(f)
            span = (0, min(span[1], f.bbox[1]), 0, max(span[3], f.bbox[3]))
        else:
            bands.append([f])
            span = (0, f.bbox[1], 0, f.bbox[3])

    rows, bound, labelled_figure_rows = [], 0, 0
    for band in bands:
        label_parts = sorted((f for f in band if id(f) not in figure_ids), key=lambda f: f.bbox[0])
        row_label = " ".join(f.text.strip() for f in label_parts).replace("|", "/")
        cells = [""] * len(columns)
        placed = 0
        for f in band:
            if id(f) not in figure_ids:
                continue
            for i, col in enumerate(columns):
                if any(f is c for c in col):
                    cells[i] = (cells[i] + " " + f.text.strip()).strip()
                    placed += 1
        if placed and row_label:
            bound += placed
            labelled_figure_rows += 1
        if row_label or placed:
            rows.append([row_label, *cells])

    if labelled_figure_rows < _MIN_LABELLED_ROWS or bound / len(figures) < _MIN_BOUND_FRACTION:
        return None

    used = [f for band in bands for f in band]
    left = min(f.bbox[0] for f in used + header_line)
    right = max(f.bbox[2] for f in used + header_line)
    top, bottom = header_top, max(f.bbox[3] for f in used)
    if any(not (b[2] < left or b[0] > right or b[3] < top or b[1] > bottom) for b in table_boxes):
        return None

    titles = [
        f for f in fragments
        if f.label == "section_header" and f.bbox[3] <= top and top - f.bbox[3] <= 150.0
    ]
    title = max(titles, key=lambda f: f.bbox[3], default=None)

    header = [label_header, *value_headers]
    return SynthesizedTable(
        markdown=_rows_to_markdown(header, rows),
        bbox=(left, top, right, bottom),
        title=title.text.strip() if title else None,
        figures=len(figures),
    )


def synthesize_continuation(
    fragments, orphans, markdown, ocr_lines, table_bbox,
) -> "tuple[SynthesizedTable, list[TextFragment]] | None":
    """Rows printed BELOW a detected table that its box stopped short of.

    Measured on a real balance sheet (Startup Odisha, page 1): docling's table
    box ended at the liabilities subtotal, so the liabilities TOTAL, the whole
    ASSETS section and the closing TOTAL sat outside it -- read by OCR, in the
    same columns, and dropped. Those figures already have a header and column
    layout: the table above. So instead of demanding a header of their own
    (`synthesize_table` does), this borrows the detected table's, matching each
    figure column to a detected column by the FIGURES THEMSELVES -- the OCR
    tokens the detected table holds at the same right edge -- never by guess.

    Returns the rebuilt rows as their own table plus the fragments it
    consumed, or ``None`` on any doubt (the caller then reports the figures).
    """
    from .tables import parse_markdown_tables

    parsed = parse_markdown_tables(markdown or "", 1, prefix="c")
    if not parsed:
        return None
    table = parsed[0]

    # Anchors: right edges where the detected table's own OCR figures line up.
    tokens = build_number_ledger(ocr_lines)
    tokens.sort(key=lambda t: t.bbox[2])
    anchors: list[list] = []
    for tok in tokens:
        if anchors and tok.bbox[2] - anchors[-1][-1].bbox[2] <= _RIGHT_EDGE_TOL_PT:
            anchors[-1].append(tok)
        else:
            anchors.append([tok])
    anchors = [a for a in anchors if len(a) >= 2]
    if not anchors:
        return None

    def column_of(anchor) -> int | None:
        texts = {t.text.strip() for t in anchor}
        best, best_hits = None, 0
        for c in range(len(table.header)):
            hits = sum(1 for r in table.rows if c < len(r) and r[c].strip() in texts)
            if hits > best_hits:
                best, best_hits = c, hits
        return best if best_hits >= max(2, len(anchor) // 2) else None

    mapped = []  # (anchor right edge, table column)
    for a in anchors:
        col = column_of(a)
        if col is not None and col != table.label_col:
            mapped.append((sum(t.bbox[2] for t in a) / len(a), col))
    if not mapped:
        return None
    if len({col for _, col in mapped}) != len(mapped):
        return None  # two anchors claim one column: ambiguous, refuse

    table_bottom = table_bbox[3]
    below = [f for f in orphans if f.bbox[1] >= table_bottom - 2.0]
    grouped: dict[int, list[TextFragment]] = {}
    for f in below:
        for edge, col in mapped:
            if abs(f.bbox[2] - edge) <= _RIGHT_EDGE_TOL_PT:
                grouped.setdefault(col, []).append(f)
                break
    figures = [f for col in sorted(grouped) for f in grouped[col]]
    if not figures:
        return None
    heights = [f.bbox[3] - f.bbox[1] for f in figures if f.bbox[3] > f.bbox[1]]
    median_h = _median(heights) or 12.0
    if min(f.bbox[1] for f in figures) - table_bottom > median_h * 3.0:
        return None  # not a continuation: a gap of several lines separates them

    columns = sorted(grouped)
    left_of_values = min(
        min(f.bbox[0] for f in grouped[c]) for c in columns
    )
    last_bottom = max(f.bbox[3] for f in figures)
    figure_ids = {id(f) for f in figures}
    labels = [
        f for f in fragments
        if id(f) not in figure_ids and f.label not in _NOT_TABLE_TEXT and f.text.strip()
        and f.bbox[1] >= table_bottom - 2.0 and f.bbox[3] <= last_bottom + median_h * 0.5
        and f.bbox[0] < left_of_values - 20.0
    ]
    if not labels:
        return None

    members = sorted(labels + figures, key=lambda f: (f.bbox[1], f.bbox[0]))
    bands: list[list[TextFragment]] = []
    span = None
    for f in members:
        if span is not None and _v_overlap(f.bbox, span) >= _SAME_LINE_OVERLAP:
            bands[-1].append(f)
            span = (0, min(span[1], f.bbox[1]), 0, max(span[3], f.bbox[3]))
        else:
            bands.append([f])
            span = (0, f.bbox[1], 0, f.bbox[3])

    rows, bound, labelled = [], 0, 0
    for band in bands:
        parts = sorted((f for f in band if id(f) not in figure_ids), key=lambda f: f.bbox[0])
        row_label = " ".join(f.text.strip() for f in parts).replace("|", "/")
        cells = [""] * len(columns)
        placed = 0
        for f in band:
            if id(f) in figure_ids:
                for i, c in enumerate(columns):
                    if any(f is g for g in grouped[c]):
                        cells[i] = (cells[i] + " " + f.text.strip()).strip()
                        placed += 1
        if placed and row_label:
            bound += placed
            labelled += 1
        if row_label or placed:
            rows.append([row_label, *cells])

    if labelled < 2 or bound / len(figures) < _MIN_BOUND_FRACTION:
        return None

    header = [table.header[table.label_col] or "Particulars"]
    header += [(table.header[c] if c < len(table.header) else "") or f"Column {c}" for c in columns]
    used = [f for band in bands for f in band]
    box = (
        min(f.bbox[0] for f in used), min(f.bbox[1] for f in used),
        max(f.bbox[2] for f in used), max(f.bbox[3] for f in used),
    )
    return (
        SynthesizedTable(markdown=_rows_to_markdown(header, rows), bbox=box,
                         title=None, figures=len(figures)),
        figures,
    )
