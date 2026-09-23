"""Tests for chat_tools.py's pure-logic helpers.

The DB-dependent functions (_keyword_table_search's ts_rank ordering,
retrieve_sar_context end-to-end) were verified manually against the live
Postgres instance — see the session notes for Coal_India_2024_2025's Key
Audit Matters query, which is what surfaced both bugs these tests guard.
"""

from sar_prod_v3.chat_tools import _is_bare_table_placeholder


def test_bare_caption_is_a_placeholder():
    assert _is_bare_table_placeholder(
        "Table: Key Audit Matters [Coal_India_2024_2025_tbl_0146]"
    ) is True


def test_bare_caption_with_curly_quoted_title_is_a_placeholder():
    assert _is_bare_table_placeholder(
        "Table: “Annexure 1” [IRCTC_2024_2025_tbl_0007]"
    ) is True


def test_caption_followed_by_real_content_is_not_a_placeholder():
    """Ingestion sometimes glues the caption onto a following text
    fragment in the same chunk (a "SUB_" chunk) — that chunk carries real
    content and must not be dropped."""
    assert _is_bare_table_placeholder(
        "Table: Key Audit Matters [Coal_India_2024_2025_tbl_0067]\n"
        "other equity to that extent of the respective years."
    ) is False


def test_ordinary_narrative_text_is_not_a_placeholder():
    assert _is_bare_table_placeholder(
        "Key audit matters are those matters that, in our professional "
        "judgment, were of most significance in the audit."
    ) is False


def test_empty_or_none_is_not_a_placeholder():
    assert _is_bare_table_placeholder("") is False
    assert _is_bare_table_placeholder(None) is False
