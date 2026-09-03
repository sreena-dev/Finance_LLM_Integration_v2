"""Routes for authentication — `/api/auth/*`.

These three are the only routes in the gateway that are reachable without a
token, alongside `GET /api/health` (which the container healthcheck polls and
which must therefore stay open).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from . import db as auth_db
from . import repo, schema
from .deps import CurrentUser, require_user
from .models import SignInRequest, SignUpRequest, TokenResponse, UserOut
from .security import AuthConfigError, make_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ensure_ready() -> None:
    """Create the tables on first use, converting failure into a clear 503.

    Deliberately here and not in a startup hook: a database blip at boot would
    otherwise crash the gateway and take the four working modes with it, and
    flap the container healthcheck. This way the gateway always starts and only
    sign-in reports the problem.
    """
    try:
        schema.ensure_schema()
    except auth_db.AuthDBError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The accounts database is not ready: {exc}",
        ) from exc


def _issue(user: dict) -> TokenResponse:
    try:
        token, expires_in = make_token(user["user_id"], user["username"])
    except AuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TokenResponse(token=token, expires_in=expires_in,
                         user=UserOut(**user))


@router.get("/health")
async def health():
    """Can accounts be read and written right now?

    Separate from `/api/health`, which must stay cheap and unauthenticated for
    the container probe and deliberately does not touch a database.
    """
    reachable, reason = await asyncio.to_thread(auth_db.ping)
    tables: list[str] = []
    if reachable:
        try:
            tables = await asyncio.to_thread(schema.existing_tables)
        except Exception as exc:  # noqa: BLE001
            reachable, reason = False, str(exc)
    return {"available": reachable, "reason": reason, "tables": tables}


@router.post("/signup", response_model=TokenResponse,
             status_code=status.HTTP_201_CREATED)
async def signup(req: SignUpRequest):
    """Register an account and sign it in immediately.

    Open by design — this is an internal tool and there is no invite flow. If
    that ever needs restricting, this is the single route to gate.
    """
    await asyncio.to_thread(_ensure_ready)
    try:
        user = await asyncio.to_thread(
            repo.create_user, req.username, req.email, req.password,
            req.display_name,
        )
    except repo.UsernameTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth_db.AuthDBError as exc:
        raise HTTPException(status_code=503,
                            detail=f"Could not create the account: {exc}") from exc

    logger.info("account created: %s", user["username"])
    return _issue(user)


@router.post("/login", response_model=TokenResponse)
async def login(req: SignInRequest):
    await asyncio.to_thread(_ensure_ready)
    try:
        user = await asyncio.to_thread(repo.authenticate, req.login, req.password)
    except auth_db.AuthDBError as exc:
        raise HTTPException(status_code=503,
                            detail=f"Could not verify the account: {exc}") from exc

    if user is None:
        # One message for both "no such account" and "wrong password": saying
        # which would let anyone enumerate who has an account here.
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(require_user)):
    """Who the current token belongs to.

    The client calls this on load to decide whether a stored token is still
    good, which is what stops a signed-out session showing the app shell for a
    moment before falling back to the sign-in screen.
    """
    return UserOut(user_id=user.user_id, username=user.username,
                   email=user.email, display_name=user.display_name)
