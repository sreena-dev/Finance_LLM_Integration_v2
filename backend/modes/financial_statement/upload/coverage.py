"""Which questions this uploaded document can actually answer.

Every narrative lookup filters on a field the ingestion service has to populate:
``chunk_type`` for heading anchors, ``section_breadcrumb`` for the notes scope,
heading numbering for policy sub-notes, ``page_ocr_start`` for passage order,
table captions for schedule notes. When one of those is missing, the lookup that
depends on it returns nothing — and an empty result is indistinguishable from a
filing that genuinely lacks the disclosure.

``sieve.py`` makes that visible *at question time*. This module makes it visible
at **upload time**, which is where the spec asks for it. Section 4.3 lists the
input-quality checks to run before analysis and says what to do when one fails:
*"Report missing component; restrict applicable checks."* A coverage line saying
"no numbered policy headings were recovered, so accounting-policy lookup will not
work on this document" is exactly that, and it reaches the auditor before they
have drawn a conclusion from a silent miss rather than after.

Three states, deliberately not two:

``viable``
    Candidates exist. It says nothing about whether the answer will be right.
``degraded``
    Candidates exist only via a relaxed filter, or a supporting service is down.
    The lookup works and will say it was weakened.
``unavailable``
    No candidates at all. The lookup cannot work on this document, and any
    "not found" it returns carries no information about the filing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .narrative import (
    POLICY_SUBHEADING_RE,
    _crumb,
    _ilike,
    _is_consolidated,
    _SCHEDULE_HEADING_RE,
)

VIABLE = "viable"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"

#: AuditorReportTools._AUDITOR_VOICE_PATTERNS. Duplicated rather than imported
#: because this module must be importable without the vendored pipeline on the
#: path -- the ingestion-side tests exercise it standalone.
AUDITOR_VOICE = (
    "in our opinion", "we report that", "we have audited",
    "referred to in paragraph", "annexure", "companies (auditor's report) order",
    "caro",
)


@dataclass
class Path:
    """One retrieval path and whether this document supports it."""

    name: str
    state: str
    detail: str
    candidates: int = 0
    #: What the user or the model should do instead, when it cannot work.
    workaround: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Coverage:
    paths: list[Path] = field(default_factory=list)

    @property
    def unavailable(self) -> list[Path]:
        return [p for p in self.paths if p.state == UNAVAILABLE]

    @property
    def degraded(self) -> list[Path]:
        return [p for p in self.paths if p.state == DEGRADED]

    def as_dict(self) -> dict[str, Any]:
        return {
            "paths": [p.as_dict() for p in self.paths],
            "unavailable": [p.name for p in self.unavailable],
            "degraded": [p.name for p in self.degraded],
        }

    def report(self) -> str:
        """The section appended to the extraction-quality report."""
        if not self.paths:
            return ""
        lines = ["WHAT CAN BE ASKED OF THIS DOCUMENT", ""]
        for path in self.paths:
            marker = {"viable": "ok", "degraded": "limited", "unavailable": "NOT AVAILABLE"}[path.state]
            lines.append(f"  [{marker}] {path.name}: {path.detail}")
            if path.workaround:
                lines.append(f"           {path.workaround}")
        if self.unavailable:
            lines.append("")
            lines.append(
                "For any lookup marked NOT AVAILABLE, a 'not found' result says "
                "nothing about whether the filing contains the disclosure — the "
                "scan did not yield what that lookup needs. Do not report it as "
                "an omission by the entity."
            )
        return "\n".join(lines)


def _texts(upload) -> list[dict]:
    try:
        return upload.narrative()
    except Exception:
        return []


def _tables(upload) -> list[dict]:
    try:
        return upload.financial_tables()
    except Exception:
        return []


def assess(upload) -> Coverage:
    """Probe every field the narrative sieves depend on."""
    texts = _texts(upload)
    tables = _tables(upload)
    standalone = [t for t in texts if not _is_consolidated(t)]
    headings = [t for t in standalone if t.get("chunk_type") == "heading"]
    bodies = [t for t in standalone if t.get("chunk_type") in ("text", "list")]

    coverage = Coverage()

    # --- the safety filter itself -----------------------------------------
    if texts and not standalone:
        coverage.paths.append(Path(
            name="standalone narrative",
            state=UNAVAILABLE,
            detail=(
                f"all {len(texts)} narrative chunks are marked consolidated, so every "
                "standalone lookup excludes them"
            ),
            candidates=0,
            workaround="Confirm whether this file is the consolidated set; "
                       "standalone questions cannot be answered from it.",
        ))

    # --- policy notes ------------------------------------------------------
    numbered = [h for h in headings if POLICY_SUBHEADING_RE.match((h.get("content") or "").strip())]
    not_schedule = [h for h in headings if not _SCHEDULE_HEADING_RE.match((h.get("content") or "").strip())]
    scoped = [h for h in headings if _ilike(_crumb(h), "notes to")]
    if numbered:
        coverage.paths.append(Path(
            "accounting-policy lookup", VIABLE,
            f"{len(numbered)} numbered policy heading(s) recovered", len(numbered)))
    elif not_schedule:
        coverage.paths.append(Path(
            "accounting-policy lookup", DEGRADED,
            "no N.M-numbered policy headings survived the scan, so the lookup "
            f"falls back to {len(not_schedule)} unnumbered heading(s)",
            len(not_schedule),
            "Results will be less precise; check the heading each policy came from.",
        ))
    else:
        coverage.paths.append(Path(
            "accounting-policy lookup", UNAVAILABLE,
            "no usable policy headings were recovered from this scan", 0,
            "Ask for the policy by its note number instead (lookup_report_reference).",
        ))
    if numbered and not scoped:
        coverage.paths[-1].detail += "; the notes-section breadcrumb is absent and will be relaxed"
        coverage.paths[-1].state = DEGRADED

    # --- disclosure search -------------------------------------------------
    if bodies:
        coverage.paths.append(Path(
            "disclosure / notes search", VIABLE,
            f"{len(bodies)} narrative passage(s) available", len(bodies)))
    else:
        coverage.paths.append(Path(
            "disclosure / notes search", UNAVAILABLE,
            "no narrative prose was recovered from this scan — it may be tables only", 0,
            "Figures remain available; narrative questions cannot be answered.",
        ))

    # --- compliance passage ------------------------------------------------
    compliance = [
        t for t in standalone
        if _ilike(t.get("content"), "statement of compliance")
        or _ilike(t.get("content"), "basis of preparation")
        or ("accordance with" in (t.get("content") or "").lower()
            and "accounting standard" in (t.get("content") or "").lower())
    ]
    coverage.paths.append(Path(
        "reporting-framework passage",
        VIABLE if compliance else UNAVAILABLE,
        f"{len(compliance)} candidate passage(s)" if compliance
        else "no Statement of Compliance or Basis of Preparation passage was recovered",
        len(compliance),
        None if compliance else
        "The framework can still be inferred from the statements themselves, but it "
        "cannot be quoted from the entity's own wording.",
    ))

    # --- auditor's report: CARO, Rule 11(g), going concern ------------------
    auditor = [
        t for t in standalone
        if any(_ilike(t.get("content"), v) for v in AUDITOR_VOICE)
    ]
    coverage.paths.append(Path(
        "auditor's report / CARO review",
        VIABLE if auditor else UNAVAILABLE,
        f"{len(auditor)} passage(s) in the auditor's voice" if auditor
        else "no passage in the auditor's voice was recovered",
        len(auditor),
        None if auditor else
        "Upload the independent auditor's report (IARSFS) alongside the statements — "
        "it is a separate file and CARO and Rule 11(g) cannot be reviewed without it.",
    ))

    # --- schedule notes ----------------------------------------------------
    captioned = [t for t in tables if (t.get("table_title") or t.get("table_description"))]
    coverage.paths.append(Path(
        "schedule notes / account-area review",
        VIABLE if captioned else UNAVAILABLE,
        f"{len(captioned)} of {len(tables)} table(s) carry a caption or description"
        if tables else "no tables were extracted",
        len(captioned),
        None if captioned else
        "Tables cannot be matched by name on this document; ask by note number.",
    ))

    # --- passage assembly --------------------------------------------------
    paged = [t for t in texts if t.get("page_ocr_start")]
    if texts and len(paged) < len(texts):
        coverage.paths.append(Path(
            "passage assembly", DEGRADED,
            f"{len(texts) - len(paged)} narrative chunk(s) carry no page number, so "
            "passages assembled around them may be out of order",
            len(paged),
            "Citations for those passages will be missing a page reference.",
        ))

    return coverage
