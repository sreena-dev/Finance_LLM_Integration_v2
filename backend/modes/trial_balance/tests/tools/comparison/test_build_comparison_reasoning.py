"""Wave 8 remark #19 (self-consistency validator): an LLM-authored observation citing
a "Section N" outside this report's real 1-25 range is a hallucinated cross-reference
and must be stripped, not silently shipped."""

from modes.trial_balance.pipeline.tools.comparison import _strip_invalid_section_citations


def test_valid_section_citation_is_kept():
    text = "See Section 7 for the full comparative variance schedule."
    assert _strip_invalid_section_citations(text) == text


def test_out_of_range_section_citation_is_stripped():
    text = "See Section 42 for further detail."
    result = _strip_invalid_section_citations(text)
    assert "Section 42" not in result


def test_zero_section_citation_is_stripped():
    text = "As discussed in Section 0 above."
    result = _strip_invalid_section_citations(text)
    assert "Section 0" not in result


def test_text_with_no_citation_is_unchanged():
    text = "This account requires corroboration against its underlying schedule."
    assert _strip_invalid_section_citations(text) == text


def test_empty_text_returns_unchanged():
    assert _strip_invalid_section_citations("") == ""
    assert _strip_invalid_section_citations(None) is None
