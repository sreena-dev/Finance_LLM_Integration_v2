"""Tests for SARReportPipeline._looks_like_a_report — the sanity check that
stops a "successful" (no-exception) writer call from embedding a non-report
response verbatim into the memo.

Found in production: a GENERATION_MODEL casing mismatch (e.g.
"gemma-4-26B-A4B-it" vs the endpoint's actual "gemma-4-26b-a4b-it") makes
yukta's context-window auto-detection raise internally, which agent.py
silently catches and falls back to a default of 8192 tokens instead of the
model's real (much larger) window. A large enough prompt then gets truncated
before it reaches the model, dropping the actual data package and the
closing "generate the report now" instruction — the model responds to what's
left (generic system-prompt framing) with something like "Please provide the
data package..." instead of a report. `_call_writer` used to embed that
verbatim into a formal audit deliverable.
"""

from sar_prod_v3.pipeline.sar_report_pipeline import SARReportPipeline


def test_rejects_the_actual_garbage_seen_in_production():
    garbage = (
        "Please provide the data package (Main SAR Report, CARO 2020, IFC annexures, "
        "Financial Statements, and relevant excerpts).\n"
        "Once the package is provided, I will execute the review according to your "
        "instructions, applying the strict definitions for FINDING, RISK_FLAG, and "
        "AUDIT_POINTER, and adhering to the mandatory tone and citation protocols.\n"
        "I am ready to begin the review upon receipt of the data."
    )
    assert SARReportPipeline._looks_like_a_report(garbage) is False


def test_accepts_a_real_report_with_both_part_headers():
    valid = (
        "# SAR REVIEW — ACME LTD FY 2023-24\n\n"
        "## PART 1 — EXECUTIVE SUMMARY\n\n### 1.1 Input Summary\n...\n\n"
        "## PART 2 — DETAILED MEMORANDUM\n\n### 2.1 Input Package Completeness\n..."
    )
    assert SARReportPipeline._looks_like_a_report(valid) is True


def test_rejects_missing_either_part_header():
    only_part_1 = "## PART 1 — EXECUTIVE SUMMARY\n\nSome content with no second part."
    only_part_2 = "## PART 2 — DETAILED MEMORANDUM\n\nSome content with no first part."
    assert SARReportPipeline._looks_like_a_report(only_part_1) is False
    assert SARReportPipeline._looks_like_a_report(only_part_2) is False


def test_rejects_empty_or_none():
    assert SARReportPipeline._looks_like_a_report("") is False
    assert SARReportPipeline._looks_like_a_report("   ") is False
    assert SARReportPipeline._looks_like_a_report(None) is False


def test_case_insensitive_part_header_match():
    lower = "## part 1 — executive summary\n...\n## part 2 — detailed memorandum\n..."
    assert SARReportPipeline._looks_like_a_report(lower) is True
