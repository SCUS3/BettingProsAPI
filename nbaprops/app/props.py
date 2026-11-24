#!/usr/bin/env python3
"""
Pull tonight's NBA player props from the BettingPros Consumer API, priced at
FanDuel, and write them to a CSV ranked by projection gap.

Usage:
    # one-time setup: put BP_API_KEY=your-key-here in a .env file next to this script
    # (never paste the key into chat; .env is git-ignored)
    python3 bp_props.py                    # today's slate
    python3 bp_props.py --date 2026-11-19  # another date
    python3 bp_props.py --max-requests 45  # 2 PM full pull: /props (all books) + /offers (FanDuel) + /events
    python3 bp_props.py --reprice          # 6:30 PM: FanDuel's current lines from /offers only, reusing 2 PM projections

Uses only the Python standard library. Output: bp_props_<date>.csv plus a
top-15 printout.

Markets pulled by default (full game only): points, rebounds, assists, threes,
pts+ast, pts+reb, pts+reb+ast, reb+ast. Market and FanDuel book IDs are looked up once and
cached in bp_cache.json next to the script, so later runs skip those requests.

    python3 bp_props.py --list-markets     # show every NBA prop market and which ones are selected

Request budget: Free/Plus keys get 100 requests per day and 20 props per page;
Elite gets 1,000 per day and 50 per page. The allowance resets at midnight UTC
(7 PM Eastern in winter, 8 PM in summer).
"""

from nbaprops import config as C  # thresholds, caps and settings
import argparse
import csv
import datetime as dt
import json
import math
import os
import sys

from nbaprops.pricing import devig as _odds  # noqa: E402 -- shared odds math
from nbaprops.rules import ledger as bp_learn  # noqa: E402  (pick tracking, grading, self-learning)
from nbaprops.sources import rotowire as bp_rotowire  # noqa: E402  (projected lineups and injuries)
from nbaprops.sources import espn as bp_boxscore  # noqa: E402  (final box scores: ESPN, NBA.com fallback)
from nbaprops.sources import bettingpros as BP  # noqa: E402  request state: BP.REQUESTS_MADE / BP.RUN_CAP / BP.PULL_LOG
from nbaprops.sources.bettingpros import (  # noqa: E402 -- moved, re-exported for existing callers
    DAILY_LIMIT,
    DISABLED,
    RunCapReached,
    WANTED,
    fetch_pages,
    find_fanduel,
    get,
    load_cache,
    load_dotenv,
    nba_markets,
    offers_params,
    pages_left,
    parse_fd_offers,
    props_params,
    pull_props,
    save_cache,
    used_today,
)
from nbaprops.output.common import (  # noqa: E402 -- moved, re-exported for existing callers
    load_value,
)
from nbaprops.output.note import (  # noqa: E402 -- moved, re-exported for existing callers
    write_top_by_market,
)
from nbaprops.output.dashboard import (  # noqa: E402 -- moved, re-exported for existing callers
    write_dashboard,
)


def eastern_today():
    """Today's date in Eastern time; falls back to the computer's own date (Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()

MAX_PICK_LOOKUPS = C.BP_MAX_PICK_LOOKUPS  # nbaprops/config.py





























def first(*vals):
    """First value that isn't None."""
    return next((v for v in vals if v is not None), None)


def implied(american):
    """Implied probability of American odds (None-safe)."""
    return None if american is None else _odds.implied_prob(american)


PERF_WINDOWS = {"form_l5": "last_5", "form_l10": "last_10", "form_l20": "last_20", "form_season": "season", "form_h2h": "h2h"}
# Book-agnostic BettingPros data per prop, kept in the CSV so the 6:30 re-price can reuse it.
BASE_FIELDS = ["player", "player_id", "team", "injury", "market", "event_id", "bp_proj", "bp_rec_side", "bp_line", "bp_book",
               "bp_win_prob", "bp_ev", "bp_rating", "market_prob", "market_ev", "opp_rank_raw", "dvp_softness",
               "consensus_line", "consensus_odds", "perf", "streak", "is_scored", "actual"]


def base_from_prop(p, labels):
    """BettingPros' book-agnostic data for one /props row."""
    part = p.get("participant") or {}
    pl = part.get("player") or {}
    proj = p.get("projection") or {}
    over, under = p.get("over") or {}, p.get("under") or {}
    rec = proj.get("recommended_side")
    side = over if rec == "over" else under if rec == "under" else over
    rank = ((p.get("extra") or {}).get("opposition_rank") or {}).get("rank")
    perf = p.get("performance") or {}
    scored = p.get("scoring") or {}
    return {
        "player": part.get("name"), "player_id": str(part.get("id") or ""), "team": pl.get("team"),
        "injury": pl.get("injury_status"), "market": labels.get(p.get("market_id"), p.get("market_id")),
        "event_id": p.get("event_id"), "bp_proj": proj.get("value"), "bp_rec_side": rec,
        "bp_line": side.get("line"), "bp_book": side.get("book"),
        "bp_win_prob": first(proj.get("probability"), side.get("probability")),
        "bp_ev": first(proj.get("expected_value"), side.get("expected_value")),
        "bp_rating": first(proj.get("bet_rating"), side.get("bet_rating")),
        "market_prob": proj.get("market_probability"), "market_ev": proj.get("market_expected_value"),
        "opp_rank_raw": rank, "dvp_softness": (31 - rank) if rank else None,     # BettingPros 1 = toughest -> 1 = softest
        "consensus_line": first(side.get("consensus_line"), over.get("consensus_line")),
        "consensus_odds": side.get("consensus_odds"),
        "perf": json.dumps({w: perf[w] for w in PERF_WINDOWS.values() if isinstance(perf.get(w), dict)}),
        "streak": f"{perf.get('streak')} {perf.get('streak_type')}" if perf.get("streak") else None,
        "is_scored": scored.get("is_scored"), "actual": scored.get("actual") if scored.get("is_scored") else None,
    }


def _perf(base):
    try:
        return json.loads(base.get("perf") or "{}")
    except (TypeError, ValueError):
        return {}


def _form_side(perf, side):
    out = {}
    for col, win in PERF_WINDOWS.items():
        w = perf.get(win) or {}
        n = (w.get("over") or 0) + (w.get("under") or 0)
        out[col] = f"{w.get(side) or 0}-{n}" if n and side else None
    return out


