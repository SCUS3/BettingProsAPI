from nbaprops.output import site as S


def test_playable_to_is_two_percent_better_than_fair():
    d, am = S.playable_to(0.55)                      # fair 55%: break-even 1.818, +2% -> 1.8545 (-117)
    assert round(d, 4) == round(1.02 / 0.55, 4) and am == "-117"
    assert S.playable_to(0.40)[1] == "+155" and S.playable_to(None) == (None, None)


def test_secret_scan_finds_env_values_and_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", str(tmp_path))
    (tmp_path / ".env").write_text("BP_API_KEY=abcdef1234567890\nSHORT=x\n")
    page = tmp_path / "index.html"
    page.write_text("<p>key abcdef1234567890</p><p>ghp_" + "a" * 36 + "</p><a href='https://drive.google.com/x'>x</a>")
    kinds = {k for _, k in S.scan([str(page)])}
    assert {".env BP_API_KEY", "GitHub token", "Google Drive / Docs link"} <= kinds
    clean = tmp_path / "clean.html"
    clean.write_text("<p>Tonight's bets</p>")
    assert S.scan([str(clean)]) == []
