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

from nbaprops import config as C  # thresholds, caps and settings
import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
import time

from nbaprops.pricing import devig as _odds  # noqa: E402 -- shared odds math
from nbaprops.rules import ledger as bp_learn  # noqa: E402
from nbaprops.app import props as bp_props  # noqa: E402
from nbaprops.sources import unabated as U  # noqa: E402
from nbaprops.sources.rotowire import norm  # noqa: E402

from nbaprops.config import ROOT as HERE  # noqa: E402
from nbaprops.sources.oddsapi import (  # noqa: E402 -- moved, re-exported for existing callers
    MIN_REMAINING,
    get,
    guard,
    load_credits,
)
MIN_BOOKS = C.ODDSAPI_PLAN_MIN_BOOKS  # nbaprops/config.py
DAILY_CREDITS = C.ODDSAPI_DAILY_CREDITS  # nbaprops/config.py
BET_MIN_EV = C.BET_MIN_EV  # nbaprops/config.py
BET_MIN_BOOKS = C.ODDSAPI_MIN_BOOKS  # nbaprops/config.py
BET_KELLY_MAX = C.BET_KELLY_MAX  # nbaprops/config.py
BET_KELLY_CAP = C.BET_KELLY_CAP  # nbaprops/config.py

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












# ------------------------------------------------------------------- picks
def load_picks(date, top):
    """Today's logged picks from the latest run (one row per prop), or the top N FanDuel props by Balanced."""
    if top:
        now = bp_learn.utc_now()
        rows = [r for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv")))
                if r.get("price_is_fanduel") == "True" and r.get("balanced") not in (None, "")
                and r.get("noproj") != "True" and r.get("placeholder") != "True"
                and r.get("stale_price") != "True" and (not r.get("tip") or _open(r, now))]
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
    """Print what an Odds API credit cap left unchecked."""
    print(f"Odds API: the full set would cost {full_cost} credits (over {budget}). Keeping the {n_games} games with the most "
          f"shortlist plays; not checked ({len(dropped)}): " +
          "; ".join(f"#{p.get('rank', '?')} {p['player']} {p['market']} {p['side']} {p['line']:g}" for p in dropped))


def gid(p):
    """Game key for an Odds API event."""
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
    """Implied probability of American odds."""
    return _odds.implied_prob(a)


def decimal(a):
    """Decimal odds from American odds."""
    return _odds.american_to_decimal(a)


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
    """Odds API event for a BettingPros game."""
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


def bet_kelly(fair: float, american: float, cap: float | None = None) -> float:
    """Quarter-Kelly stake in units (1u = 1% of bankroll) on the market's fair probability, capped."""
    cap = bet_cap()[0] if cap is None else cap
    return _odds.quarter_kelly_units(fair, american, cap)


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
    """Write the day's value file."""
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
            and (not r.get("tip") or _open(r, now))]


def _open(r, now=None):
    """Tip-time rule for a row: bettable until scheduled start + 10 min unless ESPN shows the game under way."""
    from nbaprops.rules import tipoff as bp_tipoff
    return bp_tipoff.is_open(r.get("tip"), r.get("team") or None, now)


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
    """BettingPros' side at FanDuel's line, or None with no real projection. 3PM/P+R use the calibrated projection."""
    if r.get("noproj") in ("True", True) or r.get("bp_proj") in (None, ""):
        return None
    if r.get("market") in ("3PM", "P+R") and _f(r.get("line")) is not None:
        from nbaprops.pricing import calib as bp_calib
        cal, line = bp_calib.calibrate(r["market"], _f(r["bp_proj"])), _f(r["line"])
        return "over" if cal > line else "under" if cal < line else None
    return r.get("rec_side")


def line_key(player, market, line):
    """Key for one line: player|market|line."""
    return f"{player}|{market}|{float(line):g}"


