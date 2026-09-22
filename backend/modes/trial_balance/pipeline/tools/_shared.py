import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import polars as pl

from modes.trial_balance.pipeline.config import settings
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

# ==== _knowledge ====
_PACKS = {
    "anchors": "anchors/normal_side_anchors.json",
    "sensitive": "sensitive/sensitive_tags.json",
    "language": "language/safe_language_rules.json",
    "risk": "risk/rule_weights.json",
    "materiality": "materiality/benchmarks.json",
    "relationships": "relationships/expected_relationships.json",
    "assertions": "assertions/assertion_evidence_catalogue.json",
    "estimation": "estimation/estimation_keywords.json",
    "compliance": "compliance/caro_indicators.json",
}

class KnowledgePackError(Exception):
    """Raised when a pack is missing, unparseable, or missing its _meta.version.

    Deliberately a hard failure rather than a silent fallback to an empty dict: a
    tool that scored a TB against an empty keyword table would report "no sensitive
    accounts found" -- an audit-relevant false negative that looks exactly like a
    clean result. Fail loudly instead.
    """

def pack_path(name: str) -> Path:
    if name not in _PACKS:
        raise KnowledgePackError(
            f"Unknown knowledge pack {name!r}. Known packs: {', '.join(sorted(_PACKS))}."
        )
    return Path(settings.KNOWLEDGE_DIR) / _PACKS[name]

@lru_cache(maxsize=len(_PACKS) + 1)
def load_pack(name: str) -> dict:
    """Load and cache one knowledge pack by name. Validates that _meta.version is
    present -- an unversioned pack cannot be recorded in a run log, which defeats
    the point of having packs at all."""
    path = pack_path(name)
    if not path.exists():
        raise KnowledgePackError(
            f"Knowledge pack {name!r} not found at {path}. Check settings.KNOWLEDGE_DIR "
            f"(currently {settings.KNOWLEDGE_DIR})."
        )
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise KnowledgePackError(f"Knowledge pack {name!r} at {path} is not valid JSON: {e}") from e

    version = (data.get("_meta") or {}).get("version")
    if not version:
        raise KnowledgePackError(f"Knowledge pack {name!r} at {path} has no _meta.version.")
    return data

def pack_versions() -> dict:
    """Every pack's current version, plus the two Schedule III taxonomies, for the
    run log (sec 16.2). A pack that fails to load reports its error string rather than
    raising -- the run log must still be writable when something upstream is broken,
    since that is exactly when you most want a record of what ran."""
    versions = {}
    for name in sorted(_PACKS):
        try:
            versions[name] = load_pack(name)["_meta"]["version"]
        except KnowledgePackError as e:
            versions[name] = f"UNAVAILABLE: {e}"

    # The taxonomies predate this module and keep their own location/settings keys
    # (see config.py) -- surfaced here so one call covers everything a run log needs.
    for label, tax_path in (
        ("taxonomy_AS", settings.TAXONOMY_AS_PATH),
        ("taxonomy_IND_AS", settings.TAXONOMY_IND_AS_PATH),
    ):
        try:
            with open(tax_path, encoding="utf-8") as f:
                meta = (json.load(f).get("_meta") or {})
            versions[label] = meta.get("version", "unversioned")
        except Exception as e:  # noqa: BLE001 -- run log must survive a bad taxonomy file
            versions[label] = f"UNAVAILABLE: {e}"
    return versions

def debit_anchors() -> tuple:
    """Ledger-name substrings whose accounts normally carry a DEBIT closing balance."""
    return tuple(load_pack("anchors")["debit_normal"])

def credit_anchors() -> tuple:
    """Ledger-name substrings whose accounts normally carry a CREDIT closing balance."""
    return tuple(load_pack("anchors")["credit_normal"])

def normal_debit_heads() -> frozenset:
    """report_head values (lowercased) whose normal side is debit."""
    return frozenset(load_pack("anchors")["normal_debit_heads"])

def normal_credit_heads() -> frozenset:
    """report_head values (lowercased) whose normal side is credit."""
    return frozenset(load_pack("anchors")["normal_credit_heads"])

# TB-R23: the exact, triple-verified (raw client workbook, live ingested DB, live
# pipeline output) root cause of the EPIL GL 20950021 defect -- two sibling GL codes
# ("SBI- MUSCAT (US$) -R" and "...-P") sharing an identical, correct source note
# resolved to two different, contradictory FS Heads (one to Revenue, one to Cash) purely
# from an embedding/keyword search over free-text account names. "cash"/"bank" are
# deliberately a narrow SUBSET of anchors()'s full debit_normal list (not the whole list,
# which also carries weaker/more ambiguous terms like "wages" or "rent paid" that would
# false-positive far more readily against a Revenue/Expense node) -- both terms are
# themselves already members of normal_side_anchors.json's debit_normal array, so this
# stays a precise slice of the existing pack rather than a new, undocumented keyword list.
_BANK_CASH_ANCHOR_TERMS = ("cash", "bank")

# Common bank short names/abbreviations that do NOT literally contain "bank" as a
# substring (e.g. "SBI- MUSCAT (US$) -R", the exact GL name behind the EPIL defect this
# gate exists for -- "SBI" never matches _BANK_CASH_ANCHOR_TERMS on its own). Matched as
# a whole token (word boundary), not a bare substring, since 3-4 letter abbreviations are
# far more prone to accidental substring collisions than a longer term like "receivable".
# Deliberately a separate, narrowly-scoped list rather than an addition to
# normal_side_anchors.json's debit_normal array -- that list also drives TB-000's sign-
# convention detection and other consumers, and these abbreviations are not a validated
# signal for that broader purpose.
_BANK_ABBREVIATION_RE = re.compile(
    r"\b(sbi|iob|pnb|bob|boi|ubi|idbi|hdfc|icici|indusind|axis|canara|kotak|hsbc|"
    r"citibank|cb\s*bank)\b",
    re.IGNORECASE,
)

def is_bank_cash_named(gl_name: Optional[str]) -> bool:
    """True if gl_name's normalized text contains a bank/cash ledger-name anchor
    ("cash", "bank" -- both drawn from anchors()'s debit_normal list) or a known bank
    short-name/abbreviation token (_BANK_ABBREVIATION_RE). Used to flag a bank/cash-named
    account resolving to an Income/Revenue/Expense node as implausible -- see
    taxonomy_resolver.py::_try_candidate_auto and semantic_validation.py's
    _bank_cash_vs_income_conflict rule."""
    if not gl_name:
        return False
    name = str(gl_name).lower()
    if any(term in name for term in _BANK_CASH_ANCHOR_TERMS):
        return True
    return bool(_BANK_ABBREVIATION_RE.search(name))

def sensitive_rules() -> list:
    """[(tag, base_weight, (keyword, ...)), ...] -- the shape build_sensitive_detector
    already iterates, so extraction needed no call-site logic change."""
    return [
        (c["tag"], int(c["weight"]), tuple(c["keywords"]))
        for c in load_pack("sensitive")["categories"]
    ]

def sensitive_keywords(tag: str) -> tuple:
    """Just one category's keywords, for tools that screen a single tag (e.g.
    build_relationship_analytics' suspense and inter-company signals)."""
    for c in load_pack("sensitive")["categories"]:
        if c["tag"] == tag:
            return tuple(c["keywords"])
    raise KnowledgePackError(
        f"Sensitive tag {tag!r} not in the sensitive pack. Known tags: "
        f"{', '.join(c['tag'] for c in load_pack('sensitive')['categories'])}."
    )

def risk_rule_weights() -> dict:
    return dict(load_pack("risk")["rule_weights"])

def mapping_confidence_thresholds() -> tuple:
    """(high_pct, medium_pct) -- shared by build_risk_indicators' coverage block and
    build_data_sufficiency_grade, which previously defined them independently."""
    t = load_pack("risk")["mapping_confidence_thresholds"]
    return float(t["high_pct"]), float(t["medium_pct"])

def verify_packs() -> dict:
    """Load every declared pack, raising KnowledgePackError on the first failure.

    Called from backend/main.py at startup. Several packs are read at module scope
    (backend/tools/_shared.py's FCY_KEYWORDS, _safe_wording.py's
    SAFE_WORDING_DISCLAIMER) because those names are imported directly by other
    modules. A pack missing at import time would otherwise surface as
    backend/agent/agent.py's discovery walk logging "Skipping tool module ...:
    import failed" and quietly registering fewer tools -- a silent capability loss
    on an audit pipeline. Failing loudly at boot instead makes that impossible.

    Returns the same mapping as pack_versions() on success.
    """
    for name in _PACKS:
        load_pack(name)  # raises KnowledgePackError, naming the pack and path
    versions = pack_versions()
    logger.info("Knowledge packs verified: %s", ", ".join(f"{k}@{v}" for k, v in versions.items()))
    return versions




# ==== _masking ====
_GSTIN_RE = re.compile(r"\b(\d{2})([A-Z]{5}\d{4}[A-Z])(\d)([A-Z])([A-Z\d])\b")

_PAN_RE = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z])\b")

_BANK_CONTEXT = r"(?:a/?c|acct|account|bank)\s*(?:no\.?|number|#)?\s*[:\-]?\s*"

_BANK_RE = re.compile(_BANK_CONTEXT + r"(\d[\d\s-]{7,20}\d)\b", re.IGNORECASE)

_BARE_ACCOUNT_RE = re.compile(r"(?<![\d.\-])(\d{12,18})(?![\d.\-])")

_AADHAAR_RE = re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b")

_EMPLOYEE_RE = re.compile(r"\b((?:EMP|EMPL|STAFF|PF)[-/]?)(\d{3,})\b", re.IGNORECASE)

_IFSC_RE = re.compile(r"\b([A-Z]{4})0([A-Z\d]{6})\b")

def _mask_gstin(m) -> str:
    # Keep the state code and the check digits; hide the embedded PAN.
    return f"{m.group(1)}XXXXX####X{m.group(3)}{m.group(4)}{m.group(5)}"

def _mask_pan(m) -> str:
    return f"{m.group(1)[:2]}XXX####{m.group(3)}"

def _mask_bank(m) -> str:
    """Mask the account digits while preserving the context prefix that matched,
    so "Bank A/c No: 1234..." stays readable as an account reference."""
    digits = re.sub(r"\D", "", m.group(1))
    if len(digits) < 9:
        return m.group(0)  # too short to be an account number -- leave it alone
    masked = f"{'X' * (len(digits) - 4)}{digits[-4:]}"
    return m.group(0).replace(m.group(1), masked)

def _mask_aadhaar(m) -> str:
    return f"XXXX-XXXX-{m.group(3)}"

def mask_text(text, unmask_for_documentation: bool = False) -> str:
    """Mask PAN, GSTIN, Aadhaar, bank account and employee identifiers in free text.

    Order matters: GSTIN before PAN (GSTIN contains a PAN), Aadhaar before the generic
    bank-account pattern (a 12-digit Aadhaar would otherwise be masked as an account
    number and lose its 4-4-4 shape).
    """
    if unmask_for_documentation or not text:
        return text
    s = str(text)
    s = _GSTIN_RE.sub(_mask_gstin, s)
    s = _PAN_RE.sub(_mask_pan, s)
    s = _AADHAAR_RE.sub(_mask_aadhaar, s)
    s = _IFSC_RE.sub(lambda m: f"{m.group(1)}0XXXXXX", s)
    s = _EMPLOYEE_RE.sub(lambda m: f"{m.group(1)}{'X' * len(m.group(2))}", s)
    s = _BANK_RE.sub(_mask_bank, s)
    s = _BARE_ACCOUNT_RE.sub(
        lambda m: f"{'X' * (len(m.group(1)) - 4)}{m.group(1)[-4:]}", s)
    return s

def mask_value(value, unmask_for_documentation: bool = False):
    """Mask a single value, leaving non-strings (numbers, bools, None) untouched --
    masking a float would corrupt a report figure."""
    if isinstance(value, str):
        return mask_text(value, unmask_for_documentation)
    return value

def mask_structure(obj, unmask_for_documentation: bool = False):
    """Recursively mask every string inside a nested dict/list structure.

    Dict KEYS are left alone: they are schema field names, not data, and masking one
    would break every downstream lookup.
    """
    if unmask_for_documentation:
        return obj
    if isinstance(obj, dict):
        return {k: mask_structure(v, unmask_for_documentation) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        masked = [mask_structure(v, unmask_for_documentation) for v in obj]
        return tuple(masked) if isinstance(obj, tuple) else masked
    return mask_value(obj, unmask_for_documentation)

def find_identifiers(text) -> dict:
    """Report which identifier types appear in the text, without returning the values.

    Used by build_run_log to record that masking had something to act on, so a reviewer
    can tell "no identifiers present" apart from "masking never ran".
    """
    if not text:
        return {}
    s = str(text)
    return {
        name: len(pattern.findall(s))
        for name, pattern in (
            ("gstin", _GSTIN_RE), ("pan", _PAN_RE), ("aadhaar", _AADHAAR_RE),
            ("ifsc", _IFSC_RE), ("employee_id", _EMPLOYEE_RE),
            ("bank_account", _BANK_RE), ("bare_account_number", _BARE_ACCOUNT_RE),
        )
        if pattern.findall(s)
    }




# ==== _taxonomy ====
Standard = Literal["AS", "IND_AS"]

_PL_MAIN_HEAD_OVERRIDES = {
    "exceptional items": "Expense",
    "exceptional and extraordinary items": "Expense",
    "tax expense": "Expense",
    "discontinuing operations": "Expense",
    "discontinued operations": "Expense",  # IND_AS's updated wording for the same concept
    "other comprehensive income": "Income",
    # Same judgement call as "other comprehensive income" above: a net
    # movement line can legitimately be a net credit or net debit, and the
    # 5-value account_type enum has no dedicated bucket for it -> Income is
    # the closest fit, per the taxonomy's own stated PL+Revenue/Income rule.
    "net movement in regulatory deferral account balances": "Income",
}

_INCOME_MAIN_HEADS = {"revenue from operations", "other income", "revenue"}

@lru_cache(maxsize=2)
def load_taxonomy(standard: Standard) -> dict:
    path = settings.TAXONOMY_IND_AS_PATH if standard == "IND_AS" else settings.TAXONOMY_AS_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)

_IND_AS_MARKERS___taxonomy = [
    "financial assets", "financial liabilities", "right of use",
    "other comprehensive income", "ind as",
]

_AS_MARKERS___taxonomy = [
    "tangible assets", "shareholders' funds", "shareholders funds",
    "extraordinary items", "accounting standard",
]

def detect_standard(grouping_grid: list) -> Standard:
    """Scans the grouping file's own cell text for each standard's
    distinctive vocabulary and returns whichever scores more matches. Ties
    default to IND_AS (the more current/common standard)."""
    text = " ".join(
        str(cell).lower() for row in grouping_grid for cell in row if cell not in (None, "")
    )
    ind_as_score = sum(1 for m in _IND_AS_MARKERS___taxonomy if m in text)
    as_score = sum(1 for m in _AS_MARKERS___taxonomy if m in text)
    standard: Standard = "AS" if as_score > ind_as_score else "IND_AS"
    logger.info(
        "Auto-detected accounting standard: %s (IND_AS markers=%d, AS markers=%d)",
        standard, ind_as_score, as_score,
    )
    return standard

def _derive_account_type_table(standard: Standard) -> dict:
    table = {}
    for entry in load_taxonomy(standard)["taxonomy"]:
        main_head = entry["main_head"]
        key = main_head.lower()
        if entry["bs_pl"] == "BS":
            if "asset" in key:
                table[main_head] = "Asset"
            elif "liabilit" in key:
                table[main_head] = "Liability"
            elif key in ("equity", "shareholders' funds", "share application money pending allotment"):
                table[main_head] = "Equity"
        else:  # PL
            if key in _INCOME_MAIN_HEADS:
                table[main_head] = "Income"
            elif key == "expenses":
                table[main_head] = "Expense"
            elif key in _PL_MAIN_HEAD_OVERRIDES:
                table[main_head] = _PL_MAIN_HEAD_OVERRIDES[key]
        if main_head not in table:
            logger.warning(
                "No account_type rule matched taxonomy main_head %r (standard=%s) -- leaving unmapped",
                main_head, standard,
            )
    return table

