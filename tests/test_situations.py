from nbaprops.rules import situations as S


def test_stats_and_candidate_test():
    rows = [{"result": "W", "profit_u": "1.0"}] * 70 + [{"result": "L", "profit_u": "-1.0"}] * 40
    s = S._stats(rows, [1.5] * 110)
    assert (s["n"], s["w"], s["l"], s["units"]) == (110, 70, 40, 30.0)
    assert s["p_value"] < 0.01 and s["clv"] == 1.5
    even = S._stats([{"result": "W", "profit_u": "1"}, {"result": "L", "profit_u": "-1"}] * 50, [])
    assert even["p_value"] == 0.5


def test_buckets():
    assert S._bucket(0.025, [0, 0.01, 0.02, 0.04], ["EV < 0", "0-1%", "1-2%", "2-4%", "4%+"]) == "2-4%"
    assert S._bucket(-180, [-150, -110, 111, 200], ["a", "b", "c", "d", "e"]) == "a"
    assert S._bucket(None, [1], ["x", "y"]) is None
