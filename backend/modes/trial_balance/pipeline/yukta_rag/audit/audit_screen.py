"""Trial-balance-wide screen for Audit mode (spec 6).

Runs whole-TB triage on the mapped accounts before account-area analysis:
abnormal signs (grouped to control noise), size/concentration, round-sum /
repeated amounts, sensitive heads and data-quality flags. Missing-counterpart
and quantified linkages live in ``audit_relationships`` (spec 7). Deterministic;
every item is a risk indicator, not a conclusion.
"""

from __future__ import annotations

import re
from collections import Counter

from yukta_rag.audit.audit_config import (CLEARING_MIN_GROSS_ABS,
                                          CLEARING_NET_TO_GROSS_PCT,
                                          CLEARING_STRUCTURAL_MIN_MEMBERS,
                                          CONCENTRATION_PCT, CONTRA_PAIR_MIN_ABS,
                                          CONTRA_PAIR_REL_TOL, MATERIAL_FRACTION,
                                          MIRROR_PAIR_RESOLVED_PCT,
                                          OFFSET_DEBIT_CREDIT_PCT, OFFSET_NET_PCT,
                                          ROUND_SUM_MIN_ABS)
from yukta_rag.trial_balance.tb_tools import _numeric_codes_present, _numeric_suffix

# an account is "material" for screening if it is at least this fraction of the
# largest absolute balance in the TB (keeps the screen focused, spec 6.2 noise control)
_MATERIAL_FRACTION = MATERIAL_FRACTION
_CONCENTRATION_PCT = CONCENTRATION_PCT   # flag an account exceeding this share of its class (spec 6.1)

# contra/rollup pair detection: two accounts whose balances net to ~0 are treated
# as a matched pair (e.g. INC/OTG mirror legs, IND-AS transition entries) rather
# than left to inject a stray, unpaired magnitude into a category total.
_CONTRA_PAIR_REL_TOL = CONTRA_PAIR_REL_TOL   # 0.01% relative tolerance
_CONTRA_PAIR_MIN_ABS = CONTRA_PAIR_MIN_ABS   # ignore near-zero noise


def _class_totals(accounts: list[dict]) -> dict:
    tot: dict = {}
    for a in accounts:
        c = a.get("category")
        if c:
            tot[c] = tot.get(c, 0.0) + abs(a["net"])
    return tot


def _round_sum(net: float) -> bool:
    """True if a large balance looks manually set (>= 5 trailing zeros)."""
    v = abs(round(net))
    return v >= ROUND_SUM_MIN_ABS and v % ROUND_SUM_MIN_ABS == 0


def _match_contra_pairs(accounts: list[dict]) -> list[tuple[dict, dict]]:
    """Internal matcher shared by ``find_contra_pairs`` and the value-coverage
    calculation in ``audit_normalize``. Bucketed by rounded magnitude to avoid an
    O(n^2) scan over the whole TB; each account is used in at most one pair.
    """
    buckets: dict[float, list[dict]] = {}
    for a in accounts:
        net = a.get("net", 0.0) or 0.0
        if abs(net) < _CONTRA_PAIR_MIN_ABS:
            continue
        buckets.setdefault(round(abs(net), 2), []).append(a)

    matched = []
    used: set[int] = set()
    for group in buckets.values():
        positives = [a for a in group if a.get("net", 0.0) > 0]
        negatives = [a for a in group if a.get("net", 0.0) < 0]
        for p in positives:
            if id(p) in used:
                continue
            for n in negatives:
                if id(n) in used:
                    continue
                tol = max(_CONTRA_PAIR_MIN_ABS, abs(p["net"]) * _CONTRA_PAIR_REL_TOL)
                if abs(p["net"] + n["net"]) <= tol:
                    matched.append((p, n))
                    used.add(id(p))
                    used.add(id(n))
                    break
    return matched


