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
    if date not in _cache:
        idx = {}
        try:
            for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
                if r.get("bp_proj") not in (None, ""):
                    idx.setdefault((norm(r["player"]), r["market"]), r)
        except OSError:
            pass
        _cache[date] = idx
    return _cache[date]


def context(date, player, market, side, line):
    """-> {"text", "tag", "proj", "gap"} for the +EV side `side` at `line`."""
    r = load(date).get((norm(player), market))
    if not r:
        return {"text": "–", "tag": "none", "proj": None, "gap": None}
    proj, line = float(r["bp_proj"]), float(line)
    ps = "over" if proj > line else "under" if proj < line else None
    if market in REAL:
        gap = round(proj - line, 2)
        tag = "agrees" if ps == side else "disagrees" if ps else "on the line"
        return {"text": f"BP proj {proj:g} (gap {gap:+.2f} vs {line:g}) · {DOT.get(tag, '⚪ on the line')}", "tag": tag,
                "proj": proj, "gap": gap}
    lean = ps or r.get("bp_lean") or None
    gap = round(proj - line, 2)
    if not lean:
        return {"text": f"BP proj {proj:g} (gap {gap:+.2f} vs {line:g}) · ⚪ on the line (lean only)", "tag": "none",
                "proj": proj, "gap": gap}
    tag = "lean agrees" if lean == side else "lean disagrees"
    return {"text": f"BP proj {proj:g} (gap {gap:+.2f} vs {line:g}) · {DOT[tag.split()[1]]} (lean only)",
            "tag": tag, "proj": proj, "gap": gap}