def price_row(base, offer, tip, pulled_at, fd):
    """One board row: BettingPros' projection (if any) against FanDuel's own line and price.
    Side, gap, win chance, EV and placeholder status are all recomputed at FanDuel's line."""
    proj = _num(base.get("bp_proj"))
    bp_line, bp_side = _num(base.get("bp_line")), base.get("bp_rec_side") or None
    o, u = offer.get("over") or {}, offer.get("under") or {}
    line = _num(first(o.get("line"), u.get("line")))
    perf = _perf(base)
    noproj = proj is None
    if not noproj and line is not None and proj != line:
        side = "over" if proj > line else "under"
    else:                                   # no projection, or projection exactly on FanDuel's line: lean on form
        fo, fu = form_score(_form_side(perf, "over"))[0], form_score(_form_side(perf, "under"))[0]
        if fo is not None or fu is not None:
            side = "over" if (fo or 0) >= (fu or 0) else "under"
        elif o.get("odds") is not None or u.get("odds") is not None:    # no form either: the longer price
            side = "over" if (_num(o.get("odds")) or -9999) >= (_num(u.get("odds")) or -9999) else "under"
        else:
            side = None
    sel = o if side == "over" else u if side == "under" else {}
    if sel.get("line") is not None and _num(sel.get("line")) != line:
        line = _num(sel.get("line"))       # the two sides can sit on different lines; use the bet's own
    same = (not noproj and bp_line is not None and line == bp_line and side == bp_side)
    diff = None if noproj or line is None else round(proj - line, 2)
    odds = _num(sel.get("odds"))
    row = {
        "player": base.get("player") or offer.get("player"), "team": base.get("team") or offer.get("team"),
        "injury": base.get("injury") or None, "market": base.get("market"), "rec_side": side, "line": line,
        "fd_odds_rec": odds, "price_book": fd, "price_is_fanduel": odds is not None,
        "bp_proj": proj, "diff": diff, "pct_gap": round(100 * diff / line, 1) if diff is not None and line else None,
        "bp_line": bp_line, "line_note": "" if noproj or bp_line is None or bp_line == line else f"FD {fmt(line)} vs BP {fmt(bp_line)}",
        "noproj": noproj,
        # BettingPros' win chance and rating are for its own line and side; its EV also for its own book's price.
        "bp_win_prob": base.get("bp_win_prob") if same else None,
        "bp_ev": base.get("bp_ev") if same and str(base.get("bp_book")) == str(fd) else None,
        "bp_rating": base.get("bp_rating") if same else None,
        "market_prob": base.get("market_prob"), "market_ev": base.get("market_ev"),
        "fd_fair_prob": sel.get("fair"), "fd_market_ev": sel.get("mev"),
        "implied_prob": round(implied(odds), 4) if odds is not None else None,
        "opp_rank_raw": base.get("opp_rank_raw"), "dvp_softness": _num(base.get("dvp_softness")),
        "over_odds": _num(o.get("odds")) if o and u and o.get("line") == u.get("line") else None,
        "under_odds": _num(u.get("odds")) if o and u and o.get("line") == u.get("line") else None,
        "consensus_line": base.get("consensus_line"), "consensus_odds": base.get("consensus_odds"),
        **_form_side(perf, side),
        "streak": base.get("streak"), "is_scored": base.get("is_scored"), "actual": base.get("actual"),
        "event_id": base.get("event_id") or offer.get("event_id"), "tip": tip, "pulled_at": pulled_at,
        "player_id": base.get("player_id") or offer.get("player_id"), "bp_rec_side": bp_side, "bp_book": base.get("bp_book"),
        "perf": base.get("perf") or "{}", "line_moved": False,
    }
    return row


def stale_offer(r):
    """A 2 PM CSV row's FanDuel price as an offer, for games a picks-only re-price leaves alone."""
    line, side, odds = _num(r.get("line")), r.get("rec_side"), _num(r.get("fd_odds_rec"))
    o = {"line": line, "odds": _num(r.get("over_odds")) if side != "over" else odds}
    u = {"line": line, "odds": _num(r.get("under_odds")) if side != "under" else odds}
    rec = o if side == "over" else u
    rec.update(fair=_num(r.get("fd_fair_prob")), mev=_num(r.get("fd_market_ev")))
    off = {"event_id": int(float(r["event_id"])), "market_id": r["market"], "player_id": r.get("player_id") or "",
           "player": r.get("player"), "team": r.get("team")}
    for side_name, d in (("over", o), ("under", u)):
        if d["odds"] is not None:
            off[side_name] = d
    return off


def _tip(tips, ev):
    if ev in (None, ""):
        return None
    try:
        return tips.get(ev) or tips.get(int(float(ev)))
    except (TypeError, ValueError):
        return tips.get(ev)


