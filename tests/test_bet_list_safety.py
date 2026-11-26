import datetime as dt

import pytest

from nbaprops.rules import conflicts as CF
from nbaprops.rules import ledger as L
from nbaprops.rules import tonight as TN

TIP = "2026-11-25T03:00:00Z"
NOW = dt.datetime(2026, 11, 25, 0, 0, tzinfo=dt.UTC)          # 3 h before tip


def row(strat, player, side, line, stake, ev="0.05", src="6", mk="PTS", game="g1", at="2026-11-24T23:58:00Z", **kw):
    return dict({"date": "2026-11-24", "strategy": strat, "player": player, "market": mk, "side": side, "line": str(line),
                 "kelly_u": str(stake), "entry_ev": ev, "entry_price": "-110", "odds": "-110", "event_id": game, "tip": TIP,
                 "cno_flag": "", "entry_sources": src, "entry_at": at, "proj_tag": "agrees"}, **kw)


SCORES: dict = {}


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    from nbaprops.pricing import samscore as SS
    SCORES.clear()
    monkeypatch.setattr(SS, "score", lambda r, **kw: {"score": SCORES.get(r["player"], 85.0), "tier": SS.tier(SCORES.get(r["player"], 85.0)),
                                                       "parts": {}, "flags": [], "penalties": [], "fractions": {}, "move": "none"})
    monkeypatch.setattr(SS, "stores", lambda date: ({}, {}))
    rows = []
    monkeypatch.setattr(L, "_read", lambda path: rows)
    monkeypatch.setattr(L, "HERE", str(tmp_path))           # conflicts file in tmp
    return rows


# 1. both sides of one line
def test_both_sides_held_until_repriced_then_one_side_kept(ledger, monkeypatch):
    ledger += [row("alt_mkt", "Tyson", "over", 9.5, 0.5, ev="0.06"), row("bet_only", "Tyson", "under", 9.5, 1.0, ev="0.05")]
    tb = TN.build("2026-11-24", now=NOW)
    assert all(x["stake"] == 0 and "re-checking" in x["held"] for x in tb)          # never both listed
    snap = {"over": {"ev": -0.04, "price": 120, "fair": 0.43, "books": 5, "at": "t"},
            "under": {"ev": 0.03, "price": -118, "fair": 0.57, "books": 5, "at": "t"}}
    monkeypatch.setattr(CF, "reprice_snapshot", lambda *a: snap)
    assert CF.reprice("2026-11-24", {}, {}, "2026-11-25T00:01:00Z") == [("Tyson|PTS|9.5", "re-checked")]
    tb = TN.build("2026-11-24", now=NOW)
    live = [x for x in tb if x["stake"] > 0]
    assert [(x["side"], x["case"]) for x in live] == [("under", "re-checked")]


@pytest.mark.parametrize("snap", [None, {"over": {"ev": 0.02}, "under": {"ev": 0.01}}])
def test_both_still_positive_or_reprice_failed_is_data_error(ledger, monkeypatch, snap):
    ledger += [row("alt_mkt", "T", "over", 9.5, 0.5), row("bet_only", "T", "under", 9.5, 1.0)]
    monkeypatch.setattr(CF, "reprice_snapshot", lambda *a: snap)
    assert CF.reprice("2026-11-24", {}, {}, "2026-11-25T00:01:00Z")[0][1] == "data error"
    assert all(x["stake"] == 0 and "data error" in x["held"] for x in TN.build("2026-11-24", now=NOW))


def test_unverified_near_tip_keeps_stronger_side_at_half_stake(ledger):
    ledger += [row("alt_mkt", "T", "over", 9.5, 0.8, ev="0.04", proj_tag="disagrees"),
               row("bet_only", "T", "under", 9.5, 1.0, ev="0.05", dvp_tag="helps")]
    near = dt.datetime(2026, 11, 25, 2, 55, tzinfo=dt.UTC)     # 5 min before tip, never re-priced
    live = [x for x in TN.build("2026-11-24", now=near) if x["stake"] > 0]
    assert [(x["side"], x["case"], x["stake"]) for x in live] == [("under", "unverified", 0.5)]


