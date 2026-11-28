"""Steam (v2.2 research record, from 11/28): a pregame prop that is +EV now AND whose fair probability has moved toward
its side by STEAM_MIN (1 point) or more since its first sighting that day -- the market is moving your way. Checked on
every CNO paste, Unabated board run and watch scan from the day's CrazyNinja and Unabated stores (open games only).
Logged once per day per prop at the sighting where it first qualifies (that price and EV frozen), flat 1u, no stake:
it proves itself before it ever feeds Tonight's bets.
"""
from __future__ import annotations

from nbaprops import config as C
from nbaprops.rules import ledger as L

KEY = "steam"
START = "2026-11-28"


def candidates(cno: list[dict], ua: list[dict]) -> list[dict]:
    out = []
    for src, rows, price_k in (("CNO", cno, "odds"), ("Unabated", ua, "price")):
        for e in rows:
            f0, f1, ev = L._num(e.get("entry_fair")), L._num(e.get("fair")), L._num(e.get("ev"))
            if None in (f0, f1, ev) or e.get(price_k) is None or ev <= 0:
                continue
            if src == "Unabated" and (e.get("books") or 0) < 3:
                continue
            if f1 - f0 >= C.STEAM_MIN:
                out.append(dict(e, src=src, odds=e[price_k], move=round(f1 - f0, 4)))
    best = {}
    for x in sorted(out, key=lambda x: -x["move"]):            # one row per prop: the bigger move wins
        best.setdefault((x["player"], x["market"], x["side"], float(x["line"])), x)
    return list(best.values())


def log(date: str, cno: list[dict], ua: list[dict]) -> list[dict]:
    """Log newly steaming props once per day (research, flat 1u). -> rows logged."""
    if date < START:
        return []
    from nbaprops.pricing import projctx as P
    led = L._read(L.LEDGER)
    have = {(r["player"], r["market"], r["side"], str(L._num(r.get("line")))) for r in led if r["date"] == date and r["strategy"] == KEY}
    now, out = L.utc_now(), []
    for x in candidates(cno, ua):
        if (x["player"], x["market"], x["side"], str(float(x["line"]))) in have:
            continue
        c = P.context(date, x["player"], x["market"], x["side"], float(x["line"]))
        dvp = P.dvp_of(date, x["player"], x["market"])
        out.append({"date": date, "pulled_at": now, "tip": x.get("tip") or "", "strategy": KEY, "player": x["player"],
                    "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "odds": x["odds"],
                    "our_prob": x["fair"], "nv_prob": x["fair"], "oa_fair": x["fair"], "oa_ev": x["ev"], "sources": x.get("books"),
                    "fair_src": x["src"], "kelly_u": None,
                    "cap_note": f"fair {100 * L._num(x['entry_fair']):.1f}% -> {100 * L._num(x['fair']):.1f}% ({100 * x['move']:+.1f} pts)",
                    "proj_tag": c["tag"], "proj_note": c["text"], "bp_proj_raw": c.get("proj"), "dvp": dvp,
                    "dvp_tag": P.dvp_effect(x["side"], dvp) or "unknown", "event_id": ""})
    if out:
        L._write(L.LEDGER, led + out, L.LEDGER_FIELDS)
    return out
