"""Worked few-shot examples for classification prompts, shared by
classification.py (single-shot) and staged_narrowing.py (stage 1) -- one
place that defines "what a correct classification looks like."

Ported verbatim from TB_normalization_v1's core/examples.py.
"""

from __future__ import annotations

FEW_SHOT = {
    "IND_AS": [
        {
            "gl_code": "EX-1", "gl_name": "Freehold Land",
            "selected": {
                "gl_code": "EX-1", "bs_pl": "BS", "main_head": "Non-current assets",
                "sub_head_1": "Property, Plant and Equipment", "sub_head_2": "Land",
            },
        },
        {
            "gl_code": "EX-2", "gl_name": "Sundry Creditors (MSME)",
            "selected": {
                "gl_code": "EX-2", "bs_pl": "BS", "main_head": "Current liabilities",
                "sub_head_1": "Trade payables",
                "sub_head_2": "Total outstanding dues of micro and small enterprises",
            },
        },
        {
            "gl_code": "EX-3", "gl_name": "Salaries and Wages",
            "selected": {
                "gl_code": "EX-3", "bs_pl": "PL", "main_head": "Expenses",
                "sub_head_1": "Employee benefits expense", "sub_head_2": "Salaries and wages",
            },
        },
    ],
    "AS": [
        {
            "gl_code": "EX-1", "gl_name": "Factory Building",
            "selected": {
                "gl_code": "EX-1", "bs_pl": "BS", "main_head": "Non-current assets",
                "sub_head_1": "Tangible assets", "sub_head_2": "Buildings",
            },
        },
        {
            "gl_code": "EX-2", "gl_name": "Sundry Creditors (MSME)",
            "selected": {
                "gl_code": "EX-2", "bs_pl": "BS", "main_head": "Current liabilities",
                "sub_head_1": "Trade payables",
                "sub_head_2": "Total outstanding dues of micro and small enterprises",
            },
        },
        {
            "gl_code": "EX-3", "gl_name": "Salaries and Wages",
            "selected": {
                "gl_code": "EX-3", "bs_pl": "PL", "main_head": "Expenses",
                "sub_head_1": "Employee benefits expense", "sub_head_2": "Salaries and wages",
            },
        },
    ],
}

# Stage-1-shaped worked examples (gl_code, gl_name, main_head, bs_pl only --
# sub_head_1/sub_head_2 aren't decided at this stage), derived from
# FEW_SHOT rather than hand-duplicated.
STAGE1_FEW_SHOT = {
    standard: [
        {"gl_code": ex["gl_code"], "gl_name": ex["gl_name"],
         "main_head": ex["selected"]["main_head"], "bs_pl": ex["selected"]["bs_pl"]}
        for ex in examples
    ]
    for standard, examples in FEW_SHOT.items()
}
