"""A second, independent read of each table by the vision model.

Docling's TableFormer and an OCR engine share a failure mode: they are confident
about a grid they inferred, and neither can tell you when the grid was wrong.
Two things guard against that. The arithmetic in ``verify.py`` is one. This is
the other -- a genuinely independent read of the same pixels by a different kind
of model, so that agreement between them is evidence rather than repetition.

It talks to the same vLLM endpoint the Financial Statement agent generates
against (``LLM_BASE_URL`` / ``gemma-4-26b-a4b-it``), through the OpenAI-shaped
``/v1/chat/completions`` image content part. Behind a capability probe: if the
served model turns out to be text-only, the whole stage is skipped, the fact is
recorded on the quality report as ``vlm_used=False``, and verification falls
back to arithmetic alone. That degradation is deliberate -- a missing second
opinion should narrow what the pipeline will vouch for, not stop it.

The prompt is written to make the model a *transcriber*, not an analyst. It is
told the docling read as a starting point and asked to correct it against the
image. Analysis, inference and gap-filling are all forbidden, because the one
thing worse than a cell this pipeline cannot read is a cell it invents.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .config import Config
from .numbers import parse_cell
from .structure_repair import (
    _header_bottom, _median, _column_ranges, _cluster_rows,
    _anchor_lines, _filter_lines, _label_x, is_continuation_fragment,
)
from .tables import Table, split_row, is_separator, _ENUM_MARKER_RE

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:  # pragma: no cover
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:  # pragma: no cover
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


_SYSTEM = (
    "You transcribe tables from scanned Indian financial statements. You are a "
    "transcriber, not an analyst."
)

_INSTRUCTION = """Transcribe this table from the image into a GitHub-flavoured markdown pipe table.

Rules, in order of importance:
1. Copy what is printed. Do not compute, correct, complete or infer any value.
2. If a cell is illegible, write exactly ILLEGIBLE. Never guess a digit. Never
   substitute a value that would make a column add up.
3. If a cell is empty or prints a dash, write a single dash.
4. Keep Indian digit grouping exactly as printed (20,17,448 stays 20,17,448).
5. Keep negatives as printed, in parentheses. If a closing parenthesis is cut
   off by the table border, write it as printed with the opening parenthesis
   only -- do not add the missing one.
6. Keep every row, including unlabelled subtotal rows and blank label cells.
7. Keep the same columns, in the same order, as the previous read below. Do not
   merge two columns into one and do not drop a column that is empty.
8. Put column titles and any units line ("Amount in '000") in the header, not
   in a body row.
9. Output only the markdown table. No commentary, no explanation, no fences.

A previous automated read of this table is given below. It may contain errors.
Correct it against the image; do not simply repeat it.

PREVIOUS READ:
"""

#: The blind variant of the instruction above -- no seed, no column pinning to
#: another reader's grid. This is the PRIMARY prompt now (Config.VLM_BLIND_READ
#: defaults True): pasting docling's markdown into the prompt and asking the
#: model to "correct" it, as the primed variant does, makes the two readers'
#: agreement evidence of nothing -- a model shown the answer and asked to
#: confirm it is not reading independently. The primed prompt is kept only for
#: a caller that explicitly wants a corrective pass against a known read.
_BLIND_INSTRUCTION = """Transcribe this table from the image into a GitHub-flavoured markdown pipe table.

Rules, in order of importance:
1. Copy what is printed. Do not compute, correct, complete or infer any value.
2. If a cell is illegible, write exactly ILLEGIBLE. Never guess a digit. Never
   substitute a value that would make a column add up.
3. If a cell is empty or prints a dash, write a single dash.
4. Keep Indian digit grouping exactly as printed (20,17,448 stays 20,17,448).
5. Keep negatives as printed, in parentheses. If a closing parenthesis is cut
   off by the table border, write it as printed with the opening parenthesis
   only -- do not add the missing one.
6. Keep every row, including unlabelled subtotal rows and blank label cells.
7. Read the columns exactly as printed in the image. Do not merge two columns
   into one and do not drop a column that looks empty.
8. Put column titles and any units line ("Amount in '000") in the header, not
   in a body row.
9. Output only the markdown table. No commentary, no explanation, no fences.

