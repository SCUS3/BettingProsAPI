"""Halftime live: price a pasted set of live odds with CrazyNinja's worst case and log HALF BETs (research record).

    python3 bp_value.py --half-paste live.txt [--dry]

One prop per line: player, market, side+line, FanDuel's live price, then each other book's live two-way price
(over/under), e.g.

    Franz Wagner PTS o21.5 FD -110 | DK -115/-105 | MGM -110/-110 | Caesars -112/-108 | Novig -108/-108

FanDuel's own two-way ("FD -110/-110") is fine too (its bet-side price is used). Rule: 3+ other books at the same
line with both sides; market EV (median multiplicative devig) >= +3%; CrazyNinja median worst-case EV > 0 (CNO
down -> not logged). Skip: 3+ fouls, 20+ point margin, injury mention or ejection (live ESPN box score, free).
Stake ¼K on the market fair probability, capped 0.5u per bet and 1.5u per game. A later paste of the same prop
logs its CLV (EV of the bet at the newer fair price; the next scan = start of the 3rd quarter).
"""
from nbaprops import config as C  # thresholds, caps and settings
import os
import re
import statistics
import sys

from nbaprops.rules import halftime as H
from nbaprops.rules import ledger as L
from nbaprops.app import value as V
from nbaprops.sources.rotowire import norm

from nbaprops.config import ROOT as HERE  # noqa: E402
KEY = "half"
PASTES = os.path.join(HERE, "half_pastes.csv")
PASTE_FIELDS = ["date", "at", "player", "market", "side", "line", "fd", "books_n", "books", "fair", "market_ev", "cno_ev",
                "cno_min_ev", "decision", "reason", "min", "pf", "margin", "pregame_line", "half", "exp_final", "exp_side", "exp_tag"]
MIN_EV, MIN_SOURCES = C.HALF_MIN_EV, C.HALF_MIN_SOURCES  # nbaprops/config.py
BET_CAP, GAME_CAP = C.HALF_BET_CAP, C.HALF_GAME_CAP  # nbaprops/config.py
PAIR = re.compile(r"([A-Za-z][A-Za-z0-9 .&'-]*?)\s*:?\s*([+-]\d{3,5})\s*/\s*([+-]\d{3,5})")
FDP = re.compile(r"\b(?:fd|fanduel)\b\s*:?\s*([+-]\d{3,5})(?:\s*/\s*([+-]\d{3,5}))?", re.I)


def _dec(a):
    return V.decimal(float(a))


def _mult(o, u):
    po, pu = 1 / _dec(o), 1 / _dec(u)
    return po / (po + pu)


def parse(text, names):
    out, bad = [], []
    for ln in [x for x in text.splitlines() if x.strip()]:
        head = ln.split("|")[0]
        t = head.split()
        mi = next((i for i, w in enumerate(t) if w.lower() in V.MARKET_WORDS), None)
        sm = re.search(r"\b(o|u|over|under)\s*(\d+(?:\.\d)?)\b", head, re.I)
        fm = FDP.search(ln)
        if mi is None or not sm or not fm:
            bad.append(ln[:80])
            continue
        nm = norm(" ".join(t[:mi]))
        player = next((n for n in names if nm and (nm == norm(n) or norm(n).endswith(" " + nm))), " ".join(t[:mi]))
        side = "over" if sm.group(1).lower().startswith("o") else "under"
        fd = float(fm.group(1)) if not fm.group(2) or side == "over" else float(fm.group(2))
        rest = FDP.sub("", ln[len(head):] if "|" in ln else ln[sm.end():])
        books = [(b.strip(), float(o), float(u)) for b, o, u in PAIR.findall(rest) if not re.match(r"(fd|fanduel)$", b.strip(), re.I)]
        out.append({"player": player, "market": V.MARKET_WORDS[t[mi].lower()], "side": side, "line": float(sm.group(2)),
                    "fd": fd, "books": books, "raw": ln[:200]})
    return out, bad


