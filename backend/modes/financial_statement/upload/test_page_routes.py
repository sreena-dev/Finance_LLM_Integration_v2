"""Route-level tests for the document pane's page manifest, image and text.

Same fixture style as `test_query_route.py`: the FS router with auth stubbed
out, exercising only the routes' own lookup/serialisation logic against a
document planted directly in `upload_store.STORE`.

Run with::

    cd backend
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/Scripts/python -m pytest \
        modes/financial_statement/upload/test_page_routes.py -q
"""

from __future__ import annotations

import base64
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

USER = CurrentUser(user_id="u-pages", username="tester",
                   email="t@example.com", display_name="Tester")

_JPEG_BYTES = b"\xff\xd8not-a-real-jpeg-but-real-enough-for-this-test\xff\xd9"
_JPEG_B64 = base64.b64encode(_JPEG_BYTES).decode("ascii")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(fs_router.router)
    app.dependency_overrides[require_user] = lambda: USER
    yield TestClient(app)
    store.STORE.drop_conversation(USER.user_id, "cv-pages")


def upload_with_pages(conversation_id: str, *, pages=None, texts=None, tables=None) -> None:
    store.STORE.put(store.UploadedDocument(
        doc_id="up_pg", user_id=USER.user_id, conversation_id=conversation_id,
        filename="SFS.pdf", document={}, identification={}, quality={},
        pages=pages or [], texts=texts or [], tables=tables or [],
    ))


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}/pages -- the manifest
# ---------------------------------------------------------------------------