def et_date(tip):
    """UTC tip '2026-11-23T00:00:00Z' -> its ET slate date '2026-11-22'."""
    t = dt.datetime.strptime(tip, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (t - dt.timedelta(hours=5)).date().isoformat()


def slate_lines(data, date, d=None):
    """FanDuel lines on Unabated's board for games on `date`'s slate only (the board also carries tomorrow's games)."""
    return [x for x in U.fd_lines(data, d) if x.get("tip") and et_date(x["tip"]) == date]


def extra_rows(date, rows, data):
    """FanDuel lines on Unabated's board that the BettingPros pull didn't carry, as no-projection rows mapped to
    BettingPros' game ids by team (so they can be graded)."""
    have = {(norm(r["player"]), r["market"]) for r in rows}
    ev_teams, bp_only = {}, {}
    for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
        if r.get("event_id") and r.get("team"):
            ev_teams.setdefault(str(r["event_id"]).split(".")[0], set()).add(U.TEAM_FIX.get(r["team"], r["team"]))
        if r.get("price_is_fanduel") != "True" and r.get("bp_proj") not in (None, ""):
            bp_only[(norm(r["player"]), r["market"])] = r       # BettingPros projection with no FanDuel line in /offers
    pulled = rows[0].get("pulled_at") if rows else bp_learn.utc_now()
    out = []
    for x in slate_lines(data, date):
        if (norm(x["player"]), x["market"]) in have:
            continue
        ev = next((e for e, t in ev_teams.items() if t & set(x["teams"])), None)
        bp = bp_only.get((norm(x["player"]), x["market"]))
        proj = _f(bp.get("bp_proj")) if bp else None
        if proj is not None and proj != x["line"]:              # BettingPros' projection, sided at FanDuel's line
            side = "over" if proj > x["line"] else "under"
        else:
            side = "over" if x["over_odds"] >= x["under_odds"] else "under"
        out.append({"player": x["player"], "team": (bp or {}).get("team", ""), "market": x["market"], "line": x["line"],
                    "rec_side": side, "fd_odds_rec": x[side + "_odds"], "over_odds": x["over_odds"], "under_odds": x["under_odds"],
                    "price_is_fanduel": "True", "noproj": "False" if proj is not None else "True",
                    "bp_proj": "" if proj is None else proj, "event_id": ev or (bp or {}).get("event_id", ""), "tip": x["tip"],
                    "pulled_at": pulled, "source_row": "unabated" + (" + BP projection" if proj is not None else ""),
                    "dvp_softness": (bp or {}).get("dvp_softness", ""), "perf": (bp or {}).get("perf", "")})
    return out


def cno_rows(date, cands, led, gkey, cap):
    """CrazyNinja +EV strategy rows: every side with median worst-case EV >= CNO_MIN_EV, staked at ¼K on CNO's fair
    probability with the same player/game exposure caps (counted within this strategy). Never re-logged."""
    have = {(x["player"], x["market"], x["side"], str(_f(x.get("line")))) for x in led
            if x["date"] == date and x["strategy"] in bp_learn.CNO_KEYS}
    logged = [x for x in led if x["date"] == date and x["strategy"] in bp_learn.CNO_KEYS]
    new = [(r, sd, v, c) for r, sd, v, c in sorted(cands, key=lambda x: -x[3]["median_ev"])
           if (r["player"], r["market"], sd, str(_f(r.get("line")))) not in have]
    cc = apply_exposure([{"player": r["player"], "game": gkey(r), "ev": c["median_ev"],
                          "stake": bet_kelly(c["fair"], v["price_used"], cap)} for r, sd, v, c in new], logged, gkey)
    out = []
    for (r, sd, v, c), x in zip(new, cc):
        if x["stake"] <= 0:
            continue
        row = value_row(date, r, "cno", sd, v, x["stake"], c["fair"], c)
        row["line"], row["cap_note"] = r["line"], x["cap"]
        out.append(row)
    return out


def ua_store_path(date):
    """Path of the day's latest Unabated EVs."""
    return os.path.join(HERE, "history", date, f"unabated_ev_{date}.json")


def save_ua_store(date, rows, ua):
    """Unabated market EV for every priced FanDuel side, merged into the day's file. `rows` are open games only: their
    entries get the latest price (the first sighting also sets a frozen entry_* snapshot); games already started are
    never touched, so nothing written after a tip can change or drop them."""
    now = bp_learn.utc_now()
    pos = []
    try:
        out = json.load(open(ua_store_path(date)))
    except (OSError, ValueError):
        out = {}
    for r in rows:
        if r.get("tip") and not _open(r, now):
            continue
        for sd in ("over", "under"):
            v = ua.get((r["player"], r["market"], sd)) or {}
            if v.get("ev_mult") is None:
                continue
            k = f"{r['player']}|{r['market']}|{sd}|{float(r['line']):g}"
            e = out.get(k) or {"player": r["player"], "market": r["market"], "side": sd, "line": float(r["line"]),
                               "tip": r.get("tip"), "team": r.get("team"), "entry_at": now, "entry_price": v.get("price_used"),
                               "entry_ev": v["ev_mult"], "entry_fair": v.get("fair_mult"), "entry_books": v.get("books")}
            if e.get("entry_ev") is not None and e["entry_ev"] <= 0 < v["ev_mult"] and not e.get("first_pos_at"):
                e.update(first_pos_at=now, first_pos_price=v.get("price_used"), first_pos_ev=v["ev_mult"],
                         first_pos_fair=v.get("fair_mult"), first_pos_books=v.get("books"))
            e.update(price=v.get("price_used"), ev=v["ev_mult"], fair=v.get("fair_mult"), books=v.get("books"),
                     exchange=bool(v.get("exchange")), at=now)
            out[k] = e
            if v["ev_mult"] > 0 and (v.get("books") or 0) >= 3:
                pos.append({"player": r["player"], "market": r["market"], "side": sd, "line": float(r["line"]),
                            "odds": v.get("price_used"), "fair": v.get("fair_mult"), "ev": v["ev_mult"], "books": v.get("books"),
                            "tip": r.get("tip"), "team": r.get("team"), "event_id": r.get("event_id"), "src": "Unabated"})
    try:
        os.makedirs(os.path.dirname(ua_store_path(date)), exist_ok=True)
        json.dump(out, open(ua_store_path(date), "w"))
    except OSError:
        pass
    log_all_ev(date, pos)


def log_all_ev(date, items):
    """'All +EV' log: every +EV prop from any source, once per day per player|market|side|line (first source wins;
    later sources are added to the note). Entry snapshot is the first sighting; ¼K on its entry fair, capped 1.25u."""
    items = [x for x in items if (x.get("ev") or 0) > 0 and x.get("fair") and x.get("odds") is not None]
    if not items:
        return []
    led = bp_learn._read(bp_learn.LEDGER)
    k = lambda p, m, s, ln: (norm(p), m, s, str(_f(ln)))
    have = {k(r["player"], r["market"], r["side"], r.get("line")): r for r in led
            if r["date"] == date and r["strategy"] in bp_learn.ALL_EV_KEYS}
    now, out, changed = bp_learn.utc_now(), [], False
    for x in items:
        key = k(x["player"], x["market"], x["side"], x["line"])
        if key in have:
            r = have[key]
            if x["src"] not in (r.get("cap_note") or ""):
                r["cap_note"] = ((r.get("cap_note") or "") + f"; also {x['src']}").lstrip("; ")
                changed = True
            continue
        row = {"date": date, "pulled_at": now, "tip": x.get("tip") or "", "strategy": "all_ev", "player": x["player"],
               "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "odds": x["odds"],
               "our_prob": x["fair"], "nv_prob": x["fair"], "oa_fair": x["fair"], "oa_ev": x["ev"], "sources": x.get("books"),
               "fair_src": x["src"], "event_id": x.get("event_id") or "",
               "kelly_u": _odds.quarter_kelly_units(x["fair"], x["odds"], BET_KELLY_CAP)}
        have[key] = row
        out.append(row)
    if out or changed:
        bp_learn._write(bp_learn.LEDGER, led + out, bp_learn.LEDGER_FIELDS)
    return out


def log_ua_agree(date, rows, ua):
    """v2 record 'Unabated +EV & BP agrees': each FanDuel side with Unabated market EV > 0 (3+ sources) whose game is
    open and where BettingPros' projection/lean agrees, logged once per day; ¼K on the Unabated fair, BET caps."""
    if date < bp_learn.V2_START:
        return []
    from nbaprops.pricing import projctx as bp_projctx
    now = bp_learn.utc_now()
    led = bp_learn._read(bp_learn.LEDGER)
    have = {(r["player"], r["market"], r["side"], str(_f(r.get("line")))) for r in led
            if r["date"] == date and r["strategy"] in bp_learn.UA_KEYS}
    cands = []
    for r in rows:
        if r.get("tip") and not _open(r, now):
            continue
        for sd in ("over", "under"):
            v = ua.get((r["player"], r["market"], sd)) or {}
            if (v.get("ev_mult") or 0) <= 0 or (v.get("books") or 0) < 3 or v.get("fair_src") != "Unabated":
                continue
            if (r["player"], r["market"], sd, str(_f(r.get("line")))) in have:
                continue
            c = bp_projctx.context(date, r["player"], r["market"], sd, r["line"])
            if c["tag"] in ("agrees", "lean agrees"):
                cands.append((r, sd, v, c))
    if not cands:
        return []
    logged = [x for x in led if x["date"] == date and x["strategy"] in bp_learn.UA_KEYS]
    gkey = game_resolver(rows + logged)
    cap = bet_cap()[0]
    cc = apply_exposure([{"player": r["player"], "game": gkey(r), "ev": v["ev_mult"],
                          "stake": bet_kelly(v["fair_mult"], v["price_used"], cap)} for r, sd, v, c in cands], logged, gkey)
    out = []
    for (r, sd, v, c), x in zip(cands, cc):
        if x["stake"] <= 0:
            continue
        row = value_row(date, dict(r, pulled_at=now), "ua_agree", sd, v, x["stake"], v.get("fair_mult"))
        row.update(line=r["line"], cap_note=x["cap"], proj_tag=c["tag"], proj_note=c["text"])
        out.append(row)
    if out:
        bp_learn._write(bp_learn.LEDGER, led + out, bp_learn.LEDGER_FIELDS)
    return out


def daily_counts(date):
    """(Unabated BETs, CNO +EV, both, CNO-only) logged for `date`, by player|market|side|line."""
    led = [r for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date]
    k = lambda r: (r["player"], r["market"], r["side"], str(_f(r.get("line"))))
    u = {k(r) for r in led if r["strategy"] in bp_learn.BET_KEYS}
    c = {k(r) for r in led if r["strategy"] in bp_learn.CNO_KEYS}
    sc = {k(r) for r in led if r["strategy"] in bp_learn.SCREENER_KEYS}
    return len(u), len(c), len(u & c), len(c - u), len(sc)


def counts_line(date):
    """Daily counts line (Unabated BETs, CNO +EV, both, CNO-only, screener)."""
    u, c, both, only, sc = daily_counts(date)
    return f"{date}: Unabated BETs {u}, CNO +EV {c}, both {both}, CNO-only {only}" + (f", CNO screener {sc}" if sc else "")


def run_board(args):
    """--board: price every FanDuel line once, log BETs, records and the alt ladder."""
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
    ua, ua_logged = {}, []
    if ua_ok:
        try:
            if args.board_file:                    # a saved board (no call): price it as it was when fetched
                data = json.load(open(args.board_file))
                print(f"  Using the saved Unabated board {args.board_file} (no call).")
            else:
                data = U.fetch_board(board_markets(args.date))
            extra = extra_rows(args.date, rows, data)
            if extra:
                print(f"  {len(extra)} FanDuel lines on Unabated's board that BettingPros doesn't carry: priced too (no projection).")
                rows += extra
            cands = [sp for r in rows for sp in side_picks(r)]
            ua = U.price_board(cands, data, U.load(U.DISCOVERY, {}))
            save_ua_store(args.date, rows, ua)
            ua_logged = log_ua_agree(args.date, rows, ua)
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
    entries, bets, cno_cands, rejects = {}, [], [], []
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
        q = []
        for sd, v in sides.items():
            if not qualifies(v):
                continue
            c = cno_check(sd, v) if v.get("fair_src") == "Unabated" else None
            ok, flag = bet_gate(v, c)
            if c and c["median_ev"] >= CNO_MIN_EV:
                cno_cands.append((r, sd, v, c))
            if ok:
                q.append((sd, v, c, flag))
            else:
                rejects.append(f"  CNO says no: {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g}: "
                               f"Unabated EV {100 * v['ev_mult']:+.1f}%, CNO worst-case {100 * c['median_ev']:+.1f}%")
        if q:
            sd, v, c, flag = max(q, key=lambda x: x[1]["ev_mult"])
            bets.append((r, sd, v, c, flag))
        entries[line_key(r["player"], r["market"], r["line"])] = e
    ledger, lines_out = [], []
    cap, cap_note = bet_cap()
    bets = sorted(bets, key=lambda x: -x[2]["ev_mult"])
    led_now = bp_learn._read(bp_learn.LEDGER)
    logged_bets = [x for x in led_now if x["date"] == args.date and x["strategy"] in bp_learn.BET_KEYS]
    have = {(x["player"], x["market"], x["side"], str(_f(x.get("line")))) for x in logged_bets}
    gkey = game_resolver(rows + logged_bets)
    cands = [{"player": r["player"], "game": gkey(r), "ev": v["ev_mult"],
              "stake": bet_kelly(v["fair_mult"], v["price_used"], cap)} for r, sd, v, c, flag in bets]
    new_idx = [i for i, (r, sd, v, c, flag) in enumerate(bets) if (r["player"], r["market"], sd, str(_f(r.get("line")))) not in have]
    apply_exposure([cands[i] for i in new_idx], logged_bets, gkey)
    for i in range(len(bets)):                    # already-logged BETs keep their logged stake
        if i not in new_idx:
            r, sd, v, c, flag = bets[i]
            old_r = next(x for x in logged_bets if (x["player"], x["market"], x["side"], str(_f(x.get("line"))))
                         == (r["player"], r["market"], sd, str(_f(r.get("line")))))
            cands[i].update(stake=_f(old_r.get("kelly_u")) or 0, cap=old_r.get("cap_note") or "")
    for (r, sd, v, cno, flag), c in zip(bets, cands):
        ps = proj_side(r)
        tag = "+EV + proj agrees" if ps == sd else "+EV only"
        form, dvp = side_context(r, sd)
        stake = c["stake"]
        if stake <= 0:                            # no room left under the player/game cap: not a BET today
            lines_out.append(f"  AT CAP, not logged: {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g} "
                             f"(mkt EV {100 * v['ev_mult']:+.1f}%; {c['cap']})")
            continue
        e = entries[line_key(r["player"], r["market"], r["line"])]
        e.update(decision="BET", bet_side=sd, tag=tag, bet_kelly=stake, bet_price=v["price_used"], bet_ev=v["ev_mult"],
                 form=form, dvp=dvp, proj_side=ps, cap_note=c["cap"], cno_flag=flag)
        mp = _f(r.get("model_prob"))
        ledger.append(value_row(args.date, r, "bet_agree" if ps == sd else "bet_only", sd, v, stake,
                                None if mp is None or ps is None else (mp if ps == sd else 1 - mp), cno))
        ledger[-1]["cap_note"] = c["cap"]
        e["cno_ev"], e["cno_min_ev"] = ledger[-1].get("cno_ev"), ledger[-1].get("cno_min_ev")
        lines_out.append(f"  BET [{tag}] {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g}: mkt EV "
                         f"{100 * v['ev_mult']:+.1f}%, fair {v['fair_mult']:.3f} ({v['fair_src']}, {v.get('books')} "
                         f"{'sources' if v['fair_src'] == 'Unabated' else 'books'}{', exchange' if v.get('exchange') else ''}); "
                         f"BP proj {'agrees' if ps == sd else ('says ' + ps if ps else 'none')} ({r.get('bp_proj') or '-'} vs {float(r['line']):g}); "
                         f"form {'-' if form is None else str(round(100 * form)) + '%'}; DvP {fmtn(dvp)}; stake {stake}u ¼K{cap_text(c['cap'])}"
                         + cno_note(ledger[-1]))
    lines_out += rejects
    if args.date >= bp_learn.V2_START:
        lines_out.append(f"  Unabated +EV & BP agrees (v2 record): {len(ua_logged)} new.")
    try:                                              # alt ladder (FanDuel alt rungs vs. other sources at that rung)
        from nbaprops.pricing import alts as bp_alts
        arows = bp_alts.log(args.date, bp_alts.scan(data, U.load(U.DISCOVERY, {}), args.date, bp_learn.utc_now()), bp_learn.utc_now())
        for r in arows:
            from nbaprops.pricing import projctx as bp_projctx
            lines_out.append(f"  ALT [{bp_alts.KEYS[r['strategy']]}] {r['player']} {r['market']} {r['side']} {float(r['line']):g} "
                             f"{float(r['odds']):+g}: mkt EV {100 * float(r['oa_ev']):+.1f}% ({r['sources']} sources, "
                             f"{r['cap_note']}), CNO worst-case {100 * float(r['cno_ev']):+.1f}%; {r['kelly_u']}u ¼K; "
                             f"DvP {bp_projctx.dvp_dot(r['side'], r.get('dvp')) or '–'} ({r.get('dvp_tag')})  |  {r.get('proj_note') or '–'}")
        lines_out.append(f"  Alt ladder: {len(arows)} new rungs logged (EV >= +4%, 3+ two-sided sources, <= +300, CNO > 0, one per player).")
    except Exception as e:
        lines_out.append(f"  Alt ladder skipped ({e}).")
    ledger += cno_rows(args.date, cno_cands, led_now, gkey, cap)
    lines_out.append(f"  CrazyNinja +EV (own record): {sum(1 for x in ledger if x['strategy'] == 'cno')} lines this run "
                     f"(median worst-case EV >= +{100 * CNO_MIN_EV:g}%). CNO calls only for sides with Unabated EV >= +2%.")
    bet_keys = {(b[0]["player"], b[0]["market"]) for b in bets}
    for p in shortlist:
        if (p["player"], p["market"]) in bet_keys:
            continue
        r = by_row.get((p["player"], p["market"])) or p.get("row") or {}
        e = entries.setdefault(line_key(p["player"], p["market"], p["line"]), {"sides": {}, "at": bp_learn.utc_now()})
        e.update(decision="watch")
        ledger.append(value_row(args.date, r, "watch", p["side"], e["sides"].get(p["side"]), 0.0, _f(r.get("model_prob"))))
    bp_learn.log_value_decisions(args.date, ledger)
    print("Daily counts: " + counts_line(args.date))
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
    """Number for display, '-' for None."""
    return "-" if v is None else f"{v:g}"


PLAYER_CAP = C.PLAYER_CAP  # nbaprops/config.py
GAME_CAP = C.GAME_CAP  # nbaprops/config.py


def game_resolver(rows):
    """game key for a row: BettingPros event id; rows without one (Unabated-only lines) map to the event that
    shares their tip time when only one does, else to the tip time itself."""
    by_tip = {}
    for r in rows:
        ev = str(r.get("event_id") or "").split(".")[0]
        if ev and r.get("tip"):
            by_tip.setdefault(r["tip"], set()).add(ev)

    def key(r):
        ev = str(r.get("event_id") or "").split(".")[0]
        if ev:
            return ev
        evs = by_tip.get(r.get("tip"), set())
        return next(iter(evs)) if len(evs) == 1 else f"tip:{r.get('tip')}"
    return key


def apply_exposure(cands: list[dict], logged: list[dict], key, player_cap: float | None = None,
                   game_cap: float | None = None) -> list[dict]:
    """Cap new BET stakes: PLAYER_CAP per player and GAME_CAP per game, counting stakes already logged that day.
    cands = [{"player", "game", "ev", "stake"}] (mutated: "stake", "cap"). When a cap binds, that player's or game's
    new BETs are scaled down proportionally to the room left; cents left over by rounding go to the largest edge first."""
    used_p, used_g = {}, {}
    for r in logged:
        u = _f(r.get("kelly_u")) or 0
        used_p[r["player"]] = used_p.get(r["player"], 0) + u
        used_g[key(r)] = used_g.get(key(r), 0) + u
    for c in cands:
        c.setdefault("cap", "")

    def squeeze(group, room, label):
        want = sum(c["stake"] for c in group)
        room = max(0.0, round(room, 2))
        if want <= room + 1e-9 or want <= 0:
            return
        f = room / want
        for c in group:
            c["stake"] = int(c["stake"] * f * 100) / 100           # round down to 0.01u
            c["cap"] = ", ".join(x for x in (c["cap"], f"capped: {label}") if x)
        left = round(room - sum(c["stake"] for c in group), 2)
        for c in sorted(group, key=lambda c: -c["ev"]):               # leftover cents: largest edge first
            if left < 0.01:
                break
            c["stake"] = round(c["stake"] + 0.01, 2)
            left = round(left - 0.01, 2)

    for pl in {c["player"] for c in cands}:
        grp = [c for c in cands if c["player"] == pl]
        squeeze(grp, (PLAYER_CAP if player_cap is None else player_cap) - used_p.get(pl, 0), "player")
    for g in {c["game"] for c in cands}:
        grp = [c for c in cands if c["game"] == g]
        squeeze(grp, (GAME_CAP if game_cap is None else game_cap) - used_g.get(g, 0), "game")
    return cands


def cap_text(note):
    """' (capped: ...)' or ''."""
    return f" ({note})" if note else ""


def value_row(date, r, strategy, side, v, stake, our_prob, cno="unset"):
    """Ledger row for a value decision (BET, watch, CNO, ...)."""
    v = v or {}
    return {"date": date, "pulled_at": r.get("pulled_at"), "tip": r.get("tip"), "strategy": strategy, "rank": None,
            "player": r.get("player"), "team": r.get("team"), "market": r.get("market"), "side": side,
            "line": r.get("line"), "odds": v.get("price_used") or r.get("fd_odds_rec"), "gap": r.get("pct_gap"),
            "placeholder": r.get("placeholder"), "score": r.get("score"), "balanced": r.get("balanced"),
            "learned": r.get("learned"), "our_prob": our_prob, "nv_prob": v.get("fair_mult"), "kelly_u": stake,
            "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id"),
            "oa_fair": v.get("fair_mult"), "oa_ev": v.get("ev_mult"), "fair_src": v.get("fair_src"),
            "sources": v.get("books"), **_proj_fields(r),
            **(cno_fields(side, v, cno) if strategy in bp_learn.BET_KEYS | bp_learn.CNO_KEYS else {})}


