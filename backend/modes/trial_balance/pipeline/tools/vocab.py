"""Ambiguous-vocabulary trigger list, shared by classification.py (for the
hint-vs-GL-name-conflict policy) and staged_narrowing.py (for routing) --
single source of truth, no duplication.

Ported verbatim from TB_normalization_v1's core/vocab.py.
"""

from __future__ import annotations

import re

# Vocabulary observed to trigger cross-branch sub_head_2/main_head confusion
# under a single-call tree prompt. Two confirmed clusters:
#
# 1. PL/Employee benefits expense vs. BS/Provisions confusion (e.g.
#    "Provision for employee benefits" pulled into the expense branch, or
#    "Contribution to provident and other funds" pulled into Provisions).
# 2. Liability-signaling account names (payable/deposit/retention/advance
#    received) resolved onto an Asset-side branch -- "Payable to Railways",
#    "Security Deposit Pay[able]", "Retention Money Pay[able]", "EMD
#    Payable" all landed on Asset branches while structurally identical
#    sibling GL codes correctly resolved to Liability.
#
# Grow this list only from confirmed repeat confusions -- don't preemptively
# add terms that haven't actually caused a mismatch.
AMBIGUOUS_VOCAB = (
    "provident", "staff welfare", "employee benefit", "provision for employee",
    "payable", "deposit", "retention", "advance received",
)

# Matched as a whole word/token, not a substring: ERP exports routinely
# abbreviate "Liability" to "Liab" in the GL name itself -- a plain
# substring check misses it, but a bare substring check for "liab" would
# also misfire on unrelated words like "reliable"/"unreliable".
LIAB_ABBREV_RE = re.compile(r"\bliab\b", re.IGNORECASE)


def needs_staged_narrowing(gl_name: str, hint: str) -> bool:
    text = f"{gl_name} {hint}".lower()
    return any(term in text for term in AMBIGUOUS_VOCAB) or bool(LIAB_ABBREV_RE.search(text))
