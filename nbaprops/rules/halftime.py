"""Halftime live experiment (research; own record). ESPN's public scoreboard and live box score only (free).

At halftime, lists players far off pace against their pregame FanDuel line:
  over candidates:  first-half stat <= 35% of the line
  under candidates: first-half stat >= 70% of the line
with fouls, minutes, the margin and skip flags (3+ fouls, 20+ point margin, injury mention in the play-by-play).
Then you paste live odds (bp_value.py --half-paste).

    python3 bp_halftime.py                 # games at halftime now
    python3 bp_halftime.py --watch         # poll ESPN every 2 min; alert at every halftime; runs until the last game's half
                                           # (so the session is told), or when every game is past halftime

Pregame FanDuel lines: today's saved pull (bp_props_<date>.csv), the day's value file, and the latest saved Unabated
board for the date (its FanDuel lines freeze at tip, i.e. the closing pregame line). No BettingPros requests.
"""
from nbaprops import config as C  # thresholds, caps and settings
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import time

from nbaprops.sources import espn as BX
from nbaprops.sources.rotowire import norm

from nbaprops.config import ROOT as HERE  # noqa: E402
OVER_PACE, UNDER_PACE = C.HALF_OVER_PACE, C.HALF_UNDER_PACE  # nbaprops/config.py
MIN_HALF_MIN = C.HALF_MIN_MINUTES  # nbaprops/config.py
MIN_LINE = C.HALF_MIN_LINE  # nbaprops/config.py
FOUL_FLAG, MARGIN_FLAG = C.HALF_FOUL_FLAG, C.HALF_MARGIN_FLAG  # nbaprops/config.py
PARTS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "P+A": ("PTS", "AST"),
         "P+R": ("PTS", "REB"), "PRA": ("PTS", "REB", "AST"), "R+A": ("REB", "AST")}


def et_today():
    """Today's ET date."""
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
        from nbaprops.sources import unabated as U
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
    """ESPN scoreboard for an ET date with status, period, score and margin."""
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    out = []
    for e in sb.get("events", []):
        st = e.get("status", {})
        comp = (e.get("competitions") or [{}])[0]
        score = {c["team"]["abbreviation"]: int(c.get("score") or 0) for c in comp.get("competitors", [])}
        out.append({"id": e["id"], "name": e.get("shortName"), "status": st.get("type", {}).get("name"),
                    "detail": st.get("type", {}).get("shortDetail") or "", "clock": st.get("displayClock"),
                    "full": " @ ".join(c.get("team", {}).get("displayName", "") for c in sorted(
                        comp.get("competitors", []), key=lambda c: c.get("homeAway") != "away")),
                    "period": st.get("period"), "score": score, "margin": abs(max(score.values()) - min(score.values())) if score else 0})
    return out


