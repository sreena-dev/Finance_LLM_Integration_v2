"""The arithmetic self-audit: deciding which figures the model is allowed to see.

This is the anti-hallucination core, and it is deliberately arithmetic rather
than prompt wording. The FS agent's prompt already forbids the model from doing
its own arithmetic (rule 15) and from reporting a retrieval miss as a finding
(rule 16); those rules only produce correct behaviour if what reaches the model
is honest about what could not be read. Making it honest is this module's job.

The governing sentence is spec section 15.3: *"If an amount cannot be read with
confidence, classify the item as extraction issue, not financial issue."* So a
doubtful cell is not rounded, not guessed, and not quietly dropped -- it is
replaced in the emitted markdown by its ``CellFinding.marker``, which reads
``[unreadable: page 5, table t3, row "Disposals", col "Motor Car"]``. There is
then no path by which an unverified figure reaches a prompt AS A PLAIN NUMBER,
whatever the model does.

Three independent signals decide a cell:

1. **OCR confidence** from docling, per page (or, per cell, from RapidOCR --
   see ``pipeline.py``'s caller).
2. **Agreement** between docling's read and the VLM's independent re-read of the
   same cropped table image.
3. **Footing** -- whether the column the cell sits in adds up.

Footing is the strongest of the three and the only one that is self-evidencing:
a column of components that sums to its printed total is mutually corroborating
in a way no confidence score is. It is also what rescues the clipped-parenthesis
case. ``MH 2022-23 SFS`` page 5 prints ``(1,757`` with the closing paren cropped
off by the table border; ``numbers.parse_cell`` reads that as -1757 with
``sign_uncertain`` set, and this module confirms or refutes the sign by testing
which choice makes the column add up. A sign that cannot be settled that way
stays unreadable rather than being emitted as a positive.

Subtotals are **discovered arithmetically, not by label**. Real statements print
unlabelled subtotal rows -- the OD balance sheet's Shareholder's Funds subtotal
of 14,74,540 carries no caption at all -- so a label-driven scan would miss
exactly the rows most worth checking.

RECOVERY. A cell this module cannot vouch for is not necessarily a cell nobody
can read: a second reader (the whole-table VLM pass, or a fresh, narrowly
targeted per-row rescue call -- see ``vlm_read.py``) may already have a candidate
figure for it. Two tiers need no new API call at all -- ``vlm_only_row`` and
``readers_disagree`` cells already carry a second reader's text; the pipeline
just never looked at it. The other tiers (``unreadable_text``,
``low_ocr_confidence``, ``no_second_read_for_row``) genuinely have nothing to
recover from without a targeted rescue call, and this module only classifies
those as REQUESTS -- it never makes a network call itself.

Whatever the source, a candidate is never trusted outright. It is tried in the
column's own footing arithmetic FIRST (an overlay, never written into the
table): if it is the sole unknown in a footing that then closes, the arithmetic
-- not a reader -- has proven the figure, and it is PROMOTED to a plain,
quotable number exactly like a cleanly-read one. If it is not, or if two
recovered cells would share one footing (making neither individually
determined), the candidate stays DISPLAY-ONLY: shown to a human inside a
``[recovered ...]`` marker with a derived confidence band, but refused by every
downstream computation -- see ``backend/.../tools_fs.py``'s ``Row.withheld``,
which stays True for a display-only recovered cell precisely so it can never be
mistaken for a blank one.

The invariant this module guarantees is therefore not "no unverified figure
reaches a prompt" but the sharper one that actually holds with recovery in
play: **no unpromoted figure ever parses as a number, anywhere in the stack.**
Only arithmetic promotes one.
"""

from __future__ import annotations

import logging

from .config import Config
from .models import CellFinding, FootingCheck, RecoveredCell, derive_confidence, is_marker
from .numbers import parse_cell
from .tables import Table, looks_like_total

logger = logging.getLogger(__name__)


def _tolerance(magnitude: float) -> float:
    """Slack allowed when comparing a printed total to a recomputed one.

    Both an absolute and a relative term are needed. Filings round to the
    nearest thousand, which is absolute error independent of size; and they
    present in crore to two decimals, where the rounding error scales with the
    figure. Taking the larger of the two covers both without letting either
    swallow a real difference.
    """
    return max(
        Config.FOOTING_ABS_TOLERANCE,
        abs(magnitude) * Config.FOOTING_REL_TOLERANCE,
    )


def _column_values(
    table: Table, col: int, overlay: dict[tuple[int, int], float] | None = None
) -> list[tuple[int, float | None, bool]]:
    """``(row_index, value, is_suspect)`` down one column.

    ``overlay`` -- a ``{(row, col): value}`` recovery-candidate map -- is
    substituted in wherever it has an entry for this column. This is the
    ONLY mechanism by which a recovered figure can influence footing: the
    table itself is never mutated to try one out. See the module docstring's
    RECOVERY section.
    """
    out = []
    for r in range(len(table.rows)):
        cell = table.cell(r, col)
        value = cell.value
        if overlay is not None:
            candidate = overlay.get((r, col))
            if candidate is not None:
                value = candidate
        out.append((r, value, cell.suspect))
    return out


