"""The second reader: where it looks, and how its read is lined up with ours.

None of this had a test before, which is why three compounding bugs shipped
together and stayed invisible on every clean document:

1. ``crop`` was handed ``page_height_pt=None`` by its only caller and silently
   took an "already in pixels" branch, cropping in the wrong coordinate space.
   On the MH 2024-25 filing it returned 21% of the page -- blank margin, no
   table. The vision model was "corroborating" figures it could not see, and
   every citation snippet showed the wrong part of the scan.
2. ``compare`` paired the two reads by row *position*, so one extra row on
   either side re-paired every figure with its neighbour. Measured on that same
   page: agreement 0.116, 38 false ``readers_disagree``, and because that is a
   hard withholding reason, an entire balance sheet of correctly-read figures
   was redacted.
3. An unusable read (absent, truncated, structurally different) was
   indistinguishable from wholesale disagreement, so a dropped connection could
   blank a schedule.

Run with::

    cd ingestion
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        tests/test_vlm_alignment.py -q
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import vlm_read                              # noqa: E402
from app.config import Config                         # noqa: E402
from app.tables import parse_markdown_tables          # noqa: E402


# --------------------------------------------------------------------------
# crop -- the coordinate space
# --------------------------------------------------------------------------

def _page(height=3507, width=2480):
    """A page-shaped array at 300 DPI, marked so a crop can be located."""
    return np.full((height, width), 255, dtype=np.uint8)


def test_crop_converts_points_to_pixels_and_flips_y():
    """docling reports points with a bottom-left origin; the array is pixels
    from the top. Both conversions must happen, or the box lands somewhere
    else entirely."""
    page = _page()
    height = page.shape[0]
    page_h_pt = height * 72.0 / Config.RENDER_DPI     # 841.68 at 300 DPI

    # A box over the TOP half of the page, expressed bottom-left: its `top`
    # coordinate is the larger number and sits near the page height.
    bbox = [50.0, page_h_pt - 40.0, 400.0, page_h_pt / 2.0]
    out = vlm_read.crop(page, bbox)

    # Scaled by RENDER_DPI/72, not used verbatim: a 350pt-wide box is ~1458px.
    assert out.shape[1] > 1000, "points were not scaled to pixels"
    # And it is the TOP of the page, so its height is about half the page plus
    # the 3% margin -- not the bottom half, which is what skipping the flip
    # would have produced.
    assert out.shape[0] < height * 0.75


def test_crop_covers_most_of_the_page_for_a_full_page_table():
    """The regression, in miniature. A table occupying most of an A4 page must
    crop to most of the page -- the bug produced 21%."""
    page = _page()
    page_h_pt = page.shape[0] * 72.0 / Config.RENDER_DPI
    page_w_pt = page.shape[1] * 72.0 / Config.RENDER_DPI

    bbox = [0.08 * page_w_pt, 0.94 * page_h_pt, 0.92 * page_w_pt, 0.10 * page_h_pt]
    out = vlm_read.crop(page, bbox)

    covered = (out.shape[0] * out.shape[1]) / (page.shape[0] * page.shape[1])
    assert covered > 0.6, f"crop covered only {covered:.0%} of the page"


def test_crop_without_a_bbox_returns_the_whole_page():
    page = _page()
    assert vlm_read.crop(page, None).shape == page.shape


def test_a_box_off_the_page_falls_back_to_the_page():
    """Better to show the reader more than the table than a strip of margin
    that a model would then confidently 'read' -- which is exactly how the
    original bug stayed invisible. A box in the wrong coordinate space lands
    off the page; clamping it would manufacture a plausible-looking crop."""
    page = _page()
    page_h_pt = page.shape[0] * 72.0 / Config.RENDER_DPI

    # Well above the top of the page in point-space.
    out = vlm_read.crop(page, [50.0, page_h_pt * 3.0, 400.0, page_h_pt * 2.5])
    assert out.shape == page.shape


# --------------------------------------------------------------------------
# compare -- alignment
# --------------------------------------------------------------------------

DOCLING_MD = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| (Amount in '000) | | |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 12,859 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""


def _docling_table(md=DOCLING_MD):
    return parse_markdown_tables(md, 1, prefix="t1_")[0]


def test_an_extra_units_row_on_one_side_does_not_shift_every_figure():
    """THE regression. docling captured "(Amount in '000)" as a data row and
    the vision model put it in the header. Positional pairing then compared
    every figure against its neighbour: measured agreement 0.116 on a page
    where the two readers actually agreed."""
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 12,859 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""

    alignment = vlm_read.compare(_docling_table(), vlm)

    assert alignment.usable
    assert alignment.agreement > 0.9, alignment.agreement
    assert alignment.disagreements == set()


def test_ocr_corrupted_labels_still_match():
    """What "labels survive a bad scan" actually looks like: wrong letters in
    the middle of the word. Exact and containment matching both miss these --
    real docling output from the document that prompted this work."""
    corrupted = DOCLING_MD.replace(
        "Capital work in progress", "Capltal work in prograss"
    ).replace("Other Intangible Assets", "Other Int�ngible Assets")

    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 12,859 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""

    alignment = vlm_read.compare(_docling_table(corrupted), vlm)

    assert alignment.usable
    assert alignment.matched == alignment.total, "corrupted labels failed to match"
    assert alignment.disagreements == set()


def test_a_real_disagreement_is_still_reported():
    """The alignment fix must not blunt the signal it exists to carry."""
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 99,999 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""

    alignment = vlm_read.compare(_docling_table(), vlm)

    assert alignment.usable
    assert len(alignment.disagreements) == 1
    row, _col = next(iter(alignment.disagreements))
    assert "Capital work in progress" in _docling_table().label(row)


def test_illegible_from_the_second_reader_is_a_disagreement_not_a_match():
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | ILLEGIBLE |
| Capital work in progress | B-1 (a) | 12,859 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""

    alignment = vlm_read.compare(_docling_table(), vlm)
    assert len(alignment.disagreements) == 1


def test_total_1_and_total_1_plus_2_are_not_the_same_row():
    """Normalisation keeps digits. Stripping them collapsed "Total (1)",
    "Total (2)" and "Total (1+2)" onto one key -- three different rows of
    every Schedule III balance sheet."""
    assert vlm_read._normalise_label("Total (1)") != vlm_read._normalise_label("Total (1+2)")
    assert vlm_read._normalise_label("31st March 2025") != vlm_read._normalise_label("31st March 2024")


# --------------------------------------------------------------------------
# The three states -- and the one that must never be confused with the others
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload,why", [
    ("", "empty response"),
    ("I could not read this table.", "prose, no table"),
    ("| Particulars |\n| --- |", "header only, no body"),
])
def test_an_unusable_read_is_not_a_table_full_of_disagreements(payload, why):
    """The distinction the whole three-state design exists for. An absent,
    truncated or unparseable read must yield NO disagreements, so the table
    falls back to arithmetic alone. Treating it as disagreement blanks a
    schedule over a dropped connection."""
    alignment = vlm_read.compare(_docling_table(), payload)

    assert not alignment.usable, why
    assert alignment.disagreements == set()
    assert alignment.unmatched_rows == set()
    assert alignment.reason


def test_a_structurally_different_table_is_unusable_not_disagreeing():
    """A read describing some other table entirely -- too few rows match to
    draw any cell-level conclusion."""
    vlm = """| Item | Amount |
