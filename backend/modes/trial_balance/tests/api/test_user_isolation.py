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

    def test_main_documents_shared_visibility_never_authorizes_deleting_its_live_counterpart(self, app, client):
        """Regression guard for the fix in _verify_document_access: delete_document
        must NOT use that helper's MAIN-first shortcut. A document visible in the
        shared MAIN corpus must never let a caller delete an orphaned (or another
        user's) LIVE row just because the two happen to share a tb_doc_id."""
        _as(app, USER_B)
        orphaned_live_row = {"tb_doc_id": "EPIL_2025_2026", "user_id": None}
        with patch("modes.trial_balance.router.fetch_main_document", return_value={"tb_doc_id": "EPIL_2025_2026"}), \
             patch("modes.trial_balance.router.fetch_live_document", return_value=orphaned_live_row), \
             patch("modes.trial_balance.router.call_tool") as m:
            resp = client.delete("/api/trial-balance/documents/EPIL_2025_2026")
        assert resp.status_code == 404
        m.assert_not_called()


class TestAuditAccessIsolation:
    def test_audit_on_another_users_live_only_document_is_rejected(self, app, client):
        """/audit must 404 before even creating a session if doc_id resolves ONLY
        to a LIVE document owned by someone else -- the run never starts."""
        _as(app, USER_B)
        owned_by_a = {"tb_doc_id": "D-A", "user_id": USER_A.user_id}
        with patch("modes.trial_balance.router.fetch_main_document", return_value=None), \
             patch("modes.trial_balance.router.fetch_live_document", return_value=owned_by_a), \
             patch("modes.trial_balance.router.create_session") as m_create:
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D-A"})
        assert resp.status_code == 404
        m_create.assert_not_called()

    def test_audit_on_a_shared_main_document_proceeds_for_any_user(self, app, client):
        """No LIVE row for this doc_id at all -- it's MAIN-only (or doesn't exist),
        which stays shared; _verify_document_access must not block it."""
        _as(app, USER_B)
        with patch("modes.trial_balance.router.fetch_main_document", return_value={"tb_doc_id": "MAIN-DOC"}), \
             patch("modes.trial_balance.router.fetch_live_document") as m_live, \
             patch("modes.trial_balance.router.create_session", return_value="sess-shared") as m_create, \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router._run_layer1_precheck", return_value=None), \
             patch("modes.trial_balance.router._finalize_audit_result", return_value={"report": "ok"}), \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent"):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "MAIN-DOC"})
        assert resp.status_code == 200
        assert m_create.call_args.kwargs["user_id"] == USER_B.user_id
        m_live.assert_not_called()  # MAIN short-circuits before ever checking LIVE

    def test_regression_a_main_document_with_an_orphaned_live_row_is_still_usable(self, app, client):
        """The real bug this pins: every document promoted to MAIN before this
        isolation feature existed still has its ORIGINAL LIVE staging row, now
        an orphan with user_id=None (confirmed live: 7 of 40 real MAIN documents
        in this environment). Checking LIVE first would 404 every one of them as
        "owned by nobody", even though they're visible to every user via
        GET /documents. MAIN must be checked first and win when present,
        regardless of what the old LIVE row looks like."""
        _as(app, USER_B)
        orphaned_live_row = {"tb_doc_id": "EPIL_2025_2026", "user_id": None}
        with patch("modes.trial_balance.router.fetch_main_document", return_value={"tb_doc_id": "EPIL_2025_2026"}), \
             patch("modes.trial_balance.router.fetch_live_document", return_value=orphaned_live_row), \
             patch("modes.trial_balance.router.create_session", return_value="sess-shared") as m_create, \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router._run_layer1_precheck", return_value=None), \
             patch("modes.trial_balance.router._finalize_audit_result", return_value={"report": "ok"}), \
             patch("modes.trial_balance.router.llm_reachable", return_value=False), \
             patch("modes.trial_balance.router.get_agent"):
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "EPIL_2025_2026"})
        assert resp.status_code == 200
        m_create.assert_called_once()


