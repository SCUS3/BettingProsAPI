"""BettingPros projection context for any prop, from today's saved pull only (bp_props_<date>.csv; 0 requests).
Display and tagging only: it never changes a BET decision.

    3PM / P+R (real projections): "BP proj 2.3 (gap +0.80 vs 1.5) · proj agrees"   tag: agrees / disagrees
    PTS/REB/AST/P+A/PRA (side leans): "BP proj 23.0 (gap -1.50 vs 24.5) · lean under, agrees (lean only)"
                                                                                  tag: lean agrees / lean disagrees
    not in the pull: "–"                                                          tag: none
"""
import csv
import os

from bp_rotowire import norm

HERE = os.path.dirname(os.path.abspath(__file__))
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
    t = dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        t -= dt.timedelta(hours=5)
    return t.strftime("%-I:%M %p ET")


def context(date, player, market, side, line):
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
        import bp_calib
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
