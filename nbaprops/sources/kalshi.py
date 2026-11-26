"""Kalshi NBA player props, read directly (free public API, no account): https://external-api.kalshi.com/trade-api/v2

Kalshi lists each game's player props as "N+" ladders (e.g. "Bones Hyland: 15+ points", floor_strike 14.5), which line
up with a FanDuel over at the same number. Only two-sided quotes count: a live yes bid and yes ask with a spread of
MAX_SPREAD or less. Fair over probability = the bid/ask midpoint; the two-way prices (for CrazyNinja's worst case) are the
costs to buy each side: over = yes ask, under = no ask = 1 - yes bid.

Unabated already carries Kalshi as an exchange, so price_board adds this direct quote only where Unabated has no Kalshi
price at FanDuel's line (never twice). Markets list on game day; P+A, P+R and R+A series exist but are rarely listed.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request

API = "https://external-api.kalshi.com/trade-api/v2"
SERIES = {"PTS": "KXNBAPTS", "REB": "KXNBAREB", "AST": "KXNBAAST", "3PM": "KXNBA3PT", "P+A": "KXNBAPA", "P+R": "KXNBAPR",
          "R+A": "KXNBARA", "PRA": "KXNBAPRA"}
MAX_SPREAD = 0.05
PAUSE = 0.25
CACHE_S = 60
_cache: dict = {}


def _get(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "python-nbaprops/2.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def date_code(date: str) -> str:
    """'2026-11-25' -> '26NOV25' (Kalshi event-ticker date)."""
    d = dt.date.fromisoformat(date)
    return f"{d:%y}{d:%b}".upper() + f"{d.day:02d}"


def american(p: float) -> float:
    """Probability (price in dollars) -> American odds."""
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def parse(markets: list[dict], date: str, market: str, now_iso: str) -> dict:
    """Open, two-sided markets for `date` -> {(norm player, market, line): quote}."""
    from nbaprops.sources.rotowire import norm
    code, out = date_code(date), {}
    for m in markets:
        if code not in (m.get("event_ticker") or "") or m.get("status") not in ("active", "open", None):
            continue
        try:
            bid, ask = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
            line = float(m.get("floor_strike"))
        except (TypeError, ValueError):
            continue
        if not (0 < bid < ask < 1) or ask - bid > MAX_SPREAD:
            continue
        player = (m.get("title") or "").split(":")[0].strip()
        out[(norm(player), market, line)] = {"fair": round((bid + ask) / 2, 4), "bid": bid, "ask": ask, "spread": round(ask - bid, 3),
                                             "over": american(ask), "under": american(1 - bid), "ticker": m.get("ticker"), "at": now_iso}
    return out


def quotes(date: str) -> dict:
    """All open two-sided Kalshi player-prop quotes for an ET date (cached for CACHE_S; {} on any error)."""
    hit = _cache.get(date)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    now_iso = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict = {}
    try:
        for mk, series in SERIES.items():
            cur = None
            for _ in range(10):
                j = _get(f"/markets?series_ticker={series}&status=open&limit=1000" + (f"&cursor={cur}" if cur else ""))
                out.update(parse(j.get("markets") or [], date, mk, now_iso))
                cur = j.get("cursor")
                time.sleep(PAUSE)
                if not cur:
                    break
    except Exception:  # noqa: BLE001 -- Kalshi is optional: Unabated prices without it
        out = out or {}
    _cache[date] = (time.time(), out)
    return out
