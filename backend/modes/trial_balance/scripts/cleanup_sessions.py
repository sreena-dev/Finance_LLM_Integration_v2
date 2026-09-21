"""Retention job: soft-delete pipeline_sessions older than the retention
window (resolved decision: 90 days), then remove ONLY the local scratch
directory for a session where every one of its files has a confirmed durable
copy in MinIO (storage_backend='minio' with a non-null storage_uri in
pipeline_artifact_files). A session with any file lacking a confirmed MinIO
copy is left on local disk and logged, never deleted -- this deliberately
means a session backfilled or produced while MINIO_ENABLED=false is
retained forever rather than risk destroying the only copy of an audit
artifact. Run as a scheduled job (cron / k8s CronJob) outside the request
path, never automatically at application boot:

    python -m modes.trial_balance.pipeline.scripts.cleanup_sessions [--older-than-days 90] [--dry-run]

This is the last phase of the storage-architecture rollout, run deliberately
after Phase 1 (MinIO durability) is proven, per the phase-ordering rationale:
a destructive-adjacent job must never run before the durable copy it depends
on is trustworthy.
"""

import argparse
import logging
import shutil

from modes.trial_balance.pipeline.config import settings
from modes.trial_balance.pipeline.db import list_artifact_files_for_session, list_expired_sessions, soft_delete_expired_sessions

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Same anchor router.py's resolve_output_dir("sessions/...") uses -- must agree
# with wherever a session's local files were actually written, or this script
# silently finds nothing to clean up.
SESSIONS_ROOT = settings.OUTPUT_DIR.parent / "sessions"


def _session_is_safe_to_delete_locally(session_id: str) -> tuple:
    """(safe, reason). Safe only if pipeline_artifact_files has at least one
    row for this session AND every row is storage_backend='minio' with a
    non-null storage_uri -- a session with zero rows (never registered, e.g.
    written before Phase 1 and never backfilled) or any local-only row is
    never safe."""
    files = list_artifact_files_for_session(session_id)
    if not files:
        return False, "no pipeline_artifact_files rows recorded for this session"
    not_durable = [f["artifact_path"] for f in files if f["storage_backend"] != "minio" or not f["storage_uri"]]
    if not_durable:
        return False, f"{len(not_durable)} file(s) have no confirmed MinIO copy (e.g. {not_durable[0]})"
    return True, f"all {len(files)} file(s) confirmed in MinIO"


def cleanup(older_than_days: int = 90, dry_run: bool = False) -> dict:
    stats = {"soft_deleted": 0, "locally_removed": 0, "retained_no_durable_copy": 0, "no_local_dir": 0, "errors": 0}

    if dry_run:
        logger.info("[dry-run] would soft-delete pipeline_sessions older than %d day(s)", older_than_days)
    else:
        newly_expired = soft_delete_expired_sessions(older_than_days)
        stats["soft_deleted"] = len(newly_expired)
        logger.info("Soft-deleted %d session(s) older than %d day(s).", len(newly_expired), older_than_days)

    for session in list_expired_sessions():
        session_id = session["session_id"]
        local_dir = SESSIONS_ROOT / session_id
        if not local_dir.exists():
            stats["no_local_dir"] += 1
            continue

        safe, reason = _session_is_safe_to_delete_locally(session_id)
        if not safe:
            logger.info("Retaining %s (not safe to delete: %s)", session_id, reason)
            stats["retained_no_durable_copy"] += 1
            continue

        if dry_run:
            logger.info("[dry-run] would remove local dir for %s (%s)", session_id, reason)
            stats["locally_removed"] += 1
            continue

        try:
            shutil.rmtree(local_dir)
            logger.info("Removed local dir for %s (%s)", session_id, reason)
            stats["locally_removed"] += 1
        except OSError:
            logger.exception("Failed to remove local dir for %s", session_id)
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than-days", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = cleanup(older_than_days=args.older_than_days, dry_run=args.dry_run)
    logger.info(
        "Cleanup complete: %d soft-deleted, %d locally removed, %d retained (no durable copy), "
        "%d had no local dir, %d error(s).",
        stats["soft_deleted"], stats["locally_removed"], stats["retained_no_durable_copy"],
        stats["no_local_dir"], stats["errors"],
    )


if __name__ == "__main__":
    main()
