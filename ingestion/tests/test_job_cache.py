"""Not converting the same bytes twice, and the traps in doing so.

`doc_id = sha256(bytes)` has existed since the first version of this pipeline,
but it was computed at the very END of a run and used only as a label -- so
re-uploading a file the service had already read redid 100% of the work:
render, OCR, table structure and every VLM round trip, minutes of it, to
produce a result that already existed.

Two things make a content cache safe rather than merely fast, and both are
pinned here:

* **A cache hit must not outlive the pipeline that produced it.** Deploy an
  accuracy fix, re-upload the document it was written for, and a naive cache
  hands back the old broken extraction because the bytes did not change --
  silently masking the fix. Entries carry their pipeline version.
* **A cache hit must not carry the previous upload's filename.** The same PDF
  is routinely uploaded under a different name, and the name is rendered into
  every citation (prompt rule 18) and onto every table and chunk as
  `source_file`. Serving the cached name points every figure at a file the
  user never uploaded.

Run with::

    cd ingestion
    venv/Scripts/python -m pytest tests/test_job_cache.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import jobs                                    # noqa: E402
from app.config import Config                           # noqa: E402


def _result(filename="original.pdf"):
    """An extracted result in the shape IngestResult.as_dict() produces."""
    return {
        "document": {"doc_id": "up_abc", "filename": filename, "sha256": "f" * 64},
        "identification": {"entity_name": "Acme", "financial_year": "2023-24"},
        "quality": {"grade": "good"},
        "tables": [{"table_id": "t1", "table_md": "| a |", "source_file": filename}],
        "texts": [{"chunk_id": "x1", "content": "hello", "source_file": filename}],
        "pages": [{"page_no": 1, "image_jpeg_b64": "aGk="}],
    }


def _registry():
    return jobs.Registry()


# --------------------------------------------------------------------------
# The cache itself
# --------------------------------------------------------------------------

def test_identical_bytes_hit_the_cache():
    registry = _registry()
    registry._remember("digest-a", _result())

    assert registry._cached("digest-a") is not None


def test_different_bytes_do_not():
    registry = _registry()
    registry._remember("digest-a", _result())

    assert registry._cached("digest-b") is None


def test_an_entry_from_another_pipeline_version_is_not_served(monkeypatch):
    """THE safety property. A cache that survives a pipeline change would hide
    every accuracy fix behind a stale hit for exactly the documents the fix
    was written against."""
    registry = _registry()
    registry._remember("digest-a", _result())
    assert registry._cached("digest-a") is not None

    monkeypatch.setattr(Config, "PIPELINE_VERSION", "9999.99.99")

    assert registry._cached("digest-a") is None


def test_a_stale_entry_is_dropped_rather_than_rechecked_forever(monkeypatch):
    registry = _registry()
    registry._remember("digest-a", _result())
    monkeypatch.setattr(Config, "PIPELINE_VERSION", "9999.99.99")
    registry._cached("digest-a")

    assert "digest-a" not in registry._results


def test_the_cache_is_bounded(monkeypatch):
    """An entry is a whole extracted document, page images included, so this
    is bounded by memory rather than by bookkeeping."""
    monkeypatch.setattr(Config, "RESULT_CACHE_ENTRIES", 3)
    registry = _registry()
    for i in range(6):
        registry._remember(f"digest-{i}", _result())

    assert len(registry._results) == 3
    # Oldest evicted first.
    assert registry._cached("digest-0") is None
    assert registry._cached("digest-5") is not None


def test_a_hit_refreshes_an_entrys_position(monkeypatch):
    monkeypatch.setattr(Config, "RESULT_CACHE_ENTRIES", 2)
    registry = _registry()
    registry._remember("old", _result())
    registry._remember("new", _result())
    registry._cached("old")          # touch it
    registry._remember("newest", _result())

    assert registry._cached("old") is not None, "the touched entry was evicted"
    assert registry._cached("new") is None


# --------------------------------------------------------------------------
# Relabelling -- the citation trap
# --------------------------------------------------------------------------

def test_a_cache_hit_takes_the_filename_actually_uploaded():
    relabelled = jobs._relabel(_result("first-upload.pdf"), "second-upload.pdf")

    assert relabelled["document"]["filename"] == "second-upload.pdf"


def test_every_table_and_chunk_is_relabelled_too():
    """source_file is per row, and it is what a citation names once several
    uploads are merged into one package. Renaming only the document would
    leave every individual citation pointing at the wrong file."""
    relabelled = jobs._relabel(_result("first-upload.pdf"), "second-upload.pdf")

    assert relabelled["tables"][0]["source_file"] == "second-upload.pdf"
    assert relabelled["texts"][0]["source_file"] == "second-upload.pdf"


def test_relabelling_does_not_mutate_the_cached_entry():
    """The entry is shared. If relabelling mutated it, the NEXT upload of the
    same bytes would inherit this upload's filename."""
    cached = _result("original.pdf")
    registry = _registry()
    registry._remember("digest-a", cached)

    jobs._relabel(registry._cached("digest-a"), "renamed.pdf")

    still = registry._cached("digest-a")
    assert still["document"]["filename"] == "original.pdf"
    assert still["tables"][0]["source_file"] == "original.pdf"


def test_relabelling_leaves_the_extracted_content_alone():
    """Only provenance is rewritten. A cache hit that altered a figure would
    be far worse than no cache at all."""
    original = _result("first.pdf")
    relabelled = jobs._relabel(original, "second.pdf")

    assert relabelled["tables"][0]["table_md"] == original["tables"][0]["table_md"]
    assert relabelled["texts"][0]["content"] == original["texts"][0]["content"]
    assert relabelled["identification"] == original["identification"]
    assert relabelled["pages"] == original["pages"]


def test_a_row_without_a_source_file_is_left_as_it_is():
    result = _result()
    result["tables"] = [{"table_id": "t1", "table_md": "| a |"}]

    relabelled = jobs._relabel(result, "new.pdf")

    assert "source_file" not in relabelled["tables"][0]


# --------------------------------------------------------------------------
# The version stamp the cache depends on
# --------------------------------------------------------------------------

def test_the_pipeline_version_is_set():
    """Both the cache key and the gateway's staleness column depend on it."""
    assert Config.PIPELINE_VERSION
