"""
Fact extraction — one filing in, scale-normalised canonical facts out.

WHAT THIS IS FOR
----------------
`ratio_pipeline.compute_from_doc()` already does the hard part: statement selection,
parsing, binding and arithmetic verification. What it does NOT do is state the SCALE of
what it bound, or persist anything. Both are fatal for the FDR, whose every trend signal
compares figures ACROSS filings.

This module is the thin layer that closes both gaps:

    doc_id -> compute_from_doc()  ->  bound canonical inputs
                                  +   units.resolve()          -> scale + confidence
                                  +   normalisation            -> value_inr_lakh
                                  =   list[dict]  (plain rows, no ORM, no framework)

It emits dicts on purpose. `fdr/facts_store.py` persists them and `fdr/panel.py` reads
them back, and neither package imports the other — `fs_db` stays self-contained and `fdr`
stays stdlib-only (finance-core packaging rule).

WHAT A FACT CARRIES, AND WHY
----------------------------
Value, scale and normalised value; where it came from (table, row, label, page); how it
was bound; and whether verification confirmed it. A consumer must never have to re-derive
trust — that is the whole reason for materialising rather than re-parsing.

`verify_verdict` is deliberately conservative. A figure is CONFIRMED only when the
statement it came from passed its own arithmetic identity. Everything else is
UNCONFIRMED with the reason stated, and CONTRADICTED where a tie-out it participates in
actually failed. Nothing here decides pass/fail on the entity — it decides how much the
number can be leaned on.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any, Iterable

from . import units as U
from . import db
from .schema import TABLE_CHUNKS

EXTRACTOR_VERSION = "fs_db-facts-1.0.0"

# THE MODULES WHOSE CODE DETERMINES WHAT A FACT IS.
#
# Extraction is a pure function of (filing, this code). A consumer that memoises the
# result therefore needs a key that changes whenever the code does — and "remember to
# bump a version string" is precisely the invalidation step somebody eventually forgets,
# which is how a cache starts reporting last month's binder.
#
# So the fingerprint is CONTENT-DERIVED rather than declared. Edit any of these files and
# every cached extraction is bypassed on the next import, with nothing to remember.
_PIPELINE_MODULES = ("facts.py", "binding.py", "md_parser.py", "units.py",
                     "ratio_pipeline.py", "repository.py", "schedule3.py",
                     # The PROMPT counts as extraction logic, not configuration: editing
                     # it changes which row a key binds to exactly as editing a regex
                     # does. Outside this tuple, a prompt edit would leave every cached
                     # fact looking current while the figures behind them had moved.
                     "llm_bind.py")


def pipeline_fingerprint() -> str:
    """A short content hash of the extraction pipeline's own source."""
    h = hashlib.sha256()
    h.update(EXTRACTOR_VERSION.encode())
    here = Path(__file__).parent
    for name in _PIPELINE_MODULES:
        p = here / name
        try:
            h.update(p.read_bytes())
        except OSError:
            # A module that cannot be read is itself a distinguishing state — record the
            # fact rather than silently hashing the same value as a readable one.
            h.update(f"<unreadable:{name}>".encode())
    return h.hexdigest()[:16]


PIPELINE_FINGERPRINT = pipeline_fingerprint()

# The canonical keys the FDR panel needs, mapped to what `ratio_pipeline` actually emits.
# `_RENAMED_INPUTS` in that module binds LineSpec `total_current_assets` to the input key
# `current_assets`, so the panel's name and the pipeline's name differ for exactly two
# keys. Keeping the map explicit (rather than renaming either side) means neither package
# has to know about the other's vocabulary.
PANEL_KEY_FROM_INPUT: dict[str, str] = {
    "current_assets": "total_current_assets",
    "current_liabilities": "total_current_liabilities",
}

