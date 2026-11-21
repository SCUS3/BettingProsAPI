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
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp_learn  # noqa: E402
import bp_props  # noqa: E402
import bp_unabated as U  # noqa: E402
from bp_rotowire import norm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
CREDITS = os.path.join(HERE, "odds_credits.json")
MIN_REMAINING = 50
MIN_BOOKS = 3
DAILY_CREDITS = 8          # aim per day; --top N is cut to the games with the most shortlist plays
BET_MIN_EV = 0.02          # BET: market EV (multiplicative) at FanDuel's price, and
BET_MIN_BOOKS = 4          #      The Odds API: at least 4 other books at FanDuel's line (Unabated: U.MIN_SOURCES = 3)
BET_KELLY_MAX = 2.5        # u; quarter-Kelly on the market's fair probability, once BET CLV is proven
BET_KELLY_CAP = 1.25       # u; until 50 graded BETs show positive average CLV

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
        now = bp_learn.utc_now()
        rows = [r for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv")))
                if r.get("price_is_fanduel") == "True" and r.get("balanced") not in (None, "")
                and r.get("noproj") != "True" and r.get("placeholder") != "True"
                and r.get("stale_price") != "True" and (not r.get("tip") or r["tip"] > now)]
        rows.sort(key=lambda r: -float(r["balanced"]))
        return [{"player": r["player"], "team": r["team"], "market": r["market"], "side": r["rec_side"],
                 "line": float(r["line"]), "odds": float(r["fd_odds_rec"]), "our_prob": _f(r.get("model_prob")),
                 "our_ev": _f(r.get("model_ev")), "event_id": r["event_id"], "tip": r.get("tip"),
                 "rank": i, "row": r} for i, r in enumerate(rows[:top], 1)]
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


def report_dropped(dropped, full_cost, budget, n_games):
    print(f"Odds API: the full set would cost {full_cost} credits (over {budget}). Keeping the {n_games} games with the most "
          f"shortlist plays; not checked ({len(dropped)}): " +
          "; ".join(f"#{p.get('rank', '?')} {p['player']} {p['market']} {p['side']} {p['line']:g}" for p in dropped))


def gid(p):
    return str(int(float(p["event_id"])))


def plan(picks, budget=None):
    """{game: markets}, credit cost, kept picks, dropped picks. Over budget: keep the games with the most
    shortlist plays (ties: higher total Balanced), adding a game only if its markets still fit."""
    games = {}
    for p in picks:
        games.setdefault(gid(p), set()).add(p["market"])
    cost = sum(len(m) for m in games.values())
    if budget is None or cost <= budget:
        return games, cost, picks, []
    bal = lambda g: sum(float((p.get("row") or {}).get("balanced") or 0) for p in picks if gid(p) == g)
    order = sorted(games, key=lambda g: (-sum(gid(p) == g for p in picks), -bal(g)))
    keep, spent = set(), 0
    for g in order:
        if spent + len(games[g]) <= budget:
            keep.add(g)
            spent += len(games[g])
    return ({g: games[g] for g in keep}, spent, [p for p in picks if gid(p) in keep], [p for p in picks if gid(p) not in keep])


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
def qualifies(v):
    """BET: market EV >= +2% at FanDuel's line, with 3+ sources (Unabated) or 4+ books (The Odds API)."""
    if not v or v.get("ev_mult") is None or v["ev_mult"] < BET_MIN_EV:
        return False
    if v.get("fair_src") == "Unabated":
        return v.get("fair_method") == "line" or (v.get("books") or 0) >= U.MIN_SOURCES
    return (v.get("books") or 0) >= BET_MIN_BOOKS


def bet_cap():
    """(cap in u, note). 1.25u until 50 graded BETs show positive average CLV, then 2.5u."""
    n, avg, n_clv = bp_learn.bet_clv_status()
    if n >= bp_learn.BET_CAP_MIN_BETS and avg is not None and avg > 0:
        return BET_KELLY_MAX, f"BET stakes ¼K, cap {BET_KELLY_MAX}u: {n} graded BETs, average CLV {100 * avg:+.1f}%."
    why = f"{n} of {bp_learn.BET_CAP_MIN_BETS} graded BETs" if n < bp_learn.BET_CAP_MIN_BETS else \
        f"average CLV {'n/a' if avg is None else f'{100 * avg:+.1f}%'} on {n_clv} BETs"
    return BET_KELLY_CAP, f"BET stakes ¼K, cap {BET_KELLY_CAP}u until 50 graded BETs show positive average CLV ({why})."


def bet_kelly(fair, american, cap=None):
    """Quarter-Kelly stake in units (1u = 1% of bankroll) on the market's fair probability, capped."""
    cap = bet_cap()[0] if cap is None else cap
    b = decimal(american) - 1
    f = (fair * (b + 1) - 1) / b
    return round(min(max(f / 4 * 100, 0.0), cap), 2)


