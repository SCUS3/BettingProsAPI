"""Alt-line ladder scan (FanDuel alternate rungs vs. other sources at the same rung). Free: uses the Unabated board
the board run / watch already fetched; CrazyNinja worst case per source (open API).

Rules (each FanDuel alt rung, both sides):
  - another source must price BOTH sides at that exact rung; 3+ such sources (books and exchanges, no pick'em apps)
  - FanDuel price <= +300; market EV (median multiplicative devig) >= +4%; CrazyNinja median worst-case EV > 0
  - one rung per player (the best EV), ¼K on the market fair probability, capped 0.5u per bet
Tags / ledger strategies:
  alt_mkt   "alt, market only"          PTS, REB, AST, P+A, PRA, R+A (BettingPros projections there are only leans)
  alt_proj  "alt, market + projection"  3PM, P+R: the BettingPros projection must also be on the bet side of the rung
The FanDuel main line itself is never an alt rung (main lines are the BET record's job).
"""
from nbaprops import config as CFG  # thresholds, caps and settings
import statistics

from nbaprops.sources import crazyninja as C
from nbaprops.rules import ledger as L
from nbaprops.pricing import projctx as P
from nbaprops.sources import unabated as U
from nbaprops.sources.rotowire import norm
from nbaprops.pricing import devig as _odds  # noqa: E402 -- shared odds math

MIN_EV, MIN_SOURCES, MAX_ODDS, CAP = CFG.ALT_MIN_EV, CFG.ALT_MIN_SOURCES, CFG.ALT_MAX_ODDS, CFG.ALT_CAP  # nbaprops/config.py
PROJ_MARKETS = {"3PM", "P+R"}
KEYS = {"alt_mkt": "alt, market only", "alt_proj": "alt, market + projection"}


def _dec(a):
    return _odds.american_to_decimal(a)


def ladders(data, d, now):
    """{(event name, tip, player, market): {source id: {points: {"over": price, "under": price}}}} plus FanDuel's
    main line per key, from main lines, their alternateLines (exchanges) and Milestone-Alt groups (books)."""
    people = {str(x["id"]): x for x in U._items((d.get("raw") or {}).get("people"))}
    bt = {str(b["id"]): t for t, v in d.get("bettypes", {}).items() for b in v}
    fd = str(d.get("fanduel_source_id"))
    full = {str(i) for i in d.get("full_game_period_ids") or [1]}
    lad, mains = {}, {}
    for ctx, side in U.walk_sides((data or {}).get("odds") if isinstance(data, dict) else data):
        if ctx.get("isLive") or str(ctx.get("periodTypeId")) not in full:
            continue
        if U._closed(ctx, now):
            continue
        tag, who = bt.get(str(ctx.get("betTypeId"))), people.get(str(ctx.get("personId")))
        si = str(ctx.get("sideIndex"))
        if not tag or not who or si not in ("0", "1"):
            continue
        name = U._name(who)
        key = (ctx.get("eventName"), ctx.get("eventStart"), name, tag)
        sd = "over" if si == "0" else "under"
        for ln in U._lines(side.get("marketSourceLines")):
            src = str(ln.get("marketSourceId"))
            rungs = list(ln.get("alternateLines") or [])
            if ln.get("points") is not None and not ctx.get("betSubType"):
                rungs.append(ln)
                if src == fd:
                    mains[key] = float(ln["points"])
            for r in rungs:
                if r.get("disabled") or r.get("statusId") not in (None, 1) or r.get("price") is None or r.get("points") is None:
                    continue
                lad.setdefault(key, {}).setdefault(src, {}).setdefault(float(r["points"]), {})[sd] = r["price"]
    return lad, mains


