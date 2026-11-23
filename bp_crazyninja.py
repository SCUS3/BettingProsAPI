"""Second-opinion EV from Crazy Ninja Odds' devigger API (work in progress, api=open; no key).

For one bet: each source's two-way odds at FanDuel's line are devigged with CNO's worst-case method
(multiplicative, additive, power and shin; the least favourable fair value wins) against FanDuel's price.
Reports the median and the lowest EV across sources. Only odds numbers are sent. Responses are cached in
cno_cache.json, so a repeat check costs no call.
"""
import json
import os
import statistics
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://api.crazyninjaodds.com/api/devigger/v1/sportsbook_devigger.aspx"
CACHE = os.path.join(HERE, "cno_cache.json")
PAUSE = 0.4            # seconds between calls (be polite to a pre-release API)


def _fmt(a):
    a = float(a)
    return f"+{a:g}" if a > 0 else f"{a:g}"


def _load():
    try:
        return json.load(open(CACHE))
    except (OSError, ValueError):
        return {}


def devig(leg_odds, other_odds, final_odds, cache=None):
    """CNO worst-case devig of one two-way market. Returns {'fair', 'ev', 'kelly', 'method'} or None."""
    cache = _load() if cache is None else cache
    key = f"{_fmt(leg_odds)}/{_fmt(other_odds)}|{_fmt(final_odds)}"
    if key in cache:
        return cache[key]
    q = {"api": "open", "LegOdds": f"{_fmt(leg_odds)}/{_fmt(other_odds)}", "FinalOdds": _fmt(final_odds),
         "DevigMethod": 4, "WorstCase_Multiplicative": 1, "WorstCase_Additive": 1, "WorstCase_Power": 1,
         "WorstCase_Shin": 1, "Args": "ev_p,kelly,dm"}
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "props-devig-check"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
    except Exception:
        return None
    time.sleep(PAUSE)
    fin = j.get("Final") or {}
    if fin.get("EV_Percentage") is None:
        return None
    out = {"fair": fin.get("FairValue"), "ev": fin.get("EV_Percentage"), "kelly": fin.get("Kelly_Full"),
           "method": fin.get("DevigMethod")}
    cache[key] = out
    return out


def check(side, two_way, fd_price):
    """two_way = [(source, over_odds, under_odds)] at FanDuel's line. Returns {'median_ev', 'min_ev', 'n'} or None."""
    if not two_way or fd_price is None:
        return None
    cache = _load()
    evs = []
    for _, o, u in two_way:
        leg, other = (o, u) if side == "over" else (u, o)
        r = devig(leg, other, fd_price, cache)
        if r:
            evs.append(r["ev"])
    try:
        json.dump(cache, open(CACHE, "w"))
    except OSError:
        pass
    if not evs:
        return None
    return {"median_ev": round(statistics.median(evs), 4), "min_ev": round(min(evs), 4), "n": len(evs)}
