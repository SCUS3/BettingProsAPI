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
