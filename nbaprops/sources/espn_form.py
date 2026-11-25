"""Recent form from ESPN's free public game logs (no BettingPros requests): per player, this season's regular-season
games (newest first) with PTS, REB, AST and 3PM; per prop, hit counts of the listed side vs. the line over the last 5,
10, 20 games and the season, as "hits-games" strings (form_l5, form_l10, form_l20, form_season) -- the same fields the
API pull fills, so form_score / the Sam score read them unchanged. Cached per day in history/<date>/espn_form_<date>.json.
"""
from __future__ import annotations

import json
import os
import time

from nbaprops.sources import espn as BX

ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{t}/roster"
GAMELOG = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{pid}/gamelog"
TO_ESPN = {"GSW": "gs", "PHO": "phx", "NYK": "ny", "SAS": "sa", "NOP": "no", "WAS": "wsh", "UTA": "utah", "BRK": "bkn"}
PARTS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "P+A": ("PTS", "AST"), "P+R": ("PTS", "REB"),
         "R+A": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}
PAUSE = 0.25


def _cache_path(date):
    from nbaprops.rules import ledger as L
    return os.path.join(L.HERE, "history", date, f"espn_form_{date}.json")


def _games(pid):
    """[{date, PTS, REB, AST, 3PM}] regular season, newest first, games with minutes only."""
    g = BX._get(GAMELOG.format(pid=pid))
    labels = g.get("labels") or []
    ix = {k: labels.index(k) for k in ("MIN", "3PT", "REB", "AST", "PTS") if k in labels}
    evs = g.get("events") or {}
    out = []
    for st in g.get("seasonTypes") or []:
        if "Regular Season" not in (st.get("displayName") or ""):
            continue
        for cat in st.get("categories") or []:
            for e in cat.get("events") or []:
                s = e.get("stats") or []
                try:
                    if int(float(s[ix["MIN"]] or 0)) <= 0:
                        continue
                    out.append({"date": (evs.get(e["eventId"]) or {}).get("gameDate", ""), "PTS": int(s[ix["PTS"]]),
                                "REB": int(s[ix["REB"]]), "AST": int(s[ix["AST"]]), "3PM": int(str(s[ix["3PT"]]).split("-")[0])})
                except (KeyError, ValueError, IndexError):
                    continue
    return sorted(out, key=lambda x: x["date"], reverse=True)


def load(date, players: dict[str, str], log=print) -> dict:
    """players: {name: page team code}. -> {norm name: [games]} (cached for the day; only missing players fetched)."""
    try:
        cache = json.load(open(_cache_path(date)))
    except (OSError, ValueError):
        cache = {}
    want = {BX.norm(n): (n, t) for n, t in players.items() if BX.norm(n) not in cache}
    if want:
        ids = {}
        for t in sorted({t for _, t in want.values()}):
            try:
                r = BX._get(ROSTER.format(t=TO_ESPN.get(t, t.lower())))
                for a in r.get("athletes") or []:
                    ids[BX.norm(a.get("displayName", ""))] = a.get("id")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(PAUSE)
        got = 0
        for k in want:
            pid = ids.get(k) or next((v for kk, v in ids.items() if kk.split()[-1:] == k.split()[-1:] and kk[:1] == k[:1]), None)
            if not pid:
                continue
            try:
                cache[k] = _games(pid)
                got += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(PAUSE)
        log(f"ESPN game logs: {got} of {len(want)} players fetched (free; {len(cache)} cached for {date}).")
        os.makedirs(os.path.dirname(_cache_path(date)), exist_ok=True)
        json.dump(cache, open(_cache_path(date), "w"))
    return cache


def form_fields(games: list[dict], market: str, side: str, line: float) -> dict:
    """form_l5 / form_l10 / form_l20 / form_season as 'hits-games' for the side vs. the line."""
    if market not in PARTS or not games:
        return {}
    vals = [sum(g[p] for p in PARTS[market]) for g in games]
    hit = (lambda v: v > line) if side == "over" else (lambda v: v < line)
    out = {}
    for k, n in (("form_l5", 5), ("form_l10", 10), ("form_l20", 20), ("form_season", len(vals))):
        w = vals[:n]
        out[k] = f"{sum(hit(v) for v in w)}-{len(w)}"
    return out
