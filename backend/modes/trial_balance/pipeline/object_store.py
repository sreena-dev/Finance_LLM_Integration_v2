"""Durable artifact plane (MinIO, S3 API via boto3).

Mirrors backend/valkey_client.py's shape: one lazily-built client, small typed
helpers, graceful degradation. MinIO is a SIDE-CHANNEL durability layer only --
tools still read/write local paths exclusively (see
backend/tools/pipeline_tool.py's module docstring on the artifact-only
communication invariant); nothing here changes what a tool receives as its
input `*_file` arguments or what it returns in `response["artifacts"]`.

MINIO_ENABLED defaults to false (unlike Valkey, which defaults to true) --
this is a genuinely new capability the codebase never had, and a clean local
checkout should keep working exactly as it does today (local-filesystem-only)
until an operator explicitly opts in, per the resolved rollout decision.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from modes.trial_balance.pipeline.config import settings

logger = logging.getLogger(__name__)

_FORMAT_BY_SUFFIX = {
    ".parquet": "parquet",
    ".json": "json",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".md": "md",
}

_CHECKSUM_CHUNK_SIZE = 1024 * 1024  # 1 MiB -- large report artifacts (docx/xlsx)
# must not be read into memory whole just to hash them.

_client = None
_bucket_ensured = False


@dataclass
class ArtifactUploadResult:
    storage_backend: str  # "local" | "minio"
    storage_uri: Optional[str]
    checksum_sha256: str
    size_bytes: int
    format: str


def _get_client():
    global _client
    if not settings.MINIO_ENABLED:
        return None
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_ENDPOINT,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name="us-east-1",
        )
    return _client


def _ensure_bucket(client) -> None:
    global _bucket_ensured
    if _bucket_ensured:
        return
    try:
        client.head_bucket(Bucket=settings.MINIO_BUCKET)
    except (BotoCoreError, ClientError):
        client.create_bucket(Bucket=settings.MINIO_BUCKET)
    _bucket_ensured = True


def _sha256_and_size(path: Path) -> tuple:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHECKSUM_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _infer_format(path: Path) -> str:
    return _FORMAT_BY_SUFFIX.get(path.suffix.lower(), path.suffix.lstrip(".").lower() or "unknown")


def upload_artifact(local_path: str, session_id: str, tool_name: str) -> ArtifactUploadResult:
    """Checksum + size + format are always computed and returned (local
    filesystem stats, no MinIO dependency) -- only the actual object upload
    is conditional on MinIO being enabled/reachable. This means the caller
    (pipeline_tool.py's _register_artifacts) always gets a complete
    ArtifactUploadResult to persist via db.record_artifact_files, with
    storage_backend/storage_uri simply reflecting whether the durable copy
    actually landed."""
    path = Path(local_path)
    checksum, size = _sha256_and_size(path)
    fmt = _infer_format(path)

    client = _get_client()
    if client is None:
        return ArtifactUploadResult(
            storage_backend="local", storage_uri=None, checksum_sha256=checksum, size_bytes=size, format=fmt,
        )

    key = f"sessions/{session_id}/{tool_name}/{path.name}"
    try:
        _ensure_bucket(client)
        client.upload_file(str(path), settings.MINIO_BUCKET, key)
        storage_uri = f"s3://{settings.MINIO_BUCKET}/{key}"
        return ArtifactUploadResult(
            storage_backend="minio", storage_uri=storage_uri, checksum_sha256=checksum, size_bytes=size, format=fmt,
        )
    except (BotoCoreError, ClientError) as e:
        # A MinIO outage/misconfiguration must never fail the tool call that
        # produced this artifact -- degrade to "checksum recorded, no durable
        # copy yet" exactly like a Valkey outage degrades to "no cache".
        logger.warning("[object_store] upload_artifact(%s) failed, staying local-only: %s", local_path, e)
        return ArtifactUploadResult(
            storage_backend="local", storage_uri=None, checksum_sha256=checksum, size_bytes=size, format=fmt,
        )


def download_artifact(storage_uri: str, dest_path: str) -> bool:
    """For a future restore/rehydrate flow (e.g. Phase 3's cleanup script
    confirming a durable copy before deleting local scratch, or restoring a
    session's artifacts after local disk was reclaimed). Not called by any
    tool today. Returns False (does not raise) on any failure or if MinIO is
    disabled -- callers must check the return value, not assume success."""
    client = _get_client()
    if client is None or not storage_uri.startswith("s3://"):
        return False
    _, _, rest = storage_uri.partition("s3://")
    bucket, _, key = rest.partition("/")
    try:
        client.download_file(bucket, key, dest_path)
        return True
    except (BotoCoreError, ClientError) as e:
        logger.warning("[object_store] download_artifact(%s) failed: %s", storage_uri, e)
        return False