@lru_cache(maxsize=2)
def _account_type_table(standard: Standard) -> dict:
    return _derive_account_type_table(standard)

_MAIN_HEAD_ALIASES = {
    "other expenses": "Expenses",
    "expense": "Expenses",
    "income": "Other income",
    "exceptional (income) / expense": "Exceptional items",
}

def derive_account_type(bs_pl: str, main_head: str, standard: Standard) -> str:
    """Pure function of (bs_pl, main_head) -- never guessed from gl_name.
    Called for every row after grouping is resolved, regardless of whether
    main_head came from a structured grouping file or from taxonomy
    selection. Anything matching neither the direct table nor the alias
    table fails loudly (logged) and returns "" rather than silently
    guessing."""
    table = _account_type_table(standard)
    if main_head in table:
        return table[main_head]
    canonical = _MAIN_HEAD_ALIASES.get(_norm___taxonomy(main_head))
    if canonical and canonical in table:
        return table[canonical]
    logger.warning(
        "derive_account_type: no rule or alias for main_head %r (bs_pl=%r, standard=%s) -- account_type left blank",
        main_head, bs_pl, standard,
    )
    return ""

@lru_cache(maxsize=2)
def build_taxonomy_tree(standard: Standard) -> list:
    """Same data as load_taxonomy()["taxonomy"], regrouped as one entry per
    main_head with its bs_pl and a nested list of sub_head_1 branches (each
    carrying only its own sub_head_2 candidates) -- so a classification
    prompt shows a tree to descend, not a flat list of (main_head,
    sub_head_1) pairs to free-associate a sub_head_2 across branches from."""
    tree = {}
    for entry in load_taxonomy(standard)["taxonomy"]:
        node = tree.setdefault(
            entry["main_head"],
            {"bs_pl": entry["bs_pl"], "main_head": entry["main_head"], "sub_heads": []},
        )
        node["sub_heads"].append(
            {"sub_head_1": entry["sub_head_1"], "sub_head_2_options": entry["sub_head_2"]}
        )
    return list(tree.values())

def _norm___taxonomy(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())

@lru_cache(maxsize=2)
def _normalized_index(standard: Standard) -> dict:
    """Maps (norm_sub_head_1, norm_sub_head_2) -> the canonical taxonomy
    strings, for case/whitespace-insensitive lookup, when that pair
    resolves unambiguously to a single main_head. bs_pl/main_head from the
    matched entry are returned, never trusted from the caller, so a caller
    echoing a lower-level label into a higher-level field is a harmless
    echo, not a validation failure -- AS LONG AS the pair is unambiguous.

    Real IND_AS data (Schedule III's Non-current/Current dual
    classification: the same investment/payable/provision category can
    legitimately be either) breaks the assumption that no sub_head_1/
    sub_head_2 pair is shared across two different main_heads -- 17 pairs
    are genuinely claimed by two different main_heads. For those, this
    index stores `None` (a real sentinel, not "key absent") so a caller
    with no main_head evidence correctly gets "no match" rather than a
    silent, non-deterministic pick of whichever entry happened to load
    last. See _main_head_scoped_index for the disambiguated lookup."""
    index = {}
    owner_by_key = {}
    for entry in load_taxonomy(standard)["taxonomy"]:
        owner = _norm___taxonomy(entry["main_head"])
        for sub2 in entry["sub_head_2"]:
            key = (_norm___taxonomy(entry["sub_head_1"]), _norm___taxonomy(sub2))
            if key in owner_by_key and owner_by_key[key] != owner:
                index[key] = None  # ambiguous without main_head evidence
            else:
                index[key] = {
                    "bs_pl": entry["bs_pl"],
                    "main_head": entry["main_head"],
                    "sub_head_1": entry["sub_head_1"],
                    "sub_head_2": sub2,
                }
            owner_by_key[key] = owner
    return index

@lru_cache(maxsize=2)
def _main_head_scoped_index(standard: Standard) -> dict:
    """(norm_main_head, norm_sub_head_1, norm_sub_head_2) -> canonical
    entry -- the disambiguating lookup for the pairs _normalized_index
    can't resolve alone. Only used when a caller actually supplies
    main_head as evidence (e.g. a client's own structured grouping
    column, or an LLM selection that named the branch it descended) --
    never guessed."""
    index = {}
    for entry in load_taxonomy(standard)["taxonomy"]:
        main_head_key = _norm___taxonomy(entry["main_head"])
        for sub2 in entry["sub_head_2"]:
            key = (main_head_key, _norm___taxonomy(entry["sub_head_1"]), _norm___taxonomy(sub2))
            index[key] = {
                "bs_pl": entry["bs_pl"],
                "main_head": entry["main_head"],
                "sub_head_1": entry["sub_head_1"],
                "sub_head_2": sub2,
            }
    return index

def snap_to_taxonomy(
    bs_pl: str, main_head: str, sub_head_1: str, sub_head_2: str, standard: Standard
) -> Optional[dict]:
    """Validates a (sub_head_1, sub_head_2) selection against the closed
    taxonomy. Returns the canonical taxonomy strings (bs_pl and main_head
    included, taken from the matched entry -- never from the bs_pl/main_head
    arguments directly), or None if the selection doesn't resolve to any
    real entry -- callers should leave the row UNMAPPED rather than accept
    an off-taxonomy invention.

    An unambiguous (sub_head_1, sub_head_2) match wins immediately,
    regardless of `main_head` (the original "harmless echo" behavior,
    unchanged for the vast majority of pairs with only one owner). Only
    when that's ambiguous or absent does `main_head` get used at all, to
    disambiguate via _main_head_scoped_index -- and only if the caller
    actually supplied it as real evidence. `bs_pl` remains accepted for
    interface-symmetry only and never affects whether a selection
    validates."""
    s1, s2 = _norm___taxonomy(sub_head_1), _norm___taxonomy(sub_head_2)
    unambiguous = _normalized_index(standard).get((s1, s2))
    if unambiguous is not None:
        return unambiguous
    if main_head:
        return _main_head_scoped_index(standard).get((_norm___taxonomy(main_head), s1, s2))
    return None



# ==== _finding_record ====
VALID_ASSERTIONS = {
    "Existence", "Rights", "Obligation", "Completeness", "Valuation",
    "Accuracy", "Cut-off", "Classification", "Presentation", "Occurrence",
    "Recoverability",
}

VALID_RISK_BASIS = {"value", "nature", "context", "relationship", "data_quality"}

VALID_RATINGS = ("high", "medium", "low", "information_request")

RATING_RANK = {"high": 3, "medium": 2, "low": 1, "information_request": 0}

def make_record(
    *,
    source_screen: str,
    observation: str,
    expectation: str,
    account: str = None,
    fsli: str = None,
    gap: str = None,
    assertions=None,
    risk_basis=None,
    risk_rating: str = "medium",
    proposed_response: str = None,
    evidence_requested=None,
    source_row_id=None,
    amount=None,
    normal_balance_expectation: str = None,
    regularity_flag: bool = False,
    data_sufficiency: str = "low",
    valid_reasons=None,
    extra: dict = None,
) -> dict:
    """Build one sec-14 finding record.

    Called by screens in-process, while they still hold the structured facts --
    reconstructing observation/expectation/gap from prose afterwards is not
    possible, which is why this is a shared constructor rather than a later
    transformation step.

    Every narrative field passes through apply_safe_wording() here, so a screen
    author cannot accidentally emit assurance language into an artifact.
    """
    rating = (risk_rating or "medium").lower()
    if rating not in VALID_RATINGS:
        rating = "medium"

    return {
        "source_screen": source_screen,
        "source_row_id": source_row_id,
        "account": account,
        "fsli": fsli,
        "amount": amount,
        "normal_balance_expectation": normal_balance_expectation,
        # sec 13's triad -- the whole point of this record shape.
        "observation": apply_safe_wording(observation or "", append_disclaimer=False),
        "expectation": apply_safe_wording(expectation or "", append_disclaimer=False),
        "gap": apply_safe_wording(gap, append_disclaimer=False) if gap else None,
        "assertion": list(assertions or []),
        "risk_basis": list(risk_basis or []),
        "regularity_flag": bool(regularity_flag),
        "risk_rating": rating,
        "data_sufficiency": data_sufficiency,
        "proposed_response": apply_safe_wording(proposed_response, append_disclaimer=False)
        if proposed_response else None,
        "evidence_requested": list(evidence_requested or []),
        # sec 7.2: the legitimate explanations must travel WITH the observation, so
        # it can never be read as a conclusion.
        "valid_reasons": list(valid_reasons or []),
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
        **(extra or {}),
    }

def validate_record(record: dict, index: int = 0) -> list:
    """Vocabulary and completeness warnings for one record. Never drops a record --
    a malformed finding still needs to reach the audit team, flagged."""
    issues = []
    label = record.get("account") or record.get("fsli") or f"record[{index}]"

    bad_assertions = set(record.get("assertion") or []) - VALID_ASSERTIONS
    if bad_assertions:
        issues.append(f"{label}: assertion(s) outside the sec-14 vocabulary: {sorted(bad_assertions)}")

    bad_basis = set(record.get("risk_basis") or []) - VALID_RISK_BASIS
    if bad_basis:
        issues.append(f"{label}: risk_basis outside the sec-8.2 vocabulary: {sorted(bad_basis)}")

    if not record.get("expectation"):
        issues.append(f"{label}: no expectation stated -- sec 13 requires observation, expectation AND gap.")
    if not record.get("evidence_requested"):
        issues.append(f"{label}: no evidence requested -- sec 13.1 requires naming the record that resolves it.")
    return issues




# ==== _ingest_shared ====
_COMPANY_KWS = ("company code", "co code", "co cd", "cocd", "company")

_CURRENCY_KWS = ("currency", "crcy", "curr.", "curr ")

_TYPE_KWS = ("account type", "acct type", "gl type", "type", "group", "category", "class", "nature", "classification")

_CODE_KWS = (
    "account code", "acc code", "gl code", "g/l acct", "g/l code", "g/l", "gl acct",
    "ledger code", "a/c code", "account no", "acc no", "a/c no", "acct", "a/c", "code",
)

_NAME_KWS = (
    "particular", "account name", "gl description", "description", "short text",
    "narration", "account", "ledger", "head of account", "name", "head", "text",
)

_DEBIT_RE = re.compile(r"\bdebit\b|\bdr\b")

_CREDIT_RE = re.compile(r"\bcredit\b|\bcr\b")

_OPENING_RE = re.compile(r"opening|carry\s*forward|carryforward|brought\s*forward|b/f|op\.?\s*bal|prev.*?period|previous")

_CLOSING_RE = re.compile(r"closing|accumulated|cumulative|carried\s*forward|c/f|cl\.?\s*bal")

_TXN_RE = re.compile(r"transaction|movement|during|rept|report|period|turnover")

_BALANCE_RE = re.compile(r"balance|amount")

_TOTAL_PREFIXES = ("total", "grand total", "sub total", "subtotal", "net total")

_SCALE_PATTERNS = [
    ("crore", re.compile(r"\bin\s+crore|\bcr\.?\b|crores?", re.I)),
    ("lakh", re.compile(r"\bin\s+lakh|lakhs?|lacs?", re.I)),
    ("million", re.compile(r"\bin\s+million|millions?|\bmn\b", re.I)),
    ("thousand", re.compile(r"\bin\s+thousand|thousands?|\b000s?\b|\bin\s+'?000", re.I)),
    ("units", re.compile(r"\bin\s+rs\.?\b|\bin\s+rupees|\bamt\.?\s+in\s+rs", re.I)),
]

