#!/usr/bin/env python3
"""
Pull tonight's NBA player props from the BettingPros Consumer API, priced at
FanDuel, and write them to a CSV ranked by projection gap.

Usage:
    # one-time setup: put BP_API_KEY=your-key-here in a .env file next to this script
    # (never paste the key into chat; .env is git-ignored)
    python3 bp_props.py                    # today's slate
    python3 bp_props.py --date 2026-11-19  # another date
    python3 bp_props.py --offers           # also pull FanDuel fair odds / market EV per game (more requests)

Uses only the Python standard library. Output: bp_props_<date>.csv plus a
top-15 printout.

Markets pulled by default (full game only): points, rebounds, assists, threes,
pts+ast, pts+reb, pts+reb+ast. Market and FanDuel book IDs are looked up once and
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
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def eastern_today():
    """Today's date in Eastern time; falls back to the computer's own date (Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()

BASE = "https://api.bettingpros.com/v3"
REQUESTS_MADE = 0


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

# The seven prop types to keep, described by their stat components.
WANTED = {
    frozenset({"pts"}): "PTS",
    frozenset({"reb"}): "REB",
    frozenset({"ast"}): "AST",
    frozenset({"3pm"}): "3PM",
    frozenset({"pts", "ast"}): "P+A",
    frozenset({"pts", "reb"}): "P+R",
    frozenset({"pts", "reb", "ast"}): "PRA",
}
TOKEN_MAP = {
    "points": "pts", "pts": "pts",
    "rebounds": "reb", "rebound": "reb", "reb": "reb", "rebs": "reb",
    "assists": "ast", "assist": "ast", "ast": "ast", "asts": "ast",
    "threes": "3pm", "three": "3pm", "3pt": "3pm", "3pts": "3pm", "3-pt": "3pm", "3pm": "3pm",
    "3-pointers": "3pm", "pointers": "3pm", "3s": "3pm",
}
# Any of these in a market slug/label means it is not one of the seven.
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
    """Return the short tag (PTS, PRA, ...) if a market is one of the seven, else None."""
    if m.get("period") not in (None, "", "game"):
        return None
    meta = m.get("meta") or {}
    text = " ".join([m.get("slug") or "", m.get("stat") or "", meta.get("short_label") or ""]).lower()
    text = text.replace("+", " ").replace("&", " ").replace("_", " ").replace("/", " ")
    toks = set(text.replace("-", " ").split()) | set(text.split())
    if toks & BLOCK:
        return None
    comps = frozenset(TOKEN_MAP[t] for t in toks if t in TOKEN_MAP)
    return WANTED.get(comps)


def nba_markets(key, cache, refresh=False):
    """{market_id: (tag or None, label)} for every NBA player-prop market. Cached."""
    if "nba_markets" in cache and not refresh:
        return {int(k): tuple(v) for k, v in cache["nba_markets"].items()}
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
    cache["nba_markets"] = {str(k): list(v) for k, v in out.items()}
    return out


def implied(american):
    if american is None:
        return None
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def pull_props(key, date, book_id, market_ids, page_limit):
    props, page = [], 1
    while True:
        params = {
            "sport": "NBA",
            "date": date,
            "book_id": book_id,
            "limit": page_limit,
            "page": page,
            "include_injured": "true",
            "ev_threshold": "false",   # return everything, not just the default EV band
            "sort": "diff",
        }
        if market_ids:
            params["market_id"] = market_ids
        data = get("/props", params, key)
        props.extend(data.get("props", []))
        pag = data.get("_pagination", {})
        if page >= pag.get("total_pages", 1):
            break
        page += 1
        time.sleep(0.4)
    return props


def pull_offer_metrics(key, event_ids, market_ids, book_id):
    """Map (event, market, player_id, side) -> FanDuel fair_probability / market_ev from /offers."""
    out = {}
    for ev in event_ids:
        page = 1
        while True:
            data = get("/offers", {"sport": "NBA", "event_id": ev, "market_id": market_ids,
                                   "book_id": book_id, "limit": 50, "page": page}, key)
            for off in data.get("offers", []):
                for sel in off.get("selections", []):
                    side = (sel.get("selection") or sel.get("label") or "").lower()
                    for bk in sel.get("books", []):
                        if bk.get("id") != book_id:
                            continue
                        for ln in bk.get("lines", []):
                            if not ln.get("main"):
                                continue
                            m = ln.get("metrics") or {}
                            out[(off.get("event_id"), off.get("market_id"), str(off.get("player_id")), side)] = (
                                m.get("fair_probability"), m.get("market_ev"))
            pag = data.get("_pagination", {})
            if page >= pag.get("total_pages", 1):
                break
            page += 1
            time.sleep(0.4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=eastern_today(),
                    help="slate date (defaults to today in Eastern time, so cloud machines on UTC still get the right night)")
    ap.add_argument("--location", default="NY")
    ap.add_argument("--markets", default="", help="colon-delimited market ids (overrides the seven defaults)")
    ap.add_argument("--list-markets", action="store_true", help="print NBA prop markets and exit")
    ap.add_argument("--refresh", action="store_true", help="re-fetch cached book and market IDs")
    ap.add_argument("--page-limit", type=int, default=20, help="20 for Free/Plus, 50 for Elite")
    ap.add_argument("--offers", action="store_true", help="also pull FanDuel fair odds / market EV")
    args = ap.parse_args()

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

    props = pull_props(key, args.date, fd, market_ids, args.page_limit)

    offer_metrics = {}
    if args.offers and props:
        events = sorted({p["event_id"] for p in props})
        mids = market_ids
        offer_metrics = pull_offer_metrics(key, events, mids, fd)

    rows = []
    for p in props:
        part = p.get("participant", {}) or {}
        pl = part.get("player", {}) or {}
        proj = p.get("projection", {}) or {}
        over, under = p.get("over", {}) or {}, p.get("under", {}) or {}
        rec = proj.get("recommended_side")
        side = over if rec == "over" else under if rec == "under" else {}
        line = side.get("line") if side else over.get("line")
        val, diff = proj.get("value"), proj.get("diff")
        pct = round(100 * diff / line, 1) if (diff is not None and line) else None
        rank = ((p.get("extra") or {}).get("opposition_rank") or {}).get("rank")
        fair, mev = offer_metrics.get((p.get("event_id"), p.get("market_id"), str(part.get("id")), rec or ""), (None, None))
        rows.append({
            "player": part.get("name"),
            "team": pl.get("team"),
            "injury": pl.get("injury_status"),
            "market": labels.get(p.get("market_id"), p.get("market_id")),
            "rec_side": rec,
            "line": line,
            "fd_odds_rec": side.get("odds") if side else None,
            "price_book": side.get("book") if side else None,
            "price_is_fanduel": (side.get("book") == fd) if side else None,
            "bp_proj": val,
            "diff": diff,
            "pct_gap": pct,
            "bp_win_prob": proj.get("probability"),          # Plus/Elite only
            "bp_ev": proj.get("expected_value"),              # Plus/Elite only
            "bp_rating": proj.get("bet_rating"),              # Plus/Elite only
            "market_prob": proj.get("market_probability"),
            "market_ev": proj.get("market_expected_value"),
            "fd_fair_prob": fair,                             # from /offers (--offers)
            "fd_market_ev": mev,                              # from /offers (--offers)
            "implied_prob": round(implied(side.get("odds")), 4) if side and side.get("odds") is not None else None,
            "opp_rank_raw": rank,                             # BettingPros: 1 = toughest
            "dvp_softness": (31 - rank) if rank else None,    # converted: 1 = softest
            "event_id": p.get("event_id"),
        })

    rows.sort(key=lambda r: abs(r["pct_gap"] or 0), reverse=True)
    out = f"bp_props_{args.date}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["player"])
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} props -> {out}   (requests used this run: {REQUESTS_MADE})")
    print(f"{'player':22} {'market':14} {'side':5} {'line':>5} {'proj':>6} {'gap%':>6} {'FD':>6} {'DvP':>4}")
    for r in [r for r in rows if r["price_is_fanduel"]][:15]:
        print(f"{(r['player'] or '')[:22]:22} {str(r['market'])[:14]:14} {str(r['rec_side'])[:5]:5} "
              f"{r['line']!s:>5} {r['bp_proj']!s:>6} {r['pct_gap']!s:>6} {r['fd_odds_rec']!s:>6} {r['dvp_softness']!s:>4}")


if __name__ == "__main__":
    main()
