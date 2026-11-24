"""Outside picks (Reddit / X / RotoWire cards and the like): paste them, log them as their own record per source, and
grade them from ESPN box scores. No stake: flat 1u. Where a card has no price, units assume -110 and the row says so.

    python3 bp_value.py --picks-paste cards.txt --date 2026-11-16 --source "Reddit/X card (v1, pre-system)"

One pick per line, in any of these shapes (case-insensitive; '|' or ',' separators are fine):
    Jalen Brunson Over 27.5 Points -115
    Brunson PTS o27.5 (-115)
    LaMelo Ball 3PM u3.5
Lines that aren't a player prop (game totals, spreads, moneylines) are skipped and listed.
"""
from __future__ import annotations

import re

from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm

KEY = "ext_pick"
NO_PRICE = -110.0             # assumed price for units when a card gives none
MARKETS = [
    (r"pts\s*\+\s*reb\s*\+\s*ast|points\s*\+\s*rebounds\s*\+\s*assists|\bpra\b|p\+r\+a", "PRA"),
    (r"pts\s*\+\s*ast|points\s*\+\s*assists|\bp\+a\b", "P+A"),
    (r"pts\s*\+\s*reb|points\s*\+\s*rebounds|\bp\+r\b", "P+R"),
    (r"reb\s*\+\s*ast|rebounds\s*\+\s*assists|\br\+a\b", "R+A"),
    (r"three[\s-]*point(?:er)?s?|threes|\b3pm\b|\b3pt\b|3-pointers|\b3s\b", "3PM"),
    (r"\bpoints\b|\bpts\b", "PTS"),
    (r"\brebounds\b|\breb\b|\brebs\b", "REB"),
    (r"\bassists\b|\bast\b|\basts\b", "AST"),
]
SIDE = re.compile(r"\b(over|under|o|u)\s*(\d+(?:\.\d)?)\b", re.I)
PRICE = re.compile(r"(?<![\w.])([+-]\d{3,4})(?![\d.%])")
NOT_PROP = re.compile(r"\b(total|spread|moneyline|ml|team total|1h|first half|q1)\b|@|\bvs\.?\b", re.I)


def parse(text: str) -> tuple[list[dict], list[str]]:
    """-> (picks, skipped lines). pick = {player, market, side, line, odds (None if not given)}."""
    picks, skipped = [], []
    for raw in [x.strip() for x in text.splitlines() if x.strip()]:
        ln = raw.replace("|", " ").replace(",", " ")
        low = ln.lower()
        mk = next((m for pat, m in MARKETS if re.search(pat, low)), None)
        sm = SIDE.search(ln)
        if not mk or not sm or NOT_PROP.search(low):
            skipped.append(raw)
            continue
        # player = the words before the side or the market word, whichever comes first
        cut = min(sm.start(), min((m.start() for pat, _ in MARKETS for m in [re.search(pat, low)] if m), default=sm.start()))
        player = re.sub(r"[^A-Za-z.'\- ]", "", ln[:cut]).strip()
        if not player:
            skipped.append(raw)
            continue
        pm = PRICE.search(ln[sm.end():]) or PRICE.search(ln)
        picks.append({"player": player, "market": mk, "side": "over" if sm.group(1).lower().startswith("o") else "under",
                      "line": float(sm.group(2)), "odds": float(pm.group(1)) if pm else None, "raw": raw})
    return picks, skipped


def log(picks: list[dict], date: str, source: str) -> list[dict]:
    """Log outside picks once per (source, player, market, side, line) and date. Flat 1u, no stake."""
    led = L._read(L.LEDGER)
    have = {(r.get("fair_src"), norm(r["player"]), r["market"], r["side"], str(L._num(r.get("line")))) for r in led
            if r["date"] == date and r["strategy"] == KEY}
    out = []
    for p in picks:
        k = (source, norm(p["player"]), p["market"], p["side"], str(p["line"]))
        if k in have:
            continue
        have.add(k)
        out.append({"date": date, "pulled_at": f"{date}T12:00:00Z", "strategy": KEY, "player": p["player"],
                    "market": p["market"], "side": p["side"], "line": p["line"],
                    "odds": p["odds"] if p["odds"] is not None else NO_PRICE, "kelly_u": None, "fair_src": source,
                    "cap_note": "" if p["odds"] is not None else "no price on the card: units at -110"})
    if out:
        L._write(L.LEDGER, led + out, L.LEDGER_FIELDS)
    return out


def grade(date: str) -> tuple[int, int]:
    """Grade the date's outside picks from ESPN box scores (free). -> (graded, still pending)."""
    from nbaprops.grading import grade as G
    from nbaprops.sources import espn as BX
    box = BX.finals(date)
    led = L._read(L.LEDGER)
    n = pend = 0
    for r in led:
        if r["date"] != date or r["strategy"] != KEY or r.get("result"):
            continue
        p = BX.lookup(box, r["player"])
        v = BX.stat(p, r["market"]) if p else None
        if p is not None and v is None and not p.get("played"):
            r.update(result="V", actual="", profit_u=0.0)
            n += 1
        elif v is not None:
            res = G.grade(r["side"], r["line"], v)
            r.update(result=res, actual=v, profit_u=G.profit(r["odds"], res))
            n += 1
        else:
            pend += 1
    L._write(L.LEDGER, led, L.LEDGER_FIELDS)
    return n, pend