def find_contra_pairs(accounts: list[dict]) -> list[dict]:
    """Detect near-exact offsetting account pairs (contra/rollup entries that net
    to ~0 by design — e.g. INC/OTG mirror legs, IND-AS transition entries).

    Runs over ALL accounts (classified or not): a stray, unpaired leg of such a
    pair otherwise injects its full magnitude into whatever category/coverage
    total picks it up.
    """
    pairs = [{
        "account_a": p["name"], "account_a_code": p.get("code"), "net_a": p["net"],
        "account_b": n["name"], "account_b_code": n.get("code"), "net_b": n["net"],
        "residual": round(p["net"] + n["net"], 2),
        "note": "Near-exact offsetting balances — likely a contra/rollup pair "
                "(e.g. an inter-ledger mirror or IND-AS transition entry) that "
                "nets to ~0 by design; excluded from category/coverage totals "
                "pending confirmation.",
    } for p, n in _match_contra_pairs(accounts)]
    pairs.sort(key=lambda r: abs(r["net_a"]), reverse=True)
    return pairs


def contra_pair_ids(accounts: list[dict]) -> set[int]:
    """``id()`` of every account object that is part of a confirmed contra pair —
    used to exclude them from the value-coverage calculation (a correctly-paired
    contra doesn't reduce reliability; a stray unpaired leg does)."""
    ids: set[int] = set()
    for p, n in _match_contra_pairs(accounts):
        ids.add(id(p))
        ids.add(id(n))
    return ids


_SUBTOTAL_LOOKBACK = 30  # rows searched above a candidate subtotal row (bounded, cheap)


def find_duplicate_rows(tb: dict) -> dict:
    """Spec E9 — duplicate account codes, duplicate (name+amount) rows, and
    subtotal rows accidentally left in as if they were real accounts.

    Runs on the RAW parsed TB (``tb["accounts"]``, not the mapped/classified
    accounts) since these are data-entry defects independent of FSLI
    classification, and needs the accounts in original file order
    (``source_row``) to find a subtotal sitting directly below the rows it
    sums.
    """
    accounts = sorted(tb["accounts"], key=lambda a: a.get("source_row") or 0)

    by_code: dict = {}
    for a in accounts:
        code = a.get("code")
        if code:
            by_code.setdefault(code, []).append(a)
    duplicate_codes = [
        {"code": code, "accounts": [a["name"] for a in rows],
         "source_rows": [a.get("source_row") for a in rows]}
        for code, rows in by_code.items() if len(rows) > 1
    ]
    duplicate_codes.sort(key=lambda r: len(r["accounts"]), reverse=True)

    by_name_amt: dict = {}
    for a in accounts:
        key = (a.get("name"), round(a.get("p1_net", 0.0) or 0.0, 2))
        by_name_amt.setdefault(key, []).append(a)
    duplicate_rows = [
        {"name": name, "amount": amt, "source_rows": [a.get("source_row") for a in rows]}
        for (name, amt), rows in by_name_amt.items() if len(rows) > 1 and amt != 0
    ]
    duplicate_rows.sort(key=lambda r: abs(r["amount"]), reverse=True)

    # subtotal row left in: its amount equals the running sum of a contiguous
    # block of rows directly above it (at least 2 rows summed)
    subtotal_rows = []
    for i, a in enumerate(accounts):
        amt = round(a.get("p1_net", 0.0) or 0.0, 2)
        if amt == 0:
            continue
        running = 0.0
        for j in range(i - 1, max(-1, i - 1 - _SUBTOTAL_LOOKBACK), -1):
            running = round(running + (accounts[j].get("p1_net", 0.0) or 0.0), 2)
            if j < i - 1 and abs(running - amt) < 0.01:
                subtotal_rows.append({
                    "name": a.get("name"), "amount": amt, "source_row": a.get("source_row"),
                    "block_source_rows": [accounts[k].get("source_row") for k in range(j, i)],
                })
                break

    return {
        "duplicate_codes": duplicate_codes[:15],
        "n_duplicate_codes": len(duplicate_codes),
        "duplicate_rows": duplicate_rows[:15],
        "n_duplicate_rows": len(duplicate_rows),
        "subtotal_rows": subtotal_rows[:15],
        "n_subtotal_rows": len(subtotal_rows),
    }


