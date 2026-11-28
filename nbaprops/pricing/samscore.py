"""Sam score (v2.1): 0-100 display and ranking score for staked plays. It never qualifies a -EV play and never changes a
stake; it orders Tonight's bets, keeps scores under 50 off it (paper only), and decides what the nightly cap trims first.

Each part is a 0-1 fraction times its weight (config.SAM_WEIGHTS), plus fixed penalties, clipped to 0-100:
  edge       entry EV / 6% (6%+ = full)
  quality    8+ sources .3 · exchange in the market .2 · CrazyNinja worst case > 0 .3 · Unabated and CNO both +EV .2
  bp         3PM/P+R: agrees, |gap| / SD (1 SD = full; SD = 1.1 sqrt(max(proj, line))) · leans: agrees .4 · disagrees 0
  dvp        side-aware: 1-5 / 26-30 helping = 1 · 6-10 / 21-25 helping = .6 · 11-20 = .3 · hurts = 0
  move       line/price since first seen: toward the side 1 · none .5 · against 0
  role       projected starter .5 · rotation teammate ruled out (overs) .5
  form       side hit rate >= 60% = 1 · 50-59% = .6 · < 50% = 0
  consensus  2+ CrazyNinja pastes .4 · proven Reddit poster on the same side .6
  threes     1 for the 3PM market (weight 0: exists for the fit report)
Missing data is neutral (config.SAM_NEUTRAL); 2+ missing parts = "partial data".
Penalties: FanDuel longer than +200, player DTD, outlier EV, unverified both-sides.
"""
from __future__ import annotations

import csv
import json
import math
import os

from nbaprops import config as C
from nbaprops.sources.rotowire import norm

PARTS = list(C.SAM_WEIGHTS)
REAL = {"3PM", "P+R"}
_CSV: dict = {}


def label(score, tier_=None) -> str:
    """Display form of a Sam score: '51 (C)', '44.4 (paper)'. '' if no score."""
    try:
        sc = float(score)
    except (TypeError, ValueError):
        return ""
    return f"{sc:g} ({tier_ or tier(sc)})"


def label_row(row) -> str:
    """' | Sam 51 (C)' for an alert line, from a row's frozen score; '' if none."""
    lab = label(row.get("score_entry"), row.get("score_tier") or None)
    return f" | Sam {lab}" if lab else ""


def tier(score):
    if score is None:
        return ""
    return next((t for lo, t in C.SAM_TIERS if score >= lo), "paper")


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _implied(a):
    a = _num(a)
    if a is None:
        return None
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def board_row(date, player, market):
    """The day's BettingPros pull row for player/market (cached per date), {} if none."""
    from nbaprops.rules import ledger as L
    p = os.path.join(L.HERE, f"bp_props_{date}.csv")
    key = (p, os.path.getmtime(p)) if os.path.exists(p) else (p, 0)
    if key not in _CSV:
        _CSV.clear()
        rows = list(csv.DictReader(open(p))) if os.path.exists(p) else []
        _CSV[key] = {(norm(r["player"]), r["market"]): r for r in rows}
    return _CSV[key].get((norm(player), market)) or {}


def stores(date):
    """(Unabated EV store, CrazyNinja screener store) for the date."""
    from nbaprops.app import value as V
    from nbaprops.rules import screener as S
    try:
        ua = json.load(open(V.ua_store_path(date)))
    except (OSError, ValueError):
        ua = {}
    return ua, S.store(date)


def movement(side, first, cur):
    """'toward' / 'none' / 'against' from first sighting (line, price) to now (line, price)."""
    if not first or not cur or first[0] is None or cur[0] is None:
        return "none"
    if cur[0] != first[0]:
        up = cur[0] > first[0]
        return "toward" if (up if side == "over" else not up) else "against"
    a, b = _implied(first[1]), _implied(cur[1])
    if a is None or b is None or abs(b - a) < 0.01:
        return "none"
    return "toward" if b > a else "against"


def first_sighting(ua, cno, player, market, side):
    """Earliest (time, line, price) this player/market/side was seen in either store, or None."""
    seen = []
    for e in list(ua.values()) + list(cno.values()):
        if norm(e.get("player", "")) == norm(player) and e.get("market") == market and e.get("side") == side:
            t = e.get("entry_at") or e.get("first_seen")
            if t:
                seen.append((t, _num(e.get("line")), _num(e.get("entry_price"))))
    return min(seen) if seen else None


_PROVEN: dict = {}


def _proven_today(date, RP):
    """{(player, market, side)} picked today by proven Reddit posters (cached on the ledger's mtime)."""
    from nbaprops.rules import ledger as L
    try:
        key = (date, os.path.getmtime(L.LEDGER))
    except OSError:
        return set()
    if key not in _PROVEN:
        try:
            pp = RP.proven_posters()
            _PROVEN[key] = {(norm(r["player"]), r["market"], r["side"]) for r in L._read(L.LEDGER)
                            if r["date"] == date and r["strategy"] == RP.KEY and r.get("fair_src") in pp} if pp else set()
        except Exception:  # noqa: BLE001
            _PROVEN[key] = set()
    return _PROVEN[key]