# Which statement each canonical key is sourced from — needed for the period TYPE, which
# is what keeps an instant (balance-sheet) figure from being averaged against a duration
# (P&L / cash-flow) figure.
_BS_KEYS = frozenset({
    "total_assets", "total_equity", "total_current_assets", "total_current_liabilities",
    "total_non_current_assets", "inventories", "trade_receivables", "trade_payables",
    "cash_and_cash_equivalents", "net_fixed_assets", "long_term_borrowings",
    "short_term_borrowings", "equity_share_capital", "reserves_and_surplus",
    "marketable_securities", "lease_liabilities_nc", "lease_liabilities_cl",
    "current_maturities_ltd", "cwip", "other_non_current_assets",
    "total_non_current_liabilities", "total_equity_and_liabilities",
})
_PL_KEYS = frozenset({
    "revenue", "other_income", "total_income", "total_expenses", "pbt", "total_tax",
    "pat", "depreciation", "finance_costs", "cost_of_materials_consumed",
    "purchases_of_stock_in_trade", "changes_in_inventories", "sga_expenses",
})
_CF_KEYS = frozenset({
    "ocf", "icf", "fcf_financing", "net_change_in_cash", "capex_ppe",
    "capex_intangibles", "capex_exploration", "government_grant_cf",
})

INSTANT, DURATION = "instant", "duration"


def _statement_of(key: str) -> tuple[str, str]:
    if key in _BS_KEYS:
        return "balance_sheet", INSTANT
    if key in _PL_KEYS:
        return "profit_loss", DURATION
    if key in _CF_KEYS:
        return "cash_flow", DURATION
    return "unknown", DURATION


# Derived aggregates carry no single source line, so they are not facts — they are
# computed from facts. Emitting them would create a figure with no provenance, which is
# exactly what `numeric_guard` exists to prevent.
_DERIVED = frozenset({"ebit", "avg_total_assets", "avg_inventory", "avg_receivables",
                      "avg_payables", "avg_equity"})


# The binder records provenance as the printed LABEL of the line it bound, not as a table
# id, so a fact cannot be traced back to one table. The scale is therefore resolved per
# STATEMENT — which is the right granularity anyway: a presentation basis applies to a
# whole statement, and the face statements are exactly where a unit banner is printed.
_SECTION_RX = {
    "balance_sheet": "%balance sheet%",
    "profit_loss":   "%profit%",
    "cash_flow":     "%cash flow%",
}


def _statement_units(doc_id: str) -> tuple[dict[str, U.UnitResolution], str | None]:
    """Resolve a scale per statement, plus the filing's modal scale.

    Two passes, because tier 4 reads the document mode and that mode must be computed
    from tiers 1-3 only — otherwise one weak resolution propagates across the filing.

    Within a statement the STRONGEST resolution wins, not the first: a filing prints its
    balance sheet across several chunks and only one of them carries the banner.
    """
    rows = db.query(
        f"""select t.table_id, t.unit, t.table_md, t.financial_stmt_type, t.section,
                   p.content as pc, f.content as fc
              from {TABLE_CHUNKS} t
         left join text_chunks p on p.chunk_id = t.preceding_text_chunk
         left join text_chunks f on f.chunk_id = t.following_text_chunk
             where t.doc_id = %s and t.financial_stmt_type is not null""",
        (doc_id,),
    )
    resolved = [
        (r, U.resolve(unit_column=r["unit"], table_md=r["table_md"],
                      preceding_text=r["pc"], following_text=r["fc"]))
        for r in rows
    ]
    modal = U.modal_scale([x for _, x in resolved])

    rank = {U.HIGH: 3, U.MEDIUM: 2, U.LOW: 1, U.NONE: 0}
    best: dict[str, U.UnitResolution] = {}
    for r, res in resolved:
        if not res.resolved:
            continue
        stmt = r["financial_stmt_type"]
        pat = _SECTION_RX.get(stmt)
        # Corroborate the (unreliable) type tag against the section text. A table whose
        # section contradicts its type is a note schedule mis-tagged as a statement and
        # must not lend its scale to the face statement.
        sect = (r["section"] or "").lower()
        if pat and pat.strip("%") not in sect:
            continue
        if rank[res.confidence] > rank[best.get(stmt, U.UNKNOWN).confidence]:
            best[stmt] = res

    for stmt in _SECTION_RX:
        if stmt not in best and modal in U.SCALES:
            best[stmt] = U.UnitResolution(modal, "INR", "doc_modal", U.LOW,
                                          "no banner on this statement; filing mode used")
    return best, modal


