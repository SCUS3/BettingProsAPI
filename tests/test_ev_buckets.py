from nbaprops.rules import ledger as L


def test_buckets_use_the_entry_ev_not_a_later_price():
    row = lambda ev, later, res, pu, pk: {"strategy": "my_rule", "entry_ev": str(ev), "oa_ev": str(later), "result": res,
                                          "profit_u": str(pu), "profit_kelly": str(pk), "kelly_u": "0.5"}
    graded = [(row(0.01, 0.05, "W", 1.0, 0.5), 1.0), (row(0.03, -0.01, "L", -1.0, -0.5), None), (row(0.06, 0.0, "W", 0.9, 0.4), 2.0)]
    out = L.ev_buckets(graded)
    assert out[0]["record"] == "Your rule"
    b = {x["bucket"]: x for x in out[0]["buckets"]}
    assert set(b) == {"0-2%", "2-4%", "4%+"}
    assert (b["0-2%"]["w"], b["2-4%"]["l"], b["4%+"]["kelly_units"]) == (1, 1, 0.4)
