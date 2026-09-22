"""Shared fixtures for the FS upload module's tests.

Every test file here calls ``store.STORE.put/get/list/delete/drop_conversation``
directly and expects that to just work with no external service running --
exactly the zero-setup property this module's tests have always had, even
before the store moved off a plain in-process dict.

``fakeredis`` (a pure-Python, in-process, Redis-protocol-compatible double)
preserves that property after the move to a real Redis backend: this
`autouse` fixture swaps ``store.STORE``'s lazily-constructed client for a
fake one before every test in this directory and tears it down after, so
none of the existing test files need to know the backend changed at all.
Autouse fixtures run before other same-scope fixtures a test requests, which
is what makes this safe to rely on without every fixture that touches the
store (e.g. ``test_bridge.py``'s ``uploaded``) having to request it by name.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODE = os.path.dirname(_HERE)
_BACKEND = os.path.dirname(os.path.dirname(_MODE))
for path in (os.path.join(_MODE, "pipeline"), _BACKEND):
    if path not in sys.path:
        sys.path.insert(0, path)

# store.py's _redis() raises UploadStoreError if this is unset -- never
# actually dialled out to since the fixture below replaces the client before
# any test runs, but the module still reads it at construction time.
os.environ.setdefault("ARTHA_REDIS_URL", "redis://localhost:6379/0")

# Load the backend's .env if there is one, so `test_pgstore.py` can find a
# platform database when the suite is run from the repository root rather than
# from `backend/`. Without this it skipped every persistence test and reported
# a green run that had verified nothing about persistence at all.
#
# This does NOT make the other tests here touch a database: `_no_postgres`
# below forces persistence off for every one of them.
try:  # pragma: no cover - environment plumbing
    from dotenv import load_dotenv

    for _candidate in (
        os.path.join(_BACKEND, ".env"),
        os.path.join(os.path.dirname(_BACKEND), ".env"),
    ):
        if os.path.isfile(_candidate):
            load_dotenv(_candidate, override=False)
            break
except ImportError:
    pass

import fakeredis  # noqa: E402

from modes.financial_statement.upload import store  # noqa: E402


@pytest.fixture(autouse=True)
def _fake_redis():
    fake = fakeredis.FakeRedis(decode_responses=True)
    store.STORE._client = fake
    yield fake
    store.STORE._client = None


@pytest.fixture(autouse=True)
def _no_postgres(monkeypatch):
    """Keep the zero-setup property when uploads became Postgres-backed.

    `store.put` now writes the extraction to Postgres first, because Postgres
    is the system of record and a document that was never durably written must
    not be reported as successfully uploaded. That is right in production and
    wrong for these tests: every file in this directory exercises the Redis
    cache, its eviction and its scoping, and none of them are about
    persistence. Left on, they would need a live database to run at all --
    exactly the setup burden `fakeredis` above exists to avoid.

    Persistence has its own tests in `test_pgstore.py`, which skip when no
    database is configured rather than silently passing.
    """
    monkeypatch.setattr(store, "PERSIST_TO_POSTGRES", False)
