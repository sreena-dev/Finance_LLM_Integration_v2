"""
The live corpus adapter — the only file in `fdr/` that reaches a database.

WHY THIS REPLACED A MATERIALISED FACT STORE
-------------------------------------------
The previous design ran in two steps: `materialise(entity)` extracted every filing into a
local SQLite table, and `run(entity)` read the panel back out of it. The justification was
cost — re-deriving a figure means selecting `table_md`, parsing it, profiling the columns,
binding the labels and running the tie-outs, several hundred tables per entity.

Measured, that cost is 1.4 seconds per filing, so a seven-year panel rebuilds from the
source database in about ten seconds sequentially and two to three concurrently. Against
that, the staging table bought a second copy of the truth that could silently disagree
with the corpus: a binder fix changed what the system SHOULD report, and every entity kept
reporting the old answer until somebody remembered to re-materialise. A cache whose
staleness is invisible in the output is worse than the ten seconds it saves.

So the fact staging table is gone from the read path. This module goes to the corpus, and
the only cache is one that cannot go stale — see `_extract_cached`.

WHY IT IS SAFE FOR `fdr/` TO IMPORT `fs_db`
-------------------------------------------
The package rule that matters is not "`fdr` never sees a database". It is:

    THE SIGNAL EVALUATION PATH IS A PURE FUNCTION OF A PANEL.

A rule that can perform I/O can fail on a dropped connection, and an abstain caused by a
network blip is indistinguishable in the output from an abstain caused by a missing
figure. That would corrupt the one thing this report sells. So the boundary is drawn here
instead: this module produces a `Panel` — plain data, no handles — and nothing downstream
of it can reach anything.

Two consequences, both deliberate:

  - `fs_db` is imported INSIDE the functions, never at module scope. Importing
    `fdr.source` therefore costs nothing and needs no psycopg, so the hermetic test suite
    (`python -m fdr test`) still runs with no database, no network and no model. That
    suite is what proves §18.3 reproducibility; it is not allowed to become
    reproducible-given-identical-database-state.
  - the dependency is one-directional. `fs_db` still imports nothing from `fdr/` or
    `rag/`, so it stays liftable in one move when the corpus migrates.

WHAT THIS MODULE DECIDES
------------------------
Nothing about the entity. It resolves filings, extracts facts, applies the trust gate and
assembles a panel. Every judgement lives in `rules.py`; every judgement about severity
lives in the registry. What it DOES own is the honest accounting of what came back —
which filings failed, which figures were withheld and why — because a panel that is short
three years for a reason nobody recorded produces an abstain nobody can act on.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import panel as PN

# ---- trust gate ----------------------------------------------------------------------
# Lifted verbatim from the store this module replaces, so the change of substrate does
# not quietly change which figures are served.
#
# LOW-confidence scales ARE served. What endangers a trend is not an uncertain scale but
# an INCONSISTENT one: if every year of a series resolved the same way, a wrong guess is
# a constant factor and cancels out of every growth rate, share and ratio a rule computes.
# Only absolute magnitude stays uncertain, and no rule reads absolute magnitude. The
# discipline is applied one layer up, where it can be applied precisely —
# `panel.series()` refuses a window whose scale changes while any year is a guess, and
# `rules._confidence()` caps any diagnostic built on a guessed scale.
SERVE_UNIT_CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})

# A figure whose statement failed its own arithmetic identity is retained for a reviewer
# to see and withheld from every computation.
WITHHELD_VERDICTS = frozenset({"CONTRADICTED"})

# How many filings are extracted at once. Each worker thread holds one Postgres session
# (`fs_db.db` is thread-local), so this is also the connection ceiling this module
# imposes on the corpus. Kept small on purpose: the work is DB-bound, and eight parallel
# sessions per report against a shared analytics database is antisocial.
MAX_EXTRACT_WORKERS = 5

# The executor is held at MODULE scope, not created per call. Thread-local connections
# only pay off if the threads outlive the request; an executor per call would open and
# discard a connection per filing, which is slower than running sequentially.
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(
                    max_workers=MAX_EXTRACT_WORKERS, thread_name_prefix="fdr-extract")
    return _POOL


# ---- the cache that cannot go stale ---------------------------------------------------
# A closed financial year's filing never changes, and extraction is a pure function of
# (doc_id, flavour, the extraction code). So the result is memoisable without any of the
# staleness the fact store had — PROVIDED the key tracks the code.
#
# It does, and not by a version string somebody has to remember to bump: the third
# element of the key is `fs_db.facts.PIPELINE_FINGERPRINT`, a content hash of the binder,
# parser, unit resolver and pipeline source. Edit any of them and every entry is bypassed
# on the next import. There is no invalidation step anybody can forget to run, which is
# the specific failure the fact store had.
#
# Bounded because the corpus is 346 filings and an entry is roughly thirty small dicts;
# the whole corpus cached is single-digit megabytes.
_CACHE: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 512


def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def cache_stats() -> dict[str, int]:
    with _CACHE_LOCK:
        return {"entries": len(_CACHE), "max": _CACHE_MAX}


# ---- what came back ------------------------------------------------------------------

@dataclass
class FilingRead:
    """One filing's extraction outcome. A failure is data, not an exception."""
    doc_id: str
    fy_label: str
    period_end: str
    facts: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"doc_id": self.doc_id, "fy_label": self.fy_label,
                "period_end": self.period_end, "facts": self.facts, "error": self.error}