def decide(date, shortlist, checked, results):
    """BET = shortlist play with market EV >= +2% (multiplicative) and 4+ other books at FanDuel's line.
    Every other shortlist play (including ones dropped for credits or without data) is 'watch'.
    Logged to the ledger as strategies 'bet' / 'watch' with the 2 PM pull's time."""
    res = {(p["player"], p["market"], p["side"]): v for p, v in zip(checked, results)}
    rows, bets = [], []
    for p in shortlist:
        v = res.get((p["player"], p["market"], p["side"]))
        is_bet = qualifies(v)
        r = p.get("row") or {}
        price = v["price_used"] if v else p["odds"]
        stake = bet_kelly(v["fair_mult"], price) if is_bet else 0.0
        if v is not None:
            v.update(decision="BET" if is_bet else "watch", bet_kelly=stake)
        p["decision"] = "BET" if is_bet else "watch"
        rows.append({"date": date, "pulled_at": r.get("pulled_at"), "tip": r.get("tip"), "strategy": "bet" if is_bet else "watch",
                     "rank": p.get("rank"), "player": p["player"], "team": p["team"], "market": p["market"], "side": p["side"],
                     "line": p["line"], "odds": price, "gap": r.get("pct_gap"), "placeholder": r.get("placeholder"),
                     "score": r.get("score"), "balanced": r.get("balanced"), "learned": r.get("learned"),
                     "our_prob": r.get("model_prob"), "nv_prob": v.get("fair_mult") if v else None, "kelly_u": stake,
                     "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id"),
                     "oa_fair": v.get("fair_mult") if v else None, "oa_ev": v.get("ev_mult") if v else None,
                     "fair_src": v.get("fair_src") if v else None})
        if is_bet:
            bets.append(f"{p['player']} {p['market']} {p['side']} {p['line']:g} {price:+g}: mkt EV {100 * v['ev_mult']:+.1f}% "
                        f"({v['fair_src']}" + (f", {v['books']} books" if v.get("books") else "") + f"), {stake}u ¼K")
    bp_learn.log_value_decisions(date, rows)
    print(f"\nBET ({len(bets)}): " + ("; ".join(bets) if bets else "none today") +
          f". Watch: {len(shortlist) - len(bets)}. Logged to the ledger (strategies 'bet' / 'watch').")


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
            r["oa_fair"], r["oa_ev"], r["fair_src"] = v["fair_mult"], v["ev_mult"], v.get("fair_src")
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    return path


# ------------------------------------------------------------ daily board flow
def board_rows(date):
    """Every FanDuel main line on the day's board for games still to tip."""
    now = bp_learn.utc_now()
    return [r for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv")))
            if r.get("price_is_fanduel") == "True" and r.get("stale_price") != "True" and r.get("line")
            and (not r.get("tip") or r["tip"] > now)]


def side_picks(r):
    """Both sides of one FanDuel line as picks (FanDuel's price per side when we have it)."""
    out = []
    for side in ("over", "under"):
        odds = _f(r.get("fd_odds_rec")) if side == r.get("rec_side") else _f(r.get(f"{side}_odds"))
        out.append({"player": r["player"], "team": r.get("team"), "market": r["market"], "side": side,
                    "line": float(r["line"]), "odds": odds, "event_id": r.get("event_id"), "tip": r.get("tip"), "row": r})
    return out


def side_context(r, side):
    """Form (recent hit rate for this side) and DvP (1 = softest) for a bet side."""
    perf = bp_props._perf(r)
    rate = bp_props.form_score(bp_props._form_side(perf, side))[0] if perf else (
        _f(r.get("form_rate")) if side == r.get("rec_side") else None)
    return rate, _f(r.get("dvp_softness"))


def proj_side(r):
    """BettingPros' side at FanDuel's line, or None with no real projection."""
    return None if r.get("noproj") == "True" or not r.get("bp_proj") else r.get("rec_side")


def line_key(player, market, line):
    return f"{player}|{market}|{float(line):g}"


