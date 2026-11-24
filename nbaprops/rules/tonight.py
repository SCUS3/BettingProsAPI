"""Tonight's bets: the only list meant to be bet. The union of BETs (Unabated EV >= +2% and CrazyNinja worst case > 0;
a BET logged while CNO was down is kept and flagged), alt BETs (alt ladder) and Your-rule props (CNO +EV and BettingPros
agrees). Each prop (player, market, side, line) is listed once with the LARGEST qualifying ¼K stake (never summed), then
the 1.5u-per-player and 3.5u-per-game caps are applied across the whole list. Every other record stays a paper record.
"""
from __future__ import annotations

from nbaprops import config as C
from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm

SOURCES = {"bet_agree": "BET", "bet_only": "BET", "alt_mkt": "Alt BET", "alt_proj": "Alt BET", "my_rule": "Your rule",
           "triple": "Triple green"}


def candidates(date: str, led: list[dict] | None = None) -> list[dict]:
    """One item per prop (player, market, side, line) from the betting records, at its largest qualifying ¼K stake."""
    from nbaprops.app import value as V
    rows = [r for r in (L._read(L.LEDGER) if led is None else led) if r["date"] == date and r["strategy"] in SOURCES
            and (L._num(r.get("kelly_u")) or 0) > 0]
    groups: dict = {}
    for r in rows:
        groups.setdefault((norm(r["player"]), r["market"], r["side"], L._num(r["line"])), []).append(r)
    key = V.game_resolver(rows)
    out = []
    for g in groups.values():
        best = max(g, key=lambda r: L._num(r["kelly_u"]))
        recs = sorted({SOURCES[r["strategy"]] for r in g}, key=["BET", "Alt BET", "Your rule", "Triple green"].index)
        ev = L._num(best.get("entry_ev") or best.get("oa_ev"))
        last = max([t for t in (best.get("entry_at") or best.get("pulled_at"), best.get("latest_at")) if t] or [""])
        out.append({"player": best["player"], "team": best.get("team"), "market": best["market"], "side": best["side"],
                    "line": L._num(best["line"]), "price": L._num(best.get("entry_price") or best.get("odds")), "ev": ev,
                    "sources": int(L._num(best.get("entry_sources") or best.get("sources")) or 0), "price_at": last,
                    "entry_at": best.get("entry_at") or best.get("pulled_at"),
                    "bp_agrees": (best.get("proj_tag") or "") in ("agrees", "lean agrees"), "dvp_tag": best.get("dvp_tag"),
                    "stake_raw": L._num(best["kelly_u"]), "stake": L._num(best["kelly_u"]), "records": recs,
                    "from": SOURCES[best["strategy"]], "tip": best.get("tip"), "event_id": best.get("event_id"),
                    "game": key(best), "flags": "no CNO check" if any(r.get("cno_flag") for r in g if r["strategy"] in ("bet_agree", "bet_only")) else "",
                    "held": "", "case": "", "rows": g})
    return out


def _now_eff(x, now):
    """Decision time for a prop: now, but never past its tip (so a past slate decides the same way it did pregame)."""
    from nbaprops.rules import tipoff as T
    return min(now, T._t(x["tip"])) if x.get("tip") else now


def outlier(x, now) -> bool:
    """Sanity gate: entry EV above OUTLIER_EV needs OUTLIER_MIN_SOURCES sources AND a price no older than
    OUTLIER_MAX_AGE_MIN at decision time."""
    from nbaprops.rules import tipoff as T
    if (x.get("ev") or 0) <= C.OUTLIER_EV:
        return False
    if (x.get("sources") or 0) < C.OUTLIER_MIN_SOURCES or not x.get("price_at"):
        return True
    return (_now_eff(x, now) - T._t(x["price_at"])).total_seconds() > 60 * C.OUTLIER_MAX_AGE_MIN


def _strength(x, other):
    return ((x.get("ev") or 0) > (other.get("ev") or 0)) + bool(x.get("bp_agrees")) + (x.get("dvp_tag") == "helps")