def find_offsetting_activity(tb: dict, mapping: dict) -> dict:
    """Spec E3/E6 — an account whose own gross debit and credit movement are
    each large relative to its group's gross activity, while its NET movement
    stays small relative to the group (additions/disposals cycling through the
    year rather than a genuine net change — the flagship PPE-additions-vs-
    disposals pattern, generalized to every account).

    Needs ``movement_debit``/``movement_credit`` on the raw TB accounts (only
    present when the source file has real movement columns — spec's own
    "cannot compute" honesty principle: this silently returns no flags rather
    than approximating from closing-balance composition, which would mostly
    just be zero on one side for a normal account and prove nothing).
    """
    cat_by_name = {a["name"]: a.get("category") for a in mapping["accounts"]}
    with_movement = [a for a in tb["accounts"] if "movement_debit" in a]
    if not with_movement:
        return {"flags": [], "n_flags": 0, "computable": False}

    group_debit: dict = {}
    group_credit: dict = {}
    for a in with_movement:
        cat = cat_by_name.get(a.get("name"))
        if not cat:
            continue
        group_debit[cat] = group_debit.get(cat, 0.0) + abs(a["movement_debit"])
        group_credit[cat] = group_credit.get(cat, 0.0) + abs(a["movement_credit"])

    flags = []
    for a in with_movement:
        cat = cat_by_name.get(a.get("name"))
        gd, gc = group_debit.get(cat, 0.0), group_credit.get(cat, 0.0)
        if not cat or not gd or not gc:
            continue
        debit_pct = abs(a["movement_debit"]) / gd * 100
        credit_pct = abs(a["movement_credit"]) / gc * 100
        net_pct = abs(a.get("movement_net", 0.0)) / max(gd, gc) * 100
        if (debit_pct > OFFSET_DEBIT_CREDIT_PCT and credit_pct > OFFSET_DEBIT_CREDIT_PCT
                and net_pct < OFFSET_NET_PCT):
            flags.append({
                "account": a["name"], "category": cat, "source_row": a.get("source_row"),
                "debit_pct": round(debit_pct, 1), "credit_pct": round(credit_pct, 1),
                "net_pct": round(net_pct, 1),
                "movement_debit": a["movement_debit"], "movement_credit": a["movement_credit"],
                "movement_net": a.get("movement_net", 0.0),
            })
    flags.sort(key=lambda r: r["debit_pct"] + r["credit_pct"], reverse=True)
    return {"flags": flags[:15], "n_flags": len(flags), "computable": True}


_CLEARING_KWS = ("clearing", "inter-unit", "inter unit", "inter-branch", "inter branch",
                "inter-division", "inter division", "statistical", "memo", "suspense",
                "control account")
_CODE_PREFIX_STRIP = 2  # strip this many trailing digits to group a GL "series"
_STRUCTURAL_PREFIX_LEN = 2  # leading-digit-pair grouping — coarser than the
# fine (trailing-2-digits-stripped) grouping above, used ONLY for the
# structural/name-independent signal (see find_clearing_series): a real
# numeric "80-series" style clearing block reads as ONE coherent series at
# this granularity, matching how the reference note itself describes these
# ranges, rather than fragmenting into dozens of near-complete-code slices.
_STRUCTURAL_TOP_K = 8  # cap the structural signal to the K largest-by-gross
# coarse series in the whole file — this is deliberately about "the handful
# of dominant series", not a general mechanism; bounds worst-case impact.


def _clearing_prefix(code: str | None) -> str | None:
    """GL "series" prefix for grouping — strips a leading ERP-style prefix
    (e.g. "GAIL/5410046" -> "5410046") before dropping the trailing digits
    that distinguish individual sub-accounts within a series."""
    s = _numeric_suffix(code)
    if not s or len(s) <= _CODE_PREFIX_STRIP:
        return None
    return s[:-_CODE_PREFIX_STRIP]


def _structural_series_prefix(code: str | None) -> str | None:
    """Coarse leading-digit-pair series key, e.g. "8020010" -> "80"."""
    s = _numeric_suffix(code)
    if not s or len(s) <= _STRUCTURAL_PREFIX_LEN:
        return None
    return s[:_STRUCTURAL_PREFIX_LEN]