def extra_rows(date, rows, data):
    """FanDuel lines on Unabated's board that the BettingPros pull didn't carry, as no-projection rows mapped to
    BettingPros' game ids by team (so they can be graded)."""
    have = {(norm(r["player"]), r["market"]) for r in rows}
    ev_teams = {}
    for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
        if r.get("event_id") and r.get("team"):
            ev_teams.setdefault(str(r["event_id"]).split(".")[0], set()).add(U.TEAM_FIX.get(r["team"], r["team"]))
    pulled = rows[0].get("pulled_at") if rows else bp_learn.utc_now()
    out = []
    for x in U.fd_lines(data):
        if (norm(x["player"]), x["market"]) in have:
            continue
        ev = next((e for e, t in ev_teams.items() if t & set(x["teams"])), None)
        side = "over" if x["over_odds"] >= x["under_odds"] else "under"
        out.append({"player": x["player"], "team": "", "market": x["market"], "line": x["line"], "rec_side": side,
                    "fd_odds_rec": x[side + "_odds"], "over_odds": x["over_odds"], "under_odds": x["under_odds"],
                    "price_is_fanduel": "True", "noproj": "True", "bp_proj": "", "event_id": ev or "", "tip": x["tip"],
                    "pulled_at": pulled, "source_row": "unabated"})
    return out


def run_board(args):
    if args.go and not args.board_file and U.key():
        try:                                       # discovery refresh (free; no odds interval): current player ids
            U.discover()
            print("Unabated discovery refreshed (players, bet types, sources).")
        except (U.Unavailable, RuntimeError) as e:
            print(f"Unabated discovery refresh failed ({e}); using the saved discovery.")
    rows = board_rows(args.date)
    shortlist = load_picks(args.date, args.top or 10)
    ua_ok, ua_why = U.usable()
    games, cost, _, _ = plan(shortlist, args.budget)
    print(f"Board: {len(rows)} FanDuel lines in {len({r.get('event_id') for r in rows})} games still to tip; "
          f"shortlist {len(shortlist)} (top by Balanced, real projections).")
    if ua_ok:
        print(f"1. Unabated: one props odds call for the full board, all {len(U.load(U.DISCOVERY, {}).get('bettypes', {}))} markets "
              f"(free; max 1 per 5 s). Prices both sides of every FanDuel line ({U.MIN_SOURCES}+ sources at FanDuel's line).")
    else:
        print(f"1. Unabated not used: {ua_why}.")
    print(f"2. The Odds API, only for shortlist plays Unabated can't price: at most {cost} credits "
          f"(exact after the Unabated call; needs --max-credits). Credits remaining: {load_credits().get('remaining')}.")
    if not args.go:
        print("Plan only: no call made. Re-run with --go (and --max-credits N for the fallback) once approved.")
        return
    ua = {}
    if ua_ok:
        try:
            if args.board_file:                    # a saved board (no call): price it as it was when fetched
                data = json.load(open(args.board_file))
                print(f"  Using the saved Unabated board {args.board_file} (no call).")
            else:
                data = U.fetch_board(["PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"])
            extra = extra_rows(args.date, rows, data)
            if extra:
                print(f"  {len(extra)} FanDuel lines on Unabated's board that BettingPros doesn't carry: priced too (no projection).")
                rows += extra
            cands = [sp for r in rows for sp in side_picks(r)]
            ua = U.price_board(cands, data, U.load(U.DISCOVERY, {}))
            n_lines = sum(1 for r in rows if any((ua.get((r["player"], r["market"], sd)) or {}).get("fair_mult") is not None
                                                  for sd in ("over", "under")))
            print(f"  Unabated priced {n_lines} of {len(rows)} FanDuel lines ({U.MIN_SOURCES}+ sources at FanDuel's line).")
        except U.Unavailable as e:
            print(f"  Unabated key stopped working ({e}). Switched to The Odds API until bp_unabated.py --discover passes.")
        except RuntimeError as e:
            print(f"  Unabated error ({e}); The Odds API only for the shortlist.")
    k = lambda p: (p["player"], p["market"], p["side"])
    need = [p for p in shortlist if not (ua.get(k(p)) and ua[k(p)].get("fair_mult") is not None)]
    oa = oa_fallback(args, need)
    # every priced line: both sides, best qualifying side is the BET
    entries, bets = {}, []
    by_row = {(r["player"], r["market"]): r for r in rows}
    for r in rows:
        sides = {sd: ua.get((r["player"], r["market"], sd)) for sd in ("over", "under")}
        for sd in ("over", "under"):
            if (r["player"], r["market"], sd) in oa:
                sides[sd] = oa[(r["player"], r["market"], sd)]
        sides = {sd: v for sd, v in sides.items() if v}
        if not sides:
            continue
        e = {"sides": sides, "at": bp_learn.utc_now()}
        q = [(sd, v) for sd, v in sides.items() if qualifies(v)]
        if q:
            sd, v = max(q, key=lambda x: x[1]["ev_mult"])
            bets.append((r, sd, v))
        entries[line_key(r["player"], r["market"], r["line"])] = e
    ledger, lines_out = [], []
    cap, cap_note = bet_cap()
    for r, sd, v in sorted(bets, key=lambda x: -x[2]["ev_mult"]):
        ps = proj_side(r)
        tag = "+EV + proj agrees" if ps == sd else "+EV only"
        form, dvp = side_context(r, sd)
        stake = bet_kelly(v["fair_mult"], v["price_used"], cap)
        e = entries[line_key(r["player"], r["market"], r["line"])]
        e.update(decision="BET", bet_side=sd, tag=tag, bet_kelly=stake, bet_price=v["price_used"], bet_ev=v["ev_mult"],
                 form=form, dvp=dvp, proj_side=ps)
        mp = _f(r.get("model_prob"))
        ledger.append(value_row(args.date, r, "bet_agree" if ps == sd else "bet_only", sd, v, stake,
                                None if mp is None or ps is None else (mp if ps == sd else 1 - mp)))
        lines_out.append(f"  BET [{tag}] {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g}: mkt EV "
                         f"{100 * v['ev_mult']:+.1f}%, fair {v['fair_mult']:.3f} ({v['fair_src']}, {v.get('books')} "
                         f"{'sources' if v['fair_src'] == 'Unabated' else 'books'}{', exchange' if v.get('exchange') else ''}); "
                         f"BP proj {'agrees' if ps == sd else ('says ' + ps if ps else 'none')} ({r.get('bp_proj') or '-'} vs {float(r['line']):g}); "
                         f"form {'-' if form is None else str(round(100 * form)) + '%'}; DvP {fmtn(dvp)}; stake {stake}u ¼K")
    bet_keys = {(r["player"], r["market"]) for r, _, _ in bets}
    for p in shortlist:
        if (p["player"], p["market"]) in bet_keys:
            continue
        r = by_row.get((p["player"], p["market"])) or p.get("row") or {}
        e = entries.setdefault(line_key(p["player"], p["market"], p["line"]), {"sides": {}, "at": bp_learn.utc_now()})
        e.update(decision="watch")
        ledger.append(value_row(args.date, r, "watch", p["side"], e["sides"].get(p["side"]), 0.0, _f(r.get("model_prob"))))
    bp_learn.log_value_decisions(args.date, ledger)
    path = os.path.join(HERE, f"value_{args.date}.json")
    old = {}
    try:
        old = json.load(open(path))
    except (OSError, ValueError):
        pass
    old.update(entries)
    old["_meta"] = {"bet_cap": cap, "bet_cap_note": cap_note}
    json.dump(old, open(path, "w"), indent=1)
    n_agree = sum(1 for x in ledger if x["strategy"] == "bet_agree")
    print(f"\nBET: {len(bets)} ({n_agree} +EV + proj agrees, {len(bets) - n_agree} +EV only). "
          f"Watch: {sum(1 for x in ledger if x['strategy'] == 'watch')} of the shortlist.")
    print("\n".join(lines_out) if lines_out else "  No line reached +2% with enough sources.")
    print(cap_note)
    print(f"Saved {path}; ledger updated. Re-score (python3 bp_props.py --from-csv) to show it on the board. "
          f"Odds API credits remaining: {load_credits().get('remaining')}")