def _name(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def build_rows(bases, offers, labels, tips, pulled_at, fd):
    """Join FanDuel offers to BettingPros projections on game + player + market (player id, then name).
    Projections without a FanDuel line stay in the CSV (not on the board) for re-pricing and learning."""
    by_name = {(k[0], k[1], _name(b.get("player"))): k for k, b in bases.items()}
    used, rows = set(), []
    for (ev, mid, pid), off in offers.items():
        mk = labels.get(mid, mid)
        k = (str(ev), mk, pid)
        if k not in bases:
            k = by_name.get((str(ev), mk, _name(off.get("player"))), k)
        base = bases.get(k) or {"market": mk, "event_id": ev, "player": off.get("player"), "team": off.get("team"),
                                "player_id": pid}
        used.add(k)
        r = price_row(base, off, _tip(tips, ev), pulled_at, fd)
        old_line = _num(base.get("line_seen"))
        r["line_moved"] = old_line is not None and old_line != r["line"]
        rows.append(r)
    missing = 0
    for k, b in bases.items():
        if k in used:
            continue
        missing += 1
        ev = b.get("event_id")
        r = price_row(b, {}, _tip(tips, ev), pulled_at, fd)
        r.update(rec_side=b.get("bp_rec_side") or None, line=_num(b.get("bp_line")), price_book=None, price_is_fanduel=False,
                 fd_odds_rec=None, line_note="no FanDuel line")
        bl = _num(b.get("bp_line"))
        if r["bp_proj"] is not None and bl:
            r["diff"] = round(r["bp_proj"] - bl, 2)
            r["pct_gap"] = round(100 * r["diff"] / bl, 1)
        r.update(_form_side(_perf(b), r["rec_side"]))
        rows.append(r)
    return rows, missing


def budget_math(used_before, grading, pull, reprice_full, cap, label, enforce=True):
    """Print today's request math and decide the 6:30 re-price. Returns 'full', 'picks' or 'skip'.
    Rule: never trim the 2 PM pull. If pull + full re-price fits, re-price every game; if not, re-price only the
    games with logged picks (decided once picks are known, see picks_reprice_decision); if not even 1 request is
    left after the pull, skip it (the 2 PM picks count). Stops only if the pull itself can't fit or passes --max-requests."""
    this_run = grading + pull
    left = DAILY_LIMIT - used_before
    print(f"Budget ({label}): {used_before} used earlier today + this run {this_run} (grading {grading} + pull {pull}) "
          f"= {used_before + this_run} of {DAILY_LIMIT}; full 6:30 re-price ~{reprice_full} "
          f"-> {used_before + this_run + reprice_full}")
    if not enforce:                       # estimate only: the exact check after /props page 1 decides
        return None
    if this_run > left:
        sys.exit(f"Stopped: this run needs ~{this_run} but only {left} are left today. {BP.REQUESTS_MADE} used so far this run.")
    if cap is not None and this_run > cap:
        sys.exit(f"Stopped: this run needs ~{this_run}, over --max-requests {cap}. {BP.REQUESTS_MADE} used so far this run.")
    after = left - this_run
    if reprice_full <= after:
        return "full"
    if after < 1:
        print("6:30 re-price skipped: nothing left after this pull. The 2 PM picks count.")
        return "skip"
    print(f"Full re-price ({reprice_full}) won't fit in the {after} left after this pull: re-price only the games "
          f"with logged picks (cost decided once the picks are logged).")
    return "picks"


def picks_reprice_decision(date, pulled_at, mode, after, reprice_full, fd_lines_by_game, page_limit):
    """After the 2 PM picks are logged: cost of re-pricing only their games, and the final mode."""
    games = {str(r.get("event_id")) for r in bp_learn._read(bp_learn.LEDGER)
             if r["date"] == date and r.get("pulled_at") == pulled_at and r.get("event_id")}
    lines = sum(fd_lines_by_game.get(g, 0) for g in games)
    picks_cost = max(1, math.ceil(lines / page_limit)) if games else 0
    if mode == "picks":
        mode = "picks" if games and picks_cost <= after else "skip"
    print(f"6:30 re-price: full ~{reprice_full} vs. picks-only ~{picks_cost} ({len(games)} games with picks, "
          f"{lines} FanDuel lines); {after} left after this run -> " +
          {"full": "full re-price", "picks": "picks-only re-price", "skip": "skipped (the 2 PM picks count)"}[mode])
    return mode, sorted(games), picks_cost


def warn_started(tips, pulled_at):
    """Games still to tip (only these are priced); warn when some have started."""
    from nbaprops.rules import tipoff as bp_tipoff  # bettable until start + 10 min unless ESPN shows it under way
    upcoming = {int(e) for e, t in tips.items() if e and (not t or bp_tipoff.is_open(t, None, pulled_at))}
    started = [t for e, t in tips.items() if t and e and int(e) not in upcoming]
    if started:
        first_tip = min(t for t in tips.values() if t)
        print(f"Warning: {len(started)} of {len(tips)} games had already tipped at this pull; they're skipped "
              f"(lines thinned, picks wouldn't count). Pull before the first tip ({first_tip} UTC).")
    return upcoming


def save_pull(date, pulled_at, fd, market_ids, events, tips, props, offers, rows, kind="full", extra=None, only=None):
    """Save a pull's pagination and per-game counts to history/<date>/pull_<time>.json."""
    fd_rows = [r for r in rows if r["price_is_fanduel"]]
    per_game = {}
    for r in fd_rows:
        g = per_game.setdefault(str(r["event_id"]), {"fanduel_lines": 0, "with_projection": 0})
        g["fanduel_lines"] += 1
        g["with_projection"] += 0 if r["noproj"] else 1
    bp_learn.save_pull_log(date, pulled_at, {
        "kind": kind, "pulled_at": pulled_at, "requests": BP.REQUESTS_MADE, "pages": BP.PULL_LOG,
        "props_rows": len(props), "fanduel_offers": len(offers), "board_rows": len(fd_rows),
        "fanduel_book_id": fd, "markets": market_ids, "per_game": per_game,
        "events": [{"id": e.get("id"), "tip": tips.get(e.get("id")),
                    "started": bool(tips.get(e.get("id")) and tips[e.get("id")] <= pulled_at),
                    "skipped": bool(only is not None and e.get("id") not in only)} for e in events],
        **(extra or {})}, prefix="pull_" if kind == "full" else kind + "_")









# ---------------------------------------------------------------- scoring
# Each prop gets a 0-100 score built from three parts (weights below), then an
# injury multiplier. The parts are scaled so threes, rebounds and points compete
# fairly: the gap is measured in standard deviations, not % of the line.
W_EDGE, W_EV, W_DVP = C.SCORE_WEIGHTS  # nbaprops/config.py
INJURY_MULT = {"": 1.0, "probable": 1.0, "prob": 1.0, "dtd": 0.85, "questionable": 0.85, "ques": 0.85, "gtd": 0.85,
               "doubtful": 0.5, "doubt": 0.5, "out": 0.0, "ofs": 0.0}


def _num(v):
    return _odds.num(v)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def score_row(r):
    """Add model_prob, model_ev, s_edge, s_ev, s_dvp and score (0-100) to a row in place."""
    line, proj, odds = _num(r.get("line")), _num(r.get("bp_proj")), _num(r.get("fd_odds_rec"))
    side = (r.get("rec_side") or "").lower()
    if line is None or proj is None or side not in ("over", "under"):
        r.update(placeholder=False, ph_demoted=False, model_prob=None, model_ev=None, s_edge=None, s_ev=None, s_dvp=None, score=None)
        return r

    # 1. Edge: how many standard deviations the projection sits from the line.
    #    Counting stats spread roughly like sqrt(mean); 1.1x allows for extra variance.
    sd = 1.1 * math.sqrt(max(proj, line, 0.5))
    z = abs(proj - line) / sd
    s_edge = min(z / 0.75, 1.0) * 100                    # 0.75 SD or more = full marks
    # Whole-number projection exactly 0.5 off the line = a rounded placeholder, not a real projection:
    # it claims zero edge, and its win chance is FanDuel's no-vig probability (so EV is just the price).
    placeholder = bp_learn.is_placeholder(proj, line)
    if placeholder:
        s_edge = 0.0

    # 2. EV at FanDuel's price: win chance (BettingPros' if given, else from the
    #    projection; FanDuel's no-vig probability for placeholders) against the odds.
    if placeholder:
        p = bp_learn.no_vig(r)
        if p is None:
            p = bp_learn.implied(odds) if odds is not None else 0.5
    else:
        p = _num(r.get("bp_win_prob"))
        if p is not None and p > 1:
            p /= 100
        if p is None:
            p = _phi(z)                                  # chance the recommended side hits
    evs = []
    if odds is not None:
        dec = 1 + (odds / 100 if odds > 0 else 100 / -odds)
        evs.append(p * dec - 1)
    # Blend in published EVs when present. BettingPros' EV comes from its projection, so placeholders skip it;
    # FanDuel market EV (from /offers) is price-only and applies to every prop.
    for k in (("fd_market_ev",) if placeholder else ("fd_market_ev", "bp_ev")):
        v = _num(r.get(k))
        if v is not None:
            evs.append(v / 100 if abs(v) > 1 else v)
    ev = sum(evs) / len(evs) if evs else None
    s_ev = 50.0 if ev is None else min(max((ev + 0.10) / 0.30, 0.0), 1.0) * 100   # -10% -> 0, +20% -> 100

    # 3. Matchup: soft defense helps an over, tough defense helps an under.
    soft = _num(r.get("dvp_softness"))                   # 1 = softest, 30 = toughest
    if soft is None:
        s_dvp = 50.0
    else:
        s_dvp = ((30 - soft) if side == "over" else (soft - 1)) / 29 * 100

    mult = INJURY_MULT.get((r.get("injury") or "").strip().lower(), 0.85)
    score = (W_EDGE * s_edge + W_EV * s_ev + W_DVP * s_dvp) * mult
    # Placeholders rank below every real projection unless the price alone is +EV (FanDuel market EV > 0, from /offers).
    mev = _num(r.get("fd_market_ev"))
    demoted = placeholder and not (mev is not None and mev > 0)
    r.update(placeholder=placeholder, ph_demoted=demoted, model_prob=round(p, 3), model_ev=None if ev is None else round(ev, 3),
             s_edge=round(s_edge), s_ev=round(s_ev), s_dvp=round(s_dvp), score=round(score))
    return r


def ranked(rows, key):
    """Sort descending by key: real projections (3PM, P+R), then side-lean markets and demoted placeholders,
    then lines with no projection."""
    def tier(r):
        if bp_learn._truthy(r.get("noproj")):
            return 2
        return 1 if bp_learn._truthy(r.get("ph_demoted")) or bp_learn._truthy(r.get("lean_only")) else 0
    return sorted(rows, key=lambda r: (tier(r), -(key(r) or 0)))


# ------------------------------------------------- balancing algorithms
# Three independent signals that check the main score from other angles, plus a
# Kelly stake. Missing data scores a neutral 50 so it neither helps nor hurts.
W_MINE, W_FORM, W_MKT, W_BP = 0.40, 0.25, 0.20, 0.15
FORM_WEIGHTS = {"form_l5": 0.10, "form_l10": 0.30, "form_l20": 0.25, "form_season": 0.25, "form_h2h": 0.10}


def _clamp01(x):
    return min(max(x, 0.0), 1.0)


def form_score(r):
    """Recent hit rate of the recommended side vs. this line, shrunk toward 50% on small samples."""
    total = wsum = 0.0
    for k, w in FORM_WEIGHTS.items():
        v = r.get(k)
        if not v:
            continue
        hits, n = (int(x) for x in str(v).split("-"))
        if n:
            total += w * (hits + 1) / (n + 2)            # +1/+2 = shrink toward 50%
            wsum += w
    if not wsum:
        return None, 50.0
    rate = total / wsum
    return round(rate, 3), _clamp01((rate - 0.35) / 0.40) * 100   # 35% -> 0, 75% -> 100


def market_score(r):
    """Market value: model vs. FanDuel's no-vig price, and FanDuel's line vs. the consensus line."""
    parts, side = [], (r.get("rec_side") or "").lower()
    p, fair = _num(r.get("model_prob")), no_vig_prob(r)
    nv_edge = None
    if p is not None and fair is not None:
        nv_edge = p - fair
        parts.append(_clamp01((nv_edge + 0.05) / 0.15))  # -5 pts -> 0, +10 pts -> 1
    line, cons, proj = _num(r.get("line")), _num(r.get("consensus_line")), _num(r.get("bp_proj"))
    if line is not None and cons is not None:
        sd = 1.1 * math.sqrt(max(proj if proj is not None else line, line, 0.5))
        better = (cons - line) if side == "over" else (line - cons)    # + = FanDuel line is friendlier
        parts.append(_clamp01(0.5 + better / sd / 0.6))
    return (None if nv_edge is None else round(nv_edge, 3)), (sum(parts) / len(parts) * 100 if parts else 50.0)


def no_vig_prob(r):
    """FanDuel's fair (vig-free) probability for the recommended side, if both sides are priced."""
    o, u = _num(r.get("over_odds")), _num(r.get("under_odds"))
    if o is None or u is None:
        return None
    io, iu = implied(o), implied(u)
    return (io if (r.get("rec_side") or "").lower() == "over" else iu) / (io + iu)


KELLY_MAX = C.MODEL_KELLY_MAX  # nbaprops/config.py
KELLY_SCALE = 1.0          # set by finish(): 0.5 (max 1.25u) until calibrated
CALIBRATION_MIN_PICKS = C.CALIBRATION_MIN_PICKS  # nbaprops/config.py


def calibration_status():
    """Calibrated = the combined pick record ('each prop once') has 50+ graded W/L picks and its Brier
    score beats FanDuel's no-vig Brier. Until then stakes are halved (half-Kelly cap, max 1.25u)."""
    c = bp_learn.record()["combined"]
    n = c["w"] + c["l"]
    ok = n >= CALIBRATION_MIN_PICKS and c["brier"] is not None and c["brier"] < c["brier_mkt"]
    if ok:
        note = f"Full Kelly sizing: model Brier {c['brier']} beats market {c['brier_mkt']} on {n} graded picks."
    else:
        why = (f"{n} of {CALIBRATION_MIN_PICKS} graded picks" if n < CALIBRATION_MIN_PICKS
               else f"model Brier {c['brier']} vs market {c['brier_mkt']} on {n} picks")
        note = f"Half-Kelly: model not yet calibrated ({why}). Stakes halved, max {KELLY_MAX / 2}u."
    return ok, note


def kelly_units(r):
    """Quarter-Kelly stake in units (1u = 1% of bankroll), capped at 2.5u; halved (max 1.25u) until the model
    is calibrated. The model's win chance is blended 50/50 with FanDuel's no-vig price so stakes stay realistic."""
    p, odds = _num(r.get("model_prob")), _num(r.get("fd_odds_rec"))
    if p is None or odds is None:
        return None
    fair = no_vig_prob(r)
    if fair is not None:
        p = (p + fair) / 2
    b = odds / 100 if odds > 0 else 100 / -odds
    f = (p * (b + 1) - 1) / b
    return round(min(max(f / 4 * 100 * KELLY_SCALE, 0.0), KELLY_MAX * KELLY_SCALE), 1)


# BettingPros publishes PTS, REB, AST, P+A and PRA projections as whole numbers, nearly always the line +-0.5
# (every saved pull: 100% whole). That's a side lean, not a projection. Only these markets carry real projections:
REAL_PROJ_MARKETS = C.REAL_PROJ_MARKETS  # nbaprops/config.py
W_NP_FORM, W_NP_DVP, W_NP_MKT = 0.25, 0.25, 0.20   # no projection: form, matchup and market value only (renormalized)


def lean_row(r):
    """PTS/REB/AST/P+A/PRA: BettingPros' projection is only a side lean. Rank on form, DvP and market value
    (like a line with no projection), keep the lean as the side, no edge / projection EV / Kelly, never a pick."""
    noproj_row(r)
    r.update(lean_only=True, bp_lean=r.get("rec_side"), pct_gap=None)
    return r


def noproj_row(r):
    """A FanDuel line BettingPros has no projection for: ranked on form, DvP and market value only.
    No edge, EV, score or Kelly, never a logged pick, and listed after every projected prop."""
    side = (r.get("rec_side") or "").lower()
    r.update(placeholder=False, ph_demoted=False, model_prob=None, model_ev=None, s_edge=None, s_ev=None, score=None,
             s_bp=None, kelly_u=None, learned_prob=None, learned=None)
    if side not in ("over", "under"):
        r.update(s_dvp=None, form_rate=None, s_form=None, nv_edge=None, s_market=None, signals=None, balanced=None)
        return r
    soft = _num(r.get("dvp_softness"))
    s_dvp = 50.0 if soft is None else ((30 - soft) if side == "over" else (soft - 1)) / 29 * 100
    rate, s_form = form_score(r)
    _, s_mkt = market_score(r)
    mult = INJURY_MULT.get((r.get("injury") or "").strip().lower(), 0.85)
    bal = (W_NP_FORM * s_form + W_NP_DVP * s_dvp + W_NP_MKT * s_mkt) / (W_NP_FORM + W_NP_DVP + W_NP_MKT) * mult
    agree = sum(x >= 60 for x in (s_dvp, s_form, s_mkt))
    r.update(s_dvp=round(s_dvp), form_rate=rate, s_form=round(s_form), nv_edge=None, s_market=round(s_mkt),
             signals=f"{agree}/3", balanced=round(bal))
    return r


def balance_row(r):
    """Add form_rate, s_form, nv_edge, s_market, s_bp, kelly_u and balanced (0-100)."""
    if r.get("score") is None:
        r.update(form_rate=None, s_form=None, nv_edge=None, s_market=None, s_bp=None, kelly_u=None,
                 signals=None, balanced=None)
        return r
    rate, s_form = form_score(r)
    nv, s_mkt = market_score(r)
    rating = _num(r.get("bp_rating"))
    s_bp = 50.0 if rating is None else _clamp01((rating - 1) / 4) * 100
    mult = INJURY_MULT.get((r.get("injury") or "").strip().lower(), 0.85)
    bal = W_MINE * r["score"] + (W_FORM * s_form + W_MKT * s_mkt + W_BP * s_bp) * mult
    # how many of the independent signals lean the same way (>= 60)
    agree = sum((x or 0) >= 60 for x in (r["s_edge"], r["s_ev"], r["s_dvp"], s_form, s_mkt, s_bp))
    r.update(form_rate=rate, s_form=round(s_form), nv_edge=nv, s_market=round(s_mkt), s_bp=round(s_bp),
             kelly_u=kelly_units(r), signals=f"{agree}/6", balanced=round(bal))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=eastern_today(),
                    help="slate date (defaults to today in Eastern time, so cloud machines on UTC still get the right night)")
    ap.add_argument("--location", default="NY")
    ap.add_argument("--markets", default="", help="colon-delimited market ids (overrides the eight defaults)")
    ap.add_argument("--list-markets", action="store_true", help="print NBA prop markets and exit")
    ap.add_argument("--refresh", action="store_true", help="re-fetch cached book and market IDs")
    ap.add_argument("--page-limit", type=int, default=20, help="20 for Free/Plus, 50 for Elite")
    ap.add_argument("--reprice", action="store_true",
                    help="6:30 PM: re-price the last full pull at FanDuel's current lines (/offers only, no grading)")
    ap.add_argument("--unfiltered-props", action="store_true",
                    help="full pull: fetch /props for every player (no player_id filter); ~5-6 pages per game")
    ap.add_argument("--force-reprice", action="store_true", help="run --reprice even if the 2 PM pull skipped it for budget")
    ap.add_argument("--max-requests", type=int, default=None,
                    help="stop (after the page-1 requests that measure the run) if this run would use more than this")
    ap.add_argument("--no-grade", action="store_true", help="skip grading finished slates this run")
    ap.add_argument("--only-events", metavar="IDS", help="pull only these BettingPros event ids (colon/comma list); "
                                                            "other games keep their rows and picks from their last pull")
    ap.add_argument("--grade", metavar="DATE", help="only grade the saved slate for DATE, re-learn, and exit")
    ap.add_argument("--measure", action="store_true",
                    help="3 requests: slate prop counts (total_items) with the FanDuel filter, without it, and with the default ev_threshold")
    ap.add_argument("--from-csv", action="store_true",
                    help="re-score an existing bp_props_<date>.csv and rewrite the note (no API requests)")
    args = ap.parse_args()
    BP.RUN_CAP = args.max_requests

    if args.from_csv:
        path = f"bp_props_{args.date}.csv"
        try:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            sys.exit(f"{path} not found. Run without --from-csv first.")
        typed_csv_rows(rows)
        finish(rows, args.date)
        return

    load_dotenv()
    key = os.environ.get("BP_API_KEY")
    if not key:
        sys.exit("No BP_API_KEY found. Put a line like BP_API_KEY=your-key in a .env file next to this script.")

    cache = {} if args.refresh else load_cache()
    fd = find_fanduel(key, args.location, cache)
    markets = nba_markets(key, cache, refresh=args.refresh)
    save_cache(cache)
    labels = {mid: (tag or lbl) for mid, (tag, lbl) in markets.items()}

    if args.list_markets:
        for mid, (tag, lbl) in sorted(markets.items(), key=lambda kv: (kv[1][0] is None, kv[0])):
            print(f"{mid:>6}  {'KEEP ' + tag if tag else '     -':10}  {lbl}")
        return

    if args.markets:
        market_ids = args.markets
    else:
        picked = {mid: tag for mid, (tag, _) in markets.items() if tag and tag not in DISABLED}
        missing = sorted(set(WANTED.values()) - DISABLED - set(picked.values()))
        if missing:
            print(f"Warning: no market matched {', '.join(missing)}. Run --list-markets and pass --markets by hand.")
        if not picked:
            sys.exit("No markets matched. Run --list-markets.")
        market_ids = ":".join(str(m) for m in sorted(picked))
        print("Markets: " + ", ".join(f"{t} ({m})" for m, t in sorted(picked.items(), key=lambda kv: kv[1])))

    # Grade finished slates (yesterday's picks etc.), then re-learn from every graded prop.
    to_grade = [args.grade] if args.grade else ([] if args.no_grade else bp_learn.pending_dates(args.date)[-3:])
    if args.measure:
        measure(key, args.date, fd, market_ids)
        return
    if args.reprice:
        reprice(key, args, fd, market_ids, labels)
        return

    tag_ids = {tag: mid for mid, (tag, _) in markets.items() if tag}
    for d in to_grade:
        finals = bp_learn.known_finals(d)
        capped = False
        voids, box = set(), None
        try:                                             # public box scores first: ESPN, NBA.com fallback (0 BettingPros requests)
            box = bp_boxscore.finals(d)
            got, voids, unmatched = bp_learn.finals_from_box(d, box)
            finals.update({k: v for k, v in got.items() if k not in finals})
            print(f"Box scores {d}: {len(box['games'])} final games ({', '.join(sorted(set(box['source'].values()))) or 'none'}), "
                  f"{len(got)} props with finals, {len(voids)} did not play"
                  + (f"; not final yet: {', '.join(box['unfinished'])}" if box.get("unfinished") else "")
                  + (f"; no box-score match: {', '.join(unmatched[:8])}" if unmatched else ""))
        except Exception as e:
            print(f"Box scores {d} unavailable ({e}); BettingPros finals only.")
        if bp_learn.events_without_finals(d, finals):   # a game no box score covers: BettingPros slate pull
            print(f"Grading {d}: BettingPros fallback for games without box scores.")
            try:
                finals.update(bp_learn.finals_from(pull_props(key, d, fd, market_ids, args.page_limit), labels))
            except RunCapReached as e:
                print(f"Grading {d}: {e}; grading what is known.")
                capped = True
        # FanDuel can pull a line before the game ends, which drops it from the FanDuel-filtered pull:
        # look up each still-missing pick's game and market with no book filter (1 request each).
        scored = set()
        for ev, mk in ([] if capped else bp_learn.missing_pick_markets(d, finals)[:MAX_PICK_LOOKUPS]):
            if mk not in tag_ids or not ev or any(k.startswith(f"{ev}|{mk}|") for k in voids):
                continue
            try:
                got = bp_learn.finals_from(pull_props(key, d, None, str(tag_ids[mk]), args.page_limit, event_id=ev), labels)
            except RunCapReached as e:
                print(f"Grading {d}: {e}; remaining lookups left for the next run.")
                break
            if got:
                scored.add((ev, mk))
            finals.update(got)
        n_props, n_picks, pend = bp_learn.grade_date(d, finals, scored, voids)
        bp_learn.grade_placed(d, box)
        try:
            from nbaprops.rules import tipoff as bp_tipoff
            bp_tipoff.save_actual_starts(d)                # first-play times: the grading cutoff (free, ESPN)
        except Exception as e:
            print(f"Actual starts {d}: not saved ({e}); cutoff falls back to tip + 10 min.")
        try:
            from nbaprops.rules import halftime as bp_halftime
            bp_halftime.grade_flags(d, box)
            from nbaprops.rules import halftime_backfill as bp_halftime_backfill  # games with no live halftime snapshot: base rate only
            bp_halftime_backfill.backfill(d)
        except Exception as e:
            print(f"Halftime flags {d}: not graded ({e})")
        print(f"Graded {d}: {n_props} props, {n_picks} picks" + (f", {pend} still pending" if pend else ""))
    if to_grade or not os.path.exists(bp_learn.MODEL):
        m = bp_learn.fit_model()
        print(f"Model: {m['n']} graded props" + ("" if m.get("weights") else f" (learning starts at {bp_learn.MIN_TRAIN})"))
    if args.grade:
        print_record()
        return

    # 2 PM full pull: /events, every FanDuel main line from /offers, then BettingPros projections only for
    # the players FanDuel prices (/props filtered by event_id + market_id + player_id). Budget math before
    # the /props pages: if pull + 6:30 re-price would pass the daily limit, the re-price is skipped instead.
    pulled_at = bp_learn.utc_now()
    used_before = used_today() - BP.REQUESTS_MADE           # other runs today (UTC day)
    grading = BP.REQUESTS_MADE
    events = get("/events", {"sport": "NBA", "date": args.date}, key).get("events", [])
    tips = {e.get("id"): bp_learn.tip_iso(e.get("scheduled")) for e in events}
    upcoming = warn_started(tips, pulled_at)
    only = {int(x) for x in str(args.only_events).replace(",", ":").split(":") if x.strip()} if args.only_events else None
    if only:
        upcoming &= only
        print(f"Only events {', '.join(str(e) for e in sorted(upcoming)) or '(none still to tip)'}; other games keep their last pull.")
    if not upcoming:
        sys.exit("No games left to tip on this slate.")
    off_params = offers_params(upcoming, market_ids, fd, args.page_limit)
    first_off = get("/offers", dict(off_params, page=1), key)
    reprice_est = 1 + pages_left(first_off)
    est_props = math.ceil(1.4 * ((first_off.get("_pagination") or {}).get("total_items") or 0) / args.page_limit) or 1
    budget_math(used_before, grading, BP.REQUESTS_MADE - grading + pages_left(first_off) + est_props, reprice_est,
                args.max_requests, "estimate before /props (projections ~1.4 rows per FanDuel line)", enforce=False)
    offers = parse_fd_offers(fetch_pages("/offers", off_params, key, "offers", first_off), fd)
    no_lines = sorted(set(upcoming) - {int(k[0]) for k in offers})
    if no_lines:
        print(f"Warning: no FanDuel lines in /offers yet for event(s) {', '.join(map(str, no_lines))} "
              f"(not tipped; FanDuel hasn't posted or BettingPros hasn't picked them up). Re-pull those later with --only-events.")
    if not offers:
        sys.exit("No FanDuel lines for these games yet; stopping before /props.")
    if args.unfiltered_props:
        prop_queries = [props_params(args.date, None, market_ids, args.page_limit)]
    else:
        ids = sorted({o["player_id"] for o in offers.values() if o["player_id"]})
        evs = ":".join(str(e) for e in sorted(upcoming))
        prop_queries = [dict(props_params(None, None, market_ids, args.page_limit, event_id=evs), player_id=":".join(ids[i:i + 150]))
                        for i in range(0, len(ids), 150)] or []
    firsts = [get("/props", dict(prop_queries[0], page=1), key)]
    if not args.unfiltered_props:
        check_projection_match(firsts[0], offers, labels)
    firsts += [get("/props", dict(q, page=1), key) for q in prop_queries[1:]]
    mode = budget_math(used_before, grading, BP.REQUESTS_MADE - grading + sum(pages_left(f) for f in firsts), reprice_est,
                       args.max_requests, "exact")
    after = DAILY_LIMIT - used_before - (BP.REQUESTS_MADE + sum(pages_left(f) for f in firsts))
    props = [p for q, f in zip(prop_queries, firsts) for p in fetch_pages("/props", q, key, "props", f)]
    bases = {}
    for p in props:
        b = base_from_prop(p, labels)
        bases[(str(b["event_id"]), b["market"], b["player_id"])] = b
    rows, missing = build_rows(bases, offers, labels, tips, pulled_at, fd)
    matched = sum(1 for r in rows if r["price_is_fanduel"] and not r["noproj"])
    if offers and matched < 0.5 * len(offers) and not args.unfiltered_props:
        print(f"Warning: projections matched only {matched} of {len(offers)} FanDuel lines. If /offers player ids don't "
              f"match /props ids, re-run with --unfiltered-props (about 5-6 /props pages per game).")
    print(f"Projections matched {matched} of {len(offers)} FanDuel lines ({round(100 * matched / max(len(offers), 1))}%).")
    save_pull(args.date, pulled_at, fd, market_ids, events, tips, props, offers, rows,
              extra={"reprice_mode": mode, "reprice_estimate": reprice_est}, only=upcoming if only else None)
    if only:                                          # keep the other games' rows from their last pull
        try:
            with open(f"bp_props_{args.date}.csv") as f:
                rows += typed_csv_rows([r for r in csv.DictReader(f)
                                        if r.get("event_id") and int(float(r["event_id"])) not in upcoming])
        except OSError:
            pass
    rw_players = bp_rotowire.fetch()                  # public page, not an API request
    print(f"Rotowire lineups: {len(rw_players)} players" if rw_players else "Rotowire lineups: unavailable this run")
    bp_learn.save_rotowire(rw_players, args.date, pulled_at)
    n_fd = sum(1 for r in rows if r["price_is_fanduel"])
    print(f"FanDuel lines: {n_fd} ({sum(1 for r in rows if r['price_is_fanduel'] and r['noproj'])} without a BettingPros projection); "
          f"BettingPros projections: {len(bases)}")
    finish(rows, args.date, snapshot=True, rw_players=rw_players)
    by_game = {}
    for r in rows:
        if r["price_is_fanduel"]:
            by_game[str(r["event_id"])] = by_game.get(str(r["event_id"]), 0) + 1
    mode, games, picks_cost = picks_reprice_decision(args.date, pulled_at, mode, after, reprice_est, by_game, args.page_limit)
    bp_learn.update_pull_log(args.date, pulled_at, {"reprice_mode": mode, "reprice_games": games,
                                                    "reprice_picks_estimate": picks_cost, "fanduel_lines_by_game": by_game})


