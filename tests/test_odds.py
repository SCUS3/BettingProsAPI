import pytest

from nbaprops.pricing import devig as D
from nbaprops.app import value as V
from nbaprops.rules import ledger as L
from nbaprops.grading import grade as G


@pytest.mark.parametrize("american,dec", [(100, 2.0), (150, 2.5), (-200, 1.5), ("-110", 1 + 100 / 110)])
def test_american_to_decimal(american, dec):
    assert D.american_to_decimal(american) == pytest.approx(dec)


def test_implied_and_devig():
    assert D.implied_prob(100) == pytest.approx(0.5)
    assert D.implied_prob(-200) == pytest.approx(2 / 3)
    assert D.devig_over(-110, -110) == pytest.approx(0.5)
    assert D.devig_over(-150, 130) > 0.5


def test_ev_and_kelly():
    assert D.ev(0.5, 110) == pytest.approx(0.05)
    assert D.quarter_kelly_units(0.40, 100, 1.25) == 0.0                 # negative edge floors at 0
    assert D.quarter_kelly_units(0.70, 100, 1.25) == 1.25                # capped
    assert D.quarter_kelly_units(0.52, 100, 5.0) == pytest.approx(1.0)   # f = 0.04 -> quarter = 1%


def test_existing_helpers_delegate():
    assert V.decimal(-200) == D.american_to_decimal(-200)
    assert V.bet_kelly(0.52, 100, 5.0) == D.quarter_kelly_units(0.52, 100, 5.0)
    assert L.decimal(None) is None and L.decimal("150") == 2.5
    assert L.implied("") is None


@pytest.mark.parametrize("side,line,actual,res", [("over", 2.5, 3, "W"), ("over", 2.5, 2, "L"), ("under", 2.5, 2, "W"),
                                                  ("over", 3, 3, "P"), ("over", 2.5, None, None)])
def test_grade(side, line, actual, res):
    assert G.grade(side, line, actual) == res


def test_profit():
    assert G.profit(150, "W") == pytest.approx(1.5)
    assert G.profit(-200, "L", 0.5) == -0.5
    assert G.profit(100, "P") == 0.0