class TestAuditConversationIsolation:
    """/audit persists its own conversation turn the same way /ask does (see
    router.py's audit() handler) -- same _resolve_conversation 404-before-any-
    side-effect contract, same fresh-conversation-per-run default (the
    frontend never sends conversation_id, so this path is exercised
    indirectly by every real audit run, not just an explicit continuation)."""

    def _patch_common(self):
        return (
            patch("modes.trial_balance.router.fetch_main_document", return_value={"tb_doc_id": "D1"}),
            patch("modes.trial_balance.router.update_session_status"),
            patch("modes.trial_balance.router._run_layer1_precheck", return_value=None),
            patch("modes.trial_balance.router._finalize_audit_result", return_value={"report": "ok"}),
            patch("modes.trial_balance.router.llm_reachable", return_value=False),
            patch("modes.trial_balance.router.get_agent"),
        )

    def test_audit_with_another_users_conversation_id_404s_before_creating_a_session(self, app, client):
        """Mirrors TestAskConversationIsolation's own test: _resolve_conversation()
        raises 404 before create_session is ever touched -- a caller can't probe
        for another user's conversation_id by supplying it on /audit either."""
        _as(app, USER_B)
        p_main, p_update, p_precheck, p_finalize, p_llm, p_agent = self._patch_common()
        with p_main, p_update, p_precheck, p_finalize, p_llm, p_agent, \
             patch("modes.trial_balance.router.history_for_agent", return_value=[]) as m_history, \
             patch("modes.trial_balance.router.create_session") as m_create:
            resp = client.post(
                "/api/trial-balance/audit",
                json={"doc_id": "D1", "conversation_id": "c-owned-by-a"},
            )
        assert resp.status_code == 404
        m_history.assert_called_once_with(USER_B.user_id, "c-owned-by-a")
        m_create.assert_not_called()

    def test_audit_mints_a_fresh_conversation_and_persists_the_turn(self, app, client):
        _as(app, USER_A)
        p_main, p_update, p_precheck, p_finalize, p_llm, p_agent = self._patch_common()
        with p_main, p_update, p_precheck, p_finalize, p_llm, p_agent, \
             patch("modes.trial_balance.router.new_conversation_id", return_value="c-fresh"), \
             patch("modes.trial_balance.router.create_session", return_value="sess-1"), \
             patch("modes.trial_balance.router.append_turns") as m_append:
            resp = client.post(
                "/api/trial-balance/audit",
                json={"doc_id": "D1", "doc_label": "OVL_2025.xlsx"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == "c-fresh"

        m_append.assert_called_once()
        args, kwargs = m_append.call_args
        assert args[0] == USER_A.user_id
        assert args[1] == "c-fresh"
        assert args[2] == "D1"
        assert args[3] == "Single TB Analysis — OVL_2025.xlsx"
        assert kwargs["payload"]["doc"] == {"doc_id": "D1", "filename": "OVL_2025.xlsx"}
        assert kwargs["payload"]["priorDoc"] is None
        assert kwargs["payload"]["result"]["mode"] == "SINGLE_TB"
        assert kwargs["payload"]["result"]["result"] == {"report": "ok"}

    def test_comparison_run_records_the_prior_doc_id_in_the_title_and_payload(self, app, client):
        _as(app, USER_A)
        p_main, p_update, p_precheck, p_finalize, p_llm, p_agent = self._patch_common()
        with p_main, p_update, p_precheck, p_finalize, p_llm, p_agent, \
             patch("modes.trial_balance.router.new_conversation_id", return_value="c-fresh"), \
             patch("modes.trial_balance.router.create_session", return_value="sess-1"), \
             patch("modes.trial_balance.router.append_turns") as m_append:
            resp = client.post(
                "/api/trial-balance/audit",
                json={"doc_id": "CY", "doc_id_prior": "PY", "doc_label": "CY_2025.xlsx"},
            )
        assert resp.status_code == 200
        args, kwargs = m_append.call_args
        assert args[3] == "Two TB Comparative Analysis — CY_2025.xlsx vs PY"
        assert kwargs["payload"]["priorDoc"] == {"doc_id": "PY"}

    def test_a_failed_audit_persists_no_conversation_turn_at_all(self, app, client):
        """_persist_turn only ever runs inside the try block's success path --
        an audit that raises must leave no orphan conversation, same contract
        append_turns' own docstring already promises for /ask."""
        _as(app, USER_A)
        with patch("modes.trial_balance.router.fetch_main_document", return_value={"tb_doc_id": "D1"}), \
             patch("modes.trial_balance.router.create_session", return_value="sess-fail"), \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router._run_layer1_precheck", side_effect=RuntimeError("boom")), \
             patch("modes.trial_balance.router.append_turns") as m_append:
            resp = client.post("/api/trial-balance/audit", json={"doc_id": "D1"})
        assert resp.status_code == 500
        m_append.assert_not_called()


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


class TestConversationRoutesIsolation:
    """The 3 durable chat-history routes -- same 404-not-403 contract as
    document/audit isolation: user_id is scoped inside the SQL (db.py), never
    checked afterward here, a miss is always 404."""

    def test_list_conversations_is_scoped_to_the_caller(self, app, client):
        _as(app, USER_B)
        with patch("modes.trial_balance.router.list_conversations", return_value=[{"conversation_id": "c1"}]) as m:
            resp = client.get("/api/trial-balance/conversations")
        assert resp.status_code == 200
        assert resp.json() == {"conversations": [{"conversation_id": "c1"}]}
        m.assert_called_once_with(USER_B.user_id)

    def test_get_conversation_404s_for_another_users_conversation(self, app, client):
        """get_messages() returning [] is the actual isolation mechanism --
        empty for both "doesn't exist" and "belongs to someone else"."""
        _as(app, USER_B)
        with patch("modes.trial_balance.router.get_messages", return_value=[]) as m:
            resp = client.get("/api/trial-balance/conversations/c-owned-by-a")
        assert resp.status_code == 404
        m.assert_called_once_with(USER_B.user_id, "c-owned-by-a")

    def test_get_conversation_returns_messages_for_the_owner(self, app, client):
        _as(app, USER_A)
        messages = [{"seq": 1, "role": "user", "content": "Q"}, {"seq": 2, "role": "assistant", "content": "A"}]
        with patch("modes.trial_balance.router.get_messages", return_value=messages):
            resp = client.get("/api/trial-balance/conversations/c-owned-by-a")
        assert resp.status_code == 200
        assert resp.json() == {"conversation_id": "c-owned-by-a", "messages": messages}

    def test_delete_conversation_404s_for_another_users_conversation(self, app, client):
        _as(app, USER_B)
        with patch("modes.trial_balance.router.delete_conversation", return_value=0) as m:
            resp = client.delete("/api/trial-balance/conversations/c-owned-by-a")
        assert resp.status_code == 404
        m.assert_called_once_with(USER_B.user_id, "c-owned-by-a")

    def test_delete_conversation_succeeds_for_the_owner(self, app, client):
        _as(app, USER_A)
        with patch("modes.trial_balance.router.delete_conversation", return_value=2):
            resp = client.delete("/api/trial-balance/conversations/c-owned-by-a")
        assert resp.status_code == 204


class TestAskConversationIsolation:
    def test_ask_with_another_users_conversation_id_404s_before_calling_the_agent(self, app, client):
        """_resolve_conversation() raises 404 before create_session/the agent
        are ever touched -- a caller can't probe for another user's
        conversation_id by supplying it on /ask."""
        _as(app, USER_B)
        with patch("modes.trial_balance.router.history_for_agent", return_value=[]) as m_history, \
             patch("modes.trial_balance.router.create_session") as m_create, \
             patch("modes.trial_balance.router.get_agent") as m_agent:
            resp = client.post(
                "/api/trial-balance/ask",
                json={"doc_id": "D1", "question": "Anything?", "conversation_id": "c-owned-by-a"},
            )
        assert resp.status_code == 404
        m_history.assert_called_once_with(USER_B.user_id, "c-owned-by-a")
        m_create.assert_not_called()
        m_agent.assert_not_called()

    def test_ask_without_a_conversation_id_mints_a_fresh_one_and_persists_the_turn(self, app, client):
        _as(app, USER_A)
        with patch("modes.trial_balance.router.new_conversation_id", return_value="c-fresh"), \
             patch("modes.trial_balance.router.create_session", return_value="sess-1"), \
             patch("modes.trial_balance.router.update_session_status"), \
             patch("modes.trial_balance.router.llm_reachable", return_value=True), \
             patch("modes.trial_balance.router.get_agent") as m_agent, \
             patch("modes.trial_balance.router.append_turns") as m_append:
            m_agent.return_value.invoke.return_value = "The answer is 42."
            resp = client.post("/api/trial-balance/ask", json={"doc_id": "D1", "question": "What is the answer?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == "c-fresh"
        # payload=None -- _persist_turn now always forwards it explicitly (see
        # TestAuditConversationIsolation, which is the one caller that passes a
        # real payload); /ask itself never builds one.
        m_append.assert_called_once_with(
            USER_A.user_id, "c-fresh", "D1", "What is the answer?", "The answer is 42.", payload=None
        )