def typed_csv_rows(rows):
    """CSV rows back to the types a live pull produces."""
    for r in rows:
        r["price_is_fanduel"] = r.get("price_is_fanduel") == "True"
        r["noproj"] = r.get("noproj") == "True"
        for k in ("line", "bp_proj", "diff", "pct_gap", "fd_odds_rec", "dvp_softness", "bp_line"):
            r[k] = _num(r.get(k))
    return rows


def check_projection_match(first_page, offers, labels):
    """Item-3 guard on the player_id filter, after the first /props page: stop and ask before any fallback."""
    page = first_page.get("props", [])
    # The filter returns every market for each FanDuel player, so test the player ids, not the markets.
    ids = {o["player_id"] for o in offers.values()}
    who = {_name(o.get("player")) for o in offers.values()}
    hit = 0
    for p in page:
        b = base_from_prop(p, labels)
        hit += b["player_id"] in ids or _name(b["player"]) in who
    total = (first_page.get("_pagination") or {}).get("total_items") or 0
    share_page = hit / len(page) if page else 0.0
    cover = min(total / len(offers), 1.0) if offers else 1.0
    print(f"Player-id filter check (first /props page): {hit} of {len(page)} rows are FanDuel's players "
          f"({round(100 * share_page)}%); {total} projections for {len(offers)} FanDuel lines (up to {round(100 * cover)}% matched).")
    if offers and (share_page < 0.5 or cover < 0.5):
        sys.exit(f"Stopped after the first /props page: under 50% of FanDuel lines can match a projection, so the "
                 f"player_id filter may not line up with /offers. Ask before re-running with --unfiltered-props "
                 f"(about 5-6 /props pages per game). {BP.REQUESTS_MADE} requests used this run.")