def build(date: str, led: list[dict] | None = None, now=None) -> list[dict]:
    """Every candidate prop, biggest stake first; held-out props keep stake 0 and a 'held' reason (outlier EV, both
    sides +EV, re-checked other side). Order: sanity gate -> both-sides resolution -> per-player/per-game caps ->
    nightly cap (NIGHT_CAP, proportional). Reads the ledger and the conflicts file only (no API calls)."""
    import datetime as dt
    from nbaprops.app import value as V
    from nbaprops.rules import conflicts as CF
    from nbaprops.rules import tipoff as T
    now = now or dt.datetime.now(dt.UTC)
    out = candidates(date, led)
    for x in out:
        if outlier(x, now):
            x["held"] = f"check: outlier EV ({100 * x['ev']:.1f}%, {x['sources']} sources)"
    both, contra = CF.find(out)
    st = CF.load(date)
    for o, u in both:
        e = st.get(CF.key(o["player"], o["market"], o["line"])) or {}
        case = e.get("case")
        if case == "re-checked":
            for x in (o, u):
                if x["side"] == e.get("kept_side"):
                    x["case"] = "re-checked"
                elif not x["held"]:
                    x["held"] = "both sides +EV: re-checked, this side not +EV"
        elif case == "data error":
            for x in (o, u):
                x["held"], x["case"] = "both sides +EV: data error (" + e.get("cause", "") + ")", "data error"
        else:
            near = all(x.get("tip") and _now_eff(x, now) >= T._t(x["tip"]) - dt.timedelta(minutes=C.UNVERIFIED_BEFORE_TIP_MIN)
                       for x in (o, u))
            if near:                                    # never re-priced before tip: stronger side at half stake
                keep, drop = (o, u) if (_strength(o, u), o.get("ev") or 0) >= (_strength(u, o), u.get("ev") or 0) else (u, o)
                keep["case"], keep["stake"] = "unverified", round(keep["stake"] / 2, 2)
                drop["held"] = drop["held"] or "both sides +EV: unverified, other side kept"
            else:
                for x in (o, u):
                    x["held"] = x["held"] or "both sides +EV: re-checking at the next scan"
    sam = date >= L.SAM_START
    if sam:                                             # v2.1: latest Sam score on every prop; under 50 = paper only
        from nbaprops.pricing import samscore as SS
        try:
            ctx = SS.stores(date)
        except Exception:  # noqa: BLE001
            ctx = ({}, {})
        for x in out:
            best = max(x["rows"], key=lambda r: L._num(r["kelly_u"]))
            try:
                s = SS.score(best, latest=True, unverified=x["case"] == "unverified", ctx=ctx)
            except Exception:  # noqa: BLE001
                s = None
            if s:
                x.update(score=s["score"], tier=s["tier"], score_parts=s["parts"], score_flags=s["flags"],
                         score_entry=L._num(best.get("score_entry")))
                if s["tier"] == "paper" and not x["held"]:
                    x["held"] = f"paper only: Sam score {s['score']:g} (under 50)"
    for o, u in contra:
        o["flags"] = ", ".join(f for f in (o["flags"], f"contradicts {u['side'][0]}{u['line']:g}") if f)
        u["flags"] = ", ".join(f for f in (u["flags"], f"contradicts {o['side'][0]}{o['line']:g}") if f)
    act = [x for x in out if not x["held"]]
    cands = [{"player": x["player"], "game": x["game"], "ev": x["ev"] or 0, "stake": x["stake"]} for x in act]
    V.apply_exposure(cands, [], lambda r: r["game"])
    for x, c in zip(act, cands):
        x["stake"], x["cap"] = round(c["stake"], 2), c["cap"]
    total = sum(x["stake"] for x in act)
    if total > C.NIGHT_CAP and sam:                    # v2.1: trim the lowest scores first (C before B before A)
        excess = round(total - C.NIGHT_CAP, 2)
        for x in sorted(act, key=lambda x: (x.get("score") or 0, -(x.get("ev") or 0))):
            if excess <= 0:
                break
            cut = min(x["stake"], excess)
            x["stake_capped"] = x["stake"]
            x["stake"] = round(x["stake"] - cut, 2)
            excess = round(excess - cut, 2)
            x["cap"] = ", ".join(c for c in (x.get("cap"), "trimmed: nightly cap") if c)
            if x["stake"] <= 0:
                x["held"] = "trimmed: nightly cap (lowest Sam score first)"
    elif total > C.NIGHT_CAP:
        f = C.NIGHT_CAP / total
        for x in act:
            x["stake_capped"] = x["stake"]
            x["stake"] = int(x["stake"] * f * 100) / 100
            x["cap"] = ", ".join(c for c in (x.get("cap"), "scaled: nightly cap") if c)
    for x in out:
        if x["held"]:
            x["stake"] = 0.0
    live = [x for x in out if x["stake"] > 0]
    live.sort(key=(lambda x: (-(x.get("score") or 0), -x["stake"])) if sam else (lambda x: (-x["stake"], -(x["ev"] or 0))))
    return live + [x for x in out if x["stake"] <= 0]