def find_clearing_series(tb: dict, mapping: dict) -> dict:
    """Client-format rule C9 — detect GL-series that function as internal
    clearing / inter-unit transfer / statistical-memo postings (series that
    net to nil or near-nil across themselves), so they can be excluded from
    FSLI totals and the Financial Snapshot rather than misread as real
    economic balances.

    GL-code-prefix heuristic, gated behind ``_numeric_codes_present`` (only
    trusted when codes are numeric enough to group by prefix at all — a much
    lighter bar than the full sign-validated 1-6 classification scheme used
    for account-TYPE inference elsewhere, which this function does not need:
    its own per-group net-vs-gross ratio is the real safety check).

    Two independent passes, each with its own grouping granularity:

    Pass 1 (keyword-gated, fine-grained, unchanged from the original design):
    accounts sharing a GL-code prefix (last 2 digits stripped) are grouped; a
    group is a candidate only if at least one member carries a clearing/
    inter-unit/statistical keyword (name or the existing ``suspense_control``
    sensitive tag).

    Pass 2 (structural, name-independent, coarse-grained): since naming
    conventions vary too much across clients to rely on keywords alone,
    accounts are ALSO grouped by a much coarser leading-digit-pair key (e.g.
    "8020010" -> "80") — matching how a real numbered series reads as one
    coherent block, not dozens of near-complete-code slices. To bound the
    risk of this name-independent signal firing on ordinary FSLI groups
    (confirmed empirically: an early version of this check, using the fine
    grouping and treating "entirely unmapped" as sufficient on its own,
    mis-flagged 124 unrelated groups on a real 2,363-account file — value
    ~16x larger than the genuine target), it is deliberately narrow: only the
    ``_STRUCTURAL_TOP_K`` largest-by-gross coarse series in the WHOLE file are
    even considered, and a coarse series only qualifies if it's large
    (``CLEARING_STRUCTURAL_MIN_MEMBERS``+) AND already resolves to near-nil
    relative to its own gross (a wash-like series is its own evidence — no
    "just looks unmapped" branch, that alone is not rare enough to be
    reliable). Accounts already excluded via Pass 1 are not double-counted.

    For each candidate group (either pass): if its combined net is small
    relative to its own gross debit+credit activity, it's resolved (excluded
    from totals, near-nil memorandum). If the net does NOT resolve to nil,
    it's still excluded from totals but surfaced as an unresolved
    data-quality item rather than silently dropped.
    """
    name_by_key = {a["name"]: a for a in mapping["accounts"]}
    if not _numeric_codes_present(tb):
        return {"groups": [], "excluded_names": [], "total_excluded_net": 0.0,
                "unresolved": [], "computable": False}

    def _gross_net(members: list[dict]) -> tuple[float, float]:
        gross = sum(abs(m.get("movement_debit", 0.0) or 0.0) + abs(m.get("movement_credit", 0.0) or 0.0)
                    if "movement_debit" in m else abs(m.get("p1_net", 0.0) or 0.0)
                    for m in members)
        net_group = round(sum(m.get("p1_net", 0.0) or 0.0 for m in members), 2)
        return gross, net_group

    fine_groups: dict[str, list[dict]] = {}
    for a in tb["accounts"]:
        prefix = _clearing_prefix(a.get("code"))
        if not prefix:
            continue
        fine_groups.setdefault(prefix, []).append(a)

    excluded_names, unresolved, result_groups = [], [], []
    total_excluded_net = 0.0

    # Pass 1 — keyword-gated, fine-grained (unchanged behavior)
    for prefix, members in fine_groups.items():
        if len(members) < 2:
            continue
        m_info = [name_by_key.get(m["name"]) for m in members]
        keyword_candidate = any(
            (mi and "suspense_control" in mi.get("sensitive_tags", []))
            or any(k in (m.get("name") or "").lower() for k in _CLEARING_KWS)
            for m, mi in zip(members, m_info)
        )
        if not keyword_candidate:
            continue
        gross, net_group = _gross_net(members)
        if gross < CLEARING_MIN_GROSS_ABS:
            continue
        resolved = abs(net_group) <= gross * (CLEARING_NET_TO_GROSS_PCT / 100.0)
        row = {"prefix": prefix, "accounts": [{"code": m.get("code"), "name": m["name"],
                                               "net": m.get("p1_net")} for m in members],
              "gross": round(gross, 2), "net": net_group, "resolved": resolved,
              "match_basis": "keyword"}
        result_groups.append(row)
        excluded_names.extend(m["name"] for m in members)
        total_excluded_net = round(total_excluded_net + net_group, 2)
        if not resolved:
            unresolved.append(row)

    # Pass 2 — structural, name-independent, coarse-grained, top-K by gross only
    coarse_groups: dict[str, list[dict]] = {}
    for a in tb["accounts"]:
        if a["name"] in excluded_names:
            continue  # already handled by Pass 1, don't double-count
        cprefix = _structural_series_prefix(a.get("code"))
        if not cprefix:
            continue
        coarse_groups.setdefault(cprefix, []).append(a)

    ranked = sorted(coarse_groups.items(), key=lambda kv: _gross_net(kv[1])[0], reverse=True)
    for cprefix, members in ranked[:_STRUCTURAL_TOP_K]:
        if len(members) < CLEARING_STRUCTURAL_MIN_MEMBERS:
            continue
        gross, net_group = _gross_net(members)
        if gross < CLEARING_MIN_GROSS_ABS:
            continue
        resolved = abs(net_group) <= gross * (CLEARING_NET_TO_GROSS_PCT / 100.0)
        if not resolved:
            continue  # structural signal requires an actual wash — no exceptions
        row = {"prefix": cprefix, "accounts": [{"code": m.get("code"), "name": m["name"],
                                                "net": m.get("p1_net")} for m in members],
              "gross": round(gross, 2), "net": net_group, "resolved": resolved,
              "match_basis": "structural"}
        result_groups.append(row)
        excluded_names.extend(m["name"] for m in members)
        total_excluded_net = round(total_excluded_net + net_group, 2)

    result_groups.sort(key=lambda r: abs(r["net"]), reverse=True)
    unresolved.sort(key=lambda r: abs(r["net"]), reverse=True)
    return {"groups": result_groups, "excluded_names": excluded_names,
            "total_excluded_net": total_excluded_net, "unresolved": unresolved,
            "computable": True}


