"""2 of 3 (v2.1 research record): a prop qualifies when at least TWO of the three Triple green checks hold at the same
sighting -- +EV (CrazyNinja screener EV > 0, or Unabated EV > 0 with 3+ sources), BettingPros agrees (🟢), DvP helps the
side (🟢, side-aware). Flat 1u, no stake, never in Tonight's bets (a BP + DvP pair can be -EV). Logged once per day at its
first qualifying sighting, with the FanDuel price then; split by which checks held (EV+BP, EV+DvP, BP+DvP, all 3).
Candidates: every FanDuel side in the day's Unabated EV store and CrazyNinja screener store (open games only).
"""
from __future__ import annotations

from nbaprops.rules import ledger as L

KEY = "two_of_three"
AGREE = ("agrees", "lean agrees")


def candidates(date: str, cno: list[dict], ua: list[dict]) -> list[dict]:
    from nbaprops.pricing import projctx as P
    k = lambda e: (e["player"], e["market"], e["side"], float(e["line"]))
    by: dict = {}
    for e in ua:
        if e.get("price") is not None:
            by.setdefault(k(e), {})["ua"] = e
    for e in cno:
        if e.get("odds") is not None:
            by.setdefault(k(e), {})["cno"] = e
    out, dvp_cache = [], {}
    for (player, market, side, line), s in by.items():
        un, cn = s.get("ua"), s.get("cno")
        ev_ok = bool((cn and (cn.get("ev") or 0) > 0) or (un and (un.get("ev") or 0) > 0 and (un.get("books") or 0) >= 3))
        c = P.context(date, player, market, side, line)
        bp_ok = c["tag"] in AGREE
        if (player, market) not in dvp_cache:
            dvp_cache[(player, market)] = P.dvp_of(date, player, market)
        dvp = dvp_cache[(player, market)]
        dvp_ok = P.dvp_effect(side, dvp) == "helps"
        if ev_ok + bp_ok + dvp_ok < 2 or (date >= "2026-11-28" and not ev_ok):
            continue                                   # v2.2: a BP + DvP pair without +EV no longer counts
        which = "all 3" if ev_ok and bp_ok and dvp_ok else "+".join(n for n, ok in (("EV", ev_ok), ("BP", bp_ok), ("DvP", dvp_ok)) if ok)
        evs = [(cn["ev"], cn["odds"], cn.get("fair"), cn.get("books"), "CNO")] if cn else []
        evs += [(un["ev"], un["price"], un.get("fair"), un.get("books"), "Unabated")] if un else []
        ev, price, fair, books, src = max(evs, key=lambda t: t[0] if t[0] is not None else -9)
        base = un or cn
        out.append({"player": player, "market": market, "side": side, "line": line, "which": which, "ev": ev, "odds": price,
                    "fair": fair, "books": books, "src": src, "tip": base.get("tip") or (cn or {}).get("tip"),
                    "team": (un or {}).get("team"), "proj_tag": c["tag"], "proj_note": c["text"], "proj_raw": c.get("proj"),
                    "dvp": dvp, "dvp_tag": P.dvp_effect(side, dvp) or "unknown"})
    return out


def log(date: str, cno: list[dict], ua: list[dict]) -> list[dict]:
    """Log newly qualifying props once per day (research, flat 1u). -> rows logged."""
    if date < L.SAM_START:
        return []
    led = L._read(L.LEDGER)
    have = {(r["player"], r["market"], r["side"], str(L._num(r.get("line")))) for r in led if r["date"] == date and r["strategy"] == KEY}
    now, out = L.utc_now(), []
    for x in candidates(date, cno, ua):
        if (x["player"], x["market"], x["side"], str(float(x["line"]))) in have:
            continue
        out.append({"date": date, "pulled_at": now, "tip": x.get("tip") or "", "strategy": KEY, "player": x["player"],
                    "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "odds": x["odds"],
                    "our_prob": x["fair"], "nv_prob": x["fair"], "oa_fair": x["fair"], "oa_ev": x["ev"], "sources": x["books"],
                    "fair_src": x["which"], "kelly_u": None, "cap_note": f"price from {x['src']}", "proj_tag": x["proj_tag"],
                    "proj_note": x["proj_note"], "bp_proj_raw": x["proj_raw"], "dvp": x["dvp"], "dvp_tag": x["dvp_tag"],
                    "event_id": ""})
    if out:
        L._write(L.LEDGER, led + out, L.LEDGER_FIELDS)
    return out
