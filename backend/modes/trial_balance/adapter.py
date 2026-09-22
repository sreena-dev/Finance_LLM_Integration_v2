"""Mode-registry glue for Trial Balance (TB-v2). Thin by design: the actual
pipeline lives in pipeline/, HTTP routes live in router.py -- this module exists
only to satisfy app/registry.py's Mode.status: Callable[[], tuple[bool, str | None]]
contract, the same way every other mode's adapter.py does.
"""


def status() -> tuple[bool, str | None]:
    """(available, reason) -- never raises. Available means TB-v2's own Postgres
    (its storage layer: upload/list/delete/validate/grouping-upload) is reachable;
    `ask`/`audit` additionally need the LLM stack and report their own reason via
    their response body if that's what's missing, same as the router's other
    tool-call failures already do.

    Reuses pipeline/health_checks.py's check_postgres() rather than its own raw
    probe, so there's one Postgres-reachability check for this mode, not two
    independently-maintained ones (router.py's GET /health uses the same function
    for its richer multi-engine report)."""
    try:
        from modes.trial_balance.pipeline.health_checks import check_postgres

        result = check_postgres()
        if result.get("status") == "ok":
            return True, None
        return False, result.get("detail") or "Postgres unreachable"
    except Exception as exc:
        return False, str(exc)
