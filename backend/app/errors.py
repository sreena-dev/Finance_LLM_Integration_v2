"""Shared error types for the integrated backend."""

from __future__ import annotations

from fastapi import HTTPException


class ModeUnavailableError(RuntimeError):
    """Raised when a mode's pipeline cannot be loaded or run.

    Every mode wraps its own pipeline import in this, so a missing optional
    dependency (yukta), an unset DSN, or an unreachable database degrades that
    one mode only — the gateway and the other three modes keep serving.
    """

    def __init__(self, mode_id: str, reason: str):
        self.mode_id = mode_id
        self.reason = reason
        super().__init__(f"[{mode_id}] {reason}")

    def as_http(self) -> HTTPException:
        return HTTPException(status_code=503, detail=self.reason)


class NotFoundError(ValueError):
    """A referenced resource does not exist — an unknown doc_id, an expired token.

    Kept distinct from ModeUnavailableError, which means "this mode is broken".
    Conflating them is a real problem for a caller: a 503 invites a retry and
    reads as an outage, while the honest answer to a stale doc_id is a 404 that
    says the reference itself is wrong. Subclasses ValueError so the vendored
    pipelines' own `raise ValueError("no stored trial balance for ...")` can be
    re-raised as this without changing their behaviour.
    """


class InvalidRequestError(ValueError):
    """A request that is well-formed but asks for something incoherent — e.g. the
    same trial balance given as both current and prior period. A client error
    (400), not a server or mode failure."""


class ModeNotIntegratedError(RuntimeError):
    """Raised by mode scaffolds whose branch has not been integrated yet."""

    def __init__(self, mode_id: str, branch: str):
        self.mode_id = mode_id
        self.branch = branch
        super().__init__(f"[{mode_id}] branch '{branch}' is not integrated yet")

    def as_http(self) -> HTTPException:
        # Mode ids are hyphenated for URLs; the package on disk is underscored.
        package = self.mode_id.replace("-", "_")
        return HTTPException(
            status_code=501,
            detail=(
                f"The '{self.mode_id}' mode is not integrated yet. Drop the "
                f"'{self.branch}' branch pipeline into "
                f"backend/modes/{package}/ and implement its adapter."
            ),
        )
