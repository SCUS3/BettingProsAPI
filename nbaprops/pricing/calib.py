"""BettingPros projection calibration for the real-projection markets (3PM, P+R). No API requests.

factor = sum(actual) / sum(projection) over graded props (one per player-game), from bp_graded.csv plus the sim pulls
(sim/<date>/props_raw.json: projection and final, projections checked not to be updated after the game), shrunk
toward 1.0 while n < SHRINK_N:  factor = w * raw + (1 - w) * 1.0,  w = min(n, SHRINK_N) / SHRINK_N.
calibrated projection = raw projection x factor.
"""
import csv
import glob
import json
import os

from nbaprops.config import ROOT as HERE  # noqa: E402
MARKETS = ("3PM", "P+R")
SHRINK_N = 200
USE_CALIBRATION = False      # off for now: display and tags use BettingPros' raw projection; factors are still logged
_cache = {}


def samples():
    seen = {}
    try:
        for r in csv.DictReader(open(os.path.join(HERE, "bp_graded.csv"))):
            if r.get("market") in MARKETS and r.get("bp_proj") not in ("", None) and r.get("actual") not in ("", None):
                seen[(r["date"], r["player"], r["market"])] = (float(r["bp_proj"]), float(r["actual"]))
    except OSError:
        pass
    for path in glob.glob(os.path.join(HERE, "sim", "*", "props_raw.json")):
        try:
            raw = json.load(open(path))
        except (OSError, ValueError):
            continue
        date = os.path.basename(os.path.dirname(path))
        labels = {int(k): v for k, v in raw.get("labels", {}).items()}
        for p in raw.get("props", []):
            mk = labels.get(p.get("market_id"))
            proj = (p.get("projection") or {}).get("value")
            sc = p.get("scoring") or {}
            if mk in MARKETS and proj is not None and sc.get("is_scored") and sc.get("actual") is not None:
                seen.setdefault((date, (p.get("participant") or {}).get("name"), mk), (float(proj), float(sc["actual"])))
    return seen


def factors():
    """{market: {"raw", "factor", "n", "w"}}."""
    if _cache:
        return _cache
    s = samples()
    for mk in MARKETS:
        sub = [v for k, v in s.items() if k[2] == mk]
        sp, sa = sum(p for p, _ in sub), sum(a for _, a in sub)
        raw = sa / sp if sp else 1.0
        w = min(len(sub), SHRINK_N) / SHRINK_N
        _cache[mk] = {"raw": round(raw, 3), "factor": round(w * raw + (1 - w), 3), "n": len(sub), "w": round(w, 2)}
    return _cache


def value(market, proj):
    """The calibrated projection (3PM/P+R), whether or not calibration is in use. Logged as bp_proj_cal."""
    if proj is None or market not in MARKETS:
        return proj
    return round(float(proj) * factors()[market]["factor"], 2)


def calibrate(market, proj):
    """Projection used for display and agree/disagree: calibrated when USE_CALIBRATION, else raw."""
    return value(market, proj) if USE_CALIBRATION else proj


if __name__ == "__main__":
    for mk, f in factors().items():
        print(f"{mk}: n {f['n']}, raw factor {f['raw']} (actual / projection), shrunk {f['factor']} (weight {f['w']} toward raw)")
