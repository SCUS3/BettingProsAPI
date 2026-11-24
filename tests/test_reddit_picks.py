from nbaprops.rules import reddit_picks as RP


def test_parse_comments_resolves_tonight_players_and_counts_images(monkeypatch):
    monkeypatch.setattr(RP, "tonight_players", lambda date: {
        "jalen brunson": {"name": "Jalen Brunson", "tip": "2026-11-25T00:30:00Z"},
        "karlanthony towns": {"name": "Karl-Anthony Towns", "tip": "2026-11-25T00:30:00Z"}})
    comments = [
        {"id": "a", "author": "sharp1", "created_utc": 1795550000, "body": "POTD: Brunson Over 27.5 Points -115\nKAT REB o10.5"},
        {"id": "b", "author": "pics", "created_utc": 1795550000, "body": "https://i.redd.it/abc123.png"},
        {"id": "c", "author": "talk", "created_utc": 1795550000, "body": "tailing, good luck all"},
        {"id": "d", "author": "x", "created_utc": 1795550000, "body": "Wembanyama PTS o25.5"}]
    picks, counts = RP.parse_comments(comments, "2026-11-24")
    assert [(p["author"], p["player"], p["market"], p["side"], p["line"]) for p in picks] == [
        ("sharp1", "Jalen Brunson", "PTS", "over", 27.5), ("sharp1", "Karl-Anthony Towns", "REB", "over", 10.5)]
    assert counts["image_only"] == 1 and counts["unresolved"] == 1 and counts["with_props"] == 2


def test_proven_needs_volume_clv_and_units(monkeypatch):
    rows = ([{"strategy": "reddit_pick", "fair_src": "Reddit u/a", "result": "W", "profit_u": "0.91", "date": "2026-11-24",
              "entry_ev": "0.02"}] * 20 + [{"strategy": "reddit_pick", "fair_src": "Reddit u/a", "result": "L", "profit_u": "-1",
                                            "date": "2026-11-24", "entry_ev": "0.01"}] * 12)
    from nbaprops.rules import ledger as L
    monkeypatch.setattr(L, "_read", lambda path: rows)
    monkeypatch.setattr(L, "counted_flags", lambda led: ([True] * len(led), [0.5] * len(led)))
    s = RP.poster_stats()[0]
    assert (s["n"], s["proven"]) == (32, True)
    monkeypatch.setattr(L, "counted_flags", lambda led: ([True] * len(led), [-0.5] * len(led)))
    assert RP.poster_stats()[0]["proven"] is False                      # negative CLV: not proven
