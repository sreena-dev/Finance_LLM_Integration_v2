"""
Concepts and taxonomy-namespace labels for Block 1 (Company Overview) and the
Reporting Framework row it carries.

Both values below are read from real, structured facts already confirmed present in
as_db (see the implementation plan): `financial_facts.namespace` is populated for
every row (only two values occur across the whole corpus, 'ind-as' and 'in-ca'), and
`StatementOfIndAsComplianceExplanatory` is a genuine disclosure concept present for
797 of 803 filings — the entity's own statement that its financial statements comply
with Ind AS. Neither is inferred from free text; both are read structurally by
concept/column name.
"""
from __future__ import annotations

# The disclosure concept whose presence is the entity's own explicit statement of
# Ind-AS compliance — used as the framework-detection "read from the filing" quote.
FRAMEWORK_DISCLOSURE_CONCEPT = "StatementOfIndAsComplianceExplanatory"

# Human-readable labels for the XBRL taxonomy namespaces actually found in as_db.
# 'ind-as' is the accounting-standard-specific taxonomy; 'in-ca' is the general
# Company-Affairs taxonomy (entity identification, filing metadata) that co-occurs
# on every filing alongside it — its presence is expected, not a mixed-framework
# signal in its own right.
NAMESPACE_LABELS: dict[str, str] = {
    "ind-as": "Ind AS",
    "in-ca": "General Commercial & Industrial (Company Affairs)",
}

# The namespace whose dominance we read as "this filing's reporting framework is
# Ind AS". Kept as a named constant rather than a magic string at the call site.
IND_AS_NAMESPACE = "ind-as"
