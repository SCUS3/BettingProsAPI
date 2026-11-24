"""Triple green (v2 record, staked): a prop qualifies when ALL three hold at the same sighting:
1. +EV: CrazyNinja screener EV > 0 (any amount) OR Unabated market EV > 0 with 3+ sources;
2. BettingPros agrees: its projection or lean is on the bet side (🟢);
3. DvP helps the side (side-aware 🟢: rank 1-10 for an over, 21-30 for an under).
Missing DvP or a missing BettingPros projection = doesn't qualify. ¼K on the fair probability of the source that qualified
it (the higher EV if both did), BET cap and exposure caps (1.5u/player, 3.5u/game within the record). Logged once per day
at its first qualifying sighting (that sighting's price and EV are the frozen entry); started games are never touched.
"""
from __future__ import annotations

from nbaprops.rules import ledger as L

KEY = "triple"
AGREE = ("agrees", "lean agrees")


def candidates(date: str, cno: list[dict], ua: list[dict]) -> list[dict]:
    """Qualifying props from the day's CNO screener store rows and Unabated store rows (open games only, caller filters)."""
    from nbaprops.pricing import projctx as P
    k = lambda e: (e["player"], e["market"], e["side"], float(e["line"]))
    by: dict = {}
    for e in cno:
        if (e.get("ev") or 0) > 0 and e.get("odds") is not None and e.get("fair"):
            by.setdefault(k(e), {})["cno"] = e
    for e in ua:
        if (e.get("ev") or 0) > 0 and (e.get("books") or 0) >= 3 and e.get("price") is not None and e.get("fair"):
            by.setdefault(k(e), {})["ua"] = e
    out = []
    for (player, market, side, line), s in by.items():
        c = P.context(date, player, market, side, line)
        if c["tag"] not in AGREE:
            continue
        dvp = P.dvp_of(date, player, market)
        if P.dvp_effect(side, dvp) != "helps":
            continue
        cn, un = s.get("cno"), s.get("ua")
        src = "both" if cn and un else "CNO" if cn else "Unabated"
        pick = max([x for x in (cn and ("CNO", cn["ev"], cn["fair"], cn["odds"], cn.get("books")),
                                un and ("Unabated", un["ev"], un["fair"], un["price"], un.get("books"))) if x], key=lambda t: t[1])
        base = cn or un
        out.append({"player": player, "market": market, "side": side, "line": line, "src": src, "from": pick[0], "ev": pick[1],
                    "fair": pick[2], "odds": pick[3], "books": pick[4], "cno_ev": cn and cn["ev"], "ua_ev": un and un["ev"],
                    "tip": base.get("tip") or (un or {}).get("tip"), "team": (un or {}).get("team"), "event": base.get("event"),
                    "proj_tag": c["tag"], "proj_note": c["text"], "proj_raw": c["proj"], "dvp": dvp})
    return sorted(out, key=lambda x: -x["ev"])


def log(date: str, cno: list[dict], ua: list[dict]) -> list[dict]:
    """Log newly qualifying Triple green props (once per day per player|market|side|line). -> rows logged."""
    if date < L.V2_START:
        return []
    from nbaprops.app import value as V
    led = L._read(L.LEDGER)
    have = {(r["player"], r["market"], r["side"], str(L._num(r.get("line")))) for r in led if r["date"] == date and r["strategy"] == KEY}
    new = [x for x in candidates(date, cno, ua) if (x["player"], x["market"], x["side"], str(float(x["line"]))) not in have]
    if not new:
        return []
    logged = [r for r in led if r["date"] == date and r["strategy"] == KEY]
    gkey = V.game_resolver(new + logged)
    cap = V.bet_cap()[0]
    cc = V.apply_exposure([{"player": x["player"], "game": gkey(x), "ev": x["ev"], "stake": V.bet_kelly(x["fair"], x["odds"], cap)}
                           for x in new], logged, gkey)
    now, out = L.utc_now(), []
    for x, c in zip(new, cc):
        if c["stake"] <= 0:
            continue
        out.append({"date": date, "pulled_at": now, "tip": x.get("tip") or "", "strategy": KEY, "player": x["player"],
                    "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "odds": x["odds"],
                    "our_prob": x["fair"], "nv_prob": x["fair"], "oa_fair": x["fair"], "oa_ev": x["ev"], "sources": x["books"],
                    "fair_src": x["src"], "kelly_u": c["stake"], "cap_note": c["cap"], "proj_tag": x["proj_tag"],
                    "proj_note": x["proj_note"], "bp_proj_raw": x["proj_raw"], "dvp": x["dvp"], "dvp_tag": "helps",
                    "event_id": ""})
    if out:
        L._write(L.LEDGER, led + out, L.LEDGER_FIELDS)
    return out
