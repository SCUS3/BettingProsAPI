#!/usr/bin/env python3
"""
Unabated as the primary fair-price source for bp_value.py (The Odds API stays the fallback).

    python3 bp_unabated.py --discover     # discovery only (/league, /bettype, props sources, people, period types):
                                          # does not use the odds rate limit; saves unabated_discovery.json
    python3 bp_unabated.py --status       # what discovery found, and whether Unabated is enabled

Docs (docs.unabated.com): base https://data.unabated.com, key in the X-Api-Key header, envelope
{success, data, messages} (check success before data). Props board: GET /market/{league}/props/odds with
repeated betTypeId; no sportsbook/event filter, so one call covers every game. Free API: one odds call every
5 seconds per key (shared across odds routes), odds delayed 15s+. 429/503 carry Retry-After.
A 401/403 marks Unabated unavailable (unabated_state.json) so bp_value.py switches to The Odds API and says so.
Key: UNABATED_API_KEY in .env, never printed.
"""

import argparse
import email.utils
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp_props  # noqa: E402
from bp_rotowire import norm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("UNABATED_API_BASE_URL", "https://data.unabated.com")
DISCOVERY = os.path.join(HERE, "unabated_discovery.json")
STATE = os.path.join(HERE, "unabated_state.json")
LAST_ODDS = os.path.join(HERE, ".unabated_last_odds")
ODDS_INTERVAL = 5.0          # seconds between odds calls, per key
LEAGUE = "nba"
# Consensus sources (names as /market/nba/props/sources lists them), used only when both over and under are
# priced at FanDuel's line. Pick'em apps (PrizePicks, Underdog, Pick6) are left out: their prices aren't odds.
# Duplicate feeds are left out too (TheScore CA / TheScore (Fast); Polymarket only if Polymarket US is missing).
BOOK_SOURCES = ["DraftKings", "BetMGM", "Caesars", "Fanatics", "TheScore US"]
EXCHANGE_SOURCES = ["Novig", "Prophet Exchange", "Kalshi", "Polymarket US", "Polymarket"]
MIN_SOURCES = 3
# Player over/under single-game bet types (from /bettype; names are shared across sports, so match exactly
# and require betOn=player, sides=ou, betRange=event, not a future). Some names have two ids (e.g. Points 73/275);
# both go in the odds query, which is still one call.
BETTYPE_NAMES = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "Three Pointers Made": "3PM",
                 "Points Assists": "P+A", "Points and Rebounds": "P+R", "Points Rebounds Assists": "PRA",
                 "Rebounds Assists": "R+A"}


class Unavailable(Exception):
    """The key stopped working (401/403) or the route isn't in this key's tier."""


def key():
    bp_props.load_dotenv()
    return os.environ.get("UNABATED_API_KEY")


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, d):
    with open(path, "w") as f:
        json.dump(d, f, indent=1)


