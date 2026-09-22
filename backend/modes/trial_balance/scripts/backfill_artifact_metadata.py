"""One-time backfill: register checksum/size/format/MinIO metadata for every
file in the 17+ orphaned `sessions/<uuid>/` directories that predate
pipeline_artifact_files entirely (they were written before Phase 1's
per-file durable-artifact-plane tracking existed). Resolved decision:
backfill these sessions rather than leaving them untouched, so they fall
under the same 90-day retention policy as sessions created going forward.

Deliberately NOT run automatically at boot or on any request path -- this is
a reviewed, one-time operation an operator runs by hand:

    python -m modes.trial_balance.pipeline.scripts.backfill_artifact_metadata [--dry-run]

If MINIO_ENABLED is false (the shipped default) or MinIO is unreachable, each
file is still registered with checksum/size/format and storage_backend=local,
storage_uri=None -- exactly object_store.upload_artifact()'s normal
degradation behavior (see backend/object_store.py). backend/scripts/
cleanup_sessions.py will correctly refuse to delete local files with no
recorded MinIO copy, so running this backfill with MinIO disabled is safe:
it only adds registry rows, it never enables premature deletion.
"""

import argparse
import logging
from pathlib import Path

from modes.trial_balance.pipeline.config import settings
from modes.trial_balance.pipeline.db import record_artifact_files
from modes.trial_balance.pipeline.object_store import upload_artifact

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Same anchor router.py's resolve_output_dir("sessions/...") uses -- must agree
# with wherever a session's local files were actually written, or this script
# silently finds nothing to backfill.
SESSIONS_ROOT = settings.OUTPUT_DIR.parent / "sessions"

# Every real pipeline artifact extension seen in the tools this codebase ships
# (see backend/object_store.py's _FORMAT_BY_SUFFIX) -- filters out any stray
# non-artifact file (.DS_Store, .tmp-<pid> leftovers from a past crash before
# atomic_write existed, etc.) that shouldn't be registered as an artifact.
ARTIFACT_SUFFIXES = {".parquet", ".json", ".docx", ".xlsx", ".md"}


def iter_session_files(sessions_root: Path):
    """Yields (session_id, tool_name, file_path) for every real artifact file
    under sessions_root. `session_id` is the top-level directory name.
    `tool_name` is 'backfill' for files directly under the session dir, or
    'backfill:<subdir>' for a cy/py/comparison-scoped comparative run --
    these historical files predate per-tool attribution, so this is the most
    honest label available: it's clearly distinguishable from a live entry's
    real tool_name, and still records which comparative scope a file
    belonged to."""
    if not sessions_root.is_dir():
        return
    for session_dir in sorted(sessions_root.iterdir()):
        if not session_dir.is_dir():
            continue
        session_id = session_dir.name
        for path in sorted(session_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ARTIFACT_SUFFIXES:
                continue
            rel_parent = path.parent.relative_to(session_dir)
            tool_name = "backfill" if str(rel_parent) == "." else f"backfill:{rel_parent}"
            yield session_id, tool_name, path


def backfill(sessions_root: Path = SESSIONS_ROOT, dry_run: bool = False) -> dict:
    stats = {"sessions": set(), "files": 0, "uploaded_to_minio": 0, "local_only": 0, "errors": 0}

    by_session_tool: dict = {}
    for session_id, tool_name, path in iter_session_files(sessions_root):
        by_session_tool.setdefault((session_id, tool_name), []).append(path)

    for (session_id, tool_name), paths in by_session_tool.items():
        stats["sessions"].add(session_id)
        files_payload = []
        for path in paths:
            if dry_run:
                logger.info("[dry-run] would register %s (session=%s, tool=%s)", path, session_id, tool_name)
                stats["files"] += 1
                continue
            try:
                result = upload_artifact(str(path), session_id, tool_name)
                files_payload.append({
                    "artifact_path": str(path),
                    "checksum_sha256": result.checksum_sha256,
                    "size_bytes": result.size_bytes,
                    "format": result.format,
                    "storage_backend": result.storage_backend,
                    "storage_uri": result.storage_uri,
                })
                stats["files"] += 1
                stats["uploaded_to_minio" if result.storage_backend == "minio" else "local_only"] += 1
            except Exception:
                logger.exception("Failed to register artifact %s (session=%s)", path, session_id)
                stats["errors"] += 1

        if files_payload:
            try:
                record_artifact_files(session_id, tool_name, files_payload)
            except Exception:
                logger.exception("Failed to write pipeline_artifact_files rows for session=%s tool=%s", session_id, tool_name)
                stats["errors"] += len(files_payload)

    stats["sessions"] = len(stats["sessions"])
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List what would be registered without writing anything.")
    args = parser.parse_args()

    logger.info("Backfilling artifact metadata from %s%s", SESSIONS_ROOT, " (dry run)" if args.dry_run else "")
    stats = backfill(dry_run=args.dry_run)
    logger.info(
        "Backfill complete: %d session(s), %d file(s) (%d uploaded to MinIO, %d local-only), %d error(s).",
        stats["sessions"], stats["files"], stats["uploaded_to_minio"], stats["local_only"], stats["errors"],
    )


if __name__ == "__main__":
    main()
