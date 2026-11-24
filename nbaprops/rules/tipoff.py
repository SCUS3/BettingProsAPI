"""Tip-time rule, used everywhere a game's start matters. Free: ESPN's public scoreboard / play-by-play only.

A game is bettable until its scheduled start + GRACE (10 min), unless ESPN's scoreboard shows it in progress earlier
(ESPN overrides, and is checked before anything is logged inside that window). Only pregame prices are ever used
(callers already skip lines marked isLive or suspended).

  is_open(tip, names=None, now=None)  -> bool       live decisions: pull cutoff, BET/alt/screener logging
  started(tip, names=None)            -> bool|None   ESPN says in progress (None: no matching ESPN game)
  cutoff(tip, date, event_id)         -> ISO         grading: the game's actual start (first play, ESPN
                                                     play-by-play, saved per date) or tip + GRACE if unknown
  save_actual_starts(date)                           at grading: first-play times for the date's final games
"""
from nbaprops import config as C  # thresholds, caps and settings
import csv
import datetime as dt
import json
import os
import re
import time

from nbaprops.sources import espn as BX
from nbaprops.config import ROOT as HERE  # noqa: E402
GRACE = dt.timedelta(minutes=C.GRACE_MIN)  # nbaprops/config.py
CLOSE_AFTER = dt.timedelta(minutes=C.CLOSE_AFTER_MIN)  # closing-CLV scan: scheduled start + 5 min  # nbaprops/config.py
CACHE_S = 60
_sb = {}
ALIAS = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "NOR": "NO", "NOP": "NO", "UTA": "UTAH", "UTH": "UTAH", "WAS": "WSH",
         "PHO": "PHX", "BRK": "BKN", "CHO": "CHA"}


def _t(iso):
    s = str(iso).replace("Z", "")[:19]
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S" if len(s) > 16 else "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def et_date(t):
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (t - dt.timedelta(hours=5)).date().isoformat()


def games(date):
    """ESPN scoreboard for an ET date, cached CACHE_S seconds: [{id, start, status, abbrs, names}]."""
    hit = _sb.get(date)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    except Exception:
        return hit[1] if hit else []
    out = []
    for e in sb.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        teams = [c.get("team", {}) for c in comp.get("competitors", [])]
        out.append({"id": e["id"], "start": _t(e["date"]), "status": e.get("status", {}).get("type", {}).get("name"),
                    "abbrs": {t.get("abbreviation", "") for t in teams},
                    "names": {w.lower() for t in teams for w in (t.get("displayName", "") + " " + t.get("shortDisplayName", "")).split()
                              if len(w) > 3}})
    _sb[date] = (time.time(), out)
    return out


def _match(tip_t, names):
    gs = games(et_date(tip_t))
    if names:
        words = {ALIAS.get(w, w) for w in re.findall(r"[A-Za-z]+", " ".join(names) if isinstance(names, (list, set, tuple)) else str(names))}
        low = {w.lower() for w in words if len(w) > 3}
        m = [g for g in gs if (g["abbrs"] & words) or (g["names"] & low)]
        m = [g for g in m if abs((g["start"] - tip_t).total_seconds()) < 6 * 3600] or m
        if m:
            return m
    return [g for g in gs if abs((g["start"] - tip_t).total_seconds()) < 90]


def started(tip, names=None):
    """True if ESPN shows the game (matched by team, else by scheduled start) under way or over; None if unmatched."""
    m = _match(_t(tip), names)
    if not m:
        return None
    return any(g["status"] not in ("STATUS_SCHEDULED", "STATUS_PREGAME", "STATUS_POSTPONED", "STATUS_DELAYED") for g in m)


def is_open(tip, names=None, now=None):
    """Still bettable: before scheduled start + GRACE and ESPN doesn't show it under way (checked inside the window)."""
    if not tip:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(now, str):
        now = _t(now)
    t = _t(tip)
    if now >= t + GRACE:
        return False
    if now < t - dt.timedelta(minutes=2):
        return True
    return started(tip, names) is not True


def deadline(tip):
    """ISO of scheduled start + GRACE (the latest a game can still be open)."""
    return _iso(_t(tip) + GRACE)


# ------------------------------------------------------------------ grading
def _path(date):
    return os.path.join(HERE, "history", date, f"actual_starts_{date}.json")


def save_actual_starts(date):
    """{bp event id: first-play time} for the date's final games (ESPN play-by-play), mapped by teams."""
    path = _path(date)
    try:
        have = json.load(open(path))
    except (OSError, ValueError):
        have = {}
    ev_teams = {}
    for p in [os.path.join(HERE, f"bp_props_{date}.csv")]:
        try:
            for r in csv.DictReader(open(p)):
                if r.get("event_id") and r.get("team"):
                    ev_teams.setdefault(str(r["event_id"]).split(".")[0], set()).add(ALIAS.get(r["team"], r["team"]))
        except OSError:
            pass
    for g in games(date):
        if g["status"] != "STATUS_FINAL" or g["id"] in have.get("_espn", {}):
            continue
        try:
            j = BX._get(f"{BX.ESPN}/summary?event={g['id']}")
            first = next((p.get("wallclock") for p in j.get("plays", []) if p.get("wallclock")), None)
        except Exception:
            first = None
        if not first:
            continue
        have.setdefault("_espn", {})[g["id"]] = first
        for ev, teams in ev_teams.items():
            if teams & g["abbrs"]:
                have[ev] = first
    if have:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(have, open(path, "w"), indent=1)
    return have


_starts = {}


def cutoff(tip, date, event_id=None):
    """Grading cutoff for a row: the game's actual first play if known, else scheduled start + GRACE."""
    if not tip:
        return None
    if date not in _starts:
        try:
            _starts[date] = json.load(open(_path(date)))
        except (OSError, ValueError):
            _starts[date] = {}
    first = _starts[date].get(str(event_id or "").split(".")[0])
    lim = _t(tip) + GRACE
    if first:
        lim = min(lim, _t(first))
    return _iso(lim)