def _proj_fields(r):
    """Raw and calibrated BettingPros projection for the ledger (calibration applies to 3PM/P+R only)."""
    from nbaprops.pricing import calib as bp_calib
    raw = _f(r.get("bp_proj"))
    return {"bp_proj_raw": raw, "bp_proj_cal": bp_calib.value(r.get("market"), raw) if raw is not None else None}



BOARD_MARKETS = C.BOARD_MARKETS  # nbaprops/config.py
R_A_FROM = C.R_A_FROM  # nbaprops/config.py


def board_markets(date):
    """Markets on the Unabated board for a slate (R+A from 11/24)."""
    return [m for m in BOARD_MARKETS if m != "R+A" or date >= R_A_FROM]
CNO_MIN_EV = C.CNO_MIN_EV  # nbaprops/config.py


def cno_check(side, v):
    """CrazyNinja worst-case devig of each source at FanDuel's line (one call per source), or None if CNO is down."""
    if not v or not v.get("two_way"):
        return None
    try:
        from nbaprops.sources import crazyninja as C
        return C.check(side, v.get("two_way"), v.get("price_used"))
    except Exception:
        return None


def cno_fields(side, v, c="unset"):
    """CrazyNinja worst-case fields for a ledger row."""
    c = cno_check(side, v) if c == "unset" else c
    if not c:
        return {"cno_flag": "no CNO check"}
    return {"cno_ev": c["median_ev"], "cno_min_ev": c["min_ev"], "cno_fair": c["fair"], "cno_flag": ""}


