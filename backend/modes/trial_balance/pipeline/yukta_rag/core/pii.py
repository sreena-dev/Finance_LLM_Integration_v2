"""PII masking for trial-balance account names/codes (spec OUT-04).

Ledger names occasionally carry PAN/GSTIN/bank-account-shaped strings (e.g. a
one-off suspense entry named after a specific vendor account). Masked once at
ingest, before storage, so every downstream consumer (both the audit and compare
pipelines' reports, workbooks and API responses) is already safe rather than
needing the same call repeated at each render site.
"""

from __future__ import annotations

import re

# GSTIN before PAN: a GSTIN embeds a PAN-shaped substring, and masking the PAN
# first would leave X's that no longer match the GSTIN pattern.
_GSTIN_RE = re.compile(r"\b\d{2}[A-Za-z]{5}\d{4}[A-Za-z][A-Za-z\d]Z[A-Za-z\d]\b")
_PAN_RE = re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")
# Conservative length band (11-18 digits) to avoid colliding with shorter GL
# codes or plain amounts.
_BANK_ACCT_RE = re.compile(r"\b\d{11,18}\b")


def _mask_middle(s: str) -> str:
    if len(s) <= 4:
        return "X" * len(s)
    return s[:2] + "X" * (len(s) - 4) + s[-2:]


def mask_pii(text: str | None) -> str | None:
    """Mask PAN-, GSTIN- and bank-account-shaped substrings, keeping the first/
    last 2 characters visible. Returns the input unchanged if it's None/empty or
    not a string."""
    if not text or not isinstance(text, str):
        return text
    text = _GSTIN_RE.sub(lambda m: _mask_middle(m.group(0)), text)
    text = _PAN_RE.sub(lambda m: _mask_middle(m.group(0)), text)
    text = _BANK_ACCT_RE.sub(lambda m: _mask_middle(m.group(0)), text)
    return text
