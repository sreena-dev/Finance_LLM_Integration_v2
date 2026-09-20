"""User-entered figures for cells the extraction could not read.

The pipeline withholds a figure it cannot prove (``[unreadable: ...]``) and
shows, caveated, one it cannot confirm (``[recovered ...]``). A person reading
the scan next to the extracted text can supply the figure the machine could
not -- the one input this pipeline cannot produce for itself. This module is
the pure logic for that: no I/O, no Redis, no Postgres, so every rule below is
testable on plain dicts and strings. ``store.py`` does the persisting,
``router.py`` the HTTP.

**A typed figure is a NEW kind of evidence**, so it never masquerades as an
extracted one:

- On the cell itself it is written ``1,234 [user-entered]`` -- the value first,
  a fixed literal tag after. ``tools_fs._re_parse_number`` strips the tag, so
  the figure is a real, computable number, and the tag stays on the cell so a
  reader that never consults the sidecar record still sees who supplied it.
  (A plain number would leave no trace at all -- the exact failure this avoids.)
- A sidecar record in ``quality["user_edits"]`` keeps who, when, the original
  marker and the finding it replaced, so **revert restores the lists exactly**.

**Scope is enforced here, on stored state, never on what the client says.**
Only a cell that currently holds an ``[unreadable``/``[recovered`` marker -- and
that the stored quality report agrees is one -- can be edited, plus a cell this
module already tagged (re-edit, revert). A clean cell, or a figure the
arithmetic promoted, is refused: that containment is what stops this becoming
a way to quietly rewrite a filing.

**What is deliberately NOT patched:** ``failed_footings`` and each table's own
``findings``/``footings`` (evidence of the original extraction -- rewriting
them would launder history) and the scan grade (the scan was not better; a
human corrected it).
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any

USER_ENTERED_TAG = "[user-entered]"
_TAG_RE = re.compile(r"\s*\[user-entered\]\s*$", re.I)
_MARKER_RE = re.compile(r"\[\s*(unreadable|recovered)\b", re.I)
_RECOVERED_RE = re.compile(r"\[\s*recovered\b", re.I)

_MAX_VALUE_CHARS = 20

# Indian ("1,50,000") or Western ("150,000") grouping, or ungrouped; optional
# decimals. Parentheses and a leading minus are handled separately so an
# unbalanced or doubled sign is rejected rather than guessed at.
_NUMBER_BODY_RE = re.compile(r"^\d{1,3}(?:,\d{2,3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")

_TOTAL_LABEL_RE = re.compile(r"^\s*(?:total|totai)\b", re.I)


class EditError(Exception):
    """A refused edit. ``status`` is the HTTP status the router should use."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


# ---------------------------------------------------------------------------
# Table text: split and rejoin a pipe row exactly as ingestion writes it
# ---------------------------------------------------------------------------

def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def join_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def cell_state(text: str) -> str:
    """``unreadable`` | ``recovered`` | ``user_entered`` | ``clean``."""
    if _TAG_RE.search(text or ""):
        return "user_entered"
    m = _MARKER_RE.search(text or "")
    if not m:
        return "clean"
    return "recovered" if _RECOVERED_RE.search(text) else "unreadable"


def _locate(table_md: str, row_index: int, col_index: int) -> tuple[list[str], int, list[str]]:
    """``(lines, line_index, cells)`` for a cell. ``Table.to_markdown()`` emits a
    header line, a ``---`` separator, then ``rows`` -- so a row's line is
    ``row_index + 2``. Anything that does not line up is a 422, never a guess."""
    lines = (table_md or "").split("\n")
    if not isinstance(row_index, int) or not isinstance(col_index, int) \
            or row_index < 0 or col_index < 0:
        raise EditError(422, "bad_coordinates", "row_index and col_index must be non-negative integers.")
    line_index = row_index + 2
    if line_index >= len(lines):
        raise EditError(422, "bad_coordinates", "That row does not exist in this table.")
    header_cells = split_row(lines[0])
    cells = split_row(lines[line_index])
    if len(cells) != len(header_cells):
        raise EditError(
            422, "bad_coordinates",
            "That row's cell count does not match the table header, so it cannot be "
            "addressed safely (a caption containing '|' can cause this).",
        )
    if col_index >= len(cells):
        raise EditError(422, "bad_coordinates", "That column does not exist in this table.")
    return lines, line_index, cells


# ---------------------------------------------------------------------------
# Value validation -- stricter than float(), which accepts nan, inf and 1e9
# ---------------------------------------------------------------------------