def _find_footings(
    table: Table, col: int, overlay: dict[tuple[int, int], float] | None = None
) -> list[tuple[int, list[int], float, float]]:
    """Discover subtotal rows in one column, arithmetically.

    A financial statement column is a *hierarchy*, not a flat list: leaf lines
    roll into section subtotals, and section subtotals roll into the grand
    total. So this walks the column keeping a stack of items not yet consumed by
    a subtotal, and at each row asks whether it equals the sum of some suffix of
    that stack. A match consumes those items and pushes the subtotal in their
    place, so the grand total is later matched against the *subtotals* rather
    than against the leaves it already contains.

    A flat contiguous-run scan cannot express this. Tried against the OD
    2021-22 balance sheet it found the unlabelled 14,74,540 subtotal correctly
    and then reported the genuine ``TOTAL`` of 14,99,540 as not footing, because
    it had no way to consume a single-line section subtotal and so double
    counted 25,000.

    ``overlay`` is threaded straight through to ``_column_values`` -- see its
    docstring. Passing one lets a caller ask "if this candidate figure were
    true, would the column foot?" without ever writing it into the table.

    Returns ``(row, component_rows, printed, recomputed)`` per subtotal found.
    """
    values = _column_values(table, col, overlay)
    populated = [(r, v) for r, v, _ in values if v is not None]
    if len(populated) < 3:
        return []

    found: list[tuple[int, list[int], float, float]] = []
    # Each stack entry is (representative_row, value, covered_rows).
    stack: list[tuple[int, float, list[int]]] = []

    for row, value in populated:
        matched = False
        # Longest suffix first: a section total should consume the whole section
        # rather than the last two lines of it.
        for take in range(len(stack), 0, -1):
            suffix = stack[len(stack) - take:]
            total = sum(v for _, v, _ in suffix)
            if abs(total - value) > _tolerance(value):
                continue
            # A one-item "subtotal" is a section with a single line in it. Real,
            # but indistinguishable from a value that merely repeats, so accept
            # it only where the row presents itself as a total: an explicit
            # caption, or the blank caption of an unlabelled subtotal.
            if take == 1:
                label = table.label(row).strip()
                if label and not looks_like_total(label):
                    continue
            covered = [r for _, _, rows in suffix for r in rows]
            found.append((row, covered, value, total))
            del stack[len(stack) - take:]
            stack.append((row, value, covered))
            matched = True
            break
        if not matched:
            stack.append((row, value, [row]))

    return found


def _try_sign_repair(table: Table, col: int, footings) -> set[int]:
    """Rows whose uncertain sign is settled by making the column foot.

    Only applied where the cell was already flagged ``sign_uncertain`` -- that
    is, where the source itself showed a clipped parenthesis. This never invents
    a sign for a cleanly printed positive number.
    """
    repaired: set[int] = set()
    for row, component_rows, printed, _ in footings:
        for r in component_rows:
            cell = table.cell(r, col)
            if not cell.sign_uncertain or cell.value is None:
                continue
            others = sum(
                (table.cell(x, col).value or 0.0)
                for x in component_rows if x != r
            )
            with_negative = others + cell.value
            with_positive = others - cell.value
            neg_ok = abs(with_negative - printed) <= _tolerance(printed)
            pos_ok = abs(with_positive - printed) <= _tolerance(printed)
            # Only decisive when exactly one of the two makes the column foot.
            if neg_ok and not pos_ok:
                repaired.add(r)
    return repaired


class _PendingCell:
    """One doubtful cell, classified but not yet resolved into a finding.

    Built by ``draft_table``'s Phase B and consumed by ``resolve_recoveries``'s
    Phase C. Exists so a recovery candidate (which may need a network call the
    caller has to make BETWEEN drafting and resolving) can be tried in the
    footing arithmetic before any ``CellFinding`` is built at all.
    """

    __slots__ = ("row", "col", "reasons", "hard", "raw", "row_label", "column_name")

    def __init__(self, row, col, reasons, hard, raw, row_label, column_name):
        self.row = row
        self.col = col
        self.reasons = reasons
        self.hard = hard
        self.raw = raw
        self.row_label = row_label
        self.column_name = column_name


class VerificationDraft:
    """Everything ``verify_table`` needs before recovery is resolved.

    ``rescue_requests`` is ``(row, col, row_label, column_name)`` per cell that
    has no recovery candidate yet and genuinely needs a targeted VLM re-read --
    this module never makes that call itself; a caller wanting recovery for
    those tiers runs the rescue calls for these requests and passes the results
    into ``resolve_recoveries`` as ``rescued``.
    """

    __slots__ = ("table", "page_no", "checks", "footed_cells", "pending", "rescue_requests")

    def __init__(self, table, page_no, checks, footed_cells, pending, rescue_requests):
        self.table = table
        self.page_no = page_no
        self.checks = checks
        self.footed_cells = footed_cells
        self.pending = pending
        self.rescue_requests = rescue_requests


