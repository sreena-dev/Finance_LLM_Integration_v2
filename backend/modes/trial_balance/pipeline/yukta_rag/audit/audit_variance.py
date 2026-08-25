"""Opening-vs-closing variance for Audit mode (client-format rule D12).

Distinct from ``tb_tools.compute_variance`` (which compares two PERIODS —
current file vs prior file/column — and reports a percentage change). This
compares a single period's OPENING balance against its CLOSING balance per
FSLI group, in absolute figures only (no percentages, per rule B6).
"""

from __future__ import annotations


def opening_vs_closing(mapping: dict) -> dict:
    """Aggregate opening vs closing net by FSLI group. Absolute figures only.

    Only includes groups where at least one account has an ``opening_net``
    value (the TB actually carried opening-balance columns) — otherwise
    there's nothing honest to compare (spec's "cannot compute" principle).
    """
    groups: dict[str, dict] = {}
    for a in mapping["accounts"]:
        fsli = a.get("fsli") or a.get("category")
        if not fsli:
            continue
        g = groups.setdefault(fsli, {"opening": 0.0, "closing": 0.0, "has_opening": False})
        g["closing"] += a["net"]
        if a.get("opening_net") is not None:
            g["opening"] += a["opening_net"]
            g["has_opening"] = True

    rows = []
    for fsli, g in groups.items():
        if not g["has_opening"]:
            continue
        opening, closing = round(g["opening"], 2), round(g["closing"], 2)
        movement = round(closing - opening, 2)
        scale = max(abs(opening), abs(closing), 1.0)
        if movement == 0:
            note = "no net movement in the year"
        elif abs(movement) < 0.01 * scale:
            note = "broadly stable — minor movement only"
        else:
            note = "material movement — obtain composition/reconciliation of the change"
        rows.append({"fsli": fsli, "opening": opening, "closing": closing,
                    "movement": movement, "note": note})
    rows.sort(key=lambda r: abs(r["movement"]), reverse=True)
    return {"rows": rows, "computable": any(g["has_opening"] for g in groups.values())}