def _verdict(binding: dict, statement: str) -> tuple[str, str]:
    """Translate the binder's per-statement validation into a per-fact verdict.

    A BindingReport carries the tie-outs it ran. A figure is CONFIRMED only if its own
    statement satisfied its identity; if a check FAILED, everything from that statement
    is CONTRADICTED, because the binder cannot say which line was wrong (that is error
    localisation, ROADMAP D3, not yet built).
    """
    kind = {"balance_sheet": "BS", "profit_loss": "PL", "cash_flow": "CF"}.get(statement)
    rep = binding.get(kind) if kind else None
    if rep is None:
        return "UNCONFIRMED", "no binding report for this statement"

    checks = getattr(rep, "validations", None) or []
    if not checks:
        return "UNCONFIRMED", "no arithmetic identity could be run on this statement"
    failed = [c for c in checks if c.get("status") == "FAIL"]
    passed = [c for c in checks if c.get("status") == "PASS"]
    if failed:
        names = ", ".join(str(c.get("check", "?")) for c in failed)
        return "CONTRADICTED", f"statement identity failed: {names}"
    if passed:
        names = ", ".join(str(c.get("check", "?")) for c in passed)
        return "CONFIRMED", f"statement identity held: {names}"
    return "UNCONFIRMED", "every identity was skipped for want of inputs"


def extract(doc_id: str, *, entity_id: str, fy_label: str, period_end: str,
            flavor: str = "standalone",
            extractor_version: str = EXTRACTOR_VERSION) -> list[dict[str, Any]]:
    """Extract every canonical fact from one filing. Returns plain dicts.

    Never raises on a filing that cannot be processed — an entity-year with no usable
    statements yields an empty list, and the panel reports the year as uncovered. A
    thrown exception here would take down a whole batch for one bad filing.
    """
    from .ratio_pipeline import compute_from_doc     # deferred: heavy import chain

    try:
        rep = compute_from_doc(doc_id, flavor)
    except Exception as e:                            # noqa: BLE001 - batch resilience
        return [{"_error": f"{type(e).__name__}: {e}", "doc_id": doc_id,
                 "entity_id": entity_id, "fy_label": fy_label, "flavor": flavor}]

    unit_by_stmt, modal = _statement_units(doc_id)
    prov = rep.input_provenance or {}
    out: list[dict[str, Any]] = []

    for input_key, value in (rep.inputs or {}).items():
        if value is None or input_key in _DERIVED:
            continue
        key = PANEL_KEY_FROM_INPUT.get(input_key, input_key)
        statement, ptype = _statement_of(key)

        src = prov.get(input_key) or ()
        source_kind = str(src[0]) if len(src) > 0 else "unknown"
        detail = str(src[1]) if len(src) > 1 else ""

        # The scale belongs to the STATEMENT the figure was read from. Where that
        # statement carried no banner, the filing mode stands in — stated as LOW, never
        # silently presented as though the statement itself had said so.
        unit = unit_by_stmt.get(statement) or (
            U.UnitResolution(modal, "INR", "doc_modal", U.LOW,
                             "statement scale unresolved; filing mode used")
            if modal in U.SCALES else U.UNKNOWN)

        verdict, why = _verdict(rep.binding or {}, statement)
        table_id, page = _source_cell(rep.binding or {}, statement, input_key)

        out.append({
            "doc_id": doc_id, "entity_id": entity_id, "fy_label": fy_label,
            "flavor": flavor, "statement": statement, "canonical_key": key,
            "period_end": period_end, "period_type": ptype,
            "table_id": table_id or "", "page": page,

            "value_native": float(value),
            "unit_scale": unit.scale,
            "value_inr_lakh": unit.to_lakh(float(value)),

            "source_statement": statement,
            "source_label": detail[:200],
            "bind_method": source_kind,

            "verify_verdict": verdict, "verify_reason": why,
            "unit_confidence": unit.confidence, "unit_source": unit.source,
            "unit_evidence": unit.evidence[:200],

            "extractor_version": extractor_version,
        })
    return out


