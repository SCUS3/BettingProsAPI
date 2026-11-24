"""Today's NBA schedule from ESPN (free, no BettingPros requests): earliest tip, suggested first pull
(earliest tip - 90 min) and the day's live-flow request estimate.

    python3 bp_schedule.py [YYYY-MM-DD]
"""
from nbaprops import config as C  # thresholds, caps and settings
import datetime as dt
import math
import sys

from nbaprops.sources import espn as BX
PULL_LEAD_MIN = C.PULL_LEAD_MIN  # nbaprops/config.py
PULL_PER_GAME = C.PULL_PER_GAME  # nbaprops/config.py
REPRICE_PER_GAME = C.REPRICE_PER_GAME  # nbaprops/config.py


def et(ts):
    try:
        from zoneinfo import ZoneInfo
        return ts.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return ts - dt.timedelta(hours=5)


def slate(date):
    """[(tip UTC datetime, 'AWY @ HOM', status)] for regular-season games on `date` (ET)."""
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    out = []
    for e in sb.get("events", []):
        if (e.get("season") or {}).get("type") != 2:
            continue
        tip = dt.datetime.strptime(e["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
        out.append((tip, e.get("shortName"), e.get("status", {}).get("type", {}).get("name")))
    return sorted(out)


def plan(date):
    games = slate(date)
    if not games:
        return {"date": date, "games": 0}
    first = games[0][0]
    pull_at = first - dt.timedelta(minutes=PULL_LEAD_MIN)
    n = len(games)
    return {"date": date, "games": n, "first_tip": first, "last_tip": games[-1][0], "pull_at": pull_at,
            "pull_est": math.ceil(PULL_PER_GAME * n + 1), "reprice_est": math.ceil(REPRICE_PER_GAME * n),
            "list": games}


def main():
    try:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        today = dt.date.today().isoformat()
    d = sys.argv[1] if len(sys.argv) > 1 else today
    p = plan(d)
    if not p["games"]:
        print(f"{d}: no regular-season games.")
        return
    print(f"{d}: {p['games']} games. Earliest tip {et(p['first_tip']):%I:%M %p} ET, last {et(p['last_tip']):%I:%M %p} ET.")
    print(f"Suggested first pull: {et(p['pull_at']):%I:%M %p} ET (earliest tip - {PULL_LEAD_MIN} min); "
          f"≈{p['pull_est']} BettingPros requests, re-price ≈{p['reprice_est']}. Start --watch right after the pull.")
    for tip, name, st in p["list"]:
        print(f"  {et(tip):%I:%M %p} ET  {name}  {st}")
    try:                                   # weekly, free GET of CrazyNinja's API page
        from nbaprops.app import cno_watch as bp_cno_watch
        msg = bp_cno_watch.check()
        if msg:
            print(msg)
    except Exception:
        pass


if __name__ == "__main__":
    main()
