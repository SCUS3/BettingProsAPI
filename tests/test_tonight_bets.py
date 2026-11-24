from nbaprops.rules import ledger as L
from nbaprops.rules import tonight


def _row(strat, player, mk, stake, ev="0.05", game="g1", line="10.5", side="over"):
    return {"date": "2026-11-24", "strategy": strat, "player": player, "market": mk, "side": side, "line": line,
            "kelly_u": str(stake), "entry_ev": ev, "entry_price": "-110", "odds": "-110", "event_id": game, "tip": "", "cno_flag": ""}


def test_union_largest_stake_not_summed_and_global_caps(monkeypatch):
    rows = [_row("bet_agree", "A", "PTS", 1.0), _row("my_rule", "A", "PTS", 0.6),          # same prop: one row at 1.0u
            _row("alt_mkt", "A", "REB", 0.5, line="7.5"),                                    # A total 1.5u: at the player cap
            _row("my_rule", "A", "AST", 0.4, line="5.5"),                                    # pushes A to 1.9u: capped to 1.5u
            _row("bet_only", "B", "PTS", 1.25), _row("my_rule", "C", "PTS", 1.25), _row("alt_proj", "D", "3PM", 0.5),
            _row("cno", "E", "PTS", 1.0), _row("ua_agree", "F", "PTS", 1.0)]                 # paper records: not in the list
    monkeypatch.setattr(L, "_read", lambda path: rows)
    tb = tonight.build("2026-11-24")
    a_pts = next(x for x in tb if x["player"] == "A" and x["market"] == "PTS")
    assert a_pts["records"] == ["BET", "Your rule"] and a_pts["stake_raw"] == 1.0
    assert {x["player"] for x in tb} == {"A", "B", "C", "D"}
    assert round(sum(x["stake"] for x in tb if x["player"] == "A"), 2) <= 1.5
    assert round(sum(x["stake"] for x in tb), 2) <= 3.5                                     # one game: game cap across records


def test_record_uses_capped_stake_and_one_result_per_prop(monkeypatch):
    rows = [dict(_row("bet_agree", "A", "PTS", 1.0), result="W", profit_u="0.91", profit_kelly="0.91"),
            dict(_row("my_rule", "A", "PTS", 0.6), result="W", profit_u="0.91", profit_kelly="0.55"),
            dict(_row("bet_only", "A", "REB", 1.0, line="7.5"), result="L", profit_u="-1", profit_kelly="-1")]
    monkeypatch.setattr(L, "_read", lambda path: rows)
    monkeypatch.setattr(L, "counted_flags", lambda led: ([True] * len(led), [1.0] * len(led)))
    tr, clvs = tonight.record_rows()
    assert len(tr) == 2                                                   # A PTS once (two records), A REB once
    s = L._summ(tr, clvs)
    assert (s["w"], s["l"]) == (1, 1)
    assert round(sum(r["kelly_u"] for r in tr), 2) == 1.5                 # 2.0u wanted, capped at 1.5u per player
    assert s["kelly_units"] == round(0.91 * 0.75 - 0.75, 2)