def context(date):
    """{norm player: (box row, game)} from the live ESPN box scores of games at or past halftime."""
    out = {}
    for g in H.games(date):
        if g["status"] in ("STATUS_HALFTIME", "STATUS_IN_PROGRESS", "STATUS_END_PERIOD") and (g["period"] or 0) >= 2:
            try:
                for k, p in H.half_box(g["id"]).items():
                    out[k] = (p, g)
            except Exception:
                pass
    return out


def run(args):
    text = sys.stdin.read() if args.half_paste == "-" else open(args.half_paste).read()
    led = L._read(L.LEDGER)
    ctx = context(args.date)
    pregame = H.pregame_lines(args.date)
    snap = {}                                            # first-half stat + pregame line frozen at halftime
    try:
        import json
        for g in json.load(open(H.snapshot_path(args.date))).values():
            for x in g.get("rows", []):
                snap[(norm(x["player"]), x["market"])] = x
    except (OSError, ValueError):
        pass
    names = sorted({r["player"] for r in led} | {p["name"] for p, _ in ctx.values()}, key=len, reverse=True)
    props, bad = parse(text, names)
    if bad:
        print(f"Couldn't read {len(bad)} line(s): " + " || ".join(bad[:3]))
    now = L.utc_now()
    k = lambda r: (r["player"], r["market"], r["side"], str(V._f(r.get("line"))))
    have = {k(r): r for r in led if r["date"] == args.date and r["strategy"] == KEY}
    cands, rows, clv_done, plog = [], [], 0, []
    logrow = lambda p, **k: plog.append(dict({"date": args.date, "at": now, "player": p["player"], "market": p["market"],
                                              "side": p["side"], "line": p["line"], "fd": p["fd"], "books_n": len(p["books"]),
                                              "books": "; ".join(f"{b} {o:+g}/{u:+g}" for b, o, u in p["books"])}, **k))
    for p in props:
        two = [(b, o, u) for b, o, u in p["books"]]
        fairs = [(_mult(o, u) if p["side"] == "over" else 1 - _mult(o, u)) for _, o, u in two]
        tag = f"{p['player']} {p['market']} {p['side']} {p['line']:g} FD {p['fd']:+g}"
        if len(two) < MIN_SOURCES:
            print(f"  {tag}: only {len(two)} other books with both sides (needs {MIN_SOURCES}); not priced")
            logrow(p, decision="not priced", reason=f"{len(two)} books (needs {MIN_SOURCES})")
            continue
        fair = statistics.median(fairs)
        ev = fair * _dec(p["fd"]) - 1
        prior = have.get((p["player"], p["market"], p["side"], str(p["line"])))
        if prior is not None:                                  # a later paste: CLV of the logged HALF BET
            prior.update(close_fair=round(fair, 4), clv_ev=round(fair * _dec(float(prior["odds"])) - 1, 4),
                         close_mkt_ev=round(ev, 4), close_at=now, close_fd_line=p["line"])
            clv_done += 1
            print(f"  {tag}: CLV for the logged HALF BET {100 * float(prior['clv_ev']):+.1f}% (market EV now {100 * ev:+.1f}%)")
            logrow(p, fair=round(fair, 4), market_ev=round(ev, 4), decision="CLV update", reason="later paste of a HALF BET")
            continue
        c = V.cno_check(p["side"], {"two_way": [(b, o, u) for b, o, u in two], "price_used": p["fd"]})
        box, game = ctx.get(norm(p["player"]), (None, None))
        sx = snap.get((norm(p["player"]), p["market"]))
        if sx:                                           # halftime snapshot (exact first half)
            pre, half = sx["line"], sx["half"]
        else:                                            # no snapshot: live box (only right if pasted at halftime)
            pre = pregame.get((norm(p["player"]), p["market"]))
            half = sum((box or {}).get(x) or 0 for x in H.PARTS[p["market"]]) if box else None
        exp = H.expected_final(p["market"], half, pre)[0] if half is not None and pre else None
        exp_side = None if exp is None or exp == p["line"] else ("over" if exp > p["line"] else "under")
        exp_tag = "none" if exp_side is None else ("agrees" if exp_side == p["side"] else "disagrees")
        exp_txt = (f"; FD live {p['line']:g} vs exp {exp:g} -> {exp_side or 'even'} side ({exp_tag})" if exp is not None
                   else "; no expected final (no pregame line or live box)")
        skip = []
        if box:
            if (box.get("PF") or 0) >= H.FOUL_FLAG:
                skip.append(f"{box['PF']} fouls")
            if box.get("injury"):
                skip.append("injury mention")
            if box.get("ejected"):
                skip.append("ejected")
        if game and game["margin"] >= H.MARGIN_FLAG:
            skip.append(f"margin {game['margin']}")
        cno_txt = "CNO down" if c is None else f"CNO worst-case {100 * c['median_ev']:+.1f}%"
        info = (f"market EV {100 * ev:+.1f}% (fair {fair:.3f}, {len(two)} books), {cno_txt}"
                + (f"; {box['MIN']} min, {box['PF']} PF, margin {game['margin']}" if box and game else "; no live box match"))
        ok = ev >= MIN_EV and c is not None and c["median_ev"] > 0 and not skip
        why = [w for w, on in ((f"market EV under +{100 * MIN_EV:g}%", ev < MIN_EV), ("CNO down", c is None),
                               ("CNO worst case <= 0", c is not None and c["median_ev"] <= 0)) if on] + skip
        logrow(p, fair=round(fair, 4), market_ev=round(ev, 4), cno_ev=None if c is None else c["median_ev"],
               cno_min_ev=None if c is None else c["min_ev"], decision="HALF BET" if ok else "pass", reason="; ".join(why),
               min=(box or {}).get("MIN"), pf=(box or {}).get("PF"), margin=(game or {}).get("margin"),
               pregame_line=pre, half=half, exp_final=exp, exp_side=exp_side, exp_tag=exp_tag)
        print(f"  {'HALF BET' if ok else 'pass    '} {tag}: {info}{exp_txt}" + (f"  SKIP: {', '.join(skip)}" if skip else ""))
        if ok:
            cands.append((p, fair, ev, c, game, box, exp_tag))
    if clv_done and not args.dry:
        L._write(L.LEDGER, led, L.LEDGER_FIELDS)
    if plog and not args.dry:
        import csv
        new_file = not os.path.exists(PASTES)
        with open(PASTES, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PASTE_FIELDS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(plog)
    if not cands or args.dry:
        return
    gkey = lambda r: r.get("game") or r.get("event_id") or "?"
    logged = [dict(r, game=r.get("event_id")) for r in led if r["date"] == args.date and r["strategy"] == KEY]
    cc = V.apply_exposure([{"player": p["player"], "game": f"espn:{g['id']}" if g else "?", "ev": ev,
                            "stake": min(V.bet_kelly(fair, p["fd"], BET_CAP), BET_CAP)} for p, fair, ev, c, g, b, _ in cands],
                          logged, gkey, player_cap=GAME_CAP, game_cap=GAME_CAP)
    for (p, fair, ev, c, g, b, et), x in zip(cands, cc):
        if x["stake"] <= 0:
            print(f"  at cap, not logged: {p['player']} {p['market']} {p['side']} {p['line']:g}")
            continue
        rows.append({"date": args.date, "pulled_at": now, "tip": "", "strategy": KEY, "player": p["player"],
                     "team": (b or {}).get("team"), "market": p["market"], "side": p["side"], "line": p["line"],
                     "odds": p["fd"], "our_prob": round(fair, 4), "nv_prob": round(fair, 4), "kelly_u": x["stake"],
                     "event_id": f"espn:{g['id']}" if g else "", "oa_fair": round(fair, 4), "oa_ev": round(ev, 4),
                     "fair_src": "half paste", "sources": len(p["books"]), "cno_ev": c["median_ev"], "cno_min_ev": c["min_ev"],
                     "cap_note": x["cap"], "proj_tag": "exp " + et})
        print(f"  logged HALF BET {p['player']} {p['market']} {p['side']} {p['line']:g} {p['fd']:+g}: {x['stake']}u ¼K"
              + (f" ({x['cap']})" if x["cap"] else ""))
    if rows:
        L._write(L.LEDGER, led + rows, L.LEDGER_FIELDS)
