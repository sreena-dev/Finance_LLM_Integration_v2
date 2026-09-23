"""Shared pytest bootstrap for the SAR (sar_prod_v3) test suite.

`sar_prod_v3`'s own modules import each other absolutely
(`from sar_prod_v3.tool_sar import ...`), matching how the branch code this
package vendors is written (see adapter.py's module docstring) — so
`statutory_auditor_report/` (the parent of `sar_prod_v3/`) must be on
sys.path for `import sar_prod_v3.xxx` to resolve, regardless of the
directory pytest is invoked from. This mirrors adapter.py's own
`_ensure_path()` at runtime.

Deliberately imports nothing from `sar_prod_v3.agent` / `chat_agents` here:
those pull in `yukta` (only needed for live LLM calls) at import time in
some code paths. Everything under this `tests/` directory targets the
deterministic, DB/LLM-free layer (observation.py, check_registry.py,
formal_review.py, tool_sar.CheckTools/ComputeTools, and the pipeline's pure
wiring methods) on purpose — see GAP_CLOSURE_LOG.md, "What's tested".
"""

import sys
from pathlib import Path

_STATUTORY_AUDITOR_REPORT_DIR = Path(__file__).resolve().parents[2]
if str(_STATUTORY_AUDITOR_REPORT_DIR) not in sys.path:
    sys.path.insert(0, str(_STATUTORY_AUDITOR_REPORT_DIR))
