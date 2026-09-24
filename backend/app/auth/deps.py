"""The dependency that turns a bearer token into a user.

Attached once in `app/main.py` to the whole mode-router set, so no mode router
is edited to gain authentication. A route that wants the caller's identity
declares `user: CurrentUser = Depends(require_user)` and gets it; a route that
only needs to be protected declares nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import repo
from .security import AuthConfigError, InvalidToken, read_token

# `auto_error=False` so a missing header reaches our handler and gets the same
# WWW-Authenticate treatment as a bad one, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    username: str
    email: str
    display_name: str | None = None
    # Read from the database on every request, never from the token, so a
    # revoke (`python -m app.auth.admin revoke <user>`) takes effect on the very
    # next call rather than when the token expires.
    is_super_admin: bool = False

    @property
    def name(self) -> str:
        return self.display_name or self.username


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    """Resolve the caller, or reject the request.

    The 401 is what the browser client keys on to drop back to the sign-in
    screen, so every rejection here must be a 401 and not a 403 — a 403 would
    render in the UI as "this mode is unavailable", which is both wrong and
    exactly the class of misleading message this change set is removing.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorised("Sign in to use Artha.AI.")

    try:
        claims = read_token(credentials.credentials)
    except InvalidToken as exc:
        raise _unauthorised(str(exc)) from exc
    except AuthConfigError as exc:
        # A server misconfiguration, not a bad credential. 503 so it is not
        # mistaken for a rejected login and retried forever by a client.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    user = repo.get_by_id(claims.user_id)
    if user is None:
        # A valid signature for an account that no longer exists.
        raise _unauthorised("This account no longer exists.")

    current = CurrentUser(**user)
    # Stashed so a route can read it without re-declaring the dependency.
    request.state.user = current
    return current


async def require_super_admin(
    current: CurrentUser = Depends(require_user),
) -> CurrentUser:
    """Allow only a super admin.

    A signed-in user who is NOT an admin gets a 403 with a JSON `detail` --
    deliberately not a 401. The browser client signs the user out on any 401,
    which would punish an ordinary user for merely being refused; and the
    detail must be JSON, or the client reads a bare 403 as a port conflict.
    (`require_user` above explains the opposite rule for credential failures.)

    An unauthenticated caller never reaches this: `require_user` rejects them
    with a 401 first.
    """
    if not current.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This area is restricted to super administrators.",
        )
    return current
