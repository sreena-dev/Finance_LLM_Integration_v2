"""Routes for the Financial Statement mode — `/api/financial-statement/*`.

Authentication is attached to this router as a whole in `app/main.py`, not
declared per route. The `Depends(require_user)` below are how a handler gets
hold of *which* user is calling, which it needs because chat history is scoped
to them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections import OrderedDict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.auth.db import AuthDBError
from app.auth.deps import CurrentUser, require_user
from app.auth.schema import ensure_schema
from app.errors import ModeUnavailableError
from app.schemas import QueryRequest, QueryResponse
from . import adapter
from . import conversations as convo
from .upload import client as ingest_client
from .upload import edits as upload_edits
from .upload import materiality as upload_materiality
from .upload import quality as upload_quality
from .upload import store as upload_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/financial-statement", tags=["financial-statement"])


@router.get("/health")
async def health():
    available, reason = await asyncio.to_thread(adapter.status)
    return {"mode": adapter.MODE_ID, "available": available, "reason": reason}


def _history_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"Chat history is unavailable: {exc}",
    )


def _upload_store_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"The uploaded-document store is unavailable: {exc}",
    )


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, user: CurrentUser = Depends(require_user)):
    """Answer a financial-statement question, in the context of its conversation."""
    text = req.query.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # Log the query as `repr` plus a short digest, not as plain text. The
    # pipeline's tool-selection is sensitive to the exact characters it receives,
    # and the same question typed in the UI and sent by curl can take completely
    # different tool paths — one answering in 3 iterations, the other looping
    # until it hits MAX_TOOL_ITERATIONS. Without the escapes visible (stray
    # newlines, NBSP, en-dashes, smart quotes) and a digest to compare requests
    # by, there is no way to tell whether two runs were even given the same
    # input, and the difference is invisible in a rendered chat bubble.
    logger.info(
        "query received: user=%s sha1=%s len=%d %r",
        user.username,
        hashlib.sha1(text.encode()).hexdigest()[:12],
        len(text),
        text,
    )

    # History is read from the database, never taken from the request. The
    # client cannot rewrite its own past, and a conversation resumes correctly
    # after a refresh or on a different machine.
    history: list[dict] = []
    conversation_id = (req.conversation_id or "").strip()
    try:
        await asyncio.to_thread(ensure_schema)
        if conversation_id:
            history = await asyncio.to_thread(
                convo.history_for_rewriter, user.user_id, conversation_id
            )
            # An empty history is NOT proof the conversation is bogus. Uploading
            # a document mints a conversation id and writes no message — the
            # first question in it is genuinely the first turn. Before this
            # check consulted the upload store, that first question 404'd and
            # the agent was never reached at all, which presented as "the upload
            # feature cannot answer anything".
            #
            # The store is keyed by (user_id, conversation_id) exactly as the
            # message table is, so this widens the check without widening who
            # can see what: another user's id still resolves to nothing here.
            has_upload = await asyncio.to_thread(
                upload_store.STORE.list, user.user_id, conversation_id
            )
            if not history and not has_upload:
                # Either it does not exist or it is someone else's. Same answer
                # for both — confirming which would leak that the id is real.
                raise HTTPException(status_code=404,
                                    detail="No such conversation.")
        else:
            conversation_id = convo.new_conversation_id()
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc

    try:
        # The pipeline is blocking (psycopg2 + synchronous LLM tool-calling
        # rounds), so it must not run on the event loop.
        result = await asyncio.to_thread(
            adapter.run_query, text, history=history,
            user_id=user.user_id, conversation_id=conversation_id,
        )
    except ModeUnavailableError as exc:
        raise exc.as_http() from exc

    result["conversation_id"] = conversation_id

    # Persisted only after a successful run: a failed turn would otherwise leave
    # an orphan question in the thread, and the UI already reports the failure.
    try:
        await asyncio.to_thread(
            convo.append_turns,
            user.user_id, conversation_id, text,
            result.get("final_answer") or result.get("summary") or "",
            result, result.get("rewritten_query") or "",
        )
    except AuthDBError as exc:
        # The answer is good; only the record of it failed. Returning it is far
        # better than discarding a completed run over a storage problem.
        logger.error("Could not save the conversation turn: %s", exc)

    return QueryResponse(**result)


# ---------------------------------------------------------------------------
# Conversations
#
# Every one of these is scoped to the authenticated user inside the SQL, not
# checked afterwards — see modes/financial_statement/conversations.py.
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def list_conversations(user: CurrentUser = Depends(require_user)):
    """This user's Financial Statements conversations, most recent first."""
    try:
        await asyncio.to_thread(ensure_schema)
        rows = await asyncio.to_thread(convo.list_conversations, user.user_id)
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc
    return {"conversations": rows}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str,
                           user: CurrentUser = Depends(require_user)):
    """Every turn of one conversation, for rehydrating the thread.

    Assistant turns carry the stored `payload` — the full original response — so
    a reopened conversation renders with its evidence and citations intact rather
    than as bare text.
    """
    try:
        await asyncio.to_thread(ensure_schema)
        messages = await asyncio.to_thread(
            convo.get_messages, user.user_id, conversation_id
        )
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc

    if not messages:
        raise HTTPException(status_code=404, detail="No such conversation.")
    return {"conversation_id": conversation_id, "messages": messages}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str,
                              user: CurrentUser = Depends(require_user)):
    try:
        await asyncio.to_thread(ensure_schema)
        removed = await asyncio.to_thread(
            convo.delete_conversation, user.user_id, conversation_id
        )
    except AuthDBError as exc:
        raise _history_unavailable(exc) from exc

    # Uploaded documents live only as long as the conversation they belong to,
    # so deleting it is what frees them. The TTL in upload/store.py is only a
    # backstop for conversations nobody ever deletes.
    #
    # By this point the message rows above are already gone from Postgres —
    # failing this whole request over a transient Redis error would tell the
    # client the delete didn't happen when it did. Log and let the 204 through;
    # the TTL backstop cleans up the orphaned Redis entries later. Same
    # reasoning as the append_turns failure handling in POST /query above:
    # the outcome is good, only the bookkeeping for one part of it failed.
    try:
        await asyncio.to_thread(
            upload_store.STORE.drop_conversation, user.user_id, conversation_id
        )
    except upload_store.UploadStoreError as exc:
        logger.error(
            "Could not drop uploaded documents for %s: %s", conversation_id, exc
        )
    upload_materiality.REGISTRY.clear(user.user_id, conversation_id)

    if not removed:
        raise HTTPException(status_code=404, detail="No such conversation.")
    return None


