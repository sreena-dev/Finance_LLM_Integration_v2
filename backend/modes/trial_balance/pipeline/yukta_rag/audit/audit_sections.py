"""Client-format audit report sections derived from already-computed stages.

Everything here is deterministic re-shaping of the mapping/screen/relationship/
input-quality objects into the four sections the client's example report adds:
internal-control risks, statutory-compliance risks, Schedule III disclosure
checks and a suggested audit focus sequence. No I/O, no LLM — safe wording by
construction (risk indicators, never conclusions).
"""

from __future__ import annotations


def _fmt(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except (TypeError, ValueError):
        return str(n)


# ---------------------------------------------------------------------------
# Internal control & process risks
# ---------------------------------------------------------------------------


def internal_control_risks(screen: dict, iq: dict, mapping: dict) -> list[dict]:
    rows: list[dict] = []

    def add(area, risk, source, evidence):
        rows.append({"area": area, "risk": risk, "indicator_source": source,
                     "evidence_requested": evidence})

    for head in screen.get("sensitive_heads", []):
        if head.get("tag") == "suspense_control" and head.get("count"):
            ex = ", ".join(e["account"] for e in head.get("examples", [])[:3])
            add("Suspense & control accounts",
                f"{head['count']} suspense/clearing/control ledger(s) carry balances "
                f"(e.g. {ex}) — unresolved postings are an indicator of ledger-integrity "
                "and reconciliation weaknesses requiring corroboration.",
                "tb_screen.sensitive_heads",
                ["suspense-account movement analysis", "reconciliation status",
                 "ageing of unadjusted entries"])

    if not iq.get("balanced", True):
        add("Ledger integrity",
            f"The trial balance does not balance (residual "
            f"{_fmt(iq.get('trial_balance_residual'))}) — an indicator of incomplete "
            "extraction or posting-control weakness.",
            "input_quality", ["complete GL dump", "system-generated TB with control totals"])

    n_abn = screen.get("n_abnormal", 0)
    if n_abn:
        add("Abnormal balance signs",
            f"{n_abn} ledger(s) carry a balance on the abnormal side of their class — "
            "a possible indicator of misposting, netting-off or master-data errors.",
            "tb_screen.abnormal_signs",
            ["ledger extracts for abnormal-sign accounts", "posting-source analysis"])

    if screen.get("round_sums"):
        add("Round-sum balances",
            f"{len(screen['round_sums'])} balance(s) are large round sums — a pattern "
            "associated with estimates, provisions or manual journal entries.",
            "tb_screen.round_sums",
            ["journal vouchers for round-sum balances", "approval trail for manual entries"])

    if screen.get("repeated_amounts"):
        add("Repeated identical amounts",
            f"{len(screen['repeated_amounts'])} amount(s) repeat across multiple ledgers — "
            "an indicator of duplicated postings or template journals.",
            "tb_screen.repeated_amounts",
            ["source documents for repeated amounts", "duplicate-posting exception report"])

    unmapped = mapping.get("unmapped_accounts") or []
    if unmapped:
        add("Chart-of-accounts hygiene",
            f"{len(unmapped)} ledger(s) could not be mapped to a Schedule III line item — "
            "an indicator of legacy/migration codes or non-standard ledger naming.",
            "mapping.unmapped_accounts",
            ["chart of accounts with mapping", "explanation of legacy/unmapped codes"])

    for flag in (iq.get("data_quality_flags") or [])[:4]:
        add("Data quality", f"{flag} — process-level indicator requiring corroboration.",
            "input_quality.data_quality_flags", ["supporting reconciliation for the flagged item"])
    return rows


# ---------------------------------------------------------------------------
# Statutory compliance risks (GST / TDS / PF-ESI) — indicators only
# ---------------------------------------------------------------------------

_STATUTE_KEYS = (("gst", "GST"), ("tds", "TDS"), ("pf", "PF/ESI"), ("esi", "PF/ESI"))


def statutory_compliance_risks(relationships: dict, mapping: dict) -> list[dict]:
    rows: list[dict] = []
    seen: set = set()
    for rel in relationships.get("relationships", []):
        name = (rel.get("relationship") or "").lower()
        statute = next((label for key, label in _STATUTE_KEYS if key in name), None)
        if not statute or (statute, rel.get("relationship")) in seen:
            continue
        seen.add((statute, rel.get("relationship")))
        rows.append({
            "statute": statute,
            "observation": rel.get("observation", ""),
            "expectation": rel.get("expectation", ""),
            "risk": ("Risk indicator requiring corroboration against filed returns and "
                     "challans; the trial balance alone cannot confirm filing or payment status."),
            "severity": rel.get("severity", "low"),
            "evidence_requested": rel.get("evidence_requested", []),
        })
    n_statutory = (mapping.get("sensitive_summary") or {}).get("statutory_dues", 0)
    if n_statutory:
        rows.append({
            "statute": "Statutory dues (general)",
            "observation": f"{n_statutory} ledger(s) tagged as statutory dues "
                           "(GST/TDS/TCS/PF/ESI/duty/cess) carry balances.",
            "expectation": "Statutory-dues balances should reconcile to returns filed and "
                           "subsequent payment challans.",
            "risk": ("Ageing and payment status are not visible in a trial balance — "
                     "delayed-deposit interest exposure cannot be ruled out without challans."),
            "severity": "medium",
            "evidence_requested": ["returns filed (GST/TDS/PF)", "payment challans",
                                   "ageing of statutory dues", "interest on delayed deposit workings"],
        })
    return rows


# ---------------------------------------------------------------------------
# Schedule III disclosure checks
# ---------------------------------------------------------------------------


def schedule_iii_disclosure_gaps(mapping: dict, iq: dict, screen: dict) -> list[dict]:
    fslis = set((mapping.get("fsli_groups") or {}).keys())
    rows: list[dict] = []

    def add(item, status, detail, evidence):
        rows.append({"item": item, "status": status, "detail": detail,
                     "evidence_requested": evidence, "citations": []})

    if "Trade Receivables" in fslis:
        add("Trade receivables ageing schedule", "cannot_verify_from_tb",
            "Schedule III requires receivables classified by ageing buckets and "
            "disputed/undisputed status; a trial balance carries closing balances only.",
            ["receivables ageing as per books", "disputed-receivables list"])
    if "Trade Payables" in fslis:
        add("Trade payables ageing and MSME split", "cannot_verify_from_tb",
            "Schedule III requires payables ageing and the MSME/others split; MSMED "
            "delayed-payment interest cannot be assessed without the vendor register.",
            ["payables ageing", "MSME vendor register", "MSMED interest computation"])
    if "Capital Work-in-Progress" in fslis:
        add("CWIP ageing and overdue projects", "cannot_verify_from_tb",
            "Schedule III requires CWIP classified by ageing and projects whose completion "
            "is overdue or cost-overrun against original plan.",
            ["CWIP ageing schedule", "project-wise completion status"])
    if "Borrowings" in fslis:
        add("Borrowings — security, terms and charge registration", "cannot_verify_from_tb",
            "Schedule III requires disclosure of security, repayment terms and ROC charge "
            "registration for secured borrowings; not derivable from ledger balances.",
            ["loan agreements/sanction letters", "ROC charge search report"])
    if not iq.get("comparative_present", True):
        add("Comparative figures", "gap_indicated",
            "Schedule III requires corresponding amounts for the previous reporting period; "
            "no comparative column is present in the supplied trial balance.",
            ["prior-period trial balance"])
    if screen.get("abnormal_signs"):
        add("Classification of abnormal-sign balances", "gap_indicated",
            f"{screen.get('n_abnormal', 0)} ledger(s) carry balances on the abnormal side; "
            "Schedule III presentation (e.g. advances from customers vs receivables) may "
            "require reclassification.",
            ["ledger-level review of abnormal-sign accounts"])
    if mapping.get("unmapped_accounts"):
        add("Presentation of unmapped ledgers", "gap_indicated",
            f"{len(mapping['unmapped_accounts'])} ledger(s) could not be assigned a "
            "Schedule III line item — presentation/classification risk.",
            ["mapping of unmapped ledgers to Schedule III line items"])
    return rows


# ---------------------------------------------------------------------------
# Suggested audit focus sequence
# ---------------------------------------------------------------------------


def audit_focus_sequence(findings: dict, quantified: dict | None, gaps: dict,
                         statutory: list[dict], max_items: int = 10) -> list[dict]:
    """Ordered focus list: quantified doc items > HIGH findings > statutory > MEDIUM."""
    rows: list[dict] = []
    seen_areas: set = set()

    def add(area, reason, linked_findings=(), linked_standards=()):
        key = (area or "").strip().lower()
        if not key or key in seen_areas or len(rows) >= max_items:
            return
        seen_areas.add(key)
        rows.append({"priority": len(rows) + 1, "area": area, "reason": reason,
                     "linked_findings": list(linked_findings)[:3],
                     "linked_standards": list(linked_standards)[:3]})

    q_rows = sorted(((quantified or {}).get("rows") or []),
                    key=lambda r: abs(r.get("amount") or 0.0), reverse=True)
    for r in q_rows:
        area = ", ".join(r.get("head_affected") or []) or r.get("item", "")[:60]
        std = [c["label"].split(",")[0] for c in (r.get("citations") or [])
               if c.get("source") == "ind_as"]
        add(area,
            f"Document-sourced potential effect: {r.get('item', '')[:120]} "
            f"({r.get('amount_text') or 'unquantified'}, per {r['source']['filename']} "
            f"p.{r['source']['page_no']}).",
            linked_standards=std)

    by_rating = {"high": [], "medium": []}
    for f in findings.get("findings", []):
        if f.get("risk_rating") in by_rating:
            by_rating[f["risk_rating"]].append(f)
    for f in by_rating["high"]:
        add(f.get("fsli") or f.get("account", ""),
            f"HIGH-rated finding: {f.get('observation', '')[:140]}",
            linked_findings=[f.get("account", "")])
    for s in statutory:
        if s.get("severity") in ("high", "medium"):
            add(f"Statutory dues — {s['statute']}",
                f"{s.get('observation', '')[:120]} Corroborate against returns and challans.")
    for f in by_rating["medium"]:
        add(f.get("fsli") or f.get("account", ""),
            f"MEDIUM-rated finding: {f.get('observation', '')[:140]}",
            linked_findings=[f.get("account", "")])
    for g in gaps.get("gaps", []):
        add(g.get("name", ""),
            f"Disclosure/requirement gap indicated: {g.get('gap', '')[:120]}",
            linked_standards=[g.get("standard", "")])
    return rows
