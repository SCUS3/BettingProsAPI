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

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp_learn  # noqa: E402  (pick tracking, grading, self-learning)
import bp_rotowire  # noqa: E402  (projected lineups and injuries)


def eastern_today():
    """Today's date in Eastern time; falls back to the computer's own date (Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()

BASE = "https://api.bettingpros.com/v3"
REQUESTS_MADE = 0
PULL_LOG = []         # one entry per /props page this run: rows returned vs. _pagination totals (saved to history/)
MAX_PICK_LOOKUPS = 8   # per slate per run: single game+market lookups for picks the FanDuel pull missed


def get(path, params, key, retries=3):
    """GET a v3 endpoint, honouring 429/503 back-off."""
    global REQUESTS_MADE
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"x-api-key": key, "accept": "application/json"})
        try:
            REQUESTS_MADE += 1
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and "Limit Exceeded" in body:
                sys.exit("Daily request allowance used up (resets midnight UTC).")
            if e.code in (429, 503) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After", 3))
                time.sleep(wait)
                continue
            if e.code == 403:
                sys.exit("403 Forbidden: check BP_API_KEY (missing, wrong, or deactivated).")
            sys.exit(f"HTTP {e.code} on {path}: {body[:300]}")
    sys.exit(f"Gave up on {path} after {retries} tries.")


def load_dotenv():
    """Read KEY=value lines from .env next to the script (without overriding real env vars)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except OSError:
        pass


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_cache.json")

MKT_CACHE_KEY = "nba_markets_v2"  # bump when classify() changes

# The eight prop types to keep, described by their stat components.
WANTED = {
    frozenset({"pts"}): "PTS",
    frozenset({"reb"}): "REB",
    frozenset({"ast"}): "AST",
    frozenset({"3pm"}): "3PM",
    frozenset({"pts", "ast"}): "P+A",
    frozenset({"pts", "reb"}): "P+R",
    frozenset({"pts", "reb", "ast"}): "PRA",
    frozenset({"reb", "ast"}): "R+A",
}
TOKEN_MAP = {
    "points": "pts", "pts": "pts",
    "rebounds": "reb", "rebound": "reb", "reb": "reb", "rebs": "reb",
    "assists": "ast", "assist": "ast", "ast": "ast", "asts": "ast",
    "threes": "3pm", "three": "3pm", "3pt": "3pm", "3pts": "3pm", "3-pt": "3pm", "3pm": "3pm",
    "3-pointers": "3pm", "pointers": "3pm", "3s": "3pm",
}
# Any of these in a market slug/label means it is not one of the eight.
BLOCK = {"steals", "steal", "blocks", "block", "turnovers", "turnover", "double", "triple", "first",
         "attempted", "attempts", "attempt", "fantasy", "field", "free", "minutes", "quarter", "half",
         "alt", "alternate", "milestone", "race", "most", "stocks",
         "1q", "2q", "3q", "4q", "1h", "2h", "q1", "q2", "q3", "q4"}


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(c):
    try:
        with open(CACHE, "w") as f:
            json.dump(c, f, indent=1)
    except OSError:
        pass


def find_fanduel(key, location, cache):
    ck = f"fanduel_{location}"
    if ck in cache:
        return cache[ck]
    books = get("/books", {"location": location}, key)["books"]
    for b in books:
        if "fanduel" in (b.get("slug") or "").lower() or b.get("name", "").lower() == "fanduel":
            cache[ck] = b["id"]
            return b["id"]
    sys.exit("FanDuel not found in /books for that location. Try --location NJ or NY.")


