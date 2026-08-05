"""Document-sourced audit evidence: quantified risk items from uploaded PDFs.

The client-format audit report includes a "risk areas with quantified effect"
table whose figures can only come from supporting documents (annual report /
auditor comments), never from the trial balance alone. This module harvests
relevant page chunks from the uploads store, has an LLM extract candidate items
as strict JSON, then VALIDATES deterministically: an item survives only if its
amount text appears verbatim in the cited chunk. Amount parsing and the
cumulative effect are pure Python — the LLM never does arithmetic.
"""

from __future__ import annotations

import json
import logging
import re

import psycopg2

from yukta_rag.audit.audit_mapping import _FSLI_RULES

logger = logging.getLogger("yukta_rag.audit.audit_doc_evidence")

# fixed harvest queries — the audit-relevant themes of an annual report /
# auditor-comment document (embedding search, so phrasing is thematic).
_HARVEST_QUERIES = [
    "auditor's opinion qualifications adverse observations comments on the accounts",
    "interest or borrowing cost capitalised on capital work in progress; suspended or stopped project",
    "provision not made; liability understated; contingent liability not disclosed",
    "trade receivables impairment expected credit loss ageing doubtful recovery",
    "GST TDS provident fund statutory dues delay default returns not filed",
    "going concern; accumulated losses; erosion of net worth",
    "prior period error restatement retrospective correction of accounts",
    "government grant or subsidy accounting treatment and utilisation",
]
_MAX_CHUNKS = 24
_CHUNK_TEXT_CAP = 1600

_DIRECTION_PHRASES = {
    "loss_understated": "loss potentially understated",
    "loss_overstated": "loss potentially overstated",
    "asset_overstated": "assets potentially overstated",
    "liability_understated": "liabilities potentially understated",
    "uncertain": "direction of effect not stated in the source",
}

_AMOUNT_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(crore|crores|cr\.?|lakh|lakhs|lacs|million|mn|billion|bn)?",
    re.I,
)
_UNIT_CANON = {
    "crore": "crore", "crores": "crore", "cr": "crore", "cr.": "crore",
    "lakh": "lakh", "lakhs": "lakh", "lacs": "lakh",
    "million": "million", "mn": "million", "billion": "billion", "bn": "billion",
}


def _parse_inr_amount(text: str) -> tuple[float | None, str | None]:
    """Parse "₹295.34 crore" → (295.34, "crore"). Deterministic; None when absent."""
    if not text:
        return None, None
    m = _AMOUNT_RE.search(text.replace("₹", " "))
    if not m:
        return None, None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = _UNIT_CANON.get((m.group(2) or "").lower().rstrip("."), None)
    return value, unit


def _norm(text: str) -> str:
    """Normalise for verbatim-substring checks (whitespace/commas collapse)."""
    return re.sub(r"[\s,]+", "", (text or "")).lower()


# flat FSLI keyword list (from the audit mapping rules) for head_affected mapping
_FLAT_FSLI_KWS: list[tuple[str, tuple[str, ...]]] = [
    (fsli, kws) for rules in _FSLI_RULES.values() for fsli, kws in rules
]


def _map_head(head: str) -> tuple[str, bool]:
    """Map a document phrase like "CWIP" to the TB FSLI vocabulary."""
    text = (head or "").lower()
    for fsli, kws in _FLAT_FSLI_KWS:
        if text and (text in fsli.lower() or any(k in text for k in kws)):
            return fsli, True
    return head, False


def _extract_json(raw: str) -> dict | None:
    """Best-effort strict-JSON parse of an agent response."""
    if not raw:
        return None
    cleaned = re.sub(r"\[(?:⚠️|✅)[^\]]*\]", "", raw)
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned.strip(), flags=re.M).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_items(items: list, chunks: list[dict]) -> list[dict]:
    """Keep only items whose amount text appears verbatim in the cited chunk."""
    by_ref = {f"C{i}": c for i, c in enumerate(chunks, 1)}
    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        chunk = by_ref.get(str(it.get("chunk_ref") or "").strip())
        if chunk is None:
            continue
        chunk_norm = _norm(chunk.get("text", ""))
        amount_text = str(it.get("amount_text") or "").strip()
        if amount_text and _norm(amount_text) not in chunk_norm:
            continue  # LLM-altered figure -> drop the item entirely
        quote = str(it.get("quote") or "").strip()
        if quote and _norm(quote)[:80] not in chunk_norm:
            quote = ""  # keep the item but not an unverifiable quote
        value, unit = _parse_inr_amount(amount_text)
        direction = str(it.get("effect_direction") or "uncertain")
        if direction not in _DIRECTION_PHRASES:
            direction = "uncertain"
        heads_raw = it.get("head_affected") or []
        if isinstance(heads_raw, str):
            heads_raw = [heads_raw]
        heads, mapped_flags = [], []
        for h in heads_raw[:4]:
            mapped, ok = _map_head(str(h))
            heads.append(mapped)
            mapped_flags.append(ok)
        src = {"doc_id": chunk.get("doc_id"), "filename": chunk.get("filename"),
               "page_no": chunk.get("page_no")}
        phrase = _DIRECTION_PHRASES[direction]
        effect_text = (f"Potential effect: {phrase}"
                       + (f" {amount_text}" if amount_text else "")
                       + f" (per {src['filename']} p.{src['page_no']})")
        rows.append({
            "n": len(rows) + 1,
            "item": str(it.get("item") or "").strip()[:300],
            "head_affected": heads,
            "effect_direction": direction,
            "effect_text": effect_text,
            "amount": value,
            "unit": unit,
            "amount_text": amount_text,
            "source": src,
            "quote": quote[:400],
            "fsli_mapped": any(mapped_flags) if mapped_flags else False,
        })
    return rows


