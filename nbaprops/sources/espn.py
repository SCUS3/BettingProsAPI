"""Final NBA box scores for grading, from public sources (no BettingPros requests).

ESPN's public JSON (site.api.espn.com) first, NBA.com's CDN (cdn.nba.com) as the fallback for any game ESPN
doesn't have final. Only final games are used. Results are saved to history/<date>/boxscore_<date>.json, so
a regrade reads the file instead of fetching again.

    finals(date) -> {"games": {game_key: {...}}, "players": {norm_name: {"name", "team", "played", "PTS", "REB",
                     "AST", "3PM"}}, "source": {...}}
    stat(players_entry, market) -> final number for PTS/REB/AST/3PM/P+A/P+R/PRA/R+A
"""
import datetime as dt
import json
import os
import re
import sys
import unicodedata
import urllib.request

from nbaprops.config import ROOT as HERE  # noqa: E402
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
NBA_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NBA_BOX = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{}.json"
UA = {"User-Agent": "Mozilla/5.0 (props-grader)", "Accept": "application/json"}
NBA_HEADERS = dict(UA, Referer="https://www.nba.com/", Origin="https://www.nba.com")

PARTS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "P+A": ("PTS", "AST"),
         "P+R": ("PTS", "REB"), "PRA": ("PTS", "REB", "AST"), "R+A": ("REB", "AST")}
ALIASES = {}   # norm(BettingPros/FanDuel name) -> norm(box score name), for spellings that differ


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`\-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def _get(url, headers=UA, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def stat(p, market):
    if not p or not p.get("played") or market not in PARTS:
        return None
    vals = [p.get(k) for k in PARTS[market]]
    return None if any(v is None for v in vals) else sum(vals)


# ------------------------------------------------------------------ ESPN
def _int(x):
    try:
        return int(str(x).split("-")[0])
    except (TypeError, ValueError):
        return None


def espn_games(date):
    sb = _get(f"{ESPN}/scoreboard?dates={date.replace('-', '')}")
    out = []
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = sorted(c.get("team", {}).get("abbreviation", "") for c in comp.get("competitors", []))
        out.append({"id": ev["id"], "teams": teams, "final": ev.get("status", {}).get("type", {}).get("name") == "STATUS_FINAL",
                    "name": ev.get("shortName")})
    return out


def espn_box(game_id):
    j = _get(f"{ESPN}/summary?event={game_id}")
    players = {}
    for t in (j.get("boxscore") or {}).get("players", []):
        team = t.get("team", {}).get("abbreviation", "")
        for st in t.get("statistics", [])[:1]:
            keys = st.get("keys") or []
            idx = {k: i for i, k in enumerate(keys)}
            for a in st.get("athletes", []):
                name = a.get("athlete", {}).get("displayName", "")
                s = a.get("stats") or []
                mins = _int(s[idx["minutes"]]) if s and "minutes" in idx else None
                played = bool(s) and not a.get("didNotPlay") and (mins or 0) > 0
                g = lambda k: _int(s[idx[k]]) if s and k in idx else None
                players[norm(name)] = {"name": name, "team": team, "played": played, "PTS": g("points"),
                                       "REB": g("rebounds"), "AST": g("assists"),
                                       "3PM": g("threePointFieldGoalsMade-threePointFieldGoalsAttempted")}
    return players


# ------------------------------------------------------------------ NBA.com
def nba_games(date):
    s = _get(NBA_SCHEDULE, NBA_HEADERS)
    want = dt.date.fromisoformat(date).strftime("%m/%d/%Y")
    out = []
    for gd in s.get("leagueSchedule", {}).get("gameDates", []):
        if gd.get("gameDate", "").startswith(want):
            for g in gd.get("games", []):
                out.append({"id": g["gameId"], "teams": sorted([g["awayTeam"]["teamTricode"], g["homeTeam"]["teamTricode"]]),
                            "final": g.get("gameStatus") == 3, "name": f"{g['awayTeam']['teamTricode']} @ {g['homeTeam']['teamTricode']}"})
    return out


def nba_box(game_id):
    j = _get(NBA_BOX.format(game_id), NBA_HEADERS)
    g = j.get("game", {})
    players = {}
    for side in ("homeTeam", "awayTeam"):
        t = g.get(side, {})
        for a in t.get("players", []):
            s = a.get("statistics", {})
            name = a.get("name") or f"{a.get('firstName', '')} {a.get('familyName', '')}".strip()
            played = str(a.get("played")) == "1" and a.get("status") == "ACTIVE"
            players[norm(name)] = {"name": name, "team": t.get("teamTricode", ""), "played": played,
                                   "PTS": s.get("points"), "REB": s.get("reboundsTotal"), "AST": s.get("assists"),
                                   "3PM": s.get("threePointersMade")}
    return players


# ------------------------------------------------------------------ both
def _path(date):
    return os.path.join(HERE, "history", date, f"boxscore_{date}.json")


def finals(date, refresh=False, log=print):
    """Box scores for every final game on `date` (ET). ESPN first; NBA.com for games ESPN lacks or hasn't
    marked final. Saved once every game on the slate is final."""
    path = _path(date)
    if not refresh and os.path.exists(path):
        try:
            return json.load(open(path))
        except ValueError:
            pass
    out = {"games": {}, "players": {}, "source": {}, "unfinished": []}
    try:
        eg = espn_games(date)
    except Exception as e:
        log(f"ESPN scoreboard unavailable ({e}); trying NBA.com.")
        eg = []
    for g in eg:
        if not g["final"]:
            continue
        try:
            pl = espn_box(g["id"])
        except Exception as e:
            log(f"ESPN box score {g['name']} unavailable ({e}).")
            continue
        if pl:
            key = "-".join(g["teams"])
            out["games"][key] = g["name"]
            out["source"][key] = "ESPN"
            for k, v in pl.items():
                out["players"].setdefault(k, dict(v, game=key))
    have = set(out["games"])
    need_nba = not eg or any(not g["final"] or "-".join(g["teams"]) not in have for g in eg)
    if need_nba:
        try:
            ng = nba_games(date)
        except Exception as e:
            log(f"NBA.com schedule unavailable ({e}).")
            ng = []
        for g in ng:
            key = "-".join(g["teams"])
            if key in have or not g["final"]:
                continue
            try:
                pl = nba_box(g["id"])
            except Exception as e:
                log(f"NBA.com box score {g['name']} unavailable ({e}).")
                continue
            if pl:
                out["games"][key] = g["name"]
                out["source"][key] = "NBA.com"
                for k, v in pl.items():
                    out["players"].setdefault(k, dict(v, game=key))
    all_games = {"-".join(g["teams"]): g["name"] for g in eg}
    out["unfinished"] = sorted(n for k, n in all_games.items() if k not in out["games"])
    if out["games"] and not out["unfinished"]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(out, open(path, "w"), indent=1)
    return out


def lookup(box, player):
    return box.get("players", {}).get(norm(player))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else (dt.date.today() - dt.timedelta(days=1)).isoformat()
    b = finals(d, refresh="--refresh" in sys.argv)
    print(f"{d}: {len(b['games'])} final games ({', '.join(sorted(set(b['source'].values())))}), "
          f"{len(b['players'])} players" + (f"; not final/missing: {', '.join(b['unfinished'])}" if b["unfinished"] else ""))
