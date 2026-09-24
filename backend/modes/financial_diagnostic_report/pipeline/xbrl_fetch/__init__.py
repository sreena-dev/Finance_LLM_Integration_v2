"""
xbrl_fetch — direct-fetch client for `as_db`, the hosted Postgres of parsed MCA XBRL
filings (tables: documents, financial_facts, disclosures).

WHY THIS IS NOT A SECOND `fs_db`
---------------------------------
`fs_db` and `pipeline/fdr/panel.py` exist to make PDF/OCR-extracted figures trustworthy:
every cell carries a guessed `unit_scale`, a `unit_confidence`, a `verify_verdict` from
cross-statement arithmetic checks, and `headline.py` layers a decimal/digit-grouping
error detector on top of that. All of it compensates for extraction uncertainty that
does not exist here — `as_db` facts are tagged XBRL: the unit is `INR` on every money
concept (verified against the full corpus), the period is a structured `fy_end` column,
and the value is exact, not read off a scanned page.

Routing this data through that trust apparatus would mean fabricating "HIGH"/"CONFIRMED"
on every cell for a machine that never guessed anything — a shape with no content behind
it. So this package fetches straight from `as_db` and shapes the result directly into the
JSON the frontend already renders (see `xbrl_dashboard.py`), with no intermediate panel,
fact-store or trust layer.

WHAT IS DELIBERATELY KEPT
--------------------------
Only the parts of the `fs_db` precedent that are genuinely about talking to Postgres
safely, not about PDF trust: a thread-local read-only connection (`xbrl_conn.py`,
mirrors `fs_db/db.py`) and env-var configuration with no hardcoded credentials
(`xbrl_config.py`, mirrors `fs_db/config.py`).
"""
