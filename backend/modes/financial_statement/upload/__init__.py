"""Live upload for the Financial Statement mode.

An uploaded statement is converted by the ingestion service, held in this
process's memory for the life of its conversation, and served to the *existing*
FS tool library through `bridge.install()`. Nothing is written to disk and
nothing reaches Postgres.
"""