def half_box(game_id):
    """Live box score with first-half stats, fouls, minutes and an injury mention flag per player."""
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
                raw = lambda k: s[idx[k]] if k in idx and idx[k] < len(s) else ""
                players[norm(name)] = {"name": name, "team": team, "MIN": g("minutes"), "PTS": g("points"), "REB": g("rebounds"),
                                       "FG": raw("fieldGoalsMade-fieldGoalsAttempted"),
                                       "3PT": raw("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
                                       "AST": g("assists"), "3PM": g("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
                                       "PF": g("fouls"), "ejected": bool(a.get("ejected")), "injury": injury}
    return players


BUCKETS = [(0.0, 0.25, "<=25%"), (0.25, 0.5, "25-50%"), (0.5, 0.8, "50-80%"), (0.8, 1.0, "80-100%"), (1.0, 99, ">=100%")]
SHRINK_N = C.HALF_SHRINK_N  # nbaprops/config.py


def bucket(pace):
    """First-half pace bucket label for the expected-final shares."""
    return next(lbl for lo, hi, lbl in BUCKETS if (pace <= hi if lo == 0 else lo < pace <= hi) or (hi == 99 and pace > lo))


_shares = {}


def shares():
    """{(market, pace bucket): (raw mean 2nd-half share of the pregame line, n)} from graded snapshots + backfill."""
    if _shares:
        return _shares
    acc = {}
    try:
        for r in csv.DictReader(open(FLAGS)):
            if r.get("legacy") or r.get("second_half") in ("", None) or not float(r["line"]):
                continue
            k = (r["market"], bucket(float(r["pace"])))
            acc.setdefault(k, []).append(float(r["second_half"]) / float(r["line"]))
    except OSError:
        pass
    _shares.update({k: (sum(v) / len(v), len(v)) for k, v in acc.items()})
    return _shares


def expected_final(market, half, line):
    """first-half stat + base-rate 2nd-half share x pregame line (share shrunk toward 50% while n < SHRINK_N).
    -> (expected final, share used, n)."""
    raw, n = shares().get((market, bucket(half / line if line else 0)), (0.5, 0))
    w = min(n, SHRINK_N) / SHRINK_N
    sh = w * raw + (1 - w) * 0.5
    return round(half + sh * line, 1), round(sh, 3), n


def fair_live_line(exp):
    """The x.5 live line closest to the expected final (where over and under are roughly even)."""
    return round(exp - 0.5) + 0.5


def off_pace(date, game, lines):
    """Every player with a pregame FanDuel line: first-half stat, pace, minutes, fouls, margin and skip flags.
    alert = a strong flag (overs: <=25% of the line with 12+ minutes; unders: >=80% but still under the line;
    lines at or above MIN_LINE). Unders already over the line at half ('decided') are never alerts."""
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
            skip = [x for x, on in ((f"{p['PF']} fouls", (p["PF"] or 0) >= FOUL_FLAG),
                                    (f"margin {game['margin']}", game["margin"] >= MARGIN_FLAG),
                                    ("injury mention", p["injury"]), ("ejected", p["ejected"])) if on]
            big = line >= MIN_LINE.get(mk, 99)
            pace_flag = ("slow" if pace <= OVER_PACE and (p["MIN"] or 0) >= MIN_HALF_MIN and big else
                         "fast" if pace >= UNDER_PACE and v < line and big else "")
            side = ""                         # no reversal side: the side comes only from expected final vs. the live line
            exp, sh, n = expected_final(mk, v, line)
            out.append({"game": game["name"], "game_id": game["id"], "player": p["name"], "team": p["team"], "market": mk,
                        "side": side, "pace_flag": pace_flag, "alert": bool(pace_flag), "line": line, "half": v, "pace": round(pace, 2), "min": p["MIN"],
                        "pf": p["PF"], "margin": game["margin"], "skip": skip, "big_line": big,
                        "exp_final": exp, "exp_share": sh, "exp_n": n, "fair_live": fair_live_line(exp)})
    return out


def alerts(rows):
    """One alert per player (his most extreme strong flag; correlated props of the same player are folded in)."""
    best = {}
    ext = lambda x: x["pace"] if x.get("pace_flag", x.get("side")) in ("slow", "over") else -x["pace"]
    for x in rows:
        if x["alert"] and (x["player"] not in best or ext(x) < ext(best[x["player"]])):
            best[x["player"]] = x
    for x in rows:                                   # rows not chosen stay in the snapshot but aren't alerts
        if x["alert"] and best.get(x["player"]) is not x:
            x["alert"] = False
            x["folded_into"] = f"{best[x['player']]['market']}"
    return sorted(best.values(), key=lambda x: (bool(x["skip"]), x.get("pace_flag", ""), ext(x)))


FLAGS = os.path.join(HERE, "halftime_flags.csv")
FLAG_FIELDS = ["date", "game_id", "game", "flagged_at", "player", "team", "market", "side", "alert", "big_line", "line", "half",
               "pace", "min", "pf", "margin", "skip", "final", "finished", "second_half", "hit", "legacy", "backfill"]


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
        old = [r for r in csv.DictReader(open(FLAGS)) if r["date"] != date or r.get("backfill") == "yes"]
    except OSError:
        old = []
    new = []
    for gid, g in snap.items():
        legacy = "rows" not in g                     # early snapshots kept only the old, looser flags
        for x in g.get("rows", g.get("flags", [])):
            p = BX.lookup(box, x["player"]) if box else None
            fin = BX.stat(p, x["market"]) if p else None
            row = {"date": date, "game_id": gid, "game": g.get("game"), "flagged_at": g.get("at"), "player": x["player"],
                   "team": x.get("team"), "market": x["market"], "side": x["side"], "line": x["line"], "half": x["half"],
                   "pace": x["pace"], "min": x.get("min"), "pf": x.get("pf"), "margin": x.get("margin"),
                   "skip": "; ".join(x.get("skip") or []), "final": "", "finished": "", "second_half": "", "hit": "",
                   "alert": ("" if legacy else "legacy reversal" if x.get("legacy_reversal") else
                             "yes" if x.get("alert") else "no"), "big_line": x.get("big_line", ""),
                   "legacy": "yes" if legacy else ""}
            if fin is not None:
                row.update(final=fin, second_half=fin - x["half"],
                           finished="over" if fin > x["line"] else "under" if fin < x["line"] else "push",
                           hit="" if fin == x["line"] else ("W" if (fin > x["line"]) == (x["side"] == "over") else "L"))
            if legacy and x["side"] == "under" and x["half"] > x["line"]:
                row["hit"] = "decided at half"            # already over the pregame line: not a live question
            row["over_hit"] = "" if fin is None or fin == x["line"] else ("W" if fin > x["line"] else "L")
            new.append(row)
    with open(FLAGS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FLAG_FIELDS + ["over_hit"], extrasaction="ignore")
        w.writeheader()
        w.writerows(old + new)
    return sum(1 for r in new if r["hit"])


def flag_summary(version="v2"):
    """Halftime record: snapshots per night, and how accurate the expected final is (average miss and bias, by market
    and pace bucket), scored out of sample: each night's rows use 2nd-half shares from the other nights only."""
    try:
        from nbaprops.rules import ledger as bp_learn
        rows = [r for r in csv.DictReader(open(FLAGS)) if not r.get("legacy") and bp_learn.in_version(r, version)]
    except OSError:
        rows = []
    live_rows = [r for r in rows if r.get("backfill") != "yes"]
    bf = [r for r in rows if r.get("backfill") == "yes"]
    nights = {}
    for r in live_rows:
        n = nights.setdefault(r["date"], {"date": r["date"], "players": 0, "alerts": 0, "legacy_reversal": 0, "games": set()})
        n["players"] += 1
        n["alerts"] += r["alert"] == "yes"
        n["legacy_reversal"] += r["alert"] == "legacy reversal"
        n["games"].add(r["game_id"])
    graded = [r for r in rows if r.get("final") not in ("", None) and r.get("line") and float(r["line"])]
    by_date = {}
    for r in graded:
        k = (r["market"], bucket(float(r["pace"])))
        by_date.setdefault(r["date"], {}).setdefault(k, []).append(float(r["second_half"]) / float(r["line"]))
    acc = {}
    for r in graded:
        k = (r["market"], bucket(float(r["pace"])))
        other = [v for d, m in by_date.items() if d != r["date"] for v in m.get(k, [])]
        w = min(len(other), SHRINK_N) / SHRINK_N
        sh = w * (sum(other) / len(other) if other else 0.5) + (1 - w) * 0.5
        err = float(r["final"]) - (float(r["half"]) + sh * float(r["line"]))
        acc.setdefault(k, []).append(err)
        acc.setdefault((r["market"], "all"), []).append(err)
    table = []
    for mk in PARTS:
        for _, _, b in BUCKETS + [(0, 0, "all")]:
            e = acc.get((mk, b))
            if e:
                table.append({"market": mk, "bucket": b, "n": len(e), "miss": round(sum(abs(x) for x in e) / len(e), 2),
                              "bias": round(sum(e) / len(e), 2)})
    return {"nights": [dict(v, games=len(v["games"])) for _, v in sorted(nights.items(), reverse=True)], "accuracy": table,
            "n": len(rows), "backfill": {"rows": len(bf), "games": len({r["game_id"] for r in bf}),
                                         "dates": sorted({r["date"] for r in bf})}}


def snapshot_path(date):
    """Path of the date's halftime snapshot JSON."""
    return os.path.join(HERE, "history", date, f"halftime_{date}.json")


def save(date, game, rows):
    """Save a game's halftime snapshot (every player with a pregame line)."""
    path = snapshot_path(date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        snap = json.load(open(path))
    except (OSError, ValueError):
        snap = {}
    snap[game["id"]] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "game": game["name"], "score": game["score"],
                        "margin": game["margin"], "rows": rows}
    json.dump(snap, open(path, "w"), indent=1)


def bp_pregame(date, player, market, line=None):
    """BettingPros' pregame projection for player/market, with the side it favors vs. the pregame line ('🟢O' over,
    '🟢U' under; halftime flags have no side yet), '–' if none."""
    try:
        from nbaprops.pricing import samscore as SS
        v = SS._num(SS.board_row(date, player, market).get("bp_proj"))
        if v is None:
            return "–"
        lean = "" if line is None or v == float(line) else (" 🟢O" if v > float(line) else " 🟢U")
        return f"{v:.1f}{lean}"
    except Exception:  # noqa: BLE001
        return "–"


def dvp_both(date, player, market):
    """Side-aware DvP for a flag with no side yet: '8 🟢O 🔴U' (soft), '26 🔴O 🟢U' (hard), '15' (neutral), '–'."""
    from nbaprops.pricing import projctx as P
    try:
        d = P.dvp_of(date, player, market)
    except Exception:  # noqa: BLE001
        d = None
    if d is None:
        return "–"
    eff = P.dvp_effect("over", d)
    return f"{d:g}" + (" 🟢O 🔴U" if eff == "helps" else " 🔴O 🟢U" if eff == "hurts" else "")


def report(date, game, rows):
    """Print a game's halftime alerts."""
    print(f"\nHALFTIME {game['name']}  {' - '.join(f'{t} {s}' for t, s in game['score'].items())}  (margin {game['margin']})"
          + ("  SKIP GAME: 20+ margin" if game["margin"] >= MARGIN_FLAG else ""))
    if not rows:
        print("  no strong pace flags (<=25% with 12+ min, or >=80% still under; minimum lines)")
    for x in rows:
        X = x.get("fair_live")
        bp = bp_pregame(date, x["player"], x["market"], x["line"])
        print(f"  {x['player']:22} {x['market']:4} pregame {x['line']:g} (BP proj {bp}, DvP {dvp_both(date, x['player'], x['market'])}): 1st half {x['half']} ({round(100 * x['pace'])}%, "
              f"{x['pace_flag']}), {x['min']} min, {x['pf']} PF; expected final {x.get('exp_final')} (n {x.get('exp_n')})"
              f"  -> lean: OVER if live line below {X}, UNDER if above {X}"
              + (f"   SKIP: {', '.join(x['skip'])}" if x["skip"] else ""))

def notify(msg):
    """macOS notification (best effort)."""
    try:
        import subprocess
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg[:220])} with title "Halftime"'],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def run(date, watch=False, seen=None):
    """Report games at halftime now. With watch=True, poll ESPN every 2 minutes and handle every halftime as it
    comes (snapshot, alerts, Mac notification, one 'HALFTIME ALERT' line per game) until every game is past
    halftime; a failed ESPN check is retried at the next poll. Never needs restarting."""
    lines = pregame_lines(date)
    seen = set(seen or [])
    try:
        done = set(json.load(open(snapshot_path(date))))
    except (OSError, ValueError):
        done = set()
    if watch:
        try:                                          # keep the Mac awake while watching (sleep would skip halftimes)
            import subprocess
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            pass
    while True:
        try:
            gs = games(date)
        except Exception as e:
            if not watch:
                raise
            print(f"[{time.strftime('%H:%M')}] ESPN check failed ({e}); retrying in 2 min.", flush=True)
            time.sleep(120)
            continue
        new = [g for g in gs if g["status"] == "STATUS_HALFTIME" and g["id"] not in done and g["id"] not in seen]
        for g in new:
            try:
                rows = off_pace(date, g, lines)
            except Exception as e:
                print(f"[{time.strftime('%H:%M')}] {g['name']} at halftime: box score unavailable ({e}); retrying.", flush=True)
                continue
            al = alerts(rows)
            save(date, g, rows)
            report(date, g, al)
            print(f"  ({len(rows)} player props snapshotted for the base rate)")
            live = [x for x in al if not x["skip"]]
            msg = (f"{g['name']} at halftime: {len(live)} strong flags (paste live odds)" if live else
                   f"{g['name']} at halftime: no strong flags")
            notify(msg)
            print(f"[{time.strftime('%H:%M')}] HALFTIME ALERT {msg}" +
                  "".join(f" | {x['player']} {x['market']} exp {x.get('exp_final')} (fair live ~{x.get('fair_live')})" for x in live),
                  flush=True)
            seen.add(g["id"])
        if not watch:
            if not new:
                print("No game at halftime right now." if not any(g["status"] == "STATUS_HALFTIME" for g in gs)
                      else "Halftime games already reported (see history/<date>/halftime_<date>.json).")
            return
        left = [g for g in gs if g["id"] not in done | seen and
                (g["status"] in ("STATUS_SCHEDULED", "STATUS_HALFTIME") or
                 (g["status"] == "STATUS_IN_PROGRESS" and (g["period"] or 0) <= 2))]
        if not left:
            print(f"[{time.strftime('%H:%M')}] Every game is past halftime. Watch done.", flush=True)
            return
        time.sleep(120)


if __name__ == "__main__":
    d = next((a for a in sys.argv[1:] if re.match(r"\d{4}-\d{2}-\d{2}$", a)), et_today())
    if "--grade" in sys.argv:
        print(f"Halftime flags {d}: {grade_flags(d)} graded -> {FLAGS}")
    else:
        run(d, watch="--watch" in sys.argv)