# ---------------------------------------------------------------------------
# Live upload
#
# The gateway never converts a PDF itself — it forwards to the ingestion service
# and relays progress. What it does own is authorisation, the conversation an
# upload belongs to, and the in-memory store that keeps it alive; none of that
# belongs in a stateless converter.
# ---------------------------------------------------------------------------

#: Which user and conversation each in-flight job belongs to. Process-local,
#: like the ingestion service's own registry and Trial Balance's
#: _PREVIEW_STORE; this gateway runs one worker (ARTHA_BACKEND_WORKERS=1).
#:
#: Entries are removed when their event stream finishes. A client that uploads
#: and never opens the stream — a closed tab, a dropped connection — would
#: otherwise leave one behind for the life of the process, so the map is capped
#: and the oldest entry is dropped once it is full. Losing an old entry only
#: costs that upload its progress stream; the ingestion service still finishes
#: the job, and the document is retrievable by polling.
_JOB_OWNERS: "OrderedDict[str, tuple[str, str, str]]" = OrderedDict()
_MAX_TRACKED_JOBS = 200


def _track_job(job_id: str, owner: tuple[str, str, str]) -> None:
    _JOB_OWNERS[job_id] = owner
    while len(_JOB_OWNERS) > _MAX_TRACKED_JOBS:
        _JOB_OWNERS.popitem(last=False)


