"""Find the entity the user meant, in the form the corpus actually stores it.

THE BUG THIS EXISTS TO FIX
--------------------------
`documents.company` is a raw string and IS the entity key — there is no
`entity_id` column and no alias table. Nothing in the pipeline extracts a
company name from the question either: the model alone fills that argument. So
the set of spellings that resolve is precisely the set of ways a user can
successfully ask.

The branch's resolver matched with a ONE-DIRECTIONAL substring:

    WHERE lower(replace(company,'_',' ')) ILIKE lower(replace('%' || :q || '%', '_',' '))

The stored value had to CONTAIN the user's string. `Gujarat_Gas` therefore
matched "Gujarat Gas" but NOT "Gujarat Gas Limited" — and sixteen tool-parameter
descriptions used to say `e.g. 'Coal India', 'ONGC', 'BPCL'`, which taught the
model to expand short names into exactly the legal forms that then failed. The
two faults compounded: the prompt manufactured the input the matcher rejected.

WHY THIS IS AN OVERRIDE RATHER THAN AN EDIT
-------------------------------------------
`_resolve_document`, `format_ambiguous` and `latest_fy_end` are staticmethods
reached as `DocumentResolver.X(...)` from ~17 call sites across the pipeline.
Replacing the three class attributes at import time fixes every call site at
once while leaving `pipeline/` byte-for-byte re-pullable from origin — the same
technique `adapter._normalise_embedding_url()` already uses on `Config`, and
that `agent.py` already uses on `_format_tool_result`.

`install()` verifies the attributes exist and still take the parameters we
expect before replacing them, so a re-pull that renames or re-signatures
anything fails loudly at load instead of silently reverting to the old bug.

THE LADDER, AND THE RULE THAT MAKES IT SAFE
--------------------------------------------
Stages run in order and the first that yields anything wins:

    1 exact          normalised equality
    2 containment    either direction, on whole words
    3 token subset   either direction
    4 acronym        'ONGC' <-> 'Oil and Natural Gas Corporation'
    5 fuzzy          difflib, only on a clear winner
    6 suggestion     near misses, returned as candidates rather than silence

**A stage that matches more than one distinct company NEVER picks one.** It
returns the candidate list, and the caller renders it as a question. Answering
one company's figures under another's name is the one failure this module must
never produce, and it is worse than answering nothing.

`difflib` rather than `pg_trgm`: the extension may not be installed on this
database, and `CREATE EXTENSION` is DDL we are not authorised to run. The
candidate set is a few hundred rows, so stdlib is ample.

STAGE 6 IS LOad-BEARING, NOT A NICETY
--------------------------------------
Fifteen call sites write `"No annual report found for company '{company}'."` as
a literal, and that string is what the model paraphrases into the "no relevant
data is available for this entity" the user reported. Those literals cannot be
reached by an override. But `format_ambiguous` CAN be — so whenever there is any
plausible near miss, stage 6 returns a list, control moves to our message, and
we get to say what actually happened. A name with no relationship to the corpus
still returns None and still gets the honest not-found.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Dropped before comparison. These are the words that differ between how a
# filing names itself and how a person asks about it, and carry no identity:
# every one of them can be added or removed without changing which company is
# meant. `india` is deliberately ABSENT — "Coal India" and "GAIL India" are not
# the same entities as "Coal" and "GAIL", and dropping it would merge them.
_NOISE = frozenset({
    "limited", "ltd", "pvt", "private", "plc", "inc", "incorporated",
    "corporation", "corp", "company", "co", "the", "and", "of",
})

# Never part of an acronym.
_JOINERS = frozenset({"and", "of", "the", "for"})

# Names with no derivable relationship to how the corpus stores them.
#
# Everything else in this module is derived: suffixes are stripped, initials are
# computed, near-spellings are measured. These cannot be. "Bharat Petroleum" and
# "BPCL" share almost no characters, and no amount of string cleverness turns one
# into the other -- it is knowledge about Indian public-sector companies, so it
# is written down as knowledge rather than smuggled into a similarity threshold
# loose enough to also match the wrong company.
#
# The value is a QUERY, not a stored name: it is fed back through the same ladder
# rather than pointing at a row. So this stays correct if the corpus renames or
# re-spells an entity, and an alias for a company that is not held simply finds
# nothing.
#
# Keys are already normalised (lowercase, no suffix, no punctuation). Extend it
# from what `python -m scripts.audit_fs_entities` reports as unresolved.
ALIASES: dict[str, str] = {
    "bharat petroleum": "bpcl",
    "hindustan petroleum": "hpcl",
    "indian oil": "iocl",
    "indianoil": "iocl",
    "chennai petroleum": "cpcl",
    "ongc videsh": "ovl",
    "national aluminium": "nalco",
    "national thermal power": "ntpc",
    "national hydroelectric power": "nhpc",
    "power finance": "pfc",
    "rural electrification": "rec",
    "power grid": "pgcil",
    "power grid india": "pgcil",
    "housing and urban development": "hudco",
    "rail vikas nigam": "rvnl",
    "indian railway finance": "irfc",
    "indian railway catering and tourism": "irctc",
    "indian renewable energy development agency": "ireda",
    "india infrastructure finance": "iifcl",
    "neyveli lignite": "nlc",
    "rashtriya ispat nigam": "rinl",
    "steel authority": "sail",
    "steel authority india": "sail",
}


@dataclass(frozen=True)
class Norm:
    """A company name reduced to its comparable parts.

    Two acronyms, because real ones disagree about whether the corporate suffix
    counts. ONGC and BPCL take their last letter from "Corporation"/"Limited" —
    the very words `_NOISE` strips — while a name like "Gujarat Gas" has no
    suffix to contribute. Carrying both and accepting either is what lets the
    acronym stage match real usage instead of a tidy rule.
    """
    raw: str
    norm: str
    tokens: tuple[str, ...]
    acronym: str        # from significant tokens AFTER noise removal  -> "gg"
    acronym_full: str   # from every word except joiners               -> "ongc"

    @property
    def acronyms(self) -> set[str]:
        return {a for a in (self.acronym, self.acronym_full) if a}


def normalise(name: str) -> Norm:
    """Reduce a company name to a comparable core.

    Fixed order, and each step exists for a spelling seen in the wild:
      NFKD + strip accents      unicode variants of the same letter
      lowercase                 case is never identity here
      non-alphanumeric -> space `_`, `-`, `&`, `.`, `,` all appear as separators
      drop noise tokens         'Limited', 'Ltd.', 'Corporation', 'The'
      collapse whitespace

    The noise filter never removes the LAST remaining token, so a name that is
    entirely noise ("The Company Ltd") normalises to something rather than to
    the empty string, which would match everything.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    words = [w for w in text.split() if w]

    kept = [w for w in words if w not in _NOISE]
    if not kept:
        kept = words[-1:] if words else []

    tokens = tuple(kept)
    significant = [w for w in tokens if len(w) > 2 and w not in _JOINERS]
    acronym = "".join(w[0] for w in significant) if len(significant) > 1 else ""

    # The suffix-inclusive form: every word except pure joiners, so
    # "Oil and Natural Gas Corporation" yields "ongc" and
    # "Bharat Petroleum Corporation Limited" yields "bpcl".
    full_words = [w for w in words if w not in _JOINERS]
    acronym_full = ("".join(w[0] for w in full_words)
                    if len(full_words) > 1 else "")

    return Norm(raw=str(name or ""), norm=" ".join(tokens), tokens=tokens,
                acronym=acronym, acronym_full=acronym_full)