@dataclass
class SourceReport:
    """The honest accounting of a live read — block 1 of the FDR depends on it.

    A panel that came back three years short is a materially different object depending
    on WHY: the corpus holds only three filings, or two filings failed to parse, or every
    figure in them was withheld by the trust gate. Each has a different fix and a
    different bearing on how much to lean on the report, so each is counted separately
    rather than folded into "3 comparable years".
    """
    entity_id: str
    flavor: str
    filings_found: int = 0
    filings_read: int = 0
    filings_failed: int = 0
    facts_extracted: int = 0
    facts_served: int = 0
    facts_withheld_contradicted: int = 0
    facts_withheld_no_scale: int = 0
    unit_promotions: int = 0
    reads: tuple[FilingRead, ...] = ()
    periods: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    statements: tuple[str, ...] = ()
    extractor_version: str = ""
    pipeline_fingerprint: str = ""
    from_cache: int = 0

    @property
    def errors(self) -> list[dict[str, str]]:
        """Kept in the shape the coverage view and the UI already expect."""
        return [{"doc_id": r.doc_id, "fy_label": r.fy_label, "error": r.error}
                for r in self.reads if r.error]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id, "flavor": self.flavor,
            "filings_found": self.filings_found, "filings_read": self.filings_read,
            "filings_failed": self.filings_failed,
            "facts_total": self.facts_extracted, "facts_trusted": self.facts_served,
            "withheld_contradicted": self.facts_withheld_contradicted,
            "withheld_no_scale": self.facts_withheld_no_scale,
            "unit_promotions": self.unit_promotions,
            "periods": list(self.periods), "years": len(self.periods),
            "keys": list(self.keys), "statements": list(self.statements),
            "filings": [r.to_dict() for r in self.reads],
            "errors": self.errors,
            "extractor_version": self.extractor_version,
            "pipeline_fingerprint": self.pipeline_fingerprint,
            "source": "live", "cache_hits": self.from_cache,
        }


# ---- extraction ----------------------------------------------------------------------

def _fy_label(fy_start: int | None, fy_end: int | None) -> str:
    """One canonical spelling of the financial year, from the corpus's own columns."""
    if fy_end is None:
        return "unknown"
    start = fy_start if fy_start is not None else fy_end - 1
    return f"FY{start}-{str(fy_end)[-2:]}"


def _period_end(fy_end: int | None) -> str:
    """Indian financial years close on 31 March."""
    return f"{fy_end}-03-31" if fy_end else "unknown"


