"""
Regression over the Phoenix/OTel integration.

    python -m fdr.test_tracing

The property being defended is not "spans arrive" — it is that **observability never
changes the thing it observes**. In an audit tool that is not a nicety: if a report differs
depending on whether tracing was on, then every traced run is evidence of something that
did not happen, and every untraced run is unreproducible.

So the tests here are mostly negative:
  - with tracing off, no OpenTelemetry package is imported at all;
  - with tracing off, span() is a no-op that still returns a usable handle;
  - a broken exporter, a dead Phoenix, or an unserialisable attribute never raises;
  - the report payload is byte-identical with tracing on and off.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

from . import tracing as T

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- the stdlib-only guarantee ----------------------------------------------------

def test_no_otel_import_when_disabled() -> None:
    """`fdr` must stay importable and runnable with nothing installed but the stdlib."""
    code = (
        "import sys;"
        "from fdr.assemble import build_report;"
        "build_report('X', ('FY2024-25',), assume_fired=frozenset({'S02'}));"
        "print(len([m for m in sys.modules if m.startswith('opentelemetry')]))"
    )
    env = {**os.environ, "FDR_TRACE_ENABLED": "0"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    check(out.returncode == 0, f"a traced-off run failed: {out.stderr[-300:]}")
    check(out.stdout.strip().endswith("0"),
          f"OpenTelemetry was imported with tracing off: {out.stdout.strip()!r}")


def test_span_is_a_usable_noop_when_disabled() -> None:
    """Calling code must not have to check whether tracing is on."""
    check(not T.is_enabled() or True, "")     # state-independent
    with T.span("test", input={"a": 1}) as sp:
        sp.set("fdr.x", 1)
        sp.set("fdr.list", ["a", "b"])
        sp.set_output({"ok": True})
        sp.event("something", k="v")
    # Reaching here without an exception is the assertion.


def test_span_swallows_unserialisable_values() -> None:
    class Weird:
        def __repr__(self): raise RuntimeError("boom")
    with T.span("test") as sp:
        sp.set("fdr.weird", Weird())
        sp.set_output(Weird())
        sp.event("e", bad=Weird())


# ---- fail-open ---------------------------------------------------------------------

def test_dead_phoenix_does_not_break_the_run() -> None:
    """A report is produced, and the process exits 0, when the collector is unreachable."""
    env = {**os.environ,
           "FDR_TRACE_ENABLED": "1",
           "FDR_PHOENIX_ENDPOINT": "http://127.0.0.1:59999/v1/traces"}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(
        [sys.executable, "-m", "fdr", "skeleton", "Dead", "FY2024-25",
         "--assume", "S02", "--json"],
        capture_output=True, text=True, env=env, cwd=root, timeout=180)
    check(out.returncode == 0, f"an unreachable Phoenix broke the run (rc={out.returncode})")
    payload = out.stdout[out.stdout.find("{"):]
    try:
        d = json.loads(payload)
        check(d["entity"] == "Dead", "the payload is malformed after an export failure")
    except json.JSONDecodeError:
        _fails.append("no valid payload produced when the collector was unreachable")


def test_body_exception_propagates_unchanged() -> None:
    """A failure inside a traced block must surface as ITSELF, not as a tracer error.

    Regression. `span()` used to wrap the `yield` in its own try/except and yield a second
    time on failure, so any exception raised inside `with span(...)` was thrown back into
    the generator, caught, and replaced by contextlib's "generator didn't stop after
    throw()". The real error — the one naming the figure that could not be read — was
    discarded and the traceback pointed at tracing.py.

    That is the exact failure mode this module exists to prevent, and it was invisible
    until a traced block actually raised. Asserted with tracing ON, because with tracing
    off the early-return path never had the bug.
    """
    if not _otel_available():
        return                       # nothing to assert without the SDK; not a failure
    prev = os.getenv("FDR_TRACE_ENABLED")
    os.environ["FDR_PHOENIX_ENDPOINT"] = "http://127.0.0.1:59999/v1/traces"
    T.enable()
    try:
        if not T.setup_tracing():
            return                   # setup declined (no exporter) — nothing to assert
        try:
            with T.span("boom", input={"deliberate": True}):
                raise KeyError("net_block")
        except KeyError as e:
            check("net_block" in str(e), f"the original error was altered: {e!r}")
        except RuntimeError as e:
            _fails.append(f"the tracer masked the body exception with {e!r}")
        else:
            _fails.append("an exception raised inside a traced block vanished entirely")
    finally:
        T.shutdown_tracing()
        if prev is None:
            os.environ.pop("FDR_TRACE_ENABLED", None)
        else:
            os.environ["FDR_TRACE_ENABLED"] = prev
        T._INIT = False              # leave the module as we found it: off, uninitialised
        T._ENABLED = False
        T._TRACER = None


def _otel_available() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: F401
            OTLPSpanExporter)
        return True
    except Exception:                 # noqa: BLE001
        return False


# ---- observability must not change the observed ------------------------------------

def test_output_identical_with_tracing_on_and_off() -> None:
    """The §16/§18.3 reproducibility criterion, across the tracing switch."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = [sys.executable, "-m", "fdr", "skeleton", "Same", "FY2023-24", "FY2024-25",
            "--model", "manufacturing", "--assume", "S02,S05,S21",
            "--capacity", "9", "--json"]

    def run(enabled: str) -> dict:
        env = {**os.environ, "FDR_TRACE_ENABLED": enabled,
               # a dead endpoint keeps the test hermetic: the code path is identical,
               # only the export fails, and fail-open is what we already assert above.
               "FDR_PHOENIX_ENDPOINT": "http://127.0.0.1:59999/v1/traces"}
        o = subprocess.run(args, capture_output=True, text=True, env=env, cwd=root, timeout=180)
        return json.loads(o.stdout[o.stdout.find("{"):])

    off, on = run("0"), run("1")
    check(off == on,
          "the report differs depending on whether tracing was enabled — observability is "
          "changing the observed, which makes every traced run unreproducible")


# ---- config -------------------------------------------------------------------------

def test_project_is_separate_from_the_other_tracers() -> None:
    """fs_db and rag own their projects; a shared one would interleave unrelated flows."""
    check(T.PHOENIX_PROJECT == "fdr-planning",
          f"unexpected default project {T.PHOENIX_PROJECT!r}")
    check(T.PHOENIX_PROJECT not in ("fs-db-audit", "cag-audit-rag", "default"),
          "the FDR shares a Phoenix project with another subsystem")


def test_disabled_by_default() -> None:
    """Tracing must never be on unless asked for — the test suites depend on it."""
    check(T._truthy(T.TRACE_ENABLED) is False or os.getenv("FDR_TRACE_ENABLED") == "1",
          "tracing defaults to enabled")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as e:                      # a test that errors is a failure
            _fails.append(f"{t.__name__} raised {type(e).__name__}: {e}")
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
