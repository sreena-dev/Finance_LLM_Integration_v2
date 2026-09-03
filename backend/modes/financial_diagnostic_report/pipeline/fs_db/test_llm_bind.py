"""
Offline test of the Layer 2b LLM row proposer. NO database, NO model.

The proposer is a stubbed `propose_fn`, so this exercises the part that actually carries
risk -- eligibility, vetting, shadow containment and the trust gate -- rather than a
model's taste in labels. A synthetic balance sheet is used whose PPE line is deliberately
named something no LineSpec pattern anticipates ("Fixed Assets (Net Block)"), which is
the one failure mode this layer exists for.

Run:  python -m fs_db.test_llm_bind
"""
import json
import sys

from .md_parser import parse_table_md
from .binding import Resolver
from . import llm_bind as LB

# `net_fixed_assets` is named unconventionally on purpose -> PATTERN_NO_MATCH.
# Everything else is Schedule III standard, so the rules bind it and it becomes a CONTROL.
BS_MD = """
| Particulars | Note | As at March 31, 2025 | As at March 31, 2024 |
| --- | --- | --- | --- |
| ASSETS | | | |
| Non-Current Assets | | | |
| (a) Fixed Assets (Net Block) | 3 | 5,000.00 | 4,800.00 |
| (b) Other non-current assets | 4 | 1,000.00 | 900.00 |
| Total Non-Current Assets | | 6,000.00 | 5,700.00 |
| Current Assets | | | |
| (a) Inventories | 5 | 800.00 | 700.00 |
| (b) Trade receivables | 6 | 1,200.00 | 1,100.00 |
| (c) Cash and cash equivalents | 7 | 500.00 | 400.00 |
| Total Current Assets | | 2,500.00 | 2,200.00 |
| TOTAL ASSETS | | 8,500.00 | 7,900.00 |
| EQUITY AND LIABILITIES | | | |
| Equity | | | |
| (a) Equity share capital | 8 | 2,000.00 | 2,000.00 |
| (b) Other equity | 9 | 3,500.00 | 3,000.00 |
| Total equity | | 5,500.00 | 5,000.00 |
| Non-Current Liabilities | | | |
| (a) Borrowings | 10 | 1,800.00 | 1,900.00 |
| Total Non-Current Liabilities | | 1,800.00 | 1,900.00 |
| Current Liabilities | | | |
| (a) Trade payables | 11 | 700.00 | 600.00 |
| (b) Short-term borrowings | 12 | 500.00 | 400.00 |
| Total Current Liabilities | | 1,200.00 | 1,000.00 |
| TOTAL EQUITY AND LIABILITIES | | 8,500.00 | 7,900.00 |
"""

PPE_ROW = 2          # "(a) Fixed Assets (Net Block)"  -- value 5,000.00
INVENTORIES_ROW = 6  # bound by rule -> used as the CONTROL the model should agree with
TRADE_RECV_ROW = 7   # already bound by rule -> must be refused if re-proposed


def stub(answer):
    """A propose_fn that ignores the prompt and returns a canned reply."""
    def _fn(prompt):
        _fn.prompt = prompt
        return json.dumps(answer)
    _fn.prompt = ""
    return _fn