def fmtn(v):
    return "-" if v is None else f"{v:g}"


def value_row(date, r, strategy, side, v, stake, our_prob):
    v = v or {}
    return {"date": date, "pulled_at": r.get("pulled_at"), "tip": r.get("tip"), "strategy": strategy, "rank": None,
            "player": r.get("player"), "team": r.get("team"), "market": r.get("market"), "side": side,
            "line": r.get("line"), "odds": v.get("price_used") or r.get("fd_odds_rec"), "gap": r.get("pct_gap"),
            "placeholder": r.get("placeholder"), "score": r.get("score"), "balanced": r.get("balanced"),
            "learned": r.get("learned"), "our_prob": our_prob, "nv_prob": v.get("fair_mult"), "kelly_u": stake,
            "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id"),
            "oa_fair": v.get("fair_mult"), "oa_ev": v.get("ev_mult"), "fair_src": v.get("fair_src"),
            "sources": v.get("books")}


def oa_fallback(args, need):
    """The Odds API for shortlist plays Unabated couldn't price, within the credit aim and --max-credits."""
    if not need:
        return {}
    cap = min(args.budget, args.max_credits if args.max_credits is not None else 0)
    games, cost, picks, dropped = plan(need, cap)
    full = plan(need)[1]
    print(f"  The Odds API fallback: {len(need)} shortlist plays Unabated couldn't price, {full} credits for all"
          + (f"; approved {args.max_credits}" if args.max_credits is not None else "; none approved (pass --max-credits)"))
    if dropped:
        report_dropped(dropped, full, cap, len(games))
    if not games:
        return {}
    bp_props.load_dotenv()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("  No ODDS_API_KEY in .env; fallback skipped.")
        return {}
    guard(0)
    events = get("/events", {"dateFormat": "iso"}, key, expect_cost=0)
    teams = game_teams(args.date)
    by_game = {}
    for g, mkts in games.items():
        tip = next((p.get("tip") for p in picks if gid(p) == g), None)
        e = match_event(events, teams.get(g, set()), tip)
        if not e:
            print(f"  No Odds API event for game {g}; skipped, no credits used.")
            continue
        if not guard(len(mkts), hard=False):
            break
        by_game[g] = get(f"/events/{e['id']}/odds", {"markets": ",".join(MARKET_KEYS[m] for m in sorted(mkts)),
                                                    "bookmakers": ",".join(BOOKS), "oddsFormat": "american",
                                                    "dateFormat": "iso"}, key, expect_cost=len(mkts))
    out = {}
    for p in picks:
        ev = by_game.get(gid(p))
        if not ev:
            continue
        for sd in ("over", "under"):
            q = dict(p, side=sd, odds=p["odds"] if sd == p["side"] else _f((p.get("row") or {}).get(f"{sd}_odds")))
            if q["odds"] is None:
                continue
            v = value_for(q, ev)
            v["fair_src"] = "OddsAPI"
            out[(p["player"], p["market"], sd)] = v
    return out


