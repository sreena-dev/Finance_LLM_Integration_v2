"""
Canonical-input BINDING — the production hybrid resolver (label -> canonical FS line).

Design (proven out on real ONGC data; see the lexical-vs-semantic experiment):
  Layer 0  STRUCTURE   section-tag every row (assets / equity / liabilities, current
                       vs non-current) so a concept is only searched in its own block.
  Layer 1  RULES       anchored patterns + row role. Deterministic, instant, the
                       decision AUTHORITY. Provenance = "rule" (trusted by construction).
  Layer 2  SEMANTIC    OPTIONAL, section-scoped, PROPOSE-ONLY fallback for labels the
                       rules miss (injected `embed_fn`; skipped if none). Provenance =
                       "semantic" — NOT trusted until an arithmetic tie-out confirms it.
  Layer 3  ARITHMETIC  deterministic tie-outs (assets = CA+NCA, income = rev+other, …)
                       validate bindings. A semantic bind that cannot be tied out stays
                       untrusted -> the input ABSTAINS. This is the real safety net,
                       not any matcher's confidence.

The LLM is never here. Nothing is imported from `rag/` — the embedder is dependency-
injected, so `fs_db` stays self-contained (finance-core rule).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Callable

from .config import ARITH_ABS_TOL, ARITH_REL_TOL
from .models import ParsedTable
from . import llm_bind as LB

EmbedFn = Callable[[list[str]], "object"]      # list[str] -> (n, d) float array

# "(a)","(ii)","1)" — and the CPSU arithmetic markers "(+)"/"(-)", which label bridge and
# movement lines ("(+) Additions", "(-) Payment during year"). Every LineSpec pattern is
# `^`-anchored against `_strip`'s output, so a marker left in place makes the whole rule
# layer miss the row — it is not a hierarchy problem (an unrecognised marker already falls
# through `_levels`' unmarked branch and lands at the right depth), it is a matching one.
_PREFIX = re.compile(r"^[\(\[]?\s*(?:[a-z0-9]{1,3}|[+-])\s*[\)\].]\s*", re.I)

# A section header may print its ordinal with NO closing delimiter at all — MRPL heads
# its blocks "I Non-Current Assets", "II Current Assets", "III Current Liabilities",
# "I Equity". `_PREFIX` requires a ")", "]" or "." and so leaves those untouched, the
# section rules never match, and the whole statement stays in `assets_root`: 0 of 20
# balance-sheet keys bound on a filing that parses perfectly. The giveaway is that the
# ONE head MRPL prints without an ordinal ("Non-Current Liabilities") tagged correctly.
#
# This is deliberately NOT folded into `_strip`. Stripping a bare leading token from
# every label is unsafe: a real caption can open with a short word ("Oil and Gas
# Assets"), and three of the roman characters spell ordinary words (MIX, DIM, LID),
# so a global rule would silently eat the first word of a line item. Confined to
# section detection, the worst case is that a header fails to match a section rule —
# exactly the state we are already in — and `_tag_sections` tries the plain form first
# regardless.
_BARE_ORDINAL = re.compile(r"^(?:[ivxl]{1,5}|[a-z]|\d{1,2})\s+(?=\S)", re.I)


def _strip(lab: str) -> str:
    return _PREFIX.sub("", (lab or "").strip().lower())


def _strip_bare_ordinal(lab: str) -> str:
    """`_strip` output with a delimiter-less leading ordinal also removed."""
    return _BARE_ORDINAL.sub("", lab, count=1)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= ARITH_ABS_TOL + ARITH_REL_TOL * max(abs(a), abs(b))


# --------------------------------------------------------------------------- row hierarchy
# Schedule III statements are TREES printed flat: "(a) Property, Plant and Equipment"
# carries no figure of its own — its children ((i) Oil and Gas Assets > (a) Tangible /
# (b) Intangible, (ii) Other PPE, (iii) Right-of-use assets) do. A flat label matcher
# therefore binds nothing for PPE, or worse binds a single sub-line as if it were the
# whole net block. Same for "(iii) Trade payables", whose only figures are the MSME /
# non-MSME split beneath it. We reconstruct the nesting from the ordinal markers, then
# a parent's figure is the sum of its top-most valued descendants.
#
# Depth comes from a stack, NOT from the marker glyph: "(a)" appears at two different
# levels in the same ONGC statement, and "(c)"/"(d)" are ambiguous between alpha and
# roman. A row descends a level unless its marker is the SUCCESSOR of an open level's
# last marker under some shared reading — which resolves every such ambiguity by
# sequence rather than by guessing the glyph's type.
_MARKER_RE = re.compile(r"^\s*[\(\[]?\s*([0-9]{1,3}|[a-zA-Z]{1,5})\s*[\)\].]")
_BULLET_RE = re.compile(r"^\s*[-–—•*]\s+")
_ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.I)
# Schedule III amendments insert items WITHOUT renumbering, as a lettered sub-ordinal:
# Lease Liabilities is "(ia)", a SIBLING of "(i) Borrowings", not a child of it. Read
# naively that row nests one level too deep and drops out of its section's sum — which
# is exactly the 1,198.84 by which BPCL's current liabilities failed to tie out.
# The suffix letter must NOT itself be a roman character, or "ii" splits as i+i, "iv" as
# i+v, and every roman numeral is read as an inserted sibling instead of the next item.
_ROMAN_SUFFIX_RE = re.compile(r"^([ivxlcdm]+)([abefghjknopqrstuwyz])$", re.I)
_ROMAN_VALS = [(1000,"m"),(900,"cm"),(500,"d"),(400,"cd"),(100,"c"),(90,"xc"),
               (50,"l"),(40,"xl"),(10,"x"),(9,"ix"),(5,"v"),(4,"iv"),(1,"i")]


def _roman_to_int(s: str) -> int | None:
    m, prev, total = {"i":1,"v":5,"x":10,"l":50,"c":100,"d":500,"m":1000}, 0, 0
    for ch in reversed(s.lower()):
        if ch not in m:
            return None
        v = m[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _int_to_roman(n: int) -> str:
    out = ""
    for v, sym in _ROMAN_VALS:
        while n >= v:
            out += sym
            n -= v
    return out


def _marker(label: str) -> tuple[str, frozenset]:
    """(token, candidate shapes) for a row label. A token may be readable as more than
    one shape ('c' is both the letter c and roman 100) — we keep both and let the
    successor test pick."""
    if _BULLET_RE.match(label or ""):
        return "", frozenset({"bullet"})
    m = _MARKER_RE.match(label or "")
    if not m:
        return "", frozenset()
    tok = m.group(1)
    shapes = set()
    if tok.isdigit():
        shapes.add("digit")
    else:
        if len(tok) == 1 and tok.isalpha():
            shapes.add("alpha")
        if _ROMAN_RE.match(tok) and _roman_to_int(tok):
            shapes.add("roman")
        elif _split_roman(tok)[1]:                 # "(ia)" — an inserted sibling
            shapes.add("roman")
    return tok.lower(), frozenset(shapes)


def _split_roman(t: str) -> tuple[str, str]:
    """'ia' -> ('i', 'a'); 'ii' -> ('ii', ''). The suffix marks an item inserted after
    the base one by a later Schedule III amendment, at the SAME level."""
    m = _ROMAN_SUFFIX_RE.match(t or "")
    if m and _roman_to_int(m.group(1)):
        return m.group(1).lower(), m.group(2).lower()
    return (t or "").lower(), ""


def _is_successor(shape: str, prev: str, tok: str) -> bool:
    if shape == "digit":
        return prev.isdigit() and tok.isdigit() and int(tok) == int(prev) + 1
    if shape == "alpha":
        return len(prev) == 1 and len(tok) == 1 and ord(tok) == ord(prev) + 1
    if shape == "roman":
        pb, ps = _split_roman(prev)
        tb, ts = _split_roman(tok)
        a, b = _roman_to_int(pb), _roman_to_int(tb)
        if not (a and b):
            return False
        if ts:                       # "(ia)" follows "(i)";  "(ib)" follows "(ia)"
            return tb == pb and ((not ps and ts == "a") or (ps and ord(ts) == ord(ps) + 1))
        if ps:                       # "(ii)" follows "(ia)" — back to the plain sequence
            return b == a + 1
        return b == a + 1 and _int_to_roman(b) == tb
    return False


def _levels(pt: ParsedTable) -> dict[int, int]:
    """idx -> nesting depth. A `total`/`sum` row closes the group it totals, so the
    stack resets there and the next block starts clean."""
    stack: list[tuple[str, frozenset]] = []      # (last token, its candidate shapes)
    out: dict[int, int] = {}
    prev_hdr_depth: int | None = None            # depth of a preceding value-less header
    for r in pt.rows:
        if r.role in ("total", "sum"):
            out[r.idx] = 0
            stack = []
            prev_hdr_depth = None
            continue
        tok, shapes = _marker(r.label or "")
        if not shapes:                            # unmarked line — a child of what's open
            out[r.idx] = len(stack)
            prev_hdr_depth = out[r.idx] if not r.has_values() else None
            continue
        # "(i)" straight after "(h)" is genuinely ambiguous — alpha i continuing the a..h
        # run, or roman i opening a nested list. When the row before it is a VALUE-LESS
        # header the answer is settled: a header with no figure of its own exists only to
        # introduce children, so read the ambiguous marker as the first of them. Applied
        # ONLY to ambiguous tokens, so an unambiguous "(vi)" after "(v)" stays a sibling.
        # ONGC consolidated: without this, "(i) Other Investments" ate the alpha slot and
        # the real "(i)/(j)/(k)" heads nested one level too deep, dropping 261,252.35 from
        # non-current assets.
        min_k = prev_hdr_depth + 1 if (prev_hdr_depth is not None and len(shapes) > 1) else 0
        if min_k and len(stack) > min_k:
            del stack[min_k:]
        hit = None
        for k in range(len(stack) - 1, min_k - 1, -1):   # deepest open level first
            prev_tok, prev_shapes = stack[k]
            common = shapes & prev_shapes
            if "bullet" in common:                # bullets have no ordinal: siblings
                hit = k
                break
            if any(_is_successor(s, prev_tok, tok) for s in common):
                hit = k
                break
        if hit is None:
            stack.append((tok, shapes))
            out[r.idx] = len(stack) - 1
        else:
            del stack[hit + 1:]
            stack[hit] = (tok, shapes)
            out[r.idx] = hit
        prev_hdr_depth = out[r.idx] if not r.has_values() else None
    return out


def _topmost_valued(rows, levels, period, start: int | None = None,
                    max_depth: int | None = None) -> list:
    """Walk `rows` taking each row that HAS a value and skipping everything nested
    beneath it — so a printed sub-total is used instead of (never as well as) its own
    children, and a value-less header is transparently descended through."""
    out, skip = [], None
    for r in rows:
        d = levels.get(r.idx, 0)
        if max_depth is not None and d <= max_depth and start is not None and r.idx != start:
            break
        if skip is not None and d <= skip:
            skip = None
        if skip is not None:
            continue
        if r.values.get(period) is not None:
            out.append(r)
            skip = d
    return out


def _subtree_sum(pt: ParsedTable, levels: dict[int, int], row, period: str) -> float | None:
    """Sum of a value-less parent's top-most valued descendants (None if it has none)."""
    i = next((n for n, r in enumerate(pt.rows) if r.idx == row.idx), None)
    if i is None:
        return None
    depth = levels.get(row.idx, 0)
    kids = []
    for r in pt.rows[i + 1:]:
        if r.role in ("total", "sum") or levels.get(r.idx, 0) <= depth:
            break
        kids.append(r)
    vals = _topmost_valued(kids, levels, period)
    return sum(r.values[period] for r in vals) if vals else None


