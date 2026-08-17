"""
The financial-statement assertions the FDR maps risk clusters to.

The canonical set is the one FDR §10.3 lists. Appendix D, however, uses `Occurrence` for
the revenue/receivable cluster — a transaction-level assertion that §10.3's list does not
name. Rather than silently rewrite it to `existence`, both are carried and the divergence
is recorded here, because which one the audit side means changes what gets tested:
`existence` asks whether the receivable is there at the reporting date, `occurrence`
asks whether the sale that created it actually happened.

Appendix D also assigns "condition compliance" to the grant cluster. That is a regularity
matter, not an assertion. It is modelled as a separate REGULARITY_MATTERS axis so the
assertion list stays clean and the regularity point is not lost.
""" 
from __future__ import annotations

# ---- §10.3 canonical assertion set ----------------------------------------------
EXISTENCE = "existence"
RIGHTS_AND_OBLIGATIONS = "rights_and_obligations"
COMPLETENESS = "completeness"
VALUATION_AND_ALLOCATION = "valuation_and_allocation"
ACCURACY = "accuracy"
CUTOFF = "cutoff"
CLASSIFICATION = "classification"
PRESENTATION = "presentation"

SPEC_10_3 = frozenset({
    EXISTENCE, RIGHTS_AND_OBLIGATIONS, COMPLETENESS, VALUATION_AND_ALLOCATION,
    ACCURACY, CUTOFF, CLASSIFICATION, PRESENTATION,
})

# ---- carried from Appendix D, outside the §10.3 list -----------------------------
OCCURRENCE = "occurrence"          # App D, revenue/receivable cluster
APPENDIX_D_EXTRA = frozenset({OCCURRENCE})

ASSERTIONS = SPEC_10_3 | APPENDIX_D_EXTRA

# ---- not assertions: the public-sector regularity axis (§2.4, §3.3) --------------
CONDITION_COMPLIANCE = "condition_compliance"
REGULARITY = "regularity"
PROPRIETY = "propriety"

REGULARITY_MATTERS = frozenset({CONDITION_COMPLIANCE, REGULARITY, PROPRIETY})

HUMAN_LABEL = {
    EXISTENCE: "Existence",
    RIGHTS_AND_OBLIGATIONS: "Rights and obligations",
    COMPLETENESS: "Completeness",
    VALUATION_AND_ALLOCATION: "Valuation and allocation",
    ACCURACY: "Accuracy",
    CUTOFF: "Cut-off",
    CLASSIFICATION: "Classification",
    PRESENTATION: "Presentation",
    OCCURRENCE: "Occurrence",
    CONDITION_COMPLIANCE: "Grant-condition compliance",
    REGULARITY: "Regularity",
    PROPRIETY: "Propriety",
}
