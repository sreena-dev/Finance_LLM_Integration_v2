"""Hermetic tests for the FS entity resolver — no database, no model, no network.

The ladder is pure over a list of rows, which is the whole reason it was written
that way: the behaviour that decides whether a user's question reaches their
company is testable without standing anything up.

Run:  python -m pytest modes/financial_statement/test_entity_resolution.py
      python modes/financial_statement/test_entity_resolution.py   (no pytest)

The stored names below are the FORMS, not a claim about the corpus's contents —
underscore-joined, space-joined, with and without a legal suffix, because
`documents.company` is a raw string with no convention enforced on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python test_entity_resolution.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from modes.financial_statement import entity_resolution as ER  # noqa: E402

STORED = [
    "Gujarat_Gas",
    "Gujarat_State_Petronet",
    "GAIL_India",
    "Coal India",
    "Oil and Natural Gas Corporation",
    "Bharat_Petroleum_Corporation_Limited",
    "Steel_Authority_of_India_Limited",
    "NTPC",
]

ROWS = [
    {"doc_id": f"{c}_{fy}", "company": c, "fy_start": fy - 1, "fy_end": fy,
     "doc_name": f"{c} Annual Report"}
    for c in STORED for fy in (2022, 2023, 2024)
]


def _one(query: str) -> str | None:
    """The single company `query` resolves to, or None if not unambiguous."""
    rows, _stage = ER.match_company(query, ROWS)
    names = {r["company"] for r in rows}
    return names.pop() if len(names) == 1 else None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_legal_suffixes_normalise_away():
    """The reported bug in one assertion.

    Every one of these is a form a model emits for the same company, and under
    the branch's one-directional substring match only the bare form resolved.
    """
    forms = ["Gujarat_Gas", "Gujarat Gas", "Gujarat Gas Limited",
             "Gujarat Gas Ltd", "Gujarat Gas Ltd.", "GUJARAT GAS LTD.",
             "Gujarat Gas Company Limited", "  gujarat   gas  "]
    assert len({ER.normalise(f).norm for f in forms}) == 1


def test_india_is_not_noise():
    """'Coal India' and 'Coal' are different entities; stripping India merges them."""
    assert ER.normalise("Coal India").norm != ER.normalise("Coal").norm
    assert "india" in ER.normalise("GAIL India").tokens


def test_all_noise_name_keeps_a_token():
    """A name made only of noise words must not normalise to '' — that matches
    everything."""
    assert ER.normalise("The Company Ltd").norm != ""


def test_both_acronym_forms_are_derived():
    """Real acronyms take their last letter from the suffix that `_NOISE` strips,
    so the suffix-inclusive form has to be carried too."""
    assert "ongc" in ER.normalise("Oil and Natural Gas Corporation").acronyms
    assert "bpcl" in ER.normalise("Bharat_Petroleum_Corporation_Limited").acronyms
    assert "sail" in ER.normalise("Steel_Authority_of_India_Limited").acronyms


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

def test_suffix_expansion_resolves():
    """The primary regression: the exact input the old matcher rejected."""
    assert _one("Gujarat Gas Limited") == "Gujarat_Gas"
    assert _one("Coal India Limited") == "Coal India"
    assert _one("NTPC Ltd") == "NTPC"


def test_acronyms_resolve():
    assert _one("ONGC") == "Oil and Natural Gas Corporation"
    assert _one("BPCL") == "Bharat_Petroleum_Corporation_Limited"
    assert _one("SAIL") == "Steel_Authority_of_India_Limited"


def test_partial_names_resolve():
    assert _one("GAIL") == "GAIL_India"
    assert _one("Bharat Petroleum") == "Bharat_Petroleum_Corporation_Limited"


def test_ampersand_and_case_are_not_identity():
    assert _one("Oil & Natural Gas Corporation Limited") == \
        "Oil and Natural Gas Corporation"
    assert _one("gujarat gas") == "Gujarat_Gas"


def test_unrelated_companies_never_match():
    """The safety property. A false positive answers one company's figures under
    another's name, which is worse than answering nothing."""
    for name in ["Reliance Industries", "Tata Steel", "Infosys",
                 "Wipro Limited", "Adani Green Energy", "HDFC Bank"]:
        rows, stage = ER.match_company(name, ROWS)
        assert rows == [], f"{name!r} wrongly matched at stage {stage}"


def test_shared_prefix_is_ambiguous_never_a_guess():
    """Two companies start with 'Gujarat'. Returning either one would be a
    fabrication; the caller must be asked."""
    rows, _ = ER.match_company("Gujarat", ROWS)
    assert {r["company"] for r in rows} == {"Gujarat_Gas", "Gujarat_State_Petronet"}
    assert _one("Gujarat") is None


def test_empty_input_matches_nothing():
    for value in ["", "   ", None]:
        assert ER.match_company(value, ROWS)[0] == []


# ---------------------------------------------------------------------------
# The messages — this is what the model reads and paraphrases
# ---------------------------------------------------------------------------

def test_missing_year_says_the_company_is_present():
    """Cause (2): the branch reported a missing YEAR as a missing COMPANY.

    The message must state the company exists, name the years that do, and
    forbid the 'no data for this entity' paraphrase outright.
    """
    matches = [r for r in ROWS if r["company"] == "Gujarat_Gas"]
    msg = ER.format_ambiguous("Gujarat Gas Limited", "FY2019-20", matches)
    assert "Gujarat_Gas" in msg
    assert "FY2021-22" in msg and "FY2023-24" in msg
    assert "has not been ingested" in msg
    assert "Do NOT report that no data exists" in msg


def test_multiple_companies_lists_the_stored_names():
    """The model can only echo a name back correctly if it is shown the stored
    form."""
    matches = [r for r in ROWS
               if r["company"] in {"Gujarat_Gas", "Gujarat_State_Petronet"}]
    msg = ER.format_ambiguous("Gujarat", "", matches)
    assert "Gujarat_Gas" in msg and "Gujarat_State_Petronet" in msg


def test_no_matches_message_is_a_coverage_gap_not_a_finding():
    msg = ER.format_ambiguous("Nonexistent Corp", "", [])
    assert "coverage gap" in msg


# ---------------------------------------------------------------------------
# The install contract
# ---------------------------------------------------------------------------

def test_install_rejects_a_changed_signature():
    """A re-pull that re-signatures the resolver must fail loudly, not silently
    leave the original bug in place."""
    class Drifted:
        @staticmethod
        def _resolve_document(name, fy, conn):   # 'company' renamed
            ...
        @staticmethod
        def format_ambiguous(company, label, matches, ask="x"):
            ...
        @staticmethod
        def latest_fy_end(company, conn):
            ...
        @staticmethod
        def _parse_fy(financial_year):
            ...

    try:
        ER.install(Drifted)
    except ER.ResolverContractError as exc:
        assert "_resolve_document" in str(exc)
    else:
        raise AssertionError("install() accepted a drifted signature")


def test_install_replaces_all_three():
    class Fake:
        @staticmethod
        def _resolve_document(company, financial_year, conn):
            return "original"
        @staticmethod
        def format_ambiguous(company, label, matches, ask="x"):
            return "original"
        @staticmethod
        def latest_fy_end(company, conn):
            return "original"
        @staticmethod
        def _parse_fy(financial_year):
            return (None, None)

    ER.install(Fake)
    assert Fake._resolve_document is ER.resolve_document
    assert Fake.format_ambiguous is ER.format_ambiguous
    assert Fake.latest_fy_end is ER.latest_fy_end


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - a plain runner wants the text
            failures += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
