"""Wave 9 remark #12: the PS-RECOVERY question's "deposit" tag sweeps in both genuine
deposits paid out (asset, debit-normal) and payables withheld from someone else
(liability, credit-normal, e.g. retention money) -- the latter needs payable-appropriate
assertions (Completeness/Classification/Cut-off), not asset-side recoverability ones
(Existence/Valuation/Recoverability)."""

import json

from modes.trial_balance.pipeline.tools import build_public_sector_lens


def _hit_for(records, gl_code):
    for r in records:
        if r.get("source_row_id") == gl_code:
            return r
    raise AssertionError(f"no finding for gl_code={gl_code}")


def test_liability_retention_money_gets_payable_appropriate_assertions(make_canonical_tb, tmp_path):
    canonical_tb_file = make_canonical_tb([
        {"gl_code": "10908001", "gl_name": "Retention Money Payable", "closing_balance": -5_000_000.0,
         "main_head": "Current liabilities", "account_type": "Liability"},
    ])
    (tmp_path / "engagement_context.json").write_text(json.dumps({
        "applicability_gates": {"public_sector_lens": {"applicable": True, "basis": "confirmed"}}
    }), encoding="utf-8")
    (tmp_path / "materiality.json").write_text(json.dumps({"thresholds": {"performance": 1_000_000.0}}), encoding="utf-8")

    result = build_public_sector_lens(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    payload = json.loads((tmp_path / "public_sector_lens.json").read_text(encoding="utf-8"))
    hit = _hit_for(payload["finding_records"], "10908001")
    assert hit["assertion"] == ["Completeness", "Classification", "Cut-off"]


def test_asset_deposit_paid_keeps_recoverability_assertions(make_canonical_tb, tmp_path):
    canonical_tb_file = make_canonical_tb([
        {"gl_code": "1500", "gl_name": "Security Deposit Paid", "closing_balance": 2_000_000.0,
         "main_head": "Non-current assets", "account_type": "Asset"},
    ])
    (tmp_path / "engagement_context.json").write_text(json.dumps({
        "applicability_gates": {"public_sector_lens": {"applicable": True, "basis": "confirmed"}}
    }), encoding="utf-8")
    (tmp_path / "materiality.json").write_text(json.dumps({"thresholds": {"performance": 1_000_000.0}}), encoding="utf-8")

    result = build_public_sector_lens(canonical_tb_file=str(canonical_tb_file), output_dir=str(tmp_path))
    assert result["execution_status"] == "SUCCESS"

    payload = json.loads((tmp_path / "public_sector_lens.json").read_text(encoding="utf-8"))
    hit = _hit_for(payload["finding_records"], "1500")
    assert hit["assertion"] == ["Existence", "Valuation", "Recoverability"]