def _contains_words(haystack: str, needle: str) -> bool:
    """Whole-word containment. Prevents 'gas' matching 'gasket'."""
    if not needle or not haystack:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


# ---------------------------------------------------------------------------
# Candidate cache
# ---------------------------------------------------------------------------

_CACHE_TTL = float(os.environ.get("ARTHA_FS_ENTITY_CACHE_TTL", "300") or 300)

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "rows": None}


def _all_documents(conn) -> list[dict]:
    """Every document row, cached briefly.

    One scan replaces the per-call ILIKE. The table is small (a few hundred
    rows) and ingestion happens out of band, so a short TTL costs nothing and
    lets a fresh ingest appear without restarting the gateway. Set
    ARTHA_FS_ENTITY_CACHE_TTL=0 to disable caching entirely.
    """
    now = time.monotonic()
    if _CACHE_TTL > 0:
        with _cache_lock:
            rows = _cache["rows"]
            if rows is not None and (now - _cache["at"]) < _CACHE_TTL:
                return rows

    import psycopg2.extras  # type: ignore

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT doc_id, company, fy_start, fy_end, doc_name "
                "FROM public.documents "
                "WHERE company IS NOT NULL "
                "ORDER BY company, fy_start"
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        # Never poison the cache with a failure, and leave the connection
        # usable — the branch's own handlers do the same.
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    if _CACHE_TTL > 0:
        with _cache_lock:
            _cache["rows"] = rows
            _cache["at"] = time.monotonic()
    return rows


