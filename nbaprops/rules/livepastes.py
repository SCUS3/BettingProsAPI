"""Live paste log: every screener row pasted while its game was under way (display only when pasted, never staked).

Each row keeps the paste time, FanDuel price, CrazyNinja EV and fair odds, books, BettingPros' pregame projection, the
halftime expected final and the live stat at that moment. After the games: graded from the final box score (flat 1u at
the pasted price), and CLV = the same prop's fair odds in your NEXT paste (fair_next x decimal(your price) - 1).
Stored in live_pastes.csv. Saved paste files (history/<date>/cno_paste_*.txt, half_paste_*.txt) can be backfilled.

    python3 -m nbaprops.rules.livepastes --backfill 2026-11-24   # rebuild a date from its saved paste files
    python3 -m nbaprops.rules.livepastes --grade 2026-11-25      # grade from box scores and set CLV
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import os
import re

from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm

PATH = os.path.join(L.HERE, "live_pastes.csv")
FIELDS = ["date", "at", "game", "player", "market", "side", "line", "odds", "ev", "fair", "books", "bp_proj", "bp_tag",
          "half_exp", "live_now", "clock", "source", "result", "actual", "profit_u", "clv_ev", "next_at"]


def _num(v):
    try:
        return None if v in (None, "") else float(str(v).replace("%", "").replace("+", ""))
    except ValueError:
        return None


def _dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def read(date=None):
    try:
        rows = list(csv.DictReader(open(PATH)))
    except OSError:
        return []
    return [r for r in rows if date is None or r["date"] == date]


def _write(rows):
    with open(PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["at"], r["player"], r["market"])))


def key(r):
    return (r["date"], r["at"][:16], norm(r["player"]), r["market"], r["side"], str(_num(r["line"])))


def add(date, rows, at, source="screener"):
    """Append live rows (dicts with player, market, side, line, odds, ev, fair, books, bp_proj, bp_tag, half_exp, live_now,
    clock, game); a row already logged for the same minute is skipped."""
    old = read()
    have = {key(r) for r in old}
    new = []
    for x in rows:
        r = {k: x.get(k, "") for k in FIELDS}
        r.update(date=date, at=at, source=source)
        if key(r) not in have:
            have.add(key(r))
            new.append(r)
    if new:
        _write(old + new)
    return new


def _paste_time(date, path):
    """UTC ISO time from a saved paste file name (..._HHMMSS.txt, UTC clock on the evening of ET date `date`)."""
    m = re.search(r"_(\d{6})(?:_hdr)?\.txt$", path)
    if not m:
        return None
    hh, mm, ss = int(m.group(1)[:2]), int(m.group(1)[2:4]), int(m.group(1)[4:])
    d = dt.date.fromisoformat(date) + dt.timedelta(days=1 if hh < 12 else 0)
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:{ss:02d}Z"


def parse_rows(text, date):
    """Screener rows (with or without the header) -> [{player, market, side, line, odds, ev, fair, books, game, tip}]."""
    from nbaprops.rules import screener as S
    out = []
    for t in (S._table(text) or S._loose_table(text)):
        if "fanduel" not in (t.get("book") or "").lower():
            continue
        mk = next((m for pat, m in S.MARKETS if re.search(pat, (t.get("market") or "").lower())), None)
        sm = S.SIDE.search(t.get("bet") or "")
        if not mk or not sm:
            continue
        names = S.names_map(date)
        pl = t["bet"][:sm.start()].strip()
        out.append({"player": names.get(norm(pl), pl), "market": mk, "side": "over" if sm.group(1).lower().startswith("o") else "under",
                    "line": float(sm.group(2)), "odds": t.get("odds"), "ev": _num(t.get("ev")), "fair": t.get("fair"),
                    "books": t.get("books"), "game": t.get("event"), "tip": S._when(t.get("date"), date)})
    return out


def backfill(date) -> int:
    """Rebuild a date's live rows from its saved paste files: rows whose game had tipped when the paste was saved."""
    from nbaprops.pricing import projctx as P
    files = sorted(f for f in glob.glob(os.path.join(L.HERE, "history", date, "*paste_*.txt")) if not f.endswith("_hdr.txt"))
    keep = [r for r in read() if r["date"] != date or r.get("source") not in ("screener", "backfill")]
    rows = []
    for f in files:
        at = _paste_time(date, f)
        if not at:
            continue
        for r in parse_rows(open(f).read(), date):
            if not r["tip"] or at <= r["tip"]:
                continue                                        # pregame row: logged by the pregame records instead
            c = P.context(date, r["player"], r["market"], r["side"], r["line"])
            rows.append(dict(r, date=date, at=at, source="backfill", bp_proj=c.get("proj") or "", bp_tag=c.get("tag") or ""))
    seen, out = set(), []
    for r in rows:
        k = key(r)
        if k not in seen:
            seen.add(k)
            out.append({k2: r.get(k2, "") for k2 in FIELDS})
    _write(keep + out)
    return len(out)