def draft_table(
    table: Table,
    page_no: int,
    vlm_disagreements: set[tuple[int, int]] | None = None,
    ocr_score: float | None = None,
    unmatched_rows: set[int] | None = None,
    vlm_only_rows: set[int] | None = None,
    vlm_cell_text: dict[tuple[int, int], str] | None = None,
    unsupported_cells: set[tuple[int, int]] | None = None,
    lost_figure_cells: set[tuple[int, int]] | None = None,
) -> VerificationDraft:
    """Phase A (baseline footings) + Phase B (classify every doubtful cell).

    Emits nothing yet. A cell whose second reader's text is already known --
    ``vlm_only_row`` (the cell's own text, spliced in verbatim by
    ``vlm_read.insert_unclaimed_rows``) or ``readers_disagree`` (via
    ``vlm_cell_text``, the VLM's counterpart for that exact cell) -- can be
    resolved by ``resolve_recoveries`` with no further input. Everything else
    doubtful becomes a ``rescue_requests`` entry.

    ``unsupported_cells`` is different in kind from the others: a cell in it
    already had ITS OWN targeted re-read attempted (by the OCR number-binding
    pipeline, before this table ever reaches ``draft_table`` -- see
    ``pipeline.py``'s Pass 2b) and that re-read did NOT agree with the
    figure's own value, so there is no further candidate this phase could
    usefully request. It is withheld for a reason ``resolve_recoveries``
    needs no candidate to act on -- see the ``hard`` list below.

    ``lost_figure_cells`` names EMPTY cells the caller has positive evidence
    (OCR read a figure in that cell's own printed band and column, or it is a
    bare total) held a figure the grid dropped. They are withheld as
    ``figures_not_extracted`` and, having no candidate, become ordinary
    rescue requests -- so a lost figure earns a targeted re-read and can be
    promoted only by the column's own arithmetic, never shown on a re-read
    alone (there is no other trusted figure in such a row for
    ``check_rescue_anchors`` to anchor on).

    Arguments mirror the pre-recovery ``verify_table`` exactly; see its
    docstring below for what each means. All optional: with none of them, this
    degrades to arithmetic alone -- weaker, but never wrong in the dangerous
    direction.
    """
    vlm_disagreements = vlm_disagreements or set()
    unmatched_rows = unmatched_rows or set()
    vlm_only_rows = vlm_only_rows or set()
    vlm_cell_text = vlm_cell_text or {}
    unsupported_cells = unsupported_cells or set()
    lost_figure_cells = lost_figure_cells or set()

    checks: list[FootingCheck] = []
    # Rows whose sign was confirmed by the arithmetic, per column.
    confirmed: dict[int, set[int]] = {}

    # Cells corroborated by a column that adds up, tracked PER COLUMN. Matching
    # by row label instead would let a column that foots vouch for the identical
    # row in a column that does not -- which silently un-withheld the clipped
    # Total-column cell in the safety test.
    footed_cells: set[tuple[int, int]] = set()

    # ---- Phase A: baseline footings, unchanged ----
    for col in table.value_cols:
        footings = _find_footings(table, col)
        confirmed[col] = _try_sign_repair(table, col, footings)

        for row, component_rows, printed, recomputed in footings:
            footed_cells.add((row, col))
            for component_row in component_rows:
                footed_cells.add((component_row, col))
            checks.append(FootingCheck(
                table_id=table.table_id,
                page_no=page_no,
                subtotal_label=table.label(row) or f"(unlabelled row {row + 1})",
                printed=printed,
                recomputed=recomputed,
                difference=round(printed - recomputed, 4),
                passed=True,
                component_labels=[table.label(r) for r in component_rows],
            ))

        # A row that *says* it is a total but was not discovered as one above
        # did not foot. That is a real finding -- either the extraction is wrong
        # or the statement does not add up, and both need a human.
        footed_rows = {row for row, _, _, _ in footings}
        for r in range(len(table.rows)):
            if r in footed_rows:
                continue
            label = table.label(r)
            if not looks_like_total(label):
                continue
            cell = table.cell(r, col)
            if cell.value is None:
                continue
            above = [
                table.cell(x, col).value
                for x in range(r)
                if table.cell(x, col).value is not None
            ]
            if len(above) < 2:
                continue
            checks.append(FootingCheck(
                table_id=table.table_id,
                page_no=page_no,
                subtotal_label=label,
                printed=cell.value,
                recomputed=None,
                difference=None,
                passed=False,
                component_labels=[],
            ))

    # ---- Phase B: classify every doubtful cell ----
    low_ocr = ocr_score is not None and ocr_score < 0.5
    pending: list[_PendingCell] = []
    rescue_requests: list[tuple[int, int, str, str]] = []

    for col in table.value_cols:
        for r in range(len(table.rows)):
            cell = table.cell(r, col)
            # An EMPTY cell is normally skipped -- nothing to audit. The one
            # exception is a cell the caller has positive evidence held a
            # figure the grid dropped (`lost_figure_cells`): skipping it here
            # is what made a line item that lost its figures indistinguishable
            # from a section heading, i.e. silent data loss.
            lost = (r, col) in lost_figure_cells and cell.value is None and not cell.raw.strip()
            if cell.value is None and not cell.raw.strip() and not lost:
                continue
            # A marker this module already wrote (either kind -- see
            # models.MARKER_PREFIXES) is a finding, not a cell to audit. Its
            # page/table numbers parse as an oddly grouped number, so
            # re-verifying a redacted table would otherwise nest a second
            # marker inside the first.
            if is_marker(cell.raw):
                continue

            reasons = list(cell.flags())
            # Text in a value column that parses to NO number at all, but
            # plainly tried to be one (it carries a digit, or an OCR lookalike
            # for one). Measured on MH-CPSU-ITSL-048 2024-25: "Investment
            # Properties" came back as "8Z5'E" -- value None, and its only flag
            # was the cosmetic `ocr_lookalike`, so no finding was raised, it was
            # never redacted, and the raw garbage reached the emitted table
            # where a figure belongs. Pure words ("Not applicable"), nil forms
            # (dash, NIL) and a marker this module already wrote are left alone.
            if (cell.value is None and not cell.is_nil
                    and (any(ch.isdigit() for ch in cell.raw) or cell.lookalikes)):
                reasons.append("unreadable_text")
            if (r, col) in vlm_disagreements:
                reasons.append("readers_disagree")
            if r in unmatched_rows and cell.value is not None:
                reasons.append("no_second_read_for_row")
            if r in vlm_only_rows:
                reasons.append("vlm_only_row")
            if (r, col) in unsupported_cells:
                reasons.append("unsupported_by_ocr")
            if lost:
                reasons.append("figures_not_extracted")
            if low_ocr and cell.suspect:
                reasons.append(f"low_ocr_confidence:{ocr_score:.2f}")

            if not reasons:
                continue

            # The column's own arithmetic settled the sign.
            if cell.sign_uncertain and r in confirmed.get(col, set()):
                reasons = [x for x in reasons if x != "sign_uncertain"]

            # A cell that participates in a column that adds up is corroborated,
            # which outranks a reader disagreement: two readers differing on a
            # figure that foots means one of them mis-transcribed a cell that
            # the arithmetic can independently confirm. This applies equally to
            # `unsupported_by_ocr` -- a cell the number-binding pass could not
            # place on the page is still arithmetically confirmed if its column
            # foots, which is stronger evidence than binding itself gives.
            if (r, col) in footed_cells:
                reasons = [x for x in reasons if x in ("grouping_odd",)]

            # Cosmetic flags alone -- a repaired separator, a lookalike glyph in
            # a cell that still parsed cleanly -- are recorded on the table but
            # do not withhold a figure.
            hard = [
                x for x in reasons
                if x == "sign_uncertain" or x == "grouping_odd" or x == "readers_disagree"
                or x == "no_second_read_for_row" or x == "vlm_only_row"
                or x == "unreadable_text" or x == "unsupported_by_ocr"
                or x == "figures_not_extracted"
                or x.startswith("low_ocr_confidence")
            ]
            if not hard or (cell.value is None and not cell.raw.strip() and not lost):
                continue

            row_label = table.label(r) or f"row {r + 1}"
            column_name = table.column_name(col)
            pending.append(_PendingCell(
                r, col, sorted(set(reasons)), hard, cell.raw, row_label, column_name,
            ))

            # A cell the baseline footing pass already rescued needs neither a
            # candidate nor a rescue request -- it is not going to be a
            # CellFinding candidate for recovery at all (only grouping_odd can
            # have survived the filter above, and that is a formatting
            # curiosity on an arithmetically-confirmed figure, not something
            # a second reader would resolve).
            if (r, col) in footed_cells:
                continue
            # No new call needed: the second reader already produced text for
            # this exact cell (a disagreement) or this exact row (VLM-only).
            if "readers_disagree" in hard and (r, col) in vlm_cell_text:
                continue
            if "vlm_only_row" in hard:
                continue
            # A cell already re-read once by the number-binding pass, whose
            # re-read disagreed -- requesting another rescue here would
            # double-spend budget on a call that already failed.
            if "unsupported_by_ocr" in hard:
                continue
            # Everything else -- unreadable_text, low_ocr_confidence, and
            # no_second_read_for_row (docling has a value; nobody has
            # corroborated it) -- has no candidate yet and is a genuine
            # request for a targeted rescue read.
            rescue_requests.append((r, col, row_label, column_name))

    return VerificationDraft(table, page_no, checks, footed_cells, pending, rescue_requests)


