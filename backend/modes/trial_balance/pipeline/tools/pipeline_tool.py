import functools
import logging
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)

class PipelineFileError(Exception):
    """Raised to signal a structured file-not-found failure."""

    def __init__(self, file_path: str, label: str):
        self.file_path = file_path
        self.label = label
        super().__init__(f"{label} not found: {file_path}")

class PipelineDBError(Exception):
    """Raised to signal a structured database failure (connection, query, or constraint)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

def log_and_redact_exception(context: str, exc: Exception, error_type: str = None) -> dict:
    """Log `exc`'s full traceback server-side and return a client-safe error entry.

    Tools that catch their own exceptions and build an error payload by hand used to
    embed traceback.format_exc() in it. Every route returns that payload verbatim to
    the API client, so the trace -- absolute server paths, source lines, internal call
    structure -- was shipped to the caller. The trace now goes only to the log, keyed
    by `error_id`, which is echoed to the client so an operator can correlate a
    reported failure back to the exact logged trace.

    Returns the dict to append to a response's `errors` list."""
    error_id = uuid.uuid4().hex[:12]
    logger.error("[%s] %s (error_id=%s):\n%s", context, type(exc).__name__, error_id, traceback.format_exc())
    return {
        "type": error_type or type(exc).__name__,
        "error": f"{type(exc).__name__} (see server log, error_id={error_id})",
        "error_id": error_id,
    }

def _failed_call_key(name: str, args: tuple, kwargs: dict) -> str:
    """Stable guard key for one (tool, args, kwargs) call shape. Hashed via
    valkey_client.make_cache_key so an oversized/odd-charactered args repr
    never produces an invalid Valkey key."""
    from modes.trial_balance.pipeline.valkey_client import make_cache_key

    return make_cache_key(name, str(args), str(kwargs))


_SESSION_ID_RE = re.compile(r"sessions[/\\]([0-9a-fA-F-]{36})")

def _derive_session_id(kwargs: dict) -> Optional[str]:
    output_dir = kwargs.get("output_dir")
    if not output_dir:
        return None
    match = _SESSION_ID_RE.search(str(output_dir))
    return match.group(1) if match else None

def _register_artifacts(tool_name: str, kwargs: dict, response: dict) -> None:
    """Best-effort: record this tool's output artifacts into pipeline_artifacts, keyed by
    the session_id embedded in output_dir. This is purely an index for fast lookup ("what
    did tool X write for session Y") and does NOT replace the file-artifact contract --
    downstream tools still read the actual Parquet/JSON paths from `response["artifacts"]`
    exactly as before. A DB hiccup here must never fail a tool call that otherwise
    succeeded, so every exception is caught and logged, not raised. Silently skipped for
    calls whose output_dir doesn't embed a session_id (e.g. ad hoc calls, or the default
    output_dir)."""
    session_id = _derive_session_id(kwargs)
    if not session_id:
        return
    try:
        from modes.trial_balance.pipeline.db import record_tool_artifacts  # local import: avoid a
        # hard dependency from every tool module on the DB layer at import time -- only the
        # decorator (this file) needs it, and only at call time, not at module load time.

        record_tool_artifacts(
            run_id=session_id,
            tool_name=tool_name,
            artifact_paths=response.get("artifacts", []),
            pipeline_status=response.get("pipeline_status"),
        )
    except Exception:
        logger.warning(f"[{tool_name}] artifact registration skipped (non-fatal): {traceback.format_exc()}")

    _upload_and_record_artifact_files(tool_name, session_id, response.get("artifacts", []))


def _upload_and_record_artifact_files(tool_name: str, session_id: str, artifact_paths: list) -> None:
    """Durable-artifact-plane side channel: upload each artifact to MinIO (if
    enabled/reachable) and persist its checksum/size/format/storage_uri into
    pipeline_artifact_files. Strictly additive/best-effort, same posture as
    record_tool_artifacts above -- a MinIO or DB hiccup here must never fail a
    tool call that already succeeded and already wrote its local artifact.
    Local paths remain the ONLY thing tools read/write; this never becomes the
    inter-tool handoff mechanism."""
    if not artifact_paths:
        return
    try:
        from modes.trial_balance.pipeline.db import record_artifact_files
        from modes.trial_balance.pipeline.object_store import upload_artifact

        files = []
        for path in artifact_paths:
            result = upload_artifact(path, session_id, tool_name)
            files.append({
                "artifact_path": path,
                "checksum_sha256": result.checksum_sha256,
                "size_bytes": result.size_bytes,
                "format": result.format,
                "storage_backend": result.storage_backend,
                "storage_uri": result.storage_uri,
                "uploaded_at": datetime.now(timezone.utc) if result.storage_uri else None,
            })
        record_artifact_files(session_id, tool_name, files)
    except Exception:
        logger.warning(f"[{tool_name}] durable-artifact upload/registration skipped (non-fatal): {traceback.format_exc()}")