| --- | --- |
| Interest income | 1,234 |
| Dividend received | 5,678 |
| Sundry receipts | 91 |"""

    alignment = vlm_read.compare(_docling_table(), vlm)

    assert not alignment.usable
    assert alignment.disagreements == set()
    assert "could be matched" in alignment.reason


# --------------------------------------------------------------------------
# Phase 3 -- Alignment carries content, not just coordinates
# --------------------------------------------------------------------------

def test_a_usable_alignment_exposes_the_vlm_reads_own_content():
    """compare() used to destroy vlm_body/pairs/col_map as locals the moment
    it returned -- everything downstream got a yes/no per cell and nothing
    else. Phase 3's structural work (insert_unclaimed_rows, and whatever
    arbitrates a merged row next) needs the content itself."""
    alignment = vlm_read.compare(_docling_table(), DOCLING_MD)

    assert alignment.usable
    assert alignment.vlm_body, "the second read's own rows must survive"
    assert alignment.vlm_header == ["Particulars", "Note No.", "31st March 2025"]
    assert alignment.pairs, "row pairing must survive"
    assert alignment.col_map, "column mapping must survive"


def test_an_unusable_alignment_carries_no_content_either():
    """The same discipline as disagreements/unmatched_rows: an absent,
    truncated or structurally incomparable read must leave every field empty,
    never a partial or misleading structure a caller might act on."""
    alignment = vlm_read.compare(_docling_table(), "I could not read this table.")

    assert not alignment.usable
    assert alignment.vlm_body == []
    assert alignment.vlm_header == []
    assert alignment.pairs == {}
    assert alignment.col_map == {}


# --------------------------------------------------------------------------
# insert_unclaimed_rows -- rows the VLM found that docling never emitted
# --------------------------------------------------------------------------

def test_a_vlm_only_row_is_inserted_between_its_true_neighbours():
    """The one place this pipeline can currently ADD a row rather than only
    withhold one docling already produced. "Investment Properties" here has
    no docling counterpart at all -- not garbled, not misaligned, simply
    absent -- and was, before this, invisible everywhere downstream."""
    docling_md = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 12,859 |
| Total (1) | | 41,314 |"""
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Investment Properties | B-1 (b) | 500 |
| Capital work in progress | B-1 (a) | 12,859 |
| Total (1) | | 41,814 |"""
    table = _docling_table(docling_md)
    alignment = vlm_read.compare(table, vlm)
    assert alignment.usable

    inserted, remap = vlm_read.insert_unclaimed_rows(table, alignment)

    assert len(inserted) == 1
    new_row = inserted[0]
    assert table.label(new_row) == "Investment Properties"
    assert table.cell(new_row, 2).value == 500.0
    # Lands strictly between its real neighbours, not tacked on at the end.
    assert "Property, Plant" in table.label(new_row - 1)
    assert "Capital work" in table.label(new_row + 1)
    # Every pre-existing row keeps its own content, just shifted downward
    # past the insertion point -- the whole reason remap is returned at all.
    assert remap[0] == 0
    assert remap[1] == new_row + 1
    assert remap[2] == new_row + 2
    assert table.label(remap[1]) == "Capital work in progress"


def test_insert_unclaimed_rows_skips_a_row_that_resembles_an_unmatched_docling_row():
    """A row docling read badly (present on the page, just scored below the
    pairing threshold) must not get a SECOND, fresh copy inserted from the
    VLM's clean read of the same line -- that would duplicate it, and could
    double the true value in whatever column later foots. That defect needs
    structural repair, not insertion; insertion is reserved for a row docling
    never emitted at all, which this deliberately is not."""
    md = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Investment Propertles | B-1 (b) | |
| Capital work in progress | B-1 (a) | 12,859 |"""
    table = _docling_table(md)

    # Constructed directly rather than through compare(): this pins down
    # insert_unclaimed_rows's OWN contract regardless of how compare()'s
    # greedy row-pairing happens to behave on any particular input.
    alignment = vlm_read.Alignment(
        usable=True,
        # Grounded explicitly, so this keeps testing the duplicate guard rather
        # than passing trivially on the new grounding gate.
        grounded=True,
        pairs={0: 0, 2: 2},
        unmatched_rows={1},
        vlm_body=[
            ["Property, Plant & Equipment", "B-1 (a)", "28,455"],
            ["Investment Properties", "B-1 (b)", "500"],
            ["Capital work in progress", "B-1 (a)", "12,859"],
        ],
        col_map={2: 2},
    )

    inserted, remap = vlm_read.insert_unclaimed_rows(table, alignment)

    assert inserted == []
    assert len(table.rows) == 3
    assert remap == {0: 0, 1: 1, 2: 2}


