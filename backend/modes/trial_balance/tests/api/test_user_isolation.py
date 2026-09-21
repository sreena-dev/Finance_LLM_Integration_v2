"""Cross-user isolation, end-to-end at the HTTP layer: user A's TB-owned data
(LIVE documents, audit sessions/reports) must be invisible to user B, and vice
versa -- 404, never 403 (see router.py's _verify_document_access docstring).

Unlike tests/api/test_routes_endpoints.py (one fixed fake user, testing routing/
response shape), these tests swap app.dependency_overrides[require_user] between
requests on the SAME TestClient to simulate two different logged-in users hitting
the same router instance, the way two real browser sessions would."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip(
    "yukta",
    reason="yukta is installed from a local path and published to no index, so it is "
           "absent on a clean checkout -- see requirements.txt. These tests import "
           "modes.trial_balance.pipeline.agent, which needs it.",
)

from app.auth.deps import CurrentUser, require_user
from modes.trial_balance.router import router

USER_A = CurrentUser(user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", username="alice", email="alice@example.com")
USER_B = CurrentUser(user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", username="bob", email="bob@example.com")


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _as(app, user):
    app.dependency_overrides[require_user] = lambda: user


class TestDocumentDeleteIsolation:
    def test_owner_can_delete_their_own_live_document(self, app, client):
        _as(app, USER_A)
        owned = {"tb_doc_id": "D-A", "user_id": USER_A.user_id}
        with patch("modes.trial_balance.router.fetch_live_document", return_value=owned), \
             patch("modes.trial_balance.router.call_tool", return_value={"execution_status": "SUCCESS"}) as m:
            resp = client.delete("/api/trial-balance/documents/D-A")
        assert resp.status_code == 200
        m.assert_called_once()

    def test_non_owner_gets_404_and_nothing_is_deleted(self, app, client):
        """User B never learns whether D-A exists at all -- 404, not 403."""
        _as(app, USER_B)
        owned_by_a = {"tb_doc_id": "D-A", "user_id": USER_A.user_id}
        with patch("modes.trial_balance.router.fetch_live_document", return_value=owned_by_a), \
             patch("modes.trial_balance.router.call_tool") as m:
            resp = client.delete("/api/trial-balance/documents/D-A")
        assert resp.status_code == 404
        m.assert_not_called()

    def test_orphaned_pre_isolation_document_is_invisible_to_everyone(self, app, client):
        """A LIVE row from before this migration has user_id=None -- nobody owns
        it, so it 404s for every caller rather than being claimable by whoever
        asks first."""
        _as(app, USER_A)
        orphaned = {"tb_doc_id": "D-OLD", "user_id": None}
        with patch("modes.trial_balance.router.fetch_live_document", return_value=orphaned), \
             patch("modes.trial_balance.router.call_tool") as m:
            resp = client.delete("/api/trial-balance/documents/D-OLD")
        assert resp.status_code == 404
        m.assert_not_called()


class TestAuditAccessIsolation:
    def test_audit_on_another_users_live_document_is_rejected(self, app, client):
        """/audit must 404 before even creating a session if doc_id resolves to a
        LIVE document owned by someone else -- the run never starts."""
        _as(app, USER_B)
        owned_by_a = {"tb_doc_id": "D-A", "user_id": USER_A.user_id}
        with patch("modes.trial_balance.router.fetch_live_document", return_value=owned_by_a), \
             patch("modes.trial_balance.router.create_session") as m_create:
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D-A"})
        assert resp.status_code == 404
        m_create.assert_not_called()

    def test_audit_on_a_shared_main_document_proceeds_for_any_user(self, app, client):
        """No LIVE row for this doc_id at all -- it's MAIN-only (or doesn't exist),
        which stays shared; _verify_document_access must not block it."""
        _as(app, USER_B)
        with patch("modes.trial_balance.router.fetch_live_document", return_value=None), \
             patch("modes.trial_balance.router.create_session", return_value="sess-shared") as m_create, \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router._run_layer1_precheck", return_value=None), \
             patch("modes.trial_balance.router._finalize_audit_result", return_value={"report": "ok"}), \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent"):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "MAIN-DOC"})
        assert resp.status_code == 200
        assert m_create.call_args.kwargs["user_id"] == USER_B.user_id


class TestAuditWorkbookIsolation:
    def test_find_latest_session_is_called_with_the_caller_s_user_id(self, app, client, tmp_path):
        """The actual isolation mechanism for report downloads: find_latest_session
        is always called scoped to the CALLER, so it can only ever return a session
        that caller created -- see db.py's find_latest_session docstring for why an
        unfiltered lookup would leak another user's report for a shared MAIN doc."""
        _as(app, USER_B)
        with patch("modes.trial_balance.router.find_latest_session", return_value=None) as m_find:
            resp = client.post("/api/trial-balance/audit/workbook", json={"doc_id": "D1"})
        assert resp.status_code == 404
        assert m_find.call_args.kwargs["user_id"] == USER_B.user_id