# --------------------------------------------------------------------------- diagnosis
# WHY REJECTION REASONS EXIST
# ---------------------------
# A bind must pass four INDEPENDENT and-conditions — section tag, pattern, role, non-null
# value — and `_resolve` used to collapse all four into a single `None`. The system could
# not say WHICH gate rejected a key, so every coverage investigation had to reconstruct the
# cause by reading regexes, and three successive audits reached three different conclusions
# about the same six keys. A closed reason enum turns caption work from hypothesis into a
# `GROUP BY reason` query.
#
# These are the extraction-layer (`EXT_*`) codes of the reason-code registry.
NO_SECTION_TAGGED        = "NO_SECTION_TAGGED"        # the section never appeared at all
SECTION_EMPTY            = "SECTION_EMPTY"            # section tagged but held no valued rows
PATTERN_NO_MATCH         = "PATTERN_NO_MATCH"         # rows in scope, no pattern hit
ROLE_MISMATCH            = "ROLE_MISMATCH"            # pattern hit, role rejected it
VALUE_NULL               = "VALUE_NULL"               # matched a value-less parent
HIERARCHY_UNRESOLVED     = "HIERARCHY_UNRESOLVED"     # subtree sum returned None
SEMANTIC_BELOW_THRESHOLD = "SEMANTIC_BELOW_THRESHOLD"
AMBIGUOUS_UNSCOPED       = "AMBIGUOUS_UNSCOPED"       # >1 unscoped match; refused to guess
SECTION_CRITICAL         = "SECTION_CRITICAL"         # sibling shares this pattern (see below)
NO_SPEC                  = "NO_SPEC"                  # key has no LineSpec — registry gap
EXT_SECTION_INCOMPLETE   = "EXT_SECTION_INCOMPLETE"   # statement-level tagging failure

BOUND, REJECTED = "BOUND", "REJECTED"


@dataclass(frozen=True)
class BindAttempt:
    """One spec's outcome, with the gate that rejected it. Emitted for EVERY spec on the
    statement, bound or not — a report that only lists successes cannot be diffed."""
    key: str
    outcome: str                 # BOUND | REJECTED
    reason: str | None = None    # one of the codes above; None when BOUND
    detail: str = ""             # for PATTERN_NO_MATCH: the labels that WERE in scope
    provenance: str = ""         # how it bound, when it did


# Specs distinguished from a sibling ONLY by their section. `^borrowings\b` is claimed by
# both long_term_borrowings and short_term_borrowings; `^lease liabilit` by both lease
# liability keys. For these four, a section-tagging failure is the one place in this file
# where the unscoped fallback below would produce a WRONG NUMBER rather than a miss — the
# current figure bound to the non-current key, feeding S15 and the whole funding cluster.
# So they never fall back: no section, no bind.
def _section_critical() -> frozenset:
    seen: dict[tuple, list] = {}
    for s in REGISTRY:
        for p in s.patterns:
            seen.setdefault((s.statement, p), []).append(s.key)
    out: set = set()
    for keys in seen.values():
        if len(keys) > 1:
            out.update(keys)
    return frozenset(out)


# --------------------------------------------------------------------------- trust sets
# Provenances that are PROPOSALS rather than decisions: each must be confirmed by an
# arithmetic tie-out before its figure may be used. Named once because the distinction is
# consulted in three places -- `BoundLine.trusted` and twice inside `Resolver._validate`
# -- and a provenance added to two of the three is a SILENT trust leak, not a visible
# error: the figure simply starts being believed, with nothing in any report to show it.
_NEEDS_PROOF = frozenset({"semantic", "rule_unscoped", "derived", "llm"})

# Of those, the ones that are GUESSES rather than exact arithmetic. A guess may not
# quietly fill an optional key in the very identity that is supposed to judge it, and when
# an identity fails a guess is blamed before a rule match is. `derived` is deliberately
# absent from this second set: it is arithmetic over other bound lines, wrong only if its
# inputs were, so it is never the weak link in a failed check.
_GUESSED = frozenset({"semantic", "rule_unscoped", "llm"})


# --------------------------------------------------------------------------- result types
@dataclass
class BoundLine:
    key: str
    value: float | None          # current period
    prior: float | None          # comparative period (for averages)
    label: str
    period: str | None
    section: str | None
    provenance: str              # rule | rule_unscoped | derived | semantic
    confidence: float
    table_id: str | None = None
    page: int | None = None
    validated: bool = False      # confirmed by an arithmetic tie-out
    contradicted: bool = False   # a tie-out it takes part in FAILED
    detail: str = ""             # how a derived figure was built
    def trusted(self) -> bool:
        # A figure that participates in a FAILED tie-out is not trustworthy no matter how
        # it was found: the statement itself is internally inconsistent, so a confident
        # label match is worth nothing. Caught live on BPCL 2021-22, whose extracted
        # table_md is shifted a row ("Total Current Assets | 12.41", true value 54,683.73)
        # — it still produced 23 ratios because rule-bound lines were trusted by
        # construction and nothing ever acted on the failing check.
        if self.contradicted:
            return False
        # rules are the authority; a semantic guess, a derived parent subtotal AND an
        # UNSCOPED rule match must each be tied out arithmetically before they are trusted.
        # `rule_unscoped` is a pattern that matched outside its declared section because
        # the section state machine never tagged it. The label is right and unique in the
        # statement, but the structural proof is missing — so it is a proposal, and Layer 3
        # decides. This is the demotion that replaces the old outright refusal.
        return self.provenance == "rule" or (
            self.provenance in _NEEDS_PROOF and self.validated)


@dataclass
class BindingReport:
    statement: str
    bound: dict[str, BoundLine] = field(default_factory=dict)     # key -> line (any trust)
    abstained: list[str] = field(default_factory=list)            # specs that found nothing
    validations: list[dict] = field(default_factory=list)         # tie-out checks run
    spent_checks: set = field(default_factory=set)
    # ^ tie-outs whose identity was CONSUMED to derive a missing figure (see
    #   `_derive_missing`). Such a check can no longer judge that figure — it would pass
    #   by construction — so it is skipped rather than allowed to hand out free trust.
    shifted: dict[str, dict] = field(default_factory=dict)
    # ^ key -> evidence, for a spec that abstained even though a row matching its
    #   patterns IS present and DOES carry a figure — just not in the primary period
    #   column. See `_detect_column_shift`.
    attempts: list[BindAttempt] = field(default_factory=list)
    # ^ one entry per spec on this statement, bound or not, carrying the gate that
    #   rejected it. This is what makes "why is coverage low" a query.
    sections_seen: frozenset = frozenset()
    proposals: list[dict] = field(default_factory=list)
    # ^ Layer 2b LLM row proposals: what was offered, what came back, and whether it was
    #   acted on. In SHADOW mode nothing here ever reaches `bound` -- the list is both the
    #   audit trail and the scoreboard, since `kind="control"` entries carry an `agrees`
    #   flag against a row the rules had already bound.

    def trusted(self) -> dict[str, BoundLine]:
        return {k: v for k, v in self.bound.items() if v.trusted()}

    def rejections(self) -> dict[str, list[str]]:
        """reason -> keys. The whole point of the instrumentation: one call classifies
        every unbound key on this statement by the gate that stopped it."""
        out: dict[str, list[str]] = {}
        for a in self.attempts:
            if a.outcome == REJECTED and a.reason:
                out.setdefault(a.reason, []).append(a.key)
        return out


# --------------------------------------------------------------------------- line registry
@dataclass(frozen=True)
class LineSpec:
    key: str
    statement: str                       # "BS" | "PL" | "CF"
    patterns: tuple[str, ...]            # anchored, matched against stripped label, in order
    concept: str                         # natural-language text for the semantic tier
    roles: frozenset = frozenset()       # allowed row roles ({} = any valued row)
    section: str | None = None           # constrain candidates to this section (scopes semantic)
    subtotal_of_children: bool = False
    # ^ this Schedule III head is printed as a value-less parent over the sub-lines that
    #   carry the figures (PPE, Trade Payables). When the rules match such a header, sum
    #   its top-most valued descendants instead of abstaining — trusted only once the
    #   section's own hierarchy ties to its printed total (see `_validate_hierarchy`).
    recover_unlabelled_total: bool = False
    # ^ this key IS a section sub-total. Some extractions lose the label cell on exactly
    #   those rows (CPCL prints its Total Non-current/Current Assets rows with an empty
    #   Particulars cell), so no pattern can ever match them. Such a row is claimable
    #   only by ARITHMETIC: an unlabelled sum inside the section that equals the section's
    #   own preceding rows is that section's total and cannot be anything else.


