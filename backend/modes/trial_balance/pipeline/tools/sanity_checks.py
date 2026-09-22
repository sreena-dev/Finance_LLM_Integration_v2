"""Post-hoc, non-blocking plausibility checks over already-classified rows.

Neither snap-to-taxonomy validation nor the staged-narrowing vocabulary
list can catch a row that resolved to a *valid* taxonomy entry that is
nonetheless the wrong one for that account -- both depend on the row
failing to resolve in order to be caught. These checks close that gap from
the other side: they look at the engine's own output for plausibility,
independent of taxonomy validity. Neither function here ever mutates a row
or its mapped_status -- both are purely additive signals for a human
reviewer.

Ported from TB_normalization_v1's core/sanity_checks.py.
"""

from __future__ import annotations

import re
from collections import defaultdict

# Multi-word signals are used verbatim (substring match); single words are
# common enough that a shorter/looser signal would over-trigger on
# unrelated vocabulary ("advance" alone would flag "Advance tax paid").
# "deposit" is deliberately NOT a blanket signal -- see
# _DEPOSIT_ASSET_CONTEXT below, which handles it directionally.
_LIABILITY_SIGNALS = ("payable", "retention", "advance received")
_ASSET_SIGNALS = ("receivable", "advance paid", "prepaid")

# "deposit" is directionally ambiguous: it signals Liability when the
# company is the *receiver* of the deposit ("Security Deposit Payable",
# "EMD"), but signals (correctly) Asset when the company is the *placer*
# ("Term Deposit" held at a bank, "Deposits Govt Agency" placed with a
# government body). These asset-side context words suppress the flag when
# co-occurring with "deposit" anywhere in the name; any OTHER name
# containing "deposit" defaults to flagged -- fail toward visibility, not
# silence.
_DEPOSIT_ASSET_CONTEXT = (
    "term", "fixed", "bank", "govt", "government", "court", "authority", "margin", "made",
    "tax", "pre-deposit",
)

# "Liab" as a whole word/token, not a substring -- ERP exports routinely
# abbreviate "Liability" this way in the GL name itself.
_LIAB_ABBREV_RE = re.compile(r"\bliab\b", re.IGNORECASE)


def _has_liability_signal(name: str) -> bool:
    if "deposit" in name and not any(term in name for term in _DEPOSIT_ASSET_CONTEXT):
        return True
    return any(term in name for term in _LIABILITY_SIGNALS) or bool(_LIAB_ABBREV_RE.search(name))


def find_account_type_keyword_conflicts(rows: list) -> list:
    """Flags mapped rows whose GL name carries a strong, unidirectional
    liability/asset-signaling keyword that contradicts the derived
    account_type. Returns the flagged rows; callers are expected to
    log/count them, never to act on the flag automatically."""
    flagged = []
    for row in rows:
        if row.mapped_status != "MAPPED" or not row.account_type:
            continue
        name = row.gl_name.lower()
        is_liability_signal = _has_liability_signal(name)
        is_asset_signal = any(term in name for term in _ASSET_SIGNALS)
        if is_liability_signal and row.account_type == "Asset":
            flagged.append(row)
        elif is_asset_signal and row.account_type == "Liability":
            flagged.append(row)
    return flagged


_NAME_SPLIT_RE = re.compile(r"\s+")


def _name_prefix_key(gl_name: str, prefix_words: int = 3) -> str:
    tokens = [t for t in _NAME_SPLIT_RE.split(gl_name.strip().upper()) if t]
    return " ".join(tokens[:prefix_words])


# Cash-credit / overdraft accounts legitimately flip between Asset (net
# debit) and Liability (net credit) depending on closing balance sign at a
# point in time -- excluded from grouping entirely rather than handled via
# a stricter name-match key.
_CC_OD_MARKERS = ("c c -", "cc -", "cash credit", "overdraft", "od a/c", "od account", "cr a/c")


def _is_cc_od_account(gl_name: str) -> bool:
    name = gl_name.lower()
    return any(marker in name for marker in _CC_OD_MARKERS)


# A BS-side provision (Liability) and its PL-side counterpart (Expense)
# legitimately sharing a name is standard chart-of-accounts design, not an
# inconsistency.
_PROVISION_WORD_RE = re.compile(r"\bprov", re.IGNORECASE)


def _is_provision_bs_pl_pair(key: str, group_rows: list) -> bool:
    if len(group_rows) != 2 or not _PROVISION_WORD_RE.search(key):
        return False
    sides = {(r.bs_pl, r.account_type) for r in group_rows}
    return sides == {("BS", "Liability"), ("PL", "Expense")}


def find_inconsistent_same_name_groups(rows: list, prefix_words: int = 3) -> dict:
    """Groups mapped rows by normalized GL-name prefix and flags any group
    where the same (or near-identical) name resolved to more than one
    (main_head, sub_head_1, account_type) combination within the same
    file."""
    groups: dict = defaultdict(list)
    for row in rows:
        if row.mapped_status != "MAPPED" or _is_cc_od_account(row.gl_name):
            continue
        key = _name_prefix_key(row.gl_name, prefix_words)
        if key:
            groups[key].append(row)

    inconsistent: dict = {}
    for key, group_rows in groups.items():
        if len(group_rows) < 2:
            continue
        combos = {(r.main_head, r.sub_head_1, r.account_type) for r in group_rows}
        if len(combos) > 1 and not _is_provision_bs_pl_pair(key, group_rows):
            inconsistent[key] = group_rows
    return inconsistent
