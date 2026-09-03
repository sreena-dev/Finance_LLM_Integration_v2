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
then no path by which an unverified figure reaches a prompt, whatever the model
does.

Three independent signals decide a cell:

1. **OCR confidence** from docling, per page.
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
"""

from __future__ import annotations

import logging

from .config import Config
from .models import CellFinding, FootingCheck
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


def _column_values(table: Table, col: int) -> list[tuple[int, float | None, bool]]:
    """``(row_index, value, is_suspect)`` down one column."""
    out = []
    for r in range(len(table.rows)):
        cell = table.cell(r, col)
        out.append((r, cell.value, cell.suspect))
    return out


def _find_footings(table: Table, col: int) -> list[tuple[int, list[int], float, float]]:
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

    Returns ``(row, component_rows, printed, recomputed)`` per subtotal found.
    """
    values = _column_values(table, col)
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


def verify_table(
    table: Table,
    page_no: int,
    vlm_disagreements: set[tuple[int, int]] | None = None,
    ocr_score: float | None = None,
) -> tuple[list[FootingCheck], list[CellFinding]]:
    """Audit one table. Returns its footing results and its unreadable cells.

    ``vlm_disagreements`` are ``(row, col)`` pairs where the vision model's
    independent read of the same image disagreed with docling's. ``ocr_score``
    is docling's own confidence for the page. Both are optional: with neither,
    the audit falls back to arithmetic alone, which is weaker but never wrong in
    the dangerous direction -- it withholds less, and what it does vouch for is
    still corroborated by a column that adds up.
    """
    vlm_disagreements = vlm_disagreements or set()
    checks: list[FootingCheck] = []
    findings: list[CellFinding] = []
    # Rows whose sign was confirmed by the arithmetic, per column.
    confirmed: dict[int, set[int]] = {}

    # Cells corroborated by a column that adds up, tracked PER COLUMN. Matching
    # by row label instead would let a column that foots vouch for the identical
    # row in a column that does not -- which silently un-withheld the clipped
    # Total-column cell in the safety test.
    footed_cells: set[tuple[int, int]] = set()

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

    # Now fuse the three signals per cell. A cell is withheld when something
    # doubts it and nothing corroborates it.
    low_ocr = ocr_score is not None and ocr_score < 0.5

    for col in table.value_cols:
        for r in range(len(table.rows)):
            cell = table.cell(r, col)
            if cell.value is None and not cell.raw.strip():
                continue

            reasons = list(cell.flags())
            if (r, col) in vlm_disagreements:
                reasons.append("readers_disagree")
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
            # the arithmetic can independently confirm.
            if (r, col) in footed_cells:
                reasons = [x for x in reasons if x in ("grouping_odd",)]

            # Cosmetic flags alone -- a repaired separator, a lookalike glyph in
            # a cell that still parsed cleanly -- are recorded on the table but
            # do not withhold a figure.
            hard = [
                x for x in reasons
                if x == "sign_uncertain" or x == "grouping_odd" or x == "readers_disagree"
                or x.startswith("low_ocr_confidence")
            ]
            if not hard or cell.value is None and not cell.raw.strip():
                continue

            findings.append(CellFinding(
                page_no=page_no,
                table_id=table.table_id,
                row_label=table.label(r) or f"row {r + 1}",
                column=table.column_name(col),
                raw=cell.raw,
                reasons=sorted(set(reasons)),
            ))

    return checks, findings


def redact(table: Table, findings: list[CellFinding]) -> None:
    """Replace every unreadable cell in the table with its marker, in place.

    After this the table's markdown can be handed to the model directly: there
    is no unverified number left in it to quote.
    """
    by_position = {(f.row_label, f.column) for f in findings}
    if not by_position:
        return
    lookup = {(f.row_label, f.column): f.marker for f in findings}
    for r in range(len(table.rows)):
        label = table.label(r) or f"row {r + 1}"
        for col in table.value_cols:
            key = (label, table.column_name(col))
            if key in lookup:
                row = table.rows[r]
                while len(row) <= col:
                    row.append("")
                row[col] = lookup[key]


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