def _norm___ingest_shared(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN, defensive -- shouldn't occur post-stringify
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()

def _id_role(cell: str):
    if not cell:
        return None
    cell = _norm___ingest_shared(cell)
    if any(k in cell for k in _COMPANY_KWS):
        return "company"
    if cell in ("crcy", "currency", "curr") or any(k in cell for k in _CURRENCY_KWS):
        return "currency"
    if any(k in cell for k in _TYPE_KWS):
        return "type"
    if any(k in cell for k in _CODE_KWS):
        return "code"
    if any(k in cell for k in _NAME_KWS):
        return "name"
    return None

def _money_role(cell: str):
    if not cell:
        return None
    cell = _norm___ingest_shared(cell)
    side = "debit" if _DEBIT_RE.search(cell) else "credit" if _CREDIT_RE.search(cell) else None
    if _OPENING_RE.search(cell):
        return f"opening_{side}" if side else "opening_net"
    if _CLOSING_RE.search(cell):
        return f"closing_{side}" if side else "closing_net"
    if _TXN_RE.search(cell) and _BALANCE_RE.search(cell) is None:
        return f"txn_{side}" if side else None
    if _BALANCE_RE.search(cell):
        return side or "balance"
    return side

def _detect_scale(df: pl.DataFrame, header_idx: int) -> str:
    texts = []
    for i in range(0, min(header_idx + 2, len(df))):
        texts.append(" ".join(_norm___ingest_shared(v) for v in df.row(i) if _norm___ingest_shared(v)))
    blob = " ".join(texts)
    for name, pat in _SCALE_PATTERNS:
        if pat.search(blob):
            return name
    return "unknown"

def _ffill(cells):
    out, last = [], ""
    for c in cells:
        if c:
            last = c
        out.append(last)
    return out

def _score_row(cells):
    ids = {_id_role(c) for c in cells}
    ids.discard(None)
    money = sum(1 for c in cells if _money_role(c))
    return len(ids), money

def _find_header(df: pl.DataFrame, is_grouping: bool = False):
    best, best_key = None, (-1, -1)
    limit = min(25, len(df))
    for i in range(limit):
        cells = [_norm___ingest_shared(v) for v in df.row(i)]
        n_ids, n_money = _score_row(cells)
        key = (n_money + n_ids * 2, n_ids)
        if is_grouping:
            if n_ids >= 2 and key > best_key:
                best, best_key = i, key
        else:
            if n_money >= 2 and key > best_key:
                best, best_key = i, key
    if best is None:
        return None
    two_row = False
    if best + 1 < len(df):
        nxt = [_norm___ingest_shared(v) for v in df.row(best + 1)]
        dc = sum(1 for c in nxt if _DEBIT_RE.search(c) or _CREDIT_RE.search(c))
        if dc >= 2:
            two_row = True
    return best, two_row

def _looks_text(vals):
    seen = txt = 0
    for v in vals:
        if v is None or str(v).strip() == "":
            continue
        seen += 1
        s = str(v).strip()
        try:
            n = float(re.sub(r"[,\s₹$()]+", "", s))
            if n == 0.0 and not re.fullmatch(r"-?0*\.?0*", s):
                if re.search(r"[a-zA-Z]", s):
                    txt += 1
        except Exception:
            if re.search(r"[a-zA-Z]", s):
                txt += 1
    return seen > 0 and txt / seen >= 0.6

def _detect_gl_code_and_name_columns(df: pl.DataFrame):
    """Classifies every column as a money-role or id-role column, then resolves which
    one is the GL code / GL name column -- from the id-role classification first, falling
    back to a content heuristic (mostly-text -> name, mostly-single-token -> code) over
    the non-money columns when id-role classification didn't find one or both. Returns
    (money, name_col, code_col); `money` is also needed by the caller for the
    opening/debit/credit/closing column lookups."""
    money = {}
    id_roles = {}
    for col in df.columns:
        mr = _money_role(col)
        if mr:
            money[col] = mr
        else:
            ir = _id_role(col)
            if ir:
                id_roles[col] = ir

    name_col = next((c for c, r in id_roles.items() if r == "name"), None)
    code_col = next((c for c, r in id_roles.items() if r == "code"), None)

    if name_col is None or code_col is None:
        non_money = [c for c in df.columns if c not in money]

        if name_col is None:
            for c in non_money:
                if _looks_text(df[c].head(150).to_list()):
                    name_col = c
                    break
        if code_col is None:
            for c in non_money:
                if c == name_col:
                    continue
                vals = [v.strip() for v in df[c].head(150).drop_nulls().to_list() if str(v).strip()]
                if vals and sum(1 for v in vals if " " not in v) / len(vals) >= 0.8:
                    code_col = c
                    break

    return money, name_col, code_col


def _generate_column_mapping(
    df: pl.DataFrame,
    agent_provided_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if agent_provided_mapping and agent_provided_mapping.get("gl_code") and agent_provided_mapping.get("gl_name"):
        return {k: _norm___ingest_shared(v) for k, v in agent_provided_mapping.items() if v}

    mapping = {
        "gl_code": None,
        "gl_name": None,
        "opening_balance": None,
        "debit": None,
        "credit": None,
        "closing_balance": None,
    }

    money, name_col, code_col = _detect_gl_code_and_name_columns(df)

    mapping["gl_code"] = code_col
    mapping["gl_name"] = name_col

    op_net = next((c for c, r in money.items() if r in ["opening_net", "opening_debit", "opening_credit"]), None)
    if op_net:
        mapping["opening_balance"] = op_net

    deb_col = next((c for c, r in money.items() if r in ("debit", "txn_debit")), None)
    cred_col = next((c for c, r in money.items() if r in ("credit", "txn_credit")), None)
    if deb_col:
        mapping["debit"] = deb_col
    if cred_col:
        mapping["credit"] = cred_col

    cl_net = next((c for c, r in money.items() if r in ["closing_net", "balance", "closing_debit", "closing_credit"]), None)
    if cl_net:
        mapping["closing_balance"] = cl_net

    return mapping

def _validate_input_file(file_path: Path):
    if not file_path.exists():
        raise ValueError("FILE_NOT_FOUND")
    ext = file_path.suffix.lower()
    if ext not in [".csv", ".xlsx", ".xls"]:
        raise ValueError("UNSUPPORTED_FILE_TYPE")
    return ext

def _stringify_grid(df: pl.DataFrame) -> pl.DataFrame:
    """Casts every column to Utf8 so downstream header-detection/role
    heuristics see plain text uniformly regardless of how the read engine
    typed each column (see module docstring)."""
    if df.width == 0:
        return df
    return df.select([pl.col(c).cast(pl.Utf8, strict=False) for c in df.columns])

def _load_workbook(file_path: Path, ext: str):
    """Returns the CSV file path unchanged for `.csv`; for `.xlsx`/`.xls`,
    eagerly loads every sheet via polars (calamine engine) as
    `dict[str, pl.DataFrame]` -- one read of the whole workbook instead of
    a per-sheet call, matching TB_ingestion/scripts/tb_grouping_polars.py's
    own convention. `read_options={"skip_rows": 0}` is required: fastexcel's
    default (when `has_header=False`) silently skips leading blank rows,
    which would otherwise shift every row index used downstream (header
    detection, merge-range offsets, audit finding row numbers)."""
    if ext == ".csv":
        return file_path
    try:
        return pl.read_excel(
            file_path,
            sheet_id=0,
            has_header=False,
            drop_empty_rows=False,
            drop_empty_cols=False,
            infer_schema_length=None,
            read_options={"skip_rows": 0},
        )
    except Exception as e:
        raise ValueError(f"EXCEL_LOAD_ERROR: {str(e)}")

def _detect_formula_cells(file_path: Path) -> dict:
    """Layer-4 'Formula Flag' support: a second, separate openpyxl read with
    data_only=False (raw/uncomputed cell content) to detect which cells hold
    a formula. Every value-loading path elsewhere in this module uses
    data_only=True (polars calamine engine, and the two openpyxl reads in
    _enumerate_sheets/_apply_merged_cells above) and can therefore never see
    formula text, only the last-computed result -- this is why formula
    detection needs its own dedicated read rather than reusing an existing one.

    Returns {sheet_name: [{"cell": "B4", "formula": "=SUM(...)"}]}, empty for
    CSV (no formula concept) or on any read failure -- this is informational
    only (Layer 4), so it must never raise or block the caller."""
    ext = file_path.suffix.lower()
    if ext not in (".xlsx", ".xls"):
        return {}
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=False, read_only=True)
        result = {}
        for sheet in wb.worksheets:
            cells = [
                {"cell": cell.coordinate, "formula": cell.value}
                for row in sheet.iter_rows()
                for cell in row
                if isinstance(cell.value, str) and cell.value.startswith("=")
            ]
            if cells:
                result[sheet.title] = cells
        wb.close()
        return result
    except Exception:
        return {}

def _enumerate_sheets(workbook, ext: str, file_path: Path):
    sheets_info = []
    if ext == ".csv":
        sheets_info.append({"name": "Sheet1", "hidden": False})
    else:
        hidden_sheets = set()
        if ext == ".xlsx":
            try:
                import openpyxl

                wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
                for sheet in wb.worksheets:
                    if sheet.sheet_state != "visible":
                        hidden_sheets.add(sheet.title)
                wb.close()
            except Exception:
                pass  # hidden-sheet detection is a nice-to-have, not required for a usable preview

        for sheet_name in workbook.keys():
            sheets_info.append({"name": sheet_name, "hidden": sheet_name in hidden_sheets})
    return sheets_info

def _apply_merged_cells(rows: list, file_path: Path, sheet_name: str) -> list:
    """Forward-fills merged-cell ranges (openpyxl merge topology only, not
    data -- see module docstring) into a mutable list-of-lists grid. Requires
    a full (non-read_only) openpyxl load: read_only worksheets don't expose
    `.merged_cells` at all. Silently no-ops on any failure, matching the old
    pandas-path behavior (a workbook that can't be re-opened by openpyxl for
    any reason still gets its unmerged polars-read grid, not a hard error)."""
    if not rows:
        return rows
    n_rows, n_cols = len(rows), len(rows[0])
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb[sheet_name]
        for merged_range in ws.merged_cells.ranges:
            min_col, min_row, max_col, max_row = merged_range.bounds
            if min_row - 1 < n_rows and min_col - 1 < n_cols:
                top_left_val = rows[min_row - 1][min_col - 1]
                for r in range(min_row - 1, max_row):
                    for c in range(min_col - 1, max_col):
                        if r < n_rows and c < n_cols:
                            rows[r][c] = top_left_val
        wb.close()
    except Exception:
        pass  # see docstring: silently no-ops, matching the old pandas-path behavior
    return rows

def _extract_sheet_dataframe(workbook, sheet_name: str, ext: str, file_path: Path) -> pl.DataFrame:
    if ext == ".csv":
        try:
            df = pl.read_csv(workbook, has_header=False, infer_schema_length=None, encoding="utf8-lossy")
        except Exception as e:
            raise ValueError(f"CSV_PARSE_ERROR: {str(e)}")
        return _stringify_grid(df)

    # .xlsx / .xls -- `workbook` is the dict[str, pl.DataFrame] from _load_workbook
    try:
        df = workbook[sheet_name]
    except KeyError as e:
        raise ValueError(f"WORKSHEET_LOAD_ERROR: {str(e)}")

    if ext == ".xlsx":
        # Merge-topology forward-fill (.xls merge handling dropped -- calamine
        # reads legacy .xls via the same pl.read_excel call as .xlsx/no merge
        # metadata was ever reliably available for .xls in this codebase).
        rows = [list(r) for r in df.rows()]
        rows = _apply_merged_cells(rows, file_path, sheet_name)
        df = pl.DataFrame(rows, schema=df.columns, orient="row", strict=False)

    return _stringify_grid(df)

def _dedupe_names(names) -> list:
    """Suffix-numbers repeated names (including repeated blanks). Used both
    by _normalize_duplicate_columns and by any caller building a candidate
    column-name list (e.g. a merged two-row header) BEFORE handing it to
    polars -- unlike pandas, polars' column-rename API rejects duplicate
    names outright, so callers must dedupe first rather than relying on a
    later cleanup pass."""
    seen = {}
    out = []
    for name in names:
        name_str = str(name)
        if name_str in seen:
            seen[name_str] += 1
            out.append(f"{name_str}_{seen[name_str]}")
        else:
            seen[name_str] = 0
            out.append(name_str)
    return out

def _normalize_duplicate_columns(df: pl.DataFrame) -> pl.DataFrame:
    new_cols = _dedupe_names(df.columns)
    return df.rename(dict(zip(df.columns, new_cols)))

def _generate_headers_if_missing(df: pl.DataFrame):
    """Polars always auto-names unheadered columns "column_N" (never bare
    ints like pandas' default RangeIndex), so this mostly guards a header
    row that was itself literally all-digit strings (e.g. a stray numeric
    row mistaken for a header upstream)."""
    generated = False
    if all(str(c).isdigit() for c in df.columns):
        df = df.rename({c: f"column_{i + 1}" for i, c in enumerate(df.columns)})
        generated = True
    return df, generated

def validate_dataframe_schema(df: pl.DataFrame, tool_name: str):
    """Defensive schema check before Parquet export. Polars columns are
    always concretely typed (no pandas-style ambiguous 'object' dtype to
    infer over), so this mostly guards against an all-null column landing
    as pl.Null (some parquet readers reject it) by coercing to Utf8, and
    against a stray pl.Object column (should never occur given this
    codebase never constructs one) by stringifying it.

    Unlike the old pandas version, this cannot mutate `df` in place (polars
    frames are immutable) -- returns (errors, df); callers must capture the
    (possibly-adjusted) returned frame."""
    errors = []
    if df.is_empty():
        return errors, df

    for col, dtype in zip(df.columns, df.dtypes):
        if dtype == pl.Null:
            errors.append(
                {
                    "column": col,
                    "expected": "uniform_type",
                    "actual": "empty",
                    "action": "Normalized all-null column to string.",
                }
            )
            df = df.with_columns(pl.col(col).cast(pl.Utf8))
        elif dtype == pl.Object:
            errors.append(
                {
                    "column": col,
                    "expected": "uniform_type",
                    "actual": "object",
                    "action": "Normalized object column to string.",
                }
            )
            df = df.with_columns(
                pl.col(col).map_elements(lambda x: str(x) if x is not None else None, return_dtype=pl.Utf8)
            )

    return errors, df

_NUMERIC_DTYPES = (
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Float32, pl.Float64,
)

class WorksheetClassifier:
    """Classifies a worksheet into exactly one functional role based on its
    content: TRIAL_BALANCE, GROUPING, DETAIL_DATA, FINANCIAL_STATEMENT,
    MAPPING, METADATA, OTHER."""

    ROLES = [
        "TRIAL_BALANCE",
        "GROUPING",
        "DETAIL_DATA",
        "FINANCIAL_STATEMENT",
        "MAPPING",
        "METADATA",
        "OTHER",
    ]

    def __init__(self):
        self.re_gl_code = re.compile(r"gl\s*code|gl\s*account|account\s*no|account\s*number|acct", re.IGNORECASE)
        self.re_gl_desc = re.compile(r"gl\s*name|account\s*name|account\s*desc|description", re.IGNORECASE)
        self.re_opening = re.compile(r"open(?:ing)?\s*bal(?:ance)?", re.IGNORECASE)
        self.re_debit = re.compile(r"debit|dr\.?", re.IGNORECASE)
        self.re_credit = re.compile(r"credit|cr\.?", re.IGNORECASE)
        self.re_closing = re.compile(r"clos(?:ing)?\s*bal(?:ance)?", re.IGNORECASE)

        self.re_dimensions = re.compile(
            r"cost\s*center|profit\s*center|business\s*area|company\s*code|segment|functional\s*area", re.IGNORECASE
        )

        self.re_fs = re.compile(r"balance\s*sheet|profit\s*&?\s*loss|p&l|cash\s*flow|notes?", re.IGNORECASE)

        self.re_meta = re.compile(r"parameter|setting|legend|instruction|config", re.IGNORECASE)

    def classify_worksheet(self, df: pl.DataFrame, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        if df is None or df.is_empty():
            return {"role": "OTHER", "confidence": 1.0, "reasons": ["Dataframe is empty."], "warnings": []}

        columns = [str(c) for c in df.columns]

        scores = {role: 0.0 for role in self.ROLES}
        reasons_map = {role: [] for role in self.ROLES}

        has_gl_code = any(self.re_gl_code.search(c) for c in columns)
        has_gl_desc = any(self.re_gl_desc.search(c) for c in columns)
        has_balances = (
            any(self.re_debit.search(c) for c in columns)
            or any(self.re_credit.search(c) for c in columns)
            or any(self.re_opening.search(c) for c in columns)
            or any(self.re_closing.search(c) for c in columns)
        )

        dim_cols = [c for c in columns if self.re_dimensions.search(c)]
        has_dimensions = len(dim_cols) > 0

        sample_df = df.head(100)
        numeric_cols_count = 0
        for col in df.columns:
            if df[col].dtype in _NUMERIC_DTYPES:
                numeric_cols_count += 1
            else:
                s = [str(v).replace(",", "").replace(".", "", 1) for v in sample_df[col].drop_nulls().to_list()]
                if s and sum(1 for v in s if v.isnumeric()) / len(s) > 0.8:
                    numeric_cols_count += 1

        gl_col = next((c for c in columns if self.re_gl_code.search(c)), None)
        has_duplicate_gls = False
        if gl_col and gl_col in df.columns:
            gl_sample = sample_df[gl_col].drop_nulls()
            if len(gl_sample) > 0 and len(gl_sample) > gl_sample.n_unique():
                has_duplicate_gls = True

        self._score_candidate_roles(
            scores, reasons_map, df, columns, has_gl_code, has_balances, numeric_cols_count,
            has_duplicate_gls, has_dimensions, dim_cols, gl_col, has_gl_desc,
        )

        best_role = "OTHER"
        best_score = 0.0

        for role, score in scores.items():
            if score > best_score:
                best_score = score
                best_role = role

        if best_score < 0.4:
            best_role = "OTHER"
            best_score = 1.0
            reasons_map["OTHER"].append("No other role reached the minimum confidence threshold.")

        confidence = min(0.99, best_score)

        return {
            "role": best_role,
            "confidence": round(confidence, 2),
            "reasons": reasons_map[best_role],
            "warnings": [],
        }

    def _score_candidate_roles(self, scores, reasons_map, df, columns, has_gl_code, has_balances,
                                numeric_cols_count, has_duplicate_gls, has_dimensions, dim_cols,
                                gl_col, has_gl_desc):
        """Adds this worksheet's evidence-based score/reason contributions for each of the six
        candidate roles. Mutates `scores`/`reasons_map` in place (the same dicts classify_worksheet
        already holds) -- every rule below is independent of the others, so this is pure
        accumulation, not a decision by itself; classify_worksheet picks the winner afterward."""
        if has_gl_code:
            scores["TRIAL_BALANCE"] += 0.3
            reasons_map["TRIAL_BALANCE"].append("Contains GL Code column.")
        if has_balances:
            scores["TRIAL_BALANCE"] += 0.4
            reasons_map["TRIAL_BALANCE"].append("Contains financial balance columns (Dr/Cr/Open/Close).")
        if numeric_cols_count >= 2:
            scores["TRIAL_BALANCE"] += 0.2
            reasons_map["TRIAL_BALANCE"].append("Contains multiple numeric columns.")
        if gl_col and not has_duplicate_gls:
            scores["TRIAL_BALANCE"] += 0.1
            reasons_map["TRIAL_BALANCE"].append("GL accounts appear mostly unique.")

        if has_gl_code:
            scores["DETAIL_DATA"] += 0.2
            reasons_map["DETAIL_DATA"].append("Contains GL Code column.")
        if has_dimensions:
            scores["DETAIL_DATA"] += 0.5
            reasons_map["DETAIL_DATA"].append(f"Contains dimension columns: {', '.join(dim_cols)}.")
        if has_duplicate_gls:
            scores["DETAIL_DATA"] += 0.3
            reasons_map["DETAIL_DATA"].append("Multiple rows exist for the same GL account.")

        if not has_balances and numeric_cols_count <= 1:
            scores["GROUPING"] += 0.3
            reasons_map["GROUPING"].append("Lacks numeric balance columns.")
        if has_gl_desc or any(re.search(r"head|item", c, re.IGNORECASE) for c in columns):
            scores["GROUPING"] += 0.3
            reasons_map["GROUPING"].append("Contains Description/Line Item/FS Head columns.")
        if gl_col:
            scores["GROUPING"] += 0.2
            reasons_map["GROUPING"].append("Contains GL reference for mapping.")
        if len(columns) <= 5:
            scores["GROUPING"] += 0.2
            reasons_map["GROUPING"].append("Narrow column structure typical of groupings.")

        if any(self.re_fs.search(c) for c in columns):
            scores["FINANCIAL_STATEMENT"] += 0.6
            reasons_map["FINANCIAL_STATEMENT"].append("Headers contain Financial Statement keywords.")
        if not has_gl_code:
            scores["FINANCIAL_STATEMENT"] += 0.2
            reasons_map["FINANCIAL_STATEMENT"].append("Lacks GL Code column (presentation level).")

        if len(columns) == 2 and not has_balances:
            scores["MAPPING"] += 0.5
            reasons_map["MAPPING"].append("Exactly 2 columns without balances (typical cross-reference).")

        if any(self.re_meta.search(c) for c in columns):
            scores["METADATA"] += 0.6
            reasons_map["METADATA"].append("Contains metadata keywords (Parameters/Settings).")
        if len(df) < 20 and numeric_cols_count == 0:
            scores["METADATA"] += 0.3
            reasons_map["METADATA"].append("Very few rows with mostly text.")

def _normalize_gl_code(code):
    """Keeps only the trailing digit run of a GL code as the canonical join key."""
    if code is None:
        return None
    code_str = str(code).strip()
    code_str = "".join(c for c in code_str if c.isprintable()).strip()
    if code_str.lower() == "nan" or not code_str:
        return None
    match = re.search(r"(\d+)$", code_str)
    return match.group(1) if match else code_str

def _fy_year(value) -> Optional[int]:
    """Extracts a 4-digit year from a FY start/end value that may be a
    datetime-like object, an ISO string, or a bare year string/int."""
    if value is None:
        return None
    if hasattr(value, "year"):
        return int(value.year)
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    match = re.search(r"(\d{4})", s)
    return int(match.group(1)) if match else None

def build_tb_doc_id(
    company_name: Optional[str] = None,
    fy_start=None,
    fy_end=None,
    financial_year: Optional[str] = None,
    fallback_bytes: bytes = None,
) -> str:
    """Deterministic tb_doc_id matching TB_ingestion/scripts/ingest_to_db.py's
    scheme, so a document processed through TB-v2 for a given company+FY
    resolves to the SAME id already used by that company+FY's MAIN document
    (if one exists), instead of an unrelated file-hash. Re-processing the
    same company+FY is then a natural replace, not a duplicate.

    Company name is normalized (uppercased, whitespace collapsed to "_") to
    match ingest_to_db.py's convention. Falls back to
    f"{COMPANY_NAME}_{FINANCIAL_YEAR}" when FY start/end aren't both
    resolvable, and to a random-hash id (with a logged warning -- this is a
    degraded, non-deterministic id, not a silent success) only when even the
    company name is missing."""
    name = re.sub(r"\s+", "_", str(company_name).strip().upper()) if company_name else None
    start_year = _fy_year(fy_start)
    end_year = _fy_year(fy_end)

    if name and start_year and end_year:
        return f"{name}_{start_year}_{end_year}"
    if name and financial_year:
        return f"{name}_{str(financial_year).strip()}"

    logger.warning(
        "build_tb_doc_id: insufficient company/FY info (company_name=%r, fy_start=%r, "
        "fy_end=%r, financial_year=%r) -- falling back to a non-deterministic hash id",
        company_name, fy_start, fy_end, financial_year,
    )
    basis = fallback_bytes if fallback_bytes is not None else os.urandom(16)
    # Short document-id fingerprint, not a security hash -- usedforsecurity=False
    # documents that for bandit/CWE-327 scans (reviewed 2026-09).
    return "TB-" + hashlib.sha1(basis, usedforsecurity=False).hexdigest()[:16]




# ==== _report_schedules ====
SCHEDULES = {
    # Engagement identity. First sheet in the workbook and the first thing a reviewer
    # opening the file cold needs: which entity, which period, which kind of review, and
    # on what basis the figures below may be read at all (currency/scale, framework,
    # data sufficiency). Previously the workbook opened straight into control totals with
    # nothing naming the client anywhere in it.
    "Engagement": {
        "section": 2, "artifact": "tb_metadata.json",
        "contains": "Entity identity, review type, period, and the reading basis for every "
                    "figure in this workbook (currency, scale, framework, data sufficiency)",
    },
    # ── existing sheets, retained ─────────────────────────────────────────────
    "Data Quality": {
        "section": 3, "artifact": "layer1_results.json",
        "contains": "Layer-1 rule outcomes and control totals",
    },
    "Materiality": {
        "section": 6, "artifact": "materiality.json",
        "contains": "Benchmark waterfall, thresholds and material populations",
    },
    # The arithmetic behind the Materiality sheet, shown rather than asserted. An auditor
    # must be able to re-perform the benchmark selection against signed financials and see
    # exactly which input produced which number -- so that where a figure disagrees, the
    # disagreement can be traced to the trial balance or grouping that fed it rather than
    # left as an unexplained difference against the tool.
    "Benchmark Build-Up": {
        "section": 6, "artifact": "snapshot_drilldown.parquet",
        "contains": "Every balance summing into each materiality benchmark, at the level the "
                    "financial statements present them, with the tie to the figure the "
                    "threshold was computed from and what to do where it does not agree",
    },
    "Materiality Workings": {
        "section": 6, "artifact": "materiality.json",
        "contains": "Every benchmark considered, its base amount, the percentage applied, "
                    "the resulting figure, why it was or was not selected, and the "
                    "re-performance steps for checking the selection against signed accounts",
    },
    # FSLI Summary was retired as a separate sheet: it duplicated Canonical TB's GL rows
    # under a DIFFERENT taxonomy (fsli_summary.parquet's raw main_head/sub_head_1 text vs
    # Canonical TB's classify_row()-normalized FS Head), so a user's SUMIFS/PivotTable
    # against one could never tie to the other despite both columns being labeled the same.
    # Canonical TB now carries native Excel SUBTOTAL() rows and row grouping (the +/-
    # outline buttons) at both FS-Head and FSLI level, directly over its own GL rows --
    # one taxonomy, one sheet, every total a live formula instead of a second asserted value.
    "Canonical TB": {
        "section": 8, "artifact": "canonical_tb.parquet",
        "contains": "The complete normalised trial balance, every ledger account, with "
                    "FS-Head and FSLI subtotals as collapsible Excel row groups "
                    "(current year, in a comparative report)",
    },
    "Exception Register": {
        "section": 17, "artifact": "consolidated_exceptions.json",
        "contains": "Correlated exceptions with scoring and cluster membership, broken out to "
                    "member-account detail with each account's specific risk theme, the reason "
                    "it was flagged, and a native Excel subtotal per cluster",
    },
    "Movement (BS)": {
        "section": 8, "artifact": "canonical_tb.parquet",
        "contains": "Opening to closing movement per balance-sheet account",
    },
    "Risk Indicators": {
        "section": 10, "artifact": "risk_indicators.parquet",
        "contains": "Composite risk score and triggered rules per account",
    },
    "Sensitive Accounts": {
        "section": 12, "artifact": "sensitive_accounts.parquet",
        "contains": "Accounts matching a sensitive category, with sensitivity score",
    },
    "Unmapped Accounts": {
        "section": 5, "artifact": "canonical_tb.parquet",
        "contains": "Accounts with no confirmed FSLI mapping",
    },
    "Estimation Exposure": {
        "section": 13, "artifact": "estimation_exposure.json",
        "contains": "Balances resting on management estimate or judgement",
    },
    "FX Exposure": {
        "section": 13, "artifact": "fx_exposure.json",
        "contains": "Monetary and non-monetary foreign-currency exposure",
    },
    "Mapping Quality": {
        "section": 5, "artifact": "mapping_quality.json",
        "contains": "Grouping-taxonomy quality flags and mapping coverage",
    },
    "Legend": {
        "section": 23, "artifact": "n/a -- reference sheet only, no backing artifact file",
        "contains": "Every High/Medium/Low(/Critical/Information Request) classification scale "
                    "used anywhere in this workbook, with its thresholds and how it is computed",
    },

    # ── Phase-2 populations, new ──────────────────────────────────────────────
    # Findings Register + Evidence Requests + Management Queries consolidated into 2
    # sheets (not 3): a finding's own management query is a pure function of that SAME
    # finding record (see _build_management_query), so it renders as extra columns on the
    # finding's own row rather than a third sheet a reader must go and re-correlate by
    # account name. Evidence Requests keeps its own sheet -- it is a genuinely different
    # grain (de-duplicated ACROSS findings, one row per distinct document/confirmation to
    # actually go and request), which is exactly how an auditor works: one combined
    # request per document, not one per finding that happens to cite it.
    "Findings & Queries Register": {
        "section": 17, "artifact": "finding_records.json",
        "contains": "Every finding in full: observation, expectation, gap, assertion, risk "
                    "basis, regularity flag, proposed response, evidence-request cross-"
                    "references, and -- inline, in its own clearly-labelled columns -- the "
                    "management query for any finding with a plausible innocent explanation "
                    "to put to management",
    },
    "Evidence Request Register": {
        "section": 19, "artifact": "evidence_request_list.json",
        "contains": "De-duplicated evidence requests, risk-ordered, grouped by audit area, "
                    "plus data/scope information requests -- each row's ID cross-referenced "
                    "from the Findings & Queries Register",
    },
    "Assertion Map": {
        "section": 11, "artifact": "assertion_evidence_map.json",
        "contains": "Account area to assertion to named resolving record",
    },
    "Counterpart Gaps": {
        "section": 9, "artifact": "counterpart_screen.json",
        "contains": "Expected pairs whose counterpart is absent or immaterial",
    },
    "Relationship Expectations": {
        "section": 9, "artifact": "relationship_expectations.json",
        "contains": "Observed ratio against plausible band, with the quantified gap",
    },
    "Audit Ratios": {
        "section": 9, "artifact": "audit_ratio_pack.json",
        "contains": "Debtor/creditor/inventory intensity and their day-count restatements, "
                    "depreciation, finance-cost, employee-cost, operating-expense and "
                    "gross-margin proxies, each traced to its mapped TB component",
    },
    "Sample Selection": {
        "section": 22, "artifact": "sample_selection.json",
        "contains": "SA 530 Monetary Unit and Stratified Random samples, with every parameter "
                    "(reliability factor, sampling interval, random seed) recorded so the "
                    "sample is reproducible from this sheet alone",
    },
    "Abnormal Signs": {
        "section": 3, "artifact": "abnormal_sign_screen.json",
        "contains": "Accounts on the opposite side to their class, contra accounts separated",
    },
    "Statutory Screen": {
        "section": 15, "artifact": "statutory_screen.json",
        "contains": "GST, TDS and PF/ESI reconciled against the bases they arise from",
    },
    "Public Sector Lens": {
        "section": 12, "artifact": "public_sector_lens.json",
        "contains": "The four regularity and propriety questions, by account",
    },
    "CARO Indicators": {
        "section": 15, "artifact": "caro_indicators.json",
        "contains": "CARO 2020 and Companies Act indicators with evidence to request",
    },
    "Override Indicators": {
        "section": 14, "artifact": "override_indicators.json",
        "contains": "Management-override and irregularity indicators, planning stage",
    },
    "Going Concern": {
        "section": 16, "artifact": "going_concern_screen.json",
        "contains": "Going-concern indicators with the computed basis for each",
    },

    # ── comparison-only schedules ─────────────────────────────────────────────
    # Present only in the comparative workbook. Sheets are conditional on their
    # backing artifact, so listing them here costs a single-TB run nothing -- they
    # simply never get created, never enter its manifest, and are never referenced.
    "Variance": {
        "section": 7, "artifact": "comparison_variance.parquet",
        "contains": "PY to CY closing-balance variance per ledger, with movement flag",
    },
    "New Ledgers (CY)": {
        "section": 7, "artifact": "structural_delta.json",
        "contains": "Ledgers present in CY that did not exist in PY",
    },
    "Removed Ledgers (PY)": {
        "section": 7, "artifact": "structural_delta.json",
        "contains": "Ledgers present in PY that no longer appear in CY",
    },
    "Continuity Breaks": {
        "section": 3, "artifact": "precheck_results.json",
        "contains": "Accounts where PY closing does not carry into CY opening",
    },
    "Sign Convention": {
        "section": 3, "artifact": "sign_convention_flags.json",
        "contains": "PY/CY sign-convention consistency per ledger",
    },
    "Ratio Trend (PY vs CY)": {
        "section": 8, "artifact": "financial_ratios.json",
        "contains": "PY value, CY value and direction of movement for each computed ratio",
    },
    "Canonical TB (CY)": {
        "section": 25, "artifact": "canonical_tb.parquet",
        "contains": "The complete current-year normalised trial balance",
    },
    "Canonical TB (PY)": {
        "section": 25, "artifact": "canonical_tb.parquet",
        "contains": "The complete prior-year normalised trial balance",
    },
}

MANIFEST_FILENAME = "excel_schedule_manifest.json"

_EXCEL_SHEET_NAME_LIMIT = 31

def validate_registry() -> list:
    """Sheet names Excel would reject. Called by the Excel writer before it starts,
    so a bad registry entry fails at once rather than part-way through a workbook."""
    problems = []
    for name in SCHEDULES:
        if len(name) > _EXCEL_SHEET_NAME_LIMIT:
            problems.append(f"{name!r} is {len(name)} chars; Excel's limit is {_EXCEL_SHEET_NAME_LIMIT}")
        bad = set(name) & set(r":\/?*[]")
        if bad:
            problems.append(f"{name!r} contains characters Excel forbids: {sorted(bad)}")
    return problems

def write_manifest(out_dir, sheets_written, workbook_filename: str) -> Path:
    """Record which sheets the workbook actually contains, for the Word writer.

    Written even when empty: an empty manifest tells the Word writer "the workbook
    ran and produced nothing", which is a different statement from "no manifest, so
    the workbook may not have run at all". The document says the right thing in each
    case only if it can tell them apart.
    """
    out_dir = Path(out_dir)
    payload = {
        "workbook": workbook_filename,
        "sheet_count": len(sheets_written),
        "sheets": [
            {
                "name": name,
                "section": SCHEDULES[name]["section"],
                "contains": SCHEDULES[name]["contains"],
                "rows": rows,
            }
            for name, rows in sheets_written
            if name in SCHEDULES
        ],
        "unregistered_sheets": [n for n, _ in sheets_written if n not in SCHEDULES],
    }
    path = out_dir / MANIFEST_FILENAME
    write_json_atomic(payload, path, indent=2)
    return path

def read_manifest(out_dir) -> dict:
    """What the Word writer cross-references. Returns an empty structure rather than
    raising when the workbook was not produced -- the document must still render, and
    says so in place of the schedule references."""
    path = Path(out_dir) / MANIFEST_FILENAME
    if not path.exists():
        return {"workbook": None, "sheet_count": 0, "sheets": [], "unregistered_sheets": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"workbook": None, "sheet_count": 0, "sheets": [], "unregistered_sheets": []}

def sheets_for_section(manifest: dict, section: int) -> list:
    """Sheets supporting one report section, for the inline pointer the Word writer
    places in that section."""
    return [s for s in manifest.get("sheets", []) if s.get("section") == section]

def reference_line(manifest: dict, section: int) -> str:
    """The inline cross-reference sentence for a section, or "" when no sheet backs it.

    Returning "" rather than a placeholder matters: a section with no schedule should
    read as complete in itself, not as though a reference went missing.
    """
    sheets = sheets_for_section(manifest, section)
    if not sheets:
        return ""
    workbook = manifest.get("workbook") or "the accompanying workbook"
    if len(sheets) == 1:
        s = sheets[0]
        return (f"Full population: see sheet '{s['name']}' in {workbook} "
                f"({s['rows']:,} row(s)) — {s['contains']}.")
    named = "; ".join(f"'{s['name']}' ({s['rows']:,} rows)" for s in sheets)
    return f"Full population: see sheets {named} in {workbook}."




# ==== _report_sections ====
_SECTION_CAP = 3  # narrative rows per section; the full population lives in Excel.
# Was 15 -- dropped to keep the Word report to its 5-8 page budget (Excel already holds
# the complete population for every capped section; the Word report's job is the top few
# and a pointer, not a second copy of the workbook).

def _load(out_dir, name):
    p = Path(out_dir) / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def _unavailable(add_note, what, why):
    """State what is missing and why, in place.

    A silently absent section reads as "nothing to report", which is the opposite of
    the truth when the screen never ran or the data cannot support it. The
    specification's data-sufficiency discipline requires the distinction to be visible.
    """
    add_note(f"{what} — not available. {why}")

def _capped(add_note, rows, section, manifest):
    """Disclose the cap in place, every time one is applied.

    The pre-Phase-4 reports already showed why this matters: a cluster cap was
    disclosed in two writers and not the third, so the same run rendered 13 clusters
    in one section and 10 in another with nothing to explain the difference.
    """
    if len(rows) > _SECTION_CAP:
        add_note(f"Showing the top {_SECTION_CAP} of {len(rows)} — the complete population "
                 f"is in the workbook (see the reference below this section).")
        return rows[:_SECTION_CAP]
    return rows

def _write_phase4_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                           add_bullets, safe_fmt, render_sensitive=None,
                           render_focus_areas=None, render_focus_sequence=None):
    def close(section):
        """Every section ends with its schedule reference, without exception."""
        line = reference_line(manifest, section)
        if line:
            add_note(line)

    # Read by sections 17, 18 and 22 below.
    fr = _load(out_dir, "finding_records.json")

    # ── 12. Sensitive and public-sector accounts ─────────────────────────────
    def _section_12_sensitive_and_public_sector():
        doc.add_heading("12. Sensitive and Public-Sector Accounts", level=1)
        if render_sensitive:
            render_sensitive()
        ps = _load(out_dir, "public_sector_lens.json")
        if ps:
            applic = ps.get("applicability", {})
            add_body(
                "Public money makes context decisive: the accounts below are raised by what they "
                "represent, not by size. A small write-off still requires competent sanction."
            )
            add_note(f"Government-company applicability: {applic.get('basis', 'unconfirmed')}. "
                     f"{applic.get('note', '')}")
            qrows = [[q["question"], str(v["accounts_matched"]), safe_fmt(v["total_balance"])]
                     for q, v in ((v, v) for v in ps.get("questions", {}).values())]
            if qrows:
                add_table(["Regularity question", "Accounts", "Total balance"], qrows)
        else:
            _unavailable(add_note, "Public-sector regularity lens",
                         "build_public_sector_lens did not run for this dataset.")
        close(12)

    _section_12_sensitive_and_public_sector()

    # ── 13. Estimates and judgemental balances ───────────────────────────────
    def _section_13_estimates():
        doc.add_heading("13. Estimates and Judgemental Balances", level=1)
        est = _load(out_dir, "estimation_exposure.json")
        if est:
            add_body("Balances whose carrying value rests on management estimate or judgement. "
                     "Flagged by size regardless of in-year movement — an account that moved is "
                     "not thereby less estimation-risky, only differently evidenced.")
            add_note(f"Total estimation exposure: {safe_fmt(est.get('summary', {}).get('total_exposure', 0))}.")
        else:
            _unavailable(add_note, "Estimation exposure",
                         "build_estimation_exposure did not run for this dataset.")
        close(13)

    _section_13_estimates()

    # ── 14. Journal and fraud-risk indicators ────────────────────────────────
    def _section_14_journal_and_fraud_risk():
        doc.add_heading("14. Journal and Fraud-Risk Indicators", level=1)
        ovr = _load(out_dir, "override_indicators.json")
        if ovr:
            add_note(ovr.get("planning_only", ""))
            present = [i for i in ovr.get("indicators", []) if i.get("count")]
            if present:
                add_table(["Indicator", "Accounts", "Total"],
                          [[i["indicator"].replace("_", " ").title(), str(i.get("count", 0)),
                            safe_fmt(i.get("total", 0)) if i.get("total") else "—"] for i in present])
        else:
            _unavailable(add_note, "Management-override indicators",
                         "build_override_indicators did not run for this dataset.")
        add_body(
            "Journal-level analysis is not performed. A trial balance carries closing balances, "
            "not the entries that produced them, so there is no journal population to test here. "
            "The journal dump, approval trail and edit logs are requested in the evidence list "
            "for the audit team to test directly."
        )
        close(14)

    _section_14_journal_and_fraud_risk()

    # ── 15. Disclosure-risk indicators ───────────────────────────────────────
    def _section_15_disclosure_risk():
        doc.add_heading("15. Disclosure-Risk Indicators", level=1)
        caro = _load(out_dir, "caro_indicators.json")
        stat = _load(out_dir, "statutory_screen.json")
        if caro:
            add_note(caro.get("safe_rule", ""))
            areas = caro.get("areas", [])
            if areas:
                add_table(["Area", "Clause hint", "Accounts", "Total balance"],
                          [[a["area"], a.get("clause_hint") or "—", str(a["accounts_matched"]),
                            safe_fmt(a["total_balance"])] for a in _capped(add_note, areas, 15, manifest)])
            cues = caro.get("schedule_iii_disclosure_cues", [])
            if cues:
                doc.add_heading("Schedule III disclosure cues", level=2)
                add_table(["Cue", "Record to request"], [[c["cue"], c["request"]] for c in cues])
        if stat:
            doc.add_heading("Statutory-dues reconciliation", level=2)
            add_body("Each levy compared against the base it arises from. Bands are wide because "
                     "a trial balance cannot see taxability, coverage or remittance timing.")
            add_table(["Levy relationship", "Status", "Base", "Dues"],
                      [[r["label"], r["status"].replace("_", " "),
                        safe_fmt(r.get("base_balance", 0)), safe_fmt(r.get("dues_balance", 0))]
                       for r in stat.get("results", [])])
        if not caro and not stat:
            _unavailable(add_note, "Disclosure and statutory indicators",
                         "Neither build_caro_indicators nor build_statutory_screen ran.")
        add_body(
            "A full disclosure checklist is not performed. That requires the draft financial "
            "statements and notes, which are not inputs to this pipeline."
        )
        close(15)

    _section_15_disclosure_risk()

    # ── 16. Going-concern indicators ─────────────────────────────────────────
    def _section_16_going_concern():
        doc.add_heading("16. Going-Concern Indicators", level=1)
        gc = _load(out_dir, "going_concern_screen.json")
        if gc:
            add_note(gc.get("non_conclusion", ""))
            computed = gc.get("computed", {})
            add_table(["Measure", "Amount"],
                      [[k.replace("_", " ").title(), safe_fmt(v)] for k, v in computed.items()])
            present = [i for i in gc.get("indicators", []) if i.get("present")]
            if present:
                add_bullets([i["indicator"].replace("_", " ").capitalize() for i in present])
            else:
                add_body("None of the five indicators is present on this trial balance.")
        else:
            _unavailable(add_note, "Going-concern indicators",
                         "build_going_concern_screen did not run for this dataset.")
        close(16)

    _section_16_going_concern()

    # ── 17. Consolidated audit findings ──────────────────────────────────────
    def _section_17_consolidated_findings():
        doc.add_heading("17. Consolidated Audit Findings", level=1)
        if render_focus_areas:
            render_focus_areas()
        if fr:
            summ = fr.get("summary", {})
            by = summ.get("by_risk_rating", {})
            add_body(
                f"{summ.get('total_records', 0)} finding(s) from "
                f"{len(summ.get('contributing_screens', []))} screen(s): "
                f"{by.get('high', 0)} high, {by.get('medium', 0)} medium, {by.get('low', 0)} low, "
                f"{by.get('information_request', 0)} information request. "
                f"{summ.get('regularity_flagged', 0)} carry a regularity flag."
            )
            records = _capped(add_note, fr.get("finding_records", []), 17, manifest)
            add_table(
                ["Account / FSLI", "Risk", "Observation", "Gap"],
                [[mask_text(r.get("account") or r.get("fsli") or "—"),
                  str(r.get("risk_rating", "")).upper(),
                  _brief(mask_text(r.get("observation", ""))),
                  _brief(mask_text(r.get("gap") or "—"))] for r in records],
            )
            add_note(
                "This is the full finding population. Sections 18, 20 and 22 below are "
                "views over the SAME population -- recommended procedures, management "
                "questions, and a prioritised sequence, respectively -- not a separate "
                "or additional population."
            )
        else:
            _unavailable(add_note, "Consolidated findings",
                         "build_finding_records did not run for this dataset.")
        close(17)

    _section_17_consolidated_findings()

    # ── 18. Recommended audit procedures ─────────────────────────────────────
    def _section_18_recommended_procedures():
        doc.add_heading("18. Recommended Audit Procedures", level=1)
        if fr:
            procs = [r for r in fr.get("finding_records", []) if r.get("proposed_response")]
            rows = [[mask_text(r.get("account") or r.get("fsli") or "—"),
                     "; ".join(r.get("assertion", [])) or "—",
                     _brief(mask_text(r.get("proposed_response", "")))]
                    for r in _capped(add_note, procs, 18, manifest)]
            if rows:
                add_table(["Account / FSLI", "Assertion at risk", "Proposed response"], rows)
                add_note(
                    "A filtered view of Section 17's finding population (records carrying a "
                    "proposed response) -- not a separate population."
                )
            else:
                add_body("No findings carry a proposed response for this dataset.")
        else:
            _unavailable(add_note, "Recommended procedures", "No consolidated findings available.")
        close(18)

    _section_18_recommended_procedures()

    # ── 19. Evidence request list ────────────────────────────────────────────
    def _section_19_evidence_request_list():
        doc.add_heading("19. Evidence Request List", level=1)
        ev = _load(out_dir, "evidence_request_list.json")
        if ev:
            summ = ev.get("summary", {})
            add_body(
                f"{summ.get('evidence_requests', 0)} de-duplicated request(s) across "
                f"{summ.get('audit_areas', 0)} audit area(s), covering "
                f"{summ.get('findings_covered', 0)} finding(s)."
            )
            by_area = ev.get("by_audit_area", {})
            rows = [[area, str(len(recs)), "; ".join(_brief(r, 40) for r in recs[:2]) + ("; …" if len(recs) > 2 else "")]
                    for area, recs in by_area.items()]
            if rows:
                add_table(["Audit area", "Records", "Examples"], rows)
            info = ev.get("information_request_list", [])
            if info:
                doc.add_heading("Information requests (data quality, not audit findings)", level=2)
                add_bullets([_brief(i["record"], 100) for i in info[:5]])
        else:
            _unavailable(add_note, "Evidence request list", "build_request_lists did not run.")
        close(19)

    _section_19_evidence_request_list()

    # ── 20. Management and statutory-auditor questions ───────────────────────
    def _section_20_management_queries():
        doc.add_heading("20. Management and Statutory-Auditor Questions", level=1)
        mq = _load(out_dir, "management_query_list.json")
        if mq:
            add_note(mq.get("important", ""))
            rows = [[mask_text(q.get("subject", "")), str(q.get("risk_rating", "")).upper(),
                     _brief(mask_text(q.get("query", "")), 100)]
                    for q in _capped(add_note, mq.get("management_query_list", []), 20, manifest)]
            if rows:
                add_table(["Subject", "Risk", "Question"], rows)
                add_note(
                    "Derived from the same finding population Section 17 lists in full, via "
                    "build_request_lists -- not an independently sourced set of questions."
                )
        else:
            _unavailable(add_note, "Management queries", "build_request_lists did not run.")
        add_body(
            "Questions addressed to the statutory auditor are not separately derived. Under a "
            "C&AG supplementary audit those are a distinct population, and identifying them "
            "requires the statutory auditor's own report and working papers, which are not "
            "inputs to this pipeline."
        )
    _section_20_management_queries()

    # ── 21. Prior-issue follow-up ────────────────────────────────────────────
    def _section_21_prior_issue_followup():
        doc.add_heading("21. Prior-Issue Follow-Up", level=1)
        add_body(
            "Not performed. No prior audit report, observation register, or Action Taken Note is "
            "an input to this pipeline, and a trial balance carries no record of what a previous "
            "audit found or what was accepted in response."
        )
        add_note(
            "To enable this section, supply the previous period's audit observations and the "
            "entity's Action Taken Note. Each prior observation can then be traced to the "
            "account it concerned and its current-period balance reported alongside."
        )
        close(21)

    _section_21_prior_issue_followup()

    # ── 22. Suggested audit plan and prioritisation ──────────────────────────
    def _section_22_audit_plan():
        doc.add_heading("22. Suggested Audit Plan and Prioritisation", level=1)
        if render_focus_sequence:
            render_focus_sequence()
        if fr:
            ranked = [r for r in fr.get("finding_records", [])
                      if str(r.get("risk_rating")) in ("high", "medium")]
            add_body("Ranked by risk rating, then regularity flag, then value — the order in which "
                     "an audit party would ordinarily take these up.")
            rows = [[str(i + 1), mask_text(r.get("account") or r.get("fsli") or "—"),
                     str(r.get("risk_rating", "")).upper(),
                     "Yes" if r.get("regularity_flag") else "No",
                     r.get("source_screen", "").replace("build_", "")]
                    for i, r in enumerate(_capped(add_note, ranked, 22, manifest))]
            if rows:
                add_table(["#", "Account / FSLI", "Risk", "Regularity", "Raised by"], rows)
                add_note(
                    "A prioritised sequence over Section 17's finding population; see Section 18 "
                    "for each item's recommended procedure -- not a separate or additional population."
                )
        else:
            _unavailable(add_note, "Audit plan", "No consolidated findings to prioritise.")
        close(22)

    _section_22_audit_plan()

    # ── 23. Data lineage and methodology ─────────────────────────────────────
    def _section_23_data_lineage():
        doc.add_heading("23. Data Lineage and Methodology", level=1)
        note = _load(out_dir, "normalisation_note.json")
        log = _load(out_dir, "run_log.json")
        if note:
            doc.add_heading("Normalisation applied before analysis", level=2)
            add_note(note.get("purpose", ""))
            add_table(["Step", "Applied", "Why"],
                      [[t["step"], str(t["detail"]), t["rationale"]]
                       for t in note.get("transformations_applied", [])])
            caveats = note.get("caveats", [])
            if caveats:
                doc.add_heading("Caveats qualifying every figure in this report", level=2)
                add_bullets(caveats)
            add_note(note.get("re_performance_test", ""))
        if log:
            doc.add_heading("Run provenance", level=2)
            versions = log.get("versions", {})
            packs = versions.get("knowledge_packs", {})
            add_note(
                f"Run {log.get('run', {}).get('run_timestamp', '—')} · prompt "
                f"{versions.get('prompt_version', '—')} · model {versions.get('llm_model', '—')} · "
                f"{len(log.get('artifacts', []))} artifact(s) from {len(log.get('tools_run', []))} tool(s) · "
                f"{len(packs)} knowledge-pack rule set(s) applied (full run_log.json in the workbook's "
                "source data for exact versions)."
            )
        if not note and not log:
            _unavailable(add_note, "Lineage and methodology",
                         "Neither build_normalisation_note nor build_run_log ran.")
        close(23)

    _section_23_data_lineage()

def _write_schedule_appendix(doc, manifest, add_body, add_note, add_table):
    """Section 25 -- the index of every sheet in the workbook.

    This is the guarantee behind the cross-referencing requirement. Sections above
    cite the sheets relevant to them; this lists every sheet without exception, so no
    schedule can exist in the workbook without being named in this document.
    """
    doc.add_heading("25. Detailed Appendices and Full TB Schedules", level=1)
    sheets = manifest.get("sheets", [])
    workbook = manifest.get("workbook")

    if not sheets:
        add_body(
            "No accompanying workbook was produced for this run, so there are no detailed "
            "schedules to reference. Every population summarised above is limited to what "
            "this document itself shows."
        )
        return

    add_body(
        f"The accompanying workbook {workbook} contains {len(sheets)} schedule(s). Sections "
        "above are deliberately summarised; each cites the schedule holding its complete "
        "population. Every sheet in the workbook appears in the table below."
    )
    add_table(
        ["Sheet", "Rows", "Supports section", "Contents"],
        [[s["name"], f"{s['rows']:,}", str(s["section"]), s["contains"]]
         for s in sorted(sheets, key=lambda x: (x["section"], x["name"]))],
    )

    unregistered = manifest.get("unregistered_sheets", [])
    if unregistered:
        add_note(
            "The workbook also contains sheet(s) not in the schedule registry: "
            f"{', '.join(unregistered)}. These are not cross-referenced above — add them to "
            "backend/tools/_report_schedules.py::SCHEDULES so they are."
        )

def _write_front_sections(doc, out_dir, manifest, add_body, add_note, add_table,
                          add_bullets, safe_fmt, sections):
    def close(section):
        line = reference_line(manifest, section)
        if line:
            add_note(line)

    if 4 in sections:
        doc.add_heading("4. Financial-Statement Reconstruction", level=1)
        stats = _load(out_dir, "financial_snapshot_statistics.json")
        if stats:
            add_body("Balance-sheet and profit-and-loss totals rebuilt from the trial balance's "
                     "own Schedule III grouping, to the hierarchy level the mapping supports.")
            add_table(["Statement head", "Amount"], [
                ["Total assets", safe_fmt(stats.get("total_assets", 0))],
                ["Total liabilities", safe_fmt(stats.get("total_liabilities", 0))],
                ["Total equity", safe_fmt(stats.get("total_equity_natural_sign", 0))],
                ["Total revenue", safe_fmt(stats.get("total_revenue", 0))],
                ["Total expenses", safe_fmt(stats.get("total_expenses", 0))],
            ])
        else:
            _unavailable(add_note, "Financial-statement reconstruction",
                         "build_financial_snapshot did not run for this dataset.")
        add_body("Notes to the accounts are not reconstructed. Disclosure content is not carried "
                 "in a trial balance, so only the primary statement heads can be rebuilt here.")
        close(4)

    if 5 in sections:
        doc.add_heading("5. Mapping and Classification Quality", level=1)
        note = _load(out_dir, "normalisation_note.json")
        if note:
            pop = note.get("row_population", {})
            add_table(["Measure", "Value"], [
                ["Rows in canonical TB", f"{pop.get('rows_in_canonical_tb', 0):,}"],
                ["Mapped accounts", f"{pop.get('mapped_accounts', 0):,}"],
                ["Unmapped / unmatched", f"{pop.get('unmapped_or_unmatched_accounts', 0):,}"],
                ["Mapped percentage", f"{pop.get('mapped_percentage', 0)}%"],
            ])
            add_note("Relationship, ratio and counterpart analytics degrade in proportion to "
                     "unmapped coverage. An unclear account is never force-mapped to close the "
                     "gap -- the chart of accounts is requested instead.")
            unmapped_count = pop.get("unmapped_or_unmatched_accounts", 0)
            if unmapped_count:
                add_note(f"Unless stated otherwise, downstream analysis in this report (Sections "
                         f"6 onward -- materiality, concentration, movement, sign convention, "
                         f"ratios and risk screens) covers the {pop.get('mapped_accounts', 0):,} "
                         f"mapped account(s) only; the {unmapped_count:,} unmapped/unmatched "
                         f"account(s) above are excluded from that analysis.")
        else:
            _unavailable(add_note, "Mapping quality",
                         "build_normalisation_note did not run for this dataset.")
        close(5)

    if 7 in sections:
        doc.add_heading("7. Standalone Analytical Review", level=1)
        ds = _load(out_dir, "data_sufficiency.json")
        add_body("This is a single-period review. Movement and variance analytics that require a "
                 "comparative period are not performed here; a prior-period trial balance enables "
                 "them through the comparative report.")
        if ds:
            add_note(f"Data sufficiency: {ds.get('grade', 'Unknown')}. {ds.get('rationale', '')}")
            disabled = ds.get("disabled_checks", [])
            not_impl = ds.get("not_implemented_count", 0)
            # The full rule-by-rule detail (every TB-0xx check, its status and message) is a
            # working-paper population, not narrative -- it lives in the Data Quality sheet's
            # Rule Register (added there specifically so this section can stay a summary
            # instead of inlining up to 12 bullet points, as it previously did).
            summary_bits = []
            if disabled:
                summary_bits.append(f"{len(disabled)} check(s) unavailable or weakened on this dataset")
            if not_impl:
                summary_bits.append(f"{not_impl} rule(s) not yet implemented in this product build")
            if summary_bits:
                add_note(
                    "; ".join(summary_bits) + " -- see the Rule Register in the Data Quality sheet "
                    "of the accompanying workbook for the complete, per-rule detail."
                )
        close(7)

    if 9 in sections:
        doc.add_heading("9. Ratio and Relationship Analytics", level=1)
        ratios = _load(out_dir, "audit_ratio_pack.json")
        if ratios:
            doc.add_heading("Audit-analytical ratios", level=2)
            add_table(["Ratio", "Value", "Basis"],
                      [[k.replace("_", " ").title(),
                        r.get("value_pct") or "not computed",
                        r.get("formula", "")]
                       for k, r in (ratios.get("ratios") or {}).items()])
            add_note("A ratio whose component could not be located is reported as not computed "
                     "with the component named. Approximating it would present an unvalidated "
                     "figure under a real metric's name.")
        rel = _load(out_dir, "relationship_expectations.json")
        if rel:
            doc.add_heading("Expected relationships", level=2)
            add_table(["Relationship", "Status", "Observed"],
                      [[r["name"], r["status"].replace("_", " "),
                        f"{r.get('observed_ratio', 0):.2%}" if r.get("observed_ratio") is not None else "—"]
                       for r in rel.get("results", [])])
        cp = _load(out_dir, "counterpart_screen.json")
        if cp:
            doc.add_heading("Missing counterparts", level=2)
            add_body("The audit signal is the ABSENT counterpart. A material head whose expected "
                     "pair is missing or trivially small is a completeness question.")
            add_table(["Relationship", "Source head", "Source", "Counterpart"],
                      [[f["relationship_id"], f.get("source_head", ""),
                        safe_fmt(f.get("source_balance", 0)), safe_fmt(f.get("target_balance", 0))]
                       for f in cp.get("finding_records", [])])
        if not any((ratios, rel, cp)):
            _unavailable(add_note, "Ratio and relationship analytics",
                         "None of the ratio or relationship screens ran for this dataset.")
        close(9)

    if 11 in sections:
        doc.add_heading("11. Assertion-Level Risk Assessment", level=1)
        aem = _load(out_dir, "assertion_evidence_map.json")
        if aem:
            summ = aem.get("summary", {})
            add_body(
                f"{summ.get('exceptions_mapped', 0)} exception(s) mapped to the assertions their "
                f"account area exposes. {summ.get('generic_evidence_replaced', 0)} carried generic "
                "evidence derived from which engine flagged them; those now name the specific "
                "records that resolve the assertion."
            )
            by_area = summ.get("by_account_area", {})
            if by_area:
                add_table(["Account area", "Exceptions"],
                          [[k.replace("_", " ").title(), str(v)] for k, v in by_area.items()])
            if summ.get("unclassified"):
                add_note(f"{summ['unclassified']} exception(s) could not be matched to an account "
                         "area and carry information-request evidence only -- a mapping is never "
                         "forced to close that gap.")
        else:
            _unavailable(add_note, "Assertion-level assessment",
                         "build_assertion_evidence_map did not run for this dataset.")
        close(11)




# ==== _shared_helpers ====
NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
                6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}