def _extract_cached(doc_id: str, *, entity_id: str, fy_label: str, period_end: str,
                    flavor: str, version: str) -> tuple[list[dict[str, Any]], bool]:
    """Extract one filing, memoised on (doc_id, flavour, extractor version)."""
    from fs_db import facts as FF          # deferred — keeps this module import-light

    key = (doc_id, flavor, version)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return [dict(r) for r in hit], True

    rows = FF.extract(doc_id, entity_id=entity_id, fy_label=fy_label,
                      period_end=period_end, flavor=flavor)

    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            # Plain FIFO eviction. An LRU would need per-read bookkeeping under the lock
            # for a cache whose whole population is 346 entries; the recency signal is
            # not worth the contention.
            for k in list(_CACHE)[:_CACHE_MAX // 4]:
                _CACHE.pop(k, None)
        _CACHE[key] = [dict(r) for r in rows]
    return rows, False


def _serve(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Apply the trust gate. Returns (served, withheld_contradicted, withheld_no_scale)."""
    served: list[dict[str, Any]] = []
    contradicted = no_scale = 0
    for r in rows:
        if r.get("_error"):
            continue
        if r.get("verify_verdict") in WITHHELD_VERDICTS:
            contradicted += 1
            continue
        if r.get("value_inr_lakh") is None \
                or r.get("unit_confidence") not in SERVE_UNIT_CONFIDENCE:
            no_scale += 1
            continue
        served.append(r)
    return served, contradicted, no_scale


def read_entity(entity_id: str, *, flavor: str = "standalone",
                max_years: int = 5,
                progress: Callable[[str, dict], None] | None = None,
                ) -> tuple[list[dict[str, Any]], SourceReport]:
    """Extract every canonical fact for one entity's filings, live from the corpus.

    Returns the SERVED facts (trust gate applied) and the accounting of what happened.
    Never raises for a filing that cannot be processed: an unreadable filing yields an
    error row, the year is reported as uncovered, and the other years still produce a
    report. One bad filing must not take down an entity.
    """
    from fs_db import facts as FF          # deferred

    emit = progress or (lambda *_a, **_k: None)
    version = FF.PIPELINE_FINGERPRINT

    docs = FF.documents_for(entity_id)
    rep = SourceReport(entity_id=entity_id, flavor=flavor, filings_found=len(docs),
                       extractor_version=FF.EXTRACTOR_VERSION,
                       pipeline_fingerprint=version)
    if not docs:
        return [], rep

    # Only the filings that can reach the panel are extracted. `documents_for` returns
    # oldest first and the panel keeps the most recent `max_years`, so reading the older
    # tail is work whose result is discarded — on an eight-filing entity that is three
    # wasted filings, about four seconds, every run.
    #
    # One year of headroom is kept because the comparability gate can DROP a year (a
    # non-consecutive filing run), and a panel that silently came back one year short
    # because this function economised would be a bug that looks like missing data.
    wanted = docs[-(max_years + 1):] if max_years else docs

    jobs = []
    pool = _pool()
    for d in wanted:
        fy = _fy_label(d.get("fy_start"), d.get("fy_end"))
        pe = _period_end(d.get("fy_end"))
        jobs.append((d, fy, pe, pool.submit(
            _extract_cached, d["doc_id"], entity_id=entity_id, fy_label=fy,
            period_end=pe, flavor=flavor, version=version)))

    by_year: dict[str, list[dict[str, Any]]] = {}
    reads: list[FilingRead] = []
    for i, (d, fy, pe, fut) in enumerate(jobs, 1):
        read = FilingRead(doc_id=d["doc_id"], fy_label=fy, period_end=pe)
        try:
            rows, cached = fut.result()
            rep.from_cache += int(cached)
        except Exception as e:                       # noqa: BLE001 — batch resilience
            read.error = f"{type(e).__name__}: {e}"
            rows = []
        else:
            if rows and rows[0].get("_error"):
                read.error = str(rows[0]["_error"])
                rows = []
            read.facts = len(rows)
        by_year[fy] = rows
        reads.append(read)
        emit("extract", {"doc_id": d["doc_id"], "fy": fy, "i": i, "n": len(jobs),
                         "facts": read.facts, "error": read.error})

    # Tier 5 — settle weak scales against an adjacent year, now that every year is in
    # hand. This has to happen across the whole set, which is why extraction is gathered
    # before the trust gate is applied rather than filtered per filing.
    rep.unit_promotions = FF.apply_cross_year(by_year)
    emit("cross_year", {"promoted": rep.unit_promotions})

    everything = [r for rows in by_year.values() for r in rows]
    served, contradicted, no_scale = _serve(everything)

    rep.reads = tuple(reads)
    rep.filings_read = sum(1 for r in reads if not r.error)
    rep.filings_failed = sum(1 for r in reads if r.error)
    rep.facts_extracted = len(everything)
    rep.facts_served = len(served)
    rep.facts_withheld_contradicted = contradicted
    rep.facts_withheld_no_scale = no_scale
    rep.periods = tuple(sorted({r["period_end"] for r in served}))
    rep.keys = tuple(sorted({r["canonical_key"] for r in served}))
    rep.statements = tuple(sorted({r["statement"] for r in served}))
    return served, rep


def load_panel(entity_id: str, *, flavor: str = "standalone", max_years: int = 5,
               progress: Callable[[str, dict], None] | None = None,
               ) -> tuple[PN.Panel, SourceReport]:
    """The one call the FDR makes to get an entity-year panel from the corpus.

    `panel.build` applies the comparability gate (one flavour, consecutive years, scale
    reconciliation, restatement) and can legitimately return a panel shorter than the
    filings supplied. What it dropped, and why, travels on `Panel.notes` and `Panel.gate`.
    """
    rows, rep = read_entity(entity_id, flavor=flavor, max_years=max_years,
                            progress=progress)
    pan = PN.build(rows, entity_id=entity_id, flavor=flavor, max_years=max_years)
    return pan, rep
