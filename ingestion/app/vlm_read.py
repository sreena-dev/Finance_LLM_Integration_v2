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

from .config import Config
from .numbers import parse_cell
from .tables import Table, split_row, is_separator

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
7. Output only the markdown table. No commentary, no explanation, no fences.

A previous automated read of this table is given below. It may contain errors.
Correct it against the image; do not simply repeat it.

PREVIOUS READ:
"""


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


def crop(page_image: "np.ndarray", bbox: list[float] | None, page_height_pt: float | None) -> "np.ndarray":
    """Crop a table's bounding box out of the rendered page.

    Docling reports PDF coordinates with ``coord_origin=BOTTOMLEFT``, while an
    image array is indexed from the top. Y is flipped here rather than at the
    call site so there is exactly one place that has to be right; the frontend
    overlay has to do the same flip independently and is documented to.

    A small margin is added because TableFormer's box hugs the cell grid, and
    the caption and unit line ("Amount in '000") sit just outside it -- and the
    unit line is precisely what stops a figure being wrong by three orders of
    magnitude.
    """
    if bbox is None or np is None:
        return page_image

    height, width = page_image.shape[:2]
    left, top, right, bottom = bbox

    if page_height_pt:
        scale = height / page_height_pt
        y0 = height - (top * scale)
        y1 = height - (bottom * scale)
        x0, x1 = left * scale, right * scale
    else:
        x0, y0, x1, y1 = left, top, right, bottom

    if y0 > y1:
        y0, y1 = y1, y0
    margin = int(0.03 * height)
    y0 = max(0, int(y0) - margin)
    y1 = min(height, int(y1) + margin)
    x0 = max(0, int(x0) - margin)
    x1 = min(width, int(x1) + margin)

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


def transcribe(image: "np.ndarray", previous_markdown: str) -> str | None:
    """One table image to markdown. ``None`` if the model could not be reached."""
    if httpx is None or Image is None:
        return None
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
                "max_tokens": 4000,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": _INSTRUCTION + previous_markdown},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_encode(image)}"}},
                    ]},
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("VLM transcription failed: %s", exc)
        return None

    return _strip_fences(content or "")


def _strip_fences(text: str) -> str:
    text = re.sub(r"^\s*```[a-zA-Z]*\s*", "", text.strip())
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def compare(docling_table: Table, vlm_markdown: str) -> tuple[float, set[tuple[int, int]]]:
    """``(agreement, disagreeing_cells)`` between the two reads.

    Compared on **parsed values, not on strings**. The two readers format
    differently -- one writes ``(1,757)`` and the other ``-1757`` for the same
    figure -- and a string comparison would call every negative a disagreement
    and drown the real ones. What matters is whether they read the same number.

    Cells where one reader saw nothing are not counted either way: an absent
    cell is a coverage difference, not a contradiction about a value.
    """
    vlm_rows: list[list[str]] = []
    for line in vlm_markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = split_row(stripped)
        if is_separator(cells):
            continue
        vlm_rows.append(cells)

    if len(vlm_rows) < 2:
        return 0.0, set()

    # Drop the VLM's header row so the body rows line up with ours.
    vlm_body = vlm_rows[1:]

    compared = 0
    agreed = 0
    disagreements: set[tuple[int, int]] = set()

    for r, row in enumerate(docling_table.rows):
        if r >= len(vlm_body):
            break
        for c in docling_table.value_cols:
            ours = docling_table.cell(r, c).value
            theirs_raw = vlm_body[r][c] if c < len(vlm_body[r]) else ""
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

    if compared == 0:
        return 0.0, set()
    return agreed / compared, disagreements