@router.get("/upload/health")
async def upload_health():
    """Both halves of "can a user upload and then ask about it right now":
    the ingestion service, and the store their converted document lands in.
    Reporting the store matters specifically because it runs with
    `noeviction` (see docker-compose.yml's redis service) — "Redis is full"
    is a real, worth-surfacing operational state, not a hypothetical one.
    """
    ingest_available, ingest_reason = await asyncio.to_thread(ingest_client.status)
    store_available, store_reason = await asyncio.to_thread(upload_store.STORE.ping)

    # The durable half, reported separately. An upload that converts and caches
    # but cannot be persisted is not a healthy upload -- it is one that will
    # disappear at the next cache expiry with nothing to say why, which is the
    # exact failure persistence was added to end. `persistence_status` returns
    # (True, None) when persistence is deliberately switched off, so a
    # Redis-only deployment still reports healthy.
    persist_available, persist_reason = await asyncio.to_thread(
        upload_store.persistence_status
    )
    return {
        "available": ingest_available and store_available and persist_available,
        "reason": ingest_reason or store_reason or persist_reason,
        # How long an upload is kept, so the UI states the real retention
        # ("kept until <date>") instead of hard-coding a number that drifts from
        # the server's ARTHA_FS_UPLOAD_RETENTION_DAYS.
        "retention_days": upload_store.RETENTION_DAYS,
    }


def _safe_filename(raw: str | None) -> str:
    """Basename only, and never a path.

    Trial Balance's upload route joins a client-supplied filename onto a
    directory unsanitised (modes/trial_balance/router.py:116). Nothing here
    writes to disk, so the risk is smaller — but the name is echoed back to the
    browser and rendered into every citation, so it is cleaned anyway.
    """
    name = (raw or "upload.pdf").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    return name[:180] or "upload.pdf"


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    conversation_id: str = Form(""),
    user: CurrentUser = Depends(require_user),
):
    """Accept a financial-statement PDF and start converting it.

    Returns a job id immediately: conversion takes minutes on a scan, so the
    caller watches `/upload/{job_id}/events` rather than holding this request
    open. The document is bound to `conversation_id`, and an upload with no
    conversation is refused rather than parked somewhere unreachable.
    """
    available, reason = await asyncio.to_thread(ingest_client.status)
    if not available:
        raise HTTPException(status_code=503, detail=reason)

    # An uploaded document still belongs to a conversation — that is what bounds
    # its lifetime — but the conversation does not have to exist first. Requiring
    # one inverted the natural order: a user wants to drop in a statement and
    # then ask about it, not compose a question about a document the system has
    # not seen. So a missing id is minted here, exactly as `POST /query` mints
    # one for a first question, and returned for the client to adopt.
    #
    # Nothing is written to `artha_fs_messages` yet, so a conversation holding
    # only an upload does not appear in the sidebar until a question is asked.
    # That is deliberate: an empty conversation is not worth listing.
    conversation_id = (conversation_id or "").strip() or convo.new_conversation_id()

    filename = _safe_filename(file.filename)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > upload_store.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{filename} is {len(data) // (1024 * 1024)}MB; the limit is "
                   f"{upload_store.MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=415,
            detail=f"{filename} is not a PDF. Upload the financial statements as a PDF.",
        )

    logger.info(
        "upload received: user=%s conversation=%s file=%r bytes=%d",
        user.username, conversation_id, filename, len(data),
    )

    try:
        job_id = await asyncio.to_thread(ingest_client.submit, filename, data)
    except ingest_client.IngestUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _track_job(job_id, (user.user_id, conversation_id, filename))
    return {"job_id": job_id, "filename": filename, "conversation_id": conversation_id}