#: At most this many clipped-parenthesis recoveries per table get a two-sign
#: footing trial (each is two `_find_footings` walks). Eight sign-uncertain
#: recoveries in ONE table means the scan is bad enough that withholding the
#: rest is the right answer anyway.
_MAX_SIGN_TRIALS = 8

#: Two same-line figures whose magnitudes sit within this factor of each other
#: are "comparable" for the cross-year sign check. A genuine swing from profit
#: to loss usually changes magnitude; a dropped or clipped parenthesis does
#: not.
_COMPARABLE_MAGNITUDE = 4.0


def _settle_sign(
    table: Table, col: int, row: int, magnitude: float,
    overlay: dict[tuple[int, int], float], baseline_sigs: set,
):
    """Try BOTH signs of a clipped-parenthesis candidate against the column's
    own arithmetic; return ``(sign, footing)`` only if EXACTLY ONE sign closes
    a footing the candidate was the sole unknown of, else ``None``.

    This is the confirmation `numbers.py` promises when it returns an
    unbalanced-paren value negative with ``sign_uncertain`` set ("verify.py
    either confirms or withholds"). For a printed cell `_try_sign_repair`
    keeps that promise; for a RECOVERED candidate nothing did -- it ran once,
    in Phase A, over the table's own cells, with no overlay. It also could not
    have been extended to: `_find_footings` walks the column with whatever
    value it is handed, so a footing that only closes with the OPPOSITE sign is
    never discovered by one pass. The two-sign trial is what finds it.

    Both signs closing, or neither, settles nothing: refusing is the only
    honest answer when the arithmetic cannot tell them apart.
    """
    found = {}
    for sign in (1, -1):
        trial = dict(overlay)
        trial[(row, col)] = sign * magnitude
        for frow, comp, printed, recomputed in _find_footings(table, col, trial):
            if (frow, tuple(sorted(comp))) in baseline_sigs:
                continue
            members = [frow] + comp
            if row not in members:
                continue
            if [m for m in members if (m, col) in trial] != [row]:
                continue
            found[sign] = (frow, comp, printed, recomputed)
            break
    if len(found) != 1:
        return None
    sign = next(iter(found))
    return sign, found[sign]


