"""Shared fixtures for the Phase-2 screen tests.

Imported by tests/tools/*/test_build_*.py via the `phase2_run` fixture registered in
tests/conftest.py. Kept separate from conftest.py so the Phase-2 additions stay
reviewable as a unit rather than diffused into the existing fixture file.
"""

import json

import polars as pl
import pytest

from modes.trial_balance.pipeline.tools import CANONICAL_TB_ALL_COLUMNS

_NUMERIC = {"opening_balance", "debit", "credit", "closing_balance"}

# A trial balance shaped to exercise every Phase-2 screen at once. Each row is here
# for a stated reason -- a row that triggers nothing is noise in a fixture.
SCREEN_ROWS = [
    # REL-01 outside band (receivables 80% of revenue) + REL-02 counterpart absent
    {"gl_code": "3000", "gl_name": "Sales Revenue", "closing_balance": -100_000_000.0,
     "main_head": "Revenue from operations", "sub_head_1": "Revenue",
     "account_type": "Income", "mapped_status": "MAPPED"},
    {"gl_code": "1500", "gl_name": "Trade Receivable - Domestic", "closing_balance": 80_000_000.0,
     "main_head": "Current assets", "sub_head_1": "Trade receivables",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    # REL-07 counterpart absent: borrowings with no finance cost
    {"gl_code": "2100", "gl_name": "Term Loan from Bank", "closing_balance": -30_000_000.0,
     "main_head": "Non-current liabilities", "sub_head_1": "Borrowings",
     "account_type": "Liability", "mapped_status": "MAPPED"},
    # Abnormal sign: liability carrying a debit balance
    {"gl_code": "2200", "gl_name": "Trade Payable - Vendor A", "closing_balance": 4_500_000.0,
     "main_head": "Current liabilities", "sub_head_1": "Trade payables",
     "account_type": "Liability", "mapped_status": "MAPPED"},
    # Contra -- must be EXCLUDED from abnormal-sign findings, not flagged
    {"gl_code": "1290", "gl_name": "Accumulated Depreciation on Plant", "closing_balance": -12_000_000.0,
     "main_head": "Non-current assets", "sub_head_1": "Property, Plant and Equipment",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    # PPE + depreciation present -> REL-05 must NOT raise a counterpart gap
    {"gl_code": "1200", "gl_name": "Plant and Machinery", "closing_balance": 60_000_000.0,
     "main_head": "Non-current assets", "sub_head_1": "Property, Plant and Equipment",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    {"gl_code": "4100", "gl_name": "Depreciation", "closing_balance": 6_000_000.0,
     "main_head": "Expenses", "sub_head_1": "Depreciation",
     "account_type": "Expense", "mapped_status": "MAPPED"},
    # BELOW clearly-trivial but propriety by nature -> materiality lens must elevate
    {"gl_code": "4900", "gl_name": "Bad Debts Written Off", "closing_balance": 40_000.0,
     "main_head": "Expenses", "sub_head_1": "Other expenses",
     "account_type": "Expense", "mapped_status": "MAPPED"},
    # Suspense with a balance -> override indicator + relationship signal
    {"gl_code": "7001", "gl_name": "Suspense Account", "closing_balance": 3_400_000.0,
     "main_head": "Current assets", "sub_head_1": "Other current assets",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    # Grant -> public-sector lens (purpose + authorisation) and going-concern support
    {"gl_code": "5100", "gl_name": "Grant-in-Aid Received from Ministry", "closing_balance": -25_000_000.0,
     "main_head": "Current liabilities", "sub_head_1": "Other current liabilities",
     "account_type": "Liability", "mapped_status": "MAPPED"},
    # Mobilisation advance -> public-sector recovery question
    {"gl_code": "1600", "gl_name": "Advance to Contractor - Mobilisation", "closing_balance": 9_000_000.0,
     "main_head": "Current assets", "sub_head_1": "Other current assets",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    # Payroll with no PF/ESI/TDS -> statutory screen + counterpart REL-08
    {"gl_code": "6100", "gl_name": "Salaries and Wages", "closing_balance": 20_000_000.0,
     "main_head": "Expenses", "sub_head_1": "Employee benefit expenses",
     "account_type": "Expense", "mapped_status": "MAPPED"},
    # Fraud-sensitive heads -> override indicators
    {"gl_code": "1100", "gl_name": "Cash in Hand", "closing_balance": 2_500_000.0,
     "main_head": "Current assets", "sub_head_1": "Cash and cash equivalents",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    {"gl_code": "2900", "gl_name": "Loan to Director", "closing_balance": 5_000_000.0,
     "main_head": "Current assets", "sub_head_1": "Loans",
     "account_type": "Asset", "mapped_status": "MAPPED"},
    # Net worth eroded -> going-concern indicator (capital 10m vs losses 45m)
    {"gl_code": "8000", "gl_name": "Share Capital", "closing_balance": -10_000_000.0,
     "main_head": "Equity", "sub_head_1": "Equity share capital",
     "account_type": "Equity", "mapped_status": "MAPPED"},
    {"gl_code": "8100", "gl_name": "Accumulated Losses", "closing_balance": 45_000_000.0,
     "main_head": "Equity", "sub_head_1": "Other equity",
     "account_type": "Equity", "mapped_status": "MAPPED"},
    # Unmapped -> orphan signal; must be SKIPPED by abnormal-sign (no known normal side)
    {"gl_code": "9999", "gl_name": "Misc Unclassified", "closing_balance": 100_000.0,
     "mapped_status": "UNMATCHED"},
]


def write_canonical(path, rows, tb_doc_id="TEST"):
    full = [{c: r.get(c, 0.0 if c in _NUMERIC else None) for c in CANONICAL_TB_ALL_COLUMNS} for r in rows]
    for f in full:
        f["tb_doc_id"] = tb_doc_id
    schema = {c: (pl.Float64 if c in _NUMERIC else pl.Utf8) for c in CANONICAL_TB_ALL_COLUMNS}
    pl.DataFrame(full, schema=schema).write_parquet(path)
    return path


@pytest.fixture
def phase2_run(tmp_path):
    """A run directory holding the screen-exercising canonical TB plus the upstream
    artifacts the Phase-2 screens read (materiality, sensitive accounts, Layer-1
    results, anomaly findings, consolidated exceptions, data sufficiency).

    Returns (canonical_tb_path, run_dir)."""
    tb = write_canonical(tmp_path / "canonical_tb.parquet", SCREEN_ROWS)

    (tmp_path / "materiality.json").write_text(json.dumps({
        "selected_materiality": {"overall_materiality": 1_000_000.0},
        "thresholds": {"overall": 1_000_000.0, "performance": 750_000.0, "clearly_trivial": 50_000.0},
    }), encoding="utf-8")

    (tmp_path / "sensitive_accounts.json").write_text(json.dumps({"account_sensitivity": [
        {"gl_code": "4900", "gl_name": "Bad Debts Written Off",
         "account": "4900 - Bad Debts Written Off", "category": "propriety", "balance": 40_000.0},
        {"gl_code": "5100", "gl_name": "Grant-in-Aid Received from Ministry",
         "account": "5100 - Grant-in-Aid Received from Ministry",
         "category": "grant_subsidy", "balance": -25_000_000.0},
    ]}), encoding="utf-8")

    (tmp_path / "layer1_results.json").write_text(json.dumps([
        {"rule": "TB-000", "rule_name": "Sign convention", "status": "PASS", "message": "positive = debit."},
        {"rule": "TB-026", "rule_name": "Currency and unit", "status": "WARNING", "message": "Scale not declared."},
        {"rule": "TB-029", "rule_name": "Source system", "status": "WARNING", "message": "Not supplied."},
        {"rule": "TB-009", "rule_name": "Total Dr = Total Cr", "status": "PASS", "message": "Balanced."},
    ]), encoding="utf-8")

    (tmp_path / "anomaly_findings.json").write_text(json.dumps({"findings": [
        {"rule_id": "TB-023", "account": "7001", "description": "Round balance", "trigger_value": 3_400_000},
    ]}), encoding="utf-8")

    (tmp_path / "consolidated_exceptions.json").write_text(json.dumps({"exceptions": [
        {"metadata": {"exception_id": "E1"},
         "business_context": {"gl_code": "1500", "gl_name": "Trade Receivable - Domestic"},
         "hierarchy_context": {"line_item": "Trade receivables", "fs_head": "Current assets"},
         "scoring": {"severity": "High"}, "financial_context": {"closing_balance": 80_000_000.0},
         "audit_planning": {"required_evidence": ["Ledgers"]}},
        {"metadata": {"exception_id": "E2"},
         "business_context": {"gl_code": "5100", "gl_name": "Grant-in-Aid Received from Ministry"},
         "hierarchy_context": {"line_item": "Other current liabilities", "fs_head": "Current liabilities"},
         "scoring": {"severity": "High"}, "financial_context": {"closing_balance": -25_000_000.0},
         "audit_planning": {"required_evidence": ["Contracts/Agreements"]}},
    ]}), encoding="utf-8")

    (tmp_path / "data_sufficiency.json").write_text(
        json.dumps({"grade": "Medium", "rationale": "94% mapped."}), encoding="utf-8")

    return str(tb), tmp_path


@pytest.fixture
def minimal_tb(tmp_path):
    """A single-row canonical TB for degradation tests -- exercises the path where a
    screen runs with no upstream artifacts present at all."""
    return str(write_canonical(tmp_path / "canonical_tb.parquet", [
        {"gl_code": "1001", "gl_name": "Cash in Hand", "closing_balance": 1000.0,
         "main_head": "Current assets", "account_type": "Asset", "mapped_status": "MAPPED"},
    ]))
