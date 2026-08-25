# Trial Balance tests

Ported verbatim from `Finance_llm_v2` (`main @ 3a6e9bc`, `backend/tests/`), with
one patch per file: the source repo's hardcoded `sys.path.insert(0, "/app")` /
`sys.path.insert(0, "backend")` pair is replaced by a `__file__`-relative insert
of `../pipeline`, where this integration keeps the vendored `yukta_rag` package.
Nothing else was changed.

| File | Covers | Tests |
|---|---|---|
| `test_tb_validation.py` | the deterministic rule gate — one Pass + one Fail/Warning/Halt fixture per rule, plus the cross-year layers and narrative guards | 48 |
| `test_audit_report_fixes.py` | FSLI table syntax, the structural-compliance gate, mirrored-pair detection, multi-period label parsing | 9 |
| `test_gap_closure_fixes.py` | structural clearing-series detection and its false-positive guards, relationship severity, report rendering | 7 |
| `test_grouping_parser.py` | the grouping-file parser: synonym headers, line-item-heading layout, TB value-matching, and the diagnostics on failure | 8 |

## Running

They are standalone scripts with no pytest dependency, and are also valid pytest
modules (every case is a `test_*` function). Either works:

```sh
cd backend/modes/trial_balance/tests
python test_tb_validation.py        # or any other file
python -m pytest .                 # collects all 72
```

`test_grouping_parser.py` needs only pandas. The other three import
`trial_balance/tb_tools.py` or `agents/audit_agents.py`, which import `yukta` at
module scope — so they need `yukta` installed, even though none of them makes an
LLM call. It is not on PyPI; install the wheel this repo vendors:

```sh
pip install --no-deps backend/vendor/yukta-2.1.0-py3-none-any.whl
```

`--no-deps` is enough for these tests: they exercise deterministic Python, so
none of yukta's own runtime dependencies (anthropic, mcp, pymilvus, …) is
actually reached.

## Not ported

`test_pdf_review_fixes.py` — it covers the source repo's chat/PDF-review
feature, which this integration does not vendor (see `../adapter.py`).