def test_insert_unclaimed_rows_is_a_noop_on_an_unusable_alignment():
    table = _docling_table()
    unusable = vlm_read.Alignment(usable=False)

    inserted, remap = vlm_read.insert_unclaimed_rows(table, unusable)

    assert inserted == []
    assert remap == {r: r for r in range(len(table.rows))}


def test_insert_unclaimed_rows_never_shrinks_or_reorders_existing_rows():
    """The invariant that makes remap trustworthy: every row present before
    the call is present after it, unmoved relative to every other original
    row -- only new rows are spliced in between them."""
    docling_md = DOCLING_MD
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Investment Properties | B-1 (b) | 500 |
| Capital work in progress | B-1 (a) | 12,859 |
| Land | B-1 (d) | 77 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""
    table = _docling_table(docling_md)
    original_labels = [table.label(r) for r in range(len(table.rows))]
    alignment = vlm_read.compare(table, vlm)
    assert alignment.usable

    inserted, remap = vlm_read.insert_unclaimed_rows(table, alignment)

    assert len(inserted) >= 1
    for old_r, label in enumerate(original_labels):
        assert table.label(remap[old_r]) == label


# --------------------------------------------------------------------------
# merge_wrapped_labels -- one printed line docling split across two rows
# --------------------------------------------------------------------------