def reprice(key, args, fd, market_ids, labels):
    """6:30 PM re-price: FanDuel's current lines from /offers only, reusing the last full pull's projections.
    Picks logged by this run are the ones that count for games that haven't tipped."""
    path = f"bp_props_{args.date}.csv"
    try:
        with open(path, newline="") as f:
            old = list(csv.DictReader(f))
    except OSError:
        sys.exit(f"{path} not found: run the full pull first.")
    if not old or "bp_line" not in old[0]:
        sys.exit(f"{path} is from the old pull format; run the full pull first.")
    tips = {}
    for r in old:
        if r.get("event_id") and r.get("tip"):
            tips[int(float(r["event_id"]))] = r["tip"]
    log = bp_learn.last_pull_log(args.date)
    mode = (log or {}).get("reprice_mode") or ("skip" if (log or {}).get("reprice_skipped") else "full")
    if args.force_reprice:
        mode = "full"
    if mode == "skip":
        sys.exit("The 2 PM pull skipped tonight's re-price to stay under the daily limit; the 2 PM picks count. "
                 "Use --force-reprice to run it anyway.")
    for e in (log or {}).get("events", []):
        if e.get("id") and e.get("tip"):
            tips[e["id"]] = e["tip"]
    pulled_at = bp_learn.utc_now()
    upcoming = warn_started(tips, pulled_at)
    if not upcoming:
        sys.exit("Every game has tipped; nothing to re-price.")
    cover = set(upcoming)
    if mode == "picks":
        cover &= {int(g) for g in log.get("reprice_games") or []}
        if not cover:
            sys.exit("Picks-only re-price: none of the games with 2 PM picks are still to tip.")
    used_before = used_today() - BP.REQUESTS_MADE
    by_game = (log or {}).get("fanduel_lines_by_game") or {}
    est = lambda games: max(1, math.ceil(sum(by_game.get(str(g), 0) for g in games) / args.page_limit))
    print(f"Budget (re-price): {used_before} used earlier today; full re-price ~{est(upcoming)} vs. picks-only "
          f"~{est(cover)}; running {'picks-only (' + str(len(cover)) + ' of ' + str(len(upcoming)) + ' games)' if mode == 'picks' else 'full'}; "
          f"{DAILY_LIMIT - used_before} left")
    off_params = offers_params(cover, market_ids, fd, args.page_limit)
    first_off = get("/offers", dict(off_params, page=1), key)
    more = pages_left(first_off)
    print(f"Budget (re-price, exact): {used_before} + this run {1 + more} = {used_before + 1 + more} of {DAILY_LIMIT}")
    if used_before + 1 + more > DAILY_LIMIT or (args.max_requests is not None and 1 + more > args.max_requests):
        sys.exit(f"Stopped: the re-price needs {1 + more} requests; {DAILY_LIMIT - used_before} left today"
                 + (f", --max-requests {args.max_requests}" if args.max_requests is not None else "") + ". 1 used.")
    offers = parse_fd_offers(fetch_pages("/offers", off_params, key, "offers", first_off), fd)
    bases = {}
    for r in old:
        ev = int(float(r["event_id"])) if r.get("event_id") else None
        if ev not in upcoming:
            continue
        b = {k: r.get(k) for k in BASE_FIELDS}
        b["line_seen"] = r.get("line") if r.get("price_is_fanduel") == "True" else None
        k = (str(ev), r["market"], r.get("player_id") or "")
        bases.setdefault(k, b)
        if ev not in cover and r.get("price_is_fanduel") == "True":     # not re-priced: keep the 2 PM FanDuel price
            offers[(ev, r["market"], r.get("player_id") or "")] = stale_offer(r)
    rows, _ = build_rows(bases, offers, labels, tips, pulled_at, fd)
    for r in rows:
        r["stale_price"] = r["price_is_fanduel"] and int(float(r["event_id"])) not in cover
    before = {(r["event_id"], r["market"], r["player"]) for r in old if r.get("price_is_fanduel") == "True"
              and r.get("event_id") and int(float(r["event_id"])) in cover}
    now = {(str(r["event_id"]), r["market"], r["player"]) for r in rows if r["price_is_fanduel"] and not r["stale_price"]}
    gone = sorted(before - now)
    moved = sum(1 for r in rows if r.get("line_moved"))
    # events = the games this run re-priced: its picks count only for these (2 PM picks keep counting for the rest)
    save_pull(args.date, pulled_at, fd, market_ids, [{"id": e} for e in sorted(cover)], tips, [], offers, rows, kind="reprice",
              extra={"mode": mode})
    rw_players = bp_rotowire.fetch()
    print(f"Rotowire lineups: {len(rw_players)} players" if rw_players else "Rotowire lineups: unavailable this run")
    bp_learn.save_rotowire(rw_players, args.date, pulled_at)
    print(f"Re-priced {len(now)} FanDuel lines; {moved} moved since the full pull; "
          f"{len(gone)} pulled since then (late scratches or lines taken down)" +
          (": " + ", ".join(f"{p} {m}" for _, m, p in gone[:12]) + (" ..." if len(gone) > 12 else "") if gone else ""))
    finish(rows, args.date, snapshot=True, rw_players=rw_players)