def classify(m):
    """Return the short tag (PTS, PRA, ...) if a market is one of the eight, else None."""
    if m.get("period") not in (None, "", "game"):
        return None
    meta = m.get("meta") or {}
    text = " ".join([m.get("slug") or "", m.get("stat") or "", meta.get("short_label") or ""]).lower()
    text = text.replace("+", " ").replace("&", " ").replace("_", " ").replace("/", " ")
    toks = set(text.replace("-", " ").split()) | set(text.split())
    # Threes are often named "3-point field goals made"; let "field"/"points" through for them.
    if any(TOKEN_MAP.get(t) == "3pm" for t in toks):
        return None if toks & (BLOCK - {"field"}) else "3PM"
    if toks & BLOCK:
        return None
    comps = frozenset(TOKEN_MAP[t] for t in toks if t in TOKEN_MAP)
    return WANTED.get(comps)


def nba_markets(key, cache, refresh=False):
    """{market_id: (tag or None, label)} for every NBA player-prop market. Cached."""
    if MKT_CACHE_KEY in cache and not refresh:
        out = {int(k): tuple(v) for k, v in cache[MKT_CACHE_KEY].items()}
        # A category added to WANTED after caching (e.g. R+A): tag it from its cached label, no request.
        # Only categories no cached market has yet, so period markets the full classify() rejected stay out.
        have = {t for t, _ in out.values() if t}
        for mid, (tag, lbl) in out.items():
            new = None if tag else classify({"slug": lbl})
            if new and new not in have:
                out[mid] = (new, lbl)
        return out
    out, page = {}, 1
    while True:
        data = get("/markets", {"sport": "NBA", "limit": 50, "page": page}, key)
        for m in data.get("markets", []):
            if m.get("category") not in ("player-props", "props"):
                continue
            label = (m.get("meta") or {}).get("short_label") or m.get("slug")
            out[m["id"]] = (classify(m), label)
        if page >= (data.get("_pagination") or {}).get("total_pages", 1):
            break
        page += 1
    cache[MKT_CACHE_KEY] = {str(k): list(v) for k, v in out.items()}
    return out


def first(*vals):
    """First value that isn't None."""
    return next((v for v in vals if v is not None), None)


def implied(american):
    if american is None:
        return None
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def props_params(date, book_id, market_ids, limit, page=1, event_id=None, ev_threshold=False):
    params = {
        "sport": "NBA",
        "limit": limit,
        "page": page,
        "include_injured": "true",
        "sort": "diff",
    }
    if not ev_threshold:
        params["ev_threshold"] = "false"   # return everything, not just the default EV band
    if event_id:
        params["event_id"] = event_id
    else:
        params["date"] = date
    if book_id is not None:
        params["book_id"] = book_id
    if market_ids:
        params["market_id"] = market_ids
    return params


def pull_props(key, date, book_id, market_ids, page_limit, event_id=None):
    """Props for a date (or one game with event_id; the API rejects both). book_id=None = any book."""
    props, page = [], 1
    while True:
        params = props_params(date, book_id, market_ids, page_limit, page, event_id)
        data = get("/props", params, key)
        props.extend(data.get("props", []))
        pag = data.get("_pagination", {})
        PULL_LOG.append({"date": None if event_id else date, "event_id": event_id, "book_id": book_id, "page": page,
                         "rows": len(data.get("props", [])), "total_items": pag.get("total_items"),
                         "total_pages": pag.get("total_pages")})
        if page >= pag.get("total_pages", 1):
            break
        page += 1
        time.sleep(0.4)
    return props


def pages_left(first):
    return max(((first or {}).get("_pagination") or {}).get("total_pages", 1) - 1, 0)


def check_budget(more, budget, what):
    """Stop before going over --max-requests (page 1s already spent tell us the rest)."""
    total = REQUESTS_MADE + more
    print(f"Request estimate for this {what}: {total} ({REQUESTS_MADE} so far + {more} more pages)")
    if budget is not None and total > budget:
        sys.exit(f"Stopped: the {what} needs about {total} requests, over --max-requests {budget}. "
                 f"{REQUESTS_MADE} used so far this run.")


