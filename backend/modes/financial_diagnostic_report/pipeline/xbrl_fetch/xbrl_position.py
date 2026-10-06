"""
The single place that decides what equity, borrowings, D/E and the equity multiplier
MEAN for a filing - including when equity is negative, nil or missing.

WHY THIS MODULE EXISTS
----------------------
Each report block used to re-derive these figures with its own idiom, and the idioms
disagreed exactly where it matters (negative net worth):

  * DuPont forced the equity multiplier to 0.00x when equity <= 0, so ROE printed as
    0.00% and the decomposition table was arithmetically meaningless;
  * the business profile stored a -1.0 sentinel for D/E, which then reached the LLM
    prompt as "Raw: -1.00 ratio";
  * the risk engine set D/E to None, so its leverage signal (D/E > 1.0x) could never
    fire, and RC04 reported "conservative leverage" on a filing with large borrowings.

THE RULE
--------
An undefined ratio is `None` plus an explicit `status` - never 0.0, never -1.0 -
and a missing input is `None`, never a silent 0. Callers branch on `status`, so a
negative or absent equity is a stated condition with its own wording, not a number
that happens to look like a value.
"""
from __future__ import annotations
from dataclasses import dataclass

OK = "ok"
NEGATIVE_NET_WORTH = "negative_net_worth"
NIL_EQUITY = "nil_equity"
MISSING = "missing"

NOT_MEANINGFUL = "n/m"


@dataclass(frozen=True)
class Leverage:
    borrowings: float | None
    equity: float | None
    de_ratio: float | None   # only when status == OK
    status: str

    @property
    def has_borrowings(self) -> bool:
        return bool(self.borrowings and self.borrowings > 0)

    @property
    def impaired_equity(self) -> bool:
        """Equity is negative or nil, so equity-based ratios are undefined."""
        return self.status in (NEGATIVE_NET_WORTH, NIL_EQUITY)

    def de_display(self) -> str:
        if self.status == OK and self.de_ratio is not None:
            return f"{self.de_ratio:.2f}x"
        if self.status == NEGATIVE_NET_WORTH:
            return "n/m (negative net worth)"
        if self.status == NIL_EQUITY:
            return "n/m (nil equity)"
        return "n/a"


def classify_leverage(equity: float | None, borrowings: float | None) -> Leverage:
    """D/E and its status from the filing's own equity and borrowings. `None` means
    the filing did not report the figure; it is never defaulted to zero here."""
    if equity is None or borrowings is None:
        return Leverage(borrowings, equity, None, MISSING)
    if equity < 0:
        return Leverage(borrowings, equity, None, NEGATIVE_NET_WORTH)
    if equity == 0:
        return Leverage(borrowings, equity, None, NIL_EQUITY)
    return Leverage(borrowings, equity, borrowings / equity, OK)


def equity_multiplier(assets: float | None, equity: float | None) -> float | None:
    """Assets / equity, SIGNED. A negative multiplier is a true statement about a
    negative-net-worth balance sheet and is reported as such; only a nil/absent
    denominator makes it undefined (None)."""
    if assets is None or equity is None or equity == 0:
        return None
    return assets / equity


def fmt_multiplier(value: float | None) -> str:
    return f"{value:.2f}×" if value is not None else NOT_MEANINGFUL