def validate_value(raw: Any) -> str:
    """The normalised text of a figure a user typed, or a 422.

    Accepts ``12,859``, ``1,50,000.50``, ``12859``, ``-5``, ``(5)``,
    ``₹ 1,234``. Rejects ``nan``, ``inf``, ``1e9``, ``-``, blank, anything with
    ``|`` ``[`` ``]`` or a newline, unbalanced or doubled signs, and anything
    over ``_MAX_VALUE_CHARS``. A nil balance must be typed ``0``.
    """
    if not isinstance(raw, str):
        raise EditError(422, "bad_value", "Enter the figure as text, for example 12,859.")
    text = raw.strip()
    if not text:
        raise EditError(422, "bad_value", "Enter a figure. A nil balance is entered as 0.")
    if len(text) > _MAX_VALUE_CHARS:
        raise EditError(422, "bad_value", "That figure is too long.")
    if any(ch in text for ch in "|[]\n\r\t"):
        raise EditError(422, "bad_value", "A figure cannot contain | [ ] or line breaks.")

    body = text.replace("₹", "").strip()
    opens, closes = body.count("("), body.count(")")
    if opens != closes or opens > 1:
        raise EditError(422, "bad_value",
                        "Parentheses must be balanced, for example (12,859) for a negative.")
    negative = False
    if opens:
        if not (body.startswith("(") and body.endswith(")")):
            raise EditError(422, "bad_value", "Put the whole figure inside the parentheses.")
        negative = True
        body = body[1:-1].strip()
    if body.startswith("-"):
        if negative:
            raise EditError(422, "bad_value",
                            "Use either a minus sign or parentheses for a negative, not both.")
        negative = True
        body = body[1:].strip()
    if not _NUMBER_BODY_RE.match(body):
        raise EditError(422, "bad_value",
                        "That is not a figure. Use digits with optional commas and decimals, "
                        "for example 1,50,000.50.")
    normalised = f"({body})" if negative else body

    from tools_fs import _re_parse_number  # noqa: WPS433 -- the tools' own parser

    if _re_parse_number(normalised) is None:
        raise EditError(422, "bad_value", "That figure could not be read as a number.")
    return normalised


def _tagged(value_text: str) -> str:
    return f"{value_text} {USER_ENTERED_TAG}"


# ---------------------------------------------------------------------------
# Locating the stored evidence for a cell
# ---------------------------------------------------------------------------

def short_table_id(doc_id: str, stored_table_id: str) -> str:
    prefix = f"{doc_id}_"
    return stored_table_id[len(prefix):] if stored_table_id.startswith(prefix) else stored_table_id


