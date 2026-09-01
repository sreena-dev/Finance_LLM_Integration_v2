"""Which entities can the Financial Statements mode actually find, and which can't?

    python -m scripts.audit_fs_entities                 # human-readable report
    python -m scripts.audit_fs_entities --json out.json # machine-readable too
    python -m scripts.audit_fs_entities --only "gujarat" # one entity, verbose

WHY THIS EXISTS
---------------
The FS mode reports "no annual report found for company X" for entities whose
filings are demonstrably in the corpus. There are eight candidate causes and
guessing between them from the outside is hopeless, so this script asks the
database and the real resolver directly.

The important design decision: it imports and calls the SHIPPING resolver
(`DocumentResolver._resolve_document`) rather than reimplementing its SQL. A
reimplementation would drift, and an audit that tests something other than what
runs in production is worse than no audit — it would give confidence in the
wrong place.

STRICTLY READ-ONLY
------------------
The connection is opened with `SET default_transaction_read_only = on` — the
same guard the vendored `fs_db/db.py` uses on this database — so this script
cannot write even through a bug. It issues nothing but SELECT.

WHAT IT REPORTS, PER ENTITY
---------------------------
  * the name EXACTLY as stored in `documents.company` (this is the entity key —
    there is no entity_id column and no alias table)
  * how many filings, and which financial years
  * whether those filings actually have rows in `table_chunks` / `text_chunks`,
    which separates "the resolver can't find it" from "it resolves to a document
    that has no extracted content"
  * for each realistic spelling a language model might emit, whether the
    resolver RESOLVES it, calls it AMBIGUOUS, or fails to find it at all

The last one is the point. Nothing in the pipeline extracts a company name from
the user's question — the model alone fills that argument — so the set of
spellings that resolve IS the set of ways a user can successfully ask.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import bootstrap
#
# Mirrors what the gateway does at startup: load .env, then put the vendored
# FS pipeline directory on sys.path so its top-level absolute imports
# (`import tools_fs`) resolve. Done before importing tools_fs, because that
# module reads Config from the environment at import time and hard-fails
# without DB_PASSWORD.
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PIPELINE_DIR = _BACKEND_DIR / "modes" / "financial_statement" / "pipeline"

for _p in (str(_BACKEND_DIR), str(_PIPELINE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dependency of the app
    load_dotenv = None

if load_dotenv is not None:
    # Same order and same `break` as app/main.py: most specific first.
    for _candidate in (_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"):
        if _candidate.is_file():
            load_dotenv(_candidate, override=False)
            break


def _import_pipeline(baseline: bool = False):
    """Import the FS pipeline, turning its import-time failures into advice.

    By default this installs the entity-resolution overrides first, so the audit
    measures WHAT SHIPS. `adapter._load()` normally does that, but it also builds
    the whole agent and needs the LLM stack, which this script does not — so the
    one step that matters is done directly.

    `baseline=True` skips it, which is how you measure the branch's original
    behaviour for comparison.
    """
    try:
        from tools_fs import Config, Database, DocumentResolver  # type: ignore
        if not baseline:
            from modes.financial_statement import entity_resolution
            entity_resolution.install(DocumentResolver)
        return Config, Database, DocumentResolver
    except EnvironmentError as exc:
        raise SystemExit(
            f"Cannot load the Financial Statements pipeline: {exc}\n"
            f"This script reads the same .env the gateway does. Run it from the "
            f"backend/ directory with that file present."
        ) from exc
    except ImportError as exc:
        raise SystemExit(
            f"Cannot import the Financial Statements pipeline ({exc}).\n"
            f"Run this from the backend/ directory in the environment that has "
            f"requirements.txt installed."
        ) from exc


# ---------------------------------------------------------------------------
# Name variants
# ---------------------------------------------------------------------------

# Corporate suffixes a model routinely appends when it "helpfully" expands a
# short name into what it believes is the legal name. Each one lengthens the
# string, and the shipping resolver's pattern is a ONE-DIRECTIONAL substring
# match (the stored value must contain the user's string), so every one of these
# is a candidate way to turn a working question into "no annual report found".
_SUFFIXES = (
    "Limited",
    "Ltd",
    "Ltd.",
    "Corporation",
    "Corporation Limited",
    "Company Limited",
    "India Limited",
)


# Words that are never part of an acronym and never worth appending twice.
_STOPWORDS = frozenset({"and", "of", "the", "for"})


def _acronym(name: str) -> str | None:
    """Initials of a multi-word name — 'Gujarat Gas' -> 'GG'.

    Models reach for these constantly. Only emitted for 2+ significant words,
    and never for a name that is already an acronym. Joining words are skipped,
    so 'Oil and Natural Gas Corporation' gives ONGC rather than OANGC.
    """
    words = [w for w in re.split(r"[\s_]+", name)
             if len(w) > 2 and w.lower() not in _STOPWORDS]
    if len(words) < 2 or name.isupper():
        return None
    return "".join(w[0].upper() for w in words)


def variants_for(stored: str) -> list[tuple[str, str]]:
    """Realistic spellings for one stored company name, as (label, text).

    Deduplicated, and the stored form itself always comes first so the report
    can show at a glance whether even the exact stored value resolves.
    """
    spaced = re.sub(r"[_\s]+", " ", stored).strip()
    base = re.sub(r"\s+(Limited|Ltd\.?|Corporation|Company)$", "", spaced,
                  flags=re.IGNORECASE).strip()

    out: list[tuple[str, str]] = [
        ("stored form", stored),
        ("spaced", spaced),
        ("lowercase", spaced.lower()),
        ("UPPERCASE", spaced.upper()),
    ]

    # The big one: suffix expansion. This is the suspected primary cause.
    #
    # A suffix whose words the base already carries is skipped: probing
    # "Bharat Petroleum Corporation Corporation" tests nothing a model would
    # ever emit, and a noisy audit is a harder audit to act on.
    base_words = {w.lower() for w in re.split(r"[\s_]+", base)}
    for suffix in _SUFFIXES:
        if any(w.lower().rstrip(".") in base_words
               for w in suffix.split()
               if w.lower() not in _STOPWORDS):
            continue
        out.append((f"+ {suffix}", f"{base} {suffix}"))

    # 'India' is inconsistently part of these names (GAIL_India, Coal India).
    if re.search(r"\bindia\b", spaced, re.IGNORECASE):
        out.append(("without 'India'",
                    re.sub(r"\s*\bindia\b\s*", " ", spaced, flags=re.IGNORECASE).strip()))
    else:
        out.append(("+ India", f"{base} India"))

    # Ampersand conventions differ between the filing and the question.
    if "&" in spaced:
        out.append(("& -> and", spaced.replace("&", "and")))
    elif re.search(r"\band\b", spaced, re.IGNORECASE):
        out.append(("and -> &", re.sub(r"\band\b", "&", spaced, flags=re.IGNORECASE)))

    acr = _acronym(base)
    if acr:
        out.append(("acronym", acr))

    # First significant word only — how a user often refers to a company.
    first = base.split(" ")[0] if " " in base else None
    if first and len(first) > 3:
        out.append(("first word only", first))

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, text in out:
        key = text.strip().lower()
        if not text.strip() or key in seen:
            continue
        seen.add(key)
        deduped.append((label, text.strip()))
    return deduped


# ---------------------------------------------------------------------------
# Read-only queries
# ---------------------------------------------------------------------------

def _readonly_conn(Database):
    """A reports-DB connection that cannot write.

    `default_transaction_read_only` is enforced by Postgres itself, so this is a
    real guarantee rather than a promise in a docstring.
    """
    conn = Database.get_reports_connection()
    with conn.cursor() as cur:
        cur.execute("SET default_transaction_read_only = on")
    conn.commit()
    return conn


def fetch_entities(conn) -> list[dict[str, Any]]:
    """Every entity in the corpus with its filing years.

    The same GROUP BY the FDR mode's entity picker uses — `company` is the
    corpus's own entity key.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT company,
                   count(*)        AS filings,
                   min(fy_start)   AS first_fy,
                   max(fy_end)     AS last_fy,
                   array_agg(DISTINCT fy_end ORDER BY fy_end) AS years
              FROM public.documents
             WHERE company IS NOT NULL
             GROUP BY company
             ORDER BY company
            """
        )
        return [
            {"company": r[0], "filings": r[1], "first_fy": r[2],
             "last_fy": r[3], "years": list(r[4] or [])}
            for r in cur.fetchall()
        ]


def chunk_coverage(conn, company: str) -> dict[str, int]:
    """Do this entity's documents actually have extracted content?

    An entity can resolve perfectly and still answer nothing, if extraction
    produced no chunks for it. Reporting these separately is what stops that
    being misread as a resolver bug.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(DISTINCT d.doc_id),
                   coalesce(sum(t.n), 0),
                   coalesce(sum(x.n), 0)
              FROM public.documents d
              LEFT JOIN LATERAL (
                    SELECT count(*) AS n FROM public.table_chunks tc
                     WHERE tc.doc_id = d.doc_id) t ON TRUE
              LEFT JOIN LATERAL (
                    SELECT count(*) AS n FROM public.text_chunks xc
                     WHERE xc.doc_id = d.doc_id) x ON TRUE
             WHERE d.company = %s
            """,
            (company,),
        )
        row = cur.fetchone() or (0, 0, 0)
        return {"docs": row[0] or 0, "table_chunks": row[1] or 0,
                "text_chunks": row[2] or 0}


def probe(DocumentResolver, conn, text: str, fy: str = "") -> tuple[str, str]:
    """Run one spelling through the SHIPPING resolver. Returns (verdict, detail).

    A financial year is supplied by the caller because without one EVERY entity
    is legitimately ambiguous -- seven filings match and the resolver cannot pick
    between them. Scoring that as a failure measures nothing about whether the
    NAME was understood, which is the thing being audited.
    """
    try:
        match = DocumentResolver._resolve_document(text, fy, conn)
    except Exception as exc:  # noqa: BLE001 - a probe must never abort the audit
        return "ERROR", f"{type(exc).__name__}: {exc}"

    if match is None:
        return "NOT FOUND", ""
    if isinstance(match, list):
        names = sorted({m["company"] for m in match})
        if len(names) == 1:
            # Same company, several years - the resolver cannot pick one. This
            # is the "ambiguity masquerading as absence" path.
            return "AMBIGUOUS", f"{len(match)} filings of {names[0]}"
        return "AMBIGUOUS", f"{len(match)} rows across {len(names)}: {', '.join(names[:4])}"
    return "RESOLVED", match["company"]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_OK = "RESOLVED"


def audit(only: str | None = None, baseline: bool = False) -> dict[str, Any]:
    Config, Database, DocumentResolver = _import_pipeline(baseline=baseline)
    print("Resolver   :", "BRANCH ORIGINAL (--baseline)" if baseline
          else "with entity_resolution overrides installed")

    print(f"Reports DB : {Config.REPORTS_DB_HOST}:{Config.REPORTS_DB_PORT}"
          f"/{Config.REPORTS_DB_NAME} (read-only)\n")

    conn = _readonly_conn(Database)
    try:
        entities = fetch_entities(conn)
        if only:
            needle = only.lower()
            entities = [e for e in entities if needle in e["company"].lower()]
            if not entities:
                print(f"No entity in the corpus matches '{only}'.")
                return {"entities": []}

        print(f"{len(entities)} entit{'y' if len(entities) == 1 else 'ies'} "
              f"in the corpus.\n")

        results: list[dict[str, Any]] = []
        for ent in entities:
            company = ent["company"]
            coverage = chunk_coverage(conn, company)
            # A year this entity actually has, so a correct name can resolve to
            # exactly one document.
            years = ent["years"] or []
            fy = f"FY{years[-1] - 1}-{str(years[-1])[-2:]}" if years else ""

            probes = []
            for label, text in variants_for(company):
                verdict, detail = probe(DocumentResolver, conn, text, fy)
                # The audit is about NAME resolution: landing on the right
                # company is the success condition, whether that yields one
                # document or a choice of years within it. Landing on a
                # DIFFERENT company is the serious failure and is called out
                # separately.
                if verdict == "AMBIGUOUS" and company in detail:
                    verdict = _OK
                elif verdict == _OK and detail and detail != company:
                    verdict = "WRONG ENTITY"
                probes.append({"label": label, "text": text,
                               "verdict": verdict, "detail": detail})

            resolved = sum(1 for p in probes if p["verdict"] == _OK)
            results.append({
                **ent,
                "coverage": coverage,
                "probes": probes,
                "resolved_forms": resolved,
                "total_forms": len(probes),
                "healthy": resolved == len(probes) and coverage["table_chunks"] > 0,
            })
        return {"entities": results}
    finally:
        conn.close()


def render(report: dict[str, Any], verbose: bool) -> None:
    entities = report["entities"]
    if not entities:
        return

    print("=" * 100)
    print(f"{'STORED COMPANY':<42} {'FILINGS':>7} {'YEARS':>14} "
          f"{'CHUNKS(tbl/txt)':>17} {'FORMS OK':>9}")
    print("=" * 100)

    broken: list[dict[str, Any]] = []
    for e in entities:
        years = e["years"]
        yr = f"{min(years)}-{max(years)}" if years else "-"
        cov = e["coverage"]
        flag = "" if e["healthy"] else "  <-- CHECK"
        if not e["healthy"]:
            broken.append(e)
        print(f"{e['company'][:42]:<42} {e['filings']:>7} {yr:>14} "
              f"{cov['table_chunks']:>8}/{cov['text_chunks']:<8} "
              f"{e['resolved_forms']:>4}/{e['total_forms']:<4}{flag}")

    print("=" * 100)

    # The failing-spelling breakdown. This is the actionable half: it names the
    # exact transformations that break resolution, across the whole corpus.
    failures: Counter = Counter()
    for e in entities:
        for p in e["probes"]:
            if p["verdict"] != _OK:
                failures[(p["label"], p["verdict"])] += 1

    if failures:
        print("\nSPELLINGS THAT DO NOT RESOLVE (across all entities)")
        print("-" * 100)
        for (label, verdict), n in failures.most_common():
            print(f"  {label:<26} {verdict:<12} {n:>4} of {len(entities)} entities")

    if broken:
        print(f"\n{len(broken)} ENTITY/ENTITIES NEED ATTENTION")
        print("-" * 100)
        for e in broken:
            cov = e["coverage"]
            print(f"\n  {e['company']}")
            if cov["table_chunks"] == 0:
                print("    ! No table_chunks rows — this entity's filings have no "
                      "extracted tables, so it would answer nothing even once it resolves.")
            bad = [p for p in e["probes"] if p["verdict"] != _OK]
            for p in bad[: (None if verbose else 8)]:
                detail = f"  ({p['detail']})" if p["detail"] else ""
                print(f"    {p['verdict']:<11} {p['label']:<22} {p['text']!r}{detail}")
            if not verbose and len(bad) > 8:
                print(f"    ... and {len(bad) - 8} more (use --verbose)")

    healthy = len(entities) - len(broken)
    print(f"\nSUMMARY: {healthy}/{len(entities)} entities resolve from every "
          f"spelling tried and have extracted content.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Audit which entities the Financial Statements mode can find.")
    ap.add_argument("--json", metavar="PATH",
                    help="also write the full result as JSON, for driving the fix")
    ap.add_argument("--only", metavar="SUBSTRING",
                    help="restrict to entities whose stored name contains this")
    ap.add_argument("--baseline", action="store_true",
                    help="measure the branch's original resolver instead")
    ap.add_argument("--verbose", action="store_true",
                    help="list every failing spelling, not just the first 8")
    args = ap.parse_args(argv)

    report = audit(only=args.only, baseline=args.baseline)
    render(report, verbose=args.verbose)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str),
                                   encoding="utf-8")
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
