"""``emit.build_page_image`` -- the document pane's page-by-page view.

This is the same "fail safe rather than crash the run" contract
``vlm_read.snippet`` already carries, tested at the one function that decides
whether an ingestion run can ever produce a page image the pane has nothing to
show for. If this silently stopped encoding real pages, the pane would render
blank without the ingestion run itself failing anywhere -- exactly the class of
bug this codebase's other tests exist to catch early.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                 # noqa: E402
from app.emit import build_page_image  # noqa: E402
from app.models import PageImage   # noqa: E402


def _page(width: int = 200, height: int = 280) -> "np.ndarray":
    """A small synthetic greyscale page -- not a real scan, just real pixels."""
    return np.random.randint(0, 256, size=(height, width), dtype=np.uint8)


def test_a_real_page_is_encoded_with_its_own_dimensions():
    image = _page(width=200, height=280)
    result = build_page_image(5, image)

    assert result.page_no == 5
    assert result.width_px == 200
    assert result.height_px == 280
    assert result.image_jpeg_b64
    # A real JPEG, not just a non-empty string -- base64 of the JPEG magic
    # bytes (0xFFD8) decodes to a recognisable prefix.
    import base64
    assert base64.b64decode(result.image_jpeg_b64)[:2] == b"\xff\xd8"


def test_a_missing_page_encodes_to_nothing_rather_than_raising():
    """Blank and duplicate pages reach here as ``None`` -- see pipeline.py's
    ``image_by_number.get(q.page_no)`` -- and must come back as an honestly
    empty record, not an exception that would take the whole ingestion run
    down over one skipped page."""
    result = build_page_image(7, None)

    assert result == PageImage(page_no=7, image_jpeg_b64=None, width_px=0, height_px=0)


def test_a_wide_page_is_downscaled_not_left_at_full_resolution():
    """1400px is the width build_page_image asks vlm_read.snippet for --
    this is the one place that number is asserted, so a future edit that
    silently drops the downscale (and so the memory-budget reasoning in
    pipeline.py's comment) fails a test instead of only showing up as a
    larger-than-expected document later."""
    from PIL import Image
    import base64
    import io

    image = _page(width=2481, height=3508)  # ~300 DPI A4
    result = build_page_image(1, image)

    decoded = Image.open(io.BytesIO(base64.b64decode(result.image_jpeg_b64)))
    assert decoded.width == 1400
    # Original dimensions are still reported, even though the stored JPEG is smaller --
    # a caller sizing a page nav thumbnail needs the real aspect ratio, not the encoded one.
    assert result.width_px == 2481
    assert result.height_px == 3508
