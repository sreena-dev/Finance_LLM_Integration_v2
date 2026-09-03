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

import fakeredis  # noqa: E402

from modes.financial_statement.upload import store  # noqa: E402


@pytest.fixture(autouse=True)
def _fake_redis():
    fake = fakeredis.FakeRedis(decode_responses=True)
    store.STORE._client = fake
    yield fake
    store.STORE._client = None