# Section state machine for the balance sheet (header rows flip the current section).
_BS_SECTION_RULES = [
    ("non_current_assets",      r"^non[ -]?current assets\b"),
    ("current_assets",          r"^current assets\b"),
    ("non_current_liabilities", r"^non[ -]?current liabilit"),
    ("current_liabilities",     r"^current liabilit"),
    ("equity",                  r"^equity$"),
    ("assets_root",             r"^(i\.?\s*)?assets$"),
    ("equity_liab_root",        r"^(ii\.?\s*)?equity and liabilit"),
]

# Section state machine for the cash flow statement (Ind AS 7's three activity blocks).
# Scoping matters more here than on the balance sheet: "Interest paid" appears in both
# operating and financing, and a capex row read out of the financing block would be a
# repayment. Same contract as `_BS_SECTION_RULES` — only value-less header rows flip it.
_CF_SECTION_RULES = [
    ("cf_operating", r"^cash flows? from operating activities"),
    ("cf_investing", r"^cash flows? from investing activities"),
    ("cf_financing", r"^cash flows? from financing activities"),
    ("cf_operating", r"^operating activities\b"),
    ("cf_investing", r"^investing activities\b"),
    ("cf_financing", r"^financing activities\b"),
]

# The printed sub-total that each section's own rows must add up to. Summing a section
# with `_topmost_valued` and hitting this number proves the row nesting was read
# correctly — which is what makes a derived parent subtotal (PPE, Trade Payables)
# trustworthy rather than a guess.
# The canonical key holding each section's own total, used when the printed total row lost
# its label in extraction. Deliberately NOT the grand totals: a section must be proved by
# its own total, never by the balance sheet's.
_SECTION_TOTAL_KEY = {
    "non_current_assets":      "total_non_current_assets",
    "current_assets":          "total_current_assets",
    "non_current_liabilities": "total_non_current_liabilities",
    "current_liabilities":     "total_current_liabilities",
}
# Top-level blocks that sit BETWEEN a section's own rows and the next recognised header,
# so the forward tagger (`_tag_sections`) mis-includes them in whichever section happens
# to be open — proved live on PGCIL, whose "Regulatory Deferral Account Balances" row
# (a distinct Ind AS 114 block, not a current-asset line) inherited the `current_assets`
# tag because nothing between "Current assets" and "EQUITY AND LIABILITIES" flips it.
# `_validate_hierarchy` excludes each of these from the section sum it bled into, exactly
# as it already did for held-for-sale — the same defect, three more names for it.
_OUTSIDE_SECTION_KEYS = ("assets_held_for_sale", "liabilities_held_for_sale",
                         "regulatory_deferral_debit", "regulatory_deferral_credit",
                         "deferred_revenue")

_SECTION_TOTAL_RX = {
    "non_current_assets":      r"^total non[ -]?current assets\b",
    "current_assets":          r"^total current assets\b",
    "non_current_liabilities": r"^total non[ -]?current liabilit",
    "current_liabilities":     r"^total current liabilit",
    # WHY THE EXCLUSION IS "MENTIONS LIABILITIES ANYWHERE" AND NOT "...AND LIABILITIES".
    #
    # This entry was `^total equity$` — the only anchored one in the dict, and anchored
    # for a good reason: "Total equity" and "Total equity and liabilities" differ by three
    # words and by the whole right-hand side of the balance sheet, and reading the grand
    # total as the equity section total is what makes `_tag_back_from_totals` paint the
    # asset side into `equity`.
    #
    # The anchor also refused every Schedule III numeral form. Measured over 335 parsed
    # balance sheets, `^total equity$` matched 143 rows; the real corpus also prints
    # "total equity (a)" (12), "total equity (3)" (11), "total equity (i)" (8),
    # "total equity (c)" (7), "(1)", "(b)" and one OCR "©" — 42 further filings whose
    # equity section simply could not be tagged.
    #
    # Dropping the anchor therefore needs an exclusion, and the obvious one — a lookahead
    # for `and\s+liabilit` — is not enough. The corpus writes the grand total with an
    # ampersand 13 times: "total equity & liabilities", "total equity & liability",
    # "total equity, liabilities & deferred government grants and consumers'
    # contribution". Every one of those would be read as the equity section total, which
    # is the exact catastrophic mis-tag the anchor existed to prevent, merely relocated to
    # a different set of entities.
    #
    # So the test is semantic rather than syntactic: an equity SECTION total never
    # mentions liabilities, and a grand total always does. `(?!.*liabilit)` needs no
    # enumeration of separators and cannot be defeated by the next spelling.
    "equity":                  r"^total equity\b(?!.*liabilit)",
}

# Which half of the balance sheet each section belongs to. Used as an invariant on the
# back-tagger: a printed total is evidence about its own side of the statement and can
# never be evidence that an asset row is an equity row. See `_tag_back_from_totals`.
_SECTION_SIDE = {
    "assets_root": "assets", "non_current_assets": "assets", "current_assets": "assets",
    "equity_liab_root": "equity_and_liabilities", "equity": "equity_and_liabilities",
    "non_current_liabilities": "equity_and_liabilities",
    "current_liabilities": "equity_and_liabilities",
}

# The two grand totals. Neither belongs to a section, and both close whatever block
# precedes them, so the back-tagger treats them as terminators without re-tagging.
# `equity.*liabilit` rather than `equity and liabilit` for the same reason the equity
# section total excludes it that way: the corpus writes this grand total with "and", with
# "&", and once as "total equity, liabilities & deferred government grants". This regex
# and the `equity` entry above are two halves of one decision and must stay in step — a
# row the section dict declines has to be caught HERE as a block terminator, or the next
# recognised total reaches back across it.
_GRAND_TOTAL_RX = re.compile(r"^total (assets\b|equity\b.*liabilit)")