def _context_doubts(table: Table, row: int, col: int, value: float) -> list[str]:
    """Internal-consistency reasons to doubt a RECOVERED figure's sign -- a
    withholding signal only. It never flips a sign and never rewrites a figure.

    Two rules, neither needing a vocabulary of asset or liability labels:

    - ``negative_total``: a negative figure on a total line.
    - ``sign_disagrees_across_years``: negative, while every other value
      column of the same line holds a cleanly read non-negative figure of
      COMPARABLE magnitude -- the shape of a dropped or clipped parenthesis.

    An asset-side label regex was rejected deliberately: it needs a maintained
    vocabulary and misfires on legitimately negative lines (accumulated
    depreciation, "Less: provisions").
    """
    if value >= 0:
        return []
    doubts: list[str] = []
    if looks_like_total(table.label(row)):
        doubts.append("negative_total")
    others = []
    for c in table.value_cols:
        if c == col:
            continue
        other = table.cell(row, c)
        if other.value is not None and not other.suspect:
            others.append(other.value)
    if others and all(v >= 0 for v in others):
        positives = [v for v in others if v > 0]
        if any(1.0 / _COMPARABLE_MAGNITUDE <= abs(value) / v <= _COMPARABLE_MAGNITUDE
               for v in positives):
            doubts.append("sign_disagrees_across_years")
    return doubts


def _settled_text(text: str, negative: bool) -> str:
    """A clipped-parenthesis candidate's text, rewritten to the sign the
    arithmetic settled: balanced parentheses for a negative, none for a
    positive. Left as the VLM printed it ("(5,00,000") it would parse
    sign-uncertain and NEGATIVE all over again the moment anything downstream
    re-read the table."""
    core = text.replace("(", "").replace(")", "").strip()
    return f"({core})" if negative else core


