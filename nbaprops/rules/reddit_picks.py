"""Reddit picks ranked by track record.

Daily, before the first tip: read the r/sportsbook NBA picks thread, parse every comment with an NBA player prop, and log
each pick per poster as research (no stake, flat 1u) with its entry time and, where available, FanDuel's price and the
market EV at that moment (Unabated's latest saved prices; CrazyNinja paste store as a fallback). Image-only comments are
skipped and counted. Picks are graded from box scores and get closing CLV from the watch like everything else.

Proven poster: >= PROVEN_MIN graded picks, positive average CLV and positive flat units (re-checked on every run, so a
poster drops off when they stop qualifying). A proven poster's pick where the market also shows +EV (Unabated, 3+
sources) is logged to the staked record "Proven poster + market +EV" (¼K on the Unabated fair, BET caps).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import statistics

from nbaprops.rules import ledger as L
from nbaprops.rules import outside as O
from nbaprops.sources.rotowire import norm

KEY = "reddit_pick"           # research: every parsed pick, per poster (source = "Reddit u/<name>")
PROVEN_KEY = "proven_ev"      # staked: proven poster + market +EV
PROVEN_MIN = 30
IMAGE = re.compile(r"(i\.redd\.it|preview\.redd\.it|imgur\.com|\.(png|jpe?g|gif|webp)\b)", re.I)


def tonight_players(date: str) -> dict[str, dict]:
    """{name key: row} for players on tonight's board (the pull; Unabated's saved EVs as a fallback)."""
    out = {}
    path = os.path.join(L.HERE, f"bp_props_{date}.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            out.setdefault(norm(r["player"]), {"name": r["player"], "team": r.get("team"), "tip": r.get("tip"),
                                               "event_id": r.get("event_id")})
    try:
        from nbaprops.app import value as V
        for e in json.load(open(V.ua_store_path(date))).values():
            out.setdefault(norm(e["player"]), {"name": e["player"], "team": e.get("team"), "tip": e.get("tip")})
    except (OSError, ValueError):
        pass
    return out


def resolve(players: dict, name: str) -> tuple[dict | None, str]:
    """Tonight's player for a pick name: exact, nickname, unique last name, unique substring."""
    key = norm(O.NICKNAMES.get(norm(name), name))
    if key in players:
        return players[key], "exact" if key == norm(name) else "nickname"
    last = key.split()[-1] if key else ""
    by_last = [p for k, p in players.items() if k.split() and k.split()[-1] == last]
    if len(by_last) == 1:
        return by_last[0], "last name"
    sub = [p for k, p in players.items() if key and key in k]
    if len(sub) == 1:
        return sub[0], "partial"
    return None, "ambiguous" if (by_last or sub) else "not on tonight's board"


