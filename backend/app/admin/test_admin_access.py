"""Access control for the super-admin dashboard -- the part that must never regress.

Hermetic: no database, no model, no network. Auth is stubbed with dependency
overrides; the "every route is guarded" test runs against the real assembled app.

Run:  python -m pytest app/admin/test_admin_access.py
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

os.environ.setdefault("ARTHA_JWT_SECRET", "test-secret")

import pytest  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin import queries  # noqa: E402
from app.admin.router import router as admin_router  # noqa: E402
from app.auth import repo, schema  # noqa: E402
from app.auth.deps import CurrentUser, require_super_admin, require_user  # noqa: E402

UID = str(uuid.uuid4())
CID = str(uuid.uuid4())


def _user(admin: bool) -> CurrentUser:
    return CurrentUser(user_id=UID, username="someone", email="s@x.io",
                       is_super_admin=admin)


def _app(user: CurrentUser | None) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router, dependencies=[Depends(require_super_admin)])
    if user is not None:
        app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _admin_get_paths() -> list[str]:
    paths = []
    for route in admin_router.routes:
        p = route.path
        p = p.replace("{user_id}", UID).replace("{conversation_id}", CID)
        p = p.replace("{mode}", "fs")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------

def test_signed_out_gets_401_on_every_admin_route():
    client = _app(None)  # no override: the real require_user runs, no token
    for path in _admin_get_paths():
        r = client.get(path)
        assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_ordinary_user_gets_403_with_json_on_every_admin_route():
    client = _app(_user(admin=False))
    for path in _admin_get_paths():
        r = client.get(path)
        assert r.status_code == 403, f"{path} -> {r.status_code}"
        # JSON detail, or the browser client reads a bare 403 as a "port
        # conflict"; and it must be 403 not 401, which would sign them out.
        assert r.headers["content-type"].startswith("application/json")
        assert "super administrators" in r.json()["detail"]


def test_super_admin_passes_the_guard(monkeypatch):
    monkeypatch.setattr(queries, "audit", lambda *a, **k: None)
    monkeypatch.setattr(queries, "list_users",
                        lambda: {"users": [], "tb": {"available": True}})
    client = _app(_user(admin=True))
    r = client.get("/api/admin/whoami")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.get("/api/admin/users")
    assert r.status_code == 200 and r.json()["users"] == []


def test_every_admin_route_in_the_real_app_is_guarded():
    """Guards against a route being added to the admin router, or a second
    admin-prefixed router being included, without the dependency."""
    from app.main import app as real_app

    # Discovered through the OpenAPI spec rather than `app.routes`: newer
    # FastAPI nests included routers, so a plain route scan can miss them.
    spec_paths = {p: m for p, m in real_app.openapi()["paths"].items()
                  if p.startswith("/api/admin")}
    assert spec_paths, "the admin router is not mounted"
    assert len(spec_paths) >= len(admin_router.routes)
    client = TestClient(real_app)
    for template, methods in spec_paths.items():
        path = (template.replace("{user_id}", UID)
                .replace("{conversation_id}", CID).replace("{mode}", "fs")
                .replace("{item_id}", UID))
        for method in methods:
            r = client.request(method.upper(), path)
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_no_route_can_set_the_admin_flag():
    """There is no write route in the admin router at all, and nothing that
    accepts an is_super_admin field."""
    for route in admin_router.routes:
        assert route.methods <= {"GET", "HEAD"}, f"{route.path} allows {route.methods}"
    from app.auth.models import SignUpRequest

    assert "is_super_admin" not in SignUpRequest.model_fields


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------

def test_missing_admin_column_reads_as_not_admin_without_breaking_reads(monkeypatch):
    monkeypatch.setattr(schema, "admin_column_ready", lambda: False)
    cols = repo._columns()
    assert "false AS is_super_admin" in cols
    monkeypatch.setattr(schema, "admin_column_ready", lambda: True)
    assert repo._columns().endswith(", is_super_admin")


def test_user_rows_default_to_not_admin():
    row = {"user_id": UID, "username": "a", "email": "a@x.io"}
    assert repo._row_to_user(row)["is_super_admin"] is False
    assert repo._row_to_user({**row, "is_super_admin": True})["is_super_admin"] is True


def test_flag_is_a_property_of_the_stored_user_not_of_the_token():
    # CurrentUser is built from the database row (deps.require_user), so a
    # revoke applies on the next request. The token claims carry no admin field.
    from app.auth.security import TokenClaims

    assert "is_super_admin" not in {f for f in TokenClaims.__dataclass_fields__}


# ---------------------------------------------------------------------------
# Telemetry must never affect a query
# ---------------------------------------------------------------------------

def test_recording_an_event_never_raises_even_with_a_broken_database(monkeypatch):
    from app import telemetry

    def boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(schema, "events_ready", lambda: True)
    monkeypatch.setattr(telemetry.auth_db, "db_cursor", boom)
    before = threading.active_count()
    telemetry.record_query_event(user_id=UID, mode="financial-statement", status="ok")
    for t in threading.enumerate():
        if t.name == "artha-query-event":
            t.join(timeout=5)
    assert threading.active_count() <= before + 1  # the writer thread finished


def test_query_text_is_bounded():
    from app import telemetry

    clipped = telemetry._clip("x" * 100_000)
    assert len(clipped) < 5000


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