@router.get("/upload/{job_id}/events")
async def upload_events(job_id: str, user: CurrentUser = Depends(require_user)):
    """Relay conversion progress, and store the result when it arrives.

    The gateway parses the stream rather than proxying it verbatim, because the
    final `result` frame is what gets stored against the conversation. Leaving
    that to the browser would mean a user who closed the tab lost the document
    that had just finished converting.
    """
    owner = _JOB_OWNERS.get(job_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="No such upload.")
    owner_id, conversation_id, filename = owner
    if owner_id != user.user_id:
        # 404 rather than 403: whether a job id exists is not this user's
        # business either way.
        raise HTTPException(status_code=404, detail="No such upload.")

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def pump():
            """Bridge the client's blocking generator onto the event loop.

            `requests` is synchronous, so the relay runs on a worker thread and
            hands frames back with call_soon_threadsafe — the same thread-to-loop
            pattern the FDR streaming route already uses.
            """
            try:
                for name, payload in ingest_client.events(job_id):
                    loop.call_soon_threadsafe(queue.put_nowait, (name, payload))
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", {"error": str(exc)}))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(pump))

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                name, payload = item

                if name == "result":
                    document = upload_store.UploadedDocument(
                        doc_id=payload["document"]["doc_id"],
                        user_id=user.user_id,
                        conversation_id=conversation_id,
                        filename=filename,
                        document=payload["document"],
                        identification=payload.get("identification") or {},
                        quality=payload.get("quality") or {},
                        tables=payload.get("tables") or [],
                        texts=payload.get("texts") or [],
                        pages=payload.get("pages") or [],
                    )

                    # Carry forward any prior user-entered figures (see
                    # edits.py) before this re-extraction overwrites the
                    # document — `doc_id` is content-addressed, so re-
                    # uploading the same file replaces rather than
                    # accumulates, and would otherwise silently wipe them.
                    # Gated on the flag: with it off, no document can carry
                    # `user_edits` in the first place, so skip the extra read.
                    carried_applied = carried_dropped = 0
                    if upload_store.USER_EDITS_ENABLED:
                        try:
                            existing = await asyncio.to_thread(
                                upload_store.STORE.get, user.user_id,
                                conversation_id, document.doc_id,
                            )
                        except upload_store.UploadStoreError as exc:
                            logger.warning(
                                "could not check %s for prior edits to carry "
                                "forward (%s); proceeding without them",
                                document.doc_id, exc,
                            )
                            existing = None
                        if existing is not None and upload_edits.active_edits(existing.quality):
                            try:
                                carried_applied, carried_dropped = upload_edits.carry_forward(
                                    existing.quality, document
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "carry_forward failed for %s (%s); proceeding "
                                    "with no edits carried forward",
                                    document.doc_id, exc,
                                )
                        if carried_dropped:
                            notes = list((document.quality or {}).get("notes") or [])
                            notes.append(
                                f"{carried_dropped} figure(s) a user previously entered by "
                                "hand could not be carried forward to this re-extraction "
                                "-- the cell no longer reads the same way it did before. "
                                "Re-enter them if they are still needed."
                            )
                            document.quality["notes"] = notes

                    try:
                        await asyncio.to_thread(upload_store.STORE.put, document)
                    except upload_store.UploadStoreError as exc:
                        # An SSE stream can't raise an HTTPException mid-flight —
                        # surface the failure as an `error` event instead, the
                        # same way a conversion error from the ingest client is
                        # already relayed a few lines above.
                        yield "event: error\ndata: " + json.dumps({"error": str(exc)}) + "\n\n"
                        continue
                    logger.info(
                        "upload stored: user=%s conversation=%s doc_id=%s tables=%d withheld=%d recovered=%d",
                        user.username, conversation_id, document.doc_id,
                        len(document.tables),
                        len((document.quality or {}).get("unreadable_cells") or []),
                        sum(
                            1 for c in ((document.quality or {}).get("recovered_cells") or [])
                            if c.get("promoted")
                        ),
                    )
                    # The browser gets the compact summary plus the quality
                    # report, not the whole extracted document: the tables run to
                    # megabytes and the UI renders none of them directly.
                    body = json.dumps({
                        **upload_quality.as_payload(document),
                        "conversation_id": conversation_id,
                        **(
                            {"carried_forward_edits": carried_applied, "dropped_edits": carried_dropped}
                            if (carried_applied or carried_dropped) else {}
                        ),
                    })
                    yield "event: result\ndata: " + body + "\n\n"
                    continue

                yield "event: " + name + "\ndata: " + json.dumps(payload) + "\n\n"
        finally:
            task.cancel()
            _JOB_OWNERS.pop(job_id, None)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which would hold every
            # event until the stream closed and defeat the point entirely.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/documents")