_SUMMARY: dict = {}


def _summary(game_id):
    from nbaprops.sources import espn as BX
    if game_id not in _SUMMARY:
        _SUMMARY[game_id] = BX._get(f"{BX.ESPN}/summary?event={game_id}")
    return _SUMMARY[game_id]


def stats_at(game_id, at_iso):
    """{norm name: {PTS, REB, AST, 3PM}} from ESPN's play-by-play up to a wall-clock time, and the game clock then."""
    j = _summary(game_id)
    names = {}
    for t in (j.get("boxscore") or {}).get("players", []):
        for st in t.get("statistics", [])[:1]:
            for a in st.get("athletes", []):
                names[str(a.get("athlete", {}).get("id"))] = a.get("athlete", {}).get("displayName", "")
    st: dict = {}
    clock = ""
    for p in j.get("plays", []):
        wc = p.get("wallclock") or ""
        if not wc or wc > at_iso:
            break
        per = (p.get("period") or {}).get("number") or 1
        clock = f"{(p.get('clock') or {}).get('displayValue', '')} Q{per}" if per <= 4 else f"{(p.get('clock') or {}).get('displayValue', '')} OT"
        typ = ((p.get("type") or {}).get("text") or "").lower()
        txt = (p.get("text") or "").lower()
        parts = [str(x.get("athlete", {}).get("id")) for x in p.get("participants") or []]
        get = lambda a: st.setdefault(norm(names.get(a, "")), {"PTS": 0, "REB": 0, "AST": 0, "3PM": 0})
        if p.get("scoringPlay") and parts:
            v = int(p.get("scoreValue") or 0)
            get(parts[0])["PTS"] += v
            if v == 3:
                get(parts[0])["3PM"] += 1
            if "assist" in txt and len(parts) >= 2:
                get(parts[1])["AST"] += 1
        if "rebound" in typ and parts:
            get(parts[0])["REB"] += 1
    return st, clock


PARTS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "P+A": ("PTS", "AST"), "P+R": ("PTS", "REB"),
         "R+A": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}


def enrich(date) -> int:
    """Fill 'Live at paste' (the stat when you pasted, rebuilt from timestamped play-by-play), the game clock then, and
    'Halftime exp.' (the halftime snapshot's expected final) where missing. Free ESPN data. -> rows filled."""
    import json
    from nbaprops.rules import halftime as H
    rows = read()
    mine = [r for r in rows if r["date"] == date and (r.get("live_now") in ("", None) or r.get("half_exp") in ("", None))]
    if not mine:
        return 0
    exp, game_of = {}, {}
    try:
        for g in json.load(open(H.snapshot_path(date))).values():
            for x in g.get("rows", []):
                exp[(norm(x["player"]), x["market"])] = x.get("exp_final")
    except (OSError, ValueError):
        pass
    try:
        for g in H.games(date):
            j = _summary(g["id"])
            for t in (j.get("boxscore") or {}).get("players", []):
                for st in t.get("statistics", [])[:1]:
                    for a in st.get("athletes", []):
                        game_of[norm(a.get("athlete", {}).get("displayName", ""))] = g["id"]
    except Exception:  # noqa: BLE001
        pass
    n = 0
    for r in mine:
        k = norm(r["player"])
        if r.get("half_exp") in ("", None) and exp.get((k, r["market"])) is not None:
            r["half_exp"] = exp[(k, r["market"])]
        gid = game_of.get(k)
        if r.get("live_now") in ("", None) and gid and r["market"] in PARTS:
            st, clock = stats_at(gid, r["at"])
            s = st.get(k) or {"PTS": 0, "REB": 0, "AST": 0, "3PM": 0}
            r["live_now"] = sum(s[p] for p in PARTS[r["market"]])
            r["clock"] = r.get("clock") or clock
        n += 1
    _write(rows)
    return n