def resolve_recoveries(
    draft: VerificationDraft,
    vlm_cell_text: dict[tuple[int, int], str] | None = None,
    alignment_grounded: bool = False,
    alignment_agreement: float | None = None,
    rescued: dict[tuple[int, int], "RescueResult"] | None = None,
) -> tuple[list[FootingCheck], list[CellFinding], list[RecoveredCell]]:
    """Phase C: try every pending cell's candidate in the footing arithmetic,
    promote what the arithmetic proves, and build the final findings for
    everything else.

    ``rescued`` carries the outcome of any targeted per-row VLM rescue calls
    the caller already ran for ``draft.rescue_requests`` -- ``RescueResult``
    is any object with ``.text`` (the transcribed cell text, or ``None`` for
    ``ILLEGIBLE``/failure) and ``.anchored`` (bool: whether
    ``vlm_read.check_rescue_anchors`` confirmed the read against the row's
    other, already-trusted figures). Pass ``{}`` (the default) to resolve only
    the zero-new-call tiers -- ``vlm_only_row`` and ``readers_disagree`` --
    which is a complete, correct, shippable behaviour on its own.

    The table is NEVER mutated here. A promoted figure's OWN text is written
    into the table only by ``apply_recoveries``, which a caller must run
    before ``redact`` -- see that function's docstring.
    """
    table = draft.table
    vlm_cell_text = vlm_cell_text or {}
    rescued = rescued or {}

    # ---- gather one candidate per pending cell, whatever its tier ----
    candidates: dict[tuple[int, int], tuple[str, str]] = {}
    for pc in draft.pending:
        key = (pc.row, pc.col)
        if "readers_disagree" in pc.hard and key in vlm_cell_text:
            candidates[key] = (vlm_cell_text[key], "readers_disagree")
        elif "vlm_only_row" in pc.hard:
            candidates[key] = (pc.raw, "vlm_only_row")
        elif key in rescued and rescued[key].text is not None:
            candidates[key] = (rescued[key].text, "rescue_read")

    # ---- parse each candidate; build the overlay from the ones that parse ----
    overlay: dict[tuple[int, int], float] = {}
    parsed: dict[tuple[int, int], object] = {}
    # A clipped-parenthesis candidate parses NEGATIVE with `sign_uncertain`
    # set (numbers.py's documented contract: "verify.py either confirms or
    # withholds"). It must never enter the overlay at that guessed sign -- it
    # waits here until the column's own arithmetic settles it (below).
    sign_pending: dict[tuple[int, int], float] = {}
    for key, (text, _origin) in candidates.items():
        cell = parse_cell(text)
        parsed[key] = cell
        if cell.value is None:
            continue
        if cell.sign_uncertain:
            sign_pending[key] = abs(cell.value)
        else:
            overlay[key] = cell.value

    # ---- second footing pass over the overlay; promote sole unknowns only ----
    # `_MAX_UNKNOWNS_PER_FOOTING = 1`: one equation determines one unknown.
    # Two recovered figures sharing one footing are NOT individually
    # determined by it -- any pair that happens to sum correctly would pass,
    # which is exactly the hallucination path this guards against.
    promoted: set[tuple[int, int]] = set()
    # Every overlay cell that took part in a footing with 2+ unknowns -- not
    # individually determined by that footing, so display-only with the more
    # specific "two recovered figures share one total" caveat rather than the
    # generic "column total does not confirm it".
    multi_unknown: set[tuple[int, int]] = set()
    new_footing_checks: list[FootingCheck] = []
    if overlay:
        overlay_cols = {col for (_row, col) in overlay}
        for col in overlay_cols:
            baseline_sigs = {
                (row, tuple(sorted(comp))) for row, comp, _, _ in _find_footings(table, col)
            }
            for row, component_rows, printed, recomputed in _find_footings(table, col, overlay):
                sig = (row, tuple(sorted(component_rows)))
                if sig in baseline_sigs:
                    continue  # already footed without any candidate
                members = [row] + component_rows
                unknowns = [m for m in members if (m, col) in overlay]
                if len(unknowns) != 1:
                    if len(unknowns) >= 2:
                        multi_unknown.update((m, col) for m in unknowns)
                    continue
                promoted.add((unknowns[0], col))
                new_footing_checks.append(FootingCheck(
                    table_id=table.table_id,
                    page_no=draft.page_no,
                    subtotal_label=table.label(row) or f"(unlabelled row {row + 1})",
                    printed=printed,
                    recomputed=recomputed,
                    difference=round(printed - recomputed, 4),
                    passed=True,
                    component_labels=[table.label(r) for r in component_rows],
                    used_recovered=True,
                ))

    # ---- settle clipped-parenthesis signs by arithmetic, or withhold ----------
    sign_settled: set[tuple[int, int]] = set()
    if sign_pending:
        baseline_by_col: dict[int, set] = {}
        for trials, (key, magnitude) in enumerate(sorted(sign_pending.items())):
            row, col = key
            if trials >= _MAX_SIGN_TRIALS:
                verdict = None
            else:
                if col not in baseline_by_col:
                    baseline_by_col[col] = {
                        (r, tuple(sorted(comp))) for r, comp, _, _ in _find_footings(table, col)
                    }
                verdict = _settle_sign(table, col, row, magnitude, overlay, baseline_by_col[col])
            if verdict is None:
                # The arithmetic cannot say which sign it is. Withheld -- never
                # shown at a guessed sign, which is how an asset went negative.
                del candidates[key]
                continue
            sign, (frow, comp, printed, recomputed) = verdict
            overlay[key] = sign * magnitude
            promoted.add(key)
            sign_settled.add(key)
            new_footing_checks.append(FootingCheck(
                table_id=table.table_id,
                page_no=draft.page_no,
                subtotal_label=table.label(frow) or f"(unlabelled row {frow + 1})",
                printed=printed,
                recomputed=recomputed,
                difference=round(printed - recomputed, 4),
                passed=True,
                component_labels=[table.label(r) for r in comp],
                used_recovered=True,
            ))

    checks = list(draft.checks) + new_footing_checks
    findings: list[CellFinding] = []
    recovered: list[RecoveredCell] = []

    for pc in draft.pending:
        key = (pc.row, pc.col)

        if key in promoted:
            text, origin = candidates[key]
            if key in sign_settled:
                text = _settled_text(text, overlay[key] < 0)
            recovered.append(RecoveredCell(
                page_no=draft.page_no,
                table_id=table.table_id,
                row_label=pc.row_label,
                column=pc.column_name,
                raw=pc.raw,
                recovered_text=text,
                recovered_value=overlay[key],
                confidence="high",
                confidence_basis=(
                    ["arithmetic_determined"]
                    + (["sign_settled_by_footing"] if key in sign_settled else [])
                    + [f"context_doubt:{d}" for d in
                       (_context_doubts(table, pc.row, pc.col, overlay[key])
                        if Config.CONTEXT_SANITY_ENABLED else [])]
                ),
                origin=origin,
                row_index=pc.row,
                col_index=pc.col,
            ))
            continue

        if key in candidates:
            text, origin = candidates[key]
            cell = parsed[key]
            # Two candidates sharing one footing (or a candidate whose footing
            # never closed) is display-only, not withheld outright -- but a
            # blank/garbled candidate that didn't even parse has nothing to
            # show, so it falls through to the plain-withheld branch below.
            if cell.value is None:
                findings.append(CellFinding(
                    page_no=draft.page_no,
                    table_id=table.table_id,
                    row_label=pc.row_label,
                    column=pc.column_name,
                    raw=pc.raw,
                    reasons=pc.reasons,
                    row_index=pc.row,
                    col_index=pc.col,
                ))
                continue

            # A recovered figure that contradicts its own line (a negative
            # total; a negative beside a comparable positive from the other
            # year) is withheld, never shown. Withhold-only: this does not
            # flip a sign, and it does not veto a figure arithmetic PROVED --
            # that branch returned above.
            doubts = (
                _context_doubts(table, pc.row, pc.col, cell.value)
                if Config.CONTEXT_SANITY_ENABLED else []
            )
            if doubts:
                findings.append(CellFinding(
                    page_no=draft.page_no,
                    table_id=table.table_id,
                    row_label=pc.row_label,
                    column=pc.column_name,
                    raw=pc.raw,
                    reasons=sorted(set(pc.reasons) | set(doubts)),
                    row_index=pc.row,
                    col_index=pc.col,
                ))
                continue

            clean_parse = not (cell.sign_uncertain or cell.grouping_odd or cell.lookalikes)
            rescue_anchored = rescued[key].anchored if key in rescued else None
            band, basis = derive_confidence(
                footing_determined=False,
                grounded=alignment_grounded,
                agreement=alignment_agreement,
                rescue_anchored=rescue_anchored,
                clean_parse=clean_parse,
            )
            findings.append(CellFinding(
                page_no=draft.page_no,
                table_id=table.table_id,
                row_label=pc.row_label,
                column=pc.column_name,
                raw=pc.raw,
                reasons=pc.reasons,
                row_index=pc.row,
                col_index=pc.col,
                recovered_text=text,
                recovered_value=cell.value,
                confidence=band,
                confidence_basis=basis,
                recovery_origin=(
                    "two_recovered_in_one_footing" if key in multi_unknown else origin
                ),
            ))
            continue

        # No candidate at all -- plain withheld, exactly as before recovery.
        findings.append(CellFinding(
            page_no=draft.page_no,
            table_id=table.table_id,
            row_label=pc.row_label,
            column=pc.column_name,
            raw=pc.raw,
            reasons=pc.reasons,
            row_index=pc.row,
            col_index=pc.col,
        ))

    return checks, findings, recovered


