"""``main._load_env_files`` -- loading BOTH ``.env`` candidates, not just the
first found.

Real bug, found live: a document converted through a bare `uvicorn` run came
back with "A second independent read by the vision model was not available"
on every table, even though `LLM_BASE_URL`/`LLM_MODEL_NAME`/`GENERATION_API_KEY`
were correctly set in the repo's shared root `.env`. Root cause: the old code
looped over `(ingestion/.env, <repo>/.env)` and `break`-ed after the first
match -- `ingestion/.env` exists in this repo (its own offline-cache
settings), so the shared root `.env` was never reached at all. Docker's own
`env_file:` directive masked this in that deployment; nothing masks it for a
bare process.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_env_loading.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import _load_env_files  # noqa: E402


def _write(path, **kv):
    path.write_text("\n".join(f"{k}={v}" for k, v in kv.items()) + "\n", encoding="utf-8")


def test_both_env_files_are_loaded_not_just_the_first(tmp_path, monkeypatch):
    """THE regression: a value that lives ONLY in the root .env must survive,
    even though ingestion/.env also exists."""
    service_dir = tmp_path / "ingestion"
    service_dir.mkdir()
    _write(service_dir / ".env", INGEST_LOG_LEVEL="debug")
    _write(tmp_path / ".env", LLM_BASE_URL="http://example.test:9999")

    for name in ("INGEST_LOG_LEVEL", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    loaded = _load_env_files(service_dir)

    assert os.environ["LLM_BASE_URL"] == "http://example.test:9999"
    assert os.environ["INGEST_LOG_LEVEL"] == "debug"
    assert loaded == [service_dir / ".env", tmp_path / ".env"]


def test_ingestions_own_value_wins_on_a_name_both_files_define(tmp_path, monkeypatch):
    """Most-specific first: ingestion/.env's own setting is not silently
    overridden by the shared root .env defining the same name."""
    service_dir = tmp_path / "ingestion"
    service_dir.mkdir()
    _write(service_dir / ".env", INGEST_LOG_LEVEL="debug")
    _write(tmp_path / ".env", INGEST_LOG_LEVEL="warning")

    monkeypatch.delenv("INGEST_LOG_LEVEL", raising=False)

    _load_env_files(service_dir)

    assert os.environ["INGEST_LOG_LEVEL"] == "debug"


def test_a_shell_exported_value_beats_either_file(tmp_path, monkeypatch):
    """The same rule backend/app/main.py's own env loading follows: a
    variable the caller already exported is never clobbered by a file."""
    service_dir = tmp_path / "ingestion"
    service_dir.mkdir()
    _write(service_dir / ".env", LLM_BASE_URL="http://from-file:9999")

    monkeypatch.setenv("LLM_BASE_URL", "http://from-shell:1234")

    _load_env_files(service_dir)

    assert os.environ["LLM_BASE_URL"] == "http://from-shell:1234"


def test_a_missing_root_env_is_not_an_error(tmp_path, monkeypatch):
    """No shared root .env at all (a checkout without one) must not raise --
    ingestion/.env alone is a complete, valid configuration on its own."""
    service_dir = tmp_path / "ingestion"
    service_dir.mkdir()
    _write(service_dir / ".env", INGEST_LOG_LEVEL="debug")
    monkeypatch.delenv("INGEST_LOG_LEVEL", raising=False)

    loaded = _load_env_files(service_dir)

    assert loaded == [service_dir / ".env"]
    assert os.environ["INGEST_LOG_LEVEL"] == "debug"


def test_neither_file_present_returns_an_empty_list(tmp_path):
    service_dir = tmp_path / "ingestion"
    service_dir.mkdir()

    assert _load_env_files(service_dir) == []