def bet_gate(v, c):
    """BET rule: Unabated median EV >= +2% (qualifies) AND CNO median worst-case EV > 0; CNO down -> Unabated rule,
    flagged. Returns (is_bet, flag)."""
    if not qualifies(v):
        return False, ""
    if c is None:
        return True, "no CNO check"
    return c["median_ev"] > 0, ("" if c["median_ev"] > 0 else "CNO worst-case EV <= 0")


def cno_note(row):
    """CrazyNinja note for an alert line."""
    if row.get("cno_ev") in (None, ""):
        return "; no CNO check" if row.get("cno_flag") else ""
    return f"; CNO worst-case EV {100 * float(row['cno_ev']):+.1f}% (lowest source {100 * float(row['cno_min_ev']):+.1f}%)"


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
    bets = [r for r in led if r["date"] == args.date
            and r["strategy"] in bp_learn.BET_KEYS | bp_learn.CNO_KEYS | bp_learn.SCREENER_KEYS and r.get("tip") and _open(r, now)]
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
    flagged = apply_close(args.date, led, bets, res, at, show=True)
    print(f"\nCLV logged for {sum(1 for r in bets if r.get('close_fair') not in (None, ''))} of {len(bets)} BETs. "
          + ("FLAGGED (market EV now < 0): " + "; ".join(f"{r['player']} {r['market']} {r['side']} {float(r['line']):g}" for r in flagged)
             if flagged else "No BET's market EV fell below 0."))


