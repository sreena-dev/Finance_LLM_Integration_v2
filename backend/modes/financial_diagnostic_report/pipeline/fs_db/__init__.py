"""
fs_db — self-contained FS audit flow over the `finance_llm` Postgres KB.

This package is deliberately standalone: it imports nothing from the sibling
`rag/` package and owns its own DB config, connection helper, and schema map.
The whole folder can be deleted in one move when the DB is migrated — the only
things to edit for a schema change are `config.py` (connection) and `schema.py`
(table/column identifiers).
"""
__all__ = ["config", "schema", "db", "models", "md_parser", "repository",
           "precheck", "arithmetic", "recall", "compliance", "flow", "tracing"]