def fetch_pages(path, params, key, list_key, first):
    """Every page of a paginated endpoint, starting from an already-fetched page 1."""
    items, data, page = [], first, 1
    while True:
        items += data.get(list_key, [])
        pag = data.get("_pagination") or {}
        PULL_LOG.append({"path": path, "page": page, "rows": len(data.get(list_key, [])),
                         "total_items": pag.get("total_items"), "total_pages": pag.get("total_pages")})
        if page >= pag.get("total_pages", 1):
            return items
        page += 1
        time.sleep(0.4)
        data = get(path, dict(params, page=page), key)


def offers_params(event_ids, market_ids, book_id, limit):
    return {"sport": "NBA", "event_id": ":".join(str(e) for e in sorted(event_ids)), "market_id": market_ids,
            "book_id": book_id, "condense": "true", "limit": limit}


def parse_fd_offers(offers, fd):
    """{(event_id, market_id, player_id): offer} with FanDuel's current main line and price per side."""
    out = {}
    for off in offers:
        parts = off.get("participants") or []
        p0 = parts[0] if parts else {}
        rec = {"event_id": off.get("event_id"), "market_id": off.get("market_id"), "player_id": str(off.get("player_id") or ""),
               "player": p0.get("name"), "team": (p0.get("player") or {}).get("team")}
        for sel in off.get("selections") or []:
            side = (sel.get("selection") or sel.get("label") or "").lower()
            if side not in ("over", "under"):
                continue
            for bk in sel.get("books") or []:
                if bk.get("id") != fd:
                    continue
                for ln in bk.get("lines") or []:
                    if ln.get("main") and ln.get("active", True) and not ln.get("is_off") and not ln.get("replaced"):
                        m = ln.get("metrics") or {}
                        rec[side] = {"line": ln.get("line"), "odds": ln.get("cost"),
                                     "fair": m.get("fair_probability"), "mev": m.get("market_ev")}
        if "over" in rec or "under" in rec:
            out[(rec["event_id"], rec["market_id"], rec["player_id"])] = rec
    return out


PERF_WINDOWS = {"form_l5": "last_5", "form_l10": "last_10", "form_l20": "last_20", "form_season": "season", "form_h2h": "h2h"}
# Book-agnostic BettingPros data per prop, kept in the CSV so the 6:30 re-price can reuse it.
BASE_FIELDS = ["player", "player_id", "team", "injury", "market", "event_id", "bp_proj", "bp_rec_side", "bp_line", "bp_book",
               "bp_win_prob", "bp_ev", "bp_rating", "market_prob", "market_ev", "opp_rank_raw", "dvp_softness",
               "consensus_line", "consensus_odds", "perf", "streak", "is_scored", "actual"]


def base_from_prop(p, labels):
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


def warn_started(tips, pulled_at):
    """Games still to tip (only these are priced); warn when some have started."""
    upcoming = {int(e) for e, t in tips.items() if e and (not t or t > pulled_at)}
    started = [t for e, t in tips.items() if t and t <= pulled_at]
    if started:
        first_tip = min(t for t in tips.values() if t)
        print(f"Warning: {len(started)} of {len(tips)} games had already tipped at this pull; they're skipped "
              f"(lines thinned, picks wouldn't count). Pull before the first tip ({first_tip} UTC).")
    return upcoming


def save_pull(date, pulled_at, fd, market_ids, events, tips, props, offers, rows, kind="full"):
    fd_rows = [r for r in rows if r["price_is_fanduel"]]
    per_game = {}
    for r in fd_rows:
        g = per_game.setdefault(str(r["event_id"]), {"fanduel_lines": 0, "with_projection": 0})
        g["fanduel_lines"] += 1
        g["with_projection"] += 0 if r["noproj"] else 1
    bp_learn.save_pull_log(date, pulled_at, {
        "kind": kind, "pulled_at": pulled_at, "requests": REQUESTS_MADE, "pages": PULL_LOG,
        "props_rows": len(props), "fanduel_offers": len(offers), "board_rows": len(fd_rows),
        "fanduel_book_id": fd, "markets": market_ids, "per_game": per_game,
        "events": [{"id": e.get("id"), "tip": tips.get(e.get("id")),
                    "started": bool(tips.get(e.get("id")) and tips[e.get("id")] <= pulled_at)} for e in events]})