def apply_close(date, led, bets, res, at, show=False):
    """Closing fair price, CLV (EV of the bet taken, at the closing fair) and market EV now for `bets`
    (rows of `led`), from Unabated prices `res`. Writes the ledger and the value file; returns BETs whose
    market EV at FanDuel's current price fell below 0."""
    flagged = []
    if show:
        print(f"\n{'player':22} {'mkt':4} {'side':5} {'bet':10} {'fair now':8} {'CLV (EV)':9} {'mkt EV now':10} FD now")
    for r in bets:
        v = res.get((r["player"], r["market"], r["side"])) or {}
        f = v.get("fair_mult")
        odds = float(r["odds"])
        r["close_at"] = at
        r["close_fd_line"] = v.get("oa_fd_line")
        if f is None:
            if show:
                print(f"{r['player'][:22]:22} {r['market']:4} {r['side']:5} {float(r['line']):g} {odds:+g}  no closing price ({v.get('note') or 'not on the board'})")
            continue
        r["close_fair"] = f
        r["clv_ev"] = round(f * decimal(odds) - 1, 4)                  # the bet we took, priced at the closing fair
        r["close_mkt_ev"] = v.get("ev_mult")                           # FanDuel's current price at our line, if still there
        if v.get("ev_mult") is not None and v["ev_mult"] < 0:
            flagged.append(r)
        if show:
            fd_now = "-" if v.get("oa_fd_line") is None else f"{v['oa_fd_line']:g} {v.get('oa_fd_price'):+g}"
            ev_now = "-" if v.get("ev_mult") is None else f"{100 * v['ev_mult']:+.1f}%"
            print(f"{r['player'][:22]:22} {r['market']:4} {r['side']:5} {float(r['line']):g} {odds:+g}  {f:.3f}    "
                  f"{100 * r['clv_ev']:+.1f}%    {ev_now:10} {fd_now}" + ("   FLAG: market EV now below 0" if r in flagged else ""))
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    path = os.path.join(HERE, f"value_{date}.json")
    try:
        val = json.load(open(path))
    except (OSError, ValueError):
        val = {}
    for r in bets:
        e = val.get(line_key(r["player"], r["market"], r["line"]))
        if e is not None and r.get("close_fair") not in (None, ""):
            e["close"] = {"fair": r["close_fair"], "clv_ev": r["clv_ev"], "mkt_ev": r["close_mkt_ev"], "at": at}
    json.dump(val, open(path, "w"), indent=1)
    return flagged


# --------------------------------------------------------------------- watch
WATCH_EVERY = C.WATCH_EVERY_S  # nbaprops/config.py
CLOSE_LEAD = C.CLOSE_LEAD_S  # nbaprops/config.py


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


def watch_scan(date, state, close_before=None):
    """One free Unabated scan: new BETs (logged) and logged BETs whose market EV fell below 0 (or line moved).
    BETs in games tipping before `close_before` (the next scan) get their closing price and CLV logged now:
    this is the last scan before their tip. Returns the tip times still ahead."""
    data = U.fetch_board(board_markets(date))
    d = U.load(U.DISCOVERY, {})
    have_csv = os.path.exists(os.path.join(HERE, f"bp_props_{date}.csv"))
    rows = board_rows(date) if have_csv else []
    rows += extra_rows(date, rows, data) if have_csv else [
        {"player": x["player"], "team": "", "market": x["market"], "line": x["line"], "rec_side": "over",
         "fd_odds_rec": x["over_odds"], "over_odds": x["over_odds"], "under_odds": x["under_odds"], "price_is_fanduel": "True",
         "noproj": "True", "bp_proj": "", "event_id": "", "tip": x["tip"], "pulled_at": bp_learn.utc_now()} for x in slate_lines(data, date, d)]
    ua = U.price_board([sp for r in rows for sp in side_picks(r)], data, d)
    save_ua_store(date, rows, ua)
    for x in log_ua_agree(date, rows, ua):
        print(f"[{_et_now():%H:%M}] UA+BP {x['player']} {x['market']} {x['side']} {float(x['line']):g} {float(x['odds']):+g}: "
              f"mkt EV {100 * float(x['oa_ev']):+.1f}% ({x['sources']} sources), {x['kelly_u']}u ¼K | {x.get('proj_note')}", flush=True)
    now = bp_learn.utc_now()
    led = bp_learn._read(bp_learn.LEDGER)
    logged = {f"{r['player']}|{r['market']}|{r['side']}|{float(r['line']):g}": r for r in led
              if r["date"] == date and r["strategy"] in bp_learn.BET_KEYS and r.get("tip") and _open(r, now)}
    cap = bet_cap()[0]
    new_rows, found, cno_cands = [], [], []
    state.setdefault("cno_seen", [])
    for r in rows:
        if r.get("tip") and not _open(r, now):           # past start + 10 min, or ESPN shows it under way: never log
            continue
        for sd in ("over", "under"):
            v = ua.get((r["player"], r["market"], sd))
            k = f"{r['player']}|{r['market']}|{sd}|{float(r['line']):g}"
            if not qualifies(v) or (k in logged or k in state["alerted"]) and k in state["cno_seen"]:
                continue
            c = cno_check(sd, v)
            if k not in state["cno_seen"] and c and c["median_ev"] >= CNO_MIN_EV:
                cno_cands.append((r, sd, v, c))
                state["cno_seen"].append(k)
            if k in logged or k in state["alerted"]:
                continue
            ok, flag = bet_gate(v, c)
            if not ok:
                state["alerted"][k] = v["ev_mult"]          # don't re-check every scan
                print(f"[{_et_now():%H:%M}] CNO says no: {r['player']} {r['market']} {sd} {float(r['line']):g} "
                      f"{v['price_used']:+g}: Unabated EV {100 * v['ev_mult']:+.1f}%, CNO worst-case {100 * c['median_ev']:+.1f}%",
                      flush=True)
                continue
            found.append((r, sd, v, k, c, flag))
    found.sort(key=lambda x: -x[2]["ev_mult"])
    day_bets = [x for x in led if x["date"] == date and x["strategy"] in bp_learn.BET_KEYS]
    gkey = game_resolver(rows + day_bets)
    cands = apply_exposure([{"player": r["player"], "game": gkey(r), "ev": v["ev_mult"],
                             "stake": bet_kelly(v["fair_mult"], v["price_used"], cap)} for r, sd, v, k, _, _ in found],
                           day_bets, gkey)
    for (r, sd, v, k, cno, _flag), c in zip(found, cands):
        ps = proj_side(r)
        tag = "+EV + proj agrees" if ps == sd else "+EV only"
        stake = c["stake"]
        form, dvp = side_context(r, sd)
        mp = _f(r.get("model_prob"))
        state["alerted"][k] = v["ev_mult"]
        if stake <= 0:
            notify(f"BET at cap, not logged: {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g} "
                   f"(mkt EV {100 * v['ev_mult']:+.1f}%; {c['cap']})")
            continue
        row = value_row(date, dict(r, pulled_at=now), "bet_agree" if ps == sd else "bet_only", sd, v, stake,
                        None if mp is None or ps is None else (mp if ps == sd else 1 - mp), cno)
        row["line"] = r["line"]
        row["cap_note"] = c["cap"]
        new_rows.append(row)
        notify(f"NEW BET [{tag}] {r['player']} {r['market']} {sd} {float(r['line']):g} {v['price_used']:+g}: mkt EV "
               f"{100 * v['ev_mult']:+.1f}% ({v.get('books')} sources{', exch' if v.get('exchange') else ''}); "
               f"form {'-' if form is None else str(round(100 * form)) + '%'}, DvP {fmtn(dvp)}; stake {stake}u ¼K{cap_text(c['cap'])}; "
               f"tip {str(r.get('tip'))[11:16]}Z" + cno_note(row))
    new_rows += cno_rows(date, [(dict(r, pulled_at=now), sd, v, c) for r, sd, v, c in cno_cands], led, gkey, cap)
    try:                                              # alt ladder: new qualifying rungs, logged and alerted
        from nbaprops.pricing import alts as bp_alts
        if new_rows:
            bp_learn._write(bp_learn.LEDGER, led + new_rows, bp_learn.LEDGER_FIELDS)
            led, new_rows = led + new_rows, []
        for r in bp_alts.log(date, bp_alts.scan(data, d, date, now), now):
            from nbaprops.pricing import projctx as bp_projctx
            notify(f"NEW ALT [{bp_alts.KEYS[r['strategy']]}] {r['player']} {r['market']} {r['side']} {float(r['line']):g} "
                   f"{float(r['odds']):+g}: mkt EV {100 * float(r['oa_ev']):+.1f}% ({r['sources']} sources), CNO "
                   f"{100 * float(r['cno_ev']):+.1f}%; {r['kelly_u']}u ¼K; DvP {bp_projctx.dvp_dot(r['side'], r.get('dvp')) or '–'} "
                   f"({r.get('dvp_tag')})")
        led = bp_learn._read(bp_learn.LEDGER)
        bp_alts.close(date, data, d, now)               # alt rungs: latest pregame price (open games only)
    except Exception as e:
        print(f"[{_et_now():%H:%M}] alt ladder skipped ({e})", flush=True)
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
    # Latest pregame price, every scan, for every priced record whose game is still open (open = before scheduled
    # start + 10 min and ESPN not showing it under way). Nothing is written for a game after its tip; the close is a
    # one-time copy of the last pregame observation, made when the game is final (start + 5 min, or ESPN shows it).
    from nbaprops.rules import tipoff as T
    state.setdefault("closed", [])
    led = bp_learn._read(bp_learn.LEDGER)
    keys = (bp_learn.BET_KEYS | bp_learn.CNO_KEYS | bp_learn.SCREENER_KEYS | bp_learn.RULE_KEYS | bp_learn.UA_KEYS)
    live_rows = [r for r in led if r["date"] == date and r["strategy"] in keys and r.get("tip") and _open(r, now)]
    if live_rows:
        cp = [{"player": r["player"], "market": r["market"], "side": r["side"], "line": float(r["line"]),
               "odds": float(r["odds"])} for r in live_rows]
        res = U.price_board(cp, data, d)
        record_latest(led, live_rows, res, now)
    t_now = T._t(now)
    final = []
    for r in led:
        k = f"{r['strategy']}|{r['player']}|{r['market']}|{r['side']}|{float(r['line']):g}"
        if (r["date"] != date or r["strategy"] not in keys | bp_learn.ALT_KEYS or not r.get("tip") or k in state["closed"]
                or (t_now < T._t(r["tip"]) + T.CLOSE_AFTER and _open(r, now))):
            continue                                     # not final yet
        state["closed"].append(k)
        final.append(r)
    finalize_close(date, led, final)
    for r in final:
        if r["strategy"] not in bp_learn.BET_KEYS:
            continue
        c, mev = r.get("clv_ev"), r.get("close_mkt_ev")
        notify(f"CLOSE {r['player']} {r['market']} {r['side']} {float(r['line']):g} {float(r['odds']):+g}: "
               + ("no pregame closing price" if c in (None, "") else
                  f"CLV {100 * float(c):+.1f}% (last pregame price {str(r.get('close_at'))[11:16]}Z)"
                  + (f", mkt EV then {100 * float(mev):+.1f}%" if mev not in (None, "") else ""))
               + ("  EV FELL BELOW 0" if mev not in (None, "") and float(mev) < 0 else "") + f"; tip {str(r.get('tip'))[11:16]}Z")
    return tips_to_close(date, data, d, t_now)


