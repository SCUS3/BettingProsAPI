"""Crazy Ninja Odds' devigger API (work in progress, api=open; no key): worst-case devig per source.

Every call devigs ONE source's two-way market: LegOdds = "<bet side odds>/<other side odds>" (e.g. "-120/+100"),
FinalOdds = FanDuel's price. Sources are never joined with commas (CNO would price that as a parlay); a bet
priced by five sources is five calls, and we take the median. DevigMethod=4 (worst case) with all four
WorstCase flags = 1: the least favourable of multiplicative, additive, power and shin.

Throttled to 1 call per second, cached by (odds, final odds, method, boost) in cno_cache.json, and any error
(down, timeout, odd response) is skipped silently: the caller gets None and says "no CNO check".
Only odds numbers are sent.
"""
from nbaprops import config as C  # thresholds, caps and settings
import json
import os
import statistics
import time
import urllib.parse
import urllib.request

from nbaprops.config import ROOT as HERE  # noqa: E402
URL = "http://api.crazyninjaodds.com/api/devigger/v1/sportsbook_devigger.aspx"
CACHE = os.path.join(HERE, "cno_cache.json")
MIN_INTERVAL = C.CNO_MIN_INTERVAL  # nbaprops/config.py
WORST_CASE = 4
_last = [0.0]
_cache = [None]


def _fmt(a):
    a = float(a)
    return f"+{a:g}" if a > 0 else f"{a:g}"


def _load():
    if _cache[0] is None:
        try:
            _cache[0] = json.load(open(CACHE))
        except (OSError, ValueError):
            _cache[0] = {}
    return _cache[0]


def _save():
    try:
        json.dump(_load(), open(CACHE, "w"))
    except OSError:
        pass


def params(leg_odds, other_odds, final_odds, method=WORST_CASE, boost=None, boost_type=0):
    """Query for one source's two-way market. boost = percent (e.g. 25), boost_type 0 = profit, 1 = all."""
    q = {"api": "open", "LegOdds": f"{_fmt(leg_odds)}/{_fmt(other_odds)}", "FinalOdds": _fmt(final_odds),
         "DevigMethod": method, "Args": "ev_p,kelly,dm,fo_o"}
    if method == WORST_CASE:
        q.update(WorstCase_Multiplicative=1, WorstCase_Additive=1, WorstCase_Power=1, WorstCase_Shin=1)
    if boost:
        q.update(Boost_Bool=1, Boost_Text=f"{float(boost):g}", Boost_Type=int(boost_type))
    return q


def url(*a, **k):
    """The devigger URL for one source's two-way market (for display)."""
    return f"{URL}?{urllib.parse.urlencode(params(*a, **k))}"


def devig(leg_odds, other_odds, final_odds, method=WORST_CASE, boost=None, boost_type=0):
    """{'fair', 'ev', 'kelly', 'method', 'final_odds'} for one source, or None on any error."""
    q = params(leg_odds, other_odds, final_odds, method, boost, boost_type)
    key = "|".join(f"{k}={q[k]}" for k in ("LegOdds", "FinalOdds", "DevigMethod")) + \
          (f"|boost={q['Boost_Text']}:{q['Boost_Type']}" if boost else "")
    cache = _load()
    if key in cache:
        return cache[key]
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    try:
        req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "props-devig-check"})
        with urllib.request.urlopen(req, timeout=20) as r:
            fin = (json.load(r) or {}).get("Final") or {}
        if fin.get("EV_Percentage") is None or fin.get("FairValue") is None:
            return None
        out = {"fair": float(fin["FairValue"]), "ev": float(fin["EV_Percentage"]),
               "kelly": None if fin.get("Kelly_Full") is None else float(fin["Kelly_Full"]),
               "method": fin.get("DevigMethod"), "final_odds": fin.get("Odds")}
    except Exception:
        return None
    cache[key] = out
    _save()
    return out


def check(side, two_way, fd_price, boost=None, boost_type=0):
    """two_way = [(source, over_odds, under_odds)] at FanDuel's line, one CNO call per source.
    Returns {'median_ev', 'min_ev', 'fair', 'kelly', 'n'} (medians across sources) or None if no call worked."""
    if not two_way or fd_price is None:
        return None
    res = []
    for _, o, u in two_way:
        leg, other = (o, u) if side == "over" else (u, o)
        r = devig(leg, other, fd_price, boost=boost, boost_type=boost_type)
        if r:
            res.append(r)
    if not res:
        return None
    ks = [r["kelly"] for r in res if r.get("kelly") is not None]
    return {"median_ev": round(statistics.median(r["ev"] for r in res), 4), "min_ev": round(min(r["ev"] for r in res), 4),
            "fair": round(statistics.median(r["fair"] for r in res), 4),
            "kelly": round(statistics.median(ks), 3) if ks else None, "n": len(res)}
