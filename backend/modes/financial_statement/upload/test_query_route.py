"""Route-level tests for asking a question about an uploaded document.

These exist because a bug lived exactly here and no unit test could see it. The
upload route mints a conversation id and writes no message — deliberately, so an
empty conversation does not clutter the sidebar. The query route then treated an
empty history as proof the conversation did not exist and returned 404, so the
first question after an upload never reached the agent at all. Every component
below the route worked; the request never got to them.

The lesson generalises: the store and the message table are two sources of truth
for "does this conversation exist", and a check that consults one is wrong.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_query_route.py -q
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

os.environ.setdefault("ARTHA_JWT_SECRET", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("REPORTS_DB_PASSWORD", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth.deps import CurrentUser, require_user  # noqa: E402
from modes.financial_statement import router as fs_router  # noqa: E402
from modes.financial_statement.upload import store  # noqa: E402

USER = CurrentUser(user_id="u-route", username="tester",
                   email="t@example.com", display_name="Tester")


@pytest.fixture
def client(monkeypatch):
    """The FS router with auth, the DB and the pipeline stubbed out.

    Only the route's own logic is under test — whether a question is admitted —
    so everything it delegates to is replaced.
    """
    monkeypatch.setattr(fs_router, "ensure_schema", lambda: None)
    monkeypatch.setattr(fs_router.convo, "history_for_rewriter",
                        lambda user_id, conversation_id: [])
    monkeypatch.setattr(fs_router.convo, "append_turns",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        fs_router.adapter, "run_query",
        lambda text, **kwargs: {
            "mode": "financial-statement", "query": text, "rewritten_query": "",
            "summary": "ok", "final_answer": "answered", "evidences_md": "",
            "chunks": [], "num_tables_searched": 0, "num_chunks_retrieved": 0,
            "elapsed_seconds": 0.1, "materiality_legend": None,
            "uploaded_documents": [],
        },
    )

    app = FastAPI()
    app.include_router(fs_router.router)
    app.dependency_overrides[require_user] = lambda: USER
    yield TestClient(app)
    store.STORE.drop_conversation(USER.user_id, "cv-uploaded")


def upload_into(conversation_id: str) -> None:
    store.STORE.put(store.UploadedDocument(
        doc_id="up_r", user_id=USER.user_id, conversation_id=conversation_id,
        filename="SFS.pdf", document={}, identification={}, quality={}))


def test_a_question_after_an_upload_is_not_a_404(client):
    """The regression.

    Upload creates the conversation; the first question is genuinely its first
    turn, so there is no history. It must still be answered.
    """
    upload_into("cv-uploaded")
    response = client.post("/api/financial-statement/query",
                           json={"query": "what are total assets?",
                                 "conversation_id": "cv-uploaded"})
    assert response.status_code == 200, response.text
    assert response.json()["final_answer"] == "answered"


def test_an_unknown_conversation_is_still_a_404(client):
    """Widening the check must not make every id valid."""
    response = client.post("/api/financial-statement/query",
                           json={"query": "hello",
                                 "conversation_id": "cv-does-not-exist"})
    assert response.status_code == 404


def test_another_users_upload_does_not_admit_the_conversation(client):
    """The store is keyed by (user_id, conversation_id) exactly as the message
    table is, so consulting it widens what counts as existing without widening
    who can see it."""
    store.STORE.put(store.UploadedDocument(
        doc_id="up_other", user_id="someone-else", conversation_id="cv-theirs",
        filename="SFS.pdf", document={}, identification={}, quality={}))
    try:
        response = client.post("/api/financial-statement/query",
                               json={"query": "hello",
                                     "conversation_id": "cv-theirs"})
        assert response.status_code == 404
    finally:
        store.STORE.drop_conversation("someone-else", "cv-theirs")


def test_a_first_question_with_no_conversation_still_works(client):
    """The pre-existing path: no id at all, the route mints one."""
    response = client.post("/api/financial-statement/query",
                           json={"query": "hello"})
    assert response.status_code == 200