async def list_documents(conversation_id: str,
                         user: CurrentUser = Depends(require_user)):
    try:
        documents = await asyncio.to_thread(
            upload_store.STORE.list, user.user_id, conversation_id.strip()
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    return {
        "conversation_id": conversation_id,
        "documents": [upload_quality.as_payload(d) for d in documents],
    }


@router.get("/documents/{doc_id}/quality")
async def document_quality(doc_id: str, conversation_id: str,
                           user: CurrentUser = Depends(require_user)):
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")
    return {
        **upload_quality.as_payload(document),
        "report": upload_quality.quality_report(document),
    }


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, conversation_id: str,
                          user: CurrentUser = Depends(require_user)):
    try:
        removed = await asyncio.to_thread(
            upload_store.STORE.delete, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="No such uploaded document.")
    return None


@router.patch("/documents/{doc_id}/tables/{table_id}/cells")
async def edit_table_cell(doc_id: str, table_id: str, conversation_id: str,
                          req: dict, user: CurrentUser = Depends(require_user)):
    """Set, confirm or revert one cell the extraction flagged `[unreadable
    ...]` or `[recovered ...]`, from a figure the user read off the scan.

    Off by default (`ARTHA_FS_UPLOAD_USER_EDITS`) — see `edits.py`'s module
    docstring and `store.USER_EDITS_ENABLED` for why. All the policy —
    scope, validation, idempotency, the advisory footing re-check — lives in
    `edits.apply`; this route only resolves the document, calls into the
    narrow single-table write path, and translates `edits.EditError` into the
    HTTP status it already carries.
    """
    if not upload_store.USER_EDITS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")

    conversation_id = conversation_id.strip()
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id, doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")
    if not any(t.get("table_id") == table_id for t in document.tables):
        raise HTTPException(status_code=404, detail="No such table in that document.")

    def mutate(table_md, quality):
        return upload_edits.apply(
            table_md, quality, doc_id=doc_id, stored_table_id=table_id,
            request=req, user_id=user.user_id,
        )

    try:
        outcome = await asyncio.to_thread(
            upload_store.STORE.update_cell, user.user_id, conversation_id, doc_id,
            table_id, mutate,
        )
    except upload_edits.EditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.as_dict()) from exc
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="No such uploaded document or table.")

    try:
        refreshed = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id, doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    summary = refreshed.summary() if refreshed is not None else None

    return {**outcome, "summary": summary}


@router.post("/materiality")
async def set_materiality(req: dict, user: CurrentUser = Depends(require_user)):
    """Set or clear the audit team's materiality for one conversation."""
    conversation_id = str(req.get("conversation_id") or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required.")

    raw = req.get("amount")
    if raw in (None, ""):
        upload_materiality.REGISTRY.clear(user.user_id, conversation_id)
        return {"cleared": True, "legend": upload_materiality.legend(None)}

    amount = upload_materiality.parse_amount(raw)
    if amount is None:
        raise HTTPException(
            status_code=400,
            detail=f"Could not read {raw!r} as an amount. Try '2.5 crore', "
                   "'50 lakh' or '2500000'.",
        )

    threshold = upload_materiality.Materiality(
        amount=amount,
        basis=str(req.get("basis") or "") or "supplied by the audit team",
        unit_label=str(req.get("unit_label") or "") or None,
        user_supplied=True,
        reason=str(req.get("basis") or "") or None,
    )
    upload_materiality.REGISTRY.set(user.user_id, conversation_id, threshold)
    return {
        **threshold.as_dict(),
        "legend": upload_materiality.legend(threshold),
    }


@router.get("/documents/{doc_id}/tables/{table_id}/snippet.jpg")
async def table_snippet(doc_id: str, table_id: str, conversation_id: str,
                        user: CurrentUser = Depends(require_user)):
    """The scanned region a cited table was read from.

    This is what makes a citation checkable rather than merely traceable. The
    spec requires every figure to carry statement, note, page, table, row and
    column; showing the reader the actual crop lets them confirm the number in
    one glance instead of reopening the PDF and hunting for it.

    Served as bytes rather than embedded in the answer payload because a
    conversation with several documents would otherwise carry megabytes of
    base64 into every history fetch, and the browser only ever displays one
    snippet at a time.
    """
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")

    for table in document.tables:
        if table.get("table_id") != table_id:
            continue
        encoded = table.get("snippet_jpeg_b64")
        if not encoded:
            raise HTTPException(
                status_code=404,
                detail="No page image was kept for that table — it carried no "
                       "bounding box when it was read.",
            )
        try:
            blob = base64.b64decode(encoded)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail="The stored snippet is unreadable.") from exc
        return Response(
            content=blob,
            media_type="image/jpeg",
            headers={
                # Immutable for as long as it exists: the crop is derived from a
                # document that cannot change, and the id is content-addressed.
                "Cache-Control": "private, max-age=3600",
            },
        )

    raise HTTPException(status_code=404, detail="No such table in that document.")


