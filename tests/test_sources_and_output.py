import importlib
import unicodedata

import pytest

from nbaprops.sources import bettingpros as BP
from nbaprops.output import note


def test_run_cap_stops_before_sending(monkeypatch):
    monkeypatch.setattr(BP, "RUN_CAP", 0)
    monkeypatch.setattr(BP, "REQUESTS_MADE", 0)
    monkeypatch.setattr(BP, "_count_request", lambda: pytest.fail("a capped request must not be counted"))
    with pytest.raises(BP.RunCapReached):
        BP.get("/props", {}, "key")


def test_md_table_aligns_columns():
    out = note.md_table([("#", "r"), ("Player", "l"), ("BP", "c")], [["1", "Al Horford", "🟢"], ["10", "Ty", ""]])
    width = lambda t: sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in t)   # emoji = 2 columns
    assert len({width(line) for line in out}) == 1   # header, separator and rows line up on screen
    assert "–" in out[-1]                        # blank cells show an en dash


@pytest.mark.parametrize("shim,module", [("bp_props", "nbaprops.app.props"), ("bp_learn", "nbaprops.rules.ledger"),
                                         ("bp_value", "nbaprops.app.value"), ("bp_unabated", "nbaprops.sources.unabated")])
def test_entry_point_shims_are_the_package_modules(shim, module):
    assert importlib.import_module(shim) is importlib.import_module(module)


def test_note_sections_ordered_most_tailored_first():
    from nbaprops.output.note import order_sections
    src = ["# t", "## Record", "x", "## 1. Top 10 by projection gap", "g", "## 2. Top 10 by your score", "r",
           "## 8. Triple green", "t", "## Points (PTS)", "p"]
    out = order_sections(src)
    assert [ln for ln in out if ln.startswith("## ")] == ["## Record", "## 1. Triple green", "## 2. Your rule: CNO +EV + Projection Model agrees",
                                                         "## 3. Top 10 by projection gap", "## Points (PTS)"]
    assert out[out.index("## 1. Triple green") + 1] == "t"