# The real shape, from OD-SPSU-SO-032 2023-24 SFS: the printed label wraps
# onto a second line, TableFormer reads two rows, and the note number and
# BOTH values attach to the second fragment. Neither row alone is findable as
# "Property, Plant and Equipment".
WRAPPED_LABEL_MD = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Cash and cash equivalents | 12 | 3,19,452.35 |
| (a) Property, Plant and Equipment |  |  |
| [and Intangible assets] | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""

WRAPPED_LABEL_VLM = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Cash and cash equivalents | 12 | 3,19,452.35 |
| (a) Property, Plant and Equipment [and Intangible assets] | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""


def test_a_label_wrapped_across_two_rows_is_rejoined_with_its_figures():
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, WRAPPED_LABEL_VLM)
    assert alignment.usable

    merged, remap = vlm_read.merge_wrapped_labels(table, alignment)

    assert len(merged) == 1
    row = merged[0]
    assert table.label(row) == "(a) Property, Plant and Equipment [and Intangible assets]"
    # The figure is docling's own, untouched -- nothing was chosen or invented.
    assert table.cell(row, 2).value == 68674.61
    assert len(table.rows) == 3
    # Both halves of the wrap now resolve to the one row that replaced them.
    assert remap[1] == row and remap[2] == row
    assert table.label(remap[0]) == "Cash and cash equivalents"
    assert table.label(remap[3]) == "Trade receivables"


def test_a_row_with_its_own_figure_is_never_treated_as_a_fragment():
    """The condition that makes this safe: a row carrying any figure of its
    own is a real line item, whatever its label looks like. Merging it would
    silently discard a real figure -- so a pair where BOTH sides have values
    must be left exactly alone."""
    md = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| (a) Property, Plant and Equipment | 9 | 1,000.00 |
| [and Intangible assets] | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""
    table = _docling_table(md)
    alignment = vlm_read.compare(table, WRAPPED_LABEL_VLM)

    merged, remap = vlm_read.merge_wrapped_labels(table, alignment)

    assert merged == []
    assert len(table.rows) == 3
    assert remap == {0: 0, 1: 1, 2: 2}


def test_a_genuinely_blank_line_item_is_not_swallowed_by_its_neighbour():
    """A real line item that simply has no figure THIS year still pairs to the
    vision model's own row for it, so it is not a fragment and must survive as
    its own row. Without the "label-only row must not itself be paired" check
    this would quietly eat a disclosed-but-nil line."""
    md = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Unsecured Loans and Borrowings | 5 |  |
| Trade receivables | 13 | 1,89,034.64 |"""
    vlm = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Unsecured Loans and Borrowings | 5 | - |
| Trade receivables | 13 | 1,89,034.64 |"""
    table = _docling_table(md)
    alignment = vlm_read.compare(table, vlm)

    merged, _ = vlm_read.merge_wrapped_labels(table, alignment)

    assert merged == []
    assert len(table.rows) == 2
    assert table.label(0) == "Unsecured Loans and Borrowings"


def test_merge_wrapped_labels_is_a_noop_on_an_unusable_alignment():
    table = _docling_table(WRAPPED_LABEL_MD)
    merged, remap = vlm_read.merge_wrapped_labels(table, vlm_read.Alignment(usable=False))

    assert merged == []
    assert len(table.rows) == 4
    assert remap == {r: r for r in range(4)}


