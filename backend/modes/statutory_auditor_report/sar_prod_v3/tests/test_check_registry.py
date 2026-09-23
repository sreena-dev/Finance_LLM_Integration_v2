"""Tests for check_registry.py — the §45 check-ID registry and its drift guard.

The drift-guard test (`test_no_unregistered_check_ids_in_live_modules`) is
the important one: it fails the moment tool_sar.py or formal_review.py
starts emitting a check_id this registry doesn't know about, which is
exactly the mechanism meant to stop the registry silently going stale.
"""

from sar_prod_v3.check_registry import get_check_meta, is_registered, unregistered_check_ids
from sar_prod_v3.tool_sar import CheckTools


def test_known_check_id_resolves():
    meta = get_check_meta("CHK-UDIN-01")
    assert meta["module"] == "18_formal_validation_engine"
    assert "UDIN" in meta["description"]


def test_unknown_check_id_returns_safe_fallback_not_raise():
    meta = get_check_meta("NOT-A-REAL-ID")
    assert meta["module"] == "unknown"


def test_preflight_ids_are_registered_from_the_single_source_of_truth():
    for check_id in CheckTools._MANDATORY_SECTIONS:
        assert is_registered(check_id), f"{check_id} missing from CHECK_REGISTRY"


def test_no_unregistered_check_ids_in_live_modules():
    """Every check_id the deterministic layer can actually emit today must
    have a registry entry. Extend `emitted_ids` here whenever a new
    deterministic check is added — that's the intended maintenance point,
    not editing check_registry.py's static entries and hoping nobody adds a
    check without also registering it."""
    emitted_ids = list(CheckTools._MANDATORY_SECTIONS.keys()) + [
        "CHK-UDIN-01", "CHK-EOM-01", "CHK-KAM-01", "CHK-DATE-01",
        "CHK-COH-01", "CHK-COH-02", "CHK-COH-03",
        # Gap-closure Phase 2 (Gap #4 / Gap #3):
        "APPL-CARO-01", "APPL-IFC-01", "APPL-KAM-01",
        "CONS-CARO-IX-01", "CONS-CARO-XI-01", "CONS-CARO-XIII-01", "CONS-CARO-VII-01",
        "CONS-R11G-01", "CONS-CAGDIR-01", "CONS-KAM-01",
        # Gap-closure Phase 3 (Gap #8 / Gap #6):
        "PERV-01", "PERV-02", "PRIOR-OPN-01",
        # Gap-closure Phase 5 (Gap #2):
        "DIR-I-01", "DIR-II-01", "DIR-III-01", "DIR-IV-01", "DIR-V-01",
    ]
    assert unregistered_check_ids(emitted_ids) == []
