"""yukta_rag — 2-agent Finance RAG built on the Yukta agent framework.

The Query Planner expands a question into multiple search queries; the Research
Analyst retrieves from both corpora and combines the results into one answer.
"""

__all__ = ["FinanceRAG"]


def __getattr__(name):  # lazy import so submodules stay independently importable
    if name == "FinanceRAG":
        from yukta_rag.chat.pipeline import FinanceRAG
        return FinanceRAG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
