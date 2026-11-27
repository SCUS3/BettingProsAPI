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
5.34%    Today at 8:00 PM    Basketball    NBA    Boston Celtics @ Washington Wizards    Player Points + Assists    Trae Young Over 29.5    +540    FanDuel    +513    6
4.14%    Today at 7:00 PM    Basketball    NBA    Detroit Pistons @ Orlando Magic    Player Assists    Ausar Thompson Under 5.5    -128    FanDuel    -141    6
2.10%    Today at 7:00 PM    Basketball    NBA    Detroit Pistons @ Orlando Magic    Player Rebounds    Ausar Thompson Under 7.5    -110    FanDuel    -120    3
0.94%    Today at 10:00 PM    Basketball    NBA    Dallas Mavericks @ Denver Nuggets    Player Assists    Cooper Flagg Under 6.5    -115    FanDuel    -117    14"""
    plays, dropped = S.parse(text, "2026-11-24", -0.01)
    assert [(p["player"], p["market"], p["books"]) for p in plays] == [("Ausar Thompson", "AST", 6), ("Cooper Flagg", "AST", 14)]
    assert dropped == {"not FanDuel": 1, "odds over +500": 1, "under 4 books": 1}   # "Books" header line = not FanDuel
    assert plays[0]["tip"] == "2026-11-25T00:00:00Z" and plays[0]["event"] == "Detroit Pistons @ Orlando Magic"
    assert [p["player"] for p in S.parse(text, "2026-11-24")[0]] == ["Ausar Thompson"]        # +2% floor for the screener record


def test_live_line_and_headerless_live_rows():
    from nbaprops.rules import screener as S
    rows = S._loose_table("0.91%            Today at 7:00 PM    Basketball    NBA    Detroit Pistons @ Orlando Magic    Player Points"
                          "    Ausar Thompson Over 9.5    +450    FanDuel    +445    5")
    assert rows[0]["bet"] == "Ausar Thompson Over 9.5" and rows[0]["odds"] == "+450" and rows[0]["books"] == "5"
    p = {"MIN": 18, "PTS": 15, "REB": 3, "AST": 2, "FG": "6-12", "3PT": "1-3", "PF": 0}
    g = {"score": {"ORL": 36, "DET": 48}, "detail": "Halftime"}
    ln = S.live_line(p, g, "AST", "over", 5.5, 4.0, "lean disagrees", 26)
    assert "BP pregame proj 🔴 4.0" in ln and "DvP 🔴 26" in ln
    assert "LIVE AST 2 vs o5.5 (needs 4 more) in 18 min" in ln and "FG 6-12" in ln and "Halftime" in ln
    assert "OVER CASHED" in S.live_line(p, g, "PTS", "over", 14.5) and "UNDER LOST" in S.live_line(p, g, "PTS", "under", 14.5)


def test_cheat_sheet_page_parse():
    from nbaprops.sources import bp_page as BPP
    text = """Brandin PodziemskiLogo for GSW
Brandin Podziemski
G
- SAC @ GSW
24.5
Pts + Ast + Reb
28.0
Over
64.4%
3 out of 5 stars
+9%
23rd
40%	47%
O 24.5
(-144)
Logo for Polymarket US
Mikal BridgesLogo for NYK
Mikal Bridges
F
- CLE @ NYK
3.5
Ast
2.0
Under
Click to view premium content
25th
U 3.5
(-245)
Logo for FanDuel"""
    rows = BPP.parse(text)
    assert [(r["player"], r["market"], r["line"], r["bp_proj"], r["bp_rec_side"], r["dvp_softness"], r["price_book"]) for r in rows] == [
        ("Brandin Podziemski", "PRA", 24.5, 28.0, "over", 8, "Polymarket US"), ("Mikal Bridges", "AST", 3.5, 2.0, "under", 6, "FanDuel")]


def test_espn_form_fields():
    from nbaprops.sources import espn_form as EF
    games = [{"PTS": p, "REB": 5, "AST": 2, "3PM": 1} for p in (20, 12, 25, 9, 18, 30, 11, 14, 22, 8, 16)]   # newest first
    f = EF.form_fields(games, "PTS", "over", 15.5)
    assert f == {"form_l5": "3-5", "form_l10": "5-10", "form_l20": "6-11", "form_season": "6-11"}
    assert EF.form_fields(games, "P+A", "under", 17.5)["form_l5"] == "2-5"                # 22,14,27,11,20 -> 14 and 11 under


def test_cheat_sheet_page_keeps_day_to_day_players():
    from nbaprops.sources import bp_page as BPP
    text = "\n".join(["Giannis AntetokounmpoLogo for MIA", "Giannis Antetokounmpo", "DTDInjury status: Day-to-day", "F",
                      "- MIA @ NYK", "11.5", "Reb", "11.0", "Under", "3rd", "U 11.5", "(-117)", "Logo for Polymarket US",
                      "Jalen BrunsonLogo for NYK", "Jalen Brunson", "G", "- MIA @ NYK", "28.5", "Pts", "27.0", "Under",
                      "26th", "U 28.5", "(-141)", "Logo for Novig"])
    rows = BPP.parse(text)
    assert [(r["player"], r["market"], r["bp_proj"], r["page_dtd"]) for r in rows] == [
        ("Giannis Antetokounmpo", "REB", 11.0, True), ("Jalen Brunson", "PTS", 27.0, False)]
    assert rows[0]["opp_rank_raw"] == 3 and rows[0]["price_book"] == "Polymarket US"
