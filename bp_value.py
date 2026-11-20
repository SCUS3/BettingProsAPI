#!/usr/bin/env python3
"""
Value check for our FanDuel picks against the wider market, using The Odds API (does not replace BettingPros).

For each pick: devig every other book's over/under at FanDuel's line (multiplicative and additive), take the
median fair probability across the non-FanDuel books (3+ required), and compute EV at FanDuel's price.

Usage:
    python3 bp_value.py --picks              # plan only: today's logged picks, the credit cost, no API call
    python3 bp_value.py --top 10             # plan only: top 10 FanDuel props by Balanced
    python3 bp_value.py --picks --go --max-credits 6   # run it (asks nothing; only run after the user approved)

Credits: 500 per month. GET /events is free; each GET /events/{id}/odds costs (markets in the call) x 1 region,
with FanDuel + up to 9 other books = 1 region. Every response's x-requests-used / x-requests-remaining /
x-requests-last headers are saved to odds_credits.json. The run stops before any call that would leave fewer
than 50 credits. Results go to value_<date>.json, the ledger (oa_fair, oa_ev) and the board on the next re-score.
Key: ODDS_API_KEY in .env (never printed).
"""

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp_learn  # noqa: E402
import bp_props  # noqa: E402
from bp_rotowire import norm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
CREDITS = os.path.join(HERE, "odds_credits.json")
MIN_REMAINING = 50
MIN_BOOKS = 3

# Checked against https://the-odds-api.com/sports-odds-data/betting-markets.html (NBA player props, Over/Under).
MARKET_KEYS = {"PTS": "player_points", "REB": "player_rebounds", "AST": "player_assists", "3PM": "player_threes",
               "PRA": "player_points_rebounds_assists", "P+R": "player_points_rebounds",
               "P+A": "player_points_assists", "R+A": "player_rebounds_assists"}
# FanDuel + 9 other US books = 1 region of cost. Paid-only keys (fanatics, williamhill_us) left out.
BOOKS = ["fanduel", "draftkings", "betmgm", "betrivers", "espnbet", "hardrockbet", "betonlineag", "bovada",
         "lowvig", "betus"]

# BettingPros team codes -> The Odds API team names.
TEAMS = {"ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets", "CHA": "Charlotte Hornets",
         "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
         "DET": "Detroit Pistons", "GSW": "Golden State Warriors", "GS": "Golden State Warriors",
         "HOU": "Houston Rockets", "IND": "Indiana Pacers", "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers",
         "MEM": "Memphis Grizzlies", "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
         "NOR": "New Orleans Pelicans", "NOP": "New Orleans Pelicans", "NO": "New Orleans Pelicans",
         "NYK": "New York Knicks", "NY": "New York Knicks", "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic",
         "PHI": "Philadelphia 76ers", "PHO": "Phoenix Suns", "PHX": "Phoenix Suns", "POR": "Portland Trail Blazers",
         "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs", "SA": "San Antonio Spurs", "TOR": "Toronto Raptors",
         "UTH": "Utah Jazz", "UTA": "Utah Jazz", "WAS": "Washington Wizards"}


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


# ------------------------------------------------------------------- picks
def load_picks(date, top):
    """Today's logged picks from the latest run (one row per prop), or the top N FanDuel props by Balanced."""
    if top:
        rows = [r for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv")))
                if r.get("price_is_fanduel") == "True" and r.get("balanced") not in (None, "")
                and r.get("noproj") != "True"]
        rows.sort(key=lambda r: -float(r["balanced"]))
        return [{"player": r["player"], "team": r["team"], "market": r["market"], "side": r["rec_side"],
                 "line": float(r["line"]), "odds": float(r["fd_odds_rec"]), "our_prob": _f(r.get("model_prob")),
                 "our_ev": _f(r.get("model_ev")), "event_id": r["event_id"], "tip": r.get("tip")} for r in rows[:top]]
    our_ev = {}
    try:
        for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
            our_ev[(r["player"], r["market"], r["rec_side"])] = _f(r.get("model_ev"))
    except OSError:
        pass
    led = [r for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date]
    if not led:
        sys.exit(f"No logged picks for {date}.")
    last = max(r["pulled_at"] for r in led)
    out, seen = [], set()
    for r in led:
        k = (r["player"], r["market"], r["side"])
        if r["pulled_at"] == last and k not in seen:
            seen.add(k)
            out.append({"player": r["player"], "team": r["team"], "market": r["market"], "side": r["side"],
                        "line": float(r["line"]), "odds": float(r["odds"]), "our_prob": _f(r.get("our_prob")),
                        "our_ev": our_ev.get(k), "event_id": r["event_id"], "tip": r.get("tip"), "pulled_at": last})
    return out


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except ValueError:
        return None