def run_close(args):
    """6:30 PM: one Unabated call to log CLV for today's BETs still to tip; flag any whose market EV fell below 0."""
    now = bp_learn.utc_now()
    led = bp_learn._read(bp_learn.LEDGER)
    bets = [r for r in led if r["date"] == args.date and r["strategy"] in bp_learn.BET_KEYS and r.get("tip", "") > now]
    if not bets:
        sys.exit("No BETs for games still to tip.")
    ok, why = U.usable()
    print(f"{len(bets)} BETs still to tip. Closing check: one Unabated props odds call (free; max 1 per 5 s)."
          + ("" if ok else f" Unabated not usable: {why}."))
    if not args.go or not ok:
        if not args.go:
            print("Plan only: no call made. Re-run with --close --go once approved.")
        return
    picks = [{"player": r["player"], "market": r["market"], "side": r["side"], "line": float(r["line"]),
              "odds": float(r["odds"]), "event_id": r["event_id"]} for r in bets]
    try:
        res, _ = U.fair_prices(picks)
    except U.Unavailable as e:
        sys.exit(f"Unabated key stopped working ({e}); no closing check.")
    at = bp_learn.utc_now()
    flagged = []
    print(f"\n{'player':22} {'mkt':4} {'side':5} {'bet':10} {'fair now':8} {'CLV (EV)':9} {'mkt EV now':10} FD now")
    for r, p in zip(bets, picks):
        v = res.get((p["player"], p["market"], p["side"])) or {}
        f = v.get("fair_mult")
        r["close_at"] = at
        r["close_fd_line"] = v.get("oa_fd_line")
        if f is None:
            print(f"{r['player'][:22]:22} {r['market']:4} {r['side']:5} {p['line']:g} {p['odds']:+g}  no closing price ({v.get('note') or 'not on the board'})")
            continue
        r["close_fair"] = f
        r["clv_ev"] = round(f * decimal(p["odds"]) - 1, 4)           # the bet we took, priced at the 6:30 fair
        r["close_mkt_ev"] = v.get("ev_mult")                           # FanDuel's current price at our line, if still there
        drop = v.get("ev_mult") is not None and v["ev_mult"] < 0
        if drop:
            flagged.append(r)
        fd_now = "-" if v.get("oa_fd_line") is None else f"{v['oa_fd_line']:g} {v.get('oa_fd_price'):+g}"
        ev_now = "-" if v.get("ev_mult") is None else f"{100 * v['ev_mult']:+.1f}%"
        print(f"{r['player'][:22]:22} {r['market']:4} {r['side']:5} {p['line']:g} {p['odds']:+g}  {f:.3f}    "
              f"{100 * r['clv_ev']:+.1f}%    {ev_now:10} {fd_now}" + ("   FLAG: market EV now below 0" if drop else ""))
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    path = os.path.join(HERE, f"value_{args.date}.json")
    try:
        val = json.load(open(path))
    except (OSError, ValueError):
        val = {}
    for r in bets:
        e = val.get(line_key(r["player"], r["market"], r["line"]))
        if e is not None and r.get("close_fair") not in (None, ""):
            e["close"] = {"fair": r["close_fair"], "clv_ev": r["clv_ev"], "mkt_ev": r["close_mkt_ev"], "at": at}
    json.dump(val, open(path, "w"), indent=1)
    print(f"\nCLV logged for {sum(1 for r in bets if r.get('close_fair') not in (None, ''))} of {len(bets)} BETs. "
          + ("FLAGGED (market EV now < 0): " + "; ".join(f"{r['player']} {r['market']} {r['side']} {float(r['line']):g}" for r in flagged)
             if flagged else "No BET's market EV fell below 0."))