def apply_recoveries(table: Table, recovered: list[RecoveredCell]) -> None:
    """Write a PROMOTED figure's own text into the table, in place.

    Needed for the ``readers_disagree`` tier: a ``vlm_only_row`` cell's
    text is already the VLM's own transcription (verbatim, from
    ``vlm_read.insert_unclaimed_rows``), so promoting it normally changes
    nothing on the table -- this is what makes a ``readers_disagree``
    promotion actually replace docling's disagreeing text with the reading
    the arithmetic proved. The exception is a clipped-parenthesis figure
    whose sign the arithmetic settled: its text is rewritten (see
    ``_settled_text``), so it IS written back whatever its origin.

    MUST run before ``redact``: a promoted cell never appears in ``findings``,
    so ``redact`` never touches it -- the table needs the confirmed text
    written in (or left alone) before that point, not after.
    """
    if not recovered:
        return
    for rc in recovered:
        if rc.row_index is None or rc.col_index is None:
            continue
        row = table.rows[rc.row_index]
        current = row[rc.col_index] if rc.col_index < len(row) else ""
        if rc.origin == "vlm_only_row" and current == rc.recovered_text:
            continue  # already correct -- the VLM's text is already the cell
        while len(row) <= rc.col_index:
            row.append("")
        row[rc.col_index] = rc.recovered_text


