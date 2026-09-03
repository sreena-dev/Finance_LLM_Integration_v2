"""Password hashing and token issuing.

CHOICES, AND WHY
----------------
`bcrypt` directly, not `passlib`. passlib 1.7.4 is unmaintained and raises
`AttributeError: module 'bcrypt' has no attribute '__about__'` against bcrypt
>= 4.1, which is what pip resolves today. Calling bcrypt's two functions is less
code than the shim that works around that.

`PyJWT`, not `python-jose`. One algorithm, two functions; jose brings a much
larger surface for no capability used here.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No refresh tokens, no roles, no password reset, no email verification — the
scope is an internal tool on a LAN. When a token expires the UI returns to the
sign-in screen, which is the whole session story.

THE SECRET IS REQUIRED AND IS NEVER INVENTED
--------------------------------------------
An unset `ARTHA_JWT_SECRET` raises. It would be easy to generate a random one at
boot, and that would be worse than failing: every restart would silently
invalidate every session, and with more than one replica no token issued by one
would validate at another. A missing secret is a configuration error and says so.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

# bcrypt refuses inputs over 72 bytes rather than truncating silently in recent
# versions. The sign-up model caps the password well below this; the constant is
# here so both places cite the same reason.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_CHARS = 8

_ISSUER = "artha.ai"
_ALGORITHM = "HS256"


class AuthConfigError(RuntimeError):
    """Authentication cannot start — a required setting is missing."""


class InvalidToken(Exception):
    """The presented token is absent, malformed, expired or not ours."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    username: str
    expires_at: int


def secret() -> str:
    value = (os.environ.get("ARTHA_JWT_SECRET") or "").strip()
    if not value:
        raise AuthConfigError(
            "ARTHA_JWT_SECRET is not set, so authentication cannot start. Add a "
            "long random value to .env — e.g. `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"`. It must be the same across "
            "every replica, and changing it signs everyone out."
        )
    return value


def ttl_seconds() -> int:
    try:
        hours = float(os.environ.get("ARTHA_JWT_TTL_HOURS", "12") or 12)
    except ValueError:
        hours = 12.0
    return int(max(0.25, min(24 * 30, hours)) * 3600)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """A bcrypt hash, cost 12. The salt is generated per password and stored
    inside the returned string, which is why no separate salt column exists."""
    import bcrypt

    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password is longer than bcrypt's {MAX_PASSWORD_BYTES}-byte limit."
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison via bcrypt. Never raises on a malformed hash —
    a corrupt row must read as a failed login, not a 500."""
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode("utf-8"),
                              (stored_hash or "").encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def make_token(user_id: str, username: str) -> tuple[str, int]:
    """(token, seconds_until_expiry)."""
    import jwt

    now = int(time.time())
    lifetime = ttl_seconds()
    payload = {
        "sub": str(user_id),
        "username": username,
        "iss": _ISSUER,
        "typ": "access",
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, secret(), algorithm=_ALGORITHM), lifetime


def read_token(token: str) -> TokenClaims:
    """Verify a token and return its claims, or raise InvalidToken.

    Every rejection is reported to the caller as the same thing — an invalid
    token — because the difference between 'expired', 'wrong signature' and
    'not a token' is useful to an attacker and not to a browser, which does the
    same thing in all three cases.
    """
    import jwt

    if not token:
        raise InvalidToken("No token supplied.")
    try:
        claims = jwt.decode(token, secret(), algorithms=[_ALGORITHM],
                            issuer=_ISSUER,
                            options={"require": ["exp", "sub", "iss"]})
    except jwt.ExpiredSignatureError as exc:
        # The one distinction worth keeping: the UI shows "your session ended"
        # rather than "sign-in failed", which are different user experiences.
        raise InvalidToken("Session expired.") from exc
    except Exception as exc:  # noqa: BLE001 - every other failure is one thing
        raise InvalidToken("Invalid authentication token.") from exc

    if claims.get("typ") != "access":
        raise InvalidToken("Invalid authentication token.")

    return TokenClaims(user_id=str(claims["sub"]),
                       username=str(claims.get("username") or ""),
                       expires_at=int(claims["exp"]))