def scan(data, d, date, now):
    """Qualifying alt rungs, one per player: [{player, market, side, line, odds, fair, ev, cno, books, tip, event, key}]."""
    names = {str(s["id"]): s["name"] for s in d.get("sources", [])}
    fd = str(d.get("fanduel_source_id"))
    ok = {i for i, n in names.items() if n in U.BOOK_SOURCES or n in U.EXCHANGE_SOURCES} - {fd}
    lad, mains = ladders(data, d, now)
    best = {}
    for (ev_name, tip, player, mk), srcs in lad.items():
        main = mains.get((ev_name, tip, player, mk))
        for pts, fdp in (srcs.get(fd) or {}).items():
            if main is not None and pts == main:
                continue
            for sd in ("over", "under"):
                price = fdp.get(sd)
                if price is None or price > MAX_ODDS:
                    continue
                two = [(names.get(s, s), r[pts]["over"], r[pts]["under"]) for s, r in srcs.items()
                       if s in ok and pts in r and "over" in r[pts] and "under" in r[pts]]
                if len(two) < MIN_SOURCES:
                    continue
                fairs = []
                for _, o, u in two:
                    po, pu = 1 / _dec(o), 1 / _dec(u)
                    f = po / (po + pu)
                    fairs.append(f if sd == "over" else 1 - f)
                fair = statistics.median(fairs)
                ev = fair * _dec(price) - 1
                if ev < MIN_EV:
                    continue
                cand = {"player": player, "market": mk, "side": sd, "line": pts, "odds": price, "fair": round(fair, 4),
                        "ev": round(ev, 4), "books": len(two), "two_way": two, "tip": tip, "event": ev_name, "main": main}
                b = best.get(player)
                if b is None or cand["ev"] > b["ev"]:
                    best[player] = cand
    out = []
    for c in sorted(best.values(), key=lambda c: -c["ev"]):
        cno = C.check(c["side"], c["two_way"], c["odds"])
        if cno is None or cno["median_ev"] <= 0:
            continue
        c["cno"] = cno
        if c["market"] in PROJ_MARKETS:
            pc = P.context(date, c["player"], c["market"], c["side"], c["line"])
            if pc["tag"] != "agrees":
                continue                                  # market + projection: the calibrated projection must agree
            c["key"] = "alt_proj"
        else:
            c["key"] = "alt_mkt"
            pc = P.context(date, c["player"], c["market"], c["side"], c["line"])
        c["proj_note"], c["proj_raw"], c["proj_cal"] = pc["text"], pc.get("proj"), pc.get("proj_cal", pc.get("proj"))
        dv = P.dvp_of(date, c["player"], c["market"])
        c["dvp"], c["dvp_tag"] = dv, P.dvp_effect(c["side"], dv) or "unknown"
        c["dvp_dot"] = P.dvp_dot(c["side"], dv) or "–"
        out.append(c)
    # qualification is unchanged; among qualifying rungs, a matchup that helps the side ranks first
    order = {"helps": 0, "neutral": 1, "unknown": 1, "hurts": 2}
    return sorted(out, key=lambda c: (order[c["dvp_tag"]], -c["ev"]))


def log(date, cands, now):
    """New alt rungs as ledger rows (never re-logged for the day). ¼K on the market fair, capped 0.5u."""
    from nbaprops.app import value as V
    led = L._read(L.LEDGER)
    have = {(r["player"], r["market"], r["side"], str(V._f(r.get("line")))) for r in led
            if r["date"] == date and r["strategy"] in KEYS}
    players_done = {r["player"] for r in led if r["date"] == date and r["strategy"] in KEYS}
    rows = []
    for c in cands:
        if (c["player"], c["market"], c["side"], str(float(c["line"]))) in have or c["player"] in players_done:
            continue
        stake = min(V.bet_kelly(c["fair"], c["odds"], CAP), CAP)
        if stake <= 0:
            continue
        players_done.add(c["player"])
        tip = str(c["tip"] or "")
        tip = tip if not tip or tip.endswith("Z") else tip[:19] + "Z"
        rows.append({"date": date, "pulled_at": now, "tip": tip,
                     "strategy": c["key"], "player": c["player"], "market": c["market"], "side": c["side"], "line": c["line"],
                     "odds": c["odds"], "our_prob": c["fair"], "nv_prob": c["fair"], "kelly_u": stake, "oa_fair": c["fair"],
                     "oa_ev": c["ev"], "fair_src": "Unabated alt rung", "sources": c["books"], "cno_ev": c["cno"]["median_ev"],
                     "cno_min_ev": c["cno"]["min_ev"], "proj_note": c.get("proj_note"),
                     "bp_proj_raw": c.get("proj_raw"), "bp_proj_cal": c.get("proj_cal"), "dvp_tag": c.get("dvp_tag"),
                     "dvp": c.get("dvp"),
                     "cap_note": f"alt (FanDuel main {c['main']:g})" if c.get("main") is not None else "alt"})
    if rows:
        L._write(L.LEDGER, led + rows, L.LEDGER_FIELDS)
    return rows


def close(date, data, d, now, close_before=None):
    """Closing fair price and CLV for logged alt rungs whose game is still open: every scan overwrites, so the last
    pregame price before the start is the close."""
    from nbaprops.app import value as V
    from nbaprops.rules import tipoff as T
    led = L._read(L.LEDGER)
    rows = [r for r in led if r["date"] == date and r["strategy"] in KEYS and r.get("tip") and T.is_open(r["tip"], None, now)]
    if not rows:
        return 0
    names = {str(s["id"]): s["name"] for s in d.get("sources", [])}
    fd = str(d.get("fanduel_source_id"))
    ok = {i for i, n in names.items() if n in U.BOOK_SOURCES or n in U.EXCHANGE_SOURCES} - {fd}
    lad, _ = ladders(data, d, now)
    n = 0
    for r in rows:
        pts = float(r["line"])
        srcs = next((s for (e, t, p, m), s in lad.items() if norm(p) == norm(r["player"]) and m == r["market"]), None)
        if not srcs:
            continue
        fairs = []
        for s, lv in srcs.items():
            if s in ok and pts in lv and "over" in lv[pts] and "under" in lv[pts]:
                po, pu = 1 / _dec(lv[pts]["over"]), 1 / _dec(lv[pts]["under"])
                fairs.append(po / (po + pu) if r["side"] == "over" else pu / (po + pu))
        if len(fairs) < MIN_SOURCES:
            continue
        f = statistics.median(fairs)
        r.update(close_fair=round(f, 4), clv_ev=round(f * V.decimal(float(r["odds"])) - 1, 4), close_at=now)
        n += 1
    L._write(L.LEDGER, led, L.LEDGER_FIELDS)
    return n
