"""Odds math shared by every module: American <-> decimal, implied probability, multiplicative two-way devig,
EV at a price, and the quarter-Kelly stake. Pure functions, no I/O."""
from __future__ import annotations


def num(v: object) -> float | None:
    """Float from a number or numeric string; None for blank/None/unparseable."""
    try:
        return None if v in (None, "") else float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def american_to_decimal(a: float | int | str) -> float:
    """American odds -> decimal odds (+150 -> 2.5, -200 -> 1.5)."""
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def implied_prob(a: float | int | str) -> float:
    """Implied probability of American odds, vig included (+100 -> 0.5, -200 -> 0.6667)."""
    a = float(a)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def devig_over(over: float, under: float) -> float:
    """Multiplicative (proportional) devig of a two-way market: the over's fair probability."""
    io, iu = implied_prob(over), implied_prob(under)
    return io / (io + iu)


def ev(fair: float, american: float) -> float:
    """Expected value per unit staked at `american` odds when the true probability is `fair`."""
    return fair * american_to_decimal(american) - 1


def quarter_kelly_units(fair: float, american: float, cap: float) -> float:
    """Quarter-Kelly stake in units (1u = 1% of bankroll), floored at 0 and capped, rounded to 0.01u."""
    b = american_to_decimal(american) - 1
    f = (fair * (b + 1) - 1) / b
    return round(min(max(f / 4 * 100, 0.0), cap), 2)