def mark_unavailable(reason):
    save(STATE, {"disabled": True, "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def state():
    return load(STATE, {"disabled": False})


def _retry_after(h, default=5.0):
    v = (h or {}).get("Retry-After")
    if not v:
        return default
    try:
        return max(float(v), 0.0)
    except ValueError:
        t = email.utils.parsedate_to_datetime(v)
        return max(t.timestamp() - time.time(), 0.0) if t else default


def _throttle():
    """At most one odds call every ODDS_INTERVAL seconds, across runs (timestamp kept on disk)."""
    try:
        last = float(open(LAST_ODDS).read().strip())
    except (OSError, ValueError):
        last = 0.0
    wait = last + ODDS_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    with open(LAST_ODDS, "w") as f:
        f.write(str(time.time()))


def get(path, params=None, odds=False, tries=3):
    """GET -> data. Odds calls are throttled. 429/503: wait Retry-After (bounded). 401/403: Unavailable."""
    k = key()
    if not k:
        raise Unavailable("UNABATED_API_KEY is not set in .env")
    url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    for attempt in range(tries):
        if odds:
            _throttle()
        req = urllib.request.Request(url, headers={"X-Api-Key": k, "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (401, 403):
                reason = f"HTTP {e.code} on {path}: {text}"
                mark_unavailable(reason)
                raise Unavailable(reason)
            if e.code in (429, 503) and attempt < tries - 1:
                wait = min(_retry_after(e.headers), 60.0)
                print(f"  Unabated {e.code} on {path}; waiting {wait:.0f}s (Retry-After)")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Unabated HTTP {e.code} on {path}: {text}")
        try:
            env = json.loads(body) if body.strip() else {}
        except ValueError:
            raise RuntimeError(f"Unabated {path}: non-JSON response")
        if isinstance(env, dict) and env.get("success") is False:
            raise RuntimeError(f"Unabated {path}: success=false {env.get('messages')}")
        if isinstance(env, dict) and "success" in env:
            return env.get("data")
        return env
    raise RuntimeError(f"Unabated {path}: gave up after {tries} tries")


# ------------------------------------------------------------------ discovery
def _items(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("items", "results", "leagues", "betTypes", "sources", "people", "players", "periodTypes"):
            if isinstance(d.get(k), list):
                return d[k]
        if all(isinstance(v, dict) for v in d.values()):
            return list(d.values())
    return []


def _name(x):
    if not isinstance(x, dict):
        return ""
    for k in ("name", "fullName", "displayName", "title"):
        if x.get(k):
            return str(x[k])
    return " ".join(str(x.get(k) or "") for k in ("firstName", "lastName")).strip()


def discover():
    """Discovery routes only (no odds interval). Saves raw responses plus what they mean for us."""
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "raw": {}}
    out["raw"]["league"] = get("/league")
    out["raw"]["bettype"] = get("/bettype")
    for path, k in ((f"/market/{LEAGUE}/props/sources", "sources"), ("/period-type", "period_types"),
                    (f"/market/{LEAGUE}/props/people", "people")):
        try:
            out["raw"][k] = get(path)
        except (RuntimeError, Unavailable) as e:
            out["raw"][k] = {"error": str(e)}
            if isinstance(e, Unavailable):
                out.setdefault("unavailable", []).append(path)
    return analyze(out)


def analyze(out):
    """Interpret saved discovery responses (no calls)."""
    raw = out["raw"]
    nba = next((x for x in _items(raw.get("league")) if str(x.get("name", "")).lower() == LEAGUE), None)
    out["nba"] = bool(nba)
    out["nba_league_id"] = nba.get("id") if nba else None
    tags = {}
    for b in _items(raw.get("bettype")):
        tag = BETTYPE_NAMES.get(str(b.get("name", "")).strip())
        if (tag and tag not in bp_props.DISABLED and b.get("betOn") == "player" and b.get("sides") == "ou"
                and b.get("betRange") == "event" and not b.get("isFuture") and b.get("active", True)):
            tags.setdefault(tag, []).append({"id": b["id"], "name": b["name"]})
    out["bettypes"] = tags
    out["missing_markets"] = [t for t in ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA") if t not in tags]
    people = _items(raw.get("people"))
    out["people_count"] = len(people)
    srcs = _items(raw.get("sources"))
    fd = [s for s in srcs if "fanduel" in _name(s).lower().replace(" ", "")]
    line = [s for s in srcs if "unabated" in _name(s).lower()]
    out["fanduel_source_id"] = fd[0].get("id") if fd else None
    out["line_source_id"] = line[0].get("id") if line else None
    out["sources"] = [{"id": s.get("id"), "name": _name(s), "propsStatusId": s.get("propsStatusId"),
                       "disabledMarketTypeIds": s.get("disabledMarketTypeIds")} for s in srcs]
    out["full_game_period_ids"] = [p.get("id") for p in _items(out["raw"].get("period_types"))
                                   if norm(_name(p)) in ("full game", "game", "full time", "fullgame")]
    out["props_ok"] = bool(nba and tags and srcs and people and "unavailable" not in out)
    save(DISCOVERY, out)
    if out["props_ok"]:
        save(STATE, {"disabled": False})
    return out


def report(d):
    print(f"NBA league: {'yes' if d.get('nba') else 'NO'}")
    print("Bet types for our markets: " + (", ".join(f"{t} = {' / '.join(b['name'] + ' (' + str(b['id']) + ')' for b in v)}" for t, v in sorted(d.get("bettypes", {}).items())) or "none"))
    print(f"People (player ids) on the NBA props board: {d.get('people_count', 0)}")
    if d.get("missing_markets"):
        print(f"No bet type matched: {', '.join(d['missing_markets'])}")
    print(f"Props sources: {len(d.get('sources', []))}" + (f" ({', '.join(s['name'] for s in d['sources'])})" if d.get("sources") else ""))
    print(f"FanDuel in props sources: {'yes (id ' + str(d['fanduel_source_id']) + ')' if d.get('fanduel_source_id') is not None else 'NO'}")
    print(f"Unabated Line as a props source: {'yes (id ' + str(d['line_source_id']) + ')' if d.get('line_source_id') is not None else 'NOT FOUND (needs an odds call or a higher tier to confirm)'}")
    if d.get("unavailable"):
        print(f"Not in this key's tier: {', '.join(d['unavailable'])}")
    print("Fair price from Unabated: " + ("Unabated Line" if d.get("line_source_id") is not None else
          f"median of devigged books/exchanges at FanDuel's line ({MIN_SOURCES}+ sources)" if d.get("props_ok") else "not available"))


# ----------------------------------------------------------------- odds/fair
def usable():
    """(ok, why). Unabated is used only with a key, a passing discovery, and a Line source for props."""
    if not key():
        return False, "UNABATED_API_KEY not set"
    st = state()
    if st.get("disabled"):
        return False, f"disabled since {st.get('at')} ({st.get('reason')}); run bp_unabated.py --discover to re-check"
    d = load(DISCOVERY, None)
    if not d:
        return False, "no discovery yet (run bp_unabated.py --discover)"
    if not d.get("props_ok"):
        return False, "discovery found no usable NBA props on this key"
    return True, ""


def method():
    d = load(DISCOVERY, {})
    return "line" if d.get("line_source_id") is not None else "books"


def walk_sides(node, ctx=None, live=False):
    """Yield (context, side) for every side carrying marketSourceLines, pregame only."""
    ctx = dict(ctx or {})
    if isinstance(node, dict):
        for k in ("eventId", "eventName", "eventStart", "isLive", "betSubType", "betTypeId", "periodTypeId", "personId",
                  "sideIndex", "sideName", "teamId"):
            if k in node:
                ctx[k] = node[k]
        if "marketSourceLines" in node and not live:
            yield ctx, node
        for k, v in node.items():
            if k == "marketSourceLines":
                continue
            yield from walk_sides(v, ctx, live or k == "live")
    elif isinstance(node, list):
        for v in node:
            yield from walk_sides(v, ctx, live)


def _lines(msl):
    if isinstance(msl, dict):
        return [dict(v, marketSourceId=v.get("marketSourceId", k)) for k, v in msl.items() if isinstance(v, dict)]
    return [x for x in msl or [] if isinstance(x, dict)]


def fair_prices(picks, now=None):
    """One props odds call for the picks' markets. Fair probability per pick at FanDuel's line:
    the Unabated Line (devigged) if the key has it, else the median of devigged non-FanDuel books and exchanges
    pricing both sides at FanDuel's line (MIN_SOURCES+ required). Pregame, full game, main lines only.
    Returns ({pick key: result}, calls). Raises Unavailable on 401/403."""
    d = load(DISCOVERY, {})
    bt_ids = sorted({b["id"] for p in picks for b in d.get("bettypes", {}).get(p["market"], [])})
    if not bt_ids:
        return {}, 0
    data = get(f"/market/{LEAGUE}/props/odds", {"betTypeId": bt_ids}, odds=True)
    return price_board(picks, data, d, now), 1


def _devig_over(o, u):
    io, iu = (100 / (a + 100) if a > 0 else -a / (-a + 100) for a in (o, u))
    return io / (io + iu)


def price_board(picks, data, d, now=None):
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    people = {str(x["id"]): norm(_name(x)) for x in _items((d.get("raw") or {}).get("people")) if x.get("id") is not None}
    bt_tag = {str(b["id"]): t for t, v in d.get("bettypes", {}).items() for b in v}
    full = {str(i) for i in d.get("full_game_period_ids") or [1]}
    names = {str(s["id"]): s["name"] for s in d.get("sources", [])}
    fd_id, line_id = str(d.get("fanduel_source_id")), str(d.get("line_source_id"))
    books = {i for i, n in names.items() if n in BOOK_SOURCES}
    exch = {i for i, n in names.items() if n in EXCHANGE_SOURCES}
    poly_us = next((i for i, n in names.items() if n == "Polymarket US"), None)
    board = {}                                  # (player, market) -> {source id: {side: (points, price)}}
    for ctx, side in walk_sides((data or {}).get("odds") if isinstance(data, dict) else data):
        if ctx.get("isLive") or ctx.get("betSubType") or str(ctx.get("periodTypeId")) not in full:
            continue                            # live, alternate/milestone, or not full game
        if ctx.get("eventStart") and str(ctx["eventStart"]) <= now:
            continue
        tag, who, si = bt_tag.get(str(ctx.get("betTypeId"))), people.get(str(ctx.get("personId"))), str(ctx.get("sideIndex"))
        if not tag or not who or si not in ("0", "1"):
            continue
        for ln in _lines(side.get("marketSourceLines")):
            if ln.get("disabled") or ln.get("statusId") not in (None, 1) or ln.get("price") is None or ln.get("points") is None:
                continue
            board.setdefault((who, tag), {}).setdefault(str(ln.get("marketSourceId")), {})["over" if si == "0" else "under"] = \
                (float(ln["points"]), ln["price"])
    out = {}
    for p in picks:
        b = board.get((norm(p["player"]), p["market"]))
        if not b:
            continue
        line = float(p["line"])
        two = {src: sd for src, sd in b.items() if "over" in sd and "under" in sd and sd["over"][0] == sd["under"][0]}
        fd = b.get(fd_id, {})
        fd_line = fd.get(p["side"], (None, None))[0]
        price = fd[p["side"]][1] if fd_line == line else p.get("odds")
        res = {"fair_src": "Unabated", "oa_fd_line": fd_line, "oa_fd_price": fd.get(p["side"], (None, None))[1],
               "price_used": price, "fair_add": None, "ev_add": None}
        if line_id in two:                      # Unabated Line, if this key ever returns it
            if two[line_id]["over"][0] != line:
                res.update(fair_mult=None, ev_mult=None, books=0, note=f"Unabated Line at {two[line_id]['over'][0]:g}")
            else:
                f = _devig_over(two[line_id]["over"][1], two[line_id]["under"][1])
                f = f if p["side"] == "over" else 1 - f
                res.update(fair_method="line", fair_mult=round(f, 4), ev_mult=None if price is None else round(f * _dec(price) - 1, 4), books=1,
                           sources=["Unabated Line"], exchange=False, other_line_books=0)
            out[(p["player"], p["market"], p["side"])] = res
            continue
        use = [src for src in two if src in books or src in exch]
        if poly_us in use:
            use = [src for src in use if names.get(src) != "Polymarket"]
        at = [src for src in use if two[src]["over"][0] == line]
        other = [src for src in use if two[src]["over"][0] != line]
        fairs = []
        for src in at:
            f = _devig_over(two[src]["over"][1], two[src]["under"][1])
            fairs.append(f if p["side"] == "over" else 1 - f)
        res.update(fair_method="books", books=len(at), sources=sorted(names.get(x, x) for x in at),
                   exchange=any(x in exch for x in at), other_line_books=len(other))
        if len(at) >= MIN_SOURCES:
            f = statistics.median(fairs)
            res.update(fair_mult=round(f, 4), ev_mult=None if price is None else round(f * _dec(price) - 1, 4))
        else:
            res.update(fair_mult=None, ev_mult=None, note=f"{len(at)} sources at FanDuel's line (needs {MIN_SOURCES})")
        out[(p["player"], p["market"], p["side"])] = res
    return out


def _dec(a):
    return 1 + (a / 100 if a > 0 else 100 / -a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="run discovery (no odds interval used)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reanalyze", action="store_true", help="re-read the saved discovery responses (no calls)")
    a = ap.parse_args()
    if a.discover:
        try:
            report(discover())
        except Unavailable as e:
            print(f"Unabated unavailable: {e}. bp_value.py will use The Odds API.")
        return
    d = load(DISCOVERY, None)
    if a.reanalyze and d:
        d = analyze(d)
    if d:
        report(d)
    ok, why = usable()
    print("Unabated: " + ("enabled as the primary fair-price source" if ok else f"not used ({why}); The Odds API is the source"))


if __name__ == "__main__":
    main()