TOOL_REGISTRY: dict = {}

class ToolEntry:
    """One registered pipeline tool: its domain tag (formerly the tools/<domain>/
    subdirectory it lived in, now just a label) and its decorated callable."""

    __slots__ = ("name", "domain", "func")

    def __init__(self, name: str, domain: str, func):
        self.name = name
        self.domain = domain
        self.func = func

def pipeline_tool(name: str, domain: str = "shared"):
    """Standardize tool returns, catch exceptions, validate artifacts, guard infinite
    loops, and register (name, domain, wrapped function) into TOOL_REGISTRY.

    `domain` replaces the old tools/<domain>/ directory as the signal build_tools()
    filters on via settings.AGENT_TOOL_DOMAINS -- every tool must now pass it
    explicitly here since there is no longer a directory to infer it from.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            logger.info(f"[{name}] Execution STARTED.")

            from modes.trial_balance.pipeline.valkey_client import failed_calls_check, failed_calls_clear, failed_calls_mark

            call_key = _failed_call_key(name, args, kwargs)
            prior_message = failed_calls_check(call_key)
            if prior_message is not None:
                return {
                    "tool": name,
                    "execution_status": "FAILED",
                    "pipeline_status": "FAILED",
                    "can_continue": False,
                    "message": (
                        f"Repeated tool call failed identically -- retrying with the same arguments will not "
                        f"help. The prior failure was: {prior_message!r}. If that message names a prerequisite "
                        f"tool, call that tool first, then retry {name} with its output. Terminating this "
                        f"attempt to prevent an infinite loop."
                    ),
                    "artifacts": [],
                    "errors": [{"type": "InfiniteLoopGuard", "message": "Identical failing tool call detected.", "prior_message": prior_message}],
                }

            response = {
                "tool": name,
                "execution_status": "FAILED",
                "pipeline_status": "FAILED",
                "can_continue": False,
                "message": "Tool failed unexpectedly.",
                "artifacts": [],
                "errors": [],
            }

            try:
                tool_res = func(*args, **kwargs)
                if isinstance(tool_res, dict):
                    response.update(tool_res)

                if response.get("execution_status") == "SUCCESS":
                    missing_artifacts = [art for art in response.get("artifacts", []) if not Path(art).exists()]
                    if missing_artifacts:
                        response["execution_status"] = "FAILED"
                        response["pipeline_status"] = "FAILED"
                        response["can_continue"] = False
                        response["message"] = f"Artifact validation failed. Missing outputs: {', '.join(missing_artifacts)}"
                        response["errors"].append({"type": "ArtifactMissingError", "details": missing_artifacts})

                if isinstance(tool_res, dict) and "can_continue" in tool_res:
                    response["can_continue"] = tool_res["can_continue"]
                elif response.get("execution_status") == "FAILED" or response.get("pipeline_status") == "FAILED":
                    response["can_continue"] = False
                elif response.get("pipeline_status") == "HALTED":
                    response["can_continue"] = False
                else:
                    response["can_continue"] = True

                if response.get("execution_status") == "FAILED":
                    failed_calls_mark(call_key, response.get("message", "Tool failed unexpectedly."))
                else:
                    # Per-key clear only (deliberate fix vs. the prior in-process dict, which
                    # cleared its ENTIRE contents on any unrelated tool's success -- e.g. a
                    # cheap chat lookup succeeding would silently wipe every other tool's
                    # infinite-loop guard too). This call's own guard entry (if any -- a
                    # retried call that now succeeds) is the only one cleared here.
                    failed_calls_clear(call_key)

                    if response.get("execution_status") == "SUCCESS":
                        _register_artifacts(name, kwargs, response)

            except (PipelineFileError, PipelineDBError) as e:
                response["execution_status"] = "FAILED"
                response["pipeline_status"] = "FAILED"
                response["can_continue"] = False
                response["message"] = str(e)
                response["errors"].append({"type": type(e).__name__, "message": str(e)})
            except Exception as e:
                # The full traceback goes to the server log ONLY. It contains absolute
                # server-side file paths and internal call structure, and this response
                # dict is returned verbatim to API clients by every route that calls a
                # tool -- so echoing it back is information disclosure. `error_id` is the
                # correlation handle: it appears in both the client response and the log
                # line, so an operator can pull the exact trace for a reported failure
                # without the trace ever leaving the server.
                error_entry = log_and_redact_exception(name, e)
                response["execution_status"] = "FAILED"
                response["pipeline_status"] = "FAILED"
                response["can_continue"] = False
                response["message"] = (
                    f"Unexpected error in {name} ({type(e).__name__}). "
                    f"Reference error_id={error_entry['error_id']} in the server log for details."
                )
                response["errors"].append(error_entry)

            elapsed = time.time() - start_time
            logger.info(
                f"[{name}] Execution FINISHED in {elapsed:.2f}s | Status: {response['execution_status']} | "
                f"Pipeline: {response['pipeline_status']} | Can Continue: {response['can_continue']}"
            )
            return response

        if name in TOOL_REGISTRY:
            logger.warning(f"Duplicate tool registration for '{name}' -- overwriting the prior entry.")
        TOOL_REGISTRY[name] = ToolEntry(name, domain, wrapper)

        return wrapper

    return decorator

class atomic_write:
    """Context manager: write to `{path}.tmp-{pid}`, fsync, then os.replace() onto `path`.

    os.replace() is atomic on POSIX and Windows -- a reader opening `path` mid-write
    either sees the old complete file or the new complete file, never a partial one.
    A crash before the replace leaves only the .tmp file behind (safe to ignore/clean
    up later); it never corrupts the artifact a downstream tool would read.

    Usage (Polars):
        with atomic_write(out_path) as tmp_path:
            df.write_parquet(tmp_path)

    Usage (JSON):
        with atomic_write(out_path) as tmp_path:
            Path(tmp_path).write_text(json.dumps(payload))

    See write_parquet_atomic() / write_json_atomic() below for the one-line
    wrappers most call sites should use instead of driving this context
    manager directly.
    """

    def __init__(self, final_path):
        self.final_path = Path(final_path)
        self.tmp_path = self.final_path.with_name(f"{self.final_path.name}.tmp-{os.getpid()}")

    def __enter__(self):
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        return str(self.tmp_path)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Write raised -- drop the partial tmp file, leave final_path untouched.
            self.tmp_path.unlink(missing_ok=True)
            return False
        try:
            fd = os.open(self.tmp_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # best-effort fsync; os.replace() below is still atomic regardless
        os.replace(self.tmp_path, self.final_path)
        return False

def write_parquet_atomic(df, path) -> str:
    """`df.write_parquet(path)`, but crash-safe (see atomic_write). Returns
    `str(path)` so call sites can drop it straight into a tool's `artifacts`
    list, e.g. `artifacts=[write_parquet_atomic(out_df, out_path)]`."""
    with atomic_write(path) as tmp_path:
        df.write_parquet(tmp_path)
    return str(path)

def write_json_atomic(obj, path, **json_kwargs) -> str:
    """`Path(path).write_text(json.dumps(obj))`, but crash-safe (see
    atomic_write). Returns `str(path)` for the same reason as
    write_parquet_atomic above."""
    import json as _json

    with atomic_write(path) as tmp_path:
        Path(tmp_path).write_text(_json.dumps(obj, **json_kwargs), encoding="utf-8")
    return str(path)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# In standalone TB-v2, _BACKEND_ROOT.parent happens to equal the repo root (this
# file lives at backend/tools/pipeline_tool.py, one level under the package this
# gateway calls pipeline/), so a __file__-relative fallback and settings.OUTPUT_DIR
# coincidentally agreed. Here, this file lives one level DEEPER
# (pipeline/tools/pipeline_tool.py), so that same relative computation silently
# pointed at modes/trial_balance/output/ instead of the configured TB_OUTPUT_DIR
# (modes/trial_balance/pipeline/output/ by convention) -- caught when a real
# /upload's preview_excel_data call (which omits output_dir) wrote there instead.
# Anchoring on settings.OUTPUT_DIR directly removes the fragile nesting-depth
# assumption entirely.
from modes.trial_balance.pipeline.config import settings

_DEFAULT_OUTPUT_DIR = settings.OUTPUT_DIR

def resolve_output_dir(output_dir) -> Path:
    """Anchor a relative output_dir to TB_OUTPUT_DIR's parent (matching standalone's
    convention of "sessions/" and "output/" as sibling directories), or return
    TB_OUTPUT_DIR itself when none is given."""
    if not output_dir:
        return _DEFAULT_OUTPUT_DIR
    p = Path(output_dir)
    return p if p.is_absolute() else (settings.OUTPUT_DIR.parent / p)


__all__ = [
    'logger',
    'PipelineFileError',
    'PipelineDBError',
    '_SESSION_ID_RE',
    '_derive_session_id',
    '_register_artifacts',
    'log_and_redact_exception',
    'TOOL_REGISTRY',
    'ToolEntry',
    'pipeline_tool',
    'atomic_write',
    'write_parquet_atomic',
    'write_json_atomic',
    '_BACKEND_ROOT',
    '_DEFAULT_OUTPUT_DIR',
    'resolve_output_dir',
]
