"""Tonight's bug: a scan after the last tip rewrote the Unabated EV file with nothing, and closes were overwritten
by later scans. These pin the fixes."""
import json

from nbaprops.app import value as V
from nbaprops.rules import ledger as L
from nbaprops.rules import tipoff as T


def _ledger(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(L, "LEDGER", str(tmp_path / "ledger.csv"))
    L._write(L.LEDGER, rows, L.LEDGER_FIELDS)
    return L._read(L.LEDGER)


def test_watch_stops_after_the_last_close(monkeypatch):
    monkeypatch.setattr(V, "slate_lines", lambda data, date, d=None: [{"tip": "2026-11-24T03:00:00Z"}])
    monkeypatch.setattr(L, "_read", lambda path: [])
    tip = T._t("2026-11-24T03:00:00Z")
    assert V.tips_to_close("2026-11-23", {}, {}, tip) == ["2026-11-24T03:00:00Z"]           # at tip: close still due
    assert V.tips_to_close("2026-11-23", {}, {}, tip + T.CLOSE_AFTER) == []                  # at tip + 5: nothing left


def test_entry_snapshot_is_frozen(tmp_path, monkeypatch):
    led = _ledger(tmp_path, monkeypatch, [{"date": "2026-11-24", "strategy": "bet_only", "player": "A", "market": "PTS",
                                           "side": "over", "line": "10.5", "odds": "110", "oa_ev": "0.03", "oa_fair": "0.49",
                                           "pulled_at": "2026-11-24T22:00:00Z", "sources": "5"}])
    assert led[0]["entry_price"] == "110" and led[0]["entry_ev"] == "0.03"
    led[0]["odds"], led[0]["oa_ev"] = "-120", "0.0"                                          # a later change elsewhere
    L._write(L.LEDGER, led, L.LEDGER_FIELDS)
    again = L._read(L.LEDGER)[0]
    assert again["entry_price"] == "110" and again["entry_ev"] == "0.03"                     # entry never changes


def test_close_is_a_one_time_copy_of_the_last_pregame_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "HERE", str(tmp_path))
    led = _ledger(tmp_path, monkeypatch, [{"date": "2026-11-24", "strategy": "bet_only", "player": "A", "market": "PTS",
                                           "side": "over", "line": "10.5", "odds": "110", "tip": "2026-11-25T00:00:00Z"}])
    res = {("A", "PTS", "over"): {"fair_mult": 0.5, "ev_mult": 0.04, "oa_fd_line": 10.5, "oa_fd_price": 108}}
    V.record_latest(led, led, res, "2026-11-24T23:55:00Z")
    V.finalize_close("2026-11-24", led, led)
    r = L._read(L.LEDGER)[0]
    assert r["close_at"] == "2026-11-24T23:55:00Z" and float(r["clv_ev"]) == round(0.5 * 2.1 - 1, 4)
    led = L._read(L.LEDGER)
    led[0]["latest_fair"], led[0]["latest_at"] = "0.40", "2026-11-25T00:20:00Z"             # anything later is ignored
    V.finalize_close("2026-11-24", led, led)
    assert L._read(L.LEDGER)[0]["close_at"] == "2026-11-24T23:55:00Z"


def test_store_never_touches_a_started_game(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "HERE", str(tmp_path))
    monkeypatch.setattr(V.bp_learn, "LEDGER", str(tmp_path / "ledger.csv"))      # never the real ledger (All +EV writes)
    started = {"player": "A", "market": "PTS", "line": 10.5, "tip": "2026-11-24T00:00:00Z", "team": "X"}
    upcoming = {"player": "B", "market": "REB", "line": 6.5, "tip": "2026-11-24T03:00:00Z", "team": "Y"}
    ua = {("A", "PTS", "over"): {"ev_mult": 0.03, "fair_mult": 0.5, "price_used": 110, "books": 4},
          ("B", "REB", "over"): {"ev_mult": 0.02, "fair_mult": 0.5, "price_used": 105, "books": 5}}
    monkeypatch.setattr(V, "_open", lambda r, now=None: True)
    V.save_ua_store("2026-11-23", [started, upcoming], ua)                                    # pregame: both saved
    monkeypatch.setattr(V, "_open", lambda r, now=None: r["player"] == "B")                   # A has tipped
    ua[("A", "PTS", "over")] = {"ev_mult": -0.5, "fair_mult": 0.1, "price_used": -900, "books": 4}
    V.save_ua_store("2026-11-23", [upcoming], {k: v for k, v in ua.items() if k[0] == "B"})   # scan after A's tip
    store = json.load(open(V.ua_store_path("2026-11-23")))
    assert store["A|PTS|over|10.5"]["ev"] == 0.03                                            # kept, not overwritten
    assert store["A|PTS|over|10.5"]["entry_price"] == 110 and len(store) == 2
