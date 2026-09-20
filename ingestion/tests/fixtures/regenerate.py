"""Regenerate the docling fixtures from the PDFs in ``data/``.

Not run by the test suite — it needs the full ingestion dependencies (docling,
torch, an OCR engine) and takes about half a minute per page. Run it when the
ingestion pipeline changes shape and the committed fixtures need to move with it:

    cd ingestion
    venv/Scripts/python tests/fixtures/regenerate.py

See ``ingestion/README.md`` for the install, including the Windows long-path
caveat that stops torch installing under a deep project directory.

Page indices are 0-based and refer to the rendered page order, which is *not*
the order images appear in the PDF's object stream — a mistake worth stating,
because picking pages by stream order once sent me looking at an accounting-policy
page while believing it was the balance sheet.
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PIL import Image  # noqa: E402

from app import convert as convert_mod  # noqa: E402
from app import precheck, preprocess, render  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "..", "data")

#: (output name, pdf relative to data/, 0-based page index)
TARGETS = [
    (
        "od_2021_22_policy_page.md",
        "OD-SPSU-SO-032/2021-22/OD-SPSU-SO-032_2021-22_SFS_20260522161923156.PDF",
        9,
    ),
]


def capture(pdf_path: str, page_index: int) -> str:
    pages = render.render(open(pdf_path, "rb").read())
    qualities = precheck.check_document(pages)
    images = preprocess.preprocess(pages, qualities)

    quality = qualities[page_index]
    quality.page_no = 1
    converted = convert_mod.convert([images[page_index]], [quality])
    return converted.page_markdown.get(1, converted.markdown)


def main() -> None:
    for name, relative, index in TARGETS:
        pdf = os.path.normpath(os.path.join(DATA, relative))
        if not os.path.exists(pdf):
            print(f"skipped {name}: {pdf} not found")
            continue
        print(f"converting {relative} page {index}…")
        markdown = capture(pdf, index)
        with io.open(os.path.join(HERE, name), "w", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n")
        print(f"  wrote {name} ({len(markdown)} chars)")


if __name__ == "__main__":
    main()
