"""Wave 9 remark #16: EPC/construction-domain evidence themes (claims, mobilisation
advances, retention money, amounts withheld/recoverable, provisions/write-offs,
foreign operations) previously fell through to the generic "General" bucket."""

from modes.trial_balance.pipeline.tools import _route


def test_claims_and_retention_route_to_contract_exposures():
    assert _route("Obtain the claim recognition workings supporting this balance") == "Contract Exposures & Retention"
    assert _route("Retention money withheld from the contractor") == "Contract Exposures & Retention"
    assert _route("Amount recoverable for a shortfall identified in the period") == "Contract Exposures & Retention"


def test_provisions_and_writeoffs_route_correctly():
    assert _route("Provision computation basis for the obligation") == "Provisions & Write-Offs"
    assert _route("Sanction evidencing authority for the write-off") == "Provisions & Write-Offs"


def test_foreign_operations_route_correctly():
    assert _route("Foreign branch reconciliation for the Oman segment") == "Foreign Operations & Segments"


def test_unmatched_evidence_still_falls_to_general():
    assert _route("Some entirely unrelated evidentiary request") == "General"
