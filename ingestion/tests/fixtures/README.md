# Fixtures captured from real docling conversions

These are **real docling output**, not hand-written examples. That distinction is
the entire point of the directory.

The bug that prompted it: `_policy_headings` filtered on a `section_breadcrumb`
containing "notes to", copied faithfully from the corpus SQL. It was tested
against a hand-written fixture that happened to include a
`## Notes to the Standalone Financial Statements` heading — so the test passed
while the filter eliminated every candidate on every real document, because
docling recovers no such heading on the policy page. A fixture written from
imagination confirms the shape you imagined.

## Provenance

| File | Source | Captured |
|---|---|---|
| `od_2021_22_policy_page.md` | `data/OD-SPSU-SO-032/2021-22/OD-SPSU-SO-032_2021-22_SFS_20260522161923156.PDF`, page index 9 | docling 2.123.1, RapidOCR, `force_full_page_ocr=True`, after the deskew/upscale preprocessing |

Prose lines are truncated at 100 characters, which is where the capture cut them.
That does not affect what these fixtures test — chunk typing, heading detection
and breadcrumb assembly all key off the start of a line.

## Regenerating

`regenerate.py` reproduces them from the PDFs in `data/`, for when the ingestion
pipeline changes and the fixtures need to move with it. It needs the full
ingestion dependencies (docling, torch, an OCR engine); see `ingestion/README.md`,
including the Windows long-path caveat.
