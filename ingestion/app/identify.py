"""Working out what the uploaded document *is*, from the document itself.

Three things have to be established before any analysis can run: which financial
year the statements cover, which entity filed them, and which reporting
framework they were prepared under. Spec section 5.1 requires the last of those
to be reported as an inference with its signals and a confidence, never as a
certainty, and this module treats all three the same way.

**Nothing here reads the filename.** The corpus is laid out as
``data/MH-CPSU-IAM-052/2022-23/MH-CPSU-ITSL-048_2022-23_SFS_....pdf`` -- entity
code, year and document type all sit right there in the path, and using them
would look like it worked. It is metadata somebody typed at scan time, it is
routinely wrong in practice, and an audit finding traceable to a filename rather
than to the statement is worthless. The directory layout is used in the test
suite as ground truth to check this module against, and nowhere else.

Statement-type values are the vocabulary
``ComplianceTools._STATEMENT_TITLE_KEYWORDS`` already uses -- ``balance_sheet``,
``profit_loss``, ``cash_flow``, ``statement_of_equity`` -- because that is what
``_find_statement_tables`` filters ``financial_stmt_type`` on. Emitting anything
else here means the existing compliance, ratio and tie-out tools find no
statements at all.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import Identification

# ---------------------------------------------------------------------------
# Financial year
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "as at 31st March, 2023" / "for the year ended 31 March 2023"
_DATE_LONG = re.compile(
    r"(?:as\s+at|as\s+on|year\s+ended|period\s+ended|ended)\s+"
    r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?([A-Za-z]{3,9})[,\s]+(\d{4})",
    re.I,
)
# "31-Mar-23" / "31.03.2023" / "31/03/23"
_DATE_SHORT = re.compile(r"\b(\d{1,2})[-/.]\s*([A-Za-z]{3,9}|\d{1,2})[-/.]\s*(\d{2,4})\b")
# "F.Y. 2022-23" / "FY 2022-2023" / "financial year 2022-23"
_FY_EXPLICIT = re.compile(r"(?:f\.?\s*y\.?|financial\s+year)\s*[:\-]?\s*(\d{4})\s*[-/]\s*(\d{2,4})", re.I)

_TITLE_LINE = re.compile(
    r"(balance\s+sheet|statement\s+of\s+profit\s+and\s+loss|profit\s+and\s+loss|"
    r"cash\s+flow|changes\s+in\s+equity)",
    re.I,
)


def _normalise_year(raw: str) -> int | None:
    try:
        year = int(raw)
    except ValueError:
        return None
    if year < 100:
        year += 2000
    if 1990 <= year <= 2100:
        return year
    return None


def _fy_from_reporting_date(day: int, month: int, year: int) -> str | None:
    """Indian financial year containing a given reporting date.

    The year runs 1 April to 31 March, so a balance sheet "as at 31 March 2023"
    closes FY 2022-23 -- the label is the year it *started* in. Getting this
    backwards mislabels every uploaded statement by one year, and would then
    silently misalign a multi-year comparison.
    """
    if not (1 <= month <= 12):
        return None
    start = year - 1 if month <= 3 else year
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def _dates_in(text: str) -> list[tuple[int, int, int]]:
    found: list[tuple[int, int, int]] = []
    for m in _DATE_LONG.finditer(text):
        month = _MONTHS.get(m.group(2).lower())
        year = _normalise_year(m.group(3))
        if month and year:
            found.append((int(m.group(1)), month, year))
    for m in _DATE_SHORT.finditer(text):
        raw_month = m.group(2)
        month = _MONTHS.get(raw_month.lower()) if raw_month.isalpha() else int(raw_month)
        year = _normalise_year(m.group(3))
        if month and year and 1 <= month <= 12:
            found.append((int(m.group(1)), month, year))
    return found


def detect_financial_year(pages_text: list[str]) -> tuple[str | None, str, list[str]]:
    """``(financial_year, confidence, evidence_lines)``.

    Evidence is weighted by where it was found. A date on a *statement title*
    line ("Balance Sheet as at 31st March, 2023") names the reporting date
    outright. A date in a column header is ambiguous, because the comparative
    column carries the prior year in exactly the same form -- so those are
    collected but the latest is preferred, since the current year is always the
    later of the pair.
    """
    titled: Counter[str] = Counter()
    loose: Counter[str] = Counter()
    evidence: list[str] = []

    for text in pages_text:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) > 300:
                continue

            for m in _FY_EXPLICIT.finditer(stripped):
                start = _normalise_year(m.group(1))
                if start:
                    fy = f"{start}-{str((start + 1) % 100).zfill(2)}"
                    titled[fy] += 3
                    evidence.append(f'explicit: "{stripped[:120]}"')

            dates = _dates_in(stripped)
            if not dates:
                continue
            is_title = bool(_TITLE_LINE.search(stripped))
            for day, month, year in dates:
                # 31 March and 1 April are the year boundary; anything else in a
                # statement heading is a transaction date, not a reporting date.
                if not ((month == 3 and day >= 28) or (month == 4 and day <= 2)):
                    continue
                fy = _fy_from_reporting_date(day, month, year)
                if not fy:
                    continue
                if is_title:
                    titled[fy] += 2
                    if len(evidence) < 12:
                        evidence.append(f'statement title: "{stripped[:120]}"')
                else:
                    loose[fy] += 1

    if titled:
        best = max(titled.items(), key=lambda kv: (kv[1], kv[0]))
        runners = [fy for fy, n in titled.items() if fy != best[0]]
        # Comparatives guarantee a second year appears; that is expected, not a
        # conflict. Confidence drops only when the winner is not clear.
        confidence = "high" if best[1] >= 4 else "medium"
        if runners and titled[max(runners, key=lambda f: titled[f])] >= best[1]:
            confidence = "low"
        return best[0], confidence, evidence[:8]

    if loose:
        # No statement title carried a date -- fall back to the latest reporting
        # date seen anywhere, which is the current year when comparatives are
        # present.
        best = max(loose)
        evidence.append("no dated statement title found; used the latest reporting date in the document")
        return best, "low", evidence[:8]

    return None, "low", ["no reporting date could be read from the document"]


# ---------------------------------------------------------------------------
# Reporting framework
# ---------------------------------------------------------------------------

_IND_AS_POLICY = re.compile(r"indian\s+accounting\s+standard|\bind\s*as\b", re.I)
# Two patterns, deliberately with different casing rules. The prose forms are
# case-insensitive; the bare "AS 22" form must stay case-SENSITIVE, because
# case-insensitively it matches the English word "as" followed by any number and
# fires on almost every page ("as at 31 March 2023").
_AS_POLICY_PROSE = re.compile(
    r"companies\s*\(\s*accounting\s+standards\s*\)\s*rules|"
    r"\baccounting\s+standard\s*[-\s]*\d{1,2}\b",
    re.I,
)
_AS_POLICY_SHORT = re.compile(r"\bAS[-\s]?\d{1,2}\b")


def _cites_as(text: str) -> bool:
    return bool(_AS_POLICY_PROSE.search(text) or _AS_POLICY_SHORT.search(text))
_OCI = re.compile(r"other\s+comprehensive\s+income", re.I)
_SOCIE = re.compile(r"statement\s+of\s+changes\s+in\s+equity", re.I)
_NBFC = re.compile(r"non[-\s]?banking\s+financial|\bNBFC\b|reserve\s+bank\s+of\s+india\s+.{0,40}registration", re.I)
_DIVISION = re.compile(r"division\s+(I{1,3}|1|2|3)\b", re.I)
_BANKING = re.compile(r"banking\s+regulation\s+act|third\s+schedule\s+to\s+the\s+banking", re.I)
_INSURANCE = re.compile(r"\bIRDAI\b|insurance\s+regulatory\s+and\s+development", re.I)


def detect_framework(full_text: str) -> tuple[str | None, str | None, str, list[str], list[str]]:
    """``(framework, division, confidence, signals, conflicts)``.

    Follows the ``detect_framework`` pseudocode in spec section 5.1. Banks and
    insurers are singled out because they do **not** use Schedule III at all --
    running Schedule III disclosure checks against an IRDAI-format filing
    produces a page of findings that are all artefacts of the wrong rulebook.
    """
    signals: list[str] = []
    conflicts: list[str] = []

    if _BANKING.search(full_text):
        return ("Banking (BR Act Third Schedule)", None, "medium",
                ["Banking Regulation Act / Third Schedule referenced"],
                ["Schedule III does not apply to banks; presentation checks must use the BR Act forms"])
    if _INSURANCE.search(full_text):
        return ("Insurance (IRDAI forms)", None, "medium",
                ["IRDAI referenced"],
                ["Schedule III does not apply to insurers; presentation checks must use the IRDAI formats"])

    ind_as = bool(_IND_AS_POLICY.search(full_text))
    has_oci = bool(_OCI.search(full_text))
    has_socie = bool(_SOCIE.search(full_text))
    as_rules = _cites_as(full_text)
    nbfc = bool(_NBFC.search(full_text))

    score = 0
    if ind_as:
        score += 2
        signals.append("accounting policies cite Indian Accounting Standards / Ind AS")
    if has_socie:
        score += 1
        signals.append("a Statement of Changes in Equity is presented")
    if has_oci:
        score += 1
        signals.append("Other Comprehensive Income is presented")
    if as_rules and not ind_as:
        score -= 2
        signals.append("policies cite the Companies (Accounting Standards) Rules / AS numbers")

    division = None
    m = _DIVISION.search(full_text)
    if m:
        raw = m.group(1).upper()
        division = {"I": "I", "1": "I", "II": "II", "2": "II", "III": "III", "3": "III"}.get(raw)
        if division:
            signals.append(f"Schedule III Division {division} format referenced")

    if nbfc and (ind_as or division == "III"):
        framework = "NBFC Ind AS"
        division = division or "III"
        confidence = "medium"
        signals.append("NBFC indicators present")
    elif score >= 2:
        framework = "Ind AS"
        division = division or "II"
        confidence = "high" if score >= 3 else "medium"
    elif score <= -1 or (as_rules and not has_socie and not has_oci):
        framework = "AS"
        division = division or "I"
        confidence = "medium"
    else:
        framework = None
        confidence = "low"
        conflicts.append(
            "The reporting framework could not be determined from the package. "
            "Only framework-neutral checks are safe until it is confirmed."
        )

    if ind_as and as_rules:
        conflicts.append(
            "The document references both Ind AS and the Companies (Accounting "
            "Standards) Rules. One of them is likely a comparative-period or "
            "transition reference; confirm which framework applies for the year."
        )

    return framework, division, confidence, signals, conflicts


# ---------------------------------------------------------------------------
# Entity, flavour, statement type
# ---------------------------------------------------------------------------

_LEGAL_SUFFIX = re.compile(
    r"\b(limited|ltd\.?|private\s+limited|pvt\.?\s*ltd\.?|corporation|"
    r"corpn\.?|company|nigam|udyog|bank)\b", re.I,
)
_NOISE = re.compile(r"chartered\s+accountant|auditor|annexure|independent|firm\s+regn", re.I)


def detect_entity(pages_text: list[str]) -> tuple[str | None, list[str]]:
    """The entity name, taken from the running header repeated across pages.

    A filed statement prints the entity name at the top of nearly every sheet;
    a line that recurs on many pages and carries a legal suffix is the entity
    far more reliably than the first bold line on page one, which is as often
    the auditor's letterhead.
    """
    # Two tiers. A repeated header carrying a legal suffix is the strongest
    # signal and wins outright. Failing that, a repeated markdown HEADING is
    # accepted even without a suffix -- observed on OD-SPSU-SO-032, a section 8
    # company whose registered name is simply "Startup Odisha". Requiring a
    # suffix leaves every such entity unidentified, and section 8 companies are
    # a real part of this corpus.
    suffixed: Counter[str] = Counter()
    headings: Counter[str] = Counter()

    for text in pages_text:
        seen_on_page: set[str] = set()
        lines = text.splitlines()
        for i, raw in enumerate(lines[:8]):
            line = raw.strip().strip("|").strip()
            is_heading = line.startswith("#")
            # Strip markdown heading markers as well as table pipes: docling
            # exports the running page header as "## IDBI Trusteeship Services
            # Ltd", and without this the hashes reach the UI and every citation.
            stripped = line.lstrip("#").strip()
            if not (4 < len(stripped) < 90):
                continue
            # A parenthetical is a description, not a name. Without this,
            # "(A Company Registered under section 8 of The Companies Act,
            # 2013)" -- printed under the entity name on every OD-SPSU sheet --
            # matches the legal-suffix rule on the word "Company" and outranks
            # the actual heading above it.
            if stripped.startswith("("):
                continue
            # Check the FOLLOWING line too. An auditor's letterhead is two
            # lines -- "K SWAIN & CO" then "Chartered Accountants" -- and a
            # same-line-only filter returns the audit firm as the entity, which
            # is both wrong and the kind of wrong that looks plausible.
            following = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if _NOISE.search(stripped) or _NOISE.search(following):
                continue
            key = stripped.lower()
            if key in seen_on_page:
                continue
            seen_on_page.add(key)
            if _LEGAL_SUFFIX.search(stripped):
                suffixed[stripped] += 1
            elif is_heading and any(c.isalpha() for c in stripped):
                headings[stripped] += 1

    if suffixed:
        name, hits = suffixed.most_common(1)[0]
        return name, [f'repeated page header carrying a legal suffix, on {hits} page(s): "{name}"']

    if headings:
        name, hits = headings.most_common(1)[0]
        return name, [
            f'repeated page heading on {hits} page(s): "{name}". No legal suffix '
            "was present, so confirm this is the reporting entity's registered name."
        ]

    return None, ["no repeated entity header was found in the document"]


def detect_flavour(full_text: str) -> str | None:
    lowered = full_text.lower()
    standalone = lowered.count("standalone")
    consolidated = lowered.count("consolidated")
    if standalone == consolidated == 0:
        return None
    return "consolidated" if consolidated > standalone else "standalone"


#: Ordered so the more specific title wins: "Statement of Changes in Equity"
#: contains neither "balance sheet" nor "profit and loss", but a combined
#: "Balance Sheet and Statement of Profit and Loss" heading must not be claimed
#: by whichever pattern happens to be tried first.
_STATEMENT_PATTERNS = [
    ("statement_of_equity", re.compile(r"changes\s+in\s+equity", re.I)),
    ("cash_flow", re.compile(r"cash\s+flow", re.I)),
    ("balance_sheet", re.compile(r"balance\s+sheet", re.I)),
    ("profit_loss", re.compile(r"profit\s+(and|&)\s+loss|statement\s+of\s+profit", re.I)),
]


def classify_statement(title: str | None) -> str | None:
    """Map a table title onto the vocabulary the existing FS tools filter on."""
    if not title:
        return None
    for statement_type, pattern in _STATEMENT_PATTERNS:
        if pattern.search(title):
            return statement_type
    return None


#: "Notes to balance sheet ...", "Notes forming part of the profit and loss".
#: A note schedule mentions the statement it supports and is NOT that statement;
#: classifying it as one puts the wrong table in front of every tie-out check.
_NOTE_HEADING_RE = re.compile(r"^\s*notes?\b|forming\s+part\s+of", re.I)


def classify_statement_from_page(page_text: str) -> str | None:
    """Statement type for a page whose tables carry no usable caption.

    Scanned filings mark up no captions at all, so docling supplies none and the
    line above a table is often a stray figure or a units note. But the page
    itself almost always prints the statement's own heading, and a page headed
    "Balance Sheet as at 31st March, 2023" is the balance sheet.

    Only *heading-shaped* lines count: short, containing the statement name, and
    not a note schedule. Without those guards "Notes to balance sheet for the
    year ended 31st March, 2023" -- which is the heading on a share-capital note
    page in this corpus -- would classify that page as the balance sheet.
    """
    for line in (page_text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped or len(stripped) > 120 or stripped.startswith("|"):
            continue
        if _NOTE_HEADING_RE.search(stripped):
            continue
        found = classify_statement(stripped)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(
    r"(?:amount|figures|rupees|rs\.?|inr|₹)[^\n]{0,30}?"
    r"\b(crore|crores|lakh|lakhs|million|millions|billion|thousand|thousands|'?000)\b",
    re.I,
)
_CURRENCY_RE = re.compile(r"\b(INR|Rs\.?|USD|EUR|GBP)\b|[₹$€£]", re.I)
_SCALE_CANON = {
    "crores": "crore", "lakhs": "lakh", "millions": "million",
    "thousands": "thousand", "'000": "thousand", "000": "thousand",
}


def detect_units(text: str) -> tuple[str | None, str | None]:
    """``(scale, currency)`` declared near a table, or ``(None, None)``.

    Never guesses. ``UnitResolver`` in the existing tool library is explicit
    that a document declaring no scale must be reported as declaring none --
    "a guessed scale is the one outcome worse than an absent one" -- because the
    same digits mean different things three orders of magnitude apart.
    """
    scale = None
    m = _UNIT_RE.search(text)
    if m:
        raw = m.group(1).lower().strip("'")
        scale = _SCALE_CANON.get(raw, raw)

    currency = None
    c = _CURRENCY_RE.search(text)
    if c:
        token = (c.group(0) or "").upper().strip(".")
        currency = {
            "RS": "INR", "₹": "INR", "INR": "INR",
            "$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR",
            "£": "GBP", "GBP": "GBP",
        }.get(token, None)

    return scale, currency


def identify(pages_text: list[str]) -> Identification:
    """Run every detector and assemble the reported inference."""
    full_text = "\n".join(pages_text)

    fy, fy_conf, fy_evidence = detect_financial_year(pages_text)
    entity, entity_evidence = detect_entity(pages_text)
    framework, division, fw_conf, signals, conflicts = detect_framework(full_text)

    return Identification(
        financial_year=fy,
        fy_confidence=fy_conf,
        fy_evidence=fy_evidence,
        entity_name=entity,
        entity_evidence=entity_evidence,
        framework=framework,
        framework_division=division,
        framework_confidence=fw_conf,
        framework_signals=signals,
        statement_flavour=detect_flavour(full_text),
        unresolved_conflicts=conflicts,
    )
