"""Halftime live experiment (research; own record). ESPN's public scoreboard and live box score only (free).

At halftime, lists players far off pace against their pregame FanDuel line:
  over candidates:  first-half stat <= 35% of the line
  under candidates: first-half stat >= 70% of the line
with fouls, minutes, the margin and skip flags (3+ fouls, 20+ point margin, injury mention in the play-by-play).
Then you paste live odds (bp_value.py --half-paste).

    python3 bp_halftime.py                 # games at halftime now
    python3 bp_halftime.py --watch         # poll ESPN every 2 min; alert at each new halftime; exits at the next one
                                           # (so the session is told), or when every game is past halftime

Pregame FanDuel lines: today's saved pull (bp_props_<date>.csv), the day's value file, and the latest saved Unabated
board for the date (its FanDuel lines freeze at tip, i.e. the closing pregame line). No BettingPros requests.
"""
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import time

import bp_boxscore as BX
from bp_rotowire import norm

HERE = os.path.dirname(os.path.abspath(__file__))
OVER_PACE, UNDER_PACE = 0.35, 0.70
FOUL_FLAG, MARGIN_FLAG = 3, 20
PARTS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "P+A": ("PTS", "AST"),
         "P+R": ("PTS", "REB"), "PRA": ("PTS", "REB", "AST")}


