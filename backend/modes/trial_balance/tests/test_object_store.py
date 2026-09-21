"""Unit coverage for backend/object_store.py: checksum/size/format computed
locally regardless of MinIO state, upload/download round-trip against a moto-
mocked S3 backend, and graceful degradation when MinIO is disabled or
unreachable -- a MinIO outage must never fail the tool call that produced the
artifact (see backend/tools/pipeline_tool.py's _upload_and_record_artifact_files,
the only caller in the actual pipeline)."""

import hashlib
from dataclasses import replace

import boto3
import pytest
from moto import mock_aws

import modes.trial_balance.pipeline.object_store as os_module


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "canonical_tb.parquet"
    path.write_bytes(b"fake parquet bytes for checksum testing")
    return path


def _expected_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Local-only mode (MINIO_ENABLED=false, the shipped default) ─────────────

def test_upload_artifact_computes_checksum_and_size_even_when_minio_disabled(sample_file, monkeypatch):
    monkeypatch.setattr(os_module, "settings", replace(os_module.settings, MINIO_ENABLED=False))
    monkeypatch.setattr(os_module, "_client", None)

    result = os_module.upload_artifact(str(sample_file), "sess-1", "build_canonical_tb")

    assert result.storage_backend == "local"
    assert result.storage_uri is None
    assert result.checksum_sha256 == _expected_sha256(sample_file)
    assert result.size_bytes == sample_file.stat().st_size
    assert result.format == "parquet"


def test_infer_format_covers_all_report_types(tmp_path, monkeypatch):
    monkeypatch.setattr(os_module, "settings", replace(os_module.settings, MINIO_ENABLED=False))
    monkeypatch.setattr(os_module, "_client", None)

    for suffix, expected_format in [(".parquet", "parquet"), (".json", "json"), (".docx", "docx"), (".xlsx", "xlsx"), (".md", "md")]:
        f = tmp_path / f"artifact{suffix}"
        f.write_bytes(b"x")
        result = os_module.upload_artifact(str(f), "sess-1", "some_tool")
        assert result.format == expected_format


# ── Real upload/download round-trip against a mocked S3 (moto) ─────────────

@pytest.fixture
def minio_enabled(monkeypatch):
    """moto's mock_aws intercepts calls made to boto3's standard AWS endpoint
    resolution, not an arbitrary custom endpoint_url (a real MinIO deployment
    would use MINIO_ENDPOINT, but moto can't intercept a call actually aimed
    at that URL) -- so the client built for these tests omits endpoint_url
    entirely. This still exercises 100% of object_store.py's own logic
    (checksum/format inference, bucket-ensure, upload, download, error
    handling); only the endpoint construction line itself is bypassed."""
    monkeypatch.setattr(os_module, "settings", replace(
        os_module.settings, MINIO_ENABLED=True, MINIO_BUCKET="tb-artifacts-test",
        MINIO_ACCESS_KEY="testing", MINIO_SECRET_KEY="testing",
    ))
    monkeypatch.setattr(os_module, "_bucket_ensured", False)
    test_client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(os_module, "_get_client", lambda: test_client)
    return test_client


@mock_aws
def test_upload_artifact_round_trips_through_real_s3_api(sample_file, minio_enabled, tmp_path):
    result = os_module.upload_artifact(str(sample_file), "sess-2", "build_canonical_tb")

    assert result.storage_backend == "minio"
    assert result.storage_uri == "s3://tb-artifacts-test/sessions/sess-2/build_canonical_tb/canonical_tb.parquet"
    assert result.checksum_sha256 == _expected_sha256(sample_file)

    dest = tmp_path / "downloaded.parquet"
    ok = os_module.download_artifact(result.storage_uri, str(dest))
    assert ok is True
    assert dest.read_bytes() == sample_file.read_bytes()


@mock_aws
def test_upload_artifact_creates_bucket_if_missing(sample_file, minio_enabled):
    client = boto3.client("s3", region_name="us-east-1")
    with pytest.raises(Exception):
        client.head_bucket(Bucket="tb-artifacts-test")

    os_module.upload_artifact(str(sample_file), "sess-3", "tool_x")

    # Bucket now exists -- upload_artifact's _ensure_bucket created it.
    client.head_bucket(Bucket="tb-artifacts-test")


def test_download_artifact_returns_false_when_minio_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(os_module, "settings", replace(os_module.settings, MINIO_ENABLED=False))
    monkeypatch.setattr(os_module, "_client", None)
    assert os_module.download_artifact("s3://bucket/key", str(tmp_path / "out.parquet")) is False


def test_download_artifact_returns_false_for_non_s3_uri(minio_enabled, tmp_path):
    assert os_module.download_artifact("/local/path/file.parquet", str(tmp_path / "out.parquet")) is False


# ── Graceful degradation on a genuine upload failure ────────────────────────

@mock_aws
def test_upload_artifact_degrades_to_local_on_client_error(sample_file, minio_enabled, monkeypatch):
    """A real ClientError (e.g. permissions, network) during upload must not
    raise -- the caller (pipeline_tool.py) already succeeded at writing the
    local artifact and must not have that turned into a tool failure."""
    def _broken_upload_file(*args, **kwargs):
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutObject")

    monkeypatch.setattr(minio_enabled, "upload_file", _broken_upload_file)

    result = os_module.upload_artifact(str(sample_file), "sess-4", "tool_y")
    assert result.storage_backend == "local"
    assert result.storage_uri is None
    assert result.checksum_sha256 == _expected_sha256(sample_file)