def largest_balances(mapping: dict, excluded_names: set[str] | None = None, top_n: int = 10) -> list[dict]:
    """Client-format rule D13 — top-N accounts by absolute value, independent
    of the category-percentage ``concentration`` check above (that one flags
    a share-of-class outlier; this one is a plain magnitude ranking).
    Carries GL code + name + amount + a nature/treatment note. Confirmed
    clearing/mirror-pair members (``find_clearing_series``/
    ``find_mirrored_pairs``) are excluded from the ranking entirely — shown
    instead in their own memorandum line — so the top N is always N genuine
    balances, never padded out with a pair that's excluded elsewhere in the
    same report but still occupying a slot here.
    """
    excluded_names = excluded_names or set()
    rows = []
    for a in mapping["accounts"]:
        if a["name"] in excluded_names:
            continue
        rows.append({"code": a.get("code"), "name": a["name"], "amount": a["net"],
                    "nature": a.get("fsli") or a.get("category") or "",
                    "source_row": a.get("source_row")})
    rows.sort(key=lambda r: abs(r["amount"]), reverse=True)
    return rows[:top_n]


_MIRROR_KWS = _CLEARING_KWS + ("contra", "transfer", "tfr")
_TRAILING_NUM_RE = re.compile(r"[\s\-_]*\d+\s*$")


def _stripped_name(name: str) -> str:
    """Name with a trailing distinguishing number removed, so e.g. 'Divisional
    Consolidated - Contra Mktg2' and '...Contra Mktg' compare equal — a common
    ERP pattern for numbered sibling clearing accounts."""
    return _TRAILING_NUM_RE.sub("", (name or "").lower()).strip()


