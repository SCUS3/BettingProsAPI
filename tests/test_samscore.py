import json

import pytest

from nbaprops.pricing import projctx as P
from nbaprops.pricing import samscore as SS
from nbaprops.rules import ledger as L


@pytest.fixture
def env(monkeypatch):
    st = {"ctx": {"tag": "agrees", "proj": 5.0, "gap": 2.5}, "board": {}, "ua": {}, "cno": {}, "proven": set()}
    monkeypatch.setattr(P, "context", lambda d, pl, m, s, ln: dict(st["ctx"], text=""))
    monkeypatch.setattr(SS, "board_row", lambda d, pl, m: st["board"])
    monkeypatch.setattr(SS, "stores", lambda d: (st["ua"], st["cno"]))
    monkeypatch.setattr(SS, "_proven_today", lambda d, RP: st["proven"])
    monkeypatch.setattr(SS, "active_weights", lambda: SS.C.SAM_WEIGHTS)
    return st


def row(**kw):
    return dict({"date": "2026-11-24", "player": "Jalen Brunson", "market": "3PM", "side": "over", "line": "2.5",
                 "entry_price": "-110", "entry_ev": "0.06", "entry_sources": "8", "cno_ev": "0.01", "strategy": "bet_only"}, **kw)


def test_full_marks_and_parts(env):
    env["board"] = {"dvp_softness": "3", "starter": "True", "lineup": "expected", "tm_out": "1", "form_rate": "0.65",
                    "rec_side": "over", "line": "2.5", "injury": ""}
    env["ua"] = {"Jalen Brunson|3PM|over|2.5": {"ev": 0.05, "exchange": True, "entry_at": "2026-11-24T20:00:00Z", "line": 2.5,
                                                "entry_price": 120, "player": "Jalen Brunson", "market": "3PM", "side": "over"}}
    env["cno"] = {"Jalen Brunson|3PM|over|2.5": {"ev": 0.02, "seen": 2}}
    env["proven"] = {("jalen brunson", "3PM", "over")}
    s = SS.score(row())                                             # price -110 vs first seen +120: moved toward the over
    assert s["parts"] == {"edge": 30, "quality": 10, "bp": 20, "dvp": 10, "move": 10, "role": 10, "form": 5, "consensus": 5, "threes": 0}
    assert s["score"] == 100 and s["tier"] == "A" and s["flags"] == []


def test_levels_and_penalties(env):
    env["board"] = {"dvp_softness": "8", "starter": "False", "lineup": "expected", "tm_out": "0", "form_rate": "0.55",
                    "rec_side": "under", "line": "2.5", "injury": "DTD"}          # our over: form 45% -> 0
    env["ctx"] = {"tag": "lean agrees", "proj": 25.0, "gap": 0.5}
    s = SS.score(row(market="PTS", line="24.5", entry_ev="0.03", entry_sources="4", cno_ev="", entry_price="+210"))
    p = s["parts"]
    assert (p["edge"], p["quality"], p["bp"], p["dvp"], p["move"], p["role"], p["form"]) == (15, 0, 8, 6, 5, 0, 0)
    assert set(s["penalties"]) == {"long_odds", "dtd"} and s["score"] == 34 - 20 and s["tier"] == "paper"


def test_missing_data_is_neutral_and_tagged(env):
    env["ctx"] = {"tag": "none", "proj": None, "gap": None}
    s = SS.score(row(entry_ev="0.06", entry_sources="3", cno_ev=""))               # no board row: no DvP, role, form
    p = s["parts"]
    assert (p["bp"], p["dvp"], p["role"], p["form"]) == (4, 3, 3, 2)
    assert "partial data" in s["flags"] and s["score"] == 30 + 4 + 3 + 5 + 3 + 2


def test_outlier_and_unverified_penalties(env):
    s = SS.score(row(entry_ev="0.15", entry_sources="3"), unverified=True)
    assert {"outlier", "unverified"} <= set(s["penalties"])


def test_movement_directions():
    assert SS.movement("over", (9.5, -110), (10.5, -110)) == "toward"
    assert SS.movement("under", (9.5, -110), (10.5, -110)) == "against"
    assert SS.movement("under", (9.5, -110), (9.5, -130)) == "toward"
    assert SS.movement("over", (9.5, -110), (9.5, -112)) == "none"


def test_score_entry_frozen_at_first_write(env, monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LEDGER", str(tmp_path / "ledger.csv"))
    r = row(odds="-110", oa_ev="0.06", sources="8", pulled_at="2026-11-24T22:00:00Z")
    L._write(L.LEDGER, [r], L.LEDGER_FIELDS)
    first = (r["score_entry"], r["score_parts"])
    assert first[0] not in (None, "") and json.loads(first[1])["edge"] == 30
    env["ctx"] = {"tag": "disagrees", "proj": 1.0, "gap": -1.5}                     # later: BettingPros flips
    rows = L._read(L.LEDGER)
    L._write(L.LEDGER, rows, L.LEDGER_FIELDS)
    again = L._read(L.LEDGER)[0]
    assert (float(again["score_entry"]), again["score_parts"]) == (float(first[0]), first[1])   # never rescored
    assert SS.score(again)["parts"]["bp"] == 0                                                   # the latest score moves


def test_bp_scaled_by_gap_in_sd_for_3pm(env):
    env["ctx"] = {"tag": "agrees", "proj": 4.0, "gap": 1.5}         # SD = 1.1 * sqrt(4) = 2.2 -> 1.5 / 2.2 = 0.68 of 20
    assert SS.score(row())["parts"]["bp"] == 13.6


def test_adapt_shrinks_toward_fit_without_sign_flips(monkeypatch):
    import random
    from nbaprops.pricing import samfit as SF
    random.seed(1)
    rows = []
    for _i in range(300):                                  # DvP hurts results, line movement helps
        x = [random.random() for _ in SS.PARTS]
        z = 1.5 * (x[SS.PARTS.index("move")] - 0.5) - 1.5 * (x[SS.PARTS.index("dvp")] - 0.5)
        y = 1.0 if random.random() < 1 / (1 + 2.718 ** -z) else 0.0
        rows.append({"x": x, "y": y, "w": 1.0, "nv": 0.5, "score": 50, "date": "2026-11-24"})
    monkeypatch.setattr(SF, "dataset", lambda fd_only=True, version=None: rows)
    res = SF.adapt(write=False)
    w = res["weights"]
    assert 0 <= w["dvp"] < SS.C.SAM_WEIGHTS["dvp"] and w["move"] > SS.C.SAM_WEIGHTS["move"]
    assert abs(sum(w.values()) - sum(SS.C.SAM_WEIGHTS.values())) < 1 and 0 < res["s"] < 1


def test_score_label_format():
    assert SS.label(51.2, "C") == "51.2 (C)" and SS.label(51, None) == "51 (C)" and SS.label(44.4) == "44.4 (paper)"
    assert SS.label(None) == "" and SS.label_row({"score_entry": "80", "score_tier": "A"}) == " | Sam 80 (A)"