def et_today():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def pregame_lines(date):
    """{(norm player, market): line} of FanDuel's pregame main lines."""
    out = {}
    try:
        for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
            if r.get("price_is_fanduel") == "True" and r.get("line"):
                out[(norm(r["player"]), r["market"])] = float(r["line"])
    except OSError:
        pass
    try:
        for k in json.load(open(os.path.join(HERE, f"value_{date}.json"))):
            if k.startswith("_") or k.count("|") != 2:
                continue
            pl, mk, ln = k.split("|")
            out.setdefault((norm(pl), mk), float(ln))
    except (OSError, ValueError):
        pass
    try:                                                   # frozen FanDuel lines on a saved Unabated board
        import bp_unabated as U
        d = U.load(U.DISCOVERY, {})
        people = {str(x["id"]): norm(f"{x.get('firstName', '')} {x.get('lastName', '')}")
                  for x in U._items((d.get("raw") or {}).get("people"))}
        bt = {str(b["id"]): t for t, v in d.get("bettypes", {}).items() for b in v}
        fd = str(d.get("fanduel_source_id"))
        compact = date.replace("-", "")
        nxt = (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat().replace("-", "")
        for path in sorted(glob.glob(os.path.join(HERE, f"unabated_*{compact}T*.json")) +
                           glob.glob(os.path.join(HERE, f"unabated_*{nxt}T0*.json")))[-2:]:
            data = json.load(open(path))
            for ctx, side in U.walk_sides(data.get("odds") if isinstance(data, dict) else data):
                if ctx.get("betSubType") or str(ctx.get("periodTypeId")) != "1" or str(ctx.get("sideIndex")) != "0":
                    continue
                who, tag = people.get(str(ctx.get("personId"))), bt.get(str(ctx.get("betTypeId")))
                for ln in U._lines(side.get("marketSourceLines")):
                    if str(ln.get("marketSourceId")) == fd and who and tag and ln.get("points") is not None:
                        out.setdefault((who, tag), float(ln["points"]))
    except Exception:
        pass
    return out


def games(date):
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    out = []
    for e in sb.get("events", []):
        st = e.get("status", {})
        comp = (e.get("competitions") or [{}])[0]
        score = {c["team"]["abbreviation"]: int(c.get("score") or 0) for c in comp.get("competitors", [])}
        out.append({"id": e["id"], "name": e.get("shortName"), "status": st.get("type", {}).get("name"),
                    "period": st.get("period"), "score": score, "margin": abs(max(score.values()) - min(score.values())) if score else 0})
    return out


def half_box(game_id):
    j = BX._get(f"{BX.ESPN}/summary?event={game_id}")
    plays = " ".join((p.get("text") or "") for p in j.get("plays", []) if (p.get("period") or {}).get("number", 9) <= 2).lower()
    players = {}
    for t in (j.get("boxscore") or {}).get("players", []):
        team = t.get("team", {}).get("abbreviation", "")
        for st in t.get("statistics", [])[:1]:
            idx = {k: i for i, k in enumerate(st.get("keys") or [])}
            for a in st.get("athletes", []):
                s = a.get("stats") or []
                if not s:
                    continue
                g = lambda k: BX._int(s[idx[k]]) if k in idx else None
                name = a.get("athlete", {}).get("displayName", "")
                last = name.split()[-1].lower() if name else "~"
                injury = bool(re.search(rf"{re.escape(last)}[^.]{{0,60}}injur|injur[^.]{{0,60}}{re.escape(last)}", plays))
                players[norm(name)] = {"name": name, "team": team, "MIN": g("minutes"), "PTS": g("points"), "REB": g("rebounds"),
                                       "AST": g("assists"), "3PM": g("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
                                       "PF": g("fouls"), "ejected": bool(a.get("ejected")), "injury": injury}
    return players


def off_pace(date, game, lines):
    box = half_box(game["id"])
    out = []
    for key, p in box.items():
        for mk, parts in PARTS.items():
            line = lines.get((key, mk))
            vals = [p.get(x) for x in parts]
            if line is None or any(v is None for v in vals) or line <= 0:
                continue
            v = sum(vals)
            pace = v / line
            side = "over" if pace <= OVER_PACE else "under" if pace >= UNDER_PACE else None
            if not side:
                continue
            skip = [x for x, on in ((f"{p['PF']} fouls", (p["PF"] or 0) >= FOUL_FLAG),
                                    (f"margin {game['margin']}", game["margin"] >= MARGIN_FLAG),
                                    ("injury mention", p["injury"]), ("ejected", p["ejected"])) if on]
            out.append({"game": game["name"], "game_id": game["id"], "player": p["name"], "team": p["team"], "market": mk,
                        "side": side, "line": line, "half": v, "pace": round(pace, 2), "min": p["MIN"], "pf": p["PF"],
                        "margin": game["margin"], "skip": skip})
    return sorted(out, key=lambda x: (bool(x["skip"]), x["side"], x["pace"] if x["side"] == "over" else -x["pace"]))


FLAGS = os.path.join(HERE, "halftime_flags.csv")
FLAG_FIELDS = ["date", "game_id", "game", "flagged_at", "player", "team", "market", "side", "line", "half", "pace", "min",
               "pf", "margin", "skip", "final", "finished", "second_half", "hit"]


def grade_flags(date, box=None):
    """Every halftime flag for `date` (from the saved snapshot), with the final stat once the game is final:
    finished over/under the pregame line, 2nd-half production, and whether the flagged side hit."""
    try:
        snap = json.load(open(snapshot_path(date)))
    except (OSError, ValueError):
        return 0
    if box is None:
        try:
            box = BX.finals(date)
        except Exception:
            box = None
    try:
        old = [r for r in csv.DictReader(open(FLAGS)) if r["date"] != date]
    except OSError:
        old = []
    new = []
    for gid, g in snap.items():
        for x in g.get("flags", []):
            p = BX.lookup(box, x["player"]) if box else None
            fin = BX.stat(p, x["market"]) if p else None
            row = {"date": date, "game_id": gid, "game": g.get("game"), "flagged_at": g.get("at"), "player": x["player"],
                   "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "half": x["half"],
                   "pace": x["pace"], "min": x.get("min"), "pf": x.get("pf"), "margin": x.get("margin"),
                   "skip": "; ".join(x.get("skip") or []), "final": "", "finished": "", "second_half": "", "hit": ""}
            if fin is not None:
                row.update(final=fin, second_half=fin - x["half"],
                           finished="over" if fin > x["line"] else "under" if fin < x["line"] else "push",
                           hit="" if fin == x["line"] else ("W" if (fin > x["line"]) == (x["side"] == "over") else "L"))
            if x["side"] == "under" and x["half"] > x["line"]:
                row["hit"] = "decided at half"            # already over the pregame line: not a live question
            new.append(row)
    with open(FLAGS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FLAG_FIELDS)
        w.writeheader()
        w.writerows(old + new)
    return sum(1 for r in new if r["hit"])


def flag_summary():
    """For the Record tab: flags per night and hit rate by type (side x skip / no skip)."""
    try:
        rows = list(csv.DictReader(open(FLAGS)))
    except OSError:
        rows = []
    nights = {}
    for r in rows:
        n = nights.setdefault(r["date"], {"date": r["date"], "flags": 0, "skipped": 0, "graded": 0, "games": set()})
        n["flags"] += 1
        n["skipped"] += bool(r["skip"])
        n["graded"] += r["hit"] in ("W", "L")
        n["games"].add(r["game_id"])
    by_type = []
    for side in ("over", "under"):
        for sk in (False, True):
            sub = [r for r in rows if r["side"] == side and bool(r["skip"]) == sk and r["hit"] in ("W", "L")]
            w = sum(r["hit"] == "W" for r in sub)
            sh = [float(r["second_half"]) / float(r["line"]) for r in sub if r["second_half"] not in ("", None) and float(r["line"])]
            by_type.append({"type": f"{side}, {'skip' if sk else 'no skip'}", "n": len(sub), "w": w, "l": len(sub) - w,
                            "hit": round(100 * w / len(sub), 1) if sub else None,
                            "second_half_pct": round(100 * sum(sh) / len(sh)) if sh else None})
    return {"nights": [dict(v, games=len(v["games"])) for _, v in sorted(nights.items(), reverse=True)], "by_type": by_type,
            "n": len(rows), "decided_at_half": sum(r["hit"] == "decided at half" for r in rows)}


def snapshot_path(date):
    return os.path.join(HERE, "history", date, f"halftime_{date}.json")


def save(date, game, rows):
    path = snapshot_path(date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        snap = json.load(open(path))
    except (OSError, ValueError):
        snap = {}
    snap[game["id"]] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "game": game["name"], "score": game["score"],
                        "margin": game["margin"], "flags": rows}
    json.dump(snap, open(path, "w"), indent=1)


def report(date, game, rows):
    print(f"\nHALFTIME {game['name']}  {' - '.join(f'{t} {s}' for t, s in game['score'].items())}  (margin {game['margin']})"
          + ("  SKIP GAME: 20+ margin" if game["margin"] >= MARGIN_FLAG else ""))
    if not rows:
        print("  no player far off pace against a known pregame FanDuel line")
    for x in rows:
        print(f"  {x['side'].upper():5} {x['player']:22} {x['market']:4} line {x['line']:g}: 1st half {x['half']} "
              f"({round(100 * x['pace'])}% of line), {x['min']} min, {x['pf']} PF"
              + (f"   SKIP: {', '.join(x['skip'])}" if x["skip"] else ""))


def notify(msg):
    try:
        import subprocess
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg[:220])} with title "Halftime"'],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def run(date, watch=False, seen=None):
    lines = pregame_lines(date)
    seen = set(seen or [])
    try:
        done = set(json.load(open(snapshot_path(date))))
    except (OSError, ValueError):
        done = set()
    while True:
        gs = games(date)
        new = [g for g in gs if g["status"] == "STATUS_HALFTIME" and g["id"] not in done and g["id"] not in seen]
        for g in new:
            rows = off_pace(date, g, lines)
            save(date, g, rows)
            report(date, g, rows)
            live = [x for x in rows if not x["skip"]]
            notify(f"{g['name']} at halftime: {len(live)} off-pace props (paste live odds)")
            seen.add(g["id"])
        if not watch:
            if not new:
                print("No game at halftime right now." if not any(g["status"] == "STATUS_HALFTIME" for g in gs)
                      else "Halftime games already reported (see history/<date>/halftime_<date>.json).")
            return
        if new:
            print("\nExiting so the session is told; restart --watch for the next halftime.", flush=True)
            return
        left = [g for g in gs if g["status"] in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS") and (g["period"] or 0) <= 2]
        if not left:
            print("Every game is past halftime. Done.", flush=True)
            return
        time.sleep(120)


if __name__ == "__main__":
    d = next((a for a in sys.argv[1:] if re.match(r"\d{4}-\d{2}-\d{2}$", a)), et_today())
    if "--grade" in sys.argv:
        print(f"Halftime flags {d}: {grade_flags(d)} graded -> {FLAGS}")
    else:
        run(d, watch="--watch" in sys.argv)
