from nbaprops.rules import livepastes as LP


def test_paste_time_and_clv(monkeypatch, tmp_path):
    assert LP._paste_time("2026-11-24", "x/cno_paste_011936.txt") == "2026-11-25T01:19:36Z"      # after midnight UTC
    assert LP._paste_time("2026-11-24", "x/cno_paste_234253.txt") == "2026-11-24T23:42:53Z"
    monkeypatch.setattr(LP, "PATH", str(tmp_path / "live.csv"))
    base = {"player": "Kyrie Irving", "market": "PTS", "side": "over", "line": 19.5, "books": 8}
    LP.add("2026-11-24", [dict(base, odds="+310", ev="18.2", fair="+247")], "2026-11-25T04:07:00Z")
    LP.add("2026-11-24", [dict(base, odds="+430", ev="15.6", fair="+358")], "2026-11-25T04:13:00Z")
    from nbaprops.sources import espn as BX
    monkeypatch.setattr(BX, "finals", lambda d, log=None: {"x": 1})
    monkeypatch.setattr(BX, "lookup", lambda box, p: {"PTS": 11})
    monkeypatch.setattr(BX, "stat", lambda p, m: 11)
    assert LP.grade("2026-11-24") == (2, 0)
    rows = LP.read("2026-11-24")
    first = next(r for r in rows if r["odds"] == "+310")
    assert first["result"] == "L" and float(first["clv_ev"]) < 0       # next paste's fair (+358) says +310 was a worse price
    assert LP.summary()["n"] == 2