def invalidate_cache() -> None:
    """Drop the cached document list — for tests and after a known ingest."""
    with _cache_lock:
        _cache["rows"] = None
        _cache["at"] = 0.0


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

# A fuzzy match is accepted only on a clear winner: this similarity AND this
# much daylight over the runner-up. Both are deliberately conservative —
# answering the wrong entity is far worse than asking which one was meant.
_FUZZY_ACCEPT = 0.86
_FUZZY_MARGIN = 0.08
# Anything this close is worth offering as a suggestion rather than discarding.
_FUZZY_SUGGEST = 0.60


def match_company(query: str, rows: list[dict]) -> tuple[list[dict], str]:
    """Rows whose company is what `query` meant, plus the stage that decided.

    Returns ([], "none") when nothing is even close. Never narrows to one
    company when several are plausible — that judgement belongs to the user.
    """
    q = normalise(query)
    if not q.norm:
        return [], "empty"

    # A known alias is rewritten to the form the corpus recognises, then matched
    # by the ordinary ladder -- so an alias never becomes a special case with its
    # own way of being right or wrong.
    aliased = ALIASES.get(q.norm)
    if aliased:
        q = normalise(aliased)

    index = [(normalise(r["company"]), r) for r in rows]

    def _by(pred) -> list[dict]:
        return [r for n, r in index if pred(n)]

    stages = (
        ("exact", lambda n: n.norm == q.norm),
        ("containment", lambda n: (_contains_words(n.norm, q.norm)
                                   or _contains_words(q.norm, n.norm))),
        ("token-subset", lambda n: bool(n.tokens) and (
            set(q.tokens) <= set(n.tokens) or set(n.tokens) <= set(q.tokens))),
        # An acronym only ever matches a SPELLED-OUT name, never another
        # acronym-shaped string.
        #
        # Comparing two multi-word names by their initials was wrong, and
        # measurably so on this corpus: "Gujarat Gas" and "GAIL Gas" both reduce
        # to "gg", so a plain request for one came back ambiguous against the
        # other. Initials collide constantly and carry far too little
        # information to identify a company on their own.
        #
        # So exactly one side may be the acronym, and it has to be the side that
        # is a single token -- which is what an acronym actually looks like:
        #   "ONGC"                            -> "Oil and Natural Gas Corporation"
        #   "Oil and Natural Gas Corporation" -> "ONGC"
        ("acronym", lambda n: (
            (len(q.tokens) == 1 and q.norm.replace(" ", "") in n.acronyms)
            or (len(n.tokens) == 1 and n.norm.replace(" ", "") in q.acronyms)
        )),
    )

    for label, pred in stages:
        hits = _by(pred)
        if hits:
            return hits, label

    # Stage 5 — fuzzy, only on a clear winner.
    scored = [(SequenceMatcher(None, q.norm, n.norm).ratio(), n, r)
              for n, r in index]
    scored.sort(key=lambda t: t[0], reverse=True)
    if scored:
        best = scored[0][0]
        by_company: dict[str, float] = {}
        for score, _n, r in scored:
            by_company.setdefault(r["company"], score)
        ranked = sorted(by_company.items(), key=lambda kv: kv[1], reverse=True)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

        if best >= _FUZZY_ACCEPT and (best - runner_up) >= _FUZZY_MARGIN:
            winner = ranked[0][0]
            return [r for _s, _n, r in scored if r["company"] == winner], "fuzzy"

        # Stage 6 — suggestions. Returning candidates routes the caller into
        # format_ambiguous (which we control) instead of the fifteen literal
        # "No annual report found" strings (which we cannot reach).
        near = [c for c, s in ranked if s >= _FUZZY_SUGGEST]
        if near:
            return ([r for _s, _n, r in scored if r["company"] in set(near)],
                    "suggestion")

    return [], "none"


