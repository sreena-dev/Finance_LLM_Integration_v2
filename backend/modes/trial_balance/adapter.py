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
    tool-call failures already do."""
    try:
        from modes.trial_balance.pipeline.db import db_cursor

        with db_cursor() as cur:
            cur.execute("SELECT 1")
        return True, None
    except Exception as exc:
        return False, str(exc)