def measure(key, date, fd, market_ids):
    """One-off: total_items for the slate three ways (limit=1, so 1 request each). Saved to history/."""
    out = {"date": date, "at": bp_learn.utc_now(), "markets": market_ids}
    for name, book, evt in (("fanduel", fd, False), ("all_books", None, False), ("fanduel_default_ev_threshold", fd, True)):
        pag = get("/props", props_params(date, book, market_ids, 1, ev_threshold=evt), key).get("_pagination", {})
        out[name] = pag.get("total_items")
        print(f"{name:30} total_items {pag.get('total_items')}")
    out["requests"] = BP.REQUESTS_MADE
    bp_learn.save_pull_log(date, out["at"], out, prefix="measure_")
    print(f"Saved to history/{date}/. Requests used: {BP.REQUESTS_MADE}")




def mark_model_picks(rows, date):
    """model_pick = the strategies that picked this row in this run (research, not a bet)."""
    pulled = rows[0].get("pulled_at") if rows else None
    picks = {}
    model = {k for k, _ in bp_learn.STRATEGIES}
    for r in bp_learn._read(bp_learn.LEDGER):
        if r["date"] == date and r.get("pulled_at") == pulled and r["strategy"] in model:
            picks.setdefault((r["player"], r["market"], r["side"]), []).append(r["strategy"])
    for r in rows:
        r["model_pick"] = ",".join(picks.get((r.get("player"), r.get("market"), r.get("rec_side")), [])) or None






