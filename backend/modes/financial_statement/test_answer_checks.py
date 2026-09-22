"""How an answer was checked -- read off the rendered answer, not the display copy.

The pipeline writes `**Confidence**: X`, an optional "Confidence was reduced"
blockquote, `**Tools used**: a, b`, or a "No tool was called" notice. The reader
never sees them in the answer text (that display decision stands); they are
returned as `checks` so the UI can offer them in one collapsed section.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest modes/financial_statement/test_answer_checks.py -q
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
for path in (os.path.join(_HERE, "pipeline"), _BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("REPORTS_DB_PASSWORD", "test")

from modes.financial_statement import adapter  # noqa: E402


def test_confidence_and_tools_are_read_from_the_rendered_answer():
    answer = (
        "### Summary\nFinance costs rose.\n\n"
        "**Confidence**: Medium\n"
        "> Confidence was reduced from *High* automatically: a tool reported figures "
        "it could not extract.\n"
        "**Tools used**: search_corpus, read_uploaded_table, footing_check"
    )
    checks = adapter._extract_checks(answer)
    assert checks["confidence"] == "Medium"
    assert checks["reduced_from"] == "High"
    assert "could not extract" in checks["reduced_reason"]
    assert checks["tools_used"] == ["search_corpus", "read_uploaded_table", "footing_check"]
    assert checks["unsourced"] is False


def test_an_answer_that_called_no_tool_is_marked_unsourced():
    answer = (
        "Some answer.\n"
        "> **No tool was called for this answer** — it was not verified against the "
        "knowledge base or the annual reports. Treat it as unsourced."
    )
    checks = adapter._extract_checks(answer)
    assert checks["unsourced"] is True
    assert checks["tools_used"] == []


def test_a_plain_answer_yields_empty_checks_not_an_error():
    checks = adapter._extract_checks("Just text.")
    assert checks == {
        "confidence": None, "reduced_from": None, "reduced_reason": None,
        "tools_used": [], "unsourced": False,
    }
    assert adapter._extract_checks("")["tools_used"] == []


def test_the_display_copy_is_still_cleaned():
    text = "Body.\n> Confidence was reduced from *High* automatically: because.\nMore."
    assert "reduced" not in adapter._strip_confidence_caveat(text).lower()
