"""
The planning-materiality filter (spec Sec 2.4 / Sec 10.6) for the S01-S27 XBRL signal
library — kept in its own file, separate from signal detection, per the user's explicit
requirement. A rule in `xbrl_signal_rules.py` decides whether a signal FIRES; this module
decides, independently, whether a fired signal is MATERIAL enough to be promoted onto a
shortlist — value, nature, context and public interest are four different questions from
"did the ratio cross a threshold," and the spec is explicit that they must not collapse
into one number (Sec 10.6: "the filter is applied transparently, with the reason stated
for each admitted or moderated cluster").

Kept independent from `pipeline/fdr/planning.py` (which applies the identical Sec 10.6
filter on the fs_db path) for the same reason `xbrl_signal_thresholds.py` is independent
of `pipeline/fdr/thresholds.py` — the two FDR paths are fully decoupled by design, so nothing
here imports from there. The four-dimension vocabulary is restated, not borrowed, because
both files are independently reading the same public specification, not because one
depends on the other.

WHAT THIS FILE DOES NOT DO
-------------------------------------------------------------------------------------
It does not decide severity, priority rank, or cluster status (all cluster-level work is
explicitly out of scope for this pass). It answers one question per FIRED signal: is this
admissible onto a shortlist, and on which materiality dimension(s)?
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

# Sec 2.4 / Sec 10.6 materiality dimensions.
VALUE = "value"
NATURE = "nature"
CONTEXT = "context"
PUBLIC_INTEREST = "public_interest"
DIMENSIONS = frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST})


@dataclass(frozen=True)
class MaterialityProfile:
    """One signal's materiality admission rule.

    `admits_on` names every dimension on which this signal CAN be admitted — a signal
    that only ever matters by value never lists NATURE, so a reviewer isn't misled into
    thinking a small-value instance was elevated for a reason it wasn't.

    `by_nature` is the Sec 11.2 override: when True, a FIRED instance of this signal is
    material regardless of how small the underlying ratio's margin over its threshold is
    — an auditor qualification or a related-party concentration does not need to be large
    to warrant audit attention. `by_nature_basis` is mandatory whenever `by_nature=True`.
    """
    signal_id: str
    admits_on: frozenset[str]
    by_nature: bool = False
    by_nature_basis: str = ""

    def __post_init__(self) -> None:
        if not self.admits_on <= DIMENSIONS:
            raise ValueError(f"{self.signal_id}: admits_on has an unknown dimension")
        if not self.admits_on:
            raise ValueError(f"{self.signal_id}: admits_on cannot be empty (Sec 10.6)")
        if self.by_nature and not self.by_nature_basis:
            raise ValueError(
                f"{self.signal_id}: a by-nature override must state its basis (Sec 11.2)"
            )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["admits_on"] = sorted(self.admits_on)
        return d


PROFILES: tuple[MaterialityProfile, ...] = (
    MaterialityProfile("S01", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S02", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S03", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S04", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S05", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S06", frozenset({VALUE})),
    MaterialityProfile("S07", frozenset({VALUE})),
    MaterialityProfile(
        "S08", frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "A related-party or government-counterparty receivable concentration is "
            "material by nature at any value (spec Sec 2.4): the concern is collectability "
            "and arm's-length pricing, not the absolute rupee amount."
        ),
    ),
    MaterialityProfile("S09", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S10", frozenset({VALUE})),
    MaterialityProfile("S11", frozenset({VALUE})),
    MaterialityProfile("S12", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S13", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S14", frozenset({VALUE, CONTEXT})),
    MaterialityProfile("S15", frozenset({VALUE})),
    MaterialityProfile("S16", frozenset({VALUE, NATURE})),
    MaterialityProfile(
        "S17", frozenset({NATURE}),
        by_nature=True,
        by_nature_basis=(
            "A change in useful-life or depreciation estimate is a reporting-quality "
            "matter by its nature (spec Sec 9.3/2.4), regardless of the rupee effect on "
            "the current period's depreciation charge."
        ),
    ),
    MaterialityProfile("S18", frozenset({VALUE})),
    MaterialityProfile("S19", frozenset({VALUE})),
    MaterialityProfile("S20", frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST})),
    MaterialityProfile(
        "S21", frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "A grant used outside its sanctioned conditions or left materially unspent "
            "engages regularity, propriety and public interest directly (spec Sec 2.4/3.3) "
            "at any value, for a Government company or Autonomous Body."
        ),
    ),
    MaterialityProfile("S22", frozenset({CONTEXT})),
    MaterialityProfile("S23", frozenset({VALUE, NATURE, PUBLIC_INTEREST})),
    MaterialityProfile(
        "S24", frozenset({VALUE, NATURE, CONTEXT, PUBLIC_INTEREST}),
        by_nature=True,
        by_nature_basis=(
            "A financial guarantee given for a related or group entity is material by "
            "nature (spec Sec 2.4): it is an off-balance-sheet commitment whose "
            "crystallisation risk is not captured by its face value alone, and it "
            "engages propriety and public interest for a Government company."
        ),
    ),
    MaterialityProfile("S25", frozenset({VALUE})),
    MaterialityProfile("S26", frozenset({VALUE})),
    MaterialityProfile("S27", frozenset({VALUE, CONTEXT})),
)

_BY_ID: dict[str, MaterialityProfile] = {p.signal_id: p for p in PROFILES}
if len(_BY_ID) != len(PROFILES):
    raise RuntimeError("duplicate materiality profile in xbrl_signal_materiality")


def get(signal_id: str) -> MaterialityProfile:
    try:
        return _BY_ID[signal_id]
    except KeyError:
        raise KeyError(f"no materiality profile registered for {signal_id!r}") from None


def is_admissible(signal_id: str, *, fired: bool) -> bool:
    """A signal that did not fire is never admitted onto a shortlist regardless of
    materiality profile — materiality filters FIRED signals only (Sec 10.6)."""
    return fired and signal_id in _BY_ID


def by_nature_basis(signal_id: str) -> str:
    """"" when this signal has no by-nature override — callers should treat a fired
    instance as ordinarily (value/context) material only, never assume an override."""
    p = _BY_ID.get(signal_id)
    return p.by_nature_basis if (p and p.by_nature) else ""
