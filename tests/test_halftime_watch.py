from nbaprops.rules import halftime as H


def test_watch_handles_every_halftime_then_stops(monkeypatch, tmp_path):
    states = iter([
        [("1", "STATUS_IN_PROGRESS", 1), ("2", "STATUS_SCHEDULED", 0)],
        [("1", "STATUS_HALFTIME", 2), ("2", "STATUS_IN_PROGRESS", 1)],     # game 1 at half: alert, keep watching
        [("1", "STATUS_IN_PROGRESS", 3), ("2", "STATUS_HALFTIME", 2)],     # game 2 at half: alert
        [("1", "STATUS_FINAL", 4), ("2", "STATUS_IN_PROGRESS", 3)],        # all past half: stop
    ])
    monkeypatch.setattr(H, "games", lambda date: [{"id": i, "name": f"G{i}", "status": s, "period": p, "score": {}, "margin": 3}
                                                   for i, s, p in next(states)])
    monkeypatch.setattr(H, "pregame_lines", lambda date: {})
    monkeypatch.setattr(H, "snapshot_path", lambda date: str(tmp_path / "none.json"))
    monkeypatch.setattr(H, "off_pace", lambda date, g, lines: [])
    monkeypatch.setattr(H, "save", lambda *a: None)
    alerts = []
    monkeypatch.setattr(H, "notify", alerts.append)
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    H.run("2026-11-24", watch=True)
    assert alerts == ["G1 at halftime: no strong flags", "G2 at halftime: no strong flags"]


def test_end_of_first_quarter_is_not_past_halftime(monkeypatch):
    from nbaprops.rules import halftime as H
    assert H.at_half({"status": "STATUS_END_PERIOD", "period": 2}) and not H.at_half({"status": "STATUS_END_PERIOD", "period": 1})
    games = [[{"id": "1", "name": "DAL @ DEN", "status": "STATUS_END_PERIOD", "period": 1, "score": {}, "margin": 0}],
             [{"id": "1", "name": "DAL @ DEN", "status": "STATUS_FINAL", "period": 4, "score": {}, "margin": 0}]]
    calls = []
    monkeypatch.setattr(H, "games", lambda d: calls.append(1) or games[min(len(calls) - 1, 1)])
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    H.run("2026-11-24", watch=True)
    assert len(calls) == 2                       # kept watching through the end-of-1st break; stopped once final
