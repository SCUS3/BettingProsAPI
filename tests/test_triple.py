from nbaprops.pricing import projctx as P
from nbaprops.rules import ledger as L
from nbaprops.rules import triple as TG


def _ctx(tags, dvps, monkeypatch):
    monkeypatch.setattr(P, "context", lambda d, pl, m, s, ln: {"tag": tags.get(pl, "none"), "text": "", "proj": 20.0, "gap": 1.0})
    monkeypatch.setattr(P, "dvp_of", lambda d, pl, m: dvps.get(pl))


def test_all_three_needed_and_source_choice(monkeypatch):
    _ctx({"A": "agrees", "B": "agrees", "C": "disagrees", "D": "lean agrees", "E": "agrees"},
         {"A": 5, "B": 15, "C": 3, "D": 27, "E": None}, monkeypatch)
    cno = [{"player": p, "market": "PTS", "side": s, "line": 19.5, "ev": 0.01, "fair": 0.53, "odds": -110, "books": 5}
           for p, s in (("A", "over"), ("B", "over"), ("C", "over"), ("E", "over"))]
    ua = [{"player": "A", "market": "PTS", "side": "over", "line": 19.5, "ev": 0.03, "fair": 0.54, "price": -110, "books": 4},
          {"player": "D", "market": "PTS", "side": "under", "line": 19.5, "ev": 0.02, "fair": 0.53, "price": -105, "books": 3},
          {"player": "D", "market": "PTS", "side": "over", "line": 19.5, "ev": 0.02, "fair": 0.53, "price": -105, "books": 2}]
    got = {x["player"]: x for x in TG.candidates("2026-11-24", cno, ua)}
    assert set(got) == {"A", "D"}              # B DvP neutral, C BP disagrees, E no DvP
    assert (got["A"]["src"], got["A"]["from"], got["A"]["ev"]) == ("both", "Unabated", 0.03)   # higher EV source
    assert (got["D"]["src"], got["D"]["side"]) == ("Unabated", "under")                       # 27 helps an under


def test_compare_rows_split_your_rule_by_dvp(monkeypatch):
    g = lambda strat, dvp_tag, res, pu: ({"strategy": strat, "dvp_tag": dvp_tag, "result": res, "profit_u": pu, "profit_kelly": pu,
                                         "kelly_u": "0.5", "entry_ev": "0.03", "fair_src": "CNO", "date": "2026-11-24",
                                         "player": "x", "market": "PTS", "side": "over"}, 1.0)
    rows = L.triple_rows([g("triple", "helps", "W", "0.9"), g("my_rule", "helps", "W", "0.9"), g("my_rule", "hurts", "L", "-1")])
    by = {x["key"]: x for x in rows}
    assert (by["cmp_triple"]["w"], by["cmp_rule"]["n"], by["cmp_rule_nodvp"]["l"]) == (1, 2, 1)
    assert by["triple_src_CNO"]["n"] == 1 and by["triple_ev_2-4%"]["n"] == 1


def test_two_of_three_any_two_checks(monkeypatch):
    from nbaprops.rules import twothree as T23
    _ctx({"A": "agrees", "B": "agrees", "C": "disagrees", "D": "disagrees"}, {"A": 15, "B": 5, "C": 25, "D": 15}, monkeypatch)
    ua = [{"player": p, "market": "PTS", "side": s, "line": 19.5, "ev": ev, "fair": 0.5, "price": -110, "books": 4}
          for p, s, ev in (("A", "over", 0.01), ("B", "over", -0.03), ("C", "under", 0.02), ("D", "over", 0.05))]
    got = {x["player"]: x["which"] for x in T23.candidates("2026-11-24", [], ua)}
    assert got == {"A": "EV+BP", "B": "BP+DvP", "C": "EV+DvP"}            # D: only +EV (1 of 3) -> out
