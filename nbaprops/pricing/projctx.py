"""BettingPros projection context for any prop, from today's saved pull only (bp_props_<date>.csv; 0 requests).
Display and tagging only: it never changes a BET decision.

    3PM / P+R (real projections): "BP proj 2.3 (gap +0.80 vs 1.5) · proj agrees"   tag: agrees / disagrees
    PTS/REB/AST/P+A/PRA (side leans): "BP proj 23.0 (gap -1.50 vs 24.5) · lean under, agrees (lean only)"
                                                                                  tag: lean agrees / lean disagrees
    not in the pull: "–"                                                          tag: none
"""
import csv
import os

from nbaprops.sources.rotowire import norm

from nbaprops.config import ROOT as HERE  # noqa: E402
DOT = {"agrees": "🟢 agrees", "disagrees": "🔴 disagrees"}
REAL = {"3PM", "P+R"}
_cache = {}


def load(date):
    """({(norm player, exact market): row with a projection}, markets in the pull, 'HH:MM ET' of the pull)."""
    if date not in _cache:
        idx, markets, pulled = {}, set(), ""
        try:
            for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
                markets.add(r["market"])
                pulled = max(pulled, r.get("pulled_at") or "")
                if r.get("bp_proj") not in (None, ""):
                    idx.setdefault((norm(r["player"]), r["market"]), r)
        except OSError:
            pass
        _cache[date] = (idx, markets, _et(pulled))
    return _cache[date]


def _et(iso):
    if not iso:
        return "?"
    import datetime as dt
    t = dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.UTC)
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        t -= dt.timedelta(hours=5)
    return t.strftime("%-I:%M %p ET")


def context(date: str, player: str, market: str, side: str, line: float) -> dict:
    """-> {"text", "tag", "proj", "gap"} for the +EV side `side` at `line`."""
    idx, markets, asof = load(date)
    if market not in markets:                       # exact market only: never another market's projection
        return {"text": f"– (not pulled; pull as of {asof})", "tag": "none", "proj": None, "gap": None}
    r = idx.get((norm(player), market))
    if not r:
        return {"text": f"– (no projection; as of {asof})", "tag": "none", "proj": None, "gap": None}
    proj, line = float(r["bp_proj"]), float(line)
    ps = "over" if proj > line else "under" if proj < line else None
    if market in REAL:                                  # calibrated projection decides the side (bp_calib)
        from nbaprops.pricing import calib as bp_calib
        cal = bp_calib.calibrate(market, proj)
        gap = round(cal - line, 2)
        cs = "over" if cal > line else "under" if cal < line else None
        tag = "agrees" if cs == side else "disagrees" if cs else "on the line"
        shown = f"raw {proj:.1f} → calibrated {cal:.1f}" if bp_calib.USE_CALIBRATION else f"{proj:.1f}"
        return {"text": f"BP proj {shown} (gap {gap:+.1f} vs {line:g}) · {DOT.get(tag, '⚪ on the line')} · as of {asof}",
                "tag": tag, "proj": proj, "proj_cal": bp_calib.value(market, proj), "gap": gap}
    lean = ps or r.get("bp_lean") or None
    gap = round(proj - line, 2)
    if not lean:
        return {"text": f"BP lean: proj {proj:g} (gap {gap:+.2f} vs {line:g}) · ⚪ on the line · as of {asof}", "tag": "none",
                "proj": proj, "gap": gap}
    tag = "lean agrees" if lean == side else "lean disagrees"
    return {"text": f"BP lean: proj {proj:g} (gap {gap:+.2f} vs {line:g}) · {DOT[tag.split()[1]]} · as of {asof}",
            "tag": tag, "proj": proj, "gap": gap}


def dvp_effect(side: str | None, softness) -> str | None:
    """Side-aware matchup: 'helps' (soft 1-10 for an over, hard 21-30 for an under), 'hurts' (the reverse),
    'neutral' (11-20), None if unknown. DvP 1 = softest defense vs. the position."""
    try:
        v = float(softness)
    except (TypeError, ValueError):
        return None
    if 11 <= v <= 20:
        return "neutral"
    soft = v <= 10
    if side == "over":
        return "helps" if soft else "hurts"
    if side == "under":
        return "hurts" if soft else "helps"
    return None


def dvp_dot(side: str | None, softness) -> str:
    """'🟢 7' / '🔴 28' / '15' (side-aware), '' if unknown."""
    e = dvp_effect(side, softness)
    if e is None:
        return ""
    v = float(softness)
    return {"helps": "🟢 ", "hurts": "🔴 "}.get(e, "") + f"{v:g}"


def dvp_of(date: str, player: str, market: str) -> float | None:
    """DvP softness for a player/market from today's pull (any row, projection or not), or None."""
    import csv
    for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))) if os.path.exists(os.path.join(HERE, f"bp_props_{date}.csv")) else []:
        if norm(r["player"]) == norm(player) and r["market"] == market and r.get("dvp_softness") not in (None, ""):
            return float(r["dvp_softness"])
    return None
