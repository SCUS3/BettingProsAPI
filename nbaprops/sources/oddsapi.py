"""The Odds API v4 client: GET with the key, credit accounting from the x-requests-* headers (odds_credits.json),
and the guard that stops before a call would leave fewer than MIN_REMAINING credits. Every event-odds call costs
credits: ask before calling (CLAUDE.md). /events is free.
"""
from nbaprops import config as C  # thresholds, caps and settings
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from nbaprops.config import ROOT as HERE


BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"


CREDITS = os.path.join(HERE, "odds_credits.json")


MIN_REMAINING = C.ODDSAPI_MIN_REMAINING  # nbaprops/config.py


# ------------------------------------------------------------------ credits
def load_credits():
    try:
        with open(CREDITS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"remaining": None, "used": None, "calls": []}


def save_credits(c):
    c["calls"] = c.get("calls", [])[-200:]
    with open(CREDITS, "w") as f:
        json.dump(c, f, indent=1)


def get(path, params, key, expect_cost):
    """GET with the key; record the usage headers. Never prints the key."""
    url = f"{BASE}{path}?{urllib.parse.urlencode(dict(params, apiKey=key))}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"accept": "application/json"}), timeout=30) as r:
            body, h = json.load(r), r.headers
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:200]
        record(path, e.headers, expect_cost)
        sys.exit(f"Odds API HTTP {e.code} on {path}: {msg}")
    record(path, h, expect_cost)
    return body


def record(path, h, expect_cost):
    c = load_credits()
    num = lambda k: int(float(h.get(k))) if h and h.get(k) not in (None, "") else None
    used, rem, last = num("x-requests-used"), num("x-requests-remaining"), num("x-requests-last")
    if rem is not None:
        c["remaining"], c["used"] = rem, used
    c["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.setdefault("calls", []).append({"at": c["updated"], "path": path.split("?")[0], "cost": last,
                                      "expected": expect_cost, "remaining": rem})
    save_credits(c)
    print(f"  Odds API {path.split('/odds')[0] if '/odds' in path else path}: cost {last} (expected {expect_cost}); "
          f"remaining {rem}, used {used} this month")
    if last is not None and last > expect_cost:
        sys.exit(f"Stopped: that call cost {last}, more than the {expect_cost} expected. Check before continuing.")


def guard(cost, hard=True):
    """False (or exit, if hard) when a call would leave fewer than MIN_REMAINING credits this month."""
    rem = load_credits().get("remaining")
    if rem is not None and rem - cost < MIN_REMAINING:
        msg = f"Stopped: {rem} credits left this month; this call ({cost}) would go under {MIN_REMAINING}."
        if hard:
            sys.exit(msg)
        print("  " + msg + " Remaining games skipped; results so far are saved.")
        return False
    return True