def tips_to_close(date, data, d, t_now):
    """Tip times whose close isn't final yet (before start + 5 min). Empty -> the watch stops: no scan after the last close."""
    from nbaprops.rules import tipoff as T
    tips = {x["tip"] for x in slate_lines(data, date, d)}
    tips |= {r["tip"] for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date and r.get("tip")
             and r["strategy"] in bp_learn.BET_KEYS | bp_learn.CNO_KEYS | bp_learn.SCREENER_KEYS | bp_learn.RULE_KEYS
             | bp_learn.UA_KEYS | bp_learn.ALT_KEYS}
    return sorted(t for t in tips if t_now < T._t(t) + T.CLOSE_AFTER)


def record_latest(led, rows, res, at):
    """Latest pregame observation for open rows (never touches entry_* or close_*). Writes the ledger."""
    for r in rows:
        v = res.get((r["player"], r["market"], r["side"])) or {}
        f = v.get("fair_mult")
        if f is None:
            continue
        r.update(latest_at=at, latest_fd_line=v.get("oa_fd_line"), latest_price=v.get("oa_fd_price"), latest_fair=f,
                 latest_ev=round(f * decimal(float(r["odds"])) - 1, 4), latest_mkt_ev=v.get("ev_mult"))
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)


def finalize_close(date, led, rows):
    """Copy each final row's last pregame observation into close_* / clv_ev (once). Rows with no pregame observation get
    no close. Also mirrors the close into the day's value file."""
    if not rows:
        return
    for r in rows:
        if r.get("latest_fair") in (None, "") or r.get("close_at"):
            continue
        r.update(close_fair=r["latest_fair"], clv_ev=r["latest_ev"], close_mkt_ev=r.get("latest_mkt_ev"),
                 close_fd_line=r.get("latest_fd_line"), close_at=r["latest_at"])
    bp_learn._write(bp_learn.LEDGER, led, bp_learn.LEDGER_FIELDS)
    path = os.path.join(HERE, f"value_{date}.json")
    try:
        val = json.load(open(path))
    except (OSError, ValueError):
        return
    for r in rows:
        e = val.get(line_key(r["player"], r["market"], r["line"]))
        if isinstance(e, dict) and r.get("close_at") and r["strategy"] in bp_learn.BET_KEYS:
            e["close"] = {"fair": _f(r["close_fair"]), "clv_ev": _f(r["clv_ev"]), "mkt_ev": _f(r.get("close_mkt_ev")), "at": r["close_at"]}
    json.dump(val, open(path, "w"), indent=1)


