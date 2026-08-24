"""Live evaluation of one entity, straight from the filings database.

THE RULE THIS MODULE ENFORCES
-----------------------------
An answer is computed from the corpus, never read back out of a record of what
some earlier run happened to report.

The source project persisted every report into a local SQLite register and then
answered questions from that register. Three things follow from that design, and
all three are why it is not reproduced here:

  * an entity nobody had run was invisible — and the query surface reported that
    absence as a finding ("no entity has this firing") rather than as a gap;
  * an entity run before a threshold changed kept answering with the old verdict,
    with nothing in the response to say so;
  * the register was created empty on first touch, so a completely unpopulated
    system answered every question fluently and wrongly.

So there is no register. `evaluate()` reads the filings, builds the panel and
runs the audit spine on every request. What it keeps in memory afterwards is a
LATENCY cache, which is a different thing from a store of record in the one way
that matters: it can only ever hold what the corpus itself produced, it expires,
and every answer built on it carries the age and the pipeline fingerprint of the
read it came from. A stale answer is therefore visible in the answer.

WHAT IS PURE AND WHAT IS NOT
----------------------------
`fdr.source` performs the I/O and returns a `Panel` — plain data, no handles.
Everything downstream of it (`build_report`, the signal rules, the cluster
packaging) is a pure function of that panel. This is the property that makes an
abstain trustworthy: a diagnostic that did not run did not run because a figure
was missing, never because a socket dropped.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config as CFG

_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"

_import_lock = threading.Lock()
_path_ready = False
_db_ready = False


def ensure_importable() -> None:
    """Put the vendored packages on `sys.path`. No database involved.

    Split from `_ensure_pipeline` on purpose. The registries the intent
    classifier derives its vocabulary from — signals, clusters, the binding
    spec list — are import-time constants in stdlib-only modules. Classifying a
    question, refusing one, or reporting what this mode can answer therefore
    works with no database configured and none reachable. Only reading an
    entity's figures needs a connection.

    The vendored tree imports itself absolutely (`from fs_db import facts`), so
    `pipeline/` has to be on the path — the same arrangement the Financial
    Statement and Trial Balance modes use for their vendored code.

    THE CONNECTION SETTINGS ARE EXPORTED HERE, NOT ONLY IN `_ensure_pipeline`.
    `fs_db.config` reads the environment ONCE, at import, and freezes it into a
    module-level dict. Any import of the vendored tree therefore fixes the
    connection for the life of the process — including the import this function
    performs on behalf of the classifier, which needs no database at all. Doing
    the export only in `_ensure_pipeline` meant a session whose first call was a
    classification froze an empty password, and every later read failed with
    "no password supplied" against a database that was perfectly reachable.

    So the export is best-effort here (silent when nothing is configured, which
    is a legitimate state for classification) and enforced in `_ensure_pipeline`,
    which is the only path that actually needs a connection.
    """
    global _path_ready
    if _path_ready:
        return
    with _import_lock:
        if _path_ready:
            return
        if str(_PIPELINE_DIR) not in sys.path:
            sys.path.insert(0, str(_PIPELINE_DIR))
        settings = CFG.load()
        if settings.configured:
            try:
                CFG.export_fsdb_env(settings.dsn or "")
            except ValueError:
                # A malformed DSN must not stop the classifier importing. The
                # read path re-raises it with the same message.
                pass
        _path_ready = True


def _ensure_pipeline() -> None:
    """Everything `ensure_importable` does, plus the connection settings.

    Required before any read of the corpus. Raises rather than degrading: a
    figure question with no database behind it has no honest answer.
    """
    global _db_ready
    ensure_importable()
    if _db_ready:
        return
    with _import_lock:
        if _db_ready:
            return
        settings = CFG.load()
        if not settings.configured:
            raise RuntimeError(
                "No filings database configured. Set FINANCE_DSN (shared with the "
                "Statutory Auditor's Report mode) or FDR_DSN in the environment."
            )
        CFG.export_fsdb_env(settings.dsn or "")
        _db_ready = True


# ---------------------------------------------------------------------------
# What an evaluation produced
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Evaluated:
    """One entity, evaluated. Everything an answer is allowed to be built from."""

    entity_id: str
    flavor: str
    payload: dict[str, Any]           # the §14.3 report contract
    panel: Any                        # fdr.panel.Panel — figures by key and year
    coverage: dict[str, Any]          # what the corpus actually yielded
    years: tuple[str, ...]
    computed_at: float                # monotonic clock, for age
    computed_at_wall: str             # ISO-8601, for the reader
    extractor_version: str
    pipeline_fingerprint: str

    def age_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.computed_at)

    def provenance(self) -> dict[str, Any]:
        """Stamped onto every answer. This is what makes a stale read visible
        instead of silent — the failure mode that made the original register
        untrustworthy."""
        return {
            "entity_id": self.entity_id,
            "flavor": self.flavor,
            "years": list(self.years),
            "computed_at": self.computed_at_wall,
            "age_seconds": round(self.age_seconds(), 1),
            "source": "live:finance_llm",
            "extractor_version": self.extractor_version,
            "pipeline_fingerprint": self.pipeline_fingerprint,
        }


# ---------------------------------------------------------------------------
# The latency cache
# ---------------------------------------------------------------------------

_cache: "OrderedDict[tuple[str, str, int], Evaluated]" = OrderedDict()
_cache_lock = threading.Lock()
# One lock per entity key, so two people asking about the same entity at the
# same moment run ONE extraction rather than two. Without it the second request
# waits the full extraction time for a result the first already produced.
_entity_locks: dict[tuple[str, str, int], threading.Lock] = {}
_entity_locks_guard = threading.Lock()


def _lock_for(key: tuple[str, str, int]) -> threading.Lock:
    with _entity_locks_guard:
        lock = _entity_locks.get(key)
        if lock is None:
            lock = _entity_locks[key] = threading.Lock()
        return lock


def _cached(key: tuple[str, str, int], ttl: int) -> Evaluated | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        if ttl <= 0 or hit.age_seconds() > ttl:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit


def _store(key: tuple[str, str, int], value: Evaluated, max_entries: int) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > max_entries:
            _cache.popitem(last=False)


def invalidate(entity_id: str | None = None) -> int:
    """Drop cached evaluations. Whole cache when no entity is named.

    Exposed because the corpus can change under a long-lived process: a newly
    ingested filing should be answerable without a restart.
    """
    with _cache_lock:
        if entity_id is None:
            n = len(_cache)
            _cache.clear()
            return n
        doomed = [k for k in _cache if k[0] == entity_id]
        for k in doomed:
            _cache.pop(k, None)
        return len(doomed)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _fy_label(fy_end: int | None) -> str:
    """The corpus stores the closing year; Indian financial years are named for
    the span. Rendered in one place so every surface spells it the same way."""
    if not fy_end:
        return "unknown"
    return f"FY{int(fy_end) - 1}-{str(int(fy_end))[-2:]}"


def entities() -> list[dict[str, Any]]:
    """Every entity in the filings corpus, for the picker.

    Cheap by construction: one GROUP BY over `documents`. It deliberately does
    NOT extract anything — populating a dropdown must not cost a minute of
    database time, and `readiness` answers the deeper question for one entity on
    demand.
    """
    _ensure_pipeline()
    from fdr import panel as PN
    from fs_db import facts as FF

    rows = []
    for row in FF.entities():
        filings = int(row.get("filings") or 0)
        first_fy, last_fy = row.get("first_fy"), row.get("last_fy")
        rows.append({
            "entity_id": row["entity_id"],
            "filings": filings,
            "first_fy": first_fy,
            "last_fy": last_fy,
            "first_fy_label": _fy_label(first_fy),
            "last_fy_label": _fy_label(last_fy),
            # Whether a TREND can be computed at all. Below this the panel is
            # still built and level signals still run — only trend diagnostics
            # abstain — so this is a capability flag, not a gate.
            "trend_capable": filings >= PN.MIN_TREND_YEARS,
            "min_trend_years": PN.MIN_TREND_YEARS,
        })
    return rows


def evaluate(entity_id: str, *, flavor: str | None = None,
             max_years: int | None = None, refresh: bool = False,
             progress: Callable[[str, dict[str, Any]], None] | None = None) -> Evaluated:
    """Read this entity's filings and run the audit spine over them.

    `progress` is called with (stage, detail) as the work proceeds. The stages
    that matter are the SLOW ones — one per filing parsed — because that is the
    only part of this pipeline a user waits on. Everything after the panel is
    built is arithmetic and completes in under a millisecond, so there is
    nothing there worth reporting on.

    Raises `LookupError` when the entity has no filings — the honest answer to a
    question about an entity the corpus does not hold, and distinct from an
    entity that HAS filings none of which could be read.
    """
    _ensure_pipeline()
    settings = CFG.load()
    flavor = flavor or settings.flavor
    max_years = max_years or settings.max_years
    key = (entity_id, flavor, max_years)
    emit = progress or (lambda *_a, **_k: None)

    if not refresh:
        hit = _cached(key, settings.cache_ttl_seconds)
        if hit is not None:
            emit("cached", {"age_seconds": round(hit.age_seconds(), 1),
                            "years": list(hit.years)})
            return hit

    with _lock_for(key):
        # Re-check inside the lock: whoever held it may have just produced the
        # very result this request is about to spend ten seconds recomputing.
        if not refresh:
            hit = _cached(key, settings.cache_ttl_seconds)
            if hit is not None:
                emit("cached", {"age_seconds": round(hit.age_seconds(), 1),
                                "years": list(hit.years)})
                return hit

        from fdr import source as SRC
        from fdr.assemble import build_report
        from fdr.model import BusinessProfile

        emit("read:start", {"entity_id": entity_id, "flavor": flavor})
        panel, src = SRC.load_panel(entity_id, flavor=flavor, max_years=max_years,
                                    progress=emit)
        emit("evaluate", {"years": list(panel.years)})
        coverage = src.to_dict()

        if not src.filings_found:
            raise LookupError(
                f"No filings are held for '{entity_id}' in the corpus."
            )

        # §2.1 makes the business understanding the precondition for
        # INTERPRETATION — not for measurement. The classifier that forms it is
        # part of the report-generation feature and is not vendored here, so the
        # profile is honestly left unformed: signals still evaluate and clusters
        # still raise, while every cluster carries the spec's own
        # `interpretation_withheld` note. Reporting an unformed profile as
        # unformed is a defined state; inventing one would not be.
        profile = BusinessProfile(
            basis="No business-model classification is in force for this mode. §2.1 "
                  "bars interpretation without one, so diagnostics are reported as "
                  "measured and their interpretation is withheld."
        )

        report = build_report(
            entity_id,
            tuple(panel.years),
            flavor=flavor,
            comparable_years=len(panel.years),
            business_profile=profile,
            panel=panel,
            statements_received=src.statements,
            fact_coverage=coverage,
        )

        payload = report.to_dict()
        payload["panel"] = panel.coverage()
        payload["fact_coverage"] = coverage

        evaluated = Evaluated(
            entity_id=entity_id,
            flavor=flavor,
            payload=payload,
            panel=panel,
            coverage=coverage,
            years=tuple(panel.years),
            computed_at=time.monotonic(),
            computed_at_wall=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            extractor_version=str(src.extractor_version or "unknown"),
            pipeline_fingerprint=str(src.pipeline_fingerprint or "unknown"),
        )
        _store(key, evaluated, settings.cache_max_entries)
        return evaluated


def readiness(evaluated: Evaluated) -> list[dict[str, Any]]:
    """Per signal: could it be evaluated for this entity, and if not, why not.

    Computed from the panel rather than from the run, so it explains a silence
    the report itself cannot: a cluster that says nothing because its inputs were
    never bound looks identical, in the report, to one that says nothing because
    everything was fine.
    """
    _ensure_pipeline()
    from fdr import clusters as CL, derivations as DV, panel as PN, rules as RULES, signals as SG

    out = []
    for signal in SG.SIGNALS:
        missing = evaluated.panel.missing(signal.inputs)
        derivation = DV.for_signal(signal.id)
        if signal.availability == SG.NOTE:
            state = "NOTE_LEVEL"
            why = ("Needs note-level extraction, which is not built. The derivation and "
                   "its source schedules are stated so the figures can be pulled by hand.")
        elif signal.id not in RULES.IMPLEMENTED:
            state, why = "NO_RULE", "Inputs may exist; this diagnostic is not written yet."
        elif missing:
            state = "MISSING_INPUTS"
            why = f"Not bound in every year: {', '.join(missing)}."
        elif signal.is_trend and not evaluated.panel.is_trend_capable:
            state = "SHORT_SERIES"
            why = (f"{len(evaluated.years)} comparable year(s); a trend needs "
                   f"{PN.MIN_TREND_YEARS}.")
        else:
            state, why = "READY", ""
        out.append({
            "signal_id": signal.id,
            "title": signal.title,
            "layer": signal.layer,
            "clusters": CL.clusters_for(signal.id),
            "state": state,
            "reason": why,
            "missing": list(missing),
            "formula": derivation.formula if derivation else "",
            "ar_source": list(derivation.source_lines()) if derivation else [],
        })
    return out
