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
import math
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

MKT_CACHE_KEY = "nba_markets_v2"  # bump when classify() changes

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
        return {int(k): tuple(v) for k, v in cache[MKT_CACHE_KEY].items()}
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


CATEGORY_ORDER = [("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists"), ("3PM", "Threes"),
                  ("P+A", "Points + Assists"), ("P+R", "Points + Rebounds"), ("PRA", "Points + Rebounds + Assists")]


def write_top_by_market(rows, date, n=10):
    """Write the Markdown note: three overall top-10 lists, then top 10 by gap and by score in each category."""
    out = f"bp_top10_{date}.md"
    def cell(v):
        return "" if v is None else fmt(v)
    fd = [r for r in rows if r["price_is_fanduel"] and r.get("score") is not None]
    head = ("| # | Player | Market | Side | Line | Proj | Gap % | FD odds | DvP | Form | Score | Balanced | Signals | Kelly | Injury |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    def table(group, bold):
        # mark the column this list is ranked by in the header (no ** in cells: Google Docs shows them literally)
        cols = {"gap": "Gap %", "score": "Score", "balanced": "Balanced"}
        t = [head[0].replace(f"| {cols[bold]} |", f"| {cols[bold]} ▼ |"), head[1]]
        for i, r in enumerate(group[:n], 1):
            vals = {"gap": cell(r["pct_gap"]), "score": cell(r["score"]), "balanced": cell(r["balanced"])}
            form = "" if r.get("form_rate") is None else f"{round(r['form_rate'] * 100)}%"
            kelly = "" if r.get("kelly_u") is None else f"{fmt(r['kelly_u'])}u"
            t.append("| " + " | ".join([str(i), cell(r["player"]), cell(r["market"]), cell(r["rec_side"]),
                                        cell(r["line"]), cell(r["bp_proj"]), vals["gap"], cell(r["fd_odds_rec"]),
                                        cell(r["dvp_softness"]), form, vals["score"], vals["balanced"],
                                        cell(r.get("signals")), kelly, cell(r["injury"])]) + " |")
        return t + [""]

    lines = [f"# NBA props: {date}", "",
             "FanDuel-priced props only. **DvP**: 1 = softest matchup. **Form**: recent hit rate of the "
             "recommended side vs. this line. **Score**: your rules (40% edge, 35% FanDuel EV, 25% matchup). "
             "**Balanced**: 40% Score + 25% form + 20% market value (no-vig price, line vs. consensus) + "
             "15% BettingPros bet rating. **Signals**: how many of six factors lean the same way. "
             "**Kelly**: quarter-Kelly stake, 1u = 1% of bankroll. All scores are cut for injury status.", ""]
    lines += ["## 1. Top 10 by projection gap", "", "Biggest |projection - line| as a % of the line, nothing else.", ""]
    lines += table(sorted(fd, key=lambda r: abs(r["pct_gap"] or 0), reverse=True), "gap")
    lines += ["## 2. Top 10 by your score", "", "Edge, FanDuel EV and matchup.", ""]
    lines += table(sorted(fd, key=lambda r: r["score"], reverse=True), "score")
    lines += ["## 3. Top 10 by balanced score", "", "Your score checked against recent form, market value and BettingPros' rating.", ""]
    lines += table(sorted(fd, key=lambda r: r["balanced"], reverse=True), "balanced")
    lines += ["---", "", "# Each category: top 10 by projection gap and top 10 by your score", ""]
    for tag, name in CATEGORY_ORDER:
        group = [r for r in fd if r["market"] == tag and r["pct_gap"] is not None]
        lines += [f"## {name} ({tag})", ""]
        if not group:
            lines += ["_No FanDuel-priced props found._", ""]
            continue
        lines += [f"### {tag}: by projection gap", ""]
        lines += table(sorted(group, key=lambda r: abs(r["pct_gap"]), reverse=True), "gap")
        lines += [f"### {tag}: by your score", ""]
        lines += table(sorted(group, key=lambda r: r["score"], reverse=True), "score")
    with open(out, "w") as f:
        f.write("\n".join(lines))
    return out


# ---------------------------------------------------------------- scoring
# Each prop gets a 0-100 score built from three parts (weights below), then an
# injury multiplier. The parts are scaled so threes, rebounds and points compete
# fairly: the gap is measured in standard deviations, not % of the line.
W_EDGE, W_EV, W_DVP = 0.40, 0.35, 0.25
INJURY_MULT = {"": 1.0, "probable": 1.0, "dtd": 0.85, "questionable": 0.85, "gtd": 0.85,
               "doubtful": 0.5, "out": 0.0}


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
        r.update(model_prob=None, model_ev=None, s_edge=None, s_ev=None, s_dvp=None, score=None)
        return r

    # 1. Edge: how many standard deviations the projection sits from the line.
    #    Counting stats spread roughly like sqrt(mean); 1.1x allows for extra variance.
    sd = 1.1 * math.sqrt(max(proj, line, 0.5))
    z = abs(proj - line) / sd
    s_edge = min(z / 0.75, 1.0) * 100                    # 0.75 SD or more = full marks

    # 2. EV at FanDuel's price: win chance (BettingPros' if given, else from the
    #    projection) against the odds. Juiced prices score lower here.
    p = _num(r.get("bp_win_prob"))
    if p is not None and p > 1:
        p /= 100
    if p is None:
        p = _phi(z)                                      # chance the recommended side hits
    evs = []
    if odds is not None:
        dec = 1 + (odds / 100 if odds > 0 else 100 / -odds)
        evs.append(p * dec - 1)
    for k in ("fd_market_ev", "bp_ev"):                  # blend in published EVs when present
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
    r.update(model_prob=round(p, 3), model_ev=None if ev is None else round(ev, 3),
             s_edge=round(s_edge), s_ev=round(s_ev), s_dvp=round(s_dvp), score=round(score))
    return r


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
    if line is not None and cons is not None and proj is not None:
        sd = 1.1 * math.sqrt(max(proj, line, 0.5))
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
    agree = sum(x >= 60 for x in (r["s_edge"], r["s_ev"], r["s_dvp"], s_form, s_mkt, s_bp))
    r.update(form_rate=rate, s_form=round(s_form), nv_edge=nv, s_market=round(s_mkt), s_bp=round(s_bp),
             kelly_u=kelly_units(r), signals=f"{agree}/6", balanced=round(bal))
    return r


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
            for k in ("line", "bp_proj", "diff", "pct_gap", "fd_odds_rec", "dvp_softness"):
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
        perf = p.get("performance") or {}
        def form(win):
            w = perf.get(win) or {}
            hits, n = w.get(rec or "", 0) or 0, (w.get("over", 0) or 0) + (w.get("under", 0) or 0)
            return f"{hits}-{n}" if n else None
        both_fd = over.get("book") == fd and under.get("book") == fd
        scored = p.get("scoring") or {}
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
            "bp_win_prob": first(proj.get("probability"), side.get("probability")),
            "bp_ev": first(proj.get("expected_value"), side.get("expected_value")),
            "bp_rating": first(proj.get("bet_rating"), side.get("bet_rating")),
            "market_prob": proj.get("market_probability"),
            "market_ev": proj.get("market_expected_value"),
            "fd_fair_prob": fair,                             # from /offers (--offers)
            "fd_market_ev": mev,                              # from /offers (--offers)
            "implied_prob": round(implied(side.get("odds")), 4) if side and side.get("odds") is not None else None,
            "opp_rank_raw": rank,                             # BettingPros: 1 = toughest
            "dvp_softness": (31 - rank) if rank else None,    # converted: 1 = softest
            "over_odds": over.get("odds") if both_fd else None,      # both FanDuel sides -> no-vig fair prob
            "under_odds": under.get("odds") if both_fd else None,
            "consensus_line": side.get("consensus_line"),
            "consensus_odds": side.get("consensus_odds"),
            "form_l5": form("last_5"),                        # rec-side hits - games vs this line
            "form_l10": form("last_10"),
            "form_l20": form("last_20"),
            "form_season": form("season"),
            "form_h2h": form("h2h"),
            "streak": f"{perf.get('streak')} {perf.get('streak_type')}" if perf.get("streak") else None,
            "is_scored": scored.get("is_scored"),            # for back-testing later
            "actual": scored.get("actual") if scored.get("is_scored") else None,
            "event_id": p.get("event_id"),
        })

    finish(rows, args.date)