def run_watch(args):
    """Free Unabated scan every 20 minutes from the day's first pull until the last game's tip, plus one 5 minutes
    before each tip; alerts on new BETs and on logged BETs whose market EV fell below 0 (or whose FanDuel line
    moved). The last scan before each tip logs closing fair price and CLV for that game's BETs (replaces --close)."""
    ok, why = U.usable()
    if not ok:
        sys.exit(f"Unabated not usable: {why}")
    path = os.path.join(HERE, f"watch_{args.date}.json")
    try:
        state = json.load(open(path))
    except (OSError, ValueError):
        state = {"alerted": {}, "dropped": []}
    print(f"Watching {args.date}: one free Unabated scan every {args.every} min from the first pull until the last tip, "
          f"plus scans {CLOSE_LEAD // 60} min before and after each tip (closing CLV = last pregame price; final at tip + 5). "
          f"Games stay bettable until tip + 10 unless ESPN shows them under way. Alerts: new BETs, BETs whose EV fell below 0.",
          flush=True)
    try:                                                  # keep the Mac awake while watching (sleep skipped scans)
        import subprocess
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        print("caffeinate unavailable: scans pause if the computer sleeps.", flush=True)
    try:
        U.discover()
    except (U.Unavailable, RuntimeError) as e:
        print(f"Discovery refresh failed ({e}); using the saved one.", flush=True)
    csv_path = os.path.join(HERE, f"bp_props_{args.date}.csv")
    while not os.path.exists(csv_path):                   # starts with the day's first pull
        print(f"{_et_now():%H:%M} ET: waiting for the first pull (bp_props_{args.date}.csv); checking every 5 min.", flush=True)
        time.sleep(300)
    every = args.every * 60
    close_before = None
    while True:
        t0 = time.time()
        try:
            tips = watch_scan(args.date, state, close_before)
        except U.Unavailable as e:
            notify(f"Watch stopped: Unabated key stopped working ({e}).")
            return
        except Exception as e:
            print(f"{_et_now():%H:%M} scan failed ({e}); next in {args.every} min.", flush=True)
            tips = [bp_learn.utc_now()[:11] + "23:59:59Z"]
        json.dump(state, open(path, "w"), indent=1)
        print(f"{_et_now():%H:%M} ET scan done in {time.time() - t0:.0f} s; {len(tips)} tip times still ahead. "
              f"{counts_line(args.date)}", flush=True)
        if not tips:
            print("All games have tipped. Watch done.", flush=True)
            return
        now = time.time()
        marks = []                                        # a scan 5 min before each tip and one at tip + 5 (closing)
        for t in tips:
            ts = dt.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.UTC).timestamp()
            marks += [ts - CLOSE_LEAD, ts + CLOSE_LEAD]
        nxt = min([m for m in marks if m > now + 60] + [now + every])
        time.sleep(max(nxt - time.time(), 1))


MARKET_WORDS = {"pts": "PTS", "points": "PTS", "reb": "REB", "rebounds": "REB", "ast": "AST", "assists": "AST",
                "3pm": "3PM", "threes": "3PM", "3s": "3PM", "p+a": "P+A", "pa": "P+A", "p+r": "P+R", "pr": "P+R",
                "pra": "PRA", "p+r+a": "PRA", "r+a": "R+A", "ra": "R+A", "reb+ast": "R+A"}


def parse_placed(text):
    """'Toppin 3PM o1.5 +172 0.75u' -> dict(name, market, side, line, odds, stake). Raises ValueError."""
    t = text.replace(",", " ").split()
    mi = next((i for i, w in enumerate(t) if w.lower() in MARKET_WORDS), None)
    if mi is None or mi == 0:
        raise ValueError("needs: <player> <market> <o|u><line> <price> <stake>u, e.g. 'Toppin 3PM o1.5 +172 0.75u'")
    name, market, rest = " ".join(t[:mi]), MARKET_WORDS[t[mi].lower()], t[mi + 1:]
    j = " ".join(rest).lower().replace("over ", "o").replace("under ", "u").split()
    if len(j) < 3 or j[0][0] not in "ou":
        raise ValueError("side and line like o1.5 / u12.5, then the price (+172) and stake (0.75u)")
    side = "over" if j[0][0] == "o" else "under"
    line, odds, stake = float(j[0][1:]), float(j[1]), float(j[2].rstrip("u"))
    return {"name": name, "market": market, "side": side, "line": line, "odds": odds, "stake": stake}


def run_cno_paste(args):
    """--cno-paste: parse a screener paste, update the daily store, log your rule / screener / near misses."""
    from nbaprops.rules import screener as S
    text = sys.stdin.read() if args.cno_paste == "-" else open(args.cno_paste).read()
    if args.live:
        rows, skipped = S.live_compare(text, args.date)
        print(f"Live screener rows vs. both models ({len(rows)} NBA FanDuel props; {skipped} other rows skipped). Display only.")
        for r in rows:
            both = r["half_tag"] == "agrees" and r["bp_tag"] in ("agrees", "lean agrees")
            print(f"  {r['player']} {r['market']} {r['side']} {r['line']:g} {r['odds']} ({r['ev']} LW-M, {r['books']} books)"
                  f"{'  <- both models agree' if both else ''}\n      BP: {r['bp']} | Halftime: {r['half']} ({r['half_tag']}) | now {r['now']}")
        return
    every, _ = S.parse(text, args.date, S.NEAR_LOW)     # every row from -1% EV up (fixed settings) feeds the daily store
    plays, dropped = S.parse(text, args.date, 0.0 if args.all else None)
    st = S.settings(text, plays, args.devig, args.min_books, args.max_odds) if not args.dry else None
    if not args.dry:
        S.update_store(args.date, every)
        mine = [p for p in every if p["ev"] > 0 and p.get("proj_tag") in ("agrees", "lean agrees")]
        rows = S.log(mine, args.date, st, key=S.RULE_KEY)
        miss = S.log_near_misses(every, args.date)
        log_all_ev(args.date, [dict(p, src="CNO screener") for p in every if p["ev"] > 0])
        print(f"Your rule (CNO +EV and BettingPros agrees): {len(mine)} in this paste, {len(rows)} newly logged; "
              f"{len(S.rule_rows(args.date))} on today's list; {len(miss)} new near misses"
              + (" (" + "; ".join(f"{m['player']} {m['market']}: {m['cap_note']}" for m in miss) + ")" if miss else "") + ".")
        for r in rows:
            print(f"  NEW {r['player']} {r['market']} {r['side']} {float(r['line']):g} {float(r['odds']):+g}: CNO EV "
                  f"{100 * float(r['oa_ev']):+.2f}%, {r['kelly_u']}u ¼K  |  {r.get('proj_note')}")
    if st:
        inherited = [k for k in st["assumed"] if st.get(k) is not None]
        print("Screener settings: " + S.settings_text(st) + (f" (same as last paste: {', '.join(inherited)})" if inherited else ""))
        for w in st["warnings"]:
            print("  WARNING: " + w)
    print(f"CNO screener paste: {len(plays)} NBA FanDuel player props kept"
          + (f"; dropped {sum(dropped.values())} ({', '.join(f'{k} {v}' for k, v in sorted(dropped.items()))})" if dropped else ""))
    for p in plays:
        print(f"  {p['player']} {p['market']} {p['side']} {p['line']:g} {p['odds']:+g}: CNO EV {100 * p['ev']:+.1f}%, "
              f"fair {p['fair']:.3f}" + (f", {p['books']} books" if p["books"] else "") + f"  |  {p.get('proj_note', '–')}"
              + (f"  [{p['line_note']}]" if p.get("line_note") else ""))
    if args.dry or not plays:
        return
    rows = S.log([p for p in plays if p["ev"] >= S.MIN_EV], args.date, st)
    if args.all:
        sub = S.log([p for p in plays if p["ev"] < S.MIN_EV], args.date, st, key=S.KEY_SUB)
        print(f"Logged {len(sub)} under +{100 * S.MIN_EV:g}% as 'CNO screener (under +2%)', tracking only:")
        for r in sub:
            print(f"  {r['player']} {r['market']} {r['side']} {float(r['line']):g} {float(r['odds']):+g}: {r['kelly_u']}u ¼K"
                  f"{cap_text(r['cap_note'])}  |  {r.get('proj_note') or '–'}")
    notip = sum(1 for r in rows if not r.get("tip"))
    print(f"Logged {len(rows)} as 'CNO screener' (¼K on fair prob, caps applied)"
          + (f"; {notip} have no tip time (player not on today's BettingPros board) and won't count until matched" if notip else "")
          + ". " + counts_line(args.date))
    for r in rows:
        print(f"  {r['player']} {r['market']} {r['side']} {float(r['line']):g} {float(r['odds']):+g}: {r['kelly_u']}u ¼K{cap_text(r['cap_note'])}")


