"""One-time ingestion: load the client-supplied Priority Companies List into the
`priority_companies` reference table (see database/schema.sql), which backs the
upload picker's optional Company Details suggestion fields.

Reads the "Priority Companies" sheet only -- "Not Found in Filings" is confirmed
to be exactly the `Found in E_Form Filings == "No"` subset of the first sheet, no
new data. Only company name / CIN / financial year are kept; filing counts, PSU
type, state, sector, and functional area are not needed for the suggestion
feature and are dropped.

Per row: `cin` is normalized to NULL when the source cell is literally "Not
Applicable" (case-insensitive) -- a placeholder string is not a useful autofill
suggestion. `financial_years` is "Years Seen" split on ",", trimmed, sorted
descending (most-recent-first).

Idempotent -- upserts on lower(company_name), so re-running (e.g. the client
sends an updated list later) is safe.

Deliberately NOT run automatically at boot -- this is a reviewed, one-time
operation an operator runs by hand:

    python -m modes.trial_balance.pipeline.scripts.ingest_priority_companies [--path ...] [--dry-run]
"""

import argparse
import logging
from pathlib import Path

import openpyxl

from modes.trial_balance.pipeline.db import upsert_priority_companies
from modes.trial_balance.pipeline.tools.pipeline_tool import _BACKEND_ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_PATH = _BACKEND_ROOT.parent.parent / "docs" / "Priority_Companies_List 1.xlsx"
SHEET_NAME = "Priority Companies"
NOT_APPLICABLE = "not applicable"


def _normalize_cin(raw) -> str:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == NOT_APPLICABLE:
        return None
    return text


def _parse_financial_years(raw) -> list:
    if not raw:
        return []
    years = [y.strip() for y in str(raw).split(",") if y.strip()]
    return sorted(set(years), reverse=True)


def parse_workbook(path: Path) -> list:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET_NAME]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        company_name = (row[1] or "").strip() if row[1] else None
        if not company_name:
            continue
        rows.append({
            "company_name": company_name,
            "cin": _normalize_cin(row[2] if len(row) > 2 else None),
            "financial_years": _parse_financial_years(row[5] if len(row) > 5 else None),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.path.exists():
        parser.error(f"File not found: {args.path}")

    rows = parse_workbook(args.path)
    logger.info("Parsed %d compan(y/ies) from %s.", len(rows), args.path)

    if args.dry_run:
        for r in rows[:10]:
            logger.info("[dry-run] would upsert: %s", r)
        logger.info("[dry-run] %d row(s) total, no DB write performed.", len(rows))
        return

    count = upsert_priority_companies(rows)
    logger.info("Upserted %d compan(y/ies) into priority_companies.", count)


if __name__ == "__main__":
    main()