# --------------------------------------------------------------------- watch
WATCH_EVERY = 20 * 60        # seconds between scans
WATCH_START_ET = 12          # noon ET


def _et_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return dt.datetime.now()


def notify(msg):
    """Terminal + log line (ET time-stamped), and a macOS notification when available."""
    print(f"[{_et_now():%H:%M}] {msg}", flush=True)
    try:
        import subprocess
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg[:220])} with title "NBA props"'],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def watch_scan(date, state):
    """One free Unabated scan: new BETs (logged) and logged BETs whose market EV fell below 0 (or line moved).
    Returns the number of games still to tip."""
    data = U.fetch_board(["PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"])
    d = U.load(U.DISCOVERY, {})
    have_csv = os.path.exists(os.path.join(HERE, f"bp_props_{date}.csv"))
    rows = board_rows(date) if have_csv else []
    rows += extra_rows(date, rows, data) if have_csv else [
        {"player": x["player"], "team": "", "market": x["market"], "line": x["line"], "rec_side": "over",
         "fd_odds_rec": x["over_odds"], "over_odds": x["over_odds"], "under_odds": x["under_odds"], "price_is_fanduel": "True",
         "noproj": "True", "bp_proj": "", "event_id": "", "tip": x["tip"], "pulled_at": bp_learn.utc_now()} for x in U.fd_lines(data, d)]
    ua = U.price_board([sp for r in rows for sp in side_picks(r)], data, d)
    now = bp_learn.utc_now()
    led = bp_learn._read(bp_learn.LEDGER)
    logged = {f"{r['player']}|{r['market']}|{r['side']}|{float(r['line']):g}": r for r in led
              if r["date"] == date and r["strategy"] in bp_learn.BET_KEYS and r.get("tip", "") > now}
    cap = bet_cap()[0]
    new_rows = []
    for r in rows:
        if r.get("tip") and str(r["tip"]) <= now:        # game already tipped: never log it
            continue
        for sd in ("over", "under"):
            v = ua.get((r["player"], r["market"], sd))
            k = f"{r['player']}|{r['market']}|{sd}|{float(r['line']):g}"
            if not qualifies(v) or k in logged or k in state["alerted"]:
                continue
            ps = proj_side(r)
            tag = "+EV + proj agrees" if ps == sd else "+EV only"
            stake = bet_kelly(v["fair_mult"], v["price_used"], cap)
            form, dvp = side_context(r, sd)
            mp = _f(r.get("model_prob"))
            row = value_row(date, dict(r, pulled_at=now), "bet_agree" if ps == sd else "bet_only", sd, v, stake,
                            None if mp is None or ps is None else (mp if ps == sd else 1 - mp))
            row["line"] = r["line"]
            new_rows.append(row)
            state["alerted"][k] = v["ev_mult"]
            notify(f"NEW BET [{tag}] {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g}: mkt EV "
                   f"{100 * v['ev_mult']:+.1f}% ({v.get('books')} sources{', exch' if v.get('exchange') else ''}); "
                   f"form {'-' if form is None else str(round(100 * form)) + '%'}, DvP {fmtn(dvp)}; stake {stake}u ¼K; tip {str(r.get('tip'))[11:16]}Z")
    if new_rows:
        led = [r for r in led] + new_rows
        bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    # logged BETs: market EV now, at our line
    picks = [{"player": r["player"], "market": r["market"], "side": r["side"], "line": float(r["line"]), "odds": float(r["odds"])}
             for r in logged.values()]
    now_v = U.price_board(picks, data, d) if picks else {}
    for k, r in logged.items():
        if k in state["dropped"]:
            continue
        v = now_v.get((r["player"], r["market"], r["side"])) or {}
        fd_line = v.get("oa_fd_line")
        if fd_line is not None and fd_line != float(r["line"]):
            state["dropped"].append(k)
            notify(f"BET LINE MOVED: {r['player']} {r['market']} {r['side']} {float(r['line']):g} -> FanDuel now {fd_line:g}")
        elif v.get("ev_mult") is not None and v["ev_mult"] < 0:
            state["dropped"].append(k)
            notify(f"BET EV BELOW 0: {r['player']} {r['market']} {r['side']} {float(r['line']):g}: mkt EV now "
                   f"{100 * v['ev_mult']:+.1f}% at {v['price_used']:+g} (bet {float(r['odds']):+g})")
    tips = {x["tip"] for x in U.fd_lines(data, d) if x.get("tip") and x["tip"] > now}
    return len(tips)