_ESTIMATION_PACK = load_pack("estimation")

FCY_KEYWORDS = tuple(_ESTIMATION_PACK["foreign_currency"])

def safe_fmt(val) -> str:
    """Formats a value as a thousands-separated integer string, falling back to str().

    A residual below 1 (e.g. a 3.05e-5 Dr-Cr rounding difference) is shown at full
    precision instead of collapsing to "0" -- TB-R20: blanket :,.0f rounding made a
    genuine nonzero control-total residual read as a perfect tie-out in the report,
    while the Excel workbook (which keeps the raw float in the cell, display format
    aside) showed the true value."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return str(val)
    if 0 < abs(f) < 1:
        return f"~0 (residual: {f:.6f})"
    return f"{f:,.0f}"

def _brief(text, limit=90) -> str:
    """Truncates a narrative field to `limit` characters at a word boundary, for the
    WORD report only -- the Excel workbook always keeps the untruncated field, so nothing
    is lost, just not duplicated at full length in the document meant to be read end to
    end. Never truncates mid-word, and only adds the ellipsis when text was actually cut."""
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return (cut or s[:limit]) + "…"

def resolve_severity(rule_score: float, abs_amount: float, clearly_trivial: float) -> str:
    """Single severity function -- TB-R05/R16/R17: severity must never be computed
    independently in more than one place (a report renderer must always render this
    verbatim, never re-tier it), and must never rate MEDIUM+ on an amount below the
    clearly-trivial threshold regardless of how many rule-engines fired on it.

    rule_score: composite weighted score from triggered detection rules (unchanged
    tiering: >75 Critical, >=50 High, >=25 Medium, >0 Low, else Information Request).
    abs_amount: the finding's absolute closing balance / amount at risk.
    clearly_trivial: materiality's clearly-trivial threshold for this engagement."""
    if clearly_trivial and abs_amount < clearly_trivial:
        return "Low" if rule_score > 0 else "Information Request"
    if rule_score > 75:
        return "Critical"
    if rule_score >= 50:
        return "High"
    if rule_score >= 25:
        return "Medium"
    if rule_score > 0:
        return "Low"
    return "Information Request"