def run_proj(args):
    """Projection context for props the user sends (display only; never changes a decision)."""
    from nbaprops.pricing import projctx as P
    names = sorted({r["player"] for r in bp_learn._read(bp_learn.LEDGER)} |
                   {r["player"] for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{args.date}.csv")))}
                   if os.path.exists(os.path.join(HERE, f"bp_props_{args.date}.csv")) else set(), key=len, reverse=True)
    for part in [x for x in re.split(r"[;\n]", args.proj) if x.strip()]:
        t = part.split()
        mi = next((i for i, w in enumerate(t) if w.lower() in MARKET_WORDS), None)
        sm = re.search(r"\b(o|u|over|under)\s*(\d+(?:\.\d)?)", part, re.I)
        if mi is None or not sm:
            print(f"  {part.strip()}: need '<player> <market> <o|u><line>'")
            continue
        nm = norm(" ".join(t[:mi]))
        player = next((n for n in names if nm and (nm == norm(n) or norm(n).endswith(" " + nm))), " ".join(t[:mi]))
        side = "over" if sm.group(1).lower().startswith("o") else "under"
        c = P.context(args.date, player, MARKET_WORDS[t[mi].lower()], side, float(sm.group(2)))
        print(f"  {player} {MARKET_WORDS[t[mi].lower()]} {side} {float(sm.group(2)):g}: {c['text']}")


def run_placed(args):
    """Log a bet the user actually placed and match it to the day's logged BET (or CrazyNinja row), else off-board."""
    try:
        b = parse_placed(args.placed)
    except ValueError as e:
        sys.exit(f"Can't read that bet: {e}")
    led = bp_learn._read(bp_learn.LEDGER)
    nm = norm(b["name"])
    day = [r for r in led if r["date"] == args.date and r["strategy"] in bp_learn.BET_KEYS | bp_learn.CNO_KEYS
           and r["market"] == b["market"] and r["side"] == b["side"] and _f(r.get("line")) == b["line"]
           and (nm in norm(r["player"]) or norm(r["player"]).endswith(nm))]
    day.sort(key=lambda r: 0 if r["strategy"] in bp_learn.BET_KEYS else 1)
    m = day[0] if day else None
    if m is None:                                     # player on the board at all?
        cand = [r for r in led if r["date"] == args.date and (nm in norm(r["player"]) or norm(r["player"]).endswith(nm))]
        player = cand[0]["player"] if cand else b["name"]
        tip = cand[0].get("tip") if cand else ""
        ev = cand[0].get("event_id") if cand else ""
    else:
        player, tip, ev = m["player"], m.get("tip"), m.get("event_id")
    row = {"date": args.date, "placed_at": bp_learn.utc_now(), "player": player, "market": b["market"], "side": b["side"],
           "line": b["line"], "odds": b["odds"], "stake_u": b["stake"], "match": m["strategy"] if m else "off-board",
           "rule_stake": m.get("kelly_u") if m else 0, "rule_odds": m.get("odds") if m else "", "event_id": ev, "tip": tip,
           "raw": args.placed}
    rows = bp_learn._read(bp_learn.PLACED) + [row]
    bp_learn._write(bp_learn.PLACED, rows, bp_learn.PLACED_FIELDS)
    if m:
        print(f"Logged: {player} {b['market']} {b['side']} {b['line']:g} {b['odds']:+g} for {b['stake']:g}u "
              f"-> matches {m['strategy']} (rule stake {m.get('kelly_u')}u at {float(m['odds']):+g}"
              f"{', ' + m['cap_note'] if m.get('cap_note') else ''}).")
    else:
        print(f"Logged OFF-BOARD: {player} {b['market']} {b['side']} {b['line']:g} {b['odds']:+g} for {b['stake']:g}u "
              f"(no logged BET at that line).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=bp_props.eastern_today())
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--picks", action="store_true", help="today's logged picks (latest run)")
    g.add_argument("--top", type=int, help="top N FanDuel props by Balanced")
    g.add_argument("--board", action="store_true", help="daily: one Unabated call prices every FanDuel line; BETs; "
                                                        "The Odds API only for the shortlist Unabated can't price")
    g.add_argument("--close", action="store_true", help="6:30 PM: one Unabated call logs CLV for today's BETs")
    g.add_argument("--cno-paste", metavar="FILE", help="CrazyNinja +EV screener text (file, or - for stdin): "
                                                         "NBA FanDuel player props -> 'CNO screener' record (no API calls)")
    g.add_argument("--half-paste", metavar="FILE", help="halftime live odds paste (file or -): CNO worst case; logs HALF BETs")
    g.add_argument("--proj", metavar="PROPS", help="BettingPros projection context for props you send, from today's saved "
                                                   "pull (0 requests): \"Toppin 3PM o1.5; Brunson PTS u27.5\"")
    g.add_argument("--picks-paste", metavar="FILE", help="outside picks (Reddit/X/RotoWire cards), one per line: logged per "
                                                          "--source as their own record and graded from ESPN box scores")
    g.add_argument("--placed", metavar="BET", help="log a bet you placed, e.g. \"Toppin 3PM o1.5 +172 0.75u\" (no API calls)")
    g.add_argument("--watch", action="store_true", help="free Unabated scan every 20 min from the first pull to the last tip; "
                                                        "alerts on new BETs, EV below 0; logs closing CLV per game")
    ap.add_argument("--source", default="Outside card", help="--picks-paste: source label for the record")
    ap.add_argument("--dry", action="store_true", help="--cno-paste: parse and show, log nothing")
    ap.add_argument("--all", action="store_true", help="--cno-paste: also log +EV rows under +2% (separate tracking record)")
    ap.add_argument("--live", action="store_true", help="--cno-paste: live rows vs. BettingPros projection and halftime "
                                                        "expected final (display only)")
    ap.add_argument("--devig", help="--cno-paste: your screener's devig method (logged; warns if it changes)")
    ap.add_argument("--min-books", type=int, help="--cno-paste: your screener's min books setting")
    ap.add_argument("--max-odds", type=float, help="--cno-paste: your screener's max odds setting, e.g. +500")
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
    if args.placed:
        return run_placed(args)
    if args.cno_paste:
        return run_cno_paste(args)
    if args.picks_paste:
        from nbaprops.rules import outside as O
        text = sys.stdin.read() if args.picks_paste == "-" else open(args.picks_paste).read()
        blocks = O.parse_blocks(text)
        if blocks:                                     # a file with date headers: each block is its own date
            for d, block in blocks.items():
                picks, skipped = O.parse(block)
                rows = O.log(picks, d, args.source) if not args.dry else []
                graded, pend = O.grade(d) if not args.dry else (0, 0)
                print(f"{d}: {len(picks)} picks read, {len(skipped)} skipped, {len(rows)} logged, {graded} graded, {pend} pending")
            return
        picks, skipped = O.parse(text)
        print(f"Outside picks ({args.source}, {args.date}): {len(picks)} player props read; {len(skipped)} skipped"
              + (": " + " || ".join(skipped[:6]) if skipped else ""))
        if args.dry:
            for p in picks:
                price = "" if p["odds"] is None else format(p["odds"], "+g")
                print(f"  {p['player']} {p['market']} {p['side']} {p['line']:g} {price}")
            return
        rows = O.log(picks, args.date, args.source)
        graded, pend = O.grade(args.date)
        print(f"Logged {len(rows)} new; graded {graded} from ESPN box scores" + (f", {pend} pending (game not final or name not matched)" if pend else "") + ".")
        return
    if args.proj:
        return run_proj(args)
    if args.half_paste:
        from nbaprops.rules import half as bp_half
        return bp_half.run(args)

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
