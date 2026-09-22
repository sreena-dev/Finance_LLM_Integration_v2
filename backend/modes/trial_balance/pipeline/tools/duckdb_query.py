"""Selective, workload-routed SQL query engine over persisted Parquet artifacts.

Polars remains the canonical compute/DataFrame engine for the pipeline --
this module is not a general replacement for it. DuckDB is introduced ONLY
where a tool does a genuine point/filter lookup against a full Parquet file
(the empirically-confirmed pattern in backend/tools/chat.py: every call does
`pl.read_parquet(full_file)` then a Python-side `.filter()` for a single GL
code or substring match). It is deliberately NOT used for:
  - single-pass transform/aggregation tools (canonical, materiality, risk,
    fsli, variance, netting, ...) -- Polars already does this in one lazy/
    eager pipeline, DuckDB would add a second engine for no measurable gain.
  - chat.py tools whose logic is a multi-file join, regex extraction, or
    custom sort/dispatch (chat_query_comparison, chat_query_flags,
    chat_query_fsli_table) -- rewriting that business logic into SQL risks
    introducing correctness bugs in an audit tool; those stay on Polars and
    get the Valkey caching layer instead (see chat.py's chat_cacheable).

DuckDB here is pure query pushdown: it reads Parquet, filters, and hands the
result back as a Polars DataFrame via DuckDB's built-in `.pl()` interop, so
callers never see a second DataFrame type in the codebase.
"""

from typing import Optional

import duckdb
import polars as pl


def query_parquet(sql: str, params: Optional[list] = None, parquet_path: Optional[str] = None) -> pl.DataFrame:
    """Run `sql` against a Parquet file registered as the view `t`, e.g.:
        query_parquet("SELECT * FROM t WHERE gl_code = ?", [gl_code], parquet_path)
    A fresh in-memory connection per call (:memory:, no persistence) -- the
    simplest and safest option under a multi-worker deployment, since no
    connection or view state is ever shared across calls or processes. Move
    to a pooled connection only if a load test shows connection setup itself
    is the bottleneck (see tests/test_duckdb_query.py's benchmark note).
    Parameterized via DuckDB's `?` binding -- never string-interpolate the
    path or filter values into `sql`.
    """
    con = duckdb.connect(":memory:")
    try:
        # DuckDB does not support a prepared-statement placeholder inside a DDL
        # statement's read_parquet(...) call, so the path is embedded as an
        # escaped string literal here -- it is never end-user input directly:
        # every caller passes a path produced deterministically by another
        # tool's own artifact output, already validated to exist by the
        # pipeline_tool decorator's artifact-existence check. `sql`'s own
        # filter VALUES (the actual variable, potentially chat-user-derived
        # input like a GL code or search substring) still go through `?`
        # binding via con.execute(sql, params) below -- never interpolated.
        escaped_path = str(parquet_path).replace("'", "''")
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{escaped_path}')")
        result = con.execute(sql, params or [])
        return result.pl()
    finally:
        con.close()