def hierarchy_path(row: dict) -> str:
    """Builds an on-the-fly display path from the new canonical head/sub-head columns,
    skipping any segment that is missing. Per canonical_schema.py, hierarchy_path/
    hierarchy_level are deliberately NOT persisted columns — every consumer derives this."""
    segs = [row.get("bs_pl"), row.get("main_head"), row.get("sub_head_1"), row.get("sub_head_2")]
    parts = [str(s).strip() for s in segs if s not in (None, "") and str(s).strip().lower() != "none"]
    return " > ".join(parts)

# Short Schedule III abbreviations a client-supplied grouping workbook may carry verbatim in
# account_type/main_head (e.g. "AST"/"LEQ"/"INC") instead of the full words the substring
# matching below expects -- checked as an exact match before that substring logic, so these
# never fall through to the raw-code-verbatim passthrough (which previously left a code like
# "AST" as the displayed FS Head, and silently broke every report_head-keyed screen: sign
# convention, going-concern, concentration).
_ACCOUNT_HEAD_ABBREVIATIONS = {
    "ast": "Assets",
    "lia": "Liabilities",
    "liab": "Liabilities",
    "leq": "Liabilities",  # ambiguous default -- see _AMBIGUOUS_ABBREVIATIONS below
    "equ": "Equity",
    "eqt": "Equity",
    "rev": "Revenue",
    "inc": "Revenue",
    "exp": "Expenses",
}

