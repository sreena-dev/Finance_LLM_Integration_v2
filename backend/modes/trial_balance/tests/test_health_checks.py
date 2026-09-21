"""backend/health_checks.py: each engine check reports ok/disabled/error
correctly and never raises, regardless of whether the underlying service is
actually reachable in this test environment."""

from dataclasses import replace

import modes.trial_balance.pipeline.health_checks as hc
import modes.trial_balance.pipeline.valkey_client as vc

# Captured before the autouse fake_valkey fixture (tests/conftest.py) ever
# monkeypatches vc.get_valkey_client -- restored in the one test below that
# needs to exercise a genuinely unreachable Valkey.
_REAL_GET_VALKEY_CLIENT = vc.get_valkey_client


def test_check_postgres_ok_against_the_real_test_db(db_available):
    if not db_available:
        import pytest
        pytest.skip("No reachable Postgres DB for this test run.")
    result = hc.check_postgres()
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], float)


def test_check_valkey_disabled_when_config_says_so(monkeypatch):
    monkeypatch.setattr(hc, "settings", replace(hc.settings, VALKEY_ENABLED=False))
    assert hc.check_valkey() == {"status": "disabled"}


def test_check_valkey_ok_against_fake_valkey(fake_valkey):
    """fake_valkey (tests/conftest.py, autouse) patches
    backend.valkey_client.get_valkey_client -- check_valkey imports that
    function locally at call time, so it picks up the patch."""
    result = hc.check_valkey()
    assert result["status"] == "ok"


def test_check_minio_disabled_when_config_says_so(monkeypatch):
    monkeypatch.setattr(hc, "settings", replace(hc.settings, MINIO_ENABLED=False))
    assert hc.check_minio() == {"status": "disabled"}


def test_check_minio_error_when_enabled_but_unreachable(monkeypatch):
    monkeypatch.setattr(hc, "settings", replace(
        hc.settings, MINIO_ENABLED=True, MINIO_ENDPOINT="http://localhost:1",
        MINIO_ACCESS_KEY="x", MINIO_SECRET_KEY="y", MINIO_BUCKET="tb-artifacts",
    ))
    result = hc.check_minio()
    assert result["status"] == "error"
    assert "detail" in result


def test_check_duckdb_ok():
    result = hc.check_duckdb()
    assert result["status"] == "ok"


def test_check_storage_health_returns_all_four_engines(fake_valkey, db_available):
    if not db_available:
        import pytest
        pytest.skip("No reachable Postgres DB for this test run.")
    result = hc.check_storage_health()
    assert set(result.keys()) == {"postgres", "valkey", "minio", "duckdb"}
    for engine, r in result.items():
        assert r["status"] in ("ok", "disabled", "error")


def test_no_check_ever_raises_even_with_garbage_config(monkeypatch):
    """The overarching contract: a misconfigured/unreachable engine must
    degrade to status='error', never propagate an exception into /health.
    Bypasses the autouse fake_valkey fixture for the Valkey half of this
    check (same technique as test_valkey_client.py's unreachable_valkey
    fixture) -- otherwise the fake client would mask the very unreachability
    this test exists to exercise."""
    monkeypatch.setattr(vc, "get_valkey_client", _REAL_GET_VALKEY_CLIENT)
    monkeypatch.setattr(vc, "settings", replace(vc.settings, VALKEY_HOST="not-a-real-host-xyz", VALKEY_PORT=1))
    monkeypatch.setattr(vc, "_pool", None)

    monkeypatch.setattr(hc, "settings", replace(
        hc.settings, VALKEY_ENABLED=True,
        MINIO_ENABLED=True, MINIO_ENDPOINT="http://localhost:1", MINIO_ACCESS_KEY="", MINIO_SECRET_KEY="",
    ))
    result = hc.check_storage_health()  # must not raise
    assert result["valkey"]["status"] == "error"
    assert result["minio"]["status"] == "error"
