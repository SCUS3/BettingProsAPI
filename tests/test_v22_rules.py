from nbaprops.rules import steam as ST
from nbaprops.rules import tonight as TN


def test_sources_by_rule_version():
    old, new = TN.sources_for("2026-11-27"), TN.sources_for("2026-11-28")
    assert "triple" in old and "cno_scr" not in old
    assert "cno_scr" in new and "triple" not in new and {"bet_agree", "alt_mkt", "my_rule"} <= set(new)


def test_sam_gate_waits_for_sample(monkeypatch):
    from nbaprops.pricing import samscore as SS
    monkeypatch.setattr(SS, "graded_n", lambda rows=None: 50)
    assert TN.sam_gate_on("2026-11-27") and not TN.sam_gate_on("2026-11-28")
    monkeypatch.setattr(SS, "graded_n", lambda rows=None: 200)
    assert TN.sam_gate_on("2026-11-28")


def test_steam_needs_ev_and_a_move_toward_the_side():
    base = {"player": "A", "market": "PTS", "side": "over", "line": 20.5, "odds": 110, "books": 6}
    cno = [dict(base, entry_fair=0.48, fair=0.50, ev=0.03),                      # +2 pts toward, +EV -> steam
           dict(base, player="B", entry_fair=0.48, fair=0.485, ev=0.03),          # only +0.5 pt
           dict(base, player="C", entry_fair=0.48, fair=0.52, ev=-0.01),          # moved, but not +EV
           dict(base, player="D", entry_fair=0.52, fair=0.48, ev=0.02)]           # moved against
    got = ST.candidates(cno, [])
    assert [x["player"] for x in got] == ["A"] and got[0]["move"] == 0.02 and got[0]["src"] == "CNO"
    assert ST.log("2026-11-27", cno, []) == []                                   # starts with the 11/28 slate


def test_period_records_split_last_night_week_all(monkeypatch):
    from nbaprops.rules import ledger as L
    from nbaprops.rules import livepastes as LP
    mk = lambda d, res, p: {"date": d, "strategy": "cno_scr", "player": f"P{d}", "market": "PTS", "side": "over", "line": "10.5",
                            "event_id": "", "result": res, "profit_u": str(p), "profit_kelly": "", "kelly_u": "", "tip": ""}
    rows = [mk("2026-11-27", "W", 1.0), mk("2026-11-24", "L", -1.0), mk("2026-11-10", "W", 0.9)]
    monkeypatch.setattr(L, "_read", lambda path: rows)
    monkeypatch.setattr(L, "counted_flags", lambda led: ([True] * len(led), [None] * len(led)))
    monkeypatch.setattr(TN, "record_rows", lambda version=None: ([], []))
    monkeypatch.setattr(LP, "read", lambda date=None: [])
    scr = next(x for x in L.period_records("2026-11-28") if x["label"] == "CNO screener")
    assert (scr["last"]["w"], scr["last"]["l"]) == (1, 0)
    assert (scr["week"]["w"], scr["week"]["l"]) == (1, 1)
    assert (scr["all"]["w"], scr["all"]["l"]) == (1, 1)          # 11/10 is before v2 (11/24)