def fmt(v):
    """Print floats without trailing .0 (2.0 -> 2) so the tables stay tidy."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def finish(rows, date):
    """Score, sort, and write the CSV, the top-10 note, and the terminal summary."""
    for r in rows:
        score_row(r)
        balance_row(r)
    rows.sort(key=lambda r: abs(r["pct_gap"] or 0), reverse=True)
    out = f"bp_props_{date}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["player"])
        w.writeheader()
        w.writerows(rows)

    note = write_top_by_market(rows, date)

    print(f"{len(rows)} props -> {out}   (requests used this run: {REQUESTS_MADE})")
    print(f"Top 10 lists (overall + gap and score per category) -> {note}")
    print("Top 15 by gap:")
    print(f"{'player':22} {'market':8} {'side':5} {'line':>5} {'proj':>6} {'gap%':>6} {'FD':>6} {'DvP':>4} {'score':>5} {'bal':>4}")
    for r in [r for r in rows if r["price_is_fanduel"]][:15]:
        print(f"{(r['player'] or '')[:22]:22} {str(r['market'])[:8]:8} {str(r['rec_side'])[:5]:5} "
              f"{fmt(r['line']):>5} {fmt(r['bp_proj']):>6} {fmt(r['pct_gap']):>6} {fmt(r['fd_odds_rec']):>6} "
              f"{fmt(r['dvp_softness']):>4} {fmt(r['score']):>5} {fmt(r['balanced']):>4}")


if __name__ == "__main__":
    main()
