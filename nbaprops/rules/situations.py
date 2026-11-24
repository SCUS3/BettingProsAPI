"""Situation finder: splits every record's graded plays by situation (market, side, EV size, odds, DvP, BettingPros
gap, sources, time before tip), and flags splits that look like candidate rules. It never changes a rule: adopting a
candidate is a new rule version (RECORDS.md), so old and new results stay apart.

Candidate rule = a split with at least CANDIDATE_MIN_N graded plays, positive flat units, average CLV above 0 (when
the record has CLV), and p < CANDIDATE_P that its average profit per play is really above 0 (one-sided normal test).
"""
from __future__ import annotations

import csv
import math
import os
import statistics

from nbaprops import config as C
from nbaprops.rules import ledger as L

RECORDS = [  # (record label, strategies) -- the v2 records that carry results
    ("Your rule", {"my_rule"}),
    ("Unabated +EV & BP agrees", {"ua_agree"}),
    ("BETs (Unabated)", {"bet_agree", "bet_only"}),
    ("CrazyNinja +EV", {"cno"}),
    ("CNO screener", {"cno_scr"}),
    ("Near misses (research)", {"near_miss"}),
    ("Alt ladder", {"alt_mkt", "alt_proj"}),
    ("HALF BETs", {"half"}),
]
CANDIDATE_MIN_N = getattr(C, "SITUATION_MIN_N", 100)
CANDIDATE_P = getattr(C, "SITUATION_P", 0.05)
SHOW_MIN_N = 5                # splits with fewer plays are hidden (noise)


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _bucket(v, edges, labels):
    if v is None:
        return None
    for edge, lab in zip(edges, labels):
        if v < edge:
            return lab
    return labels[-1]


_dvp_cache: dict = {}


def _dvp(row):
    """Side-aware DvP for a row: its logged dvp_tag, else from that day's pull."""
    if row.get("dvp_tag"):
        return row["dvp_tag"] if row["dvp_tag"] != "unknown" else None
    from nbaprops.pricing import projctx as P
    d = row["date"]
    if d not in _dvp_cache:
        idx = {}
        path = os.path.join(L.HERE, f"bp_props_{d}.csv")
        if os.path.exists(path):
            for r in csv.DictReader(open(path)):
                if r.get("dvp_softness") not in (None, ""):
                    idx[(P.norm(r["player"]), r["market"])] = float(r["dvp_softness"])
        _dvp_cache[d] = idx
    v = _dvp_cache[d].get((P.norm(row["player"]), row["market"]))
    return P.dvp_effect(row.get("side"), v)


def _minutes_before_tip(row):
    import datetime as dt
    try:
        p = dt.datetime.strptime(row["pulled_at"][:19], "%Y-%m-%dT%H:%M:%S")
        t = dt.datetime.strptime(row["tip"][:19], "%Y-%m-%dT%H:%M:%S")
    except (KeyError, ValueError, TypeError):
        return None
    return (t - p).total_seconds() / 60


DIMENSIONS = [
    ("Market", lambda r: r.get("market")),
    ("Side", lambda r: r.get("side")),
    ("EV at logging", lambda r: _bucket(_f(r.get("oa_ev")), [0, 0.01, 0.02, 0.04], ["EV < 0", "0-1%", "1-2%", "2-4%", "4%+"])),
    ("FanDuel odds", lambda r: _bucket(_f(r.get("odds")), [-150, -110, 111, 200], ["-150 or shorter", "-149 to -111", "-110 to +110", "+111 to +199", "+200 or longer"])),
    ("DvP", _dvp),
    ("BettingPros gap", lambda r: _bucket(None if _f(r.get("bp_proj_raw")) is None or _f(r.get("line")) is None else
                                          abs(_f(r["bp_proj_raw"]) - _f(r["line"])), [0.5, 1.0, 2.0], ["< 0.5", "0.5-1", "1-2", "2+"])),
    ("Sources", lambda r: _bucket(_f(r.get("sources")), [4, 6], ["3", "4-5", "6+"])),
    ("Logged before tip", lambda r: _bucket(_minutes_before_tip(r), [30, 120], ["< 30 min", "30 min - 2 h", "2 h+"])),
]


def _stats(rows, clvs):
    profits = [_f(r.get("profit_u")) or 0.0 for r in rows]
    n = len(rows)
    w = sum(r["result"] == "W" for r in rows)
    l_ = sum(r["result"] == "L" for r in rows)
    units = sum(profits)
    out = {"n": n, "w": w, "l": l_, "p": n - w - l_, "hit": round(100 * w / (w + l_), 1) if w + l_ else None,
           "units": round(units, 2), "roi": round(100 * units / n, 1) if n else None,
           "clv": round(statistics.mean(clvs), 2) if clvs else None, "clv_n": len(clvs), "p_value": None}
    if n >= 2:
        sd = statistics.pstdev(profits)
        if sd > 0:
            z = (units / n) / (sd / math.sqrt(n))
            out["p_value"] = round(0.5 * math.erfc(z / math.sqrt(2)), 4)       # one-sided: mean profit > 0
    return out


def find(version: str = "v2") -> dict:
    """{"records": [{"record", "n", "splits": [...]}], "candidates": [...]} for one rule version."""
    led = [r for r in L._read(L.LEDGER) if L.rule_version(r) == version]
    counted, clv = L.counted_flags(led)
    graded = [(r, v) for r, ok, v in zip(led, counted, clv) if ok and r.get("result") in ("W", "L", "P")]
    out, cands = [], []
    for label, keys in RECORDS:
        sub = [(r, v) for r, v in graded if r["strategy"] in keys]
        if not sub:
            continue
        splits = []
        for dim, fn in DIMENSIONS:
            groups: dict = {}
            for r, v in sub:
                k = fn(r)
                if k:
                    groups.setdefault(k, []).append((r, v))
            for k, g in groups.items():
                if len(g) < SHOW_MIN_N or len(groups) < 2:
                    continue
                s = _stats([r for r, _ in g], [v for _, v in g if v is not None])
                s.update(dimension=dim, value=k)
                s["candidate"] = (s["n"] >= CANDIDATE_MIN_N and s["units"] > 0 and (s["clv"] is None or s["clv"] > 0)
                                  and s["p_value"] is not None and s["p_value"] < CANDIDATE_P)
                splits.append(s)
                if s["candidate"]:
                    cands.append(dict(s, record=label))
        splits.sort(key=lambda s: (-s["candidate"], -(s["units"] or 0)))
        out.append({"record": label, "n": len(sub), "splits": splits})
    return {"version": version, "records": out, "candidates": cands, "min_n": CANDIDATE_MIN_N, "p": CANDIDATE_P}