# --------------------------------------------------------------------------
# Proof of sight -- a reader that cannot see must never be trusted
#
# Measured on the live deployment, the vision model ACCEPTED images (so
# probe() passed) while perceiving nothing: it read an image printing
# "HELLO 12345 TOTAL" as "text", said "no image was provided" for a full
# balance sheet, and when pushed invented a complete income statement
# ("Total Revenue 1,15,44,822 ... Profit After Tax 9,00,000"). Its generic
# Schedule III labels still paired with docling's, so insert_unclaimed_rows
# spliced invented rows into OD's tables -- and a row LABEL is never redacted.
# --------------------------------------------------------------------------

def _configure_vlm(monkeypatch, reply=None, raises=None, secret_index=23456):
    """Point perceives() at a fake endpoint and pin its random number."""
    import secrets

    monkeypatch.setattr(Config, "VLM_ENABLED", True)
    monkeypatch.setattr(Config, "VLM_BASE_URL", "http://fake")
    monkeypatch.setattr(Config, "VLM_MODEL", "fake-model")
    monkeypatch.setattr(secrets, "randbelow", lambda n: secret_index)

    def fake_post(url, headers=None, timeout=None, json=None):
        if raises is not None:
            raise raises
        return _FakeResponse(reply)

    monkeypatch.setattr(vlm_read, "httpx",
                        type("_FakeHttpx", (), {"post": staticmethod(fake_post)}))
    return str(secret_index + 100000)


def test_a_model_that_reads_the_number_back_exactly_perceives(monkeypatch):
    secret = _configure_vlm(monkeypatch, reply=None)
    _configure_vlm(monkeypatch, reply=secret)
    assert vlm_read.perceives() is True


def test_indian_digit_grouping_in_the_reply_still_counts_as_an_exact_read(monkeypatch):
    _configure_vlm(monkeypatch, reply="1,23,456")
    assert vlm_read.perceives() is True


@pytest.mark.parametrize("reply", [
    "text",                                              # the live model's actual reply
    "I cannot read this because no image was provided.",  # and its other one
    "123457",                                            # close is what a hallucination looks like
    "",
])
def test_a_model_that_cannot_read_the_number_does_not_perceive(monkeypatch, reply):
    _configure_vlm(monkeypatch, reply=reply)
    assert vlm_read.perceives() is False


def test_an_unreachable_endpoint_does_not_perceive(monkeypatch):
    """Fails closed: wrongly skipping a second reader narrows corroboration;
    wrongly trusting a blind one puts invented rows in an auditor's tables."""
    _configure_vlm(monkeypatch, raises=RuntimeError("connection refused"))
    assert vlm_read.perceives() is False


def test_perceives_is_false_when_the_vlm_is_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "VLM_ENABLED", False)
    assert vlm_read.perceives() is False


# The live blind model's invented statement, trimmed, laid over labels docling
# really read. Labels pair; not one figure does.
INVENTED_VLM = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 1,15,44,822 |
| Capital work in progress | B-1 (a) | 85,44,322 |
| Other Intangible Assets | B-1 (c) | 30,00,500 |
| Total (1) | | 9,00,000 |"""


def test_an_invented_table_whose_labels_match_is_discarded_not_trusted():
    """THE regression. Every label pairs, so the old checks called this usable.
    None of the figures the page prints were reproduced, which a reader that
    actually saw the table cannot fail to do."""
    alignment = vlm_read.compare(_docling_table(), INVENTED_VLM)

    assert not alignment.usable
    assert not alignment.grounded
    assert alignment.disagreements == set(), "an invented read must not withhold real figures either"
    assert "invented" in alignment.reason


def test_a_read_that_reproduces_the_figures_is_grounded():
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | 12,859 |
| Other Intangible Assets | B-1 (c) | 2,524 |
| Total (1) | | 43,838 |"""

    alignment = vlm_read.compare(_docling_table(), vlm)

    assert alignment.usable and alignment.grounded


def test_a_read_with_too_few_figures_to_prove_sight_is_not_grounded():
    """One agreeing figure is not proof: a blind model can land one common
    number by luck. It stays usable (it can still flag a disagreement, which
    only ever withholds) but it may not change the table's structure."""
    vlm = """| Particulars | Note No. | 31st March 2025 |
| --- | --- | --- |
| Property, Plant & Equipment | B-1 (a) | 28,455 |
| Capital work in progress | B-1 (a) | - |
| Other Intangible Assets | B-1 (c) | - |
| Total (1) | | - |"""

    alignment = vlm_read.compare(_docling_table(), vlm)

    assert alignment.usable
    assert not alignment.grounded