def verify_table(
    table: Table,
    page_no: int,
    vlm_disagreements: set[tuple[int, int]] | None = None,
    ocr_score: float | None = None,
    unmatched_rows: set[int] | None = None,
    vlm_only_rows: set[int] | None = None,
    vlm_cell_text: dict[tuple[int, int], str] | None = None,
    alignment_grounded: bool = False,
    alignment_agreement: float | None = None,
) -> tuple[list[FootingCheck], list[CellFinding]]:
    """Audit one table. Returns its footing results and its unreadable cells.

    ``vlm_disagreements`` are ``(row, col)`` pairs where the vision model's
    read of the same image disagreed with docling's. ``unmatched_rows`` are
    rows the second reader did not produce a counterpart for at all, so their
    figures carry no second opinion -- a different thing from a disagreement,
    and reported as such. ``vlm_only_rows`` are rows that exist in ``table``
    ONLY because ``vlm_read.insert_unclaimed_rows`` spliced them in from the
    vision model's own read -- docling never produced them at all. ``ocr_score``
    is docling's own confidence for the page.

    ``vlm_cell_text`` (new), ``alignment_grounded`` (new) and
    ``alignment_agreement`` (new) enable RECOVERY for the two tiers that need
    no fresh network call -- see the module docstring. All optional and
    defaulted, so every pre-existing caller is unaffected: with none of the
    new arguments, a ``vlm_only_row``/``readers_disagree`` cell is still
    withheld exactly as before recovery existed, because there is then no
    candidate text to try.

    This is a thin convenience wrapper over ``draft_table`` +
    ``resolve_recoveries`` (+ ``apply_recoveries`` for whatever got promoted)
    for callers that have no targeted rescue calls to make. A caller that
    DOES want the ``unreadable_text``/``low_ocr_confidence``/
    ``no_second_read_for_row`` tiers recovered must call ``draft_table``
    directly, run its own rescue calls for ``draft.rescue_requests``, and pass
    the results to ``resolve_recoveries`` as ``rescued``.
    """
    draft = draft_table(
        table, page_no, vlm_disagreements, ocr_score, unmatched_rows, vlm_only_rows, vlm_cell_text,
    )
    checks, findings, recovered = resolve_recoveries(
        draft,
        vlm_cell_text=vlm_cell_text,
        alignment_grounded=alignment_grounded,
        alignment_agreement=alignment_agreement,
    )
    apply_recoveries(table, recovered)
    return checks, findings


def redact(table: Table, findings: list[CellFinding]) -> None:
    """Replace every unreadable cell in the table with its marker, in place.

    After this the table's markdown can be handed to the model directly: there
    is no number left in it that a computation may use without either having
    been cleanly read, or promoted by arithmetic (see ``apply_recoveries``,
    which must run first). A display-only recovered cell's ``[recovered ...]``
    marker is written here exactly like a plain ``[unreadable: ...]`` one --
    this function reads only ``f.marker`` and does not care which kind it is.
    """
    if not findings:
        return

    # Keyed on position, not on the label. A PPE roll-forward prints two rows
    # called "Additions" and two called "Disposals"; keying on the name
    # redacted the readable one alongside the unreadable one, blanking a figure
    # nothing was ever wrong with. Findings from before indices were recorded
    # still fall back to the label so an older record redacts as it used to.
    by_index = {
        (f.row_index, f.col_index): f.marker
        for f in findings
        if f.row_index is not None and f.col_index is not None
    }
    by_label = {
        (f.row_label, f.column): f.marker
        for f in findings
        if f.row_index is None or f.col_index is None
    }

    for r in range(len(table.rows)):
        label = table.label(r) or f"row {r + 1}"
        for col in table.value_cols:
            marker = by_index.get((r, col)) or by_label.get((label, table.column_name(col)))
            if marker is None:
                continue
            row = table.rows[r]
            while len(row) <= col:
                row.append("")
            row[col] = marker


def check_balance_sheet(figures: dict[str, float]) -> FootingCheck | None:
    """The balance-sheet equation, as a named cross-statement check.

    Kept separate from column footing because it spans the whole statement
    rather than one contiguous run, and because ``total_equity_and_liabilities``
    is the caption that actually balances against ``total_assets`` on Indian
    filings -- ``Total liabilities`` alone appears on a minority of them.
    """
    assets = figures.get("total_assets")
    eq_and_liab = figures.get("total_equity_and_liabilities")
    if assets is None or eq_and_liab is None:
        return None
    diff = assets - eq_and_liab
    return FootingCheck(
        table_id="balance_sheet",
        page_no=0,
        subtotal_label="Balance sheet equation (assets = equity + liabilities)",
        printed=assets,
        recomputed=eq_and_liab,
        difference=round(diff, 4),
        passed=abs(diff) <= _tolerance(assets),
    )