def _source_cell(binding: dict, statement: str, input_key: str) -> tuple[str | None, int | None]:
    """Which table and page this figure was read from, if the binder recorded one.

    `BoundLine` carries `table_id`/`page` from the moment it is bound
    (`binding.py` — every construction site passes `pt.table_id, pt.page`); this
    is the first place anything reads them back out. Missing for a figure that
    was DERIVED rather than read off one row (`binding.py`'s `_derive_missing`
    sets no `page`) — that is a real absence, not a lookup failure, and is
    reported as one rather than guessed at.
    """
    kind = {"balance_sheet": "BS", "profit_loss": "PL", "cash_flow": "CF"}.get(statement)
    rep = binding.get(kind) if kind else None
    if rep is None:
        return None, None
    line = rep.bound.get(input_key)
    if line is None:
        return None, None
    return line.table_id, line.page


def _table_from_detail(detail: str, known: dict[str, Any]) -> str | None:
    """Recover the source table id from a provenance string.

    The binder records provenance as free text; rather than parse it, match against the
    table ids actually present in this filing. Longest match wins so a chunk suffix can
    never shadow the full id.
    """
    if not detail:
        return None
    hits = [t for t in known if t and t in detail]
    return max(hits, key=len) if hits else None


def apply_cross_year(facts_by_year: dict[str, list[dict[str, Any]]]) -> int:
    """Tier 5. Settle LOW-confidence scales against an adjacent year, in place.

    The comparative figure in filing N and the current figure in filing N-1 describe the
    same economic quantity. Where the two differ by a clean power of ten, the scales
    differ by exactly that factor, and a neighbouring year whose scale IS known settles
    the one that is not.

    Returns the number of facts promoted.
    """
    years = sorted(facts_by_year)
    promoted = 0
    for i, fy in enumerate(years):
        if i == 0:
            continue
        prior = {(f["canonical_key"], f["flavor"]): f for f in facts_by_year[years[i - 1]]}
        for f in facts_by_year[fy]:
            if f.get("_error") or f["unit_confidence"] in (U.HIGH, U.MEDIUM):
                continue
            p = prior.get((f["canonical_key"], f["flavor"]))
            if not p or p["unit_confidence"] not in (U.HIGH, U.MEDIUM):
                continue
            weak = U.UnitResolution(f["unit_scale"], "INR", f["unit_source"],
                                    f["unit_confidence"])
            better = U.promote_by_cross_year(
                weak, this_year=f["value_native"],
                prior_year_same_key=p["value_native"], prior_scale=p["unit_scale"],
            )
            if better.confidence != weak.confidence or better.scale != weak.scale:
                f["unit_scale"] = better.scale
                f["unit_confidence"] = better.confidence
                f["unit_source"] = better.source
                f["unit_evidence"] = better.evidence[:200]
                f["value_inr_lakh"] = better.to_lakh(f["value_native"])
                promoted += 1
    return promoted


def documents_for(entity_id: str) -> list[dict[str, Any]]:
    """The filings of one entity, oldest first, with the fields a panel needs."""
    return db.query(
        """select doc_id, company, fy_start, fy_end
             from documents where company = %s order by fy_end""",
        (entity_id,),
    )


def entities() -> list[dict[str, Any]]:
    """Every entity with its filing years. `company` is the corpus's own entity key."""
    return db.query(
        """select company as entity_id, count(*) as filings,
                  min(fy_end) as first_fy, max(fy_end) as last_fy
             from documents group by company order by company"""
    )