def test_an_ungrounded_read_never_inserts_a_row():
    table = _docling_table()
    before = [list(r) for r in table.rows]
    alignment = vlm_read.Alignment(
        usable=True,
        grounded=False,
        pairs={1: 0, 2: 2},
        vlm_body=[
            ["Property, Plant & Equipment", "B-1 (a)", "28,455"],
            ["Total Revenue", "", "1,15,44,822"],
            ["Capital work in progress", "B-1 (a)", "12,859"],
        ],
        col_map={2: 2},
    )

    inserted, remap = vlm_read.insert_unclaimed_rows(table, alignment)

    assert inserted == []
    assert table.rows == before


def test_an_ungrounded_read_never_merges_a_label():
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, WRAPPED_LABEL_VLM)
    alignment.grounded = False

    merged, _ = vlm_read.merge_wrapped_labels(table, alignment)

    assert merged == []
    assert len(table.rows) == 4


def test_the_multi_label_glom_is_deliberately_left_alone():
    """SK-SPSU-SSLSA-010's balance sheet: FOUR line items concatenated into one
    row that ALREADY carries a figure. Which of the four that figure belongs to
    cannot be told from the page without a targeted re-read, so this must stay
    untouched rather than be guessed at -- guessing here is exactly how a
    WRONG figure would reach an auditor."""
    md = """| Corpus/Capital Fund And Liabilities | Appendix | Current Year | Previous Year |
| --- | --- | --- | --- |
| Corpus/Capital Fund Reserve and Surplus Earmarked/Endownment Funds Secured Loans and Borrowings | 1 2 3 | 1,51,85,562.98 | 31,11,100.93 |
|  |  | 2,469.00 | 2,469.00 |
| Total |  | 1,51,88,031.98 | 31,13,569.93 |"""
    vlm = """| Corpus/Capital Fund And Liabilities | Appendix | Current Year | Previous Year |
| --- | --- | --- | --- |
| Corpus/Capital Fund | 1 | 1,51,85,562.98 | 31,11,100.93 |
| Reserve and Surplus | 2 | 2,469.00 | 2,469.00 |
| Total |  | 1,51,88,031.98 | 31,13,569.93 |"""
    table = _docling_table(md)
    alignment = vlm_read.compare(table, vlm)

    merged, _ = vlm_read.merge_wrapped_labels(table, alignment)

    assert merged == [], "a row that already carries a figure is never a fragment"


# --------------------------------------------------------------------------
# transcribe -- blind is primary, primed is opt-in
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}, "finish_reason": "stop"}]}


