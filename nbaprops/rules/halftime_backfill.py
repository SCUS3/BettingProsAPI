"""Backfill halftime snapshots for past nights (free: ESPN play-by-play + box score). Base rate only.

    python3 bp_halftime_backfill.py --check 2026-11-20     # validate play-by-play totals vs. the final box score
    python3 bp_halftime_backfill.py                        # every pulled / simmed date not yet backfilled

First-half PTS, REB, AST, 3PM and fouls come from periods 1-2 of the play-by-play; minutes from starters plus
substitutions against the game clock (approximate); margin from the score at the end of the 2nd quarter. Pregame
FanDuel lines from the day's pull (bp_props_<date>.csv) or sim pull (sim/<date>/sim_props_<date>.csv).
Rows are written to halftime_flags.csv with backfill=yes and alert=no: they feed the base-rate rows of the Record
tab only, never alerts or HALF BETs.
"""
import csv
import glob
import os
import sys
from collections import defaultdict

from nbaprops.sources import espn as BX
from nbaprops.rules import halftime as H
from nbaprops.sources.rotowire import norm

from nbaprops.config import ROOT as HERE  # noqa: E402


def lines_for(date):
    """Pregame FanDuel lines for a date (pull, value file, saved boards, sim pull)."""
    out = H.pregame_lines(date)
    path = os.path.join(HERE, "sim", date, f"sim_props_{date}.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            if r.get("price_is_fanduel") == "True" and r.get("line"):
                out.setdefault((norm(r["player"]), r["market"]), float(r["line"]))
    return out


def _secs(clock):
    c = str(clock or "0")
    if ":" in c:
        m, s = c.split(":")
        return int(m) * 60 + float(s)
    return float(c)


def game_stats(game_id, upto_period=2):
    """{athlete id: {name, team, PTS, REB, AST, 3PM, PF, MIN}} through `upto_period`, from the play-by-play,
    plus {athlete id: final box row} and the margin at the end of `upto_period`."""
    j = BX._get(f"{BX.ESPN}/summary?event={game_id}")
    names, team_of, starters, final = {}, {}, defaultdict(set), {}
    for t in (j.get("boxscore") or {}).get("players", []):
        tid, abbr = str(t.get("team", {}).get("id")), t.get("team", {}).get("abbreviation", "")
        for st in t.get("statistics", [])[:1]:
            idx = {k: i for i, k in enumerate(st.get("keys") or [])}
            for a in st.get("athletes", []):
                aid = str(a.get("athlete", {}).get("id"))
                names[aid], team_of[aid] = a.get("athlete", {}).get("displayName", ""), (tid, abbr)
                if a.get("starter"):
                    starters[tid].add(aid)
                s = a.get("stats") or []
                g = lambda k: BX._int(s[idx[k]]) if s and k in idx else None
                final[aid] = {"PTS": g("points"), "REB": g("rebounds"), "AST": g("assists"), "PF": g("fouls"), "MIN": g("minutes"),
                              "3PM": g("threePointFieldGoalsMade-threePointFieldGoalsAttempted"), "played": bool(s) and not a.get("didNotPlay")}
    st = defaultdict(lambda: {"PTS": 0, "REB": 0, "AST": 0, "3PM": 0, "PF": 0, "sec": 0.0})
    on = {tid: set(v) for tid, v in starters.items()}
    period, last_clock, margin = 1, 720.0, None
    for p in j.get("plays", []):
        per = (p.get("period") or {}).get("number") or 1
        if per > upto_period:
            break
        clk = _secs((p.get("clock") or {}).get("displayValue"))
        if per != period:                                   # credit the rest of the previous period
            for ids in on.values():
                for a in ids:
                    st[a]["sec"] += last_clock
            period, last_clock = per, 720.0 if per <= 4 else 300.0
        dt_ = max(0.0, last_clock - clk)
        for ids in on.values():
            for a in ids:
                st[a]["sec"] += dt_
        last_clock = clk
        typ = (p.get("type") or {}).get("text", "")
        txt = (p.get("text") or "").lower()
        parts = [str(x.get("athlete", {}).get("id")) for x in p.get("participants") or []]
        if typ == "Substitution" and len(parts) >= 2:
            tid = team_of.get(parts[0], (None,))[0]
            if tid:
                on.setdefault(tid, set()).discard(parts[1])
                on[tid].add(parts[0])
            continue
        if p.get("scoringPlay") and parts:
            st[parts[0]]["PTS"] += int(p.get("scoreValue") or 0)
            if int(p.get("scoreValue") or 0) == 3:
                st[parts[0]]["3PM"] += 1
            if "assist" in txt and len(parts) >= 2:
                st[parts[1]]["AST"] += 1
        if "rebound" in typ.lower() and parts:
            st[parts[0]]["REB"] += 1
        if "foul" in typ.lower() and "technical" not in typ.lower() and "turnover" not in typ.lower() and parts:
            st[parts[0]]["PF"] += 1
        margin = abs(int(p.get("homeScore") or 0) - int(p.get("awayScore") or 0))
    for ids in on.values():                                 # rest of the last period played
        for a in ids:
            st[a]["sec"] += last_clock
    out = {}
    for aid, s in st.items():
        if aid in names:
            out[aid] = dict(s, name=names[aid], team=team_of[aid][1], MIN=round(s["sec"] / 60))
    return out, final, names, team_of, margin


def check(date):
    """Full-game play-by-play totals vs. the final box score, per stat."""
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    agree, total, mins = defaultdict(int), defaultdict(int), []
    for e in sb.get("events", []):
        st, final, *_ = game_stats(e["id"], upto_period=9)
        for aid, f in final.items():
            if not f["played"] or aid not in st:
                continue
            for k in ("PTS", "REB", "AST", "3PM", "PF"):
                if f[k] is not None:
                    total[k] += 1
                    agree[k] += st[aid][k] == f[k]
            if f["MIN"] is not None:
                mins.append(abs(st[aid]["MIN"] - f["MIN"]))
    for k in ("PTS", "REB", "AST", "3PM", "PF"):
        print(f"  {k}: {agree[k]}/{total[k]} players exact ({100 * agree[k] / max(total[k], 1):.0f}%)")
    if mins:
        mins.sort()
        print(f"  MIN: median miss {mins[len(mins) // 2]} min, 90th pct {mins[int(0.9 * (len(mins) - 1))]} min, "
              f"within 2 min {sum(m <= 2 for m in mins)}/{len(mins)}")


def backfill(date):
    """Backfill base-rate rows for a date's final games without a live snapshot."""
    lines = lines_for(date)
    if not lines:
        return 0
    try:
        have = [r for r in csv.DictReader(open(H.FLAGS))]
    except OSError:
        have = []
    done = {r["game_id"] for r in have if r["date"] == date and not r.get("legacy")}   # backfilled or live-snapshotted
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    new = []
    for e in sb.get("events", []):
        if e["id"] in done or e.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        half, final, names, team_of, margin = game_stats(e["id"])
        for aid, p in half.items():
            key = norm(p["name"])
            for mk, parts in H.PARTS.items():
                line = lines.get((key, mk))
                f = final.get(aid) or {}
                if line is None or line <= 0 or not f.get("played"):
                    continue
                v = sum(p[x] for x in parts)
                fin = sum((f.get(x) or 0) for x in parts)
                pace = v / line
                skip = [x for x, on in ((f"{p['PF']} fouls", p["PF"] >= H.FOUL_FLAG), (f"margin {margin}", (margin or 0) >= H.MARGIN_FLAG)) if on]
                new.append({"date": date, "game_id": e["id"], "game": e.get("shortName"), "flagged_at": "backfill",
                            "player": p["name"], "team": p["team"], "market": mk, "side": "", "alert": "no",
                            "big_line": line >= H.MIN_LINE.get(mk, 99), "line": line, "half": v, "pace": round(pace, 2),
                            "min": p["MIN"], "pf": p["PF"], "margin": margin, "skip": "; ".join(skip), "final": fin,
                            "finished": "over" if fin > line else "under" if fin < line else "push",
                            "second_half": fin - v, "hit": "", "legacy": "", "backfill": "yes",
                            "over_hit": "" if fin == line else ("W" if fin > line else "L")})
    if new:
        with open(H.FLAGS, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=H.FLAG_FIELDS + ["over_hit"], extrasaction="ignore")
            w.writeheader()
            w.writerows(have + new)
    return len(new)


def dates():
    """Dates with a pull or a sim pull."""
    ds = {os.path.basename(p)[9:-4] for p in glob.glob(os.path.join(HERE, "bp_props_*.csv"))}
    ds |= {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, "sim", "*", "sim_props_*.csv"))}
    return sorted(ds)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check(sys.argv[sys.argv.index("--check") + 1])
    else:
        for d in dates():
            n = backfill(d)
            print(f"{d}: {n} backfill rows")