# Codes in _ACCOUNT_HEAD_ABBREVIATIONS that are genuinely ambiguous on their own -- a real
# client export was found using "LEQ" for Liabilities AND Equity (and even a couple of Asset
# rows) indiscriminately, so resolving it immediately would silently merge two different
# statement heads (this is exactly what broke going-concern's net-worth calc: _head_total(df,
# "Equity") found nothing because every genuine Equity row had been mapped to "Liabilities").
# For these codes, classify_row treats the abbreviation lookup as a last-resort fallback only,
# giving the next source (main_head's own substring-matchable text, e.g. "Equity Share
# Capital") a real chance to resolve the correct head first.
_AMBIGUOUS_ABBREVIATIONS = {"leq"}

def classify_row(row: dict) -> tuple:
    """Best-effort (head, sub, note) classification for a canonical TB row under the new
    schema (bs_pl/main_head/sub_head_1/sub_head_2/account_type/mapped_status).

    Replaces TB-v1's `_NOTE_TO_FSLI` reclassification table, which existed only to patch
    known bugs in the old pipeline's own fs_head rollup (e.g. CWIP rolled under Equity).
    The new schema's main_head/account_type are trusted directly instead.

    `head` is normalized to one of Assets/Liabilities/Equity/Revenue/Expenses/Unmapped where
    recognizable from account_type or main_head; otherwise the raw label (or a bs_pl-derived
    Balance Sheet/Profit & Loss fallback) is used verbatim so the report never crashes, it
    just shows whatever classification label upstream actually produced.
    """
    status = str(row.get("mapped_status") or "").upper()
    if status in (MAPPED_STATUS_UNMAPPED, MAPPED_STATUS_UNMATCHED):
        note = row.get("sub_head_1") or row.get("main_head") or row.get("sub_head_2") or "Unmapped"
        return "Unmapped", "Unmapped", str(note)

    head = None
    fallback_head = None  # set only by an ambiguous abbreviation (e.g. LEQ); used only if
                           # nothing -- including a later source's own substring match -- resolves it
    for source in (row.get("account_type"), row.get("main_head")):
        s = str(source).strip() if source not in (None, "") else ""
        if not s or s.lower() == "none":
            continue
        low = s.lower()
        if low in _ACCOUNT_HEAD_ABBREVIATIONS:
            if low in _AMBIGUOUS_ABBREVIATIONS:
                fallback_head = fallback_head or _ACCOUNT_HEAD_ABBREVIATIONS[low]
                continue
            head = _ACCOUNT_HEAD_ABBREVIATIONS[low]
        elif "asset" in low:
            head = "Assets"
        elif "liab" in low:
            head = "Liabilities"
        elif "equity" in low:
            head = "Equity"
        elif "revenue" in low or "income" in low:
            head = "Revenue"
        elif "expense" in low or "expenditure" in low:
            head = "Expenses"
        else:
            head = s
        break

    if head is None and fallback_head is not None:
        head = fallback_head

    if head is None:
        bs_pl = str(row.get("bs_pl") or "").strip().upper()
        if bs_pl.startswith("BS"):
            head = "Balance Sheet"
        elif bs_pl.startswith("PL") or "P&L" in bs_pl or "P & L" in bs_pl:
            head = "Profit & Loss"
        else:
            head = "Unmapped"

    sub = row.get("sub_head_1") or row.get("main_head") or head
    return head, str(sub), hierarchy_path(row)