REGISTRY: list[LineSpec] = [
    # ---- Balance sheet ----
    LineSpec("total_assets", "BS", (r"^total assets$", r"^total assets\b"),
             "Total assets, grand total of all assets", frozenset({"total", "sum"})),
    LineSpec("total_current_assets", "BS", (r"^total current assets\b",),
             "Total current assets", frozenset({"total", "sum"}), "current_assets",
             recover_unlabelled_total=True),
    LineSpec("total_non_current_assets", "BS", (r"^total non[ -]?current assets\b",),
             "Total non-current assets", frozenset({"total", "sum"}), "non_current_assets",
             recover_unlabelled_total=True),
    LineSpec("total_equity", "BS", (r"^total equity$", r"^total equity(?! and)",
             r"^total shareholders?.? funds?\b", r"^shareholders?.? funds?\b"),
             "Total shareholders equity, net worth, shareholders funds",
             frozenset({"total", "sum"}), "equity", recover_unlabelled_total=True),
    LineSpec("total_current_liabilities", "BS", (r"^total current liabilit",),
             "Total current liabilities", frozenset({"total", "sum"}), "current_liabilities",
             recover_unlabelled_total=True),
    LineSpec("equity_share_capital", "BS", (r"^equity share capital\b", r"^share capital\b"),
             "Equity share capital, paid up capital", frozenset({"line"}), "equity"),
    LineSpec("reserves_and_surplus", "BS", (r"^other equity\b", r"^reserves and surplus\b",
             r"^reserves\b"), "Other equity, reserves and surplus, retained earnings",
             frozenset({"line"}), "equity"),
    LineSpec("non_controlling_interests", "BS",
             # GAIL prints "Non - Controlling Interests" with spaces around the hyphen,
             # so the separator has to be tolerant rather than a literal "-" or " ".
             (r"^non[\s-]*controlling interest", r"^minority interest"),
             "Non-controlling interests, minority interest",
             frozenset({"line", "total", "sum"}), "equity"),
    LineSpec("inventories", "BS", (r"^inventor(y|ies)\b",),
             "Inventories, stock in trade", frozenset({"line"}), "current_assets"),
    LineSpec("trade_receivables", "BS", (r"^trade receivable",),
             "Trade receivables, sundry debtors", frozenset({"line"}), "current_assets"),
    LineSpec("cash_and_cash_equivalents", "BS", (r"^cash and cash equivalent",),
             "Cash and cash equivalents, bank balances", frozenset({"line"}), "current_assets"),
    # Sheet trace: Balance Sheet > Assets > Non-Current Assets > "Property, Plant and
    # Equipment (Net Block per PPE Note/Schedule)". Often a value-less parent over its
    # own sub-lines, hence subtotal_of_children.
    LineSpec("net_fixed_assets", "BS",
             (r"^\(?[a-z]\)?\s*property,? plant and equipment\b", r"^property,? plant and equipment\b",
              r"^tangible assets\b", r"^net block\b"),
             "Property, plant and equipment, net block, tangible fixed assets",
             frozenset({"line"}), "non_current_assets", subtotal_of_children=True),
    # Sheet trace: Balance Sheet > Assets > Current Assets > "Current Investments"
    # (the sheet's "Marketable Securities/Current Investments" in Cash Ratio).
    LineSpec("marketable_securities", "BS",
             (r"^current investments\b", r"^investments\b"),
             "Current investments, marketable securities, short-term investments",
             frozenset({"line"}), "current_assets"),
    # Sheet trace: Balance Sheet > Equity & Liabilities > Current Liabilities >
    # "Trade Payables". Usually a value-less parent over the MSME / non-MSME split.
    LineSpec("trade_payables", "BS", (r"^trade payables\b", r"^sundry creditors\b"),
             "Trade payables, sundry creditors, amounts due to suppliers",
             frozenset({"line"}), "current_liabilities", subtotal_of_children=True),
    # Registry gap, not a matching failure: this key had NO LineSpec at all, so S10
    # (rising non-current-other share) could never run on any entity in the corpus.
    LineSpec("other_non_current_assets", "BS",
             (r"^other non[ -]?current assets\b", r"^other assets\b"),
             "Other non-current assets", frozenset({"line"}), "non_current_assets"),
    LineSpec("total_non_current_liabilities", "BS", (r"^total non[ -]?current liabilit",),
             "Total non-current liabilities", frozenset({"total", "sum"}),
             "non_current_liabilities", recover_unlabelled_total=True),
    LineSpec("total_equity_and_liabilities", "BS", (r"^total equity and liabilit",),
             "Total equity and liabilities", frozenset({"total", "sum"})),
    # Ind AS 105 held-for-sale blocks sit OUTSIDE the current/non-current split, as a
    # third top-level group, so Total Assets = NCA + CA + this. Without them the
    # assets_split identity fails on a perfectly sound balance sheet (CPCL) and the
    # contradiction rule then demotes figures that were right all along.
    LineSpec("assets_held_for_sale", "BS",
             (r"^assets? (included in |of )?disposal group", r"^assets? held[- ]for[- ]sale",
              r"^assets? classified as held for sale"),
             "Assets held for sale, assets included in a disposal group",
             frozenset({"line", "total", "sum"})),
    LineSpec("liabilities_held_for_sale", "BS",
             (r"^liabilit(y|ies) (included in |of )?disposal group",
              r"^liabilit(y|ies) held[- ]for[- ]sale",
              r"^liabilit(y|ies) classified as held for sale"),
             "Liabilities held for sale, liabilities in a disposal group",
             frozenset({"line", "total", "sum"})),
    # Ind AS 114 regulatory deferral accounts — a THIRD top-level group on each side of
    # the balance sheet, same shape as held-for-sale above and for the same reason: a
    # rate-regulated entity (a power distribution utility, principally) is required to
    # carry the timing difference a regulator has approved for future recovery or refund
    # as its own line, outside the current/non-current split. Without these two keys
    # assets_split and liabilities_split fail on every entity that reports one — not a
    # sign of a bad filing, but of an identity that did not know a fourth Schedule III
    # possibility exists (caught live on NTPC, whose accounts otherwise balance to the
    # rupee: a ₹16,960.60cr regulatory-deferral debit was the entire gap).
    LineSpec("regulatory_deferral_debit", "BS",
             (r"^regulatory deferral account.*debit", r"^regulatory deferral.*debit balance",
              # Some filings (PGCIL) print the ASSETS-side line bare, with neither
              # "debit" nor "credit" in the label — Ind AS 114's own defined term is
              # "regulatory deferral account debit balances", but the presentation
              # is a filer choice. Scoped to the assets section so it cannot also
              # claim the liabilities-side balance where a filer omits both labels
              # (none observed in this corpus, but the ambiguity is real).
              r"^regulatory deferral account balances?$"),
             "Regulatory deferral account debit balances",
             frozenset({"line", "total", "sum"}), "current_assets"),
    LineSpec("regulatory_deferral_credit", "BS",
             (r"^regulatory deferral account.*credit", r"^regulatory deferral.*credit balance"),
             "Regulatory deferral account credit balances",
             frozenset({"line", "total", "sum"})),
    # Deferred revenue (typically an unamortised capital grant) prints as its own
    # top-level line below current liabilities in the same filings that carry a
    # regulatory deferral account — NTPC's own liabilities_split gap was this line plus
    # the credit-side deferral above, together and exactly.
    LineSpec("deferred_revenue", "BS", (r"^deferred revenue\b",),
             "Deferred revenue, unamortised capital grant",
             frozenset({"line", "total", "sum"})),
    LineSpec("long_term_borrowings", "BS", (r"^borrowings\b", r"^long[- ]term borrowings\b"),
             "Long-term borrowings", frozenset({"line", "total", "sum"}), "non_current_liabilities"),
    LineSpec("short_term_borrowings", "BS", (r"^short[- ]term borrowings\b", r"^borrowings\b"),
             "Short-term borrowings, bank overdraft, cash credit",
             frozenset({"line", "total", "sum"}), "current_liabilities"),
    # -- Debt-definition components (FDR Appendix G ratio 3). The FDR mapping sheet's
    #    "Legend & Assumptions" is explicit that no single Schedule III line equals
    #    "total debt" and that the reviewer must FIX a definition covering current
    #    maturities and lease liabilities, then apply it consistently. Binding the
    #    components separately is what lets `computations._total_debt()` state which
    #    definition it used in the trace instead of silently summing two lines.
    LineSpec("lease_liabilities_nc", "BS", (r"^lease liabilit",),
             "Lease liabilities (non-current), Ind AS 116",
             frozenset({"line", "total", "sum"}), "non_current_liabilities"),
    LineSpec("lease_liabilities_cl", "BS", (r"^lease liabilit",),
             "Lease liabilities (current), Ind AS 116",
             frozenset({"line", "total", "sum"}), "current_liabilities"),
    LineSpec("current_maturities_ltd", "BS",
             (r"^current maturit(y|ies) of long[- ]term (debt|borrowing)",
              r"^current maturit(y|ies)\b"),
             "Current maturities of long-term debt",
             frozenset({"line", "total", "sum"}), "current_liabilities"),
    # ---- Statement of P&L ----
    LineSpec("revenue", "PL", (r"^revenue from operation", r"^turnover\b"),
             "Revenue from operations, sales, turnover", frozenset({"line"})),
    LineSpec("other_income", "PL", (r"^other income\b",), "Other income", frozenset({"line"})),
    LineSpec("total_income", "PL", (r"^total income\b",), "Total income",
             frozenset({"total", "sum", "line"})),
    # COGS is not a Schedule III face line — it is the sum of these three P&L lines
    # (computations.py's `_cogs()` derives it; each binds independently, and the
    # ratio specs' `any_of` accepts a partial set rather than requiring all three,
    # since a given filing — e.g. an extraction company vs. a manufacturer — prints
    # a different subset of them).
    LineSpec("cost_of_materials_consumed", "PL", (r"^cost of materials consumed\b",),
             "Cost of materials consumed", frozenset({"line"})),
    LineSpec("purchases_of_stock_in_trade", "PL", (r"^purchase(s)? of stock[- ]in[- ]trade\b",),
             "Purchases of stock-in-trade", frozenset({"line"})),
    LineSpec("changes_in_inventories", "PL",
             (r"^changes? in inventor(y|ies)", r"^\(increase\)\s*/\s*decrease in inventor(y|ies)"),
             "Changes in inventories of finished goods, work-in-progress and stock-in-trade",
             frozenset({"line"})),
    LineSpec("finance_costs", "PL", (r"^finance cost",),
             "Finance costs, interest expense", frozenset({"line"})),
    # Sheet trace: Statement of Profit & Loss > Expenses > "Other Expenses". Schedule III
    # prints ONE aggregate here; the sheet's Administrative vs Selling & Distribution
    # split is note-level, so both its components map to this single line (S.No 29).
    LineSpec("sga_expenses", "PL", (r"^other expenses\b", r"^administrative expenses\b"),
             "Other expenses, selling administrative and general expenses",
             frozenset({"line"})),
    LineSpec("depreciation", "PL", (r"deplet.*depreciat", r"depreciat.*amort", r"^depreciation\b"),
             "Depreciation, depletion and amortisation expense", frozenset({"line"})),
    LineSpec("total_expenses", "PL", (r"^total expenses\b",), "Total expenses",
             frozenset({"total", "sum", "line"})),
    LineSpec("pbt", "PL", (r"^profit before tax\b", r"^profit.* before tax\b"),
             "Profit before tax", frozenset()),
    LineSpec("total_tax", "PL", (r"^total tax expense\b", r"^tax expense\b"),
             "Total tax expense", frozenset({"total", "sum", "line"})),
    # `_strip` removes an ordinal prefix but NOT the "/(loss)" construction, so
    # "Profit/(Loss) for the Year" matched none of the four literal patterns while
    # "Profit/(Loss) before tax" was caught by PBT's `^profit.* before tax\b` wildcard.
    # That asymmetry — a wildcard on one key and none on its sibling — is the silent
    # coverage collapse this file warns about elsewhere: PAT abstained on filings whose
    # PBT bound perfectly. The negative lookahead is load-bearing: without it the same
    # wildcard swallows "Profit before tax for the year" and binds a CONFIDENTLY WRONG
    # figure, which is worse than the miss it replaces.
    LineSpec("pat", "PL", (r"^profit for the period\b", r"^profit for the year\b",
             r"^profit(?!.*before tax).*for the (year|period)\b",
             r"^profit.*after tax\b", r"^net profit\b"),
             "Profit for the period, profit for the year, net profit after tax (PAT)", frozenset()),
    LineSpec("eps", "PL", (r"^basic and diluted\b", r"earnings per equity share",
             r"earnings per share"), "Earnings per share, basic and diluted EPS", frozenset()),
    # ---- Cash Flow Statement (Ind AS 7) ----
    # Needed by FDR Appendix G ratios 14 (Accruals) and 15 (Free Cash Flow), whose inputs
    # exist on no other statement. Sign convention is NOT assumed: Ind AS 7 prints
    # outflows as negatives, but not every extraction preserves the sign, so the capex
    # keys are magnitude-normalised in `computations._capex()` and the direction is
    # proved instead by `cash_flow_reconciles` below.
    LineSpec("ocf", "CF",
             (r"^net cash (generated|used|inflow|outflow|flow)?.{0,40}operating activities",
              r"^net cash.{0,40}from operating activities",
              r"^cash (generated from|flow from) operating activities"),
             "Net cash generated by / used in operating activities",
             frozenset({"line", "total", "sum"}), "cf_operating"),
    LineSpec("icf", "CF",
             (r"^net cash (generated|used|inflow|outflow|flow)?.{0,40}investing activities",
              r"^net cash.{0,40}from investing activities"),
             "Net cash generated by / used in investing activities",
             frozenset({"line", "total", "sum"}), "cf_investing"),
    LineSpec("fcf_financing", "CF",
             (r"^net cash (generated|used|inflow|outflow|flow)?.{0,40}financing activities",
              r"^net cash.{0,40}from financing activities"),
             "Net cash generated by / used in financing activities",
             frozenset({"line", "total", "sum"}), "cf_financing"),
    LineSpec("net_change_in_cash", "CF",
             (r"^net (increase|decrease|change)\b.{0,40}cash and cash equivalent",),
             "Net increase / (decrease) in cash and cash equivalents",
             frozenset({"line", "total", "sum"})),
    # Capex components — Appendix G ratio 15 items 2a / 2b / 2c. Scoped to the investing
    # section so a financing-section repayment can never be read as capital expenditure.
    LineSpec("capex_ppe", "CF",
             (r"^payments? for property,? plant and equipment",
              r"^purchase(s)? of property,? plant and equipment",
              r"^additions? to property,? plant and equipment",
              r"^purchase(s)? of fixed assets",
              r"^payments? for (property|fixed assets|capital)"),
             "Payments for property, plant and equipment, purchase of fixed assets",
             frozenset({"line"}), "cf_investing"),
    LineSpec("capex_intangibles", "CF",
             (r"^(payments?|purchase(s)?) (for|of) intangible assets",
              r"^additions? to intangible assets"),
             "Purchase of intangible assets", frozenset({"line"}), "cf_investing"),
    # Sector-specific capital expenditure the mapping sheet calls out explicitly for
    # extraction entities (item 2c: "Exploratory/development drilling or CWIP additions").
    # For ONGC this is 34% of total capex — omitting it would overstate free cash flow.
    LineSpec("capex_exploration", "CF",
             (r"^exploratory and development drilling",
              r"^expenditure on exploratory",
              r"^(payments? for|additions? to) capital work[- ]in[- ]progress"),
             "Exploratory and development drilling, capital work-in-progress additions",
             frozenset({"line"}), "cf_investing"),
    # Government-grant movement. This is ONE of the three scattered components the
    # mapping sheet's Legend names for Appendix G ratio 16 — never the whole figure —
    # so `govt_support_dependency` reports it with a mandatory completeness caveat.
    LineSpec("government_grant_cf", "CF",
             (r"^-?\s*amorti[sz]ation of government grant",
              r"^-?\s*government grant", r"^-?\s*grants?[- ]in[- ]aid"),
             "Government grant amortisation or receipt", frozenset({"line"})),
]

