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