def load_canonical_tb(path, numeric_cols=None) -> Optional[pl.DataFrame]:
    """Loads a canonical_tb.parquet with numeric coercion and adds report_head/report_sub/
    report_note columns derived via classify_row(). Returns None if the file is absent or
    fails to parse — callers should treat that as 'section unavailable', not fatal."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        df = pl.read_parquet(p)
    except Exception:
        return None

    cols = numeric_cols or CANONICAL_TB_NUMERIC_COLUMNS
    exprs = [
        (pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0).alias(c) if c in df.columns else pl.lit(0.0).alias(c))
        for c in cols
    ]
    df = df.with_columns(exprs)

    if "gl_code" in df.columns:
        df = df.filter(pl.col("gl_code").is_not_null())

    if df.is_empty():
        return df.with_columns(
            [
                pl.lit(None, dtype=pl.Utf8).alias("report_head"),
                pl.lit(None, dtype=pl.Utf8).alias("report_sub"),
                pl.lit(None, dtype=pl.Utf8).alias("report_note"),
            ]
        )

    classified = [classify_row(r) for r in df.iter_rows(named=True)]
    df = df.with_columns(
        [
            pl.Series("report_head", [c[0] for c in classified]),
            pl.Series("report_sub", [c[1] for c in classified]),
            pl.Series("report_note", [c[2] for c in classified]),
        ]
    )
    return df

def movement_flag(abs_amount, overall_materiality, performance_materiality) -> Optional[str]:
    """Classifies an absolute movement against materiality thresholds — CRITICAL (>=2x
    overall), HIGH (>=overall), MEDIUM (>=performance), else None (not material enough)."""
    if overall_materiality and abs_amount >= overall_materiality * 2:
        return "CRITICAL"
    if overall_materiality and abs_amount >= overall_materiality:
        return "HIGH"
    if performance_materiality and abs_amount >= performance_materiality:
        return "MEDIUM"
    return None

def control_totals(df: Optional[pl.DataFrame]) -> Optional[dict]:
    """Deterministic control-total checks (debit=credit, tie-out, duplicates, zero rows).

    TB-R20 correction: the footing identity ("total_debit"/"total_credit"/"difference") is
    computed from signed CLOSING BALANCES split by sign (debit-positive convention) -- the
    genuine trial-balance foot -- not from the raw debit/credit TURNOVER columns. Turnover
    ties by construction in most ERP period-extract exports and proves nothing about whether
    the TB itself balances; it is still reported, under turnover_total_debit/
    turnover_total_credit, as a legitimate but separate figure."""
    if df is None or df.is_empty():
        return None
    turnover_total_debit = float(df["debit"].sum()) if "debit" in df.columns else 0.0
    turnover_total_credit = float(df["credit"].sum()) if "credit" in df.columns else 0.0
    if "closing_balance" in df.columns:
        sum_closing = float(df["closing_balance"].sum())
        closing_total_debit = float(df.filter(pl.col("closing_balance") > 0)["closing_balance"].sum())
        closing_total_credit = float(-df.filter(pl.col("closing_balance") < 0)["closing_balance"].sum())
    else:
        sum_closing = 0.0
        closing_total_debit = 0.0
        closing_total_credit = 0.0
    # height - n_unique matches pandas Series.duplicated().sum() semantics (counts
    # occurrences BEYOND the first per value), unlike polars' own is_duplicated()
    # which flags every occurrence including the first.
    dup_count = int(df.height - df["gl_code"].n_unique()) if "gl_code" in df.columns else 0
    zero_rows = 0
    if {"opening_balance", "closing_balance", "debit", "credit"}.issubset(set(df.columns)):
        zero_rows = df.filter(
            (pl.col("opening_balance") == 0)
            & (pl.col("closing_balance") == 0)
            & (pl.col("debit") == 0)
            & (pl.col("credit") == 0)
        ).height
    return {
        "total_debit": closing_total_debit,
        "total_credit": closing_total_credit,
        "difference": closing_total_debit - closing_total_credit,
        "sum_closing": sum_closing,
        "turnover_total_debit": turnover_total_debit,
        "turnover_total_credit": turnover_total_credit,
        "duplicate_gl_codes": dup_count,
        "fully_zero_rows": zero_rows,
    }

def movement_rows_from_canonical(df, om, perf_mat, exclude_heads=("Revenue", "Expenses", "Suspense", "Unmapped"), limit=20) -> list:
    """Balance-sheet opening-to-closing movement, flagged against materiality — Revenue/
    Expense accounts are excluded since they structurally run from nil to their full-year
    figure and are not 'movement' in the single-period risk sense."""
    if df is None or df.is_empty() or "report_head" not in df.columns:
        return []
    df = mapped_only(df)
    exclude = {h.lower() for h in exclude_heads}
    rows = []
    for r in df.iter_rows(named=True):
        head = str(r.get("report_head", "") or "")
        if head.lower() in exclude:
            continue
        ob = float(r.get("opening_balance", 0.0))
        cb = float(r.get("closing_balance", 0.0))
        mov = cb - ob
        flag = movement_flag(abs(mov), om, perf_mat)
        if not flag:
            continue
        rows.append({
            "gl_code": r.get("gl_code", ""),
            "gl_name": r.get("gl_name", ""),
            "fs_head": head,
            "opening": ob,
            "closing": cb,
            "movement": mov,
            "flag": flag,
        })
    sev_order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}
    rows.sort(key=lambda x: (sev_order.get(x["flag"], 0), abs(x["movement"])), reverse=True)
    return rows[:limit]

_OFFSETTING_PAIR_TOLERANCE_PCT = 1.0

def concentration_rows_from_canonical(df, threshold_pct=5.0, limit=15, clearly_trivial=0.0) -> list:
    """Accounts individually exceeding threshold_pct of their report_head's total absolute
    closing balance.

    TB-R14 (denominator degeneracy): a report_head with only one non-trivial account
    trivially produces a ~100% "concentration" finding that says nothing about risk --
    skipped entirely rather than surfaced as a false artefact.

    TB-R13 (offsetting contra pairs): two near-equal-and-opposite accounts in the same
    head (e.g. a recovery/allocation pair) each individually cross the threshold and used
    to render as two separate findings, even though their combined effect nets close to
    nil. Such pairs are collapsed into one annotated finding instead."""
    if df is None or df.is_empty() or "report_head" not in df.columns:
        return []
    df = mapped_only(df)
    head_totals = dict(
        df.group_by("report_head").agg(pl.col("closing_balance").abs().sum().alias("total")).iter_rows()
    )
    head_material_counts: dict = {}
    if clearly_trivial:
        head_material_counts = dict(
            df.filter(pl.col("closing_balance").abs() > clearly_trivial)
            .group_by("report_head").agg(pl.len().alias("n")).iter_rows()
        )

    rows = []
    for r in df.iter_rows(named=True):
        head = str(r.get("report_head", "") or "")
        total = head_totals.get(head, 0.0)
        if not total:
            continue
        if clearly_trivial and head_material_counts.get(head, 0) <= 1:
            continue
        cb = float(r.get("closing_balance", 0.0))
        pct = abs(cb) / total * 100
        if pct >= threshold_pct:
            rows.append({
                "gl_code": r.get("gl_code", ""),
                "gl_name": r.get("gl_name", ""),
                "fs_head": head,
                "closing": cb,
                "pct": pct,
            })

    rows = _collapse_offsetting_pairs(rows)
    rows.sort(key=lambda x: x["pct"], reverse=True)
    return rows[:limit]

def _collapse_offsetting_pairs(rows: list) -> list:
    """Merges near-equal-and-opposite-sign row pairs within the same fs_head into one
    annotated finding, keeping the larger-magnitude account's pct for "why this was
    flagged" while reporting the combined net effect."""
    used = set()
    merged = []
    for i, a in enumerate(rows):
        if i in used:
            continue
        pair_found = False
        for j in range(i + 1, len(rows)):
            if j in used:
                continue
            b = rows[j]
            if b["fs_head"] != a["fs_head"] or (a["closing"] >= 0) == (b["closing"] >= 0):
                continue
            hi, lo = (abs(a["closing"]), abs(b["closing"])) if abs(a["closing"]) >= abs(b["closing"]) else (abs(b["closing"]), abs(a["closing"]))
            if hi == 0:
                continue
            if (hi - lo) / hi * 100 > _OFFSETTING_PAIR_TOLERANCE_PCT:
                continue
            primary, secondary = (a, b) if abs(a["closing"]) >= abs(b["closing"]) else (b, a)
            net = a["closing"] + b["closing"]
            merged.append({
                **primary,
                "offsetting_pair": True,
                "paired_gl_code": secondary["gl_code"],
                "paired_gl_name": secondary["gl_name"],
                "net_closing": net,
                "note": (
                    f"Offsetting pair with {secondary['gl_name']} ({secondary['gl_code']}) -- "
                    f"combined net impact {net:,.0f}, close to nil."
                ),
            })
            used.add(i)
            used.add(j)
            pair_found = True
            break
        if not pair_found:
            merged.append(a)
    return merged

_ESTIMATION_RISK_KEYWORDS = tuple(_ESTIMATION_PACK["estimation_risk"])

def is_estimation_risk_account(gl_name) -> bool:
    """TB-R12/R15: accounts whose carrying value rests on management estimate/judgment
    (acquisition cost, CWIP, exploration cost, impairment provisions) rather than a
    transactable market price. A dormant/no-movement pattern on these is not "structurally
    expected" the way a fully-depreciated fixed asset is -- the balance could be materially
    wrong for years with no transaction ever surfacing it."""
    name_lower = str(gl_name or "").lower()
    return any(kw in name_lower for kw in _ESTIMATION_RISK_KEYWORDS)

_DORMANT_ESTIMATION_EXPLANATION = (
    "Estimation risk — this balance rests on management judgement (cost/valuation), not a "
    "transactable price, so no activity does not mean no risk. Confirm continued "
    "recoverability/valuation despite the lack of movement."
)
_DORMANT_ORDINARY_EXPLANATION = (
    "No activity this period. Commonly expected for accounts like Share Capital or a fully "
    "depreciated/written-down asset — confirm there is no change in circumstances that should "
    "have produced a movement (e.g. a disposal, impairment, or capital change)."
)

def dormant_rows_from_canonical(df, limit=15, exclude_heads=()) -> list:
    """Accounts with zero debit/credit movement in the period but a non-zero closing
    balance. Each row is tagged `is_estimation_risk` (TB-R15) so renderers can split
    "structurally expected, no movement" (share capital, fully-depreciated assets) from
    large estimation-prone balances that warrant confirmation regardless of size -- `explanation`
    spells that distinction out in prose rather than leaving the reader to infer it from a flag.

    exclude_heads mirrors movement_rows_from_canonical's own exclusion set, so a caller wanting
    "no movement, balance-sheet-only" (paired with that function's BS-only movement rows, e.g.
    the Movement (BS) sheet) can pass the same tuple and get a consistent population."""
    if df is None or df.is_empty() or not {"debit", "credit", "closing_balance"}.issubset(set(df.columns)):
        return []
    df = mapped_only(df)
    dormant = df.filter((pl.col("debit") == 0) & (pl.col("credit") == 0) & (pl.col("closing_balance") != 0))
    if exclude_heads and "report_head" in dormant.columns:
        exclude = {h.lower() for h in exclude_heads}
        dormant = dormant.filter(~pl.col("report_head").cast(pl.Utf8).str.to_lowercase().is_in(exclude))
    if dormant.is_empty():
        return []
    dormant = dormant.with_columns(pl.col("closing_balance").abs().alias("_abs")).sort("_abs", descending=True)
    rows = []
    for r in dormant.head(limit).iter_rows(named=True):
        is_est_risk = is_estimation_risk_account(r.get("gl_name"))
        rows.append({
            "gl_code": r.get("gl_code", ""),
            "gl_name": r.get("gl_name", ""),
            "fs_head": r.get("report_head", ""),
            "closing": float(r.get("closing_balance", 0.0)),
            "is_estimation_risk": is_est_risk,
            "explanation": _DORMANT_ESTIMATION_EXPLANATION if is_est_risk else _DORMANT_ORDINARY_EXPLANATION,
        })
    return rows

def resolve_entity_and_fy(tb_meta: dict, manifest: Optional[dict] = None) -> tuple:
    """Best-effort entity label + FY label for report titles/headers.

    Prefers the structured fields load_tb_from_db.py writes into tb_metadata.json
    (entity_name/company_name/financial_year) -- real MAIN document fields, not a
    guess. Falls back to regex-parsing the uploaded file's name (the only signal
    available for an upload-sourced run, e.g. "OVRL_TB_FY2025-26_31.03.2026.xlsx")
    when the structured fields aren't present. Without this, every report builder
    fell back to a literal "Unknown Source" title/header for DB-sourced runs, since
    no tool wrote tb_metadata.json at all before load_tb_from_db.py started doing so.

    Returns (entity_label, fy_label, source_file)."""
    manifest = manifest or {}
    source_file = tb_meta.get("source_file") or manifest.get("source_file") or "Unknown Source"

    entity_label = tb_meta.get("company_name") or tb_meta.get("entity_name")
    fy_label = tb_meta.get("financial_year")

    if not entity_label or not fy_label:
        stem = Path(source_file).stem if source_file else "Unknown Entity"
        if not fy_label:
            fy_match = re.search(r"FY\s*[\d\-/]+", stem, re.IGNORECASE)
            fy_label = fy_match.group(0).upper() if fy_match else fy_label
        if not entity_label:
            tokens = re.split(r"[_\-\s]+", stem)
            entity_candidates = [t for t in tokens if t.isalpha() and t.upper() == t and len(t) > 1]
            entity_label = entity_candidates[-1] if entity_candidates else stem

    return entity_label, (fy_label or ""), source_file

def resolve_artifact_path(provided_path, out_dir: Path, default_filename: str) -> Path:
    """Resolve a possibly-missing/optional input path against a default filename in out_dir.

    Also corrects a wrong-format path (e.g. an LLM passing the .json sibling of a tool that
    writes both .parquet and .json, like fsli_summary.json instead of fsli_summary.parquet)
    back to the canonical default_filename in out_dir, when that canonical file actually
    exists -- SINGLE_TB uses one shared output_dir per run (see system_prompt.py's
    Artifact-Passing Convention), so the canonical filename is always the authoritative
    location for that artifact type once its producing tool has run."""
    default_path = out_dir / default_filename
    if not provided_path:
        return default_path
    p = Path(provided_path)
    if p.is_dir() or not p.exists():
        return default_path
    if p.suffix != default_path.suffix and default_path.exists():
        return default_path
    return p

