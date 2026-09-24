"""Recovering the structured trend data `tools_fs.py` computes and discards.

THE GAP THIS EXISTS TO FIX
---------------------------
`TrendAnalysisTools._compute_line_item_row` already computes, server-side,
everything a trend chart needs per line item per year — cells, year-over-year
absolute and percentage change, CAGR (see the dict shape returned there:
`{"label", "cells", "yoy", "cagr", "significant"}`). `get_multi_year_trend`
assembles a list of these per statement type and hands it straight to
`_format_statement_trend`, which turns it into a markdown pipe-table string
for the model to read. The dict is never seen again — only the string leaves
the tool, because `get_multi_year_trend` itself is typed `-> str`.

WHY THIS IS AN OVERRIDE RATHER THAN AN EDIT
--------------------------------------------
`pipeline/` (which `tools_fs.py` is part of) is vendored verbatim and the
backend README says not to edit it, so that it stays re-pullable — see
`upload/bridge.py`'s docstring, which exists for exactly this reason. This
module follows the same idiom `entity_resolution.py` and `bridge.py` already
established: verify the target function's signature, then rebind the
attribute from outside. A re-pull that renames or re-signatures
`_format_statement_trend` makes `install()` raise at load time rather than
silently going back to discarding the data.

WHY `_format_statement_trend` AND NOT `_compute_line_item_row`
-----------------------------------------------------------------
`_compute_line_item_row` is called once per line item, with no statement-type
context in its own arguments. `_format_statement_trend(statement_label, rows,
years_sorted, discrepancies)` is called once per statement type and already
receives the fully-assembled `rows` list plus `statement_label` and
`years_sorted` as plain arguments — one interception point, not two, and
nothing about `get_multi_year_trend` itself needs to change or even be
inspected.

WHY A CONTEXTVAR AND NOT A PERSISTENT REGISTRY
--------------------------------------------------
Unlike materiality (`upload/materiality.py`'s `_Registry`), which must survive
to the *next* question in a conversation, trend data is only ever needed for
the single request that produced it — a pure within-request side channel.
`upload/store.py` already carries a ContextVar (`_SCOPE`) for exactly this
lifetime, set immediately before the orchestrator runs and read/reset right
after in the same `finally` block that resets the upload scope token. This
module mirrors that shape rather than inventing a locked dict keyed by
`(user_id, conversation_id)`, which would be solving a durability problem this
data does not have.

SAFE WHEN NOT INSTALLED, OR WHEN NO CAPTURE IS ACTIVE
----------------------------------------------------------
The wrapped function is a no-op pass-through whenever `begin()` was never
called for the current context (the ContextVar reads as `None`) — a corpus
question that never touches `TrendAnalysisTools`, or any code path that calls
`get_multi_year_trend` outside `adapter.run_query`, costs nothing and behaves
exactly as before this module existed.
"""

from __future__ import annotations

import contextvars
import inspect
import logging

logger = logging.getLogger(__name__)

_EXPECTED = {
    "_format_statement_trend": ("statement_label", "rows", "years_sorted", "discrepancies"),
}

_TREND_CAPTURE: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "fs_trend_capture", default=None
)

_ORIGINAL = None


class TrendCaptureContractError(RuntimeError):
    """The vendored trend tool is not the shape this override was written for."""


def begin() -> object:
    """Start capturing for the current request. Returns a reset token."""
    return _TREND_CAPTURE.set([])


def collect(token: object) -> list[dict]:
    """Return whatever was captured since `begin()`, and end the capture.

    Always resets the ContextVar, even though the caller already holds the
    list — mirrors `upload_store.reset_scope`'s discipline of never leaving a
    stale value bound to a context that outlives the request that set it.
    """
    captured = _TREND_CAPTURE.get() or []
    _TREND_CAPTURE.reset(token)
    return captured


def _capturing_format_statement_trend(statement_label, rows, years_sorted, discrepancies):
    current = _TREND_CAPTURE.get()
    if current is not None and rows:
        # A copy, not the same list object `get_multi_year_trend` built —
        # nothing here may hold a reference the vendored function could still
        # mutate, and nothing here may risk the reverse either.
        current.append({
            "statement_label": statement_label,
            "years_sorted": list(years_sorted),
            "rows": list(rows),
        })
    return _ORIGINAL(statement_label, rows, years_sorted, discrepancies)


def install(trend_analysis_tools) -> None:
    """Replace `TrendAnalysisTools._format_statement_trend` with the capturing
    wrapper. Verifies the signature first — see module docstring for why a
    failure here must be loud rather than a silent degrade."""
    global _ORIGINAL

    for name, params in _EXPECTED.items():
        fn = getattr(trend_analysis_tools, name, None)
        if fn is None:
            raise TrendCaptureContractError(
                f"TrendAnalysisTools.{name} is missing. The vendored "
                f"Financial_Statement pipeline has changed shape; "
                f"modes/financial_statement/trend_capture.py must be updated "
                f"to match before it can be installed."
            )
        actual = tuple(inspect.signature(fn).parameters)
        if actual[:len(params)] != params:
            raise TrendCaptureContractError(
                f"TrendAnalysisTools.{name}{actual} no longer takes {params}. "
                f"Update modes/financial_statement/trend_capture.py."
            )

    _ORIGINAL = trend_analysis_tools._format_statement_trend
    trend_analysis_tools._format_statement_trend = staticmethod(_capturing_format_statement_trend)

    logger.info(
        "FS trend capture installed: TrendAnalysisTools._format_statement_trend "
        "now also records structured rows for chart rendering."
    )