You are not given, and must not assume, any other transcription of this
table. Read only what is in the image."""


class VlmUnavailable(RuntimeError):
    pass


def _encode(image: "np.ndarray") -> str:
    buffer = io.BytesIO()
    Image.fromarray(image).convert("L").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if Config.VLM_API_KEY:
        headers["Authorization"] = f"Bearer {Config.VLM_API_KEY}"
    return headers


def probe() -> bool:
    """Ask the endpoint, once, whether it accepts an image at all.

    A 1x1 pixel is enough: a text-only server rejects the multimodal content
    part on shape, before it ever looks at the image. Doing this at startup
    rather than per table means one clear log line instead of a failure on every
    table of every upload.
    """
    if not Config.vlm_configured() or httpx is None or Image is None:
        return False
    try:
        buffer = io.BytesIO()
        Image.new("L", (1, 1), 255).save(buffer, format="PNG")
        pixel = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return False

    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=30.0,
            json={
                "model": Config.VLM_MODEL,
                "max_tokens": 1,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{pixel}"}},
                    ],
                }],
            },
        )
    except Exception as exc:
        logger.warning("VLM probe failed to reach %s: %s", Config.VLM_BASE_URL, exc)
        return False

    if response.status_code >= 400:
        logger.warning(
            "VLM probe rejected by %s (%s). Falling back to docling-only extraction.",
            Config.VLM_MODEL, response.status_code,
        )
        return False
    return True


def perceives() -> bool:
    """Whether the model can actually SEE an image, not merely accept one.

    ``probe()`` answers a narrower question -- does the endpoint accept a
    multimodal request -- and on the live deployment that answer was yes while
    the model was effectively blind. Measured directly: shown an image printing
    "HELLO 12345 TOTAL" it replied "text"; shown a full balance sheet it said
    "no image was provided"; and pushed to transcribe anyway it invented a
    complete income statement ("Total Revenue 1,15,44,822 ... Profit After Tax
    9,00,000") that appears nowhere in the filing. Every probe passed.

    So this prints a RANDOM six-digit number and requires it read back exactly.
    Random so it cannot be recalled or guessed (one chance in 900,000); exact
    because "close" is what a model hallucinating a plausible figure produces.
    Any failure -- a wrong read, an unreachable endpoint, an image that could
    not be drawn -- returns False, because the cost of wrongly skipping a
    second reader is a narrower corroboration, while the cost of trusting a
    blind one is invented rows in an auditor's tables.
    """
    if not Config.vlm_configured() or httpx is None or Image is None or np is None:
        return False

    import secrets

    secret = str(secrets.randbelow(900000) + 100000)
    try:
        from PIL import ImageDraw, ImageFont

        canvas = Image.new("L", (640, 160), 255)
        try:
            font = ImageFont.load_default(size=72)
            ImageDraw.Draw(canvas).text((40, 40), secret, fill=0, font=font)
        except Exception:
            # Pillow older than 10.1 has no sized default font: draw small and
            # scale up, which stays crisp with nearest-neighbour resampling.
            small = Image.new("L", (80, 20), 255)
            ImageDraw.Draw(small).text((4, 4), secret, fill=0)
            canvas = small.resize((640, 160), Image.NEAREST)
        image = np.array(canvas)
    except Exception as exc:
        logger.warning("VLM perception check could not draw its test image: %s", exc)
        return False

    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=min(Config.VLM_TIMEOUT, 60.0),
            json={
                "model": Config.VLM_MODEL,
                "temperature": 0.0,
                "max_tokens": 20,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": "Read the number printed in this image. "
                                 "Reply with the digits only."},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{_encode(image)}"}},
                    ],
                }],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        logger.warning("VLM perception check could not run: %s", exc)
        return False

    digits = "".join(ch for ch in content if ch.isdigit())
    if digits == secret:
        return True
    logger.warning(
        "VLM perception check FAILED: the image printed %s and %s read %r. The "
        "endpoint accepts images but the model is not perceiving them; the "
        "second read is disabled rather than trusted.",
        secret, Config.VLM_MODEL, content[:80],
    )
    return False


def crop(page_image: "np.ndarray", bbox: list[float] | None,
         page_height_pt: float | None = None) -> "np.ndarray":
    """Crop a table's bounding box out of the rendered page.

    Two coordinate conversions happen here, and BOTH are mandatory:

    * **Points to pixels.** Docling reports the box in PDF points against the
      synthesized PDF ``pages_to_pdf`` builds, which is written at
      ``resolution=Config.RENDER_DPI`` -- so a page ``h`` pixels tall is
      ``h * 72 / RENDER_DPI`` points tall and the scale is ``RENDER_DPI / 72``
      (4.167 at 300 DPI).
    * **The Y flip.** Docling uses ``coord_origin=BOTTOMLEFT`` while an image
      array is indexed from the top.

    ``page_height_pt`` is derived from the image rather than taken on trust,
    because it used to be a required argument that the single call site passed
    as ``None`` -- which silently took an "already in pixels" branch and
    returned a box in the wrong coordinate space entirely. Measured on the MH
    2024-25 filing that produced a crop covering 21% of the page: blank left
    margin and scan noise, not one figure of the table it claimed to be. The
    vision model was then "corroborating" figures it could not see, and every
    citation snippet showed the wrong region of the scan. Deriving the height
    here means no caller can reintroduce that.

    A small margin is added because TableFormer's box hugs the cell grid, and
    the caption and unit line ("Amount in '000") sit just outside it -- and the
    unit line is precisely what stops a figure being wrong by three orders of
    magnitude.
    """
    if bbox is None or np is None:
        return page_image

    height, width = page_image.shape[:2]
    left, top, right, bottom = bbox

    if not page_height_pt:
        page_height_pt = height * 72.0 / float(Config.RENDER_DPI or 72)

    scale = height / page_height_pt
    y0 = height - (top * scale)
    y1 = height - (bottom * scale)
    x0, x1 = left * scale, right * scale

    if y0 > y1:
        y0, y1 = y1, y0

    # A box mostly off the page is a coordinate-space mistake, not a table near
    # the edge. Clamping it would hand back a sliver of margin that looks like
    # a real crop -- which is exactly how the previous bug stayed invisible.
    if x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
        return page_image
    inside = (min(x1, width) - max(x0, 0)) * (min(y1, height) - max(y0, 0))
    claimed = max(1.0, (x1 - x0) * (y1 - y0))
    if inside / claimed < 0.5:
        return page_image
    margin = int(0.03 * height)
    y0 = max(0, int(y0) - margin)
    y1 = min(height, int(y1) + margin)
    x0 = max(0, int(x0) - margin)
    x1 = min(width, int(x1) + margin)

    # A box that lands mostly outside the page is a coordinate-space mistake,
    # not a small table. Returning the whole page is the honest degradation --
    # the reader sees more than the table rather than a strip of margin, and
    # the failure cannot masquerade as a confident second read of nothing.
    if y1 - y0 < 20 or x1 - x0 < 20:
        return page_image
    return page_image[y0:y1, x0:x1]


def snippet(image: "np.ndarray", max_width: int = 1100, quality: int = 72) -> str | None:
    """A base64 JPEG of an image, for a reader to look at rather than re-OCR.

    Generic over any array, not table-specific -- used for a table's cropped
    region (the citation viewer's default 1100px/q72) and, at a caller-chosen
    lower width and quality, for a whole corrected page (the document pane's
    page view, see ``emit.build_page_image``). Produced whether or not the
    vision model is reachable: the reader's need to see the scan does not
    depend on whether a second model read it. Downscaled and JPEG-compressed
    hard in both uses because this is evidence to look at, not to OCR again --
    a 300 DPI crop of a full-page table is ~1.5MB raw and ~60KB at these
    settings, and the gateway holds up to twelve documents per user in memory.
    """
    if Image is None:
        return None
    try:
        frame = Image.fromarray(image).convert("L")
        if frame.width > max_width:
            height = int(frame.height * max_width / frame.width)
            frame = frame.resize((max_width, height), Image.LANCZOS)
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def transcribe(image: "np.ndarray", previous_markdown: str | None = None) -> str | None:
    """One table image to markdown. ``None`` if the model could not be reached.

    ``previous_markdown`` is optional and, by default (``Config.VLM_BLIND_READ``),
    not used: omitting it (``None``) reads the image cold, with no seed and no
    column pinning to another reader's grid, which is what makes the resulting
    read independent evidence rather than a corrected echo. Pass a markdown
    string only for an explicit corrective pass against a known read -- the
    original behaviour, kept for callers that want it.
    """
    if httpx is None or Image is None:
        return None
    instruction = (
        _INSTRUCTION + previous_markdown if previous_markdown else _BLIND_INSTRUCTION
    )
    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=Config.VLM_TIMEOUT,
            json={
                "model": Config.VLM_MODEL,
                # Deterministic: this is transcription, and sampling variety is
                # nothing but a chance to read a digit differently each run.
                "temperature": 0.0,
                # A 9-column PPE roll-forward over 20 rows is comfortably four
                # figures of markdown, and a table cut off mid-transcription is
                # worse than no transcription: every row past the cut has no
                # second opinion while looking exactly like one that agreed.
                # Detected below rather than trusted not to happen.
                "max_tokens": Config.VLM_MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_encode(image)}"}},
                    ]},
                ],
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        # `length` means the model ran out of room mid-table. compare() would
        # otherwise align the prefix it did send and treat the missing tail as
        # rows with no second opinion -- withholding real figures because of a
        # token budget. Discarding the whole read falls back to arithmetic,
        # which is the honest answer when the second reader was cut off.
        if choice.get("finish_reason") == "length":
            logger.warning(
                "VLM transcription truncated at %d tokens; discarding the partial "
                "read rather than comparing against a prefix", Config.VLM_MAX_TOKENS,
            )
            return None
    except Exception as exc:
        logger.warning("VLM transcription failed: %s", exc)
        return None

    return _strip_fences(content or "")


def _strip_fences(text: str) -> str:
    text = re.sub(r"^\s*```[a-zA-Z]*\s*", "", text.strip())
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


#: Below this share of docling's labelled rows matching, the two tables are not
#: describing the same grid and no cell-level conclusion can be drawn from them.
#: Not a quality bar -- a structural one. See Alignment.usable.
_MIN_MATCH_RATIO = 0.5

#: Characters OCR confuses often enough that a label differing only by these is
#: the same label. Mirrors _OCR_LOOKALIKES in numbers.py, applied to text rather
#: than to digits.
_LABEL_LOOKALIKES = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "2": "z"})


class Alignment:
    """How the two reads line up, and whether they line up at all.

    Three states, which the old ``(agreement, disagreements)`` pair could not
    express and which verify.py needs to keep apart:

    ``usable=False``
        No conclusion is possible -- the read was absent, unparseable,
        truncated, or describes a different grid. The table falls back to
        arithmetic-only verification. This must never be confused with "every
        cell disagreed": doing so blanks a whole schedule over a dropped
        connection, which is the failure this class was introduced to end.
    ``unmatched_rows``
        The read is usable, but these specific docling rows had no counterpart
        in it. Their figures carry no second opinion.
    ``disagreements``
        Both readers found the row and read the cell differently.
    ``vlm_body`` / ``vlm_header``
        The second read's own rows and header, as parsed markdown cells. Used
        to matter only as coordinates (``disagreements``, ``unmatched_rows``);
        kept here as content too, because a row the VLM found and docling
        never emitted at all cannot be named by a coordinate into docling's
        table -- there is no such coordinate. See ``insert_unclaimed_rows``.
    ``pairs`` / ``col_map``
        docling row -> vlm_body row, and docling column -> vlm column, exactly
        as ``compare()`` resolved them. Exposed so a caller can locate the
        vision model's own text for a row or column docling paired, not just
        know that a comparison happened.
    """

    def __init__(self, agreement=0.0, disagreements=None, unmatched_rows=None,
                 usable=False, reason=None, matched=0, total=0,
                 vlm_body=None, vlm_header=None, pairs=None, col_map=None,
                 grounded=False):
        self.agreement = agreement
        self.disagreements: set[tuple[int, int]] = disagreements or set()
        self.unmatched_rows: set[int] = unmatched_rows or set()
        self.usable = usable
        self.reason = reason
        self.matched = matched
        self.total = total
        self.vlm_body: list[list[str]] = vlm_body or []
        self.vlm_header: list[str] = vlm_header or []
        self.pairs: dict[int, int] = pairs or {}
        self.col_map: dict[int, int] = col_map or {}
        # True only when the read reproduced enough of the table's own printed
        # figures to show it actually saw THIS table. A usable-but-ungrounded
        # read may still flag a disagreement, which can only withhold; it is
        # never trusted to add, merge or relabel a row, because a model that
        # cannot see invents plausible Schedule III labels that match docling's
        # well enough to pass the row-pairing threshold. Defaults False so any
        # Alignment built without this evidence is untrusted by construction.
        self.grounded = grounded


def _normalise_label(text: str) -> str:
    """A label reduced to what two readers of the same scan should agree on.

    Digits are KEPT. Stripping them collapses ``Total (1)``, ``Total (2)`` and
    ``Total (1+2)`` onto one key -- three different rows of every Schedule III
    balance sheet -- and makes the ``31st March 2025`` and ``2024`` column
    headers indistinguishable, which is precisely the confusion that would
    compare one year's figure against the other's and call it a disagreement.
    """
    lowered = (text or "").strip().lower().translate(_LABEL_LOOKALIKES)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _is_header_ish(cells: list[str]) -> bool:
    """A row that carries no figure and reads like a header or a unit line.

    docling captured ``(Amount in '000)`` as a *data row* on the MH 2024-25
    balance sheet while the vision model treated it as part of the header. That
    one-row difference shifted every subsequent row and produced 38 false
    disagreements. Both sides are trimmed the same way so the offset cannot
    arise in the first place.
    """
    joined = " ".join(cells).lower()
    if any(parse_cell(c).value is not None for c in cells):
        return False
    return bool(re.search(r"amount\s+in|rs\.?\s*in|in\s+'?000|\(₹|as\s+at|particulars", joined))


def _body_rows(rows: list[list[str]]) -> list[list[str]]:
    """Drop leading header/unit rows, not a fixed number of them."""
    i = 0
    while i < len(rows) and _is_header_ish(rows[i]):
        i += 1
    return rows[i:]


def _row_score(ours: str, their_row: list[str]) -> float:
    """How well this row of the other read matches our label. 0.0 = not at all.

    Scored rather than answered yes/no, and every cell of the row is checked
    rather than only the column docling calls the label column -- the two
    readers need not agree on which column that is, and one may not emit an
    empty leading S.N. column at all.

    An exact match outranks containment deliberately. Taking the *first*
    acceptable row rather than the *best* one is what made an earlier version
    of this worse than no lookahead at all: a loose early match consumed the
    cursor and starved every later row of its true counterpart.
    """
    if not ours:
        return 0.0
    best = 0.0
    for cell in their_row:
        theirs = _normalise_label(cell)
        if not theirs:
            continue
        if ours == theirs:
            return 1.0
        # Edit-distance similarity, not exact or containment matching. The
        # whole point of anchoring on labels is that they survive a bad scan,
        # and what "survives" actually looks like is character corruption:
        # docling read "Capltal work in prograss" and "Investment Propertles"
        # on this document -- one or two wrong letters in the MIDDLE of the
        # word, which exact and containment matching both miss entirely while
        # a human reads straight through them.
        best = max(best, SequenceMatcher(None, ours, theirs).ratio())
    return best


#: How far ahead to look for a row's counterpart. Bounded because the two reads
#: are in the same document order: a match twenty rows away is a coincidence,
#: not the same row.
_MATCH_WINDOW = 8
#: Containment weaker than this is not a match. Tuned so "Total (1)" does not
#: pair with "Total (1+2)".
_MIN_ROW_SCORE = 0.72


#: A read compared on at least this many figures, agreeing on fewer than
#: `_MIN_GROUNDED_AGREEMENT` of them, is PROVEN not to be a read of this table
#: and is discarded. Below this count there is too little evidence either way
#: to call it blind.
_MIN_GROUNDING_CELLS = 3
_MIN_GROUNDED_AGREEMENT = 0.5
#: Exact agreement on this many specific printed figures is treated as proof of
#: sight. Two is enough: a blind model does not reproduce "3,19,452.35" and
#: "1,89,034.64" by chance, and requiring more would leave small note tables
#: permanently ungrounded.
_MIN_GROUNDING_AGREED = 2


def compare(docling_table: Table, vlm_markdown: str) -> Alignment:
    """How the two reads line up, cell by cell, anchored on row labels.

    Compared on **parsed values, not on strings**. The two readers format
    differently -- one writes ``(1,757)`` and the other ``-1757`` for the same
    figure -- and a string comparison would call every negative a disagreement
    and drown the real ones. What matters is whether they read the same number.

    Rows are paired by **label**, never by position. Position was the original
    design and it is wrong for the reason every scanned filing demonstrates:
    the two readers do not always agree on how many rows a table has, and one
    extra row at the top silently re-pairs every figure in the table with its
    neighbour. Labels survive OCR far better than grid geometry does -- on the
    document that prompted this, every label matched while the row count did
    not -- so they are the reliable anchor.

    Cells where one reader saw nothing are not counted either way: an absent
    cell is a coverage difference, not a contradiction about a value.
    """
    vlm_rows: list[list[str]] = []
    for line in (vlm_markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = split_row(stripped)
        if is_separator(cells):
            continue
        vlm_rows.append(cells)

    if len(vlm_rows) < 2:
        return Alignment(usable=False, reason="second read did not contain a table")

    vlm_body = _body_rows(vlm_rows)
    if not vlm_body:
        return Alignment(usable=False, reason="second read contained no body rows")

    # Pair rows by label, in order. Monotonic on both sides: a label may only
    # match forwards, so a repeated label ("Additions" appears twice in a PPE
    # schedule) pairs with the next unused occurrence rather than the first.
    pairs: dict[int, int] = {}
    cursor = 0
    labelled = 0
    for r in range(len(docling_table.rows)):
        # Header and unit rows are excluded on BOTH sides, not just the other
        # reader's. docling captured "(Amount in '000)" as a data row on the
        # document that prompted this; counting it as a row needing a
        # counterpart would report it unmatched forever, and under a
        # withhold-unmatched policy would withhold a figure it does not have.
        if _is_header_ish(docling_table.rows[r]):
            continue
        ours = _normalise_label(docling_table.label(r))
        if not ours:
            continue
        labelled += 1
        best_j, best_score = None, 0.0
        for j in range(cursor, min(cursor + _MATCH_WINDOW, len(vlm_body))):
            score = _row_score(ours, vlm_body[j])
            if score > best_score:
                best_j, best_score = j, score
            if score == 1.0:
                break
        if best_j is not None and best_score >= _MIN_ROW_SCORE:
            pairs[r] = best_j
            cursor = best_j + 1

    if labelled == 0:
        return Alignment(usable=False, reason="no labelled rows to align on")
    if len(pairs) < _MIN_MATCH_RATIO * labelled:
        return Alignment(
            usable=False, matched=len(pairs), total=labelled,
            reason=(f"only {len(pairs)} of {labelled} rows could be matched between "
                    "the two reads; they do not describe the same table"),
        )

    # Columns are matched by header text where both sides have one, because
    # docling's column index means nothing in the other reader's grid -- it may
    # have merged an empty leading column or split the label.
    col_map = _map_columns(docling_table, vlm_rows[0] if vlm_rows else [])

    compared = 0
    agreed = 0
    disagreements: set[tuple[int, int]] = set()

    for r, j in pairs.items():
        for c in docling_table.value_cols:
            their_c = col_map.get(c)
            if their_c is None:
                continue
            ours = docling_table.cell(r, c).value
            theirs_raw = vlm_body[j][their_c] if their_c < len(vlm_body[j]) else ""
            if theirs_raw.strip().upper() == "ILLEGIBLE":
                # The VLM saying it cannot read a cell is information, and it is
                # a disagreement only if we claimed to read one.
                if ours is not None:
                    compared += 1
                    disagreements.add((r, c))
                continue
            theirs = parse_cell(theirs_raw).value
            if ours is None or theirs is None:
                continue
            compared += 1
            if abs(ours - theirs) <= max(0.01, abs(ours) * 1e-6):
                agreed += 1
            else:
                disagreements.add((r, c))

    unmatched = {r for r in range(len(docling_table.rows))
                 if r not in pairs
                 and _normalise_label(docling_table.label(r))
                 and not _is_header_ish(docling_table.rows[r])}

    agreement = (agreed / compared) if compared else 0.0

    # Proof of sight. Matching LABELS proves nothing: a model that cannot see
    # the image still writes "Share capital", "Reserves and surplus" and
    # "Total", because every Schedule III statement says those words.
    # Measured on the live deployment, a blind model invented a whole income
    # statement ("Total Revenue 1,15,44,822 ... Profit After Tax 9,00,000")
    # and its labels overlapped docling's enough to count as the same table.
    # What a blind model cannot do is reproduce the specific figures the page
    # prints. So a read that has been compared on enough figures and agrees on
    # too few of them is not a noisy second opinion -- it is a transcription of
    # a different or invented table, and it is discarded outright, which puts
    # the table back on its own arithmetic exactly as an unreachable model does.
    if compared >= _MIN_GROUNDING_CELLS and agreement < _MIN_GROUNDED_AGREEMENT:
        return Alignment(
            usable=False, agreement=agreement, matched=len(pairs), total=labelled,
            reason=(f"the second read agreed with only {agreed} of {compared} "
                    "figures printed in the table, so it describes a different "
                    "or invented table rather than this one"),
        )

    return Alignment(
        agreement=agreement,
        disagreements=disagreements,
        unmatched_rows=unmatched,
        usable=True,
        matched=len(pairs),
        total=labelled,
        vlm_body=vlm_body,
        vlm_header=vlm_rows[0] if vlm_rows else [],
        pairs=dict(pairs),
        col_map=col_map,
        # Grounded only when the read reproduced enough of the table's own
        # printed figures to be SHOWN to have seen it. With fewer than three
        # compared, reaching _MIN_GROUNDING_AGREED means every one agreed; with
        # three or more, the discard above has already required a majority.
        grounded=agreed >= _MIN_GROUNDING_AGREED,
    )


def _map_columns(docling_table: Table, vlm_header: list[str]) -> dict[int, int]:
    """docling column index -> the other read's column index.

    By header text where both sides name their columns, because the two grids
    need not have the same shape. Falls back to matching the *n*-th value
    column on each side, and only when both sides expose the same number of
    them -- guessing past that point is how a 2025 figure gets compared against
    a 2024 one and reported as a disagreement.
    """
    mapping: dict[int, int] = {}
    ours_header = docling_table.header or []

    for c in docling_table.value_cols:
        ours = _normalise_label(ours_header[c] if c < len(ours_header) else "")
        if not ours:
            continue
        for j, theirs_raw in enumerate(vlm_header):
            theirs = _normalise_label(theirs_raw)
            if theirs and (ours == theirs or ours in theirs or theirs in ours):
                mapping[c] = j
                break

    if len(mapping) == len(docling_table.value_cols):
        return mapping

    # Positional fallback, but only when the shapes agree exactly.
    their_value_cols = [
        j for j in range(len(vlm_header))
        if j not in (docling_table.label_col, docling_table.note_col)
    ]
    if len(their_value_cols) == len(docling_table.value_cols):
        return dict(zip(docling_table.value_cols, their_value_cols))
    return {c: c for c in docling_table.value_cols} if not mapping else mapping


def _label_cell(row: list[str]) -> str:
    """The most label-like cell in a VLM row.

    First cell that is non-empty, not ILLEGIBLE, and does not parse as a
    number -- the same "mostly non-numeric" reasoning tables._resolve_roles
    uses to find a label COLUMN, applied here to one row of a grid whose
    column layout was never resolved against docling's.
    """
    for cell in row:
        text = (cell or "").strip()
        if text and text.upper() != "ILLEGIBLE" and parse_cell(text).value is None:
            return text
    return row[0] if row else ""


def merge_wrapped_labels(
    table: Table, alignment: Alignment, ocr_lines=None, header_bottom: float | None = None,
) -> tuple[list[int], dict[int, int]]:
    """Rejoin a docling row that is a WRAPPED LABEL FRAGMENT with the row that
    carries the figures it belongs to.

    Real, verified shape (OD-SPSU-SO-032's Property, Plant and Equipment
    line, captured from actual docling output): the printed label wraps onto
    a second line -- "(a) Property, Plant and Equipment" / "[and Intangible
    assets]" -- and TableFormer, which relies on ruling to find row
    boundaries, reads that as two rows: one with a label and no figures at
    all, the other with the note number and both values but a label fragment
    too short to mean anything on its own. Neither row alone carries what a
    reader -- or the accuracy harness -- recognises as "Property, Plant and
    Equipment".

    Unlike ``insert_unclaimed_rows`` or any value-level arbitration, this
    chooses between NOTHING and invents NOTHING: exactly one of the two rows
    has any figure at all (checked below, not assumed), so the merged row's
    values are taken 100% from whichever row already had them, precisely as
    docling read them. The only thing that changes is the label text and the
    row count; every value keeps the verification it would have received
    anyway, since it is still exactly the value docling produced.

    Deliberately narrower than the general "several real line items merged
    onto one row" defect (see SK-SPSU-SSLSA-010's Corpus/Capital Fund row,
    ``tests/test_golden.py::SK_BALANCE_SHEET``): there, a SINGLE row already
    carries a figure across four concatenated labels, and there is no way to
    tell from the page which of the four it belongs to without a targeted
    re-read -- Phase 4 territory, not this. This function only ever fires
    where exactly one side of the pair has a figure, which is what makes it
    safe to automate today.

    Conservative by construction -- every condition below must hold:
    - Exactly one of the two adjacent rows carries a value in any value
      column; the other carries none. A row with its own figure is a real
      line item, never a fragment, whatever its label looks like.
    - EXACTLY ONE of the two rows is paired to a vision-model row. That one
      row is the anchor to check against. If BOTH are paired they are paired
      to *different* vision-model rows (``compare()``'s cursor is monotonic
      and never claims one twice) -- the second reader saw two distinct
      printed lines there, and merging would contradict its read. If NEITHER
      is paired there is no independent evidence at all, only a guess.
      Which side happens to be the paired one is deliberately not assumed:
      on the real OD document it is the label-only half that pairs, because
      its text is a clean PREFIX of the vision model's full combined label,
      while the value-bearing fragment ("[and Intangible assets]") matches
      nothing on its own.
    - The CONCATENATION of both rows' labels, in printed order, scores
      higher against that anchor than EITHER row's own label does alone, and
      clears the same ``_MIN_ROW_SCORE`` bar ``compare()`` uses to call two
      labels the same printed line. Strictly beating both is the actual proof
      this is one wrapped line: a genuine standalone item pairs to its own
      counterpart at near 1.0, and appending a neighbour's text can only
      dilute that, so this condition cannot fire on one.

    Returns the new indices of the merged rows, and an old -> new row-index
    map for every row that existed before the call. A merged pair's OLD
    indices both map to the SAME new index. Callers holding ``(row, col)`` or
    ``row`` sets computed against ``table`` and ``alignment.pairs`` /
    ``alignment.unmatched_rows`` BEFORE this call MUST remap them through it
    -- exactly the same obligation ``insert_unclaimed_rows`` documents for its
    own remap, and this function must run BEFORE that one for the same
    reason: it also changes row indices out from under a stale reference.
    """
    remap = {r: r for r in range(len(table.rows))}
    # `grounded`, not merely `usable`: this rewrites a label on the second
    # reader's word, and a blind reader's labels look exactly like real ones.
    if (not alignment.usable or not alignment.grounded
            or not alignment.vlm_body or not table.value_cols):
        return [], remap

    def row_values(r: int) -> list[float]:
        return [
            v for c in table.value_cols
            for v in [table.cell(r, c).value] if v is not None
        ]

    # Printed geometry, when the caller has it: where each row's caption
    # actually starts on the page. Computed ONCE against the table as it is
    # now -- `table.rows` is only replaced at the very end, so these indices
    # stay valid for the whole scan.
    lines = _filter_lines(ocr_lines, header_bottom) if ocr_lines else []
    anchors = _anchor_lines(table, lines) if lines else {}

    merged_new_indices: list[int] = []
    new_rows: list[list[str]] = []
    r = 0
    n = len(table.rows)
    while r < n:
        value_row = label_only_row = None
        if r + 1 < n:
            r_vals, r1_vals = row_values(r), row_values(r + 1)
            if r_vals and not r1_vals:
                value_row, label_only_row = r, r + 1
            elif r1_vals and not r_vals:
                value_row, label_only_row = r + 1, r

        do_merge = False
        if value_row is not None:
            # Exactly one of the two rows paired to a vision-model row --
            # which side happens to be it is NOT assumed (see docstring: on
            # the real document it is the label-only half). Two pairs would
            # mean the second reader saw two distinct lines; zero means no
            # independent evidence to check against at all.
            paired = [x for x in (r, r + 1) if x in alignment.pairs]
            if len(paired) == 1:
                vlm_row = alignment.vlm_body[alignment.pairs[paired[0]]]
                alone_r = _row_score(_normalise_label(table.label(r)), vlm_row)
                alone_r1 = _row_score(_normalise_label(table.label(r + 1)), vlm_row)
                combined_label = f"{table.label(r)} {table.label(r + 1)}".strip()
                combined = _row_score(_normalise_label(combined_label), vlm_row)
                # Two independent ways to prove "one wrapped line". The score
                # ordering below (the combined label must strictly beat BOTH
                # halves) cannot fire when either half already matches the
                # second reader's row exactly -- score 1.0 cannot be beaten --
                # which is precisely the case where one half is the clean
                # caption and the other its tail: it declines exactly when it
                # should merge. The printed page can say what a score
                # ordering cannot: the tail carries no enumerator, opens
                # mid-sentence, and sits no shallower than the caption above.
                # Both still require the second reader to corroborate the
                # joined text (`combined >= _MIN_ROW_SCORE`), so this stays a
                # VLM-confirmed path, distinct from the geometry-only join.
                fragment = is_continuation_fragment(
                    table.label(r + 1), table.label(r),
                    _label_x(lines, anchors, r + 1), _label_x(lines, anchors, r),
                )
                if combined >= _MIN_ROW_SCORE and (
                    (combined > alone_r and combined > alone_r1) or fragment
                ):
                    do_merge = True

        if do_merge:
            merged_row = list(table.rows[value_row])
            # The value-bearing row may be shorter than label_col if docling
            # emitted a ragged row -- pad rather than IndexError on it.
            while len(merged_row) <= table.label_col:
                merged_row.append("")
            merged_row[table.label_col] = f"{table.label(r)} {table.label(r + 1)}".strip()
            new_index = len(new_rows)
            new_rows.append(merged_row)
            merged_new_indices.append(new_index)
            remap[r] = new_index
            remap[r + 1] = new_index
            r += 2
        else:
            remap[r] = len(new_rows)
            new_rows.append(table.rows[r])
            r += 1

    table.rows = new_rows
    return merged_new_indices, remap


def insert_unclaimed_rows(table: Table, alignment: Alignment) -> tuple[list[int], dict[int, int]]:
    """Splice in rows the vision model found that docling never emitted at all.

    Every check in this pipeline before this one is subtractive: it can only
    withhold a figure docling already produced. This is the exception. When
    ``compare()``'s own row-pairing leaves one of the VLM's rows completely
    unclaimed by any docling row, that line exists on the page and was, until
    now, invisible everywhere downstream -- worse than withheld, because
    nothing could tell it apart from a line the filing genuinely did not
    print.

    Inserted verbatim from the vision model's own transcription -- this is
    exactly what a second, independent reader is for -- at the document-order
    position implied by its neighbours in ``alignment.pairs``. The invariant is
    unweakened: nothing here is computed, only relocated from "read but
    discarded" to "read and kept". Every cell in an inserted row is then
    marked ``vlm_only_row`` by ``verify.verify_table`` and withheld unless the
    column's own arithmetic corroborates it -- a new row reaches the model
    corroborated or not at all, the same rule as any other cell.

    Skipped, deliberately, when an unclaimed VLM row's label resembles an
    UNMATCHED docling row (one that exists on the page but scored below the
    pairing threshold): inserting it there would not recover anything, it
    would sit a duplicate beside a row already present, and could double the
    true value in whatever column later foots. That shape of defect -- a row
    docling read badly rather than one it dropped entirely -- is left exactly
    as withheld as it is today; it needs structural repair, not an insertion.

    Returns the new indices of the inserted rows, and an old -> new row-index
    map for every row that already existed. Callers holding ``(row, col)`` or
    ``row`` sets computed against ``table`` BEFORE this call (``disagreements``,
    ``unmatched_rows``) MUST remap them through the second return value --
    inserting a row shifts the index of every row after it, and using a stale
    index would silently point a finding at the wrong row.
    """
    remap = {r: r for r in range(len(table.rows))}
    # `grounded`, not merely `usable`: this adds a row -- label included --
    # on the second reader's word alone. Measured on the live deployment, a
    # blind reader's invented rows were inserted into OD's tables because their
    # generic Schedule III labels paired well enough to look usable, and a row
    # LABEL is never redacted the way its figures are.
    if not alignment.usable or not alignment.grounded or not alignment.vlm_body:
        return [], remap

    used = set(alignment.pairs.values())
    unclaimed = sorted(j for j in range(len(alignment.vlm_body)) if j not in used)
    if not unclaimed:
        return [], remap

    unmatched_labels = [
        _normalise_label(table.label(r)) for r in alignment.unmatched_rows
    ]

    def duplicates_an_unmatched_row(vlm_row: list[str]) -> bool:
        norm = _normalise_label(_label_cell(vlm_row))
        if not norm:
            return False
        return any(
            SequenceMatcher(None, norm, other).ratio() >= _MIN_ROW_SCORE
            for other in unmatched_labels
        )

    reverse_col = {v: k for k, v in alignment.col_map.items()}
    width = max([len(table.header)] + [len(r) for r in table.rows], default=0)

    def build_row(vlm_row: list[str]) -> list[str]:
        row = [""] * width
        if table.label_col < width:
            row[table.label_col] = _label_cell(vlm_row)
        for dc, vc in reverse_col.items():
            if dc < width and vc < len(vlm_row):
                row[dc] = vlm_row[vc]
        return row

    new_rows: list[list[str]] = []
    inserted: list[int] = []
    u_ptr = 0

    def flush_up_to(vlm_index_exclusive: int) -> None:
        nonlocal u_ptr
        while u_ptr < len(unclaimed) and unclaimed[u_ptr] < vlm_index_exclusive:
            j = unclaimed[u_ptr]
            u_ptr += 1
            vlm_row = alignment.vlm_body[j]
            if _is_header_ish(vlm_row) or duplicates_an_unmatched_row(vlm_row):
                continue
            new_rows.append(build_row(vlm_row))
            inserted.append(len(new_rows) - 1)

    for r in range(len(table.rows)):
        if r in alignment.pairs:
            flush_up_to(alignment.pairs[r])
        remap[r] = len(new_rows)
        new_rows.append(table.rows[r])
    flush_up_to(len(alignment.vlm_body))

    table.rows = new_rows
    return inserted, remap


# ---------------------------------------------------------------------------
# Whole-table structural re-read: for when the GRID itself, not one cell's
# value, is wrong. Everything above this point (compare/merge_wrapped_labels/
# insert_unclaimed_rows) checks or repairs VALUES within docling's own
# row/column grid. That is no defence when the grid itself is broken -- a
# label cell that swallowed four real line items, paired against one
# unrelated value, still "compares" against docling's grid because there is
# nothing in that grid for it to disagree with. This section is the one place
# in the module that discards docling's grid outright and asks the vision
# model to derive its own, validated against evidence that does not depend on
# the grid already distrusted: the page's raw OCR text, and the candidate's
# own internal arithmetic.
# ---------------------------------------------------------------------------

#: Below this row count, a merged-row fraction is too noisy to mean anything
#: -- a 2-3 row note table where one row looks merged is not evidence of a
#: broken grid, it is not enough rows to compute a meaningful fraction from.
_MIN_ROWS_FOR_STRUCTURAL_RISK = 4

#: A candidate row's label must resemble SOME text actually printed on the
#: page, not merely a plausible caption -- see select_structure.
_MIN_ROW_GROUNDING_RATIO = 0.7


def _grounded_row_count(table: Table, pool: str) -> int:
    """How many of `table`'s own rows have a label with support in `pool`.

    Used as a cheap, text-only gate on a VLM candidate before it is ever
    bound against the number ledger -- see `select_structure`'s bar 1.
    """
    grounded = 0
    for r in range(len(table.rows)):
        label = _normalise_label(table.label(r))
        if not label:
            continue
        if label in pool or SequenceMatcher(None, label, pool).ratio() >= 0.3:
            grounded += 1
    return grounded


@dataclass
class StructuralRisk:
    unreliable: bool
    reasons: list[str]


def assess_structural_risk(table: Table, ct) -> StructuralRisk:
    """Whether THIS TABLE's own row/column grid looks unreliable -- a
    structural judgement made BEFORE any cell value is checked, distinct
    from a quality judgement about one cell.

    Two independent signals, either enough:

    - the aggregated form of `_looks_merged` (a per-row heuristic elsewhere
      in this module): the FRACTION of this table's rows that look like
      several real line items TableFormer merged into one.
    - a mismatch between the row count TableFormer emitted and the row count
      raw OCR geometry clusters into independently (`structure_repair.
      _cluster_rows` -- the same primitive `repair_from_geometry` already
      uses to REPAIR a grid, reused here purely as a DIAGNOSTIC).

    Below `_MIN_ROWS_FOR_STRUCTURAL_RISK` rows this never fires.
    """
    n = len(table.rows)
    reasons: list[str] = []
    if n < _MIN_ROWS_FOR_STRUCTURAL_RISK:
        return StructuralRisk(False, [])

    merged = sum(
        1 for r in range(n) if _looks_merged(
            table.label(r),
            [table.rows[r][c] if c < len(table.rows[r]) else "" for c in table.value_cols],
        )
    )
    merged_fraction = merged / n
    if merged_fraction >= Config.VLM_STRUCTURE_MERGED_ROW_THRESHOLD:
        reasons.append(
            f"{merged} of {n} rows ({merged_fraction:.0%}) look like several "
            "merged line items rather than one"
        )

    ocr_lines = getattr(ct, "ocr_lines", None)
    cells = getattr(ct, "cells", None)
    if ocr_lines and cells:
        ranges = _column_ranges(cells, len(table.header))
        if len(ranges) >= 2:
            geometry_rows = _cluster_rows(
                ocr_lines, ranges, len(table.header),
                header=table.header, header_bottom=_header_bottom(cells),
            )
            if geometry_rows is not None:
                mismatch = abs(len(geometry_rows) - n) / max(n, len(geometry_rows), 1)
                if mismatch >= Config.VLM_STRUCTURE_ROW_MISMATCH_THRESHOLD:
                    reasons.append(
                        f"the table's own OCR text clusters into {len(geometry_rows)} "
                        f"rows by position, but the grid has {n} ({mismatch:.0%} apart)"
                    )

    return StructuralRisk(unreliable=bool(reasons), reasons=reasons)


def select_structure(
    vlm_markdown: "str | None", ct, original_table: Table, prefix: str,
) -> tuple[Table, "structure_repair.Binding", bool, str]:
    """Bind BOTH docling's table and the vision model's own, independently-
    structured read against the SAME OCR number ledger, and keep whichever
    accounts for more of what the page actually prints.

    This replaces an earlier design that compared the two structures by
    footing strength (how many subtotals each one's arithmetic closed).
    Measured on a real document (OD-SPSU-SO-032 FY2023-24's Cash Flow
    statement), footing count rewards FRAGMENTATION: an already-broken,
    23-row original with many orphan rows (one loose number each) footed on
    9 subtotals -- more than a genuinely cleaner candidate's 6 -- purely
    because it had more scattered stray values to coincidentally pair up. A
    coherence filter (discarding footings whose components were scattered
    far apart) narrowed but did not close the gap (7 vs 4), because the
    remaining inflation came from DUPLICATED adjacent fragments, which are
    indistinguishable from a genuine adjacent subtotal by any span
    heuristic. Coverage does not have this failure mode: consume-once
    accounting means a duplicated or scattered stray value can back at most
    ONE cell, a misplaced value costs the structure that placed it wrong
    rather than being neutral, and a printed figure neither structure
    mentions counts against both -- see `structure_repair.coverage_score`.

    Returns ``(chosen_table, chosen_binding, replaced, reason)``. Never
    mutates either table; returns `original_table` itself, untouched, on
    every refusal path.
    """
    from .tables import parse_markdown_tables
    from . import structure_repair

    ocr_lines = getattr(ct, "ocr_lines", None) or []
    cells = getattr(ct, "cells", None) or []
    header_count = max(
        [len(original_table.header)]
        + [len(r) for r in original_table.rows]
    )
    col_bands = structure_repair._column_ranges(cells, header_count)
    header_bottom = structure_repair._header_bottom(cells)

    ledger = structure_repair.build_number_ledger(ocr_lines, header_bottom)
    base_binding = structure_repair.bind_table(
        original_table, ledger, col_bands, ocr_lines, header_bottom,
    )
    if base_binding.refused:
        return original_table, base_binding, False, base_binding.refused

    if not vlm_markdown:
        return original_table, base_binding, False, "no independent read was available"

    parsed = parse_markdown_tables(vlm_markdown, ct.page_no, prefix=prefix)
    if not parsed:
        return original_table, base_binding, False, "the independent read did not contain a parseable table"
    candidate = parsed[0]
    if len(candidate.rows) < 2:
        return original_table, base_binding, False, "the independent read had too few rows to be a structure"

    # Bar 1 -- cheap, text-only, and orthogonal to the number ledger: stops a
    # plausible-looking FABRICATION from ever reaching a numeric comparison.
    # A coverage comparison is one sign-flip from picking the wrong winner;
    # a text gate on labels the page never printed is not.
    pool = " ".join(_normalise_label(l.text) for l in ocr_lines if l.text.strip())
    if not pool:
        return original_table, base_binding, False, "no raw OCR text available in this table's region to check labels against"

    grounded_rows = _grounded_row_count(candidate, pool)
    row_grounding = grounded_rows / len(candidate.rows)
    if row_grounding < _MIN_ROW_GROUNDING_RATIO:
        return original_table, base_binding, False, (
            f"only {grounded_rows} of {len(candidate.rows)} rows the independent "
            "read reported have any supporting text in the page's own OCR lines"
        )

    cand_binding = structure_repair.bind_table(
        candidate, ledger, col_bands, ocr_lines, header_bottom,
    )
    if cand_binding.refused:
        return original_table, base_binding, False, cand_binding.refused

    base_score = structure_repair.coverage_score(base_binding, original_table.note_col)
    cand_score = structure_repair.coverage_score(cand_binding, candidate.note_col)

    # Strictly greater -- a tie keeps docling, the same status-quo bias
    # `structure_repair.repair_from_geometry` already enforces.
    if cand_score > base_score:
        return candidate, cand_binding, True, (
            f"the independent read accounted for more of the page's own printed "
            f"figures ({cand_score} vs {base_score} by OCR coverage)"
        )
    return original_table, base_binding, False, (
        f"the independent read did not account for more figures than today's "
        f"reading ({cand_score} vs {base_score} by OCR coverage)"
    )


# ---------------------------------------------------------------------------
# Targeted per-row rescue: a second-chance, narrowly cropped re-read for one
# cell verify.py could not otherwise recover from (see verify.draft_table's
# rescue_requests). Everything above this line is the whole-table pass;
# everything below is the rescue pass, called only for cells nothing else
# already has a candidate for.
# ---------------------------------------------------------------------------

#: Hard ceiling on an upscaled rescue crop's pixel count -- a wide schedule's
#: full-width row band, upscaled by Config.VLM_CROP_SCALE, must not be able to
#: blow up the request.
_MAX_RESCUE_PIXELS = 4_000_000


def _upscale(image: "np.ndarray", factor: float | None) -> "np.ndarray":
    """Upsample `image` by `factor` (Config.VLM_CROP_SCALE), capped at
    `_MAX_RESCUE_PIXELS`. A no-op for factor <= 1.0 -- this is only ever
    applied to a small, targeted rescue crop, never to a whole-table read
    (see Config.VLM_CROP_SCALE's docstring for why the two are treated
    differently)."""
    if Image is None or np is None or not factor or factor <= 1.0:
        return image
    height, width = image.shape[:2]
    new_h, new_w = int(height * factor), int(width * factor)
    if new_h * new_w > _MAX_RESCUE_PIXELS:
        cap = (_MAX_RESCUE_PIXELS / float(height * width)) ** 0.5
        new_h, new_w = int(height * cap), int(width * cap)
    if new_h <= height or new_w <= width:
        return image
    frame = Image.fromarray(image).convert("L").resize((new_w, new_h), Image.LANCZOS)
    return np.array(frame)


def row_band(
    page_image: "np.ndarray", converted_table, row_label: str, *, scale: float | None = None,
) -> "np.ndarray | None":
    """Crop just the header strip plus ONE body row out of a table, for a
    targeted rescue read. Returns ``None`` when there is no usable geometry
    (``converted_table.ocr_lines`` / ``page_height_pt``) or no confident match
    for ``row_label`` -- the caller's fallback is the whole-table crop it
    already has, with the same targeted instruction naming the row/column.

    Located by matching ``row_label`` against the table's own OCR lines --
    **never by row index**. A ``Table``'s row indices shift under
    ``structure_repair.repair_from_geometry``, ``merge_wrapped_labels`` and
    ``insert_unclaimed_rows``, so by the time a rescue request reaches this
    function the index it was raised at may no longer name the same row; the
    printed label text is the only stable join key left.

    Deliberately **no Y-flip** here, unlike ``crop()`` above. ``TableCellGeom``
    and ``OcrLine`` bboxes (``convert.py``) are normalised to a TOP-LEFT
    origin at capture time -- unlike the table's own provenance ``bbox``,
    which stays in docling's native BOTTOMLEFT space. Repeating ``crop()``'s
    flip here would silently crop the mirror-image row; see ``crop()``'s own
    docstring for the exact bug class this asymmetry protects against.
    """
    if np is None or not getattr(converted_table, "ocr_lines", None):
        return None
    page_height_pt = getattr(converted_table, "page_height_pt", None)
    if not page_height_pt:
        return None

    target = _normalise_label(row_label)
    if not target:
        return None

    best_line, best_score = None, 0.0
    for line in converted_table.ocr_lines:
        if line.bbox is None or not line.text.strip():
            continue
        score = SequenceMatcher(None, target, _normalise_label(line.text)).ratio()
        if score > best_score:
            best_line, best_score = line, score
    if best_line is None or best_score < _MIN_ROW_SCORE:
        return None

    heights = [
        l.bbox[3] - l.bbox[1] for l in converted_table.ocr_lines
        if l.bbox is not None and l.bbox[3] > l.bbox[1]
    ]
    median_height = _median(heights) if heights else (best_line.bbox[3] - best_line.bbox[1])
    row_top_pt = best_line.bbox[1] - median_height * 0.4
    row_bottom_pt = best_line.bbox[3] + median_height * 0.4

    xs = [c.bbox[0] for c in converted_table.cells if c.bbox is not None]
    xe = [c.bbox[2] for c in converted_table.cells if c.bbox is not None]
    if not xs or not xe:
        return None
    x0_pt, x1_pt = min(xs), max(xe)

    height, width = page_image.shape[:2]
    px = height / float(page_height_pt)  # points -> pixels; TOP-LEFT, no Y-flip

    def _y_pixels(top_pt: float, bottom_pt: float) -> tuple[int, int]:
        return max(0, int(top_pt * px)), min(height, int(bottom_pt * px))

    strips: list[tuple[int, int]] = []
    header_bottom_pt = _header_bottom(converted_table.cells)
    if header_bottom_pt is not None:
        hy0, hy1 = _y_pixels(0.0, header_bottom_pt)
        if hy1 - hy0 >= 5:
            strips.append((hy0, hy1))
    ry0, ry1 = _y_pixels(row_top_pt, row_bottom_pt)
    if ry1 - ry0 >= 5:
        strips.append((ry0, ry1))
    if not strips:
        return None

    margin_px = max(1, int(0.01 * width))
    x0 = max(0, int(x0_pt * px) - margin_px)
    x1 = min(width, int(x1_pt * px) + margin_px)
    if x1 - x0 < 20:
        return None

    bands = [page_image[y0:y1, x0:x1] for y0, y1 in strips]
    image = bands[0] if len(bands) == 1 else np.vstack(bands)

    factor = scale if scale is not None else Config.VLM_CROP_SCALE
    return _upscale(image, factor)


_RESCUE_INSTRUCTION = """You are shown two strips cut from one scanned table: the
column headers on top, and ONE body row beneath them.

Transcribe ONLY that body row, as a single GitHub-flavoured markdown pipe row:
the row label first, then every value cell left to right, in the same column
order as the header strip. Output that one line and nothing else.

Rules, in order of importance:
1. If you cannot read a cell with certainty, write exactly ILLEGIBLE.
   ILLEGIBLE IS A CORRECT AND EXPECTED ANSWER. It is what this request is for.
   A cell you guess wrong is far worse than a cell you mark ILLEGIBLE: the
   guess will be printed to an auditor as a figure from the filing.
2. Never guess a digit. Never complete a partly visible figure. Never
   substitute a value that would make a column add up. Do not compute or
   infer anything.
3. Transcribe EVERY value cell in the row, not only the one asked about. The
   other cells are how this read is checked.
4. Keep Indian digit grouping exactly as printed - 20,17,448 stays 20,17,448.
5. Keep negatives as printed, in parentheses. If a closing parenthesis is cut
   off by the table border, write it as printed with the opening parenthesis
   only.
6. An empty cell or a printed dash is a single dash.

The cell of interest is row "{row_label}", column "{column_name}".
"""


def transcribe_row(image: "np.ndarray", row_label: str, column_name: str) -> str | None:
    """One targeted row re-read. ``None`` if the model could not be reached
    or the reply was cut off -- mirrors ``transcribe()``'s
    ``finish_reason == "length"`` discard, and matters even more here: a
    truncated one-row read may be missing exactly the anchor cells
    ``check_rescue_anchors`` needs, and a caller must never compare against a
    prefix it never received in full.
    """
    if httpx is None or Image is None:
        return None
    instruction = _RESCUE_INSTRUCTION.format(row_label=row_label, column_name=column_name)
    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=Config.VLM_TIMEOUT,
            json={
                "model": Config.VLM_MODEL,
                "temperature": 0.0,
                "max_tokens": Config.VLM_RESCUE_MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_encode(image)}"}},
                    ]},
                ],
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length":
            logger.warning(
                "VLM rescue read truncated at %d tokens; discarding rather than "
                "comparing against a partial row", Config.VLM_RESCUE_MAX_TOKENS,
            )
            return None
    except Exception as exc:
        logger.warning("VLM rescue read failed: %s", exc)
        return None
    return _strip_fences(content or "")


_DISAGREEMENT_INSTRUCTION = """You are shown one cropped table image. Two different automated
readers disagreed about the value in ONE cell: row "{row_label}", column "{column_name}".

Their candidate readings are:
{candidate_lines}

Look ONLY at that one cell in the image and decide which candidate, if any, matches exactly
what is printed there.

Rules, in order of importance:
1. Reply with EXACTLY ONE letter from the list above (for example: A), or the single word
   UNCERTAIN.
2. You may choose ONLY from the candidates given above. Do NOT transcribe a new value, do
   NOT complete a partial one, and do NOT correct either candidate. If neither candidate
   matches what is printed, or you cannot tell, reply UNCERTAIN.
3. Output the letter or UNCERTAIN and nothing else -- no explanation, no punctuation, no
   repetition of the candidate text.
"""


def resolve_disagreement(
    image: "np.ndarray", row_label: str, column_name: str, candidates: list[str],
) -> str | None:
    """Force a choice between EXACTLY the candidates already in evidence --
    never a free read. Returns the chosen candidate's own text, or `None`
    if the model could not be reached, replied UNCERTAIN, or replied
    anything that isn't cleanly one of the offered letters (a malformed
    answer is treated identically to UNCERTAIN, not parsed leniently).

    Built specifically because the existing rescue path (`transcribe_row`)
    reads freely, and a free read can in principle answer with a THIRD
    value never seen in ANY reader's output -- exactly the failure this
    function exists to make structurally impossible: the model has no
    channel here to express a value it was not given as a candidate.
    Intended for the `readers_disagree` tier specifically, where a real,
    closed candidate set already exists (docling's own reading vs. the
    whole-table VLM's own reading) -- it does not replace `transcribe_row`
    for tiers with no second candidate to choose between.

    `candidates` is deliberately ordered but not deduplicated by the
    caller's choice -- if two candidates happen to be identical text, that
    is itself useful information the caller can see in which index was
    chosen, not something this function should silently collapse.
    """
    if httpx is None or Image is None or not candidates or len(candidates) > 26:
        return None
    labels = [chr(ord("A") + i) for i in range(len(candidates))]
    candidate_lines = "\n".join(f"{label}: {text}" for label, text in zip(labels, candidates))
    instruction = _DISAGREEMENT_INSTRUCTION.format(
        row_label=row_label, column_name=column_name, candidate_lines=candidate_lines,
    )
    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=Config.VLM_TIMEOUT,
            json={
                "model": Config.VLM_MODEL,
                "temperature": 0.0,
                "max_tokens": 6,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_encode(image)}"}},
                    ]},
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        logger.warning("VLM closed-world disagreement resolution failed: %s", exc)
        return None

    return _parse_disagreement_reply(content, labels, candidates)


def _parse_disagreement_reply(content: str, labels: list[str], candidates: list[str]) -> str | None:
    """The one candidate `content` cleanly names, or `None` -- strict on
    purpose (see `resolve_disagreement`'s own docstring): only an exact,
    unambiguous single-letter reply resolves anything.
    """
    lines = (content or "").strip().splitlines()
    if not lines:
        return None
    reply = lines[0].strip().upper().rstrip(".:")
    if reply == "UNCERTAIN":
        return None
    matched = [i for i, label in enumerate(labels) if reply == label]
    if len(matched) == 1:
        return candidates[matched[0]]
    return None


def parse_rescue_row(reply: str) -> list[str] | None:
    """The rescued row's cells, split the same way ``compare()`` parses a
    body row. ``None`` if the reply does not contain a recognisable pipe row
    at all (the model ignored the "output only" instruction, or the request
    failed in a way ``transcribe_row`` did not already catch)."""
    lines = [l.strip() for l in (reply or "").splitlines() if l.strip().startswith("|")]
    if not lines:
        return None
    # The model may wrap its answer as a mini two-row table (header +
    # separator + data) despite being asked for one line -- take the last
    # non-separator pipe line, which is the actual data.
    for line in reversed(lines):
        cells = split_row(line)
        if not is_separator(cells):
            return cells
    return None


def _map_rescue_columns(row_label: str, rescued_cells: list[str], value_cols: list[int]) -> dict[int, int]:
    """docling value-column index -> index into `rescued_cells`.

    Positional, not name-matched: a single rescued row carries no header of
    its own to map by text, so this locates the rescue's OWN label cell (best
    match against `row_label`, the same anchor `row_band` located it by) and
    assumes every value column follows it, left to right, in the header
    strip's own order -- which is what the rescue was explicitly instructed
    to preserve (`_RESCUE_INSTRUCTION` rule 3). A rescue that dropped a column
    entirely (e.g. an empty note column it read as nothing) will misalign
    everything after it; `check_rescue_anchors` is what catches that in
    practice, by refusing a rescue whose anchors do not match.
    """
    if not rescued_cells:
        return {}
    target = _normalise_label(row_label)
    label_idx, best_score = 0, -1.0
    for i, cell in enumerate(rescued_cells):
        score = SequenceMatcher(None, target, _normalise_label(cell)).ratio() if target else 0.0
        if score > best_score:
            label_idx, best_score = i, score
    mapping: dict[int, int] = {}
    for offset, col in enumerate(value_cols):
        idx = label_idx + 1 + offset
        if idx < len(rescued_cells):
            mapping[col] = idx
    return mapping


#: A rescue read is accepted only if it reproduces at least this many of the
#: row's OTHER already-trusted figures. One is enough on a narrow note table
#: with only two value columns; see check_rescue_anchors's docstring for why
#: these anchors are never shown to the model.
_MIN_RESCUE_ANCHORS = 1


@dataclass
class RescueResult:
    """The outcome of one targeted rescue call, as `verify.resolve_recoveries`
    expects it: `text` is the rescued cell's own text (`None` for a failed
    call, a truncated reply, or an explicit ILLEGIBLE), and `anchored` is
    whether `check_rescue_anchors` confirmed it against the row's other
    figures."""

    text: str | None
    anchored: bool


def check_rescue_anchors(
    table: Table, row: int, target_col: int, rescued_cells: list[str], value_cols: list[int] | None = None,
) -> bool:
    """Proof of sight for a rescue read, which has no whole-table ``Alignment``
    to lean on.

    Every OTHER value column in ``row`` where docling already has a
    confident (non-suspect) figure is an anchor -- an already-trusted number
    the rescue was never shown (the crop it received names only the CELL of
    interest, per ``_RESCUE_INSTRUCTION``; the row's other values are not
    disclosed), so reproducing one is genuine pixel evidence, not an echo of
    something it was told. The rescue must reproduce **every** anchor within
    the same tolerance ``compare()`` uses (``vlm_read.py``'s own module-level
    tolerance): one wrong anchor fails the whole read, because a model that
    misread a figure it COULD see is not evidence about one it could not.

    With no anchor available at all (a table with only one value column, or
    every other cell in the row also unread), the rescue is **not accepted**
    -- honest degradation, common only on narrow note tables where column
    footing is usually available as the corroborating signal instead.
    """
    value_cols = value_cols if value_cols is not None else table.value_cols
    col_map = _map_rescue_columns(table.label(row), rescued_cells, value_cols)

    anchors = 0
    for col in value_cols:
        if col == target_col:
            continue
        cell = table.cell(row, col)
        if cell.value is None or cell.suspect:
            continue
        idx = col_map.get(col)
        if idx is None:
            continue
        theirs_raw = rescued_cells[idx]
        if theirs_raw.strip().upper() == "ILLEGIBLE":
            return False
        theirs = parse_cell(theirs_raw).value
        if theirs is None:
            continue
        anchors += 1
        if abs(cell.value - theirs) > max(0.01, abs(cell.value) * 1e-6):
            return False
    return anchors >= _MIN_RESCUE_ANCHORS


def rescue_cell_text(table: Table, row: int, target_col: int, rescued_cells: list[str]) -> str | None:
    """The rescued text for the one cell a rescue request was actually
    raised for, positioned via the same column mapping ``check_rescue_anchors``
    uses. ``None`` if the mapping does not reach that far (a short or
    malformed reply) or the cell reads ``ILLEGIBLE`` -- the encouraged,
    correct answer when the model is not certain, which must never be turned
    into a candidate."""
    col_map = _map_rescue_columns(table.label(row), rescued_cells, table.value_cols)
    idx = col_map.get(target_col)
    if idx is None or idx >= len(rescued_cells):
        return None
    text = rescued_cells[idx]
    if text.strip().upper() == "ILLEGIBLE":
        return None
    return text


def _reread_cell_value(
    table: Table, row: int, col: int, page_image: "np.ndarray | None", converted_table,
) -> float | None:
    """One independent targeted second look at a single cell: a tight,
    upscaled crop of the row's own band, read blind, returning the parsed
    figure -- or ``None`` on EVERY refusal (no image, no band located, no
    reply, unparseable, ILLEGIBLE).

    Returning ``None`` rather than raising or guessing is the contract every
    caller depends on: a vision model that is unreachable produces ``None``
    here, so nothing built on top of this can ever act on a figure nobody
    read. The two callers compare the result against DIFFERENT things --
    `resolve_unbound_cell` against the cell's own existing value,
    `confirm_gap_fill` against an OCR token's value -- which is why the read
    itself is separated from the comparison.
    """
    if page_image is None:
        return None
    row_label = table.label(row)
    image = row_band(page_image, converted_table, row_label)
    if image is None:
        return None
    reply = transcribe_row(image, row_label, table.column_name(col))
    if not reply:
        return None
    cells = parse_rescue_row(reply)
    if not cells:
        return None
    text = rescue_cell_text(table, row, col, cells)
    if text is None:
        return None
    return parse_cell(text).value


def resolve_unbound_cell(
    table: Table, row: int, col: int, page_image: "np.ndarray | None", converted_table,
) -> bool:
    """The escape hatch for a cell number binding could not place on the
    page at all (`structure_repair.bind_table`'s ``unbound`` set): a
    figure the VLM's chosen structure claims, but no OCR token anywhere in
    this table's region backed at that position -- possibly a genuine VLM
    misread, possibly ink OCR simply failed to read (faint, smudged). One
    independent second look decides which, and returns True only if it
    agrees with the value already there.

    Deliberately **not** `check_rescue_anchors` -- and that is a real
    loosening, stated plainly rather than hidden: the anchors in an UNBOUND
    row may themselves be unbound (that is exactly why the row went
    unanchored), so requiring one here would make this hatch fire least
    often exactly where it is needed most. What replaces it is agreement
    with the cell's OWN existing reading. Honest accounting of what that is
    worth: the crop differs (a tight, upscaled row band, not whatever crop
    produced the original read), the model is never shown its own previous
    answer, and ILLEGIBLE is explicitly presented as the correct response
    when uncertain (`_RESCUE_INSTRUCTION`) -- but it is still the SAME
    model looking at the SAME printed digits, so the two reads are
    correlated. This is strictly weaker evidence than an OCR/VLM binding
    and weaker than an anchored rescue; it is better than one read alone,
    never proof.
    """
    target = table.cell(row, col).value
    if target is None:
        return False
    value = _reread_cell_value(table, row, col, page_image, converted_table)
    if value is None:
        return False
    return abs(value - target) <= max(0.01, abs(target) * 1e-6)


def confirm_gap_fill(
    table: Table, row: int, col: int, token, page_image: "np.ndarray | None",
    converted_table,
) -> bool:
    """The second reader for a proposed gap fill: does an independent, tightly
    cropped read of that cell produce the SAME figure the OCR token carries?

    Returns False on EVERY refusal -- no image, no band, no reply, ILLEGIBLE,
    unparseable, a different figure. **A vision model that is unreachable
    therefore places nothing.** There is no fallback to position alone
    anywhere in the chain: `structure_repair.apply_gap_fills` only ever
    receives what this returned True for.

    Honest accounting of what agreement is worth: OCR read the digits
    independently, the vision model read the digits, and they agree at the
    same position -- that is strictly stronger than `resolve_unbound_cell`'s
    same-model-twice comparison, and the strongest evidence any gap fill can
    have. It is still not arithmetic proof.
    """
    value = _reread_cell_value(table, row, col, page_image, converted_table)
    if value is None:
        return False
    return abs(value - token.value) <= max(0.01, abs(token.value) * 1e-6)


# ---------------------------------------------------------------------------
# Band rescue: splitting a row TableFormer merged from SEVERAL real line
# items, a different defect from a single unreadable cell (see
# _looks_merged's docstring). This is a STRUCTURAL repair, like
# merge_wrapped_labels / insert_unclaimed_rows above -- it changes the
# table's row count and is meant to run alongside them in pipeline.py,
# BEFORE verify.draft_table ever sees the table, not as one of verify.py's
# per-cell rescue tiers. Once split, the new rows are simply marked
# vlm_only -- the withhold-unless-arithmetic-confirms machinery already
# built for that tier handles everything downstream with no new trust logic.
# ---------------------------------------------------------------------------

def _looks_merged(raw_label: str, value_texts: list[str]) -> bool:
    """Whether a row's OWN content suggests TableFormer merged several real
    line items into it -- two independent signals, either enough:

    - the label carries 2+ Schedule III enumerator markers
      (``tables._ENUM_MARKER_RE``). ``tables._split_merged_row`` already
      looks for exactly this and refuses to auto-split when any value cell
      holds more than one number -- which is precisely why a row with this
      shape can still reach here unsplit.
    - a value cell itself holds 2+ whitespace-separated tokens that each
      parse as a number -- the un-enumerated case (e.g. a cash-flow
      schedule, which prints no "(a)/(b)" markers at all): several figures
      run together with no marker in the label to say so.
    """
    if len(_ENUM_MARKER_RE.findall(raw_label or "")) >= 2:
        return True
    for text in value_texts:
        tokens = (text or "").split()
        if len(tokens) < 2:
            continue
        numeric = sum(1 for t in tokens if parse_cell(t).value is not None)
        if numeric >= 2:
            return True
    return False


def multi_row_band(
    page_image: "np.ndarray", converted_table, merged_label: str, *,
    max_lines: int | None = None, scale: float | None = None,
) -> "np.ndarray | None":
    """Crop the header strip plus the SPAN of consecutive OCR lines whose
    concatenation best matches `merged_label`, for a band-level rescue read.

    `row_band` above matches one label against one OCR line, which is
    exactly what fails for a merged row: no single printed line says the
    whole concatenated label. This instead slides a window over consecutive
    lines (by Y-order, within the table's own X-range) and scores their
    CONCATENATED text against the merged label -- the same "does the
    concatenation score higher than either piece alone" reasoning
    `merge_wrapped_labels` already uses for its narrower two-row case,
    generalised to up to `max_lines` (`Config.VLM_BAND_MAX_LINES`) lines.

    Returns ``None`` under the same conditions as `row_band` (no geometry,
    no confident match) -- the caller's fallback is to leave the row
    unsplit, exactly as if no band rescue had been attempted.
    """
    if np is None or not getattr(converted_table, "ocr_lines", None):
        return None
    page_height_pt = getattr(converted_table, "page_height_pt", None)
    if not page_height_pt:
        return None

    target = _normalise_label(merged_label)
    if not target:
        return None

    lines = sorted(
        (l for l in converted_table.ocr_lines if l.bbox is not None and l.text.strip()),
        key=lambda l: l.bbox[1],
    )
    if not lines:
        return None

    limit = max_lines if max_lines is not None else Config.VLM_BAND_MAX_LINES
    best_span: tuple[int, int] | None = None  # [start, end) into `lines`
    best_score = 0.0
    for start in range(len(lines)):
        concatenated = ""
        for end in range(start + 1, min(start + limit, len(lines)) + 1):
            concatenated = _normalise_label(
                " ".join(l.text for l in lines[start:end])
            )
            score = SequenceMatcher(None, target, concatenated).ratio()
            if score > best_score:
                best_score, best_span = score, (start, end)

    if best_span is None or best_score < _MIN_BAND_SCORE:
        return None

    span_lines = lines[best_span[0]:best_span[1]]
    heights = [l.bbox[3] - l.bbox[1] for l in lines if l.bbox[3] > l.bbox[1]]
    median_height = _median(heights) if heights else (span_lines[0].bbox[3] - span_lines[0].bbox[1])
    band_top_pt = span_lines[0].bbox[1] - median_height * 0.4
    band_bottom_pt = span_lines[-1].bbox[3] + median_height * 0.4

    xs = [c.bbox[0] for c in converted_table.cells if c.bbox is not None]
    xe = [c.bbox[2] for c in converted_table.cells if c.bbox is not None]
    if not xs or not xe:
        return None
    x0_pt, x1_pt = min(xs), max(xe)

    height, width = page_image.shape[:2]
    px = height / float(page_height_pt)  # points -> pixels; TOP-LEFT, no Y-flip (see row_band)

    def _y_pixels(top_pt: float, bottom_pt: float) -> tuple[int, int]:
        return max(0, int(top_pt * px)), min(height, int(bottom_pt * px))

    strips: list[tuple[int, int]] = []
    header_bottom_pt = _header_bottom(converted_table.cells)
    if header_bottom_pt is not None:
        hy0, hy1 = _y_pixels(0.0, header_bottom_pt)
        if hy1 - hy0 >= 5:
            strips.append((hy0, hy1))
    by0, by1 = _y_pixels(band_top_pt, band_bottom_pt)
    if by1 - by0 < 5:
        return None
    strips.append((by0, by1))

    margin_px = max(1, int(0.01 * width))
    x0 = max(0, int(x0_pt * px) - margin_px)
    x1 = min(width, int(x1_pt * px) + margin_px)
    if x1 - x0 < 20:
        return None

    bands = [page_image[y0:y1, x0:x1] for y0, y1 in strips]
    image = bands[0] if len(bands) == 1 else np.vstack(bands)

    factor = scale if scale is not None else Config.VLM_CROP_SCALE
    return _upscale(image, factor)


#: How well a candidate band's line-concatenation must match the merged
#: label to be considered the right region. Looser than row_band's
#: _MIN_ROW_SCORE (0.72): the model's own line breaks and spacing when
#: concatenated will not exactly reproduce docling's merged-label text, only
#: resemble it closely.
_MIN_BAND_SCORE = 0.55


_BAND_INSTRUCTION = """You are shown two strips cut from one scanned table: the
column headers on top, and a SMALL REGION beneath them that may contain
MORE THAN ONE printed line item.

Transcribe EVERY distinct line item you see in the region below the header,
each as its OWN row, in a GitHub-flavoured markdown pipe table with the same
columns as the header strip, in the same order. Output only that table, no
commentary.

Rules, in order of importance:
1. Do NOT merge two or more printed line items into one row, even if they
   look related. If you see four separate printed lines, output four rows.
2. If you cannot read a cell with certainty, write exactly ILLEGIBLE.
   ILLEGIBLE IS A CORRECT AND EXPECTED ANSWER. A cell you guess wrong is far
   worse than one you mark ILLEGIBLE: the guess will be printed to an
   auditor as a figure from the filing.
3. Never guess a digit. Never compute, infer, or substitute a value that
   would make a column add up.
4. Keep Indian digit grouping exactly as printed - 20,17,448 stays 20,17,448.
5. Keep negatives as printed, in parentheses. If a closing parenthesis is cut
   off by the table border, write it as printed with the opening parenthesis
   only.
6. An empty cell or a printed dash is a single dash.
"""


def transcribe_band(image: "np.ndarray") -> str | None:
    """A band-level re-read that may return several rows. ``None`` on
    failure or truncation -- mirrors `transcribe_row`'s discard, and matters
    even more here: a truncated reply may be missing entire line items,
    which the caller has no way to tell apart from the model choosing not
    to report them."""
    if httpx is None or Image is None:
        return None
    try:
        response = httpx.post(
            f"{Config.VLM_BASE_URL}/v1/chat/completions",
            headers=_headers(),
            timeout=Config.VLM_TIMEOUT,
            json={
                "model": Config.VLM_MODEL,
                "temperature": 0.0,
                "max_tokens": Config.VLM_BAND_MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": _BAND_INSTRUCTION},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_encode(image)}"}},
                    ]},
                ],
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") == "length":
            logger.warning(
                "VLM band rescue read truncated at %d tokens; discarding rather than "
                "reporting a possibly incomplete set of line items", Config.VLM_BAND_MAX_TOKENS,
            )
            return None
    except Exception as exc:
        logger.warning("VLM band rescue read failed: %s", exc)
        return None
    return _strip_fences(content or "")


def parse_band_rows(reply: str) -> list[list[str]]:
    """Every data row in a band reply, in order -- unlike `parse_rescue_row`,
    which returns only the last one, because a band reply is expected to
    contain several. Header/separator lines are dropped the same way
    `compare()` drops them from a whole-table reply."""
    rows: list[list[str]] = []
    for line in (reply or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = split_row(stripped)
        if is_separator(cells):
            continue
        rows.append(cells)
    return _body_rows(rows)


def split_merged_rows(
    table: Table, converted_table, page_image: "np.ndarray | None",
    merged_rows: set[int] | None = None, max_attempts: int | None = None,
) -> tuple[set[int], dict[int, int], list[str], int]:
    """Replace every merge-suspected row with the several rows a band rescue
    read reports for it, IN PLACE, mutating `table.rows`.

    `max_attempts` caps how many rows get an actual band-rescue CALL (in row
    order) -- the caller's per-document budget (`Config.VLM_MAX_BAND_RESCUES`),
    since each attempt is its own network round trip. Rows past the cap are
    left exactly as they were, same as if no merge had been detected; the
    returned `attempted` count is how much of the caller's budget this table
    actually spent, so a multi-table document can share one running total.

    Returns ``(new_row_indices, remap, notes, attempted)``:

    - `new_row_indices` -- every row in the (mutated) table that came from a
      split. Callers should fold these into `vlm_only_rows` before calling
      `verify.draft_table` -- a split row gets EXACTLY the same
      withhold-unless-the-column-foots treatment as any other row the vision
      model alone is responsible for. No new trust logic is introduced here.
    - `remap` -- old row index -> new row index, for every row that was NOT
      split (the same obligation `merge_wrapped_labels` / `insert_unclaimed_rows`
      document: any `(row, col)` / `row` set computed against `table` BEFORE
      this call must be remapped through it, or dropped). A row that WAS
      split has NO entry here -- its old identity no longer names a single
      row, so a stale reference to it should be dropped, not remapped; that
      is also the correct answer, since a garbled merged label practically
      never survives `compare()`'s row-pairing anyway.
    - `notes` -- one sentence per row actually split, for the document's
      quality notes.

    Refuses (leaves the row exactly as it was) unless the band read passes
    ALL of: enough geometry to crop a band at all, at least two lines
    returned (a single line back is not a split), and -- the anti-
    hallucination check, since a band read has no whole-table `Alignment` to
    lean on -- the returned rows' labels, CONCATENATED in order, still
    resemble the original merged label. That proves the read is of the same
    region, not an invention; it does NOT vouch for any individual figure,
    which is why every split cell still goes on to the ordinary
    withhold-unless-footed path.
    """
    merged_rows = merged_rows if merged_rows is not None else {
        r for r in range(len(table.rows))
        if _looks_merged(
            table.label(r),
            [table.rows[r][c] if c < len(table.rows[r]) else "" for c in table.value_cols],
        )
    }
    remap = {r: r for r in range(len(table.rows))}
    if not merged_rows or page_image is None:
        return set(), remap, [], 0

    new_rows: list[list[str]] = []
    new_row_indices: set[int] = set()
    notes: list[str] = []
    attempted = 0
    width = max([len(table.header)] + [len(r) for r in table.rows], default=0)

    for r in range(len(table.rows)):
        # Budget exhausted, or this row was never flagged: left exactly as
        # it was, for the ordinary per-cell path to withhold downstream.
        if r not in merged_rows or (max_attempts is not None and attempted >= max_attempts):
            remap[r] = len(new_rows)
            new_rows.append(table.rows[r])
            continue
        attempted += 1

        original_label = table.label(r)
        image = multi_row_band(page_image, converted_table, original_label)
        candidate_rows: list[list[str]] = []
        if image is not None:
            reply = transcribe_band(image)
            if reply:
                candidate_rows = parse_band_rows(reply)

        accepted = False
        if len(candidate_rows) >= 2:
            # Proof of sight: the concatenation of what came back must still
            # resemble the ORIGINAL merged label -- the same test
            # merge_wrapped_labels uses for its own (narrower) pairing.
            # This is self-consistency, not external corroboration: it shows
            # the model read the same region, not that any one figure in it
            # is correct -- that judgement is left entirely to the ordinary
            # footing-or-withhold path every other cell already goes through.
            candidate_labels = [_label_cell(row) for row in candidate_rows]
            concatenated = _normalise_label(" ".join(candidate_labels))
            target = _normalise_label(original_label)
            if target and SequenceMatcher(None, target, concatenated).ratio() >= _MIN_BAND_SCORE:
                accepted = True

        if not accepted:
            remap[r] = len(new_rows)
            new_rows.append(table.rows[r])
            continue

        # This old index no longer names a single row -- drop it rather than
        # remap it, per this function's own contract (see docstring).
        del remap[r]
        for candidate in candidate_rows:
            built = [""] * width
            if table.label_col < width:
                built[table.label_col] = _label_cell(candidate)
            # Positional-after-label, same reasoning as _map_rescue_columns:
            # the band crop showed the header strip in the table's own
            # column order, and the model was asked to preserve it.
            label_idx = min(
                range(len(candidate)),
                key=lambda i: 0 if candidate[i] == _label_cell(candidate) else 1,
                default=0,
            )
            for offset, col in enumerate(table.value_cols):
                idx = label_idx + 1 + offset
                if col < width and idx < len(candidate):
                    built[col] = candidate[idx]
            new_row_indices.add(len(new_rows))
            new_rows.append(built)

        notes.append(
            f"A row printed as \"{original_label[:80]}\" was split into "
            f"{len(candidate_rows)} line item(s) by a targeted second read, "
            "because it looked like several real line items TableFormer "
            "merged into one detected row. Each split figure is shown only "
            "where the column's own arithmetic confirms it."
        )

    table.rows = new_rows
    return new_row_indices, remap, notes, attempted
