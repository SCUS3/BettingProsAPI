from nbaprops.rules import learns as LR


def test_daily_changes_between_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "HIST", str(tmp_path / "h.json"))
    monkeypatch.setattr(LR, "NOTES", str(tmp_path / "n.json"))
    from nbaprops.rules import tonight as TN
    monkeypatch.setattr(TN, "record_rows", lambda version=None: ([], []))
    base = dict(LR.C.SAM_WEIGHTS)
    LR.record("2026-11-24", {"n": 142, "weights": dict(base, dvp=7.8)})
    LR.record("2026-11-25", {"n": 222, "weights": dict(base, dvp=7.85, move=12.8)})
    LR.add_note("2026-11-25", "Cap rose.")
    days = LR.daily()
    assert [d["date"] for d in days] == ["2026-11-25", "2026-11-24"]
    assert [(c["part"], c["old"], c["new"], c["delta"]) for c in days[1]["changes"]] == [("dvp", 10, 7.8, -2.2)]
    assert [c["part"] for c in days[0]["changes"]] == ["move"]                  # dvp moved only 0.05: listed? no (rounds to 0.1)
    assert "Cap rose." in LR.lines(days[0]) and days[1]["first"]