def grade(date) -> tuple[int, int]:
    """Final result and CLV for a date's live rows. -> (graded, still pending)."""
    from nbaprops.grading import grade as G
    from nbaprops.sources import espn as BX
    rows = read()
    mine = [r for r in rows if r["date"] == date]
    try:
        box = BX.finals(date, log=lambda *a: None)
    except Exception:  # noqa: BLE001
        box = {}
    n = pend = 0
    by_prop: dict = {}
    for r in mine:
        by_prop.setdefault((norm(r["player"]), r["market"], r["side"], str(_num(r["line"]))), []).append(r)
    for lst in by_prop.values():
        lst.sort(key=lambda r: r["at"])
        for i, r in enumerate(lst):
            nxt = next((x for x in lst[i + 1:] if x["at"][:16] > r["at"][:16] and _num(x.get("fair")) is not None), None)
            if nxt and _num(r.get("odds")) is not None:
                f_next = 1 / _dec(_num(nxt["fair"]))
                r["clv_ev"], r["next_at"] = round(100 * (f_next * _dec(_num(r["odds"])) - 1), 2), nxt["at"]
    for r in mine:
        if r.get("result"):
            continue
        p = BX.lookup(box, r["player"]) if box else None
        v = BX.stat(p, r["market"]) if p else None
        if v is None:
            pend += 1
            continue
        res = G.grade(r["side"], _num(r["line"]), v)
        r.update(result=res, actual=v, profit_u=G.profit(_num(r["odds"]), res) if _num(r["odds"]) is not None else "")
        n += 1
    _write(rows)
    return n, pend


def summary(version=None):
    """Rows and totals for the dashboard (newest first)."""
    rows = [r for r in read() if version is None or L.in_version({"date": r["date"]}, version)]
    graded = [r for r in rows if r.get("result") in ("W", "L", "P")]
    clv = [_num(r["clv_ev"]) for r in rows if _num(r.get("clv_ev")) is not None]
    evs = [_num(r["ev"]) for r in rows if _num(r.get("ev")) is not None]
    units = sum(_num(r.get("profit_u")) or 0 for r in graded)
    first = {}                                         # Live +EV record (v2.2): each prop once, at its first +EV live paste
    for r in sorted(rows, key=lambda r: r["at"]):
        if (_num(r.get("ev")) or 0) > 0:
            first.setdefault((r["date"], norm(r["player"]), r["market"], r["side"], str(_num(r["line"]))), r)
    eg = [r for r in first.values() if r.get("result") in ("W", "L", "P")]
    ev_rec = {"n": len(first), "graded": len(eg), "w": sum(r["result"] == "W" for r in eg), "l": sum(r["result"] == "L" for r in eg),
              "p": sum(r["result"] == "P" for r in eg), "units": round(sum(_num(r.get("profit_u")) or 0 for r in eg), 2),
              "avg_ev": round(sum(_num(r["ev"]) for r in first.values()) / len(first), 2) if first else None}
    return {"ev_record": ev_rec, "rows": sorted(rows, key=lambda r: r["at"], reverse=True), "n": len(rows),
            "w": sum(r["result"] == "W" for r in graded), "l": sum(r["result"] == "L" for r in graded),
            "p": sum(r["result"] == "P" for r in graded), "units": round(units, 2),
            "avg_ev": round(sum(evs) / len(evs), 2) if evs else None, "avg_clv": round(sum(clv) / len(clv), 2) if clv else None,
            "clv_n": len(clv)}


if __name__ == "__main__":
    import sys
    if "--backfill" in sys.argv:
        d = sys.argv[sys.argv.index("--backfill") + 1]
        print(f"{d}: {backfill(d)} live rows rebuilt from saved pastes")
    if "--grade" in sys.argv:
        d = sys.argv[sys.argv.index("--grade") + 1]
        print(f"{d}: {enrich(d)} live rows filled (live stat at paste, halftime expected)")
        g, p = grade(d)
        print(f"{d}: {g} live rows graded, {p} pending")