def features(row, *, latest=False, unverified=False, ctx=None) -> dict:
    """Parts (0-1), missing parts, penalties and the movement label for a ledger-style row."""
    from nbaprops.pricing import projctx as P
    from nbaprops.rules import reddit_picks as RP
    date, player, market, side = row["date"], row["player"], row["market"], row["side"]
    line = _num(row.get("latest_fd_line") if latest and row.get("latest_fd_line") else row.get("line"))
    price = _num(row.get("latest_price") if latest and row.get("latest_price") else (row.get("entry_price") or row.get("odds")))
    ev = _num(row.get("entry_ev") or row.get("oa_ev")) or 0.0
    sources = int(_num(row.get("entry_sources") or row.get("sources")) or 0)
    ua, cno = ctx if ctx is not None else stores(date)
    lk = f"{player}|{market}|{side}|{float(row['line']):g}"
    ue, ce = ua.get(lk) or {}, cno.get(lk) or {}
    br = board_row(date, player, market)
    f, missing = {}, []
    f["edge"] = max(0.0, min(ev / C.SAM_EDGE_FULL, 1.0))
    cno_ev = _num(row.get("cno_ev"))
    f["quality"] = (0.3 if sources >= 8 else 0) + (0.2 if ue.get("exchange") else 0) + (0.3 if cno_ev is not None and cno_ev > 0 else 0) \
        + (0.2 if (ue.get("ev") or 0) > 0 and (ce.get("ev") or 0) > 0 else 0)
    try:
        c = P.context(date, player, market, side, row["line"])
    except Exception:  # noqa: BLE001
        c = {"tag": "none", "proj": None}
    tag, proj = c.get("tag") or "none", _num(c.get("proj"))
    if tag in ("agrees", "lean agrees"):
        if market in REAL and proj is not None:
            sd = 1.1 * math.sqrt(max(proj, float(row["line"]), 0.5))
            f["bp"] = min(abs(proj - float(row["line"])) / sd, 1.0)
        else:
            f["bp"] = 0.4
    elif tag in ("disagrees", "lean disagrees"):
        f["bp"] = 0.0
    else:
        f["bp"] = C.SAM_NEUTRAL["bp"]
        missing.append("bp")
    d = _num(br.get("dvp_softness")) if br else None
    if d is None:
        d = _num(row.get("dvp"))
    eff = P.dvp_effect(side, d) if d is not None else None
    if eff is None:
        f["dvp"] = C.SAM_NEUTRAL["dvp"]
        missing.append("dvp")
    elif eff == "hurts":
        f["dvp"] = 0.0
    elif eff == "neutral":
        f["dvp"] = 0.3
    else:
        f["dvp"] = 1.0 if (d <= 5 or d >= 26) else 0.6
    fs = first_sighting(ua, cno, player, market, side)
    mv = movement(side, (fs[1], fs[2]) if fs else None, (line, price))
    f["move"] = {"toward": 1.0, "none": 0.5, "against": 0.0}[mv]
    if not br or (br.get("lineup") in (None, "") and br.get("starter") in (None, "")):
        f["role"] = C.SAM_NEUTRAL["role"]
        missing.append("role")
    else:
        f["role"] = (0.5 if br.get("starter") == "True" else 0) + (0.5 if side == "over" and (_num(br.get("tm_out")) or 0) >= 1 else 0)
    rate = _num(br.get("form_rate")) if br else None
    if rate is None:
        f["form"] = C.SAM_NEUTRAL["form"]
        missing.append("form")
    else:
        rate = rate if (br.get("rec_side") or "").lower() == side else 1 - rate
        f["form"] = 1.0 if rate >= 0.6 else 0.6 if rate >= 0.5 else 0.0
    proven = (norm(player), market, side) in _proven_today(date, RP)
    f["consensus"] = (0.4 if (ce.get("seen") or 0) >= 2 else 0) + (0.6 if proven else 0)
    f["threes"] = 1.0 if market == "3PM" else 0.0
    pen = []
    if price is not None and price > C.SAM_LONG_ODDS:
        pen.append("long_odds")
    if (row.get("injury") or (br or {}).get("injury")) == "DTD":
        pen.append("dtd")
    if ev > C.OUTLIER_EV and sources < C.OUTLIER_MIN_SOURCES:
        pen.append("outlier")
    if unverified:
        pen.append("unverified")
    return {"parts": {k: round(v, 3) for k, v in f.items()}, "missing": missing, "penalties": pen, "move": mv}