# Computed once, after the registry is complete. Currently: long_term_borrowings /
# short_term_borrowings (both claim `^borrowings\b`) and lease_liabilities_nc / _cl (both
# claim `^lease liabilit`).
_SECTION_CRITICAL = _section_critical()


# Tie-outs used by Layer 3. Each is (name, required keys, optional keys, check): every
# REQUIRED key must be bound or the check SKIPs; OPTIONAL keys default to 0 so a block a
# given filing simply doesn't have (held-for-sale) can't fail an otherwise sound identity.
_VALIDATIONS = {
    "BS": [
        ("assets_split", ["total_current_assets", "total_non_current_assets", "total_assets"],
         ["assets_held_for_sale", "regulatory_deferral_debit"],
         lambda v: _close(v["total_current_assets"] + v["total_non_current_assets"]
                          + v["assets_held_for_sale"] + v["regulatory_deferral_debit"],
                          v["total_assets"])),
        # CONSOLIDATED statements carry a third equity component — non-controlling
        # interests — so Total Equity exceeds Share Capital + Other Equity by exactly the
        # NCI. Without it this identity fails on every consolidated filing, and the
        # contradiction rule then discards equity figures that are perfectly good.
        ("equity_split", ["equity_share_capital", "reserves_and_surplus", "total_equity"],
         ["non_controlling_interests"],
         lambda v: _close(v["equity_share_capital"] + v["reserves_and_surplus"]
                          + v["non_controlling_interests"], v["total_equity"])),
        # These two prove the statement was assembled COMPLETELY. A balance sheet split
        # across table chunks and only half-read (see `_with_continuations`) cannot
        # satisfy them, so a bad/missing join fails loudly here instead of silently
        # abstaining nine ratios and computing debt on part of the borrowings.
        ("liabilities_split", ["total_current_liabilities", "total_non_current_liabilities",
                               "total_equity", "total_equity_and_liabilities"],
         ["liabilities_held_for_sale", "deferred_revenue", "regulatory_deferral_credit"],
         lambda v: _close(v["total_current_liabilities"] + v["total_non_current_liabilities"]
                          + v["total_equity"] + v["liabilities_held_for_sale"]
                          + v["deferred_revenue"] + v["regulatory_deferral_credit"],
                          v["total_equity_and_liabilities"])),
        ("balance_sheet_balances", ["total_assets", "total_equity_and_liabilities"], [],
         lambda v: _close(v["total_assets"], v["total_equity_and_liabilities"])),
    ],
    "PL": [
        ("income_identity", ["revenue", "other_income", "total_income"], [],
         lambda v: _close(v["revenue"] + v["other_income"], v["total_income"])),
        ("pat_identity", ["pbt", "total_tax", "pat"], [],
         lambda v: _close(v["pbt"] - v["total_tax"], v["pat"])),
    ],
    "CF": [
        # Ind AS 7's closing identity, and the reason Free Cash Flow and the Accruals
        # Ratio can be published at all: it proves the three activity subtotals were
        # read from the right rows AND that the extraction preserved their signs. If
        # `ocf` had bound to a working-capital line, or an outflow had lost its minus,
        # this fails and every OCF-dependent ratio drops to untrusted rather than
        # quietly computing off a wrong figure.
        ("cash_flow_reconciles", ["ocf", "icf", "fcf_financing", "net_change_in_cash"], [],
         lambda v: _close(v["ocf"] + v["icf"] + v["fcf_financing"], v["net_change_in_cash"])),
    ],
}