def test_contradictory_rungs_flagged():
    both, contra = CF.find([{"player": "Champagnie", "market": "REB", "side": "under", "line": 5.5},
                            {"player": "Champagnie", "market": "REB", "side": "over", "line": 7.5},
                            {"player": "Champagnie", "market": "REB", "side": "over", "line": 4.5}])   # o4.5/u5.5 can both win
    assert both == [] and [(o["line"], u["line"]) for o, u in contra] == [(7.5, 5.5)]


# 3. sanity gate
def test_outlier_ev_needs_five_sources_and_fresh_price(ledger):
    ledger += [row("bet_only", "Thin", "under", 9.5, 1.0, ev="0.106", src="3"),                         # 3 sources
               row("bet_only", "Stale", "over", 5.5, 1.0, ev="0.15", src="6", at="2026-11-24T23:00:00Z", game="g2"),  # 1 h old
               row("bet_only", "Fresh", "over", 6.5, 1.0, ev="0.15", src="6", game="g3"),               # 2 min old, 6 sources
               row("bet_only", "Normal", "over", 7.5, 1.0, ev="0.04", src="3", game="g4")]
    tb = {x["player"]: x for x in TN.build("2026-11-24", now=NOW)}
    assert tb["Thin"]["stake"] == 0 and tb["Thin"]["held"].startswith("check: outlier EV")
    assert tb["Stale"]["stake"] == 0 and tb["Fresh"]["stake"] > 0 and tb["Normal"]["stake"] > 0


# 4. nightly cap
def test_nightly_cap_trims_lowest_scores_first(ledger):
    ledger += [row("bet_only", f"P{i}", "over", 5.5, 1.25, game=f"g{i}") for i in range(10)]   # 12.5u wanted
    SCORES.update({"P0": 52, "P1": 55, "P2": 60, "P3": 66, "P4": 70})                         # C, C, C, B, B; rest A (85)
    tb = TN.build("2026-11-24", now=NOW)
    tot, pre, capped = TN.totals(tb)
    assert capped and pre == 12.5 and tot == 8.0
    st = {x["player"]: x["stake"] for x in tb}
    assert st["P0"] == st["P1"] == st["P2"] == 0 and st["P3"] == 0.5 and st["P4"] == 1.25   # 4.5u cut: C first, then B
    assert all(st[f"P{i}"] == 1.25 for i in range(5, 10))                                    # A untouched
    assert [x["player"] for x in tb if x["stake"] > 0][:1] == ["P5"]                         # sorted by score


def test_nightly_cap_scales_on_archive_slates(ledger):
    ledger += [dict(row("bet_only", f"P{i}", "over", 5.5, 1.25, game=f"g{i}"), date="2026-11-22") for i in range(10)]
    tb = TN.build("2026-11-22", now=NOW)
    tot, pre, capped = TN.totals(tb)
    assert capped and tot <= 8.0 and len({x["stake"] for x in tb}) == 1                   # v2: same proportion for all


def test_score_under_50_is_paper_only(ledger):
    ledger += [row("bet_only", "Low", "over", 5.5, 1.0), row("bet_only", "Hi", "over", 6.5, 1.0, game="g2")]
    SCORES.update({"Low": 49.9})
    tb = {x["player"]: x for x in TN.build("2026-11-24", now=NOW)}
    assert tb["Low"]["stake"] == 0 and tb["Low"]["held"].startswith("paper only") and tb["Hi"]["stake"] == 1.0


def test_past_night_keeps_the_score_it_was_decided_on(ledger, monkeypatch):
    from nbaprops.app import props as P
    ledger += [dict(row("bet_only", "W", "over", 6.5, 1.0), date="2026-11-24", score_entry="53")]
    SCORES.update({"W": 70.0})                                          # today's weights would now call it a B
    monkeypatch.setattr(P, "eastern_today", lambda: "2026-11-26")
    TN.save_decisions("2026-11-24", {"W|PTS|over|6.5": 49.0})          # at tip it scored 49: held out
    tb = TN.build("2026-11-24", now=NOW)
    assert tb[0]["stake"] == 0 and tb[0]["held"].startswith("paper only: Sam score 49")