@router.get("/documents/{doc_id}/pages")
async def document_pages(doc_id: str, conversation_id: str,
                         user: CurrentUser = Depends(require_user)):
    """Which pages this document has an image for, and their pixel size.

    A manifest, not the images themselves — the pane needs the list of page
    numbers that actually exist before it can build prev/next, and sending
    every page's bytes here would repeat the tables' megabytes-in-one-response
    mistake `table_snippet` above already avoids. Page numbers are not
    contiguous: blank and duplicate pages are silently skipped during
    ingestion, so a document may jump from page 4 straight to page 6.
    """
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")
    pages = sorted(document.pages, key=lambda p: p.get("page_no") or 0)
    return {
        "doc_id": doc_id,
        "pages": [
            {
                "page_no": p.get("page_no"),
                "width_px": p.get("width_px"),
                "height_px": p.get("height_px"),
            }
            for p in pages
        ],
    }


@router.get("/documents/{doc_id}/pages/{page_no}.jpg")
async def page_image(doc_id: str, page_no: int, conversation_id: str,
                     user: CurrentUser = Depends(require_user)):
    """The corrected page image OCR/docling actually read.

    Same shape as `table_snippet` above — pure decode-and-serve, because the
    original PDF is dropped the moment the ingestion job ends and there is no
    later point at which this bitmap could be re-derived.
    """
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")

    for page in document.pages:
        if page.get("page_no") != page_no:
            continue
        encoded = page.get("image_jpeg_b64")
        if not encoded:
            raise HTTPException(status_code=404, detail="No image was kept for that page.")
        try:
            blob = base64.b64decode(encoded)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail="The stored page image is unreadable.") from exc
        return Response(
            content=blob,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    raise HTTPException(
        status_code=404,
        detail="That page was blank, a repeat of another page, or out of range.",
    )


@router.get("/documents/{doc_id}/pages/{page_no}/text")
async def page_text(doc_id: str, page_no: int, conversation_id: str,
                    user: CurrentUser = Depends(require_user)):
    """The reconstructed narrative and verified tables for one page.

    Reuses `narrative()`/`financial_tables()` — the same page-order guarantees
    `DocumentResolver._fetch_following_chunks` already relies on — rather than
    re-deriving page grouping here. Narrative chunks and tables are returned as
    two separate lists, not one interleaved stream: nothing in the extracted
    record carries a shared vertical position for a text chunk and a table on
    the same page (`TextRecord.bbox` is never populated), so a merged single
    order would be a guess dressed up as a fact. Two honestly-ordered lists
    beat one invented one. An empty page — a cover sheet, a blank divider — is
    a normal answer, not a 404.
    """
    try:
        document = await asyncio.to_thread(
            upload_store.STORE.get, user.user_id, conversation_id.strip(), doc_id
        )
    except upload_store.UploadStoreError as exc:
        raise _upload_store_unavailable(exc) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="No such uploaded document.")
    narrative = [c for c in document.narrative() if c.get("page_ocr_start") == page_no]
    tables = [t for t in document.financial_tables() if t.get("page_ocr_start") == page_no]
    if upload_store.USER_EDITS_ENABLED:
        # Flagged cells addressed by (row_index, col_index), never derived by
        # the client from marker text — a marker string is not unique across
        # a table (two "Additions" rows), the exact case ingestion's own
        # redaction had to fix by switching to index-keyed lookups.
        for table in tables:
            table["cells"] = upload_edits.cells_for_table(
                document.quality, doc_id, table.get("table_id") or "", table.get("table_md") or "",
            )
    return {"doc_id": doc_id, "page_no": page_no, "narrative": narrative, "tables": tables}
