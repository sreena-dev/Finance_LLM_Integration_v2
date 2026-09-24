"""Request/response contracts for authentication."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from .security import MAX_PASSWORD_BYTES, MIN_PASSWORD_CHARS

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
# Deliberately permissive, and deliberately not `pydantic.EmailStr`: that
# pulls in the `email-validator` package for a check this app does not act
# on. Nothing is emailed anywhere — there is no verification and no reset —
# so the address is an identifier, and the only thing worth rejecting is
# something that is obviously not one.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignUpRequest(BaseModel):
    username: str
    email: str
    password: str
    display_name: str | None = None

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        value = (value or "").strip()
        if not _USERNAME_RE.match(value):
            raise ValueError(
                "Username must be 3-40 characters, using letters, digits, dot, "
                "underscore or hyphen."
            )
        return value

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = (value or "").strip()
        if not _EMAIL_RE.match(value) or len(value) > 254:
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        # Capped rather than pre-hashed. bcrypt rejects inputs over 72 bytes, and
        # silently pre-hashing to dodge that limit is a well-known way to end up
        # with a scheme nobody can reason about later. An explicit ceiling is
        # honest and costs a user nothing.
        if len(value or "") < MIN_PASSWORD_CHARS:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_CHARS} characters.")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
        return value

    @field_validator("display_name")
    @classmethod
    def _trim_display_name(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value[:120] or None


class SignInRequest(BaseModel):
    # Either the username or the email — people remember one or the other, and
    # refusing the one they typed is a pointless obstacle.
    login: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1)


class UserOut(BaseModel):
    user_id: str
    username: str
    email: str
    display_name: str | None = None
    # Only ever used by the client to decide whether to SHOW the Admin link. It
    # is not what protects the admin API -- the server checks the database on
    # every admin request (see deps.require_super_admin).
    is_super_admin: bool = False


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