CATEGORY_ORDER = [("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists"), ("3PM", "Threes"),
                  ("P+A", "Points + Assists"), ("P+R", "Points + Rebounds"), ("PRA", "Points + Rebounds + Assists"),
                  ("R+A", "Rebounds + Assists")]


def write_top_by_market(rows, date, n=10):
    """Write the Markdown note: three overall top-10 lists, then top 10 by gap and by score in each category."""
    out = f"bp_top10_{date}.md"
    def cell(v):
        return "" if v is None else fmt(v)
    fd = [r for r in rows if r["price_is_fanduel"] and r.get("score") is not None and not r.get("noproj")]
    head = ("| # | Player | Market | Side | Line | Proj | Gap % | FD odds | DvP | Form | Score | Balanced | Signals | Kelly | Injury |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    def table(group, bold):
        # mark the column this list is ranked by in the header (no bold markers anywhere: Docs shows them literally)
        cols = {"gap": "Gap %", "score": "Score", "balanced": "Balanced"}  # ranked column gets a ▼
        t = [head[0].replace(f"| {cols[bold]} |", f"| {cols[bold]} ▼ |"), head[1]]
        for i, r in enumerate(group[:n], 1):
            vals = {"gap": cell(r["pct_gap"]), "score": cell(r["score"]), "balanced": cell(r["balanced"])}
            form = "" if r.get("form_rate") is None else f"{round(r['form_rate'] * 100)}%"
            kelly = "" if r.get("kelly_u") is None else f"{fmt(r['kelly_u'])}u"
            t.append("| " + " | ".join([str(i), cell(r["player"]), cell(r["market"]), cell(r["rec_side"]),
                                        cell(r["line"]), cell(r["bp_proj"]) + ("*" if r.get("placeholder") else ""), vals["gap"], cell(r["fd_odds_rec"]),
                                        cell(r["dvp_softness"]), form, vals["score"], vals["balanced"],
                                        cell(r.get("signals")), kelly, cell(r["injury"])]) + " |")
        return t + [""]

    lines = [f"# NBA props: {date}", "",
             "FanDuel-priced props only. DvP: 1 = softest matchup. Form: recent hit rate of the "
             "recommended side vs. this line. Score: your rules (40% edge, 35% FanDuel EV, 25% matchup). "
             "Balanced: 40% Score + 25% form + 20% market value (no-vig price, line vs. consensus) + "
             "15% BettingPros bet rating. Signals: how many of six factors lean the same way. "
             "Kelly: quarter-Kelly stake, 1u = 1% of bankroll. All scores are cut for injury status.", ""]
    rec = bp_learn.record()
    lines += ["## Record", ""]
    played = [x for x in rec["strategies"] + [rec["combined"]] if x["w"] + x["l"] + x["p"]]
    if played:
        lines += [f"{rec['slates']} graded slates, pre-tip picks only, flat 1u.", "",
                  "| Strategy | W-L-P | Hit | Units | Kelly units | Brier ours / mkt | CLV |", "|---|---|---|---|---|---|---|"]
        for x in played:
            br = f"{x['brier']} / {x['brier_mkt']}" if x["brier"] is not None else ""
            clv = f"{x['clv']:+} pts" if x["clv"] is not None else ""
            lines.append(f"| {x['label']} | {x['w']}-{x['l']}-{x['p']} | {x['hit'] if x['hit'] is not None else ''}% | "
                         f"{x['units']:+.2f} | {x['kelly_units']:+.2f} | {br} | {clv} |")
        lines.append("")
    else:
        lines += ["No graded picks yet.", ""]
    lines += ["Placeholder projections (Proj*) claim no edge, use FanDuel's no-vig price for EV, and rank below every "
              "real projection unless the price alone is +EV.", ""]
    lines += ["## 1. Top 10 by projection gap", "", "Biggest |projection - line| as a % of the line, nothing else.", ""]
    lines += table(ranked(fd, lambda r: abs(r["pct_gap"] or 0)), "gap")
    lines += ["## 2. Top 10 by your score", "", "Edge, FanDuel EV and matchup.", ""]
    lines += table(ranked(fd, lambda r: r["score"]), "score")
    lines += ["## 3. Top 10 by balanced score", "", "Your score checked against recent form, market value and BettingPros' rating.", ""]
    lines += table(ranked(fd, lambda r: r["balanced"]), "balanced")
    np_rows = [r for r in rows if r["price_is_fanduel"] and r.get("noproj") and r.get("balanced") is not None]
    lines += ["## 4. FanDuel lines without a BettingPros projection", "",
              f"{len(np_rows)} lines. Ranked on form, matchup and market value only: no edge, EV or Kelly, never a logged pick.", ""]
    if np_rows:
        lines += ["| # | Player | Market | Side | Line | FD odds | DvP | Form | Market | Ranking | Injury |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(ranked(np_rows, lambda r: r["balanced"])[:n], 1):
            form = "" if r.get("form_rate") is None else f"{round(r['form_rate'] * 100)}%"
            lines.append("| " + " | ".join([str(i), cell(r["player"]), cell(r["market"]), cell(r["rec_side"]), cell(r["line"]),
                                            cell(r["fd_odds_rec"]), cell(r["dvp_softness"]), form, cell(r.get("s_market")),
                                            cell(r["balanced"]), cell(r["injury"])]) + " |")
        lines.append("")
    diffs = [r for r in fd if r.get("line_note")]
    if diffs:
        lines += ["## FanDuel line differs from BettingPros' line", "",
                  "Gap, side, EV and Kelly use FanDuel's line; BettingPros' win chance and rating are dropped for these.", ""]
        lines += [f"- {r['player']} {r['market']} {r['rec_side']}: {r['line_note']}" for r in ranked(diffs, lambda r: r["balanced"])[:15]]
        lines.append("")
    lines += ["---", "", "# Each category: top 10 by projection gap and top 10 by your score", ""]
    for tag, name in CATEGORY_ORDER:
        group = [r for r in fd if r["market"] == tag and r["pct_gap"] is not None]
        lines += [f"## {name} ({tag})", ""]
        if not group:
            lines += ["_No FanDuel-priced props found._", ""]
            continue
        lines += [f"### {tag}: by projection gap", ""]
        lines += table(ranked(group, lambda r: abs(r["pct_gap"])), "gap")
        lines += [f"### {tag}: by your score", ""]
        lines += table(ranked(group, lambda r: r["score"]), "score")
    with open(out, "w") as f:
        f.write("\n".join(lines))
    return out



# ---------------------------------------------------------------- scoring
# Each prop gets a 0-100 score built from three parts (weights below), then an
# injury multiplier. The parts are scaled so threes, rebounds and points compete
# fairly: the gap is measured in standard deviations, not % of the line.
W_EDGE, W_EV, W_DVP = 0.40, 0.35, 0.25
INJURY_MULT = {"": 1.0, "probable": 1.0, "prob": 1.0, "dtd": 0.85, "questionable": 0.85, "ques": 0.85, "gtd": 0.85,
               "doubtful": 0.5, "doubt": 0.5, "out": 0.0, "ofs": 0.0}


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


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
    """Sort descending by key: real projections, then demoted placeholders, then lines with no projection."""
    def tier(r):
        if bp_learn._truthy(r.get("noproj")):
            return 2
        return 1 if bp_learn._truthy(r.get("ph_demoted")) else 0
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


def kelly_units(r):
    """Quarter-Kelly stake in units (1u = 1% of bankroll), capped at 2.5u.
    The model's win chance is blended 50/50 with FanDuel's no-vig price so stakes stay realistic."""
    p, odds = _num(r.get("model_prob")), _num(r.get("fd_odds_rec"))
    if p is None or odds is None:
        return None
    fair = no_vig_prob(r)
    if fair is not None:
        p = (p + fair) / 2
    b = odds / 100 if odds > 0 else 100 / -odds
    f = (p * (b + 1) - 1) / b
    return round(min(max(f / 4 * 100, 0.0), 2.5), 1)


W_NP_FORM, W_NP_DVP, W_NP_MKT = 0.25, 0.25, 0.20   # no projection: form, matchup and market value only (renormalized)


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
    ap.add_argument("--max-requests", type=int, default=None,
                    help="stop (after the page-1 requests that measure the run) if this run would use more than this")
    ap.add_argument("--no-grade", action="store_true", help="skip grading finished slates this run")
    ap.add_argument("--grade", metavar="DATE", help="only grade the saved slate for DATE, re-learn, and exit")
    ap.add_argument("--measure", action="store_true",
                    help="3 requests: slate prop counts (total_items) with the FanDuel filter, without it, and with the default ev_threshold")
    ap.add_argument("--from-csv", action="store_true",
                    help="re-score an existing bp_props_<date>.csv and rewrite the note (no API requests)")
    args = ap.parse_args()

    if args.from_csv:
        path = f"bp_props_{args.date}.csv"
        try:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            sys.exit(f"{path} not found. Run without --from-csv first.")
        for r in rows:
            r["price_is_fanduel"] = r.get("price_is_fanduel") == "True"
            r["noproj"] = r.get("noproj") == "True"
            for k in ("line", "bp_proj", "diff", "pct_gap", "fd_odds_rec", "dvp_softness", "bp_line"):
                r[k] = _num(r.get(k))
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
        picked = {mid: tag for mid, (tag, _) in markets.items() if tag}
        missing = sorted(set(WANTED.values()) - set(picked.values()))
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
        if bp_learn.events_without_finals(d, finals):   # first grade of the slate (or a game had no finals yet)
            finals.update(bp_learn.finals_from(pull_props(key, d, fd, market_ids, args.page_limit), labels))
        # FanDuel can pull a line before the game ends, which drops it from the FanDuel-filtered pull:
        # look up each still-missing pick's game and market with no book filter (1 request each).
        scored = set()
        for ev, mk in bp_learn.missing_pick_markets(d, finals)[:MAX_PICK_LOOKUPS]:
            if mk not in tag_ids or not ev:
                continue
            got = bp_learn.finals_from(pull_props(key, d, None, str(tag_ids[mk]), args.page_limit, event_id=ev), labels)
            if got:
                scored.add((ev, mk))
            finals.update(got)
        n_props, n_picks, pend = bp_learn.grade_date(d, finals, scored)
        print(f"Graded {d}: {n_props} props, {n_picks} picks" + (f", {pend} still pending" if pend else ""))
    if to_grade or not os.path.exists(bp_learn.MODEL):
        m = bp_learn.fit_model()
        print(f"Model: {m['n']} graded props" + ("" if m.get("weights") else f" (learning starts at {bp_learn.MIN_TRAIN})"))
    if args.grade:
        print_record()
        return

    # 2 PM full pull: /events, then page 1 of /offers (FanDuel) and /props (all books) to learn the page counts,
    # stop if the run would go over --max-requests, else fetch the rest and join on game + player + market.
    pulled_at = bp_learn.utc_now()
    events = get("/events", {"sport": "NBA", "date": args.date}, key).get("events", [])
    tips = {e.get("id"): bp_learn.tip_iso(e.get("scheduled")) for e in events}
    upcoming = warn_started(tips, pulled_at)
    off_params = offers_params(upcoming, market_ids, fd, args.page_limit)
    prop_params = props_params(args.date, None, market_ids, args.page_limit)
    first_off = get("/offers", dict(off_params, page=1), key) if upcoming else {"offers": []}
    first_props = get("/props", dict(prop_params, page=1), key)
    left = pages_left(first_off) + pages_left(first_props)
    check_budget(left, args.max_requests, "full pull")
    offers = parse_fd_offers(fetch_pages("/offers", off_params, key, "offers", first_off), fd) if upcoming else {}
    props = fetch_pages("/props", prop_params, key, "props", first_props)
    bases = {}
    for p in props:
        b = base_from_prop(p, labels)
        bases[(str(b["event_id"]), b["market"], b["player_id"])] = b
    rows, missing = build_rows(bases, offers, labels, tips, pulled_at, fd)
    save_pull(args.date, pulled_at, fd, market_ids, events, tips, props, offers, rows)
    rw_players = bp_rotowire.fetch()                  # public page, not an API request
    print(f"Rotowire lineups: {len(rw_players)} players" if rw_players else "Rotowire lineups: unavailable this run")
    bp_learn.save_rotowire(rw_players, args.date, pulled_at)
    n_fd = sum(1 for r in rows if r["price_is_fanduel"])
    print(f"FanDuel lines: {n_fd} ({sum(1 for r in rows if r['price_is_fanduel'] and r['noproj'])} without a BettingPros projection); "
          f"BettingPros projections: {len(bases)}")
    finish(rows, args.date, snapshot=True, rw_players=rw_players)


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
    for e in (log or {}).get("events", []):
        if e.get("id") and e.get("tip"):
            tips[e["id"]] = e["tip"]
    pulled_at = bp_learn.utc_now()
    upcoming = warn_started(tips, pulled_at)
    if not upcoming:
        sys.exit("Every game has tipped; nothing to re-price.")
    off_params = offers_params(upcoming, market_ids, fd, args.page_limit)
    first_off = get("/offers", dict(off_params, page=1), key)
    check_budget(pages_left(first_off), args.max_requests, "re-price")
    offers = parse_fd_offers(fetch_pages("/offers", off_params, key, "offers", first_off), fd)
    bases = {}
    for r in old:
        if r.get("event_id") and int(float(r["event_id"])) in upcoming:
            b = {k: r.get(k) for k in BASE_FIELDS}
            b["line_seen"] = r.get("line") if r.get("price_is_fanduel") == "True" else None
            bases.setdefault((str(int(float(r["event_id"]))), r["market"], r.get("player_id") or ""), b)
    rows, _ = build_rows(bases, offers, labels, tips, pulled_at, fd)
    before = {(r["event_id"], r["market"], r["player"]) for r in old if r.get("price_is_fanduel") == "True"
              and r.get("event_id") and int(float(r["event_id"])) in upcoming}
    now = {(str(r["event_id"]), r["market"], r["player"]) for r in rows if r["price_is_fanduel"]}
    gone = sorted(before - now)
    moved = sum(1 for r in rows if r.get("line_moved"))
    save_pull(args.date, pulled_at, fd, market_ids, [{"id": e} for e in sorted(upcoming)], tips, [], offers, rows, kind="reprice")
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
    out["requests"] = REQUESTS_MADE
    bp_learn.save_pull_log(date, out["at"], out, prefix="measure_")
    print(f"Saved to history/{date}/. Requests used: {REQUESTS_MADE}")


DASH_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")
DASH_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_dashboard.html")


def write_dashboard(rows, date):
    """Fill dashboard_template.html with tonight's FanDuel-priced props -> bp_dashboard.html."""
    try:
        with open(DASH_TEMPLATE) as f:
            tpl = f.read()
    except OSError:
        return None
    def n(v):
        return _num(v)
    data = []
    for r in rows:
        if not r["price_is_fanduel"] or r.get("balanced") is None:
            continue
        data.append({
            "player": r["player"], "team": r["team"], "inj": r["injury"] or None, "market": r["market"],
            "side": r["rec_side"], "line": n(r["line"]), "proj": n(r["bp_proj"]), "gap": n(r["pct_gap"]),
            "odds": n(r["fd_odds_rec"]), "dvp": n(r["dvp_softness"]), "form": n(r.get("form_rate")),
            "score": r["score"], "bal": r.get("balanced"), "kelly": n(r.get("kelly_u")), "sig": r.get("signals"),
            "s_edge": r.get("s_edge"), "s_ev": r.get("s_ev"), "s_dvp": r.get("s_dvp"), "s_form": r.get("s_form"),
            "s_market": r.get("s_market"), "nv": n(r.get("nv_edge")), "cons": n(r.get("consensus_line")),
            "streak": r.get("streak") or None, "l10": r.get("form_l10") or None, "season": r.get("form_season") or None,
            "learned": r.get("learned"), "tm_out": r.get("tm_out"), "tm_dtd": r.get("tm_dtd"),
            "tm_out_names": r.get("tm_out_names") or None, "ph": bool(r.get("placeholder")), "demoted": bool(r.get("ph_demoted")),
            "starter": r.get("starter") in (True, "True"), "rw": r.get("rw_status") or None,
            "noproj": bool(r.get("noproj")), "bp_line": n(r.get("bp_line")), "note": r.get("line_note") or None,
        })
    d = dt.date.fromisoformat(date)
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
        updated = now.strftime("%b %-d, %-I:%M %p ET")
    except Exception:
        updated = dt.datetime.now().strftime("%b %d, %I:%M %p")
    payload = {"date": date, "date_label": d.strftime("%A, %B %-d"), "updated": updated, "rows": data,
               "record": bp_learn.record()}
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    with open(DASH_OUT, "w") as f:
        f.write(tpl.replace("__BP_DATA__", blob))
    return DASH_OUT


def fmt(v):
    """Print floats without trailing .0 (2.0 -> 2) so the tables stay tidy."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def print_record():
    rec = bp_learn.record()
    if rec["dropped_after_tip"]:
        print(f"Not counted: {rec['dropped_after_tip']} picks logged after their game started.")
    lines = rec["strategies"] + [rec["combined"]]
    if not any(x["w"] + x["l"] + x["p"] for x in lines):
        print("Record: no graded picks yet.")
        return
    print(f"Record ({rec['slates']} graded slates, pre-tip picks only, flat 1u):")
    for x in lines:
        hit = f"{x['hit']}%" if x["hit"] is not None else "-"
        br = f"Brier {x['brier']} vs mkt {x['brier_mkt']}" if x["brier"] is not None else ""
        clv = f"CLV {x['clv']:+} pts" if x["clv"] is not None else ""
        print(f"  {x['label']:28} {x['w']}-{x['l']}-{x['p']}  hit {hit:>6}  {x['units']:+.2f}u  "
              f"Kelly {x['kelly_units']:+.2f}u  {br}  {clv}".rstrip())


def finish(rows, date, snapshot=False, rw_players=None):
    """Score, sort, and write the CSV, the top-10 note, and the terminal summary."""
    if rw_players is None and rows:                     # re-score: use the run's saved Rotowire snapshot
        rw_players = bp_learn.load_rotowire(date, rows[0].get("pulled_at"))
    bp_learn.add_teammate_context(rows, date, rw_players)
    model = bp_learn.load_model()
    for r in rows:
        r["noproj"] = bp_learn._truthy(r.get("noproj"))
        if r["noproj"]:
            noproj_row(r)
            continue
        score_row(r)
        balance_row(r)
        bp_learn.learned_row(r, model)
    n_logged = bp_learn.log_picks(rows, date)
    rows[:] = ranked(rows, lambda r: abs(r["pct_gap"] or 0))
    out = f"bp_props_{date}.csv"
    with open(out, "w", newline="") as f:
        fields = list(dict.fromkeys(k for r in rows for k in r)) or ["player"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if snapshot and rows:
        bp_learn.save_snapshot(rows, date, fields)

    note = write_top_by_market(rows, date)
    dash = write_dashboard(rows, date)

    print(f"{len(rows)} props -> {out}   (requests used this run: {REQUESTS_MADE})")
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