# --------------------------------------------------------------------------- resolver
class Resolver:
    def __init__(self, embed_fn: EmbedFn | None = None,
                 sem_threshold: float = 0.55,
                 propose_fn: LB.ProposeFn | None = None,
                 llm_shadow: bool = True):
        self.embed_fn = embed_fn
        self.sem_threshold = sem_threshold      # a floor; the arithmetic gate is the real check
        self.propose_fn = propose_fn
        # Shadow is the DEFAULT and should stay so until the numbers say otherwise. A
        # proposer that has not been scored against controls ON THIS CORPUS has no
        # measured error rate, and "`trusted()` will catch it" is an argument, not
        # evidence. `_llm_pass` records the agreement rate that settles the question.
        self.shadow = llm_shadow

    # -- section tagging (Layer 0) --
    def _tag_sections(self, pt: ParsedTable, headers_only: bool = True,
                      rules: list | None = None) -> dict[int, str | None]:
        """Section-tag every row from the block headers above it.

        Only VALUE-LESS rows may flip the section (`_classify_role` returns "header"
        exactly when a row carries no figure). A Schedule III section head never has a
        figure of its own, whereas a LINE ITEM can begin with the very same words:
        MRPL prints "Non-Current Assets held for Sale  32.31" inside current assets,
        which matched `^non[ -]?current assets\\b` and threw the state machine back to
        non_current_assets, mis-tagging "Total Current Assets (II)" and "TOTAL ASSETS
        (I+II)" on the way out. ONGC shows the same misfire benignly (its Total Assets
        row tags as current_assets), harmless only because that spec is unscoped.

        `headers_only=False` is the fallback for extractions that lost their header
        rows entirely — there, tagging off any row beats no sections at all.
        """
        rules = _BS_SECTION_RULES if rules is None else rules
        tags, cur = {}, None
        for r in pt.rows:
            if not headers_only or r.role == "header":
                plain = _strip(r.label)
                # plain form first, so an unambiguous head can never be mis-read by the
                # bare-ordinal rule; only then retry without a delimiter-less ordinal.
                for lab in (plain, _strip_bare_ordinal(plain)):
                    hit = next((n for n, pat in rules if re.match(pat, lab)), None)
                    if hit:
                        cur = hit
                        break
                    if lab == plain and _strip_bare_ordinal(plain) == plain:
                        break                      # nothing more to try
            tags[r.idx] = cur
        if headers_only and not any(tags.values()):
            return self._tag_sections(pt, headers_only=False, rules=rules)
        if rules is _BS_SECTION_RULES:
            self._tag_back_from_totals(pt, tags)
        return tags

    def _tag_back_from_totals(self, pt: ParsedTable, tags: dict) -> None:
        """Second, INDEPENDENT source of section evidence — the printed section totals.

        The forward state machine is single-pass and forward-only: one missed or misread
        header silently disables every section-scoped spec downstream, and the failure is
        indistinguishable from a filing that simply lacks the line. Measured consequence —
        `total_current_assets`, `total_non_current_assets` and `total_current_liabilities`
        bound on 0 of 10 entities while unscoped keys on the same statements sat at 61-89%.

        A row reading "Total Current Assets" PROVES the rows above it, back to the previous
        total, are current assets, whatever the header said. That is the same
        arithmetic-over-matcher-confidence principle the rest of this file runs on, so it
        OVERRIDES the forward tag rather than merely filling gaps — the MRPL case
        ("Non-Current Assets held for Sale" printed inside current assets, throwing the
        machine back a section) is a mis-tag, not an absent tag, and a gap-fill would leave
        it broken.

        TWO BOUNDS ON THE WINDOW, BOTH LEARNED THE HARD WAY
        ---------------------------------------------------
        The first version advanced its window only when it CONSUMED a total it could
        recognise. Where a filing's section sub-total labels were lost in extraction —
        an empty label on a `sum` row, which is common — nothing advanced the window, and
        the first total the machine did recognise painted every row above it, back to row
        zero, into that one section.

        Measured on ONGC 2022-23: the forward pass tagged 21 rows `non_current_assets`
        and 11 `current_assets`, correctly. The only recognised total on the statement
        was "Total equity", printed last. Back-tagging then relabelled ALL 38 rows
        `equity`. Every asset-side spec was thereby scoped out of existence, the
        `assets_split` tie-out could not run, and every asset line that did bind stayed
        `rule_unscoped` and untrusted — which is why `total_non_current_assets` and
        `other_non_current_assets` bound on zero entities corpus-wide while the same
        statements happily produced `total_assets`. One defect, the whole asset side.

        So:

          1. EVERY printed total or sub-total closes a block, whether or not its label
             survived and whether or not the label is one we recognise. An unrecognised
             or unlabelled terminator does not re-tag anything — it just stops the next
             recognised total from reaching back past it. A labelled inner sub-total
             ("Total Property, Plant and Equipment") is deliberately NOT a terminator,
             so the MRPL mis-tag repair still spans its whole section.

          2. A total may only re-tag rows on ITS OWN SIDE of the balance sheet. A row the
             forward pass placed among the assets cannot be evidence for an equity
             total, no matter what the window arithmetic says. This is the invariant
             that makes the ONGC failure impossible rather than merely unlikely.
        """
        order = list(pt.rows)
        start = 0                       # first row of the block the next total closes
        for n, r in enumerate(order):
            if r.role not in ("total", "sum"):
                continue
            lab = _strip(r.label or "")
            hit = next((sec for sec, pat in _SECTION_TOTAL_RX.items()
                        if lab and re.match(pat, lab)), None)

            # Bound 1 — a lost-label sub-total, or a grand total, terminates the block
            # without re-tagging it. A labelled row we simply do not recognise is an
            # inner sub-total and is left alone entirely.
            if hit is None:
                if not lab or _GRAND_TOTAL_RX.match(lab):
                    start = n + 1
                continue

            # Bound 2 — same side of the statement only.
            side = _SECTION_SIDE.get(hit)
            for q in order[start:n + 1]:
                had = tags.get(q.idx)
                if side and had and _SECTION_SIDE.get(had, side) != side:
                    continue
                tags[q.idx] = hit
            start = n + 1

    @staticmethod
    def _section_audit(kind: str, tags: dict) -> dict | None:
        """Post-validate the tagging. A balance sheet missing `equity` or
        `current_liabilities` entirely is a TAGGING failure, not a filing without
        liabilities — and saying so is what stops a silent zero-coverage section being
        read downstream as a disclosure gap."""
        if kind != "BS":
            return None
        expected = {"non_current_assets", "current_assets", "equity",
                    "non_current_liabilities", "current_liabilities"}
        seen = {v for v in tags.values() if v}
        missing = sorted(expected - seen)
        if not missing:
            return None
        return {"check": "section_tagging", "status": "FAIL",
                "reason": EXT_SECTION_INCOMPLETE,
                "inputs": {"missing_sections": missing, "sections_seen": sorted(seen)}}

    def _candidates(self, pt, sections, section, period):
        out = []
        for r in pt.rows:
            if r.values.get(period) is None or not r.label:
                continue
            if section and sections.get(r.idx) != section:
                continue
            out.append(r)
        return out

    # -- one line spec --
    def _resolve(self, spec: LineSpec, pt, sections, periods,
                 levels=None) -> tuple[BoundLine | None, str | None, str]:
        """Returns (line, reject_reason, detail). `reject_reason` is None when a line is
        returned, and otherwise one of the closed reason codes — never a bare None, so a
        failed bind always says which of the four and-conditions stopped it."""
        period = periods[0]
        prior = periods[1] if len(periods) > 1 else None
        cands = self._candidates(pt, sections, spec.section, period)
        valued = [r for r in pt.rows if r.values.get(period) is not None and r.label]

        section_present = (not spec.section) or any(v == spec.section
                                                    for v in sections.values())
        # Section scoping is a PREFERENCE, not a precondition. A spec that declares a
        # section but whose section was never tagged may still match on the full table —
        # demoted to `rule_unscoped`, which Layer 3 must confirm before it is trusted.
        # Specs with no declared section are unaffected and keep their authority.
        may_fall_back = bool(spec.section) and spec.key not in _SECTION_CRITICAL
        saw_role_reject = hierarchy_failed = semantic_weak = False
        unscoped_reason: str | None = None

        # Layer 1 — rules (authority), inside the declared section
        for pat in spec.patterns:
            rx = re.compile(pat, re.I)
            for r in cands if spec.section else valued:
                if rx.search(_strip(r.label)) is None:
                    continue
                if spec.roles and r.role not in spec.roles:
                    saw_role_reject = True
                    continue
                return BoundLine(spec.key, r.values.get(period),
                                 r.values.get(prior) if prior else None, r.label, period,
                                 sections.get(r.idx), "rule", 1.0, pt.table_id, pt.page), None, ""

        # Layer 1a — the same patterns, unscoped, accepted ONLY if unique in the statement.
        # A second match means the section tag was the only discriminator, and binding
        # either one would be a guess: abstain instead. `_SECTION_CRITICAL` specs never
        # reach here at all — for them a wrong bind is silent and feeds the funding cluster.
        if may_fall_back and not section_present:
            hits: list = []
            for pat in spec.patterns:
                rx = re.compile(pat, re.I)
                for r in valued:
                    if rx.search(_strip(r.label)) is None:
                        continue
                    if spec.roles and r.role not in spec.roles:
                        saw_role_reject = True
                        continue
                    if all(r.idx != h.idx for h in hits):
                        hits.append(r)
                if hits:
                    break                      # ordered patterns: first one to hit wins
            if len(hits) == 1:
                r = hits[0]
                return BoundLine(spec.key, r.values.get(period),
                                 r.values.get(prior) if prior else None, r.label, period,
                                 sections.get(r.idx), "rule_unscoped", 1.0, pt.table_id,
                                 pt.page,
                                 detail=f"matched outside its declared section "
                                        f"({spec.section}), which was never tagged; unique "
                                        f"in the statement, so awaiting arithmetic proof"), None, ""
            if len(hits) > 1:
                unscoped_reason = AMBIGUOUS_UNSCOPED
        elif spec.section and not section_present and not may_fall_back:
            unscoped_reason = SECTION_CRITICAL

        # Layer 1b — the head is printed as a value-less parent: sum its children.
        # Runs BEFORE the semantic tier so a real hierarchy always beats a fuzzy
        # label match (on ONGC the semantic tier otherwise grabs "(ii) Other Property,
        # Plant and Equipment" — one sub-line — as if it were the whole net block).
        # A derived figure is untrusted until `_validate_hierarchy` ties its section out,
        # so the unscoped pass here carries no extra risk beyond the scoped one.
        if spec.subtotal_of_children and levels:
            for unscoped in (False, True):
                if unscoped and not (may_fall_back and not section_present):
                    break
                heads: list = []
                for pat in spec.patterns:
                    rx = re.compile(pat, re.I)
                    for r in pt.rows:
                        if not r.label or r.values.get(period) is not None:
                            continue
                        if not unscoped and spec.section and sections.get(r.idx) != spec.section:
                            continue
                        if rx.search(_strip(r.label)) is None:
                            continue
                        heads.append(r)
                    if heads:
                        break
                if unscoped and len(heads) > 1:
                    unscoped_reason = unscoped_reason or AMBIGUOUS_UNSCOPED
                    heads = []
                for r in heads:
                    cur = _subtree_sum(pt, levels, r, period)
                    if cur is None:
                        continue
                    pri = _subtree_sum(pt, levels, r, prior) if prior else None
                    return BoundLine(spec.key, cur, pri, r.label, period,
                                     sections.get(r.idx), "derived", 1.0, pt.table_id,
                                     pt.page, detail="sum of sub-lines printed beneath "
                                                     "this head" + (" (matched outside its "
                                                     "declared section)" if unscoped else "")), None, ""
                if heads:
                    hierarchy_failed = True

        # Layer 1c — a section sub-total whose label cell was lost in extraction. Claimed
        # only by arithmetic: the row must sit in the section and must equal the sum of
        # that section's own rows ABOVE it (a subtotal totals what precedes it). A wrong
        # row cannot satisfy that, so this recovers the figure without guessing.
        if spec.recover_unlabelled_total and levels and spec.section:
            in_sec = [r for r in pt.rows if sections.get(r.idx) == spec.section]
            for n, r in enumerate(in_sec):
                if (r.label or "").strip() or r.role not in ("total", "sum"):
                    continue
                val = r.values.get(period)
                if val is None:
                    continue
                above = [q for q in in_sec[:n] if q.role not in ("total", "sum")]
                if not above or not _close(sum(q.values[period] for q in
                                              _topmost_valued(above, levels, period)), val):
                    continue
                pri = None
                if prior:
                    ap = [q for q in above if q.values.get(prior) is not None]
                    pri = (sum(q.values[prior] for q in _topmost_valued(ap, levels, prior))
                           if ap else None)
                return BoundLine(spec.key, val, pri, f"(unlabelled sub-total of {spec.section})",
                                 period, spec.section, "derived", 1.0, pt.table_id, pt.page,
                                 validated=True,
                                 detail="unlabelled row equal to the sum of its section's "
                                        "preceding lines"), None, ""

        # Layer 2 — scoped semantic proposal (optional). NOT for structural aggregates:
        # a spec that accepts only total/sum rows is a sub-total, and "Total Equity" vs
        # "Total Equity and Liabilities" are near-identical to an embedder while being
        # wildly different numbers. Those must come from a rule, or from the arithmetic
        # unlabelled-total recovery above, or not at all.
        structural_total = bool(spec.roles) and spec.roles <= {"total", "sum"}
        if self.embed_fn and cands and not structural_total:
            import numpy as np
            labels = [_strip(r.label) for r in cands]
            mat = np.asarray(self.embed_fn(labels + [spec.concept]), dtype="float32")
            qv, cv = mat[-1], mat[:-1]
            sims = cv @ qv
            best = int(np.argmax(sims))
            if float(sims[best]) >= self.sem_threshold:
                r = cands[best]
                return BoundLine(spec.key, r.values.get(period),
                                 r.values.get(prior) if prior else None, r.label, period,
                                 sections.get(r.idx), "semantic", float(sims[best]),
                                 pt.table_id, pt.page), None, ""
            semantic_weak = True

        # ---- nothing bound: name the gate that stopped it, most actionable first ----
        if saw_role_reject:
            return None, ROLE_MISMATCH, (f"a label matched but its row role is not in "
                                         f"{sorted(spec.roles)} — role-classifier fix")
        if hierarchy_failed:
            return None, HIERARCHY_UNRESOLVED, ("matched a value-less head whose descendants "
                                                "produced no sum")
        if unscoped_reason:
            return None, unscoped_reason, (
                f"section {spec.section!r} was never tagged; "
                + ("more than one row matches these patterns, so binding either would be a "
                   "guess" if unscoped_reason == AMBIGUOUS_UNSCOPED else
                   "a sibling spec claims the same pattern, so an unscoped match could bind "
                   "the wrong figure silently"))
        # A pattern that matches a row carrying NO figure is a `subtotal_of_children`
        # candidate, not a caption problem — a materially different fix.
        for pat in spec.patterns:
            rx = re.compile(pat, re.I)
            for r in pt.rows:
                if r.label and r.values.get(period) is None and rx.search(_strip(r.label)):
                    return None, VALUE_NULL, (f"matched {r.label.strip()!r}, which carries no "
                                              f"figure of its own")
        if semantic_weak:
            return None, SEMANTIC_BELOW_THRESHOLD, f"best similarity below {self.sem_threshold}"
        if spec.section and not section_present:
            return None, NO_SECTION_TAGGED, (f"section {spec.section!r} was never tagged on "
                                             f"this statement")
        if spec.section and not cands:
            return None, SECTION_EMPTY, (f"section {spec.section!r} was tagged but held no "
                                         f"labelled row carrying a figure")
        in_scope = [(r.label or "").strip()[:48] for r in (cands if spec.section else valued)]
        return None, PATTERN_NO_MATCH, "labels in scope: " + "; ".join(in_scope[:12])

    # -- Layer 2b — fill a missing aggregate from an identity, then prove it elsewhere --
    def _derive_missing(self, kind: str, rep: BindingReport) -> None:
        """A grand-total row that no rule matched can still be known exactly: Total Assets
        is Total Non-current + Total Current Assets (+ held-for-sale) by the same identity
        `_VALIDATIONS` already tests. National Fertilizers 2024-25 binds both section
        totals but not the grand total, which abstained Equity Ratio, Debt Ratio, ROCE,
        Capital Turnover and Debt-to-Total-Assets on a balance sheet that plainly contains
        the number.

        This is arithmetic, not a guess — but the identity that produced the figure cannot
        also vouch for it, so the check is marked SPENT and the derived line only becomes
        trusted if the INDEPENDENT `balance_sheet_balances` tie-out (against Total Equity
        and Liabilities, read from the other side of the statement) confirms it. No
        confirmation -> stays untrusted -> the ratio abstains exactly as before.

        MEASURED, so nobody has to re-measure it: over 110 finance_llm documents this
        fires 3 times and is confirmed 0 times, i.e. it currently yields no extra ratio.
        The blocker is NOT this derivation — it is diagnosed and specific. CPCL 2017-18
        prints its two grand totals with the bare label "TOTAL", so neither
        `^total assets$` nor `^total equity and liabilit` matches and
        `total_equity_and_liabilities` never binds to confirm anything (the derived figure
        1,416,549.28 does equal that filing's printed TOTAL row exactly). Binding a bare
        "TOTAL" needs position/section disambiguation — the first one closes the assets
        side, the second the equity-and-liabilities side — and must NOT be done by letting
        both keys match the same row, which would make `balance_sheet_balances` pass
        circularly. Until that lands, this stays a correct half of a two-part fix."""
        if kind != "BS":
            return
        parts = ("total_non_current_assets", "total_current_assets")
        cur = rep.bound.get("total_assets")
        if (cur and cur.value is not None) or not all(
                k in rep.bound and rep.bound[k].value is not None for k in parts):
            return
        extra = 0.0
        for extra_key in ("assets_held_for_sale", "regulatory_deferral_debit"):
            ln = rep.bound.get(extra_key)
            if ln and ln.value is not None and ln.trusted():
                extra += ln.value
        total = sum(rep.bound[k].value for k in parts) + extra
        priors = [rep.bound[k].prior for k in parts]
        prior = sum(priors) if all(p is not None for p in priors) else None
        rep.bound["total_assets"] = BoundLine(
            "total_assets", total, prior, "(derived: non-current + current assets)",
            rep.bound[parts[0]].period, None, "derived", 1.0,
            rep.bound[parts[0]].table_id, rep.bound[parts[0]].page,
            detail="sum of the printed section totals; the grand-total row did not bind")
        rep.spent_checks.add("assets_split")
        if "total_assets" in rep.abstained:
            rep.abstained.remove("total_assets")

    # -- Layer 3 validation over the bound set --
    def _detect_column_shift(self, pt: ParsedTable, rep: BindingReport) -> None:
        """Explain an abstain that is an EXTRACTION fault, not a disclosure gap.

        A spec abstains when no row carries a figure in the primary period. That message
        ("missing inputs: ocf") reads as "the entity did not disclose this" — but a row
        whose label matches perfectly and which carries a figure in a LATER column has
        plainly been disclosed; the extraction pushed it sideways. This is failure mode
        F1/F3 in RATIO_ENGINE_DESIGN.md §5.1 (a phantom leading column from a collapsed
        rowspan/colspan), measured live on ONGC 2019-20, 2020-21 and 2023-24, whose cash
        flow tables all carry a spurious trailing `col_N` and leave the current-year cell
        of some rows empty.

        This DETECTS and REPORTS only. It deliberately does not shift the value back:
        deciding which column is really the current period needs the date parsing of §6
        (P3), and a wrong re-alignment silently inverts every ratio and average — far
        worse than an abstain. So the figure stays unbound and the reviewer is told the
        number exists and where, which is the actionable half."""
        if len(pt.periods) < 2:
            return
        primary, others = pt.periods[0], pt.periods[1:]
        for key in rep.abstained:
            spec = next((s for s in REGISTRY if s.key == key and s.statement == rep.statement),
                        None)
            if spec is None:
                continue
            for pat in spec.patterns:
                rx = re.compile(pat, re.I)
                for r in pt.rows:
                    if not r.label or rx.search(_strip(r.label)) is None:
                        continue
                    if spec.roles and r.role not in spec.roles:
                        continue
                    if r.values.get(primary) is not None:
                        continue                      # bound elsewhere / genuinely absent
                    found = {p: r.values[p] for p in others if r.values.get(p) is not None}
                    if found:
                        rep.shifted[key] = {
                            "label": r.label, "row": r.idx,
                            "empty_period": primary,
                            "values_found_in": {str(p): v for p, v in found.items()},
                            "reason": "label matched and a figure is present, but not in "
                                      "the primary period column — suspected column shift "
                                      "in extraction, NOT a non-disclosure by the entity",
                        }
                        break
                if key in rep.shifted:
                    break

    def _validate(self, kind: str, rep: BindingReport) -> None:
        for name, keys, opt, check in _VALIDATIONS.get(kind, []):
            if name in rep.spent_checks:
                rep.validations.append({"check": name, "status": "SKIP",
                                        "reason": "identity consumed to derive a missing "
                                                  "figure — cannot also verify it"})
                continue
            if not all(k in rep.bound and rep.bound[k].value is not None for k in keys):
                rep.validations.append({"check": name, "status": "SKIP",
                                        "reason": "not all inputs bound"})
                continue
            vals = {k: rep.bound[k].value for k in keys}
            for k in opt:
                # A block this filing doesn't have contributes zero — and so does a mere
                # SEMANTIC PROPOSAL. An optional key is there to accommodate a component
                # some filings print; letting an unvalidated guess fill it lets the guess
                # break the very identity that is supposed to judge it. (Standalone ONGC
                # has no non-controlling interests, so the semantic tier offered "Other
                # equity" for it and equity_split then failed on a sound balance sheet.)
                ln = rep.bound.get(k)
                usable = ln is not None and ln.value is not None and (
                    ln.provenance not in _GUESSED or ln.validated)
                vals[k] = ln.value if usable else 0.0
            ok = bool(check(vals))
            rep.validations.append({"check": name, "status": "PASS" if ok else "FAIL",
                                    "inputs": vals})
            if ok:
                for k in keys:                       # upgrade semantic guesses to trusted
                    rep.bound[k].validated = True
                continue
            # FAILED. Blame the weakest participant. An unvalidated semantic guess is a
            # PROPOSAL, so if one is in the mix it is the likely culprit and must not be
            # allowed to demote a rule match (a wrong guess for Total Equity and
            # Liabilities would otherwise discredit a correctly rule-bound Total Assets).
            guesses = [k for k in keys
                       if rep.bound[k].provenance in _GUESSED
                       and not rep.bound[k].validated]
            # With no guess to blame, every figure here is rule-bound or already tied
            # out, so the STATEMENT itself does not add up and none of them may be used
            # — a ratio built on a balance sheet that does not balance is worse than no
            # ratio at all (BPCL 2021-22, whose extracted rows are shifted by one).
            for k in (guesses or keys):
                rep.bound[k].contradicted = True

    def _validate_hierarchy(self, pt, levels, sections, rep: BindingReport) -> None:
        """A derived parent subtotal is only as good as the nesting it was read from, so
        prove the nesting: re-add each section from its own rows (top-most valued node
        per branch) and require the printed section total. If the section ties, every
        derived figure inside it is trusted; if it does not, the derivation stays
        untrusted and the ratio abstains rather than using a half-summed head."""
        period = pt.periods[0]
        derived_sections = {ln.section for ln in rep.bound.values()
                            if ln.provenance == "derived"}
        for section in sorted(s for s in derived_sections if s in _SECTION_TOTAL_RX):
            rx = re.compile(_SECTION_TOTAL_RX[section], re.I)
            rows = [r for r in pt.rows if sections.get(r.idx) == section]
            total = next((r.values.get(period) for r in rows
                          if r.role in ("total", "sum") and rx.search(_strip(r.label))), None)
            # A section total whose LABEL CELL was lost in extraction still exists as a
            # figure, and the binder has already recovered it arithmetically through
            # `recover_unlabelled_total` — where it was then proved by assets_split /
            # liabilities_split. Refusing to use it here means the section can never tie,
            # so every `subtotal_of_children` figure inside it stays untrusted for a reason
            # that has nothing to do with the figure. BPCL prints unlabelled sub-totals on
            # every section of its balance sheet, which is why `trade_payables` — a value
            # the statement plainly carries — was missing on 241 of 346 filings.
            recovered_from = ""
            if total is None:
                key = _SECTION_TOTAL_KEY.get(section)
                ln = rep.bound.get(key) if key else None
                if ln is not None and ln.trusted() and ln.value is not None:
                    total, recovered_from = ln.value, key

            parts = [r for r in rows if r.role not in ("total", "sum")]
            got = sum(r.values[period] for r in _topmost_valued(parts, levels, period))

            # Ind AS 105 held-for-sale balances, and Ind AS 114 regulatory deferral
            # accounts, sit inside the section ON THE PAGE but OUTSIDE its printed total
            # — which is exactly why assets_split and liabilities_split carry them as
            # their own term, and exactly why the forward section tagger mis-includes
            # them here (see `_OUTSIDE_SECTION_KEYS`). Whether a given filing prints
            # them in or out is decided by the filing, not assumed here: the plain sum
            # is tried first, and the exclusion applied only if the plain one misses.
            outside = sum(ln.value for k in _OUTSIDE_SECTION_KEYS
                          if (ln := rep.bound.get(k)) is not None
                          and ln.section == section and ln.value is not None)
            ok = total is not None and _close(got, total)
            if not ok and total is not None and outside and _close(got - outside, total):
                ok, got = True, got - outside
                recovered_from = (recovered_from + "; " if recovered_from else "") + \
                    "held-for-sale / regulatory-deferral balances excluded, per the " \
                    "printed total"

            rep.validations.append({"check": f"hierarchy_{section}",
                                    "status": "PASS" if ok else ("SKIP" if total is None else "FAIL"),
                                    "inputs": {"sum_of_rows": round(got, 2),
                                               "printed_total": total,
                                               **({"total_recovered": recovered_from}
                                                  if recovered_from else {})}})
            if ok:
                for ln in rep.bound.values():
                    if ln.provenance == "derived" and ln.section == section:
                        ln.validated = True

    # -- Layer 2b -- LLM row proposer (shadow by default) ----------------------------
    def _llm_pass(self, pt: ParsedTable, sections: dict, rep: BindingReport,
                  kind: str) -> None:
        """Offer the specs that abstained for a NAMING reason to an injected model.

        Placed after `_derive_missing`, so arithmetic has had first refusal on every gap,
        and before `_validate`, so the existing tie-outs judge whatever comes back with no
        special-casing anywhere else in this file. The model returns a ROW INDEX and the
        figure is read from the row here -- see `llm_bind` for why that is the whole
        safety argument rather than a stylistic choice.

        ONE call. NO retry. Any failure is a no-op recorded in `validations`: a filing the
        model cannot help with must still yield every figure the rules did find, and a
        retry loop is precisely how an extraction starts being fitted to the check that is
        supposed to judge it.
        """
        period = pt.periods[0]
        prior = pt.periods[1] if len(pt.periods) > 1 else None
        specs = {s.key: s for s in REGISTRY if s.statement == kind}
        attempt = {a.key: a for a in rep.attempts}

        gaps = []
        for key, spec in specs.items():
            a = attempt.get(key)
            if a is None or a.outcome != REJECTED or a.reason not in LB.ELIGIBLE_REASONS:
                continue
            # Structural aggregates are excluded for the same reason Layer 2 excludes
            # them: "Total Equity" and "Total Equity and Liabilities" are one word apart
            # and half a balance sheet apart, and `_derive_missing` together with
            # `recover_unlabelled_total` already owns that ground arithmetically.
            if spec.roles and spec.roles <= {"total", "sum"}:
                continue
            gaps.append(spec)
        if not gaps:
            return

        rows = [{"idx": r.idx, "label": (r.label or "")[:80], "role": r.role,
                 "section": sections.get(r.idx)}
                for r in pt.rows if r.label and r.values.get(period) is not None]
        if not rows:
            return

        # CONTROLS -- specs the RULES already bound, offered alongside the gaps and never
        # acted on. Proposals are only ever made where the rules failed, so a shadow run
        # scored on gaps alone can report how OFTEN the model answers but never how often
        # it is RIGHT. A control carries a known-good answer, so the same single query
        # yields a hard agreement rate -- which is the entire reason to run shadow first
        # rather than go straight to live and infer quality from downstream ratios.
        controls = [specs[k] for k, ln in rep.bound.items()
                    if k in specs and ln.provenance == "rule"] if self.shadow else []

        offered = gaps + controls
        keys = [{"key": s.key, "concept": s.concept} for s in offered]
        try:
            raw = self.propose_fn(LB.build_prompt(kind, keys, rows))
        except Exception as e:                       # noqa: BLE001 -- batch resilience
            rep.validations.append({"check": "llm_propose", "status": "SKIP",
                                    "reason": f"proposer unavailable: {type(e).__name__}"})
            return

        props = LB.parse_proposals(raw, {s.key for s in offered}, len(pt.rows))
        by_idx = {r.idx: r for r in pt.rows}
        bound_labels = {(ln.label or "").strip().lower() for ln in rep.bound.values()}
        tagged_sections = {v for v in sections.values() if v}
        gap_keys = {s.key for s in gaps}
        taken: set = set()

        for p in props:
            spec = specs[p["key"]]
            r = by_idx.get(p["row"])
            rec = {"key": p["key"], "row": p["row"], "label": r.label if r else None,
                   "role": r.role if r else None, "section": sections.get(p["row"]),
                   "confidence": p["confidence"], "why": p["why"],
                   "kind": "gap" if p["key"] in gap_keys else "control"}

            if rec["kind"] == "control":
                # Scored, never acted on -- the rule's answer stands either way.
                rec["agrees"] = bool(r and (r.label or "").strip().lower()
                                     == (rep.bound[p["key"]].label or "").strip().lower())
                rep.proposals.append(rec)
                continue

            reject = None
            if r is None or r.values.get(period) is None:
                reject = "row carries no figure in the primary period"
            elif p["row"] in taken:
                reject = "row already proposed for another key"
            elif (r.label or "").strip().lower() in bound_labels:
                reject = "row is already bound to another key"
            elif spec.roles and r.role not in spec.roles:
                reject = f"role {r.role!r} not in {sorted(spec.roles)}"
            elif (spec.section and spec.section in tagged_sections
                  and sections.get(r.idx) != spec.section):
                # Enforced only where the section WAS tagged. Where tagging failed there
                # is nothing to check against, and the bind stays untrusted regardless.
                reject = f"row sits outside {spec.section!r}, which was tagged"
            if reject:
                rec["outcome"], rec["reason"] = "DROPPED", reject
                rep.proposals.append(rec)
                continue

            taken.add(p["row"])
            rec["value"] = r.values.get(period)
            rec["outcome"] = "SHADOW" if self.shadow else "BOUND"
            if not self.shadow:
                line = BoundLine(spec.key, r.values.get(period),
                                 r.values.get(prior) if prior else None, r.label, period,
                                 sections.get(r.idx), "llm",
                                 0.9 if p["confidence"] == "high" else 0.6,
                                 pt.table_id, pt.page,
                                 detail=f"LLM row proposal ({p['why']}) -- awaiting "
                                        f"arithmetic proof")
                rep.bound[spec.key] = line
                if spec.key in rep.abstained:
                    rep.abstained.remove(spec.key)
                rep.attempts.append(BindAttempt(spec.key, BOUND, None, line.detail, "llm"))
            rep.proposals.append(rec)

        ctrl = [x for x in rep.proposals if x["kind"] == "control"]
        filled = [x for x in rep.proposals
                  if x["kind"] == "gap" and x.get("outcome") in ("SHADOW", "BOUND")]
        rep.validations.append({"check": "llm_propose", "status": "INFO", "inputs": {
            "mode": "shadow" if self.shadow else "live",
            "prompt_version": LB.PROMPT_VERSION,
            "gaps_offered": len(gaps), "gaps_filled": len(filled),
            "controls": len(ctrl),
            "control_agreement": (round(sum(1 for x in ctrl if x["agrees"]) / len(ctrl), 3)
                                  if ctrl else None)}})

    def bind_statement(self, pt: ParsedTable | None, kind: str) -> BindingReport:
        rep = BindingReport(statement=kind)
        if pt is None or not pt.periods:
            return rep
        sections = self._tag_sections(pt, rules=_CF_SECTION_RULES) if kind == "CF" else \
                   self._tag_sections(pt) if kind == "BS" else {}
        levels = _levels(pt)
        rep.sections_seen = frozenset(v for v in sections.values() if v)
        audit = self._section_audit(kind, sections)
        if audit:
            rep.validations.append(audit)
        for spec in REGISTRY:
            if spec.statement != kind:
                continue
            line, reason, detail = self._resolve(spec, pt, sections, pt.periods, levels)
            if line:
                rep.bound[spec.key] = line
                rep.attempts.append(BindAttempt(spec.key, BOUND, None, line.detail,
                                                line.provenance))
            else:
                rep.abstained.append(spec.key)
                rep.attempts.append(BindAttempt(spec.key, REJECTED, reason, detail))
        if kind == "BS":
            self._validate_hierarchy(pt, levels, sections, rep)
            self._derive_missing(kind, rep)
        if self.propose_fn:
            self._llm_pass(pt, sections, rep, kind)
        self._validate(kind, rep)
        self._detect_column_shift(pt, rep)
        return rep
