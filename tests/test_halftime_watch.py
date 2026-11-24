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