# ---------------------------------------------------------------------------
# The three replacements
# ---------------------------------------------------------------------------

def _fy_label(row: dict) -> str:
    return f"FY{row['fy_start']}-{str(row['fy_end'])[-2:]}"


def _years_of(rows: list[dict]) -> list[str]:
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: (x["fy_start"] or 0, x["fy_end"] or 0)):
        label = _fy_label(r)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def resolve_document(company: str, financial_year: str, conn):
    """Replacement for `DocumentResolver._resolve_document`.

    Same contract as the branch's: a dict for one unambiguous match, a list of
    candidate rows when the caller must ask, None when the company is genuinely
    not in the corpus.

    The one behavioural change that matters: when the company resolves but the
    requested YEAR is not ingested, this returns the candidate list rather than
    silently dropping the year filter. The branch's `if exact:` kept every year
    and let the caller render "multiple reports matched", which the model then
    paraphrased as "no data for this company" — reporting a missing year as a
    missing company.
    """
    if not company or not str(company).strip():
        return None

    try:
        rows = _all_documents(conn)
    except Exception:
        return None

    matched, stage = match_company(str(company), rows)
    if not matched:
        return None

    # A suggestion is never a match — hand the candidates back to be asked about.
    if stage == "suggestion":
        return matched

    companies = {r["company"] for r in matched}
    if len(companies) > 1:
        return matched

    from_resolver = _PARSE_FY(financial_year or "")
    fy_start, fy_end = from_resolver

    if fy_start is not None and fy_end is not None and fy_start != fy_end:
        exact = [r for r in matched
                 if r["fy_start"] == fy_start and r["fy_end"] == fy_end]
    elif fy_end is not None:
        exact = [r for r in matched
                 if r["fy_start"] == fy_end or r["fy_end"] == fy_end]
    else:
        exact = matched

    if exact:
        matched = exact
    elif fy_end is not None:
        # Company present, year absent. Returning the list routes into
        # format_ambiguous, which says exactly that.
        return matched

    if len(matched) == 1:
        return matched[0]
    return matched