def attach_value(rows, date):
    """The day's market check (bp_value.py) on each row: oa_fair, oa_ev, fair_src, oa_books for the row's side,
    and the BET / watch decision for the line (a BET can be on the other side of the row)."""
    val = load_value(date)
    for r in rows:
        line = _num(r.get("line"))
        e = val.get(f"{r.get('player')}|{r.get('market')}|{line:g}") if line is not None else None
        if e and "sides" in e:
            v = e["sides"].get(r.get("rec_side")) or {}
            r.update(oa_fair=v.get("fair_mult"), oa_ev=v.get("ev_mult"), oa_books=v.get("books"),
                     fair_src=v.get("fair_src"), decision=e.get("decision"), bet_kelly=e.get("bet_kelly"),
                     bet_side=e.get("bet_side"), bet_tag=e.get("tag"), bet_ev=e.get("bet_ev"),
                     close_mkt_ev=(e.get("close") or {}).get("mkt_ev"), close_clv=(e.get("close") or {}).get("clv_ev"),
                     bet_cap_note=e.get("cap_note") or None)
            continue
        v = val.get(f"{r.get('player')}|{r.get('market')}|{r.get('rec_side')}|{line:g}") if line is not None else None
        r["oa_fair"] = v.get("fair_mult") if v else None
        r["oa_ev"] = v.get("ev_mult") if v else None
        r["oa_books"] = v.get("books") if v else None
        r["fair_src"] = v.get("fair_src") if v else None
        r["decision"] = v.get("decision") if v else None          # BET / watch (2 PM shortlist value check)
        r["bet_kelly"] = v.get("bet_kelly") if v else None        # quarter-Kelly on the market's fair probability