def safe_load_json(path) -> dict:
    """Load JSON, returning {} on any failure (missing file, parse error)."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return {}

def safe_load_parquet(path) -> pl.DataFrame:
    """Load a parquet file, returning an empty DataFrame on any failure."""
    if not path:
        return pl.DataFrame()
    p = Path(path)
    if not p.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(p)
    except Exception:
        return pl.DataFrame()

def first_present_column(df: pl.DataFrame, candidates, default=None):
    """Return the first column name from `candidates` present in df, else `default`."""
    for c in candidates:
        if c in df.columns:
            return c
    return default

def row_fsli(row, default="Unmapped") -> str:
    """Resolve the FSLI grouping key for a canonical TB row: sub_head_1, falling back to main_head."""
    for key in ("sub_head_1", "main_head"):
        val = row.get(key)
        if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
            return str(val).strip()
    return default

def row_fs_head(row, default="Unmapped") -> str:
    """Resolve the top-level FS head for a canonical TB row: main_head."""
    val = row.get("main_head")
    if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
        return str(val).strip()
    return default

# TB-R31: a THIRD, genuinely different sign-convention basis from canonical.py's TB-000 and
# comparison.py's run_comparison_sign_check -- both of those are gl_name keyword-anchor
# screens over a small subset of rows; this one filters by the already-*resolved*
# report_head column against the full normal_debit_heads()/normal_credit_heads() population,
# so its denominator (every mapped row belonging to a debit/credit-normal head) is much
# larger. Every render site consuming this dict's numbers must cite this basis by name so a
# reader never mistakes it for one of the other two screens' percentages.
SIGN_CONVENTION_STATS_BASIS_LABEL = "population-wide, mapped-row basis (report_head classification)"

def sign_convention_stats(df) -> Optional[dict]:
    """Counts accounts sitting opposite their report_head's normal balance side — a broad
    screen for contra/provision/clearing accounts, not a misclassification finding itself."""
    if df is None or df.is_empty() or "report_head" not in df.columns:
        return None
    df = mapped_only(df)
    heads_lower = pl.col("report_head").cast(pl.Utf8).str.to_lowercase()
    debit_side = df.filter(heads_lower.is_in(list(normal_debit_heads())))
    credit_side = df.filter(heads_lower.is_in(list(normal_credit_heads())))
    debit_flip = int((debit_side["closing_balance"] < 0).sum())
    credit_flip = int((credit_side["closing_balance"] > 0).sum())
    return {
        "basis": SIGN_CONVENTION_STATS_BASIS_LABEL,
        "debit_total": len(debit_side), "debit_flip": debit_flip,
        "debit_flip_pct": round(debit_flip / len(debit_side) * 100, 1) if len(debit_side) else 0.0,
        "credit_total": len(credit_side), "credit_flip": credit_flip,
        "credit_flip_pct": round(credit_flip / len(credit_side) * 100, 1) if len(credit_side) else 0.0,
    }

def canonical_row_count(df) -> int:
    """Single source of truth for 'how many rows are in this canonical TB' --
    remark #18 fix. Four call sites (reports.py's docx builders x2, risk.py's
    mapping-coverage and sensitive-detector scans) previously each computed
    `len(df)` independently; a None-safe shared helper removes that duplication
    without changing behavior."""
    return len(df) if df is not None else 0

def find_fsli_component(df, keywords) -> Optional[dict]:
    """Shallowest FSLI node whose node_name contains any keyword (case-insensitive
    substring), as {"balance", "node_name", "hierarchy_level"}, or None.

    Taking the SHALLOWEST match is what avoids double-counting a subtotal together
    with its own children -- e.g. "Current assets" and "Trade receivables" both
    match a "current asset"/"receivable" search in a rolled-up hierarchy.

    Promoted here from build_financial_ratios.py::_find_component: the counterpart,
    relationship-expectation, statutory and audit-ratio screens all need the same
    matcher, and copying it four times would recreate exactly the duplication the
    knowledge packs exist to remove.
    """
    if df is None or df.is_empty() or "node_name" not in df.columns:
        return None
    mask = pl.any_horizontal([
        pl.col("node_name").cast(pl.Utf8).str.to_lowercase().str.contains(kw, literal=True).fill_null(False)
        for kw in keywords
    ])
    matches = df.filter(mask)
    if matches.is_empty():
        return None
    row = matches.sort("hierarchy_level").row(0, named=True)
    return {
        "balance": abs(float(row.get("closing_balance", 0.0))),
        "node_name": row.get("node_name"),
        "hierarchy_level": row.get("hierarchy_level"),
    }

def gl_total_by_keywords(tb_df, keywords, exclude=()) -> dict:
    """Fallback matcher over the canonical TB itself, for when the FSLI hierarchy
    carries no matching node (unmapped or thinly-mapped TBs).

    Scans gl_name, main_head and sub_head_1 -- a real account's currency/statutory
    nature often shows in its grouping text rather than its own ledger name (the
    same finding that drove build_entity_profile to widen its FX scan).

    `exclude` drops rows matching those terms even when a keyword hit. Needed
    because a contra account's grouping text matches its parent head: "Accumulated
    Depreciation on Plant" sits under "Property, Plant and Equipment" AND contains
    "depreciation", so without exclusion it inflates both sides of the depreciation
    proxy at once.

    Returns {"balance", "account_count", "accounts"} with balance as the ABSOLUTE
    sum, so a debit-and-credit pair inside one keyword group does not net to zero
    and read as "head absent".
    """
    empty = {"balance": 0.0, "account_count": 0, "accounts": []}
    if tb_df is None or tb_df.is_empty():
        return empty

    cols = [c for c in ("gl_name", "main_head", "sub_head_1", "sub_head_2") if c in tb_df.columns]
    if not cols:
        return empty

    mask = pl.lit(False)
    for col in cols:
        lowered = pl.col(col).cast(pl.Utf8).str.to_lowercase().fill_null("")
        for kw in keywords:
            mask = mask | lowered.str.contains(kw, literal=True)

    if exclude:
        excl = pl.lit(False)
        for col in cols:
            lowered = pl.col(col).cast(pl.Utf8).str.to_lowercase().fill_null("")
            for term in exclude:
                excl = excl | lowered.str.contains(term, literal=True)
        mask = mask & ~excl

    matches = tb_df.filter(mask)
    if matches.is_empty():
        return empty

    return {
        "balance": float(matches["closing_balance"].abs().sum()),
        "account_count": matches.height,
        "accounts": [
            {
                "gl_code": str(r.get("gl_code") or ""),
                "gl_name": str(r.get("gl_name") or ""),
                "closing_balance": round(float(r.get("closing_balance") or 0.0), 2),
            }
            for r in matches.sort(pl.col("closing_balance").abs(), descending=True)
                            .head(10).iter_rows(named=True)
        ],
    }

_REVENUE_KEYWORDS = ("revenue from operations", "revenue", "sales", "income from operations")

def compute_total_revenue(out_dir, tb_df=None) -> dict:
    """Wave 2 Fix 2a: the single shared revenue figure. build_audit_ratio_pack,
    build_relationship_expectations, and STAT-GST-OUT each used to resolve revenue
    independently (three different keyword/fallback mechanisms), which is exactly why
    an EPIL live run showed revenue stated three different ways in the same report.

    Prefers financial_snapshot_statistics.json's total_revenue (the authoritative
    classify_row()-based figure build_financial_snapshot already computes) --
    materiality.py's build_materiality already reads it this way (`abs(float(stats.get(
    "total_revenue", 0.0)))`); this just makes that the ONE place every other revenue
    consumer defers to as well. Falls back to a keyword scan over tb_df only when that
    file doesn't exist yet (e.g. called before build_financial_snapshot has run).

    Returns {"balance", "basis"} where basis is "financial_snapshot" or
    "gl_name_fallback" -- callers should record which one they got."""
    out_dir = Path(out_dir) if out_dir is not None else None
    stats_path = out_dir / "financial_snapshot_statistics.json" if out_dir else None
    if stats_path and stats_path.exists():
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        if "total_revenue" in stats:
            return {"balance": abs(float(stats["total_revenue"])), "basis": "financial_snapshot"}

    fallback = gl_total_by_keywords(tb_df, _REVENUE_KEYWORDS) if tb_df is not None else {"balance": 0.0}
    return {"balance": fallback["balance"], "basis": "gl_name_fallback"}




# ==== _safe_wording ====
_LANGUAGE_PACK = load_pack("language")

_SAFE_WORDING_RULES = [
    (r["pattern"], r["replacement"]) for r in _LANGUAGE_PACK["phrase_substitutions"]
]

_ASSURANCE_WORDS = list(_LANGUAGE_PACK["assurance_words"])

SAFE_WORDING_DISCLAIMER = _LANGUAGE_PACK["mandatory_disclaimer"]

# What this pipeline deliberately does NOT do, stated once here and rendered verbatim into
# both the Word report (below the Notice to the Reader) and the Excel Legend sheet, so a
# reviewer never has to infer a scope boundary from silence -- an absent CARO conclusion or
# an absent journal-entry test should read as "out of scope by design", not as "the pipeline
# missed it". Each line names WHY the boundary exists (a real limitation of TB-level data, or
# a deliberate methodology choice), not just that it exists.
SCOPE_LIMITATIONS = [
    "No statistical audit sampling is applied by default -- every screen in this report tests "
    "the FULL population, not a sample. Where a Sample Selection sheet is present for this "
    "run, it states the method (Monetary Unit or Stratified Random) and population explicitly.",
    "No journal-entry-level testing is performed. This tool reads account BALANCES (opening/"
    "debit/credit/closing per GL); it never receives transaction-level journal entries, so it "
    "cannot test who posted an entry, when, or in an unusual account combination -- Benford's-"
    "law and round-number screening are population-level proxies for this, not a substitute.",
    "No consolidation or group-audit procedure is performed. This tool analyses one entity's "
    "trial balance at a time; it does not eliminate intercompany balances or address component "
    "auditors.",
    "CARO 2020 clauses are raised as information requests only, never as a reporting "
    "conclusion. Several of CARO's clauses (e.g. physical verification, title deeds, "
    "whistle-blower complaints) cannot be evidenced from a trial balance at all.",
    "No Schedule III presentation-compliance check is performed. This tool classifies trial-"
    "balance lines into Schedule III heads; it does not ingest or review a drafted financial "
    "statement's line-item completeness or disclosure notes.",
    "No opinion or conclusion is expressed on financial statement misstatement, fraud, "
    "non-compliance, recoverability, or going concern. Every output here is a risk indicator "
    "for further audit work, never a finding in itself.",
]

# One line per SCOPE_LIMITATIONS item, for the Word report's page budget -- the Legend
# sheet carries the full explanation of each; the reader who wants "why" goes there.
SCOPE_LIMITATIONS_BRIEF = [
    "No statistical sampling by default — every screen tests the full population.",
    "No journal-entry-level testing — this tool reads GL balances, not transactions.",
    "No consolidation/group-audit procedures — single-entity analysis only.",
    "CARO 2020 clauses are raised as information requests, never a conclusion.",
    "No Schedule III presentation-compliance check — TB-level classification only.",
    "No opinion on misstatement, fraud, non-compliance, or going concern.",
]

def apply_safe_wording(text: str, append_disclaimer: bool = True) -> str:
    """Deterministic post-processing pass: substitutes banned assurance-language
    phrases for safe risk-indicator phrasing (word-boundary regex, not naive
    str.replace), then appends the mandatory disclaimer verbatim unless
    append_disclaimer=False (use False for per-finding text where the
    disclaimer should be attached once at the payload level instead --
    see SAFE_WORDING_DISCLAIMER). Called by every narrative-producing tool
    before it returns text -- never rely on the LLM prompt alone."""
    if not text:
        return SAFE_WORDING_DISCLAIMER if append_disclaimer else text

    filtered = text
    substitutions = 0
    for pattern in _ASSURANCE_WORDS:
        filtered, n = re.subn(pattern, "", filtered, flags=re.IGNORECASE)
        substitutions += n
    for pattern, replacement in _SAFE_WORDING_RULES:
        filtered, n = re.subn(pattern, replacement, filtered, flags=re.IGNORECASE)
        substitutions += n

    if substitutions:
        # An unusually high count on one run is itself a signal the
        # underlying narrative-generation prompt is drifting toward
        # assurance language -- log, never silently drop.
        logger.info("apply_safe_wording: %d substitution(s) made", substitutions)

    filtered = re.sub(r"[ \t]{2,}", " ", filtered).strip()

    if append_disclaimer:
        filtered = f"{filtered} {SAFE_WORDING_DISCLAIMER}" if filtered else SAFE_WORDING_DISCLAIMER

    return filtered

__all__ = [
    '_PACKS',
    'KnowledgePackError',
    'pack_path',
    'load_pack',
    'pack_versions',
    'debit_anchors',
    'credit_anchors',
    'normal_debit_heads',
    'normal_credit_heads',
    'is_bank_cash_named',
    'sensitive_rules',
    'sensitive_keywords',
    'risk_rule_weights',
    'mapping_confidence_thresholds',
    'verify_packs',
    '_GSTIN_RE',
    '_PAN_RE',
    '_BANK_CONTEXT',
    '_BANK_RE',
    '_BARE_ACCOUNT_RE',
    '_AADHAAR_RE',
    '_EMPLOYEE_RE',
    '_IFSC_RE',
    '_mask_gstin',
    '_mask_pan',
    '_mask_bank',
    '_mask_aadhaar',
    'mask_text',
    'mask_value',
    'mask_structure',
    'find_identifiers',
    'Standard',
    '_PL_MAIN_HEAD_OVERRIDES',
    '_INCOME_MAIN_HEADS',
    'load_taxonomy',
    '_IND_AS_MARKERS___taxonomy',
    '_AS_MARKERS___taxonomy',
    'detect_standard',
    '_derive_account_type_table',
    '_account_type_table',
    '_MAIN_HEAD_ALIASES',
    'derive_account_type',
    'build_taxonomy_tree',
    '_norm___taxonomy',
    '_normalized_index',
    'snap_to_taxonomy',
    'VALID_ASSERTIONS',
    'VALID_RISK_BASIS',
    'VALID_RATINGS',
    'RATING_RANK',
    'make_record',
    'validate_record',
    '_COMPANY_KWS',
    '_CURRENCY_KWS',
    '_TYPE_KWS',
    '_CODE_KWS',
    '_NAME_KWS',
    '_DEBIT_RE',
    '_CREDIT_RE',
    '_OPENING_RE',
    '_CLOSING_RE',
    '_TXN_RE',
    '_BALANCE_RE',
    '_TOTAL_PREFIXES',
    '_SCALE_PATTERNS',
    '_norm___ingest_shared',
    '_id_role',
    '_money_role',
    '_detect_scale',
    '_ffill',
    '_score_row',
    '_find_header',
    '_looks_text',
    '_generate_column_mapping',
    '_validate_input_file',
    '_stringify_grid',
    '_load_workbook',
    '_detect_formula_cells',
    '_enumerate_sheets',
    '_apply_merged_cells',
    '_extract_sheet_dataframe',
    '_dedupe_names',
    '_normalize_duplicate_columns',
    '_generate_headers_if_missing',
    'validate_dataframe_schema',
    '_NUMERIC_DTYPES',
    'WorksheetClassifier',
    '_normalize_gl_code',
    '_fy_year',
    'build_tb_doc_id',
    'SCHEDULES',
    'MANIFEST_FILENAME',
    '_EXCEL_SHEET_NAME_LIMIT',
    'validate_registry',
    'write_manifest',
    'read_manifest',
    'sheets_for_section',
    'reference_line',
    '_SECTION_CAP',
    '_load',
    '_unavailable',
    '_capped',
    '_write_phase4_sections',
    '_write_schedule_appendix',
    '_write_front_sections',
    'NUMBER_WORDS',
    '_ESTIMATION_PACK',
    'FCY_KEYWORDS',
    'safe_fmt',
    '_brief',
    'resolve_severity',
    'hierarchy_path',
    'classify_row',
    'load_canonical_tb',
    'movement_flag',
    'control_totals',
    'movement_rows_from_canonical',
    '_OFFSETTING_PAIR_TOLERANCE_PCT',
    'concentration_rows_from_canonical',
    '_collapse_offsetting_pairs',
    '_ESTIMATION_RISK_KEYWORDS',
    'is_estimation_risk_account',
    '_DORMANT_ESTIMATION_EXPLANATION',
    '_DORMANT_ORDINARY_EXPLANATION',
    'dormant_rows_from_canonical',
    'resolve_entity_and_fy',
    'resolve_artifact_path',
    'safe_load_json',
    'safe_load_parquet',
    'first_present_column',
    'row_fsli',
    'row_fs_head',
    'sign_convention_stats',
    'SIGN_CONVENTION_STATS_BASIS_LABEL',
    'canonical_row_count',
    'find_fsli_component',
    'gl_total_by_keywords',
    'compute_total_revenue',
    '_LANGUAGE_PACK',
    '_SAFE_WORDING_RULES',
    '_ASSURANCE_WORDS',
    'SAFE_WORDING_DISCLAIMER',
    'SCOPE_LIMITATIONS',
    'SCOPE_LIMITATIONS_BRIEF',
    'apply_safe_wording',
]