def parse_comments(comments: list[dict], date: str) -> tuple[list[dict], dict]:
    """-> (picks, counts). Each pick: author, comment id, created, player (resolved), market, side, line, odds (card)."""
    players = tonight_players(date)
    picks, counts = [], {"comments": len(comments), "with_props": 0, "image_only": 0, "unresolved": 0}
    for c in comments:
        body = c.get("body") or ""
        found, _ = O.parse(body)
        if not found:
            if IMAGE.search(body) and len(re.sub(r"\S*(i\.redd\.it|imgur|preview\.redd\.it)\S*", "", body).strip()) < 15:
                counts["image_only"] += 1
            continue
        counts["with_props"] += 1
        for p in found:
            who, how = resolve(players, p["player"])
            if who is None:
                counts["unresolved"] += 1
                continue
            picks.append(dict(p, player=who["name"], team=who.get("team"), tip=who.get("tip"), event_id=who.get("event_id"),
                              author=c["author"], comment_id=c["id"], match=how,
                              created=dt.datetime.fromtimestamp(c["created_utc"], dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                              if c.get("created_utc") else L.utc_now()))
    return picks, counts


def market_at_entry(date: str, p: dict) -> dict:
    """FanDuel price, fair and market EV for the pick's side and line from Unabated's latest saved prices (3+ sources),
    else the CrazyNinja paste store; {} if neither prices that line."""
    try:
        from nbaprops.app import value as V
        e = json.load(open(V.ua_store_path(date))).get(f"{p['player']}|{p['market']}|{p['side']}|{float(p['line']):g}")
        if e and (e.get("books") or 0) >= 3 and e.get("price") is not None:
            return {"price": e["price"], "fair": e.get("fair"), "ev": e.get("ev"), "books": e.get("books"), "src": "Unabated"}
    except (OSError, ValueError):
        pass
    from nbaprops.rules import screener as S
    e = S.store(date).get(f"{p['player']}|{p['market']}|{p['side']}|{float(p['line']):g}")
    if e:
        return {"price": e.get("odds"), "fair": e.get("fair"), "ev": e.get("ev"), "books": e.get("books"), "src": "CrazyNinja"}
    return {}


def poster_stats(version: str | None = None) -> list[dict]:
    """Per-poster record from graded, counted Reddit picks: picks, W-L, units, ROI, avg CLV, avg entry market EV, proven."""
    led = L._read(L.LEDGER)
    counted, clv = L.counted_flags(led)
    by: dict = {}
    for r, ok, v in zip(led, counted, clv):
        if r["strategy"] != KEY or not ok or (version and L.rule_version(r) != version):
            continue
        s = by.setdefault(r.get("fair_src") or "?", {"poster": r.get("fair_src") or "?", "picks": 0, "graded": [], "clv": [], "ev": []})
        s["picks"] += 1
        if r.get("result") in ("W", "L", "P"):
            s["graded"].append(r)
            if v is not None:
                s["clv"].append(v)
        if L._num(r.get("entry_ev")) is not None:
            s["ev"].append(L._num(r["entry_ev"]))
    out = []
    for s in by.values():
        g = s["graded"]
        units = sum(L._num(r.get("profit_u")) or 0 for r in g)
        x = {"poster": s["poster"], "picks": s["picks"], "n": len(g), "w": sum(r["result"] == "W" for r in g),
             "l": sum(r["result"] == "L" for r in g), "p": sum(r["result"] == "P" for r in g), "units": round(units, 2),
             "roi": round(100 * units / len(g), 1) if g else None, "clv": round(statistics.mean(s["clv"]), 2) if s["clv"] else None,
             "clv_n": len(s["clv"]), "entry_ev": round(100 * statistics.mean(s["ev"]), 2) if s["ev"] else None}
        x["proven"] = x["n"] >= PROVEN_MIN and (x["clv"] or 0) > 0 and units > 0
        out.append(x)
    return sorted(out, key=lambda x: (-x["proven"], -(x["clv"] if x["clv"] is not None else -99), -x["units"]))


def proven_posters() -> set[str]:
    return {x["poster"] for x in poster_stats() if x["proven"]}


def log(date: str, picks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Log research picks (once per poster/prop/day) and, for proven posters with market +EV, the staked record.
    -> (research rows, staked rows)."""
    from nbaprops.app import value as V
    led = L._read(L.LEDGER)
    k = lambda src, pl, m, s, ln: (src, norm(pl), m, s, str(L._num(ln)))
    have = {k(r.get("fair_src"), r["player"], r["market"], r["side"], r.get("line")) for r in led
            if r["date"] == date and r["strategy"] in (KEY, PROVEN_KEY)}
    proven = proven_posters()
    research, staked = [], []
    for p in picks:
        src = f"Reddit u/{p['author']}"
        if k(src, p["player"], p["market"], p["side"], p["line"]) in have:
            continue
        have.add(k(src, p["player"], p["market"], p["side"], p["line"]))
        if p.get("tip") and p["created"] >= p["tip"]:
            continue                                         # posted after tip: not a pregame pick
        mk = market_at_entry(date, p)
        price = mk.get("price") if mk.get("price") is not None else (p["odds"] if p["odds"] is not None else O.NO_PRICE)
        base = {"date": date, "pulled_at": p["created"], "tip": p.get("tip") or "", "player": p["player"], "team": p.get("team"),
                "market": p["market"], "side": p["side"], "line": p["line"], "odds": price, "event_id": p.get("event_id") or "",
                "oa_fair": mk.get("fair"), "oa_ev": mk.get("ev"), "sources": mk.get("books"), "fair_src": src,
                "cap_note": "; ".join(x for x in (f"price from {mk['src']}" if mk else ("card price" if p["odds"] is not None
                                                  else "no price: units at -110"), f"name: {p['match']}", f"comment {p['comment_id']}") if x)}
        research.append(dict(base, strategy=KEY, kelly_u=None))
        if src in proven and mk.get("src") == "Unabated" and (mk.get("ev") or 0) > 0:
            staked.append(dict(base, strategy=PROVEN_KEY, kelly_u=V.bet_kelly(mk["fair"], mk["price"])))
    if staked:                                               # BET exposure caps within this record
        logged = [r for r in led if r["date"] == date and r["strategy"] == PROVEN_KEY]
        key = V.game_resolver(staked + logged)
        cc = V.apply_exposure([{"player": r["player"], "game": key(r), "ev": r["oa_ev"], "stake": r["kelly_u"]} for r in staked], logged, key)
        staked = [dict(r, kelly_u=c["stake"], cap_note=(r["cap_note"] + ("; " + c["cap"] if c["cap"] else ""))) for r, c in zip(staked, cc) if c["stake"] > 0]
    if research or staked:
        L._write(L.LEDGER, led + research + staked, L.LEDGER_FIELDS)
    return research, staked
