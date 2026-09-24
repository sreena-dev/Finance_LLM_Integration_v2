"""`/api/admin/*` -- the super-admin dashboard's API.

EVERY ROUTE IS BEHIND `require_super_admin`, AND CANNOT BE MADE OTHERWISE
--------------------------------------------------------------------------
The guard is attached where this router is included in `app/main.py`
(`dependencies=[Depends(require_super_admin)]`), not per route. A route added
here later inherits it; there is no route in this file that could forget it. A
signed-out caller gets 401, a signed-in non-admin gets 403 (JSON, so the client
shows a message instead of signing them out), and only then does any handler run.

READ-ONLY, AND AUDITED
----------------------
Nothing here changes anyone's data. Reading another user's conversations is
sensitive, so each such read leaves a row in `artha_admin_audit` (who, what,
about whom). Aggregate insights are not audited: they expose no individual's
content, and the dashboard polls them.

`is_super_admin` is not settable from here or from anywhere else over HTTP; see
`app/auth/admin.py`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import CurrentUser, require_super_admin

from . import insights, queries

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/whoami")
async def whoami(admin: CurrentUser = Depends(require_super_admin)):
    """Cheap check the client can use to confirm the server agrees the caller
    is an admin, independent of what the login payload claimed."""
    return {"ok": True, "username": admin.username}


@router.get("/users")
async def users(admin: CurrentUser = Depends(require_super_admin)):
    result = await asyncio.to_thread(queries.list_users)
    await asyncio.to_thread(queries.audit, admin.user_id, "list_users")
    return result


@router.get("/users/{user_id}")
async def user_detail(user_id: str, admin: CurrentUser = Depends(require_super_admin)):
    result = await asyncio.to_thread(queries.get_user, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No such user.")
    await asyncio.to_thread(queries.audit, admin.user_id, "view_user", user_id)
    return result


@router.get("/users/{user_id}/conversations")
async def user_conversations(
    user_id: str,
    mode: str = Query("fs", pattern="^(fs|tb)$"),
    admin: CurrentUser = Depends(require_super_admin),
):
    try:
        result = await asyncio.to_thread(queries.list_conversations, user_id, mode)
    except Exception as exc:  # noqa: BLE001 - e.g. the TB database is down
        raise HTTPException(
            status_code=503,
            detail=f"Could not read {mode.upper()} conversations: {exc}",
        ) from exc
    await asyncio.to_thread(
        queries.audit, admin.user_id, "list_conversations", user_id, f"mode={mode}")
    return result


@router.get("/conversations/{mode}/{conversation_id}")
async def conversation(
    mode: str, conversation_id: str,
    admin: CurrentUser = Depends(require_super_admin),
):
    if mode not in ("fs", "tb"):
        raise HTTPException(status_code=404, detail="Unknown mode.")
    try:
        result = await asyncio.to_thread(queries.get_conversation, mode, conversation_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"Could not read that conversation: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="No such conversation.")
    await asyncio.to_thread(
        queries.audit, admin.user_id, "view_conversation", result["user_id"],
        f"{mode}:{conversation_id}")
    return result


@router.get("/users/{user_id}/events")
async def user_events(
    user_id: str,
    status: str | None = Query(None, pattern="^(ok|error|unavailable)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: CurrentUser = Depends(require_super_admin),
):
    result = await asyncio.to_thread(
        queries.list_events, user_id, status, limit, offset)
    await asyncio.to_thread(queries.audit, admin.user_id, "view_events", user_id)
    return result


@router.get("/events")
async def all_events(
    status: str | None = Query(None, pattern="^(ok|error|unavailable)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: CurrentUser = Depends(require_super_admin),
):
    return await asyncio.to_thread(queries.list_events, None, status, limit, offset)


@router.get("/insights")
async def insights_view(
    days: int = Query(30, ge=1, le=365),
    admin: CurrentUser = Depends(require_super_admin),
):
    return await asyncio.to_thread(insights.build, days)


@router.get("/audit")
async def audit_log(
    limit: int = Query(100, ge=1, le=200),
    admin: CurrentUser = Depends(require_super_admin),
):
    return {"entries": await asyncio.to_thread(queries.list_audit, limit)}