def total(feat, weights=None) -> float:
    w = weights or C.SAM_WEIGHTS
    s = sum(w[k] * feat["parts"].get(k, 0) for k in w) + sum(C.SAM_PENALTIES[p] for p in feat["penalties"])
    return round(max(0.0, min(100.0, s)), 1)


def points(feat, weights=None):
    """Part -> points (fraction x weight), for display and the record splits."""
    w = weights or C.SAM_WEIGHTS
    return {k: round(w[k] * feat["parts"].get(k, 0), 1) for k in w}


def score(row, **kw) -> dict:
    """{score, tier, parts (points), penalties, flags} for a row."""
    feat = features(row, **kw)
    s = total(feat, active_weights())
    flags = (["partial data"] if len(feat["missing"]) >= 2 else []) + feat["penalties"]
    return {"score": s, "tier": tier(s), "parts": points(feat, active_weights()), "fractions": feat["parts"],
            "penalties": feat["penalties"], "flags": flags, "move": feat["move"]}


def active_weights():
    """Hand-set weights unless a fitted set was switched on by the weekly check (sam_weights.json, active)."""
    from nbaprops.rules import ledger as L
    try:
        j = json.load(open(os.path.join(L.HERE, "sam_weights.json")))
        if j.get("active") and j.get("weights"):
            return dict(C.SAM_WEIGHTS, **j["weights"])
    except (OSError, ValueError):
        pass
    return C.SAM_WEIGHTS


def freeze(row) -> None:
    """Set score_entry / score_tier / score_parts / score_flags on a row at its first write (logging time)."""
    try:
        s = score(row)
    except Exception:  # noqa: BLE001
        return
    row.update(score_entry=s["score"], score_tier=s["tier"], score_parts=json.dumps(s["parts"], separators=(",", ":")),
               score_flags=", ".join(s["flags"]),
               score_fracs=json.dumps({k: round(v, 4) for k, v in s["fractions"].items()}, separators=(",", ":")))


def weights_on(date):
    """Sam weights in force when a row dated `date` was scored: the last nightly snapshot before that date, else hand-set."""
    from nbaprops.rules import learns as LR
    ws = [h for h in LR.history() if h["date"] < date and h.get("weights")]
    return dict(C.SAM_WEIGHTS, **ws[-1]["weights"]) if ws else dict(C.SAM_WEIGHTS)


def learned(row, weights=None):
    """The row's Sam score re-computed with the current learned weights (same frozen inputs, same penalties):
    fractions from score_fracs, or from score_parts / the weights in force on its date. None if never scored."""
    w = weights or active_weights()
    try:
        fr = json.loads(row.get("score_fracs") or "null")
        if fr is None:
            parts, then = json.loads(row.get("score_parts") or "null"), weights_on(row["date"])
            if parts is None:
                return None
            fr = {k: (float(parts.get(k, 0)) / then[k] if then.get(k) else 0.0) for k in then}
    except (ValueError, TypeError):
        return None
    fl = row.get("score_flags") or ""
    s = sum(w[k] * float(fr.get(k, 0)) for k in w) + sum(v for p, v in C.SAM_PENALTIES.items() if p in fl)
    return round(max(0.0, min(100.0, s)), 1)


def graded_n(rows=None) -> int:
    """Graded props with a frozen Sam score (each prop once) -- the sample the v2.2 'under 50' gate waits for."""
    from nbaprops.rules import ledger as L
    rows = L._read(L.LEDGER) if rows is None else rows
    return len({(r["date"], r["player"], r["market"], r["side"], r["line"]) for r in rows
                if r.get("strategy") in L.SAM_KEYS and r.get("score_entry") not in (None, "") and r.get("result") in ("W", "L", "P")})


def rescore_summary(rows=None):
    """Every scored prop (each prop once, its best entry score) re-scored with the current weights:
    {n, changed (tier), crossed (the 50 line), up, down, avg}."""
    from nbaprops.rules import ledger as L
    if rows is None:
        rows = L._read(L.LEDGER)
    best = {}
    for r in rows:
        if r.get("strategy") not in L.SAM_KEYS or r.get("score_entry") in (None, ""):
            continue
        k = (r["date"], r["player"], r["market"], r["side"], r["line"])
        if k not in best or float(r["score_entry"]) > float(best[k]["score_entry"]):
            best[k] = r
    pairs = [(float(r["score_entry"]), learned(r)) for r in best.values()]
    pairs = [(a, b) for a, b in pairs if b is not None]
    if not pairs:
        return {"n": 0}
    return {"n": len(pairs), "changed": sum(tier(a) != tier(b) for a, b in pairs),
            "crossed": sum((a >= 50) != (b >= 50) for a, b in pairs), "up": sum((a < 50) and (b >= 50) for a, b in pairs),
            "down": sum((a >= 50) and (b < 50) for a, b in pairs), "avg": round(sum(b - a for a, b in pairs) / len(pairs), 1)}