def check(name, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got!r}" + ("" if ok else f"  != {want!r}"))
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pt = parse_table_md(BS_MD, table_id="T1", statement="balance_sheet")
    print(f"parsed: {len(pt.rows)} rows, periods={pt.periods}\n")

    ok = True

    # -- baseline: no proposer at all. Must be byte-identical behaviour to today. -------
    base = Resolver().bind_statement(pt, "BS")
    print("1. BASELINE (no propose_fn)")
    ok &= check("net_fixed_assets abstained", "net_fixed_assets" in base.abstained, True)
    reason = next(a.reason for a in base.attempts if a.key == "net_fixed_assets")
    ok &= check("abstention reason is eligible", reason in LB.ELIGIBLE_REASONS, True)
    ok &= check("no proposals recorded", base.proposals, [])
    ok &= check("balance sheet still ties out",
                [v["status"] for v in base.validations
                 if v["check"] == "balance_sheet_balances"], ["PASS"])

    # -- shadow mode: proposal is recorded, and CANNOT reach `bound` -------------------
    print("\n2. SHADOW MODE (the default)")
    fn = stub({"proposals": [
        {"key": "net_fixed_assets", "row": PPE_ROW, "confidence": "high",
         "why": "net block of fixed assets"},
        {"key": "inventories", "row": INVENTORIES_ROW, "confidence": "high", "why": "control"},
    ]})
    rep = Resolver(propose_fn=fn).bind_statement(pt, "BS")
    gap = next(x for x in rep.proposals if x["key"] == "net_fixed_assets")
    ok &= check("proposal outcome", gap["outcome"], "SHADOW")
    ok &= check("proposal carries the row's own figure", gap["value"], 5000.0)
    ok &= check("proposal did NOT enter bound", "net_fixed_assets" in rep.bound, False)
    ok &= check("still abstained", "net_fixed_assets" in rep.abstained, True)
    ctl = [x for x in rep.proposals if x["kind"] == "control"]
    ok &= check("control was scored", bool(ctl) and ctl[0]["agrees"], True)
    ok &= check("prompt withholds every figure", "5,000" in fn.prompt or "5000" in fn.prompt, False)
    info = next(v for v in rep.validations if v["check"] == "llm_propose")
    print(f"     scoreboard: {info['inputs']}")

    # -- live mode: enters bound as untrusted, and the tie-out promotes it -------------
    print("\n3. LIVE MODE (untrusted until arithmetic confirms)")
    rep = Resolver(propose_fn=fn, llm_shadow=False).bind_statement(pt, "BS")
    ln = rep.bound.get("net_fixed_assets")
    ok &= check("provenance", ln.provenance if ln else None, "llm")
    ok &= check("value read from the row, not the model", ln.value if ln else None, 5000.0)
    ok &= check("prior period picked up too", ln.prior if ln else None, 4800.0)
    ok &= check("page/table provenance retained", (ln.table_id, ln.label) if ln else None,
                ("T1", "(a) Fixed Assets (Net Block)"))
    # net_fixed_assets takes part in no BS identity here, so nothing can confirm it ->
    # it must stay OUT of the trusted set. This is the load-bearing assertion.
    ok &= check("untrusted without a tie-out", ln.trusted() if ln else None, False)
    ok &= check("excluded from trusted()", "net_fixed_assets" in rep.trusted(), False)

    # -- the vetting gates ------------------------------------------------------------
    print("\n4. VETTING (each bad proposal must be DROPPED, not bound)")
    cases = [
        ("row already bound to another key", TRADE_RECV_ROW),
        ("out-of-range row index", 999),
    ]
    for label, row in cases:
        r = Resolver(propose_fn=stub({"proposals": [
            {"key": "net_fixed_assets", "row": row, "confidence": "high", "why": "x"}]}),
            llm_shadow=False).bind_statement(pt, "BS")
        ok &= check(label, "net_fixed_assets" in r.bound, False)

    # A structural total must never even be OFFERED to the model. Asserted against the
    # PROMPT ITSELF -- checking a scoreboard field would pass vacuously if the field were
    # ever renamed or dropped, which is exactly how a safety test rots into decoration.
    probe = stub({"proposals": []})
    Resolver(propose_fn=probe, llm_shadow=False).bind_statement(pt, "BS")
    keys_block = probe.prompt.split("KEYS:")[1]
    ok &= check("structural total 'total_assets' not offered",
                "total_assets" in keys_block, False)
    ok &= check("structural total 'total_equity' not offered",
                "total_equity:" in keys_block, False)
    ok &= check("but a real gap IS offered", "net_fixed_assets" in keys_block, True)
    # And a proposal for an ineligible key must be refused even if the model volunteers it.
    r = Resolver(propose_fn=stub({"proposals": [
        {"key": "total_assets", "row": 10, "confidence": "high", "why": "volunteered"}]}),
        llm_shadow=False).bind_statement(pt, "BS")
    ok &= check("volunteered ineligible key ignored",
                r.bound["total_assets"].provenance, "rule")

    # -- resilience: a broken proposer must be a no-op, never an exception -------------
    print("\n5. RESILIENCE")
    def boom(_):
        raise RuntimeError("endpoint down")
    r = Resolver(propose_fn=boom).bind_statement(pt, "BS")
    ok &= check("exception contained", next(
        v["status"] for v in r.validations if v["check"] == "llm_propose"), "SKIP")
    ok &= check("rules unaffected by proposer failure",
                r.bound["total_assets"].value, 8500.0)
    for junk in ["not json at all", '{"proposals": [{"key": "net_fixed_assets", "row": true}]}',
                 '{"proposals": [{"key": "nonexistent_key", "row": 2}]}', ""]:
        r = Resolver(propose_fn=stub_raw(junk), llm_shadow=False).bind_statement(pt, "BS")
        ok &= check(f"junk reply {junk[:28]!r} bound nothing",
                    "net_fixed_assets" in r.bound, False)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


def stub_raw(text):
    def _fn(prompt):
        return text
    return _fn


if __name__ == "__main__":
    raise SystemExit(main())
