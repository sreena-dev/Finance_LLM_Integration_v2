"""Postgres connection helper for the finance_llm database."""

from __future__ import annotations

import psycopg2

from yukta_rag.core.config import FINANCE_DSN, REFERENCE_DSN


def get_connection():
    """Open a new psycopg2 connection to the finance_llm database."""
    return psycopg2.connect(FINANCE_DSN)


def get_reference_connection():
    """Open a new psycopg2 connection to the reference corpora database.

    Holds EAC opinions, SA 700, Schedule III, CARO/CAG and Ind AS appendices.
    """
    return psycopg2.connect(REFERENCE_DSN)