def find_mirrored_pairs(mapping: dict) -> dict:
    """Bug-3 fix — detect contra/mirror clearing PAIRS that do NOT share a
    GL-code prefix (so ``find_clearing_series`` misses them) but instead
    cross-reference each other by NAME: either (a) one account's own GL code
    is literally cited inside the other's name (e.g. code 4600005021 named
    "...MD-4600005022(C)", citing its pair's code verbatim), or (b) both
    names are otherwise identical but for a trailing distinguishing number
    (e.g. "...Contra Mktg" / "...Contra Mktg2").

    A pair "resolves" when its combined net is small relative to its own
    gross (a genuine offsetting pair) — excluded from totals, surfaced only
    in a memorandum line. A pair that does NOT resolve is still excluded from
    totals (its own balances aren't trustworthy as standalone items either)
    but surfaced as an unresolved, high-priority finding instead of silently
    dropped.

    Scope (deliberate, this pass): PAIRWISE only — two-account matches, not
    N-way groups that collectively net to nil with no single matching pair
    inside them (that shape is covered separately, for GL-code-prefix series,
    by ``find_clearing_series``). Extending to N-way groups (e.g. union-find
    over candidates whose combined net is ~nil) is a reasonable follow-up,
    out of scope here.

    Performance: candidates are narrowed up front to accounts whose name
    carries a clearing-flavoured keyword — a small subset of the whole TB, and
    the guard against false-positiving on two unrelated, non-keyword accounts
    that coincidentally share a similar magnitude. Code cross-reference is an
    O(1) dict lookup per candidate (O(n) overall, not pairwise). Name-based
    matching groups candidates by their stripped name in a dict (O(n)) and
    only pairs within the same small group — no O(n^2) all-pairs scan.
    """
    candidates = [a for a in mapping["accounts"] if a.get("net")
                 and any(k in (a.get("name") or "").lower() for k in _MIRROR_KWS)]
    if len(candidates) < 2:
        return {"pairs": [], "unresolved": [], "excluded_names": [], "total_excluded_net": 0.0}

    by_code = {a["code"]: a for a in candidates if a.get("code")}
    used: set[int] = set()
    matched: list[tuple[dict, dict, str]] = []

    # (a) explicit code cross-reference — O(n)
    for a in candidates:
        if id(a) in used:
            continue
        for token in re.findall(r"\d{4,}", a.get("name") or ""):
            b = by_code.get(token)
            if b is not None and b is not a and id(b) not in used:
                matched.append((a, b, "code_reference"))
                used.add(id(a))
                used.add(id(b))
                break

    # (b) near-identical name (differing only by a trailing number) — grouped
    # by stripped name (O(n)), paired within each small group by opposite sign
    remaining = [a for a in candidates if id(a) not in used]
    by_stripped: dict[str, list[dict]] = {}
    for a in remaining:
        by_stripped.setdefault(_stripped_name(a["name"]), []).append(a)
    for group in by_stripped.values():
        if len(group) < 2:
            continue
        positives = [a for a in group if a["net"] > 0]
        negatives = [a for a in group if a["net"] < 0]
        for a in positives:
            if id(a) in used:
                continue
            for b in negatives:
                if id(b) in used:
                    continue
                matched.append((a, b, "name_match"))
                used.add(id(a))
                used.add(id(b))
                break

    result_pairs, unresolved, excluded_names = [], [], []
    total_excluded_net = 0.0
    for a, b, basis in matched:
        net = round(a["net"] + b["net"], 2)
        gross = max(abs(a["net"]), abs(b["net"])) or 1.0
        resolved = abs(net) <= gross * (MIRROR_PAIR_RESOLVED_PCT / 100.0)
        row = {"match_basis": basis,
              "accounts": [{"code": a.get("code"), "name": a["name"], "net": a["net"]},
                          {"code": b.get("code"), "name": b["name"], "net": b["net"]}],
              "net": net, "gross": round(gross, 2), "resolved": resolved}
        result_pairs.append(row)
        excluded_names.extend([a["name"], b["name"]])
        total_excluded_net = round(total_excluded_net + net, 2)
        if not resolved:
            unresolved.append(row)

    result_pairs.sort(key=lambda r: r["gross"], reverse=True)
    unresolved.sort(key=lambda r: r["gross"], reverse=True)
    return {"pairs": result_pairs, "unresolved": unresolved,
            "excluded_names": excluded_names, "total_excluded_net": total_excluded_net}


