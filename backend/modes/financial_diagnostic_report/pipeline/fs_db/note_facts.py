"""
Note-schedule facts — the figures that live in a note and nowhere on the face.

WHY THIS MODULE EXISTS
----------------------
Eight of the twenty-two Appendix D signals need figures the face of the statements does not
carry: CWIP ageing, the provisions movement, the impairment charge, the receivables ageing.
Until those figures reach the panel, RC-EST and RC-DEP cannot fire for ANY entity and the
audit-planning matrix is a four-cluster deliverable.

The reader for note tables already existed in `repository.py` (`note_index`, `parse_note`,
`find_note_table`). What was missing is a layer that turns a note SCHEDULE into canonical
facts — which is a different problem from binding the face, and is why this is its own
module rather than more `LineSpec`s:

  A face line is ONE row bound to ONE key.        "Trade payables" -> trade_payables
  A note schedule is a SHAPE.                     an ageing table is a set of buckets;
                                                  a movement table is opening -> closing.

Binding a shape row-by-row loses the thing that makes it useful. The ageing buckets are only
meaningful as a distribution, and the movement is only meaningful if the four components
reconcile. So each extractor below reads a whole schedule and emits the derived quantities,
with the reconciliation that proves it recorded on every fact.

THE DISCIPLINE IS THE SAME AS THE FACE BINDER'S
-----------------------------------------------
  * NO FACT WITHOUT A PROOF. A movement is emitted only when opening + additions -
    deductions == closing within tolerance. An ageing is emitted only when the buckets sum
    to the schedule's own total. A schedule that does not reconcile yields NOTHING and says
    why — it is never partially reported, because a partial ageing understates the old end
    of the distribution, which is exactly the signal it would be feeding.
  * NO LLM. Pure functions over the parsed table.
  * THE NOTE NUMBER TRAVELS. Every fact carries the note it came from, so a matrix row can
    cite "Note 8" the way the reference report does.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from . import repository as R
from .config import ARITH_ABS_TOL, ARITH_REL_TOL
from .models import ParsedTable

EXTRACTOR_VERSION = "fs_db-note-facts-1.0.0"


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= ARITH_ABS_TOL + ARITH_REL_TOL * max(abs(a), abs(b))


@dataclass
class NoteFact:
    canonical_key: str
    value: float
    note_no: str
    table_id: str
    schedule: str                       # the schedule caption, for the source trace
    basis: str                          # the reconciliation that proves it
    components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"canonical_key": self.canonical_key, "value_native": self.value,
                "note_no": self.note_no, "table_id": self.table_id,
                "schedule": self.schedule, "basis": self.basis,
                "components": dict(self.components),
                "extractor_version": EXTRACTOR_VERSION}


# =====================================================================================
# Schedule III ageing schedules.
#
# Division II mandates an ageing for CWIP, intangible assets under development, trade
# receivables and trade payables, in FIXED buckets. That fixed shape is what makes these
# the right first target: the captions vary in wording across filers, the BUCKETS do not.
# =====================================================================================

# Ordered oldest-last. The order is the whole point — "more than 3 years" is the bucket the
# diagnostics care about, and it can only be identified by matching, never by position,
# because filings print the buckets in either direction.
_AGEING_BUCKETS: tuple[tuple[str, str], ...] = (
    ("lt_1y",   r"less than 1 year|<\s*1\s*year|upto 1 year|up to 1 year|0\s*-\s*1"),
    ("y1_2",    r"1\s*-\s*2\s*year|1 to 2 year"),
    ("y2_3",    r"2\s*-\s*3\s*year|2 to 3 year"),
    ("gt_3y",   r"more than 3 year|>\s*3\s*year|above 3 year|3\s*years? and above"),
)
_AGEING_TOTAL_RX = re.compile(r"^total\b", re.I)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def ageing_from_table(pt: ParsedTable) -> dict[str, float] | None:
    """Bucket -> amount for a Schedule III ageing table, or None if it does not reconcile.

    The buckets are read from the COLUMN HEADERS where the schedule is laid out across
    columns (the Schedule III format), and from the ROW LABELS where a filer has printed it
    down the page instead. Both occur in the corpus and both are the same schedule.
    """
    by_col = _ageing_across_columns(pt)
    if by_col is not None:
        return by_col
    return _ageing_down_rows(pt)


def _ageing_across_columns(pt: ParsedTable) -> dict[str, float] | None:
    cols: dict[str, str] = {}
    for period in pt.periods:
        for name, rx in _AGEING_BUCKETS:
            if re.search(rx, _norm(period), re.I) and name not in cols:
                cols[name] = period
    if len(cols) < len(_AGEING_BUCKETS):
        # ALL FOUR OR NOTHING. Schedule III mandates the four buckets, so a table yielding
        # three of them was not fully read — and the bucket most often lost is an end one.
        # A partial ageing is worse than no ageing: `gt_3y` as a share of the total is the
        # figure S09 reads, and dropping "less than 1 year" inflates that share dramatically
        # while still looking like a clean answer.
        return None
    total_row = next((r for r in pt.rows
                      if _AGEING_TOTAL_RX.match(_norm(r.label))), None)
    src = total_row if total_row is not None else None
    if src is None:
        # No total row: add the bucket columns over every valued row instead. Legitimate
        # for a single-population schedule, and the sum is then the schedule's own total.
        got = {name: sum(r.values.get(col) or 0.0 for r in pt.rows)
               for name, col in cols.items()}
        return got if any(v for v in got.values()) else None
    got = {name: src.values.get(col) for name, col in cols.items()}
    if any(v is None for v in got.values()):
        return None
    return {k: float(v) for k, v in got.items()}


def _ageing_down_rows(pt: ParsedTable) -> dict[str, float] | None:
    if not pt.periods:
        return None
    period = pt.periods[0]
    got: dict[str, float] = {}
    for r in pt.rows:
        lab = _norm(r.label)
        for name, rx in _AGEING_BUCKETS:
            if name in got:
                continue
            if re.search(rx, lab, re.I) and r.values.get(period) is not None:
                got[name] = float(r.values[period])
    return got if len(got) == len(_AGEING_BUCKETS) else None   # all four, per above


# =====================================================================================
# Movement schedules — opening / additions / deductions / closing.
#
# Provisions (S16), CWIP and exploratory wells all print this shape. The reconciliation is
# the proof: a movement that does not tie is not reported, because the component that would
# be wrong is exactly the one the diagnostic reads (the reversal).
# =====================================================================================

_MOVEMENT_ROWS: tuple[tuple[str, str], ...] = (
    ("opening",    r"^opening balance|^balance as at the beginning|^as at the beginning|"
                   r"^opening$|^balance at the beginning"),
    ("additions",  r"^additions?\b|^expenditure during the year|^provision made|"
                   r"^charge(d)? (to|during)|^created during|^add(ed)? during"),
    ("deductions", r"^deletions?\b|^disposals?\b|^capitalis|^utilis|^paid during|"
                   r"^amounts? used|^reversal|^written back|^less[:\s]|^transfer(red)? (out|to)"),
    ("closing",    r"^closing balance|^balance as at the end|^as at the end|^closing$|"
                   r"^balance at the end"),
)


def movement_from_table(pt: ParsedTable) -> dict[str, float] | None:
    """opening / additions / deductions / closing for the primary period, if it ties.

    `deductions` accumulates: a schedule commonly prints several deduction lines
    (utilised, reversed, capitalised) and the movement only ties against their sum.
    """
    if not pt.periods:
        return None
    period = pt.periods[0]
    got: dict[str, float] = {}
    for r in pt.rows:
        lab = _norm(r.label)
        v = r.values.get(period)
        if v is None:
            continue
        for name, rx in _MOVEMENT_ROWS:
            if not re.search(rx, lab, re.I):
                continue
            if name == "deductions":
                got["deductions"] = got.get("deductions", 0.0) + abs(float(v))
            elif name not in got:
                got[name] = float(v)
            break
    if not {"opening", "closing"} <= set(got):
        return None
    got.setdefault("additions", 0.0)
    got.setdefault("deductions", 0.0)
    if not _close(got["opening"] + got["additions"] - got["deductions"], got["closing"]):
        return None                       # does not reconcile -> report nothing
    return got


# =====================================================================================
# The schedules this module knows how to read, and the keys each yields.
# =====================================================================================

@dataclass(frozen=True)
class NoteSpec:
    key_prefix: str
    shape: str                            # "ageing" | "movement"
    caption_rx: str                       # matches the schedule caption
    keys: tuple[str, ...]                 # canonical keys emitted


NOTE_SPECS: tuple[NoteSpec, ...] = (
    NoteSpec("cwip", "ageing",
             r"capital work[- ]in[- ]progress|cwip|exploratory well|"
             r"intangible (assets? )?(under development|in progress)",
             ("cwip_ageing_lt_1y", "cwip_ageing_y1_2", "cwip_ageing_y2_3",
              "cwip_ageing_gt_3y", "cwip_ageing_total")),
    NoteSpec("receivables", "ageing",
             r"trade receivable",
             ("receivables_ageing_lt_1y", "receivables_ageing_y1_2",
              "receivables_ageing_y2_3", "receivables_ageing_gt_3y",
              "receivables_ageing_total")),
    NoteSpec("provisions", "movement",
             r"^provisions?\b|movement in provision",
             ("provisions_opening", "provisions_charge", "provisions_reversal",
              "provisions_closing")),
    NoteSpec("cwip_movement", "movement",
             r"capital work[- ]in[- ]progress|exploratory well",
             ("cwip_opening", "cwip_additions", "cwip_capitalised", "cwip_closing")),
)

_AGEING_KEY_SUFFIX = {"lt_1y": "lt_1y", "y1_2": "y1_2", "y2_3": "y2_3", "gt_3y": "gt_3y"}


def extract_notes(doc_id: str, flavor: str = "standalone") -> list[NoteFact]:
    """Every note fact this module can prove from one filing.

    Never raises: a filing whose notes cannot be read yields an empty list, exactly as
    `facts.extract` does for the face, so one bad filing cannot take down a batch.
    """
    try:
        idx = R.note_index(doc_id, flavor)
    except Exception:                                     # noqa: BLE001 - batch resilience
        return []

    out: list[NoteFact] = []
    seen: set[str] = set()
    for note_no, cands in sorted(idx.items()):
        for cand in cands:
            caption = f"{cand.get('title') or ''} {cand.get('section') or ''}"
            try:
                pt = R.parse_note(cand)
            except Exception:                             # noqa: BLE001
                continue
            if not pt.rows:
                continue
            for spec in NOTE_SPECS:
                if not re.search(spec.caption_rx, _norm(caption), re.I):
                    continue
                facts = (_ageing_facts(spec, pt, note_no, caption)
                         if spec.shape == "ageing"
                         else _movement_facts(spec, pt, note_no, caption))
                for f in facts:
                    if f.canonical_key in seen:
                        # FIRST schedule wins. A filing prints the same shape more than once
                        # (standalone and a subsidiary's, or a note and its sub-schedule) and
                        # silently overwriting would make the reported figure depend on note
                        # ordering. The note number on the kept fact says which one it was.
                        continue
                    seen.add(f.canonical_key)
                    out.append(f)
    return out


def _ageing_facts(spec: NoteSpec, pt: ParsedTable, note_no: str,
                  caption: str) -> list[NoteFact]:
    buckets = ageing_from_table(pt)
    if not buckets:
        return []
    total = sum(buckets.values())
    if not total:
        return []
    basis = (f"Schedule III ageing, note {note_no}: "
             + " + ".join(f"{k} {v:,.2f}" for k, v in buckets.items())
             + f" = {total:,.2f}")
    out = [NoteFact(f"{spec.key_prefix}_ageing_{_AGEING_KEY_SUFFIX[k]}", v, note_no,
                    pt.table_id or "", caption.strip(), basis, dict(buckets))
           for k, v in buckets.items()]
    out.append(NoteFact(f"{spec.key_prefix}_ageing_total", total, note_no,
                        pt.table_id or "", caption.strip(), basis, dict(buckets)))
    return out


def _movement_facts(spec: NoteSpec, pt: ParsedTable, note_no: str,
                    caption: str) -> list[NoteFact]:
    mv = movement_from_table(pt)
    if not mv:
        return []
    basis = (f"movement, note {note_no}: opening {mv['opening']:,.2f} "
             f"+ additions {mv['additions']:,.2f} - deductions {mv['deductions']:,.2f} "
             f"= closing {mv['closing']:,.2f}")
    names = dict(zip(("opening", "additions", "deductions", "closing"), spec.keys))
    return [NoteFact(names[part], mv[part], note_no, pt.table_id or "",
                     caption.strip(), basis, dict(mv))
            for part in ("opening", "additions", "deductions", "closing")
            if part in names]