def _cumulative(rows: list[dict]) -> dict:
    """Pure-Python aggregation of parseable amounts, single-unit only."""
    valued = [r for r in rows if r["amount"] is not None]
    units = {r["unit"] for r in valued if r["unit"]}
    unit = units.pop() if len(units) == 1 else None
    usable = [r for r in valued if unit is None or r["unit"] in (unit, None)]
    by_direction: dict[str, float] = {}
    for r in usable:
        by_direction[r["effect_direction"]] = round(
            by_direction.get(r["effect_direction"], 0.0) + r["amount"], 2)
    return {
        "by_direction": by_direction,
        "gross_total": round(sum(r["amount"] for r in usable), 2),
        "unit": unit,
        "n_items": len(usable),
        "unit_note": ("amounts reported in mixed units; totals cover only "
                      "comparable items" if len(units) > 0 and unit is None else ""),
        "note": ("Arithmetic aggregation of document-sourced potential effects; "
                 "not an audited misstatement total."),
    }


def _context_note(context: dict, filenames: list[str], engagement_context: str | None) -> str:
    bits = []
    for key in ("entity_description", "listed_status", "auditor_opinion_context"):
        v = str((context or {}).get(key) or "").strip()
        if v:
            bits.append(v.rstrip("."))
    lead = (f"Per the uploaded document(s) ({', '.join(filenames)}): " + ". ".join(bits) + ". "
            if bits else f"Supporting document(s) reviewed: {', '.join(filenames)}. ")
    if engagement_context:
        lead += f"Engagement context: {engagement_context}. "
    return (lead + "The analysis below cross-maps these document-sourced observations to "
            "the trial balance; quantified effects are potential effects per the cited "
            "source, not audited conclusions.")


def extract_doc_risk_items(upload_doc_ids: list[str] | None, extractor,
                           engagement_context: str | None = None) -> dict:
    """Harvest chunks → LLM extraction → deterministic validation → cumulative.

    Never raises: uploads-DB outage or extraction failure degrade to a status
    string and empty rows so the audit endpoint still returns 200.
    """
    empty = {"doc_evidence_status": "no_docs", "context_note": "",
             "quantified_risk_areas": {"rows": [], "cumulative": {}},
             "source_chunks": []}
    if not upload_doc_ids:
        return empty

    from yukta_rag.uploads.uploads import retrieve_uploaded
    chunks: list[dict] = []
    seen: set = set()
    try:
        for q in _HARVEST_QUERIES:
            for row in retrieve_uploaded(q, upload_doc_ids, top_k=4):
                key = (row.get("doc_id"), row.get("page_no"))
                if key not in seen:
                    seen.add(key)
                    chunks.append(row)
            if len(chunks) >= _MAX_CHUNKS:
                break
    except psycopg2.OperationalError as exc:
        logger.warning("uploads DB unavailable for doc evidence: %s", exc)
        return {**empty, "doc_evidence_status": "unavailable"}
    except Exception as exc:  # noqa: BLE001 - doc evidence is optional
        logger.warning("uploaded-chunk harvest failed: %s", exc)
        return {**empty, "doc_evidence_status": "unavailable"}

    chunks = chunks[:_MAX_CHUNKS]
    filenames = sorted({c.get("filename") or c.get("doc_id") for c in chunks})
    if not chunks:
        return {**empty, "doc_evidence_status": "no_items",
                "context_note": _context_note({}, filenames or ["(no matching pages)"],
                                              engagement_context)}

    numbered = "\n\n".join(
        f"[C{i}: {c.get('filename')} p.{c.get('page_no')}] "
        f"{str(c.get('text') or '')[:_CHUNK_TEXT_CAP]}"
        for i, c in enumerate(chunks, 1))
    prompt = ("Extract audit-relevant observations from these excerpts.\n\n"
              + numbered + "\n\nReturn the JSON object only.")

    parsed = None
    for _ in range(2):  # one retry on malformed JSON
        try:
            run = extractor.run(prompt, reset_conversation=True)
            parsed = _extract_json((run.get("response") or "").strip())
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc-evidence extraction call failed: %s", exc)
            parsed = None
        if parsed is not None:
            break
    if parsed is None:
        return {**empty, "doc_evidence_status": "no_items",
                "context_note": _context_note({}, filenames, engagement_context),
                "source_chunks": [{"doc_id": c.get("doc_id"),
                                   "filename": c.get("filename"),
                                   "page_no": c.get("page_no")} for c in chunks]}

    rows = _validate_items(parsed.get("items") or [], chunks)
    return {
        "doc_evidence_status": "ok" if rows else "no_items",
        "context_note": _context_note(parsed.get("context") or {}, filenames,
                                      engagement_context),
        "quantified_risk_areas": {"rows": rows, "cumulative": _cumulative(rows)},
        "source_chunks": [{"doc_id": c.get("doc_id"), "filename": c.get("filename"),
                           "page_no": c.get("page_no")} for c in chunks],
    }