def test_transcribe_reads_blind_by_default(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, timeout=None, json=None):
        captured["json"] = json
        return _FakeResponse("| a |\n| --- |\n| 1 |")

    monkeypatch.setattr(vlm_read, "httpx", type("_FakeHttpx", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(Config, "VLM_BASE_URL", "http://fake")
    monkeypatch.setattr(Config, "VLM_MODEL", "fake-model")

    result = vlm_read.transcribe(np.zeros((10, 10), dtype=np.uint8))

    assert result is not None
    text = captured["json"]["messages"][1]["content"][0]["text"]
    assert text == vlm_read._BLIND_INSTRUCTION
    assert "PREVIOUS READ" not in text


def test_transcribe_primes_only_when_a_previous_read_is_explicitly_passed(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, timeout=None, json=None):
        captured["json"] = json
        return _FakeResponse("| a |\n| --- |\n| 1 |")

    monkeypatch.setattr(vlm_read, "httpx", type("_FakeHttpx", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(Config, "VLM_BASE_URL", "http://fake")
    monkeypatch.setattr(Config, "VLM_MODEL", "fake-model")

    vlm_read.transcribe(np.zeros((10, 10), dtype=np.uint8), previous_markdown="| x |\n| --- |\n| 1 |")

    text = captured["json"]["messages"][1]["content"][0]["text"]
    assert "PREVIOUS READ" in text
    assert text.startswith(vlm_read._INSTRUCTION.split("PREVIOUS READ")[0].strip()[:20])


# --------------------------------------------------------------------------
# The strict-beat trap: a caption whose clean half the second reader matched
# exactly can never be beaten on score -- only the printed page can say it
# wrapped
# --------------------------------------------------------------------------

# The second reader labelled the row with the FIRST half of the caption only
# ("(a) Property, Plant and Equipment") and put the figures on it. Docling
# split the caption in two and attached the figures to the tail. The clean
# half matches the second reader's row at 1.0, and nothing can beat 1.0, so
# `combined > alone_r` can never hold and the merge declines -- exactly when
# it should fire.
TRAP_VLM = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Cash and cash equivalents | 12 | 3,19,452.35 |
| (a) Property, Plant and Equipment | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""


def _trap_lines(lower_x=60.0, upper_x=48.0):
    from app.convert import OcrLine
    return [
        OcrLine("Cash and cash equivalents", 0.99, (48.0, 80.0, 230.0, 92.0)),
        OcrLine("(a) Property, Plant and Equipment", 0.99, (upper_x, 100.0, 230.0, 112.0)),
        OcrLine("[and Intangible assets]", 0.99, (lower_x, 116.0, 190.0, 128.0)),
        OcrLine("Trade receivables", 0.99, (48.0, 140.0, 200.0, 152.0)),
    ]


def test_the_trap_is_real_without_printed_geometry():
    """Documented, not fixed: with no OCR lines the deterministic branch is
    unavailable, so behaviour is exactly what it was before."""
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, TRAP_VLM)
    merged, remap = vlm_read.merge_wrapped_labels(table, alignment)
    assert merged == []
    assert len(table.rows) == 4


def test_a_clean_caption_half_no_longer_blocks_a_merge_the_printed_page_confirms():
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, TRAP_VLM)
    assert alignment.usable and alignment.grounded

    merged, remap = vlm_read.merge_wrapped_labels(table, alignment, _trap_lines())

    assert len(merged) == 1
    row = merged[0]
    assert table.label(row) == "(a) Property, Plant and Equipment [and Intangible assets]"
    assert table.cell(row, 2).value == 68674.61      # docling's own figure, untouched
    assert len(table.rows) == 3
    assert remap[1] == row and remap[2] == row


def test_the_relaxed_merge_still_refuses_a_tail_printed_shallower_than_its_caption():
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, TRAP_VLM)
    merged, _ = vlm_read.merge_wrapped_labels(table, alignment, _trap_lines(lower_x=30.0))
    assert merged == []


def test_the_relaxed_merge_never_joins_two_sibling_items_when_one_matches_cleanly():
    """The sibling guard now lives in the enumerator, not the score ordering:
    the lower row opens with its own printed (b), so it is a new line item
    however well the upper half matches the second reader."""
    md = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Cash and cash equivalents | 12 | 3,19,452.35 |
| (a) Property, Plant and Equipment |  |  |
| (b) Tools | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""
    from app.convert import OcrLine
    lines = [
        OcrLine("Cash and cash equivalents", 0.99, (48.0, 80.0, 230.0, 92.0)),
        OcrLine("(a) Property, Plant and Equipment", 0.99, (48.0, 100.0, 230.0, 112.0)),
        OcrLine("(b) Tools", 0.99, (60.0, 116.0, 190.0, 128.0)),
        OcrLine("Trade receivables", 0.99, (48.0, 140.0, 200.0, 152.0)),
    ]
    table = _docling_table(md)
    alignment = vlm_read.compare(table, TRAP_VLM)
    merged, _ = vlm_read.merge_wrapped_labels(table, alignment, lines)
    assert merged == []
    assert len(table.rows) == 4


def test_the_relaxed_merge_still_needs_the_second_reader_to_corroborate_the_joined_text():
    """A geometry-confirmed continuation is not enough on its own: the joined
    label must still resemble what the second reader actually saw. This is
    what keeps this a VLM-confirmed path distinct from the geometry-only
    join in structure_repair."""
    vlm_unrelated = """| Particulars | Note No. | Figures as at 31st March, 2024 |
| --- | --- | --- |
| Cash and cash equivalents | 12 | 3,19,452.35 |
| Goodwill | 10 | 68,674.61 |
| Trade receivables | 13 | 1,89,034.64 |"""
    table = _docling_table(WRAPPED_LABEL_MD)
    alignment = vlm_read.compare(table, vlm_unrelated)
    merged, _ = vlm_read.merge_wrapped_labels(table, alignment, _trap_lines())
    assert merged == []