def screen_tb(mapping: dict) -> dict:
    """Whole-TB screen over ``map_accounts`` output. Returns grouped screen results."""
    accounts = mapping["accounts"]
    if not accounts:
        return {"abnormal_signs": [], "concentration": [], "round_sums": [],
                "repeated_amounts": [], "sensitive_heads": [], "contra_pairs": [],
                "n_contra_pairs": 0, "data_quality": {}}

    max_abs = max((abs(a["net"]) for a in accounts), default=0.0) or 1.0
    material = [a for a in accounts if abs(a["net"]) >= _MATERIAL_FRACTION * max_abs]
    class_tot = _class_totals(accounts)

    # 1. abnormal signs — group by FSLI to avoid hundreds of line-level findings
    abn = [a for a in accounts if a.get("abnormal_sign")]
    by_fsli: dict = {}
    for a in abn:
        key = a.get("fsli") or (a.get("category") or "unmapped")
        g = by_fsli.setdefault(key, {"fsli": key, "count": 0, "total": 0.0,
                                     "note": a.get("abnormal_note"), "examples": []})
        g["count"] += 1
        g["total"] = round(g["total"] + a["net"], 2)
        g["examples"].append({"account": a["name"], "code": a.get("code"), "net": a["net"],
                              "source_row": a.get("source_row")})
    for g in by_fsli.values():
        g["examples"].sort(key=lambda e: abs(e["net"]), reverse=True)
        g["examples"] = g["examples"][:5]
    abnormal_signs = sorted(by_fsli.values(), key=lambda g: abs(g["total"]), reverse=True)

    # 2. concentration — accounts exceeding _CONCENTRATION_PCT of their class total
    concentration = []
    for a in material:
        c = a.get("category")
        ct = class_tot.get(c, 0.0)
        if ct > 0:
            share = round(abs(a["net"]) / ct * 100, 1)
            if share >= _CONCENTRATION_PCT:
                concentration.append({"account": a["name"], "code": a.get("code"),
                                      "fsli": a.get("fsli"),
                                      "category": c, "net": a["net"], "pct_of_class": share,
                                      "source_row": a.get("source_row")})
    concentration.sort(key=lambda r: r["pct_of_class"], reverse=True)

    # 3. round-sum / manually-set-looking balances. Keep the true count and combined
    # total across ALL round-sum ledgers (the displayed list is capped below), so a
    # finding reports an accurate magnitude even when more than the cap are flagged.
    round_sums = [{"account": a["name"], "code": a.get("code"), "net": a["net"],
                   "fsli": a.get("fsli"), "source_row": a.get("source_row")}
                  for a in material if _round_sum(a["net"])]
    round_sums.sort(key=lambda r: abs(r["net"]), reverse=True)
    n_round_sums = len(round_sums)
    round_sums_total = round(sum(r["net"] for r in round_sums), 2)

    # 4. repeated identical amounts across different accounts (possible parking/manual)
    amt_counts = Counter(abs(a["net"]) for a in material if a["net"] != 0)
    repeated = []
    for amt, cnt in amt_counts.items():
        if cnt >= 3:
            names = [a["name"] for a in material if abs(a["net"]) == amt][:6]
            repeated.append({"amount": amt, "count": cnt, "accounts": names})
    repeated.sort(key=lambda r: (r["count"], r["amount"]), reverse=True)

    # 5. sensitive heads — grouped by tag. ``total_net`` is the true combined net
    # across ALL ledgers carrying the tag (not just the ≤6 examples), so a finding
    # can report an accurate magnitude for the whole group.
    sensitive_heads = []
    for tag, cnt in sorted(mapping.get("sensitive_summary", {}).items(),
                           key=lambda kv: -kv[1]):
        tagged = [a for a in accounts if tag in a.get("sensitive_tags", [])]
        # sort by |net| so the largest contributors are the ones surfaced by name
        examples = sorted(({"account": a["name"], "code": a.get("code"), "net": a["net"],
                            "source_row": a.get("source_row")} for a in tagged),
                          key=lambda e: abs(e["net"]), reverse=True)[:6]
        total_net = round(sum(a["net"] for a in tagged), 2)
        sensitive_heads.append({"tag": tag, "count": cnt, "total_net": total_net,
                                "examples": examples})

    data_quality = {
        "mapping_confidence_summary": mapping["mapping_confidence_summary"],
        "n_unmapped": len(mapping["unmapped_accounts"]),
        "unmapped_examples": mapping["unmapped_accounts"][:15],
    }

    # 6. contra/rollup pairs — over ALL accounts, not just "material" ones, since a
    # stray leg distorts totals regardless of whether it individually clears the
    # materiality threshold
    contra_pairs = find_contra_pairs(accounts)

    return {
        "abnormal_signs": abnormal_signs[:20],
        "n_abnormal": len(abn),
        "concentration": concentration[:20],
        "round_sums": round_sums[:15],
        "n_round_sums": n_round_sums,
        "round_sums_total": round_sums_total,
        "repeated_amounts": repeated[:10],
        "sensitive_heads": sensitive_heads,
        "contra_pairs": contra_pairs[:15],
        "n_contra_pairs": len(contra_pairs),
        "data_quality": data_quality,
    }