def fmt(v):
    """Print floats without trailing .0 (2.0 -> 2) so the tables stay tidy."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def print_record():
    """Record v2 (current rules), then the v1 archive."""
    print(f"Record v2 (rules from {bp_learn.V2_START}):")
    _print_record("v2")
    print("v1 archive (before the v2 fresh start; kept, not counted in v2):")
    _print_record("v1")


def _print_record(version):
    rec = bp_learn.record(version)
    if rec["dropped_after_tip"]:
        print(f"Not counted: {rec['dropped_after_tip']} picks logged after their game started.")
    lines = rec["strategies"] + [rec["combined"]]
    if not any(x["w"] + x["l"] + x["p"] for x in lines):
        print("Record: no graded picks yet.")
        return
    print(f"Record ({rec['slates']} graded slates, pre-tip picks only):")
    heads = [("rule", "Your rule: CNO +EV + BP agrees"), ("ua", "Unabated +EV & BP agrees"), ("bet", "BETs: Unabated market +EV"), ("cno", "CrazyNinja +EV"), ("screener", "CNO screener"), ("alt", "Alt ladder"), ("half", "Halftime live (experimental)"),
             ("model", "BettingPros Projection Model (research)"), ("sim", "Sims (research, separate)")]
    for g, head in heads:
        rs = [x for x in lines if x.get("group") == g]
        if not rs:
            continue
        print(f" {head}:")
        for x in rs:
            hit = f"{x['hit']}%" if x["hit"] is not None else "-"
            br = f"Brier {x['brier']} vs mkt {x['brier_mkt']}" if x["brier"] is not None else ""
            unit = "% EV" if x.get("clv_unit") else " pts"
            clv = f"CLV {x['clv']:+}{unit}" if x["clv"] is not None else ""
            k = f"¼K {x['kelly_units']:+.2f}u" if g in ("bet", "cno", "screener", "half", "alt", "rule", "ua") else ""
            print(f"  {x['label']:40} {x['w']}-{x['l']}-{x['p']}  hit {hit:>6}  flat {x['units']:+.2f}u  {k}  {br}  {clv}".rstrip())


def finish(rows, date, snapshot=False, rw_players=None):
    """Score, sort, and write the CSV, the top-10 note, and the terminal summary."""
    if rw_players is None and rows:                     # re-score: use the run's saved Rotowire snapshot
        rw_players = bp_learn.load_rotowire(date, rows[0].get("pulled_at"))
    bp_learn.add_teammate_context(rows, date, rw_players)
    global KELLY_SCALE
    calibrated, kelly_note = calibration_status()
    KELLY_SCALE = 1.0 if calibrated else 0.5
    model = bp_learn.load_model()
    for r in rows:
        r["noproj"] = bp_learn._truthy(r.get("noproj"))
        if r["noproj"]:
            noproj_row(r)
            continue
        if r.get("market") not in REAL_PROJ_MARKETS and _num(r.get("bp_proj")) is not None:
            lean_row(r)
            continue
        r["lean_only"] = False
        score_row(r)
        balance_row(r)
        bp_learn.learned_row(r, model)
    attach_value(rows, date)
    n_logged = bp_learn.log_picks(rows, date)
    mark_model_picks(rows, date)
    rows[:] = ranked(rows, lambda r: abs(r["pct_gap"] or 0))
    out = f"bp_props_{date}.csv"
    with open(out, "w", newline="") as f:
        fields = list(dict.fromkeys(k for r in rows for k in r)) or ["player"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if snapshot and rows:
        bp_learn.save_snapshot(rows, date, fields)

    note = write_top_by_market(rows, date, kelly_note=kelly_note)
    dash = write_dashboard(rows, date, kelly_note=kelly_note)

    print(f"{len(rows)} props -> {out}   (requests used this run: {BP.REQUESTS_MADE})")
    print(f"Top 10 lists (overall + gap and score per category) -> {note}")
    if dash:
        print(f"Dashboard -> {dash}")
    print(f"Logged {n_logged} picks for {date} (top {bp_learn.PICKS_PER_STRATEGY} per strategy); graded next run.")
    print_record()
    print("Top 15 by gap:")
    print(f"{'player':22} {'market':8} {'side':5} {'line':>5} {'proj':>6} {'gap%':>6} {'FD':>6} {'DvP':>4} {'score':>5} {'bal':>4}")
    for r in [r for r in rows if r["price_is_fanduel"]][:15]:
        print(f"{(r['player'] or '')[:22]:22} {str(r['market'])[:8]:8} {str(r['rec_side'])[:5]:5} "
              f"{fmt(r['line']):>5} {fmt(r['bp_proj']):>6} {fmt(r['pct_gap']):>6} {fmt(r['fd_odds_rec']):>6} "
              f"{fmt(r['dvp_softness']):>4} {fmt(r['score']):>5} {fmt(r['balanced']):>4}")


if __name__ == "__main__":
    main()