def game_teams(date):
    """{BettingPros event_id: set of team codes} from the day's CSV."""
    out = {}
    for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
        if r.get("event_id") and r.get("team"):
            out.setdefault(str(int(float(r["event_id"]))), set()).add(r["team"])
    return out


def plan(picks):
    games = {}
    for p in picks:
        games.setdefault(str(int(float(p["event_id"]))), set()).add(p["market"])
    return games, sum(len(m) for m in games.values())


# -------------------------------------------------------------------- math
def implied(a):
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def decimal(a):
    return 1 + (a / 100 if a > 0 else 100 / -a)


def devig(over, under):
    """(multiplicative, additive) fair probability of the over."""
    io, iu = implied(over), implied(under)
    return io / (io + iu), io - (io + iu - 1) / 2


def value_for(pick, event):
    """Fair prob and EV for one pick from one event-odds response."""
    mk, me = MARKET_KEYS[pick["market"]], norm(pick["player"])
    fd_line = fd_price = None
    fair_m, fair_a, other_lines = [], [], 0
    books = []
    for bk in event.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m.get("key") != mk:
                continue
            sides = {}
            for o in m.get("outcomes", []):
                if norm(o.get("description")) == me and o.get("name", "").lower() in ("over", "under"):
                    sides[o["name"].lower()] = (o.get("point"), o.get("price"))
            if "over" not in sides or "under" not in sides or sides["over"][0] != sides["under"][0]:
                continue
            point = sides["over"][0]
            pm, pa = devig(sides["over"][1], sides["under"][1])
            if pick["side"] == "under":
                pm, pa = 1 - pm, 1 - pa
            if bk["key"] == "fanduel":
                fd_line, fd_price = point, sides[pick["side"]][1]
                continue
            if point == pick["line"]:
                fair_m.append(pm)
                fair_a.append(pa)
                books.append(bk["key"])
            else:
                other_lines += 1
    price = fd_price if fd_price is not None and fd_line == pick["line"] else pick["odds"]
    out = {"books": len(books), "book_keys": books, "other_line_books": other_lines,
           "oa_fd_line": fd_line, "oa_fd_price": fd_price, "price_used": price,
           "fair_mult": None, "fair_add": None, "ev_mult": None, "ev_add": None}
    if len(fair_m) >= MIN_BOOKS:
        out["fair_mult"], out["fair_add"] = round(statistics.median(fair_m), 4), round(statistics.median(fair_a), 4)
        out["ev_mult"] = round(out["fair_mult"] * decimal(price) - 1, 4)
        out["ev_add"] = round(out["fair_add"] * decimal(price) - 1, 4)
    return out


def match_event(events, teams, tip):
    names = {TEAMS.get(t, t) for t in teams}
    best = None
    for e in events:
        if names & {e.get("home_team"), e.get("away_team")}:
            if tip and e.get("commence_time"):
                gap = abs((dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) -
                           dt.datetime.fromisoformat(tip.replace("Z", "+00:00"))).total_seconds())
                if gap > 6 * 3600:
                    continue
            best = e
    return best


