"""Standalone, deterministic trial-balance validation/comparison gate.

Fully separate from ``yukta_rag.audit`` (the FSLI/Focus-Areas/materiality
engine). Produces mechanical PASS/WARNING/HALT/SKIPPED validation results and
PY-vs-CY structural/variance observations only — never risk findings, FSLI
classification, or narrative audit conclusions. Nothing in this package
imports from ``yukta_rag.audit`` except one explicit, narrow exception
(``sensitive_tags`` reuse in ``tbv_rules_completeness.py`` for TB-028, by
deliberate instruction — not a merge of the two systems).
"""
