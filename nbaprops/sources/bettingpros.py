"""BettingPros Consumer API v3: HTTP with back-off, per-UTC-day request accounting, the --max-requests hard stop,
the .env key loader, the books/markets cache, market classification, and the /props and /offers pagers.

Request state (REQUESTS_MADE, RUN_CAP, PULL_LOG) lives here only; callers read and set it as module attributes
(``bettingpros.RUN_CAP = n``), never through copied names. Every call costs requests; ask before calling (CLAUDE.md).
"""
from nbaprops import config as C  # thresholds, caps and settings
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from nbaprops.config import ROOT as _ROOT


BASE = "https://api.bettingpros.com/v3"


REQUESTS_MADE = 0


DAILY_LIMIT = C.BP_DAILY_LIMIT  # nbaprops/config.py


REQUEST_LOG = os.path.join(_ROOT, "bp_requests.json")


PULL_LOG = []         # one entry per /props page this run: rows returned vs. _pagination totals (saved to history/)


RUN_CAP = None         # --max-requests: hard stop for this run, checked before every request


class RunCapReached(RuntimeError):
    pass


def get(path, params, key, retries=3):
    """GET a v3 endpoint, honouring 429/503 back-off."""
    global REQUESTS_MADE
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"x-api-key": key, "accept": "application/json"})
        if RUN_CAP is not None and REQUESTS_MADE >= RUN_CAP:
            raise RunCapReached(f"--max-requests {RUN_CAP} reached; request not sent")
        try:
            REQUESTS_MADE += 1
            _count_request()
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


def _utc_day():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _count_request():
    """Keep a per-UTC-day count of every request this project sends (bp_requests.json)."""
    try:
        with open(REQUEST_LOG) as f:
            log = json.load(f)
    except (OSError, ValueError):
        log = {}
    day = _utc_day()
    log[day] = log.get(day, 0) + 1
    try:
        with open(REQUEST_LOG, "w") as f:
            json.dump(dict(sorted(log.items())[-30:]), f, indent=1)
    except OSError:
        pass


def used_today():
    try:
        with open(REQUEST_LOG) as f:
            return json.load(f).get(_utc_day(), 0)
    except (OSError, ValueError):
        return 0


def load_dotenv():
    """Read KEY=value lines from .env next to the script (without overriding real env vars)."""
    path = os.path.join(_ROOT, ".env")
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


CACHE = os.path.join(_ROOT, "bp_cache.json")


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


# Categories classified but left out of every pull. R+A was off 11/19-11/23 (BettingPros carried no FanDuel R+A
# offers); back on from 11/24 for projection/lean context, and Unabated prices FanDuel's R+A lines.
DISABLED = set()


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
    elif date:
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
        # participants[] uses the same participant schema as /props (its id is what /props' player_id filters on)
        rec = {"event_id": off.get("event_id"), "market_id": off.get("market_id"),
               "player_id": str(p0.get("id") or off.get("player_id") or ""),
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
