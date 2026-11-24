import datetime as dt
import json

import pytest

from nbaprops.rules import ledger as L
from nbaprops.rules import tipoff as T
from nbaprops.rules import halftime as H
from nbaprops.rules import screener as S
from nbaprops.pricing import projctx as P
from nbaprops.pricing import calib as CB


def test_rule_version():
    assert L.rule_version({"date": "2026-11-23"}) == "v1"
    assert L.rule_version({"date": "2026-11-24"}) == "v2.1"           # v2.1 (Sam score) starts with v2's first slate
    assert L.in_version({"date": "2026-11-24"}, "v2") and not L.in_version({"date": "2026-11-23"}, "v2")
    assert L.rule_version({"date": "2026-11-20", "rule_version": "v2"}) == "v2"


def test_is_open_outside_the_window_needs_no_network(monkeypatch):
    monkeypatch.setattr(T, "games", lambda date: pytest.fail("ESPN should not be called"))
    assert T.is_open("2026-11-24T00:00:00Z", now="2026-11-23T20:00:00Z") is True
    assert T.is_open("2026-11-24T00:00:00Z", now="2026-11-24T00:10:00Z") is False   # start + 10 min


def test_is_open_inside_the_window_follows_espn(monkeypatch):
    start = dt.datetime(2026, 11, 24, 0, 0, tzinfo=dt.UTC)
    for status, expect in (("STATUS_SCHEDULED", True), ("STATUS_IN_PROGRESS", False)):
        monkeypatch.setattr(T, "games", lambda date, s=status: [{"id": "1", "start": start, "status": s, "abbrs": {"MEM", "CHA"},
                                                                 "names": {"memphis", "charlotte"}}])
        assert T.is_open("2026-11-24T00:00:00Z", "Memphis Grizzlies @ Charlotte Hornets", now="2026-11-24T00:04:00Z") is expect


def test_cutoff_uses_actual_first_play(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "HERE", str(tmp_path))
    T._starts.clear()
    (tmp_path / "history" / "2026-11-22").mkdir(parents=True)
    json.dump({"28134": "2026-11-23T01:08:00Z"}, open(tmp_path / "history" / "2026-11-22" / "actual_starts_2026-11-22.json", "w"))
    assert T.cutoff("2026-11-23T01:00:00Z", "2026-11-22", "28134") == "2026-11-23T01:08:00Z"
    assert T.cutoff("2026-11-23T01:00:00Z", "2026-11-22", "999") == "2026-11-23T01:10:00Z"


def test_halftime_expected_final_shrinks_to_half(monkeypatch):
    monkeypatch.setattr(H, "shares", lambda: {("PTS", "<=25%"): (0.30, 50)})
    exp, share, n = H.expected_final("PTS", 4, 24.5)
    assert n == 50 and share == pytest.approx(0.5 * 0.30 + 0.5 * 0.5)
    assert exp == round(4 + share * 24.5, 1)
    assert H.bucket(0.0) == "<=25%" and H.bucket(1.5) == ">=100%"


def test_dvp_is_side_aware():
    assert P.dvp_effect("over", 3) == "helps" and P.dvp_effect("under", 3) == "hurts"
    assert P.dvp_effect("under", 28) == "helps" and P.dvp_effect("over", 15) == "neutral"
    assert P.dvp_dot("over", 28) == "🔴 28" and P.dvp_dot("under", None) == ""


def test_calibration_switch(monkeypatch):
    monkeypatch.setattr(CB, "factors", lambda: {"P+R": {"factor": 1.05}, "3PM": {"factor": 1.0}})
    monkeypatch.setattr(CB, "USE_CALIBRATION", False)
    assert CB.calibrate("P+R", 20.0) == 20.0 and CB.value("P+R", 20.0) == 21.0
    monkeypatch.setattr(CB, "USE_CALIBRATION", True)
    assert CB.calibrate("P+R", 20.0) == 21.0 and CB.calibrate("PTS", 20.0) == 20.0


def test_screener_helpers():
    assert S.miss_reason({"ev": -0.004, "proj_tag": "lean agrees"}) == "EV short by 0.40%"
    assert S.miss_reason({"ev": 0.01, "proj_tag": "disagrees", "proj_raw": 3.0}) == "BP disagrees (proj 3.0)"
    assert S.miss_reason({"ev": 0.01, "proj_tag": "none"}) == "no BP projection"
    assert S.miss_reason({"ev": 0.01, "proj_tag": "agrees"}) is None
    page = "Devig Method: \nLiquidity-Weighted - Multiplicative\nMinimum Books: \n2\nMaximum Odds: \n+400\n"
    assert S.page_settings(page) == {"devig": "LW-M", "min_books": 2, "max_odds": 400.0}
    assert S.ev_header("LW-M EV%    Calc    Bet Name    Odds    Sportsbook") == "LW-M EV%"
    assert S._when("Today at 8:00 PM", "2026-11-22") == "2026-11-23T01:00:00Z"