def totals(items):
    """(total staked, total before the nightly cap, capped?) -- scaled (v2) or trimmed lowest score first (v2.1)."""
    act = [x for x in items if x["stake"] > 0 or "stake_capped" in x]      # trimmed-to-zero props count in the pre-cap total
    pre = sum(x.get("stake_capped", x["stake"]) for x in act)
    return round(sum(x["stake"] for x in act), 2), round(pre, 2), any("stake_capped" in x for x in act)


def record_rows(version: str | None = None) -> tuple[list[dict], list]:
    """Graded 'Tonight's bets' plays across every slate: one synthetic row per listed prop, at the CAPPED stake.
    Result and flat units come from the prop's ledger row (all its records share one result); ¼K profit = flat profit per
    1u x capped stake; CLV from that row. Only plays counted for their record (logged before tip) count. -> (rows, clvs)."""
    led = L._read(L.LEDGER)
    counted, clv = L.counted_flags(led)
    ok = {id(r): (c, v) for r, c, v in zip(led, counted, clv)}
    out, clvs = [], []
    for date in sorted({r["date"] for r in led if r["strategy"] in SOURCES}):
        for x in build(date, led):
            best = max(x["rows"], key=lambda r: L._num(r["kelly_u"]))
            c, v = ok.get(id(best), (False, None))
            if not c or best.get("result") not in ("W", "L", "P") or (version and not L.in_version(best, version)):
                continue
            pu = L._num(best.get("profit_u")) or 0.0
            held = "outlier" if x["held"].startswith("check: outlier") else "data error" if x["case"] == "data error" else \
                "other side" if x["held"] else ""
            out.append(dict(best, strategy="tonight", kelly_u=x["stake"], profit_kelly=round(pu * x["stake"], 4),
                            tn_case=x["case"], tn_held=held))
            clvs.append(v)
    return out, clvs


def payload(date: str) -> list[dict]:
    """JSON-safe rows for the note and dashboard: game status, BettingPros projection (dot, number, gap) and side-aware DvP."""
    from nbaprops.pricing import projctx as P
    from nbaprops.rules import tipoff as T
    res = []
    for x in build(date):
        y = {k: v for k, v in x.items() if k != "rows"}
        try:
            c = P.context(date, x["player"], x["market"], x["side"], x["line"])
            y.update(bp_tag=c["tag"], bp_proj=c["proj"], bp_gap=c["gap"], bp_note=c["text"])
        except Exception:  # noqa: BLE001
            y.update(bp_tag="none", bp_proj=None, bp_gap=None, bp_note="")
        try:
            d = P.dvp_of(date, x["player"], x["market"])
            y.update(dvp=d, dvp_tag=P.dvp_effect(x["side"], d) if d is not None else None)
        except Exception:  # noqa: BLE001
            y.update(dvp=None, dvp_tag=None)
        try:
            y["status"] = "open" if not x.get("tip") or T.is_open(x["tip"], [x.get("team")] if x.get("team") else None) else "started"
        except Exception:  # noqa: BLE001
            y["status"] = ""
        res.append(y)
    return res
