"""Run-provenance metadata (spec OUT-05) — attached once per report so every run
is traceable: which file(s), which model, which mapping/prompt logic version, and
when it ran. No automated versioning scheme exists yet, so MAPPING_VERSION /
PROMPT_VERSION start as simple hand-bumped constants rather than something
derived automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone

from yukta_rag.core.config import GENERATION_MODEL

MAPPING_VERSION = "1.0"
PROMPT_VERSION = "1.0"


def build_run_log(file_name: str, file_hash: str, output_file_name: str) -> dict:
    """Return the 7 spec-required run-log fields as a dict, ready to render."""
    return {
        "file_name": file_name,
        "file_hash": file_hash,
        "model_version": GENERATION_MODEL,
        "prompt_version": PROMPT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "run_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output_file_name": output_file_name,
    }