# -------------------------------------------------------------------- save
def save(date, picks, results):
    path = os.path.join(HERE, f"value_{date}.json")
    try:
        with open(path) as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    for p, v in zip(picks, results):
        if v is not None:
            old[f"{p['player']}|{p['market']}|{p['side']}|{p['line']:g}"] = dict(v, at=bp_learn.utc_now())
    with open(path, "w") as f:
        json.dump(old, f, indent=1)
    # ledger: put the Odds API fair prob / EV on every logged row of these picks (all runs and strategies that day)
    led = bp_learn._read(bp_learn.LEDGER)
    for r in led:
        if r["date"] != date:
            continue
        v = old.get(f"{r['player']}|{r['market']}|{r['side']}|{float(r['line']):g}") if r.get("line") else None
        if v and v.get("fair_mult") is not None:
            r["oa_fair"], r["oa_ev"] = v["fair_mult"], v["ev_mult"]
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=bp_props.eastern_today())
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--picks", action="store_true", help="today's logged picks (latest run)")
    g.add_argument("--top", type=int, help="top N FanDuel props by Balanced")
    ap.add_argument("--go", action="store_true", help="make the calls (only after the user approved the credit cost)")
    ap.add_argument("--max-credits", type=int, help="required with --go: most credits this run may spend")
    args = ap.parse_args()

    picks = load_picks(args.date, args.top)
    games, cost = plan(picks)
    c = load_credits()
    print(f"{len(picks)} picks in {len(games)} games. Credit cost: " +
          " + ".join(f"{len(m)} ({', '.join(sorted(m))})" for m in games.values()) + f" = {cost} "
          f"(markets x 1 region; FanDuel + {len(BOOKS) - 1} books). /events is free.")
    print(f"Credits: {c.get('remaining') if c.get('remaining') is not None else 'unknown (no call yet)'} remaining this month; "
          f"stop at {MIN_REMAINING}.")
    if not args.go:
        print("Plan only: no call made. Re-run with --go --max-credits N once approved.")
        return
    if args.max_credits is None or cost > args.max_credits:
        sys.exit(f"Stopped: this run costs {cost}; pass --max-credits {cost} (or more) once approved.")
    bp_props.load_dotenv()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("No ODDS_API_KEY in .env.")
    guard(0)
    events = get("/events", {"dateFormat": "iso"}, key, expect_cost=0)
    teams = game_teams(args.date)
    by_game = {}
    for gid, mkts in games.items():
        tip = next((p.get("tip") for p in picks if str(int(float(p["event_id"]))) == gid), None)
        e = match_event(events, teams.get(gid, set()), tip)
        if not e:
            print(f"  No Odds API event for game {gid} ({', '.join(sorted(teams.get(gid, [])))}); skipped, no credits used.")
            continue
        n = len(mkts)
        if not guard(n, hard=False):
            break
        by_game[gid] = get(f"/events/{e['id']}/odds", {"markets": ",".join(MARKET_KEYS[m] for m in sorted(mkts)),
                                                       "bookmakers": ",".join(BOOKS), "oddsFormat": "american",
                                                       "dateFormat": "iso"}, key, expect_cost=n)
    results = []
    print(f"\n{'player':22} {'mkt':4} {'side':5} {'FD line/price':14} {'fair (mult/add)':16} {'EV mult/add':14} {'our EV':7} books  other-line")
    for p in picks:
        ev = by_game.get(str(int(float(p["event_id"]))))
        v = value_for(p, ev) if ev else None
        results.append(v)
        if v is None:
            print(f"{p['player'][:22]:22} {p['market']:4} {p['side']:5} (no Odds API data)")
            continue
        fd = f"{v['oa_fd_line'] if v['oa_fd_line'] is not None else p['line']:g} {v['price_used']:+g}"
        fair = "-" if v["fair_mult"] is None else f"{v['fair_mult']:.3f}/{v['fair_add']:.3f}"
        evs = "-" if v["ev_mult"] is None else f"{100 * v['ev_mult']:+.1f}%/{100 * v['ev_add']:+.1f}%"
        ours = "-" if p.get("our_ev") is None else f"{100 * p['our_ev']:+.1f}%"
        diff = f"{v['other_line_books']} on another line" if v["other_line_books"] else ""
        warn = "" if v["oa_fd_line"] in (None, p["line"]) else f"  FanDuel now {v['oa_fd_line']:g}"
        print(f"{p['player'][:22]:22} {p['market']:4} {p['side']:5} {fd:14} {fair:16} {evs:14} {ours:7} {v['books']:>5}  {diff}{warn}"
              + ("" if v["books"] >= MIN_BOOKS else f"  (needs {MIN_BOOKS}+ books at FanDuel's line)"))
    path = save(args.date, picks, results)
    print(f"\nSaved {path}; ledger updated. Re-score (python3 bp_props.py --from-csv) to show it on the board.")
    print(f"Credits remaining: {load_credits().get('remaining')}")


if __name__ == "__main__":
    main()
