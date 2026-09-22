"""Wave 8 remark #9: contribution_to_fsli must use a gross-then-sum basis
(abs().sum()), matching contribution_to_tb, not net-then-abs (sum().abs()) --
the latter lets offsetting dr/cr sub-accounts within one FSLI head cancel before
taking abs, producing >100% contribution for one account."""

import json

from modes.trial_balance.pipeline.tools import build_exception_consolidator


def test_offsetting_balances_never_exceed_100_percent_contribution(make_canonical_tb, tmp_path):
    # Two accounts under the same FSLI head (sub_head_1) with near-offsetting balances:
    # net = 6,396.87 - 6,390.00 = 6.87, but the larger account's own gross balance
    # (6,396.87) would read as ~93,000% of that tiny net total under the old
    # sum().abs() basis. Gross-then-sum keeps the FSLI total at the real gross scale.
    canonical_tb_file = make_canonical_tb([
        {"gl_code": "20950021", "gl_name": "SBI- MUSCAT (US$) -R", "closing_balance": 6_396_870_000.0,
         "main_head": "Current assets", "sub_head_1": "Cash and cash equivalents"},
        {"gl_code": "20950022", "gl_name": "SBI- MUSCAT (US$) -P", "closing_balance": -6_390_000_000.0,
         "main_head": "Current assets", "sub_head_1": "Cash and cash equivalents"},
    ])
    for name in ("materiality.json", "financial_snapshot.json", "risk_indicators.json", "relationship_analytics.json", "relationship_graph.json"):
        with open(tmp_path / name, "w") as f:
            json.dump({}, f)
    with open(tmp_path / "sensitive_accounts.json", "w") as f:
        json.dump({
            "account_sensitivity": [
                {"gl_code": "20950021", "gl_name": "SBI- MUSCAT (US$) -R", "account": "20950021 - SBI- MUSCAT (US$) -R",
                 "category": "foreign_currency", "sensitivity_score": 80, "priority": "Critical"},
            ]
        }, f)

    result = build_exception_consolidator(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    with open(tmp_path / "consolidated_exceptions.json") as f:
        payload = json.load(f)
    exc = payload["exceptions"][0]
    assert exc["financial_context"]["contribution_to_fsli"] <= 100.0
