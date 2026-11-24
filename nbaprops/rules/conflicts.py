"""Opposite-side conflicts in Tonight's bets.

both sides: the same player/market/line is +EV on the over AND the under (e.g. Jaylon Tyson PTS o9.5 from the alt ladder
  at 00:35 and u9.5 from a BET at 00:55 on 11/23: two FanDuel markets, two scans 20 minutes apart, two source sets, and
  nothing compared one record's plays with another's). Resolution, logged per prop in history/<date>/conflicts_<date>.json:
  - re-price BOTH sides from one fresh snapshot (the Unabated board the board run / watch scan just fetched: same source
    set, same timestamp, FanDuel's current over and under at that line);
  - exactly one side +EV -> keep it, "re-checked"; neither +EV -> drop both, "re-checked: neither +EV";
  - both still +EV, or the re-price fails (FanDuel or < 3 sources missing a side) -> drop both, "data error";
  - never re-priced and the game is about to tip -> keep the side with the stronger signals (higher EV, BettingPros
    agrees, DvP helps) at HALF its stake, "unverified" (decided from the ledger, never written after tip).
contradictory: same player/market, an over at a HIGHER line than an under (Champagnie REB u5.5 and o7.5: both can't win).
  Flagged only.
"""
from __future__ import annotations

import json
import os
import statistics

from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm


def path(date):
    return os.path.join(L.HERE, "history", date, f"conflicts_{date}.json")


def load(date):
    try:
        return json.load(open(path(date)))
    except (OSError, ValueError):
        return {}


def save(date, st):
    os.makedirs(os.path.dirname(path(date)), exist_ok=True)
    json.dump(st, open(path(date), "w"), indent=1)


def key(player, market, line):
    return f"{player}|{market}|{float(line):g}"


def find(items):
    """items: [{player, market, side, line, ...}] (one per prop). -> (both-sides pairs [(over, under)], contradictory pairs)."""
    by: dict = {}
    for x in items:
        by.setdefault((norm(x["player"]), x["market"]), []).append(x)
    both, contra = [], []
    for g in by.values():
        overs = [x for x in g if x["side"] == "over"]
        unders = [x for x in g if x["side"] == "under"]
        for o in overs:
            for u in unders:
                if float(o["line"]) == float(u["line"]):
                    both.append((o, u))
                elif float(o["line"]) > float(u["line"]):
                    contra.append((o, u))
    return both, contra


def reprice_snapshot(data, d, now, player, market, line):
    """Both sides of player/market/line from ONE Unabated board snapshot: FanDuel's over and under at that line and the
    median two-way devig of 3+ other sources pricing both sides there. -> {over: {...}, under: {...}} or None."""
    from nbaprops.pricing import alts as A
    from nbaprops.pricing import devig as D
    from nbaprops.sources import unabated as U
    names = {str(s["id"]): s["name"] for s in d.get("sources", [])}
    fd = str(d.get("fanduel_source_id"))
    ok = {i for i, n in names.items() if n in U.BOOK_SOURCES or n in U.EXCHANGE_SOURCES} - {fd}
    lad, _ = A.ladders(data, d, now)
    line = float(line)
    for (_ev, _tip, name, mk), srcs in lad.items():
        if mk != market or norm(name) != norm(player):
            continue
        fdp = (srcs.get(fd) or {}).get(line) or {}
        two = [(r[line]["over"], r[line]["under"]) for s, r in srcs.items() if s in ok and line in r
               and "over" in r[line] and "under" in r[line]]
        if "over" not in fdp or "under" not in fdp or len(two) < U.MIN_SOURCES:
            return None
        f_over = statistics.median([(1 / D.american_to_decimal(o)) / (1 / D.american_to_decimal(o) + 1 / D.american_to_decimal(u))
                                    for o, u in two])
        out = {}
        for sd, f in (("over", f_over), ("under", 1 - f_over)):
            out[sd] = {"price": fdp[sd], "fair": round(f, 4), "ev": round(f * D.american_to_decimal(fdp[sd]) - 1, 4),
                       "books": len(two), "at": now}
        return out
    return None


def reprice(date, data, d, now, items=None):
    """Resolve every unresolved both-sides conflict in today's betting list from this snapshot (board run / watch scan).
    Open games only. -> [(key, case)] newly decided."""
    from nbaprops.rules import tipoff as T
    from nbaprops.rules import tonight as TN
    items = TN.candidates(date) if items is None else items
    both, _ = find(items)
    st, done = load(date), []
    for o, u in both:
        k = key(o["player"], o["market"], o["line"])
        if st.get(k, {}).get("case") or (o.get("tip") and not T.is_open(o["tip"])):
            continue
        snap = reprice_snapshot(data, d, now, o["player"], o["market"], o["line"])
        logged = {"over": {"ev": o.get("ev"), "price": o.get("price"), "from": o.get("from"), "at": o.get("entry_at")},
                  "under": {"ev": u.get("ev"), "price": u.get("price"), "from": u.get("from"), "at": u.get("entry_at")}}
        if snap is None:
            case, kept, cause = "data error", None, "re-price failed: FanDuel or 3+ sources missing a side at this line now"
        else:
            pos = [sd for sd in ("over", "under") if snap[sd]["ev"] > 0]
            if len(pos) == 2:
                case, kept, cause = "data error", None, "both sides still +EV in one snapshot (source mismatch at this line)"
            elif len(pos) == 1:
                case, kept, cause = "re-checked", pos[0], "one side +EV in one snapshot; the other side's logged price was stale"
            else:
                case, kept, cause = "re-checked", None, "neither side +EV in one snapshot; both logged prices were stale"
        st[k] = {"type": "both sides", "case": case, "kept_side": kept, "at": now, "cause": cause, "logged": logged,
                 "snapshot": snap}
        done.append((k, case))
    if done:
        save(date, st)
    return done
