"""The trace endpoints. Mounted by `install()` behind the gateway's normal sign-in."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import CurrentUser, require_user
from live_trace import recorder

router = APIRouter(prefix="/api/trial-balance/trace", tags=["trial-balance-trace"])


def _own_run(run_id: str, user: CurrentUser) -> dict:
    meta = recorder.get_run(run_id)
    # Someone else's run answers exactly like a missing one: do not confirm that it exists.
    if meta is None or (meta.get("user_id") != user.user_id and not user.is_super_admin):
        raise HTTPException(status_code=404, detail="Unknown or expired run.")
    return meta


@router.get("/status")
def status():
    """Whether tracing can record right now (Valkey reachable and tracing enabled)."""
    return {"available": recorder.available(), "capture_payloads": recorder._capture_payloads()}


@router.get("/runs")
def runs(limit: int = 50, user: CurrentUser = Depends(require_user)):
    """Recent runs, newest first: the caller's own, or everyone's for a super admin."""
    return {"runs": recorder.list_runs(user.user_id, limit=max(1, min(limit, 100)), all_users=user.is_super_admin)}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, after: int = 0, user: CurrentUser = Depends(require_user)):
    """Run metadata plus the events after sequence number `after` (poll with the last seq seen)."""
    meta = _own_run(run_id, user)
    events = recorder.get_events(run_id, after=after)
    return {"run": meta, "events": events, "last_seq": events[-1]["seq"] if events else after}