def run_watch(args):
    """Free Unabated scan every 20 minutes from noon ET until the last game's tip; alerts only on new BETs and
    on logged BETs whose market EV fell below 0 (or whose FanDuel line moved). Start it once a day."""
    ok, why = U.usable()
    if not ok:
        sys.exit(f"Unabated not usable: {why}")
    path = os.path.join(HERE, f"watch_{args.date}.json")
    try:
        state = json.load(open(path))
    except (OSError, ValueError):
        state = {"alerted": {}, "dropped": []}
    print(f"Watching {args.date}: one free Unabated scan every {args.every} min from {WATCH_START_ET}:00 ET until the last tip. "
          f"Alerts: new BETs (logged) and BETs whose market EV fell below 0.", flush=True)
    try:                                                  # keep the Mac awake while watching (sleep skipped scans)
        import subprocess
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        print("caffeinate unavailable: scans pause if the computer sleeps.", flush=True)
    try:
        U.discover()
    except (U.Unavailable, RuntimeError) as e:
        print(f"Discovery refresh failed ({e}); using the saved one.", flush=True)
    while True:
        et = _et_now()
        if et.hour < WATCH_START_ET:
            wait = ((WATCH_START_ET - et.hour) * 3600 - et.minute * 60 - et.second)
            print(f"Waiting {wait // 60} min until {WATCH_START_ET}:00 ET.", flush=True)
            time.sleep(max(wait, 1))
            continue
        try:
            left = watch_scan(args.date, state)
        except U.Unavailable as e:
            notify(f"Watch stopped: Unabated key stopped working ({e}).")
            return
        except Exception as e:
            print(f"{_et_now():%H:%M} scan failed ({e}); next in {args.every} min.", flush=True)
            left = 1
        json.dump(state, open(path, "w"), indent=1)
        print(f"{_et_now():%H:%M} ET scan done; {left} tip times still ahead.", flush=True)
        if left == 0:
            print("All games have tipped. Watch done.", flush=True)
            return
        time.sleep(args.every * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=bp_props.eastern_today())
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--picks", action="store_true", help="today's logged picks (latest run)")
    g.add_argument("--top", type=int, help="top N FanDuel props by Balanced")
    g.add_argument("--board", action="store_true", help="daily: one Unabated call prices every FanDuel line; BETs; "
                                                        "The Odds API only for the shortlist Unabated can't price")
    g.add_argument("--close", action="store_true", help="6:30 PM: one Unabated call logs CLV for today's BETs")
    g.add_argument("--watch", action="store_true", help="free Unabated scan every 20 min from noon ET to the last tip; "
                                                        "alerts on new BETs and BETs whose market EV fell below 0")
    ap.add_argument("--go", action="store_true", help="make the calls (only after the user approved the credit cost)")
    ap.add_argument("--max-credits", type=int, help="required with --go: most credits this run may spend")
    ap.add_argument("--budget", type=int, default=DAILY_CREDITS,
                    help=f"credit aim for the run (default {DAILY_CREDITS}); --top is cut to fit")
    ap.add_argument("--shortlist", type=int, default=10, help="--board: shortlist size (top N by Balanced)")
    ap.add_argument("--every", type=int, default=WATCH_EVERY // 60, help="--watch: minutes between scans")
    ap.add_argument("--board-file", help="--board: price a saved Unabated board JSON instead of calling")
    args = ap.parse_args()
    if args.board:
        args.top = args.shortlist
        return run_board(args)
    if args.close:
        return run_close(args)
    if args.watch:
        return run_watch(args)

    shortlist = load_picks(args.date, args.top)
    ua_ok, ua_why = U.usable()
    c = load_credits()
    games, cost, _, dropped = plan(shortlist, args.budget)
    print(f"{len(shortlist)} plays in {len(plan(shortlist)[0])} games.")
    if ua_ok:
        print("Fair source 1: Unabated, one props odds call for the whole board (no credits; max 1 per 5 s): " +
              ("the Unabated Line." if U.method() == "line" else
               f"median of devigged books + exchanges at FanDuel's line, {U.MIN_SOURCES}+ sources (no pick'em apps)."))
        print(f"Fair source 2 (fallback, only plays Unabated doesn't price): The Odds API, at most {cost} credits "
              f"(worst case; the exact cost is known after the Unabated call).")
    else:
        print(f"Unabated not used: {ua_why}. Fair source: The Odds API.")
        print("Odds API cost: " + " + ".join(f"{len(m)} ({', '.join(sorted(m))})" for m in games.values()) + f" = {cost} credits "
              f"(markets x 1 region; FanDuel + {len(BOOKS) - 1} books). /events is free.")
    if dropped and not ua_ok:
        report_dropped(dropped, plan(shortlist)[1], args.budget, len(games))
    print(f"Credits: {c.get('remaining') if c.get('remaining') is not None else 'unknown (no call yet)'} remaining this month; "
          f"stop at {MIN_REMAINING}.")
    if not args.go:
        print("Plan only: no call made. Re-run with --go --max-credits N once approved.")
        return

    # 1. Unabated for every play (if usable); a 401/403 switches to The Odds API for this and later runs.
    ua = {}
    if ua_ok:
        try:
            ua, _ = U.fair_prices(shortlist)
            got = sum(1 for v in ua.values() if v.get("fair_mult") is not None)
            print(f"  Unabated: fair price for {got} of {len(shortlist)} plays.")
        except U.Unavailable as e:
            print(f"  Unabated key stopped working ({e}). Switched to The Odds API for this run and later ones "
                  f"until bp_unabated.py --discover passes again.")
        except RuntimeError as e:
            print(f"  Unabated error ({e}); using The Odds API for this run.")
    k = lambda p: (p["player"], p["market"], p["side"])
    need = [p for p in shortlist if not (ua.get(k(p)) and ua[k(p)].get("fair_mult") is not None)]

    # 2. The Odds API only for plays Unabated didn't price, within the credit aim and the approved cap.
    cap = min(args.budget, args.max_credits if args.max_credits is not None else 0)
    games, cost, picks, dropped = plan(need, cap)
    if dropped:
        report_dropped(dropped, plan(need)[1], cap, len(games))
    by_game = {}
    if need and games:
        if args.max_credits is None:
            sys.exit(f"Stopped: the fallback costs {cost}; pass --max-credits {cost} (or more) once approved.")
        bp_props.load_dotenv()
        key = os.environ.get("ODDS_API_KEY")
        if not key:
            sys.exit("No ODDS_API_KEY in .env.")
        print(f"The Odds API for {len(picks)} plays in {len(games)} games: {cost} credits.")
        guard(0)
        events = get("/events", {"dateFormat": "iso"}, key, expect_cost=0)
        teams = game_teams(args.date)
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
    checked = {k(p) for p in picks}
    picks = [p for p in shortlist if k(p) in checked or (ua.get(k(p)) and ua[k(p)].get("fair_mult") is not None)]
    dropped = [p for p in shortlist if p not in picks]
    results = []
    print(f"\n{'player':22} {'mkt':4} {'side':5} {'FD line/price':14} {'fair':12} {'EV':8} {'our EV':7} {'src':9} books  note")
    for p in picks:
        if ua.get(k(p)) and ua[k(p)].get("fair_mult") is not None:
            v = ua[k(p)]
        else:
            ev = by_game.get(str(int(float(p["event_id"]))))
            v = value_for(p, ev) if ev else None
            if v is not None:
                v["fair_src"] = "OddsAPI"
                if ua.get(k(p), {}).get("note"):
                    v["note"] = ua[k(p)]["note"]
        results.append(v)
        if v is None:
            print(f"{p['player'][:22]:22} {p['market']:4} {p['side']:5} (no data from either source)")
            continue
        fd = f"{v['oa_fd_line'] if v.get('oa_fd_line') is not None else p['line']:g} {v['price_used']:+g}"
        fair = "-" if v["fair_mult"] is None else f"{v['fair_mult']:.3f}"
        evs = "-" if v["ev_mult"] is None else f"{100 * v['ev_mult']:+.1f}%"
        ours = "-" if p.get("our_ev") is None else f"{100 * p['our_ev']:+.1f}%"
        books = "-" if v.get("books") is None else str(v["books"]) + ("x" if v.get("exchange") else "")
        note = "; ".join(x for x in (f"{v.get('other_line_books')} books on another line" if v.get("other_line_books") else "",
                                     v.get("note") or "",
                                     "" if v.get("oa_fd_line") in (None, p["line"]) else f"FanDuel now {v['oa_fd_line']:g}",
                                     "" if v["fair_mult"] is not None else f"needs {MIN_BOOKS}+ books at FanDuel's line") if x)
        print(f"{p['player'][:22]:22} {p['market']:4} {p['side']:5} {fd:14} {fair:12} {evs:8} {ours:7} {v['fair_src']:9} {books:>5}  {note}")
    if args.top:
        decide(args.date, shortlist, picks, results)
        stub = {"decision": "watch", "bet_kelly": 0.0, "books": 0, "fair_mult": None, "ev_mult": None, "fair_src": None,
                "note": "not checked (credits)"}
        picks, results = picks + dropped, results + [dict(stub) for _ in dropped]
    path = save(args.date, picks, results)
    print(f"\nSaved {path}; ledger updated. Re-score (python3 bp_props.py --from-csv) to show it on the board.")
    print(f"Credits remaining: {load_credits().get('remaining')}")


if __name__ == "__main__":
    main()