def format_ambiguous(company: str, label: str, matches: list[dict],
                     ask: str = "the exact company name") -> str:
    """Replacement for `DocumentResolver.format_ambiguous`.

    This is where the user-visible wording for every "could not pin it down"
    path is decided, and the reason stage 6 returns candidates at all. Each
    branch tells the model something different and actionable, and the
    single-company branches state outright that the company IS present — because
    the failure being fixed is the model reporting a missing year, or an
    imprecise name, as an absent company.
    """
    if not matches:
        return (f"No company matching '{company}' is in the corpus. "
                f"Report this as a coverage gap, not as a company that "
                f"disclosed nothing.")

    companies = sorted({r["company"] for r in matches})
    years = _years_of(matches)

    if len(companies) == 1:
        stored = companies[0]
        available = ", ".join(years)
        if label and label.strip():
            return (
                f"'{company}' resolves to {stored}, which IS present in the "
                f"corpus — but financial year '{label}' has not been ingested "
                f"for it. Available: {available}. Ask again naming one of those "
                f"years. Do NOT report that no data exists for this company: "
                f"the company is present and only that year is missing."
            )
        return (
            f"'{company}' resolves to {stored}, which IS present in the corpus "
            f"with {available}. Specify which financial year you want. Do NOT "
            f"report that no data exists for this company."
        )

    options = "; ".join(
        f"{r['company']} {_fy_label(r)} ({r['doc_id']})" for r in matches[:10]
    )
    names = ", ".join(companies[:8])
    return (
        f"'{company}'{f' / {label}' if label else ''} matched several companies: "
        f"{options}. The exact stored names are: {names}. Ask again using one of "
        f"those names verbatim, and specify {ask}."
    )


def latest_fy_end(company: str, conn) -> int | None:
    """Replacement for `DocumentResolver.latest_fy_end`.

    Routed through the same ladder rather than repeating the raw ILIKE. Without
    this the multi-year trend window keeps failing on exactly the spellings the
    resolver now handles — the branch has this query written out a second time.
    """
    if not company or not str(company).strip():
        return None
    try:
        rows = _all_documents(conn)
    except Exception:
        return None

    matched, stage = match_company(str(company), rows)
    if not matched or stage == "suggestion":
        return None
    if len({r["company"] for r in matched}) > 1:
        return None

    ends = [r["fy_end"] for r in matched if r.get("fy_end") is not None]
    return max(ends) if ends else None


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

# Bound at install time to the branch's own FY parser, so the year semantics
# stay whatever the branch says they are rather than being re-derived here.
_PARSE_FY = None

_EXPECTED = {
    "_resolve_document": ("company", "financial_year", "conn"),
    "format_ambiguous": ("company", "label", "matches", "ask"),
    "latest_fy_end": ("company", "conn"),
    "_parse_fy": ("financial_year",),
}


class ResolverContractError(RuntimeError):
    """The vendored resolver is not the shape this override was written for."""


def install(document_resolver) -> None:
    """Replace the three staticmethods on the branch's DocumentResolver.

    Verifies the contract first. If a re-pull renamed a method or changed its
    parameters, this raises and the mode reports itself unavailable with that
    reason — which is the point. Silently declining to install would leave the
    original bug in place while every test and every log line said the fix had
    shipped.
    """
    global _PARSE_FY

    for name, params in _EXPECTED.items():
        fn = getattr(document_resolver, name, None)
        if fn is None:
            raise ResolverContractError(
                f"DocumentResolver.{name} is missing. The vendored "
                f"Financial_Statement pipeline has changed shape; "
                f"modes/financial_statement/entity_resolution.py must be "
                f"updated to match before it can be installed."
            )
        actual = tuple(inspect.signature(fn).parameters)
        if actual[:len(params)] != params:
            raise ResolverContractError(
                f"DocumentResolver.{name}{actual} no longer takes {params}. "
                f"Update modes/financial_statement/entity_resolution.py."
            )

    _PARSE_FY = document_resolver._parse_fy

    document_resolver._resolve_document = staticmethod(resolve_document)
    document_resolver.format_ambiguous = staticmethod(format_ambiguous)
    document_resolver.latest_fy_end = staticmethod(latest_fy_end)

    logger.info(
        "FS entity resolution installed: _resolve_document, format_ambiguous "
        "and latest_fy_end now use the normalised match ladder "
        "(cache TTL %ss).", int(_CACHE_TTL),
    )