def active_edits(quality: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [e for e in ((quality or {}).get("user_edits") or []) if e.get("active", True)]


def _same_table(entry_table: str | None, short: str, stored: str) -> bool:
    return entry_table in (short, stored)


def _find_indexed(entries: list[dict[str, Any]], short: str, stored: str,
                  row: int, col: int) -> list[int]:
    return [
        i for i, e in enumerate(entries)
        if _same_table(e.get("table_id"), short, stored)
        and e.get("row_index") == row and e.get("col_index") == col
    ]


def _find_unindexed(entries: list[dict[str, Any]], short: str, stored: str,
                    marker_text: str) -> list[int]:
    """Old records carry no coordinates; the only safe fallback is an exact
    marker match, and the caller refuses unless exactly one entry matches."""
    return [
        i for i, e in enumerate(entries)
        if _same_table(e.get("table_id"), short, stored)
        and e.get("row_index") is None and (e.get("marker") or "") == marker_text
    ]


def _resolve_finding(quality: dict[str, Any], short: str, stored: str,
                     row: int, col: int, cell_text: str) -> int:
    entries = quality.get("unreadable_cells") or []
    hits = _find_indexed(entries, short, stored, row, col)
    if not hits:
        hits = _find_unindexed(entries, short, stored, cell_text)
    if len(hits) > 1:
        raise EditError(409, "ambiguous", "More than one recorded finding matches this cell, "
                        "so it cannot be edited safely.")
    if not hits:
        raise EditError(409, "not_editable",
                        "The extraction has no record of this cell being unreadable, "
                        "so it cannot be edited.")
    return hits[0]


# ---------------------------------------------------------------------------
# Applying an edit
# ---------------------------------------------------------------------------

def apply(
    table_md: str, quality: dict[str, Any], *, doc_id: str, stored_table_id: str,
    request: dict[str, Any], user_id: str, now: float | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Apply one edit. Returns ``(new_table_md, new_quality, result)``; inputs
    are never mutated. Raises `EditError` on every refusal.

    ``request``: ``row_index``, ``col_index``, ``expected_cell`` (the text the
    client saw -- the optimistic-concurrency token), ``action``
    (``set``|``confirm``|``revert``) and, for ``set``, ``value``.
    """
    now = time.time() if now is None else now
    action = request.get("action")
    if action not in ("set", "confirm", "revert"):
        raise EditError(422, "bad_action", "action must be set, confirm or revert.")
    row = request.get("row_index")
    col = request.get("col_index")
    lines, line_index, cells = _locate(table_md, row, col)
    current = cells[col]

    if current != (request.get("expected_cell") or ""):
        raise EditError(409, "cell_changed",
                        "This cell changed since you loaded it. Reload and try again.",
                        current=current)

    short = short_table_id(doc_id, stored_table_id)
    state = cell_state(current)
    quality = dict(quality or {})
    edits = list(quality.get("user_edits") or [])
    unreadable = list(quality.get("unreadable_cells") or [])
    recovered = list(quality.get("recovered_cells") or [])

    record_idx = next(
        (i for i, e in enumerate(edits)
         if e.get("active", True) and e.get("table") == short
         and e.get("row_index") == row and e.get("col_index") == col),
        None,
    )

    # ---- scope guard: stored state only ------------------------------------
    if state == "clean":
        raise EditError(409, "not_editable",
                        "Only figures the extraction could not read can be edited.")
    if state == "user_entered" and record_idx is None:
        raise EditError(409, "not_editable", "This figure has no edit record.")

    if state != "user_entered":
        finding_idx = _resolve_finding(quality, short, stored_table_id, row, col, current)
        finding = unreadable[finding_idx]
    else:
        finding = None

    # ---- decide the new cell text -------------------------------------------
    if action == "revert":
        if state != "user_entered":
            raise EditError(409, "not_editable", "Only a user-entered figure can be reverted.")
        record = edits[record_idx]
        new_text = record["original_marker"]
        value_text = None
    elif action == "confirm":
        if state == "user_entered":
            raise EditError(409, "not_editable", "This figure was already entered by the user.")
        if state != "recovered":
            raise EditError(409, "not_editable",
                            "There is no recovered figure to confirm for an unreadable cell. "
                            "Enter the figure instead.")
        value_text = validate_value(finding.get("recovered_text") or "")
        new_text = _tagged(value_text)
    else:  # set
        value_text = validate_value(request.get("value"))
        new_text = _tagged(value_text)

    if new_text == current:
        return table_md, quality, {"unchanged": True, "cell": current,
                                   "edit_record": edits[record_idx] if record_idx is not None else None,
                                   "footing": None}

    # ---- rewrite the cell -----------------------------------------------------
    new_cells = list(cells)
    new_cells[col] = new_text
    new_lines = list(lines)
    new_lines[line_index] = join_row(new_cells)
    new_md = "\n".join(new_lines)

    # ---- patch the quality lists and the sidecar record -----------------------
    base = {"action": action, "at": now, "by": user_id}
    if action == "revert":
        record = dict(edits[record_idx])
        if record.get("original_finding"):
            unreadable.append(record["original_finding"])
        if record.get("original_recovered_entry"):
            recovered.append(record["original_recovered_entry"])
        record["active"] = False
        record["history"] = list(record.get("history") or []) + [
            {**base, "value": None}]
        edits[record_idx] = record
        footing = None
    else:
        footing = advisory_footing(new_md, row, col)
        if state == "user_entered":
            record = dict(edits[record_idx])
            record["value"] = value_text
            record["cell_text"] = new_text
            record["action"] = action
            record["by"] = user_id
            record["at"] = now
            record["footing"] = footing
            record["history"] = list(record.get("history") or []) + [
                {**base, "value": value_text}]
            edits[record_idx] = record
        else:
            saved_recovered = None
            for i in reversed(_find_indexed(recovered, short, stored_table_id, row, col)):
                if not recovered[i].get("promoted"):
                    saved_recovered = recovered.pop(i)
            unreadable.pop(finding_idx)
            record = {
                "table": short, "stored_table_id": stored_table_id,
                "row_index": row, "col_index": col,
                "row_label": finding.get("row_label"), "column": finding.get("column"),
                "page_no": finding.get("page_no"),
                "original_marker": current, "original_state": state,
                "original_finding": finding, "original_recovered_entry": saved_recovered,
                "recovered_text": finding.get("recovered_text"),
                "confidence": finding.get("confidence"),
                "value": value_text, "cell_text": new_text, "action": action,
                "by": user_id, "at": now, "footing": footing, "active": True,
                "history": [{**base, "value": value_text}],
            }
            edits.append(record)

    quality["unreadable_cells"] = unreadable
    quality["recovered_cells"] = recovered
    quality["user_edits"] = edits
    return new_md, quality, {"unchanged": False, "cell": new_text,
                             "edit_record": record, "footing": footing}


# ---------------------------------------------------------------------------
# Advisory footing -- a simple, flat check; NEVER blocks an edit
# ---------------------------------------------------------------------------

def _num(text: str) -> float | None:
    from tools_fs import _re_parse_number
    return _re_parse_number(text)


def _tolerance(x: float) -> float:
    return max(1.0, 0.001 * abs(x))


def advisory_footing(table_md: str, row_index: int, col_index: int) -> dict[str, Any]:
    """Does the edited column still add up? Advisory, in every path.

    Flat: the nearest ``Total`` row (the edited row itself when it is one) is
    compared with the rows since the previous total. **Weaker than ingestion's
    hierarchical footing** -- it can call a grand total "not confirmed" because
    a nested subtotal was double counted, so the wording is always "not
    confirmed by this column's arithmetic (simple check)", never "wrong". To cut
    false alarms it also tries the sum without unlabelled (subtotal-shaped)
    rows, and reports ``ties`` if EITHER closes -- which cannot produce a false
    "ties" unless some subset of the printed figures really does add up.
    """
    lines = (table_md or "").split("\n")
    body = [split_row(l) for l in lines[2:]]
    if row_index >= len(body) or col_index >= len(body[row_index] if row_index < len(body) else []):
        return {"verdict": "not_checkable", "reason": "cell not found"}

    def label(r: int) -> str:
        return body[r][0] if body[r] else ""

    def value_at(r: int) -> tuple[float | None, str]:
        cell = body[r][col_index] if col_index < len(body[r]) else ""
        return _num(cell), cell

    if _TOTAL_LABEL_RE.match(label(row_index)):
        total_row = row_index
    else:
        total_row = next((r for r in range(row_index + 1, len(body))
                          if _TOTAL_LABEL_RE.match(label(r))), None)
    if total_row is None:
        return {"verdict": "not_checkable", "reason": "no total below this figure"}

    printed, printed_cell = value_at(total_row)
    if printed is None or cell_state(printed_cell) in ("unreadable", "recovered"):
        return {"verdict": "not_checkable", "reason": "the total itself is not readable",
                "total_label": label(total_row)}

    prev = next((r for r in range(total_row - 1, -1, -1)
                 if _TOTAL_LABEL_RE.match(label(r))), -1)
    components = list(range(prev + 1, total_row))
    populated: list[tuple[int, float]] = []
    for r in components:
        val, cell = value_at(r)
        if cell_state(cell) in ("unreadable", "recovered"):
            return {"verdict": "not_checkable",
                    "reason": "another figure in this section is still unreadable",
                    "total_label": label(total_row)}
        if val is not None:
            populated.append((r, val))
    if len(populated) < 3:
        return {"verdict": "not_checkable", "reason": "too few figures to check",
                "total_label": label(total_row)}

    def verdict(rows: list[tuple[int, float]]) -> dict[str, Any] | None:
        computed = sum(v for _, v in rows)
        if abs(computed - printed) <= _tolerance(printed):
            return {"verdict": "ties", "printed": printed, "computed": computed,
                    "difference": round(printed - computed, 4),
                    "total_label": label(total_row)}
        return None

    ok = verdict(populated) or verdict([(r, v) for r, v in populated if label(r).strip()])
    if ok:
        return ok
    computed = sum(v for _, v in populated)
    return {"verdict": "does_not_tie", "printed": printed, "computed": computed,
            "difference": round(printed - computed, 4), "total_label": label(total_row),
            "note": "not confirmed by this column's arithmetic (simple check)"}


# ---------------------------------------------------------------------------
# What the UI and the model are told
# ---------------------------------------------------------------------------

def cells_for_table(quality: dict[str, Any], doc_id: str, stored_table_id: str,
                    table_md: str) -> list[dict[str, Any]]:
    """The flagged cells of one table, addressed by coordinates -- so the UI
    never has to parse a marker (which fails on duplicate row labels such as
    "Additions" printed twice)."""
    short = short_table_id(doc_id, stored_table_id)
    lines = (table_md or "").split("\n")
    out: dict[tuple[int, int], dict[str, Any]] = {}

    def text_at(r: int, c: int) -> str | None:
        try:
            return split_row(lines[r + 2])[c]
        except (IndexError, TypeError):
            return None

    for e in (quality or {}).get("unreadable_cells") or []:
        if not _same_table(e.get("table_id"), short, stored_table_id):
            continue
        r, c = e.get("row_index"), e.get("col_index")
        if r is None or c is None:
            continue
        text = text_at(r, c)
        if text is None or cell_state(text) not in ("unreadable", "recovered"):
            continue
        out[(r, c)] = {
            "row_index": r, "col_index": c, "state": cell_state(text), "marker": text,
            "recovered_text": e.get("recovered_text"), "confidence": e.get("confidence"),
            "row_label": e.get("row_label"), "column": e.get("column"),
        }
    for rec in active_edits(quality):
        if rec.get("table") != short:
            continue
        r, c = rec["row_index"], rec["col_index"]
        text = text_at(r, c)
        if text is None or cell_state(text) != "user_entered":
            continue
        out[(r, c)] = {
            "row_index": r, "col_index": c, "state": "user_entered", "marker": text,
            "recovered_text": rec.get("recovered_text"), "confidence": rec.get("confidence"),
            "row_label": rec.get("row_label"), "column": rec.get("column"),
            "edit": {"value": rec.get("value"), "by": rec.get("by"), "at": rec.get("at"),
                     "footing": rec.get("footing"),
                     "original_marker": rec.get("original_marker")},
        }
    return sorted(out.values(), key=lambda x: (x["row_index"], x["col_index"]))


def user_edits_note(quality: dict[str, Any] | None, doc_id: str,
                    stored_table_id: str) -> str:
    """A DATA QUALITY NOTE line for one table, or "" -- appended after the
    table text a tool returns. Prompt rule 17 already requires the model to
    reproduce such notes, so the disclosure travels with the figure into the
    answer. Not a ``|`` line, so ``parse_table_md`` ignores it."""
    short = short_table_id(doc_id, stored_table_id)
    mine = [e for e in active_edits(quality) if e.get("table") == short]
    if not mine:
        return ""
    items = "; ".join(
        f'row "{e.get("row_label") or "?"}", col "{e.get("column") or "?"}" = {e.get("value")}'
        f' (previously {e.get("original_state") or "unreadable"})'
        for e in mine
    )
    return (
        f"DATA QUALITY NOTE: {len(mine)} figure(s) in this table were entered by the "
        f"user from the scan, not read by the extraction: {items}. Say so wherever you "
        "rely on them."
    )


# ---------------------------------------------------------------------------
# Re-uploading the same file must not silently wipe a person's work
# ---------------------------------------------------------------------------

def carry_forward(old_quality: dict[str, Any] | None, new_document: Any) -> tuple[int, int]:
    """Re-apply the previous extraction's active edits onto a freshly ingested
    document, mutating ``new_document.tables`` and ``new_document.quality`` in
    place. Returns ``(applied, dropped)``.

    An edit is re-applied ONLY where the new extraction's cell at the same
    coordinates still holds exactly the marker it originally replaced --
    otherwise it is dropped, and the count is reported by the caller, so a loss
    is visible rather than silent. The same scope guard as a live edit runs, so
    an edit can never be applied to a cell the new extraction read cleanly."""
    old_edits = active_edits(old_quality)
    if not old_edits:
        return 0, 0
    by_id = {t.get("table_id"): t for t in new_document.tables}
    applied = dropped = 0
    quality = copy.copy(new_document.quality or {})
    for rec in old_edits:
        stored = rec.get("stored_table_id") or f"{new_document.doc_id}_{rec['table']}"
        table = by_id.get(stored)
        if table is None:
            dropped += 1
            continue
        try:
            new_md, quality, result = apply(
                table.get("table_md") or "", quality, doc_id=new_document.doc_id,
                stored_table_id=stored, user_id=rec.get("by") or "carried-forward",
                request={"row_index": rec["row_index"], "col_index": rec["col_index"],
                         "expected_cell": rec["original_marker"], "action": "set",
                         "value": rec.get("value")},
            )
        except EditError:
            dropped += 1
            continue
        table["table_md"] = new_md
        # Keep the original history and attribution rather than restarting it.
        for i, e in enumerate(quality.get("user_edits") or []):
            if e.get("table") == rec["table"] and e.get("row_index") == rec["row_index"] \
                    and e.get("col_index") == rec["col_index"] and e.get("active", True):
                merged = dict(e)
                merged["history"] = list(rec.get("history") or []) + [
                    {"action": "carried_forward", "at": time.time(), "by": rec.get("by"),
                     "value": rec.get("value")}]
                merged["by"], merged["at"] = rec.get("by"), rec.get("at")
                quality["user_edits"][i] = merged
        applied += 1
    new_document.quality = quality
    return applied, dropped
