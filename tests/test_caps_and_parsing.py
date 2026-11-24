from nbaprops.app import value as V


def test_exposure_caps_scale_proportionally():
    cands = [{"player": p, "game": "g", "ev": e, "stake": k} for p, e, k in
             [("A", .14, 1.25), ("A", .11, 1.25), ("B", .07, 1.25), ("C", .06, 1.25), ("D", .02, .5)]]
    out = V.apply_exposure(cands, [], lambda r: "g")
    assert round(sum(c["stake"] for c in out), 2) <= V.GAME_CAP
    assert round(sum(c["stake"] for c in out if c["player"] == "A"), 2) <= V.PLAYER_CAP
    assert all("capped: game" in c["cap"] for c in out)


def test_exposure_counts_logged_stakes():
    logged = [{"player": "A", "kelly_u": "1.5", "event_id": "g"}]
    out = V.apply_exposure([{"player": "A", "game": "g", "ev": .1, "stake": 1.0}], logged, lambda r: r.get("event_id") or r.get("game"))
    assert out[0]["stake"] == 0                       # no room left for player A


def test_parse_placed():
    b = V.parse_placed("Toppin 3PM o1.5 +172 0.75u")
    assert b == {"name": "Toppin", "market": "3PM", "side": "over", "line": 1.5, "odds": 172.0, "stake": 0.75}
    assert V.parse_placed("Karl-Anthony Towns REB u12.5 +108 0.25u")["side"] == "under"


def test_headerless_screener_paste_applies_fixed_settings(monkeypatch):
    from nbaprops.rules import screener as S
    monkeypatch.setattr(S, "known_players", lambda date: ["Trae Young", "Ausar Thompson", "Cooper Flagg"])
    monkeypatch.setattr(S, "fd_main_lines", lambda date: {})
    monkeypatch.setattr(S, "_with_proj", lambda plays, date: plays)
    from nbaprops.rules import tipoff as T
    monkeypatch.setattr(T, "is_open", lambda tip, names=None, now=None: True)
    text = """Books
5.34%    Today at 8:00 PM    Basketball    NBA    Boston Celtics @ Washington Wizards    Player Points + Assists    Trae Young Over 29.5    +440    FanDuel    +413    6
4.14%    Today at 7:00 PM    Basketball    NBA    Detroit Pistons @ Orlando Magic    Player Assists    Ausar Thompson Under 5.5    -128    FanDuel    -141    6
2.10%    Today at 7:00 PM    Basketball    NBA    Detroit Pistons @ Orlando Magic    Player Rebounds    Ausar Thompson Under 7.5    -110    FanDuel    -120    3
0.94%    Today at 10:00 PM    Basketball    NBA    Dallas Mavericks @ Denver Nuggets    Player Assists    Cooper Flagg Under 6.5    -115    FanDuel    -117    14"""
    plays, dropped = S.parse(text, "2026-11-24", -0.01)
    assert [(p["player"], p["market"], p["books"]) for p in plays] == [("Ausar Thompson", "AST", 6), ("Cooper Flagg", "AST", 14)]
    assert dropped == {"not FanDuel": 1, "odds over +200": 1, "under 4 books": 1}   # "Books" header line = not FanDuel
    assert plays[0]["tip"] == "2026-11-25T00:00:00Z" and plays[0]["event"] == "Detroit Pistons @ Orlando Magic"
    assert [p["player"] for p in S.parse(text, "2026-11-24")[0]] == ["Ausar Thompson"]        # +2% floor for the screener record