def test_manifest_is_sorted_and_does_not_assume_contiguous_pages(client):
    """Blank/duplicate pages are skipped during ingestion, so page numbers can
    jump -- the manifest must reflect exactly what was stored, in order, not
    assume every number from 1..N exists."""
    upload_with_pages("cv-pages", pages=[
        {"page_no": 4, "width_px": 100, "height_px": 140},
        {"page_no": 1, "width_px": 100, "height_px": 140},
        {"page_no": 3, "width_px": 100, "height_px": 140},
    ])
    response = client.get("/api/financial-statement/documents/up_pg/pages",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    page_nos = [p["page_no"] for p in response.json()["pages"]]
    assert page_nos == [1, 3, 4]


def test_manifest_for_unknown_document_is_404(client):
    response = client.get("/api/financial-statement/documents/does-not-exist/pages",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}/pages/{page_no}.jpg
# ---------------------------------------------------------------------------

def test_a_stored_page_image_is_served_as_jpeg(client):
    upload_with_pages("cv-pages", pages=[
        {"page_no": 5, "image_jpeg_b64": _JPEG_B64, "width_px": 100, "height_px": 140},
    ])
    response = client.get("/api/financial-statement/documents/up_pg/pages/5.jpg",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == _JPEG_BYTES


def test_a_page_number_not_present_is_404(client):
    upload_with_pages("cv-pages", pages=[
        {"page_no": 5, "image_jpeg_b64": _JPEG_B64, "width_px": 100, "height_px": 140},
    ])
    response = client.get("/api/financial-statement/documents/up_pg/pages/6.jpg",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 404


def test_a_pre_feature_document_with_no_pages_at_all_is_404_not_500(client):
    """Documents uploaded before this feature shipped carry no `pages` field --
    the route must fail cleanly, not assume the field is always populated."""
    upload_with_pages("cv-pages", pages=[])
    response = client.get("/api/financial-statement/documents/up_pg/pages/1.jpg",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}/pages/{page_no}/text
# ---------------------------------------------------------------------------

def test_text_route_filters_narrative_and_tables_to_the_requested_page(client):
    upload_with_pages(
        "cv-pages",
        texts=[
            {"chunk_id": "c1", "page_ocr_start": 5, "content": "on page 5"},
            {"chunk_id": "c2", "page_ocr_start": 6, "content": "on page 6"},
        ],
        tables=[
            {"table_id": "t1", "page_ocr_start": 5, "table_md": "| a |\n|---|\n| 1 |"},
            {"table_id": "t2", "page_ocr_start": 6, "table_md": "| b |\n|---|\n| 2 |"},
        ],
    )
    response = client.get("/api/financial-statement/documents/up_pg/pages/5/text",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    body = response.json()
    assert [c["chunk_id"] for c in body["narrative"]] == ["c1"]
    assert [t["table_id"] for t in body["tables"]] == ["t1"]


UNREADABLE = '[unreadable: page 5, table t1, row "Revenue", col "Amount"]'


def test_cells_array_is_omitted_when_the_edit_flag_is_off(client):
    """Default off (`store.USER_EDITS_ENABLED`): the UI must see no
    affordance at all, not an empty list that could be mistaken for 'nothing
    flagged'."""
    upload_with_pages(
        "cv-pages",
        tables=[{"table_id": "t1", "page_ocr_start": 5,
                 "table_md": f"| Revenue |\n| --- |\n| {UNREADABLE} |"}],
    )
    response = client.get("/api/financial-statement/documents/up_pg/pages/5/text",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    assert "cells" not in response.json()["tables"][0]


def test_cells_array_lists_flagged_cells_when_the_flag_is_on(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    upload_with_pages(
        "cv-pages",
        tables=[{"table_id": "t1", "page_ocr_start": 5,
                 "table_md": f"| Revenue |\n| --- |\n| {UNREADABLE} |"}],
    )
    doc = store.STORE.get(USER.user_id, "cv-pages", "up_pg")
    doc.quality = {"unreadable_cells": [
        {"table_id": "t1", "row_index": 0, "col_index": 0,
         "row_label": "Revenue", "column": "Amount", "marker": UNREADABLE},
    ]}
    store.STORE.put(doc)

    response = client.get("/api/financial-statement/documents/up_pg/pages/5/text",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    cells = response.json()["tables"][0]["cells"]
    assert cells == [{
        "row_index": 0, "col_index": 0, "state": "unreadable", "marker": UNREADABLE,
        "recovered_text": None, "confidence": None, "row_label": "Revenue", "column": "Amount",
    }]


def test_a_page_with_nothing_extracted_is_200_with_empty_lists(client):
    """An empty page -- a cover sheet, a blank divider -- is a normal answer,
    not a 404: the document exists, this page just had nothing on it."""
    upload_with_pages("cv-pages", texts=[], tables=[])
    response = client.get("/api/financial-statement/documents/up_pg/pages/1/text",
                          params={"conversation_id": "cv-pages"})
    assert response.status_code == 200
    assert response.json() == {
        "doc_id": "up_pg", "page_no": 1, "narrative": [], "tables": [],
    }


# ---------------------------------------------------------------------------
# Cross-user isolation -- every existing route in this module re-checks
# user_id, so a new one that forgot to would be a real regression.
# ---------------------------------------------------------------------------

def test_another_users_document_pages_are_not_visible(client):
    store.STORE.put(store.UploadedDocument(
        doc_id="up_other", user_id="someone-else", conversation_id="cv-theirs",
        filename="SFS.pdf", document={}, identification={}, quality={},
        pages=[{"page_no": 1, "image_jpeg_b64": _JPEG_B64, "width_px": 1, "height_px": 1}],
    ))
    try:
        response = client.get("/api/financial-statement/documents/up_other/pages",
                              params={"conversation_id": "cv-theirs"})
        assert response.status_code == 404
    finally:
        store.STORE.drop_conversation("someone-else", "cv-theirs")


# ---------------------------------------------------------------------------
# PATCH /documents/{doc_id}/tables/{table_id}/cells
# ---------------------------------------------------------------------------

def _upload_with_unreadable_cell(conversation_id="cv-pages"):
    store.STORE.put(store.UploadedDocument(
        doc_id="up_pg", user_id=USER.user_id, conversation_id=conversation_id,
        filename="SFS.pdf", document={}, identification={}, quality={
            "unreadable_cells": [
                {"table_id": "t1", "row_index": 0, "col_index": 0,
                 "row_label": "Revenue", "column": "Amount", "marker": UNREADABLE},
            ],
        },
        tables=[{"table_id": "t1", "page_ocr_start": 5,
                 "table_md": f"| Revenue |\n| --- |\n| {UNREADABLE} |"}],
    ))


def _patch(client, **body):
    return client.patch(
        "/api/financial-statement/documents/up_pg/tables/t1/cells",
        params={"conversation_id": "cv-pages"}, json=body,
    )


def test_patch_route_is_404_when_the_edit_flag_is_off(client):
    _upload_with_unreadable_cell()
    response = _patch(client, row_index=0, col_index=0, expected_cell=UNREADABLE,
                      action="set", value="12,859")
    assert response.status_code == 404


def test_patch_route_sets_an_unreadable_cell(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    _upload_with_unreadable_cell()

    response = _patch(client, row_index=0, col_index=0, expected_cell=UNREADABLE,
                      action="set", value="12,859")

    assert response.status_code == 200
    body = response.json()
    assert body["cell"] == "12,859 [user-entered]"
    assert body["summary"]["user_entered_cells"] == 1
    assert body["summary"]["unreadable_cells"] == 0

    reloaded = store.STORE.get(USER.user_id, "cv-pages", "up_pg")
    assert reloaded.tables[0]["table_md"].strip().endswith("12,859 [user-entered] |")
    assert reloaded.quality["unreadable_cells"] == []


def test_patch_route_refuses_a_clean_cell(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    store.STORE.put(store.UploadedDocument(
        doc_id="up_pg", user_id=USER.user_id, conversation_id="cv-pages",
        filename="SFS.pdf", document={}, identification={}, quality={},
        tables=[{"table_id": "t1", "page_ocr_start": 5,
                 "table_md": "| Revenue |\n| --- |\n| 999 |"}],
    ))

    response = _patch(client, row_index=0, col_index=0, expected_cell="999",
                      action="set", value="12,859")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_editable"


def test_patch_route_refuses_an_unparsable_value(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    _upload_with_unreadable_cell()

    response = _patch(client, row_index=0, col_index=0, expected_cell=UNREADABLE,
                      action="set", value="nan")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "bad_value"


def test_patch_route_404_for_an_unknown_document(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    response = client.patch(
        "/api/financial-statement/documents/up_missing/tables/t1/cells",
        params={"conversation_id": "cv-pages"},
        json={"row_index": 0, "col_index": 0, "expected_cell": "x", "action": "set", "value": "1"},
    )
    assert response.status_code == 404


def test_patch_route_does_not_touch_another_users_document(client, monkeypatch):
    monkeypatch.setattr(store, "USER_EDITS_ENABLED", True)
    store.STORE.put(store.UploadedDocument(
        doc_id="up_other", user_id="someone-else", conversation_id="cv-theirs",
        filename="SFS.pdf", document={}, identification={},
        quality={"unreadable_cells": [
            {"table_id": "t1", "row_index": 0, "col_index": 0,
             "row_label": "Revenue", "column": "Amount", "marker": UNREADABLE},
        ]},
        tables=[{"table_id": "t1", "page_ocr_start": 1,
                 "table_md": f"| Revenue |\n| --- |\n| {UNREADABLE} |"}],
    ))
    try:
        response = client.patch(
            "/api/financial-statement/documents/up_other/tables/t1/cells",
            params={"conversation_id": "cv-theirs"},
            json={"row_index": 0, "col_index": 0, "expected_cell": UNREADABLE,
                  "action": "set", "value": "1"},
        )
        assert response.status_code == 404
    finally:
        store.STORE.drop_conversation("someone-else", "cv-theirs")
