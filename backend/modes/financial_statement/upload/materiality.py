"""The materiality threshold in force, and the legend that must accompany it.

The requirement is that any risk assessment or flag carries a legend saying what
materiality threshold it was derived against, and that a user-supplied threshold
overrides the computed one and is shown as such.

Most of the arithmetic already exists. ``MaterialityReference.BASES`` in
``tools_fs.py`` holds the conventional percentage bands -- revenue 0.5-1%, total
assets 0.5-2%, PBT 5-10%, net worth 1-5% -- with a standing disclaimer that
these are practice ranges and not a rule, since SA 320 requires the auditor to
choose a benchmark and justify it. Spec section 3.2 gives the same bands from
the C&AG side and adds the instruction this module exists to honour:

    "The model must state the benchmark, percentage, amount and reason used. If
    audit-team materiality is supplied, use it. If not, label the computed band
    as provisional and do not use it to suppress by-nature or by-context
    matters."

That last clause is the reason ``legend()`` always prints the by-nature and
by-context carve-out even when a number has been supplied. A threshold in this
domain filters *ordinary* items only; grants, related parties, statutory dues,
write-offs and propriety questions are material regardless of size, and a legend
that implied otherwise would be actively misleading.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

#: Sensitive heads that a value threshold must never filter out. Taken from
#: spec section 3.1 ("By nature") and its "Specific lower thresholds" row.
BY_NATURE = (
    "grants and subsidies",
    "related-party transactions",
    "government guarantees",
    "statutory dues",
    "CSR",
    "managerial remuneration",
    "write-offs, waivers and ex gratia payments",
    "fraud-sensitive matters",
    "public-fund and propriety questions",
)


@dataclass
class Materiality:
    """The threshold in force for one conversation."""

    amount: float | None = None
    basis: str | None = None
    percentage: float | None = None
    unit_label: str | None = None
    #: True when the audit team supplied the figure, False when this module
    #: computed a provisional band. The distinction is the whole point.
    user_supplied: bool = False
    reason: str | None = None
    computed_bands: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "basis": self.basis,
            "percentage": self.percentage,
            "unit_label": self.unit_label,
            "user_supplied": self.user_supplied,
            "provisional": not self.user_supplied,
            "reason": self.reason,
            "computed_bands": self.computed_bands,
            "by_nature_exclusions": list(BY_NATURE),
        }


class _Registry:
    """Per-conversation threshold, held in memory alongside the documents."""

    def __init__(self) -> None:
        self._by_conversation: dict[tuple[str, str], Materiality] = {}
        self._lock = threading.Lock()

    def set(self, user_id: str, conversation_id: str, materiality: Materiality) -> None:
        with self._lock:
            self._by_conversation[(user_id, conversation_id)] = materiality

    def get(self, user_id: str, conversation_id: str) -> Materiality | None:
        with self._lock:
            return self._by_conversation.get((user_id, conversation_id))

    def clear(self, user_id: str, conversation_id: str) -> None:
        with self._lock:
            self._by_conversation.pop((user_id, conversation_id), None)


REGISTRY = _Registry()


def provisional_from_figures(figures: dict[str, float], unit_label: str | None) -> Materiality:
    """A provisional band from whatever the statements actually disclose.

    Bands and their ordering follow spec section 3.2. Profit before tax is the
    usual first choice for a profit-oriented entity but is explicitly unsuitable
    near break-even, so it is skipped when PBT is under 1% of revenue -- the
    same instability guard ``MaterialityReference.PBT_INSTABILITY_RATIO``
    applies. Total assets is preferred for asset-heavy entities, and revenue or
    total expenditure for grant-driven ones, which is most of this corpus.
    """
    bands: list[dict[str, Any]] = []

    revenue = figures.get("revenue_from_operations")
    assets = figures.get("total_assets")
    pbt = figures.get("profit_before_tax")
    equity = figures.get("total_equity")

    def add(key: str, name: str, value: float | None, low: float, high: float, note: str) -> None:
        if value is None:
            bands.append({"basis": name, "figure": None, "note":
                          f"{note} — not extracted from the face statements"})
            return
        bands.append({
            "basis": name,
            "figure": value,
            "low_pct": low,
            "high_pct": high,
            "low_amount": round(abs(value) * low / 100.0, 2),
            "high_amount": round(abs(value) * high / 100.0, 2),
            "note": note,
        })

    add("revenue_from_operations", "Revenue from operations", revenue, 0.5, 2.0,
        "general operating entities; choose expenditure where revenue is grant-driven")
    add("total_assets", "Total assets", assets, 0.5, 1.0,
        "asset-heavy utilities, infrastructure and holding companies")

    unstable = (
        pbt is not None and revenue not in (None, 0)
        and abs(pbt) < abs(revenue) * 0.01
    )
    if pbt is not None and not unstable:
        add("profit_before_tax", "Profit before tax", pbt, 5.0, 10.0,
            "usual first choice for a profit-oriented entity")
    elif pbt is not None:
        bands.append({
            "basis": "Profit before tax", "figure": pbt,
            "note": "skipped — profit is near break-even, where a PBT benchmark "
                    "is unstable (spec 3.2: avoid for loss-making or break-even PSUs)",
        })

    add("total_equity", "Net worth (total equity)", equity, 0.5, 2.0,
        "useful where erosion, leverage, covenants or government support are central")

    usable = [b for b in bands if b.get("low_amount") is not None]
    chosen = usable[0] if usable else None

    return Materiality(
        amount=chosen["low_amount"] if chosen else None,
        basis=chosen["basis"] if chosen else None,
        percentage=chosen["low_pct"] if chosen else None,
        unit_label=unit_label,
        user_supplied=False,
        reason=(
            f"provisional band computed from {chosen['basis']} because the audit "
            "team supplied no materiality figure" if chosen else
            "no benchmark figure could be extracted from the face statements"
        ),
        computed_bands=bands,
    )


def legend(materiality: Materiality | None) -> str:
    """The legend that must appear under any set of flags or risk ratings.

    Always ends with the by-nature carve-out. Spec section 3.2 is explicit that
    a computed band must not be used to suppress matters that are material by
    nature or by context, and a legend stating a threshold without that caveat
    reads as though it may.
    """
    lines = ["", "---", "**Materiality legend**", ""]

    if materiality is None or materiality.amount is None:
        lines.append(
            "No materiality threshold is in force. The flags above are ordered by "
            "audit significance, not filtered by value, because no audit-team "
            "materiality was supplied and no benchmark figure could be extracted "
            "from the statements. Supply one to have ordinary items triaged by "
            "value."
        )
    else:
        unit = f" {materiality.unit_label}" if materiality.unit_label else ""
        amount = f"{materiality.amount:,.2f}{unit}"
        if materiality.user_supplied:
            lines.append(
                f"- Threshold applied: **{amount}** — supplied by the audit team "
                "for this review. Used as given; no provisional band was computed."
            )
        else:
            lines.append(
                f"- Threshold applied: **{amount}** — PROVISIONAL, computed as "
                f"{materiality.percentage}% of {materiality.basis} because no "
                "audit-team materiality was supplied."
            )
            lines.append(
                "- These percentage bands are conventional audit practice, not a "
                "regulatory rule. SA 320 requires the auditor to select a "
                "benchmark and percentage using professional judgement about the "
                "entity's circumstances, and to document the choice."
            )
        if materiality.reason and materiality.user_supplied:
            lines.append(f"- Basis stated: {materiality.reason}")

        if materiality.computed_bands:
            lines.append("- Other benchmarks available on these statements:")
            for band in materiality.computed_bands:
                if band.get("low_amount") is None:
                    lines.append(f"    - {band['basis']}: {band.get('note')}")
                    continue
                lines.append(
                    f"    - {band['basis']}: {band['low_pct']}%–{band['high_pct']}% "
                    f"= {band['low_amount']:,.2f}–{band['high_amount']:,.2f}{unit}"
                )

    lines.append("")
    lines.append(
        "- A value threshold triages ORDINARY items only. The following are "
        "material by nature or by context and are flagged regardless of amount: "
        + ", ".join(BY_NATURE) + "."
    )
    lines.append(
        "- Materiality by context also applies: an item that turns a profit into "
        "a loss, breaches a covenant, affects dividend, CSR or net worth, or "
        "masks liquidity stress is material whatever its size."
    )
    return "\n".join(lines)


def parse_amount(raw: str | float | None) -> float | None:
    """Accept a threshold as a user would type it: ``2.5 crore``, ``1,00,000``."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip().lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "")
    multiplier = 1.0
    for word, factor in (("crore", 1e7), ("cr", 1e7), ("lakh", 1e5), ("lac", 1e5),
                         ("million", 1e6), ("mn", 1e6), ("thousand", 1e3), ("k", 1e3)):
        if word in text:
            multiplier = factor
            text = text.replace(word, "")
            break
    text = text.strip()
    try:
        return float(text) * multiplier
    except ValueError:
        return None
