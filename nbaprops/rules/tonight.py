"""Tonight's bets: the only list meant to be bet. The union of BETs (Unabated EV >= +2% and CrazyNinja worst case > 0;
a BET logged while CNO was down is kept and flagged), alt BETs (alt ladder) and Your-rule props (CNO +EV and BettingPros
agrees). Each prop (player, market, side, line) is listed once with the LARGEST qualifying ¼K stake (never summed), then
the 1.5u-per-player and 3.5u-per-game caps are applied across the whole list. Every other record stays a paper record.
"""
from __future__ import annotations

from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm

SOURCES = {"bet_agree": "BET", "bet_only": "BET", "alt_mkt": "Alt BET", "alt_proj": "Alt BET", "my_rule": "Your rule"}


def build(date: str) -> list[dict]:
    """-> [{player, team, market, side, line, price, ev, stake, stake_raw, records, cap, tip, event_id, flags, rows}],
    biggest stake first. Reads the ledger only (no API calls)."""
    from nbaprops.app import value as V
    rows = [r for r in L._read(L.LEDGER) if r["date"] == date and r["strategy"] in SOURCES and (L._num(r.get("kelly_u")) or 0) > 0]
    groups: dict = {}
    for r in rows:
        groups.setdefault((norm(r["player"]), r["market"], r["side"], L._num(r["line"])), []).append(r)
    key = V.game_resolver(rows)
    out = []
    for g in groups.values():
        best = max(g, key=lambda r: L._num(r["kelly_u"]))
        recs = sorted({SOURCES[r["strategy"]] for r in g}, key=["BET", "Alt BET", "Your rule"].index)
        ev = L._num(best.get("entry_ev") or best.get("oa_ev"))
        out.append({"player": best["player"], "team": best.get("team"), "market": best["market"], "side": best["side"],
                    "line": L._num(best["line"]), "price": L._num(best.get("entry_price") or best.get("odds")), "ev": ev,
                    "stake_raw": L._num(best["kelly_u"]), "stake": L._num(best["kelly_u"]), "records": recs,
                    "from": SOURCES[best["strategy"]], "tip": best.get("tip"), "event_id": best.get("event_id"),
                    "game": key(best), "flags": "no CNO check" if any(r.get("cno_flag") for r in g if r["strategy"] in ("bet_agree", "bet_only")) else "",
                    "rows": g})
    cands = [{"player": x["player"], "game": x["game"], "ev": x["ev"] or 0, "stake": x["stake"]} for x in out]
    V.apply_exposure(cands, [], lambda r: r["game"])
    for x, c in zip(out, cands):
        x["stake"], x["cap"] = round(c["stake"], 2), c["cap"]
    out = [x for x in out if x["stake"] > 0]
    return sorted(out, key=lambda x: (-x["stake"], -(x["ev"] or 0)))


def payload(date: str) -> list[dict]:
    """JSON-safe rows for the dashboard (with game status)."""
    from nbaprops.rules import tipoff as T
    res = []
    for x in build(date):
        y = {k: v for k, v in x.items() if k != "rows"}
        try:
            y["status"] = "open" if not x.get("tip") or T.is_open(x["tip"], [x.get("team")] if x.get("team") else None) else "started"
        except Exception:  # noqa: BLE001
            y["status"] = ""
        res.append(y)
    return res
