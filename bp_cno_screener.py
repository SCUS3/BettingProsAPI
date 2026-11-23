"""CrazyNinja +EV screener paste -> its own "CNO screener" record (no API calls).

    python3 bp_value.py --cno-paste screener.txt        # or "-" to read stdin
    python3 bp_value.py --cno-paste screener.txt --dry  # parse and show, log nothing

The paste can be the screener table copied from the browser: tab-separated rows, or one play per block of lines
(blank line between plays). Each play needs a recognised NBA player, a prop (points, rebounds, assists, threes,
P+A, P+R, PRA), Over/Under and a line, FanDuel's American odds and an EV%. Fair odds or a fair % are used when
present; otherwise the fair probability is backed out of EV% and FanDuel's price. Rows for other sports, other
books, game lines or alternate/unknown props are dropped and counted.

Logged as strategy "cno_scr" at ¼K on the fair probability with the same caps (1.25u per BET until 50 graded BETs
show positive CLV; 1.5u per player, 3.5u per game, counted within this record). Graded from box scores and given
closing CLV by the watch like the other records. Plays already logged for the day aren't logged twice.
"""
import csv
import glob
import os
import re

import bp_learn as L
from bp_rotowire import norm

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = "cno_scr"
KEY_SUB = "cno_scr_sub"     # logged with --all: +EV but under the +2% bar (tracking only, kept out of the +2% record)

# longest phrases first: combos before singles
MARKETS = [
    (r"pts\s*\+\s*reb\s*\+\s*ast|points\s*\+\s*rebounds\s*\+\s*assists|p\s*\+\s*r\s*\+\s*a|\bpra\b", "PRA"),
    (r"pts\s*\+\s*ast|points\s*\+\s*assists|p\s*\+\s*a\b", "P+A"),
    (r"pts\s*\+\s*reb|points\s*\+\s*rebounds|p\s*\+\s*r\b", "P+R"),
    (r"reb\s*\+\s*ast|rebounds\s*\+\s*assists|r\s*\+\s*a\b", "R+A"),
    (r"three[\s-]*point(?:er)?s?(?:\s*made)?|threes|3[\s-]*pt(?:s)?(?:\s*made)?|3pm|3-pointers", "3PM"),
    (r"\bpoints\b|\bpts\b", "PTS"),
    (r"\brebounds\b|\breb\b", "REB"),
    (r"\bassists\b|\bast\b", "AST"),
]
SKIP_WORDS = r"alt(?:ernate)?\b|milestone|1st\s*(?:half|quarter)|1h\b|1q\b|first\s*(?:half|quarter)|double[\s-]*double|triple|steals|blocks|turnovers|moneyline|spread|total\s+points"
AMER = re.compile(r"(?<![\w.])([+-]\d{3,5})(?![\d.%])")
EVP = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
SIDE = re.compile(r"\b(over|under|o|u)\s*(\d+(?:\.\d)?)\b", re.I)


def known_players(date):
    names = set()
    for path in [os.path.join(HERE, f"bp_props_{date}.csv"), L.LEDGER, L.GRADED] + glob.glob(os.path.join(HERE, "bp_props_*.csv"))[-5:]:
        try:
            for r in csv.DictReader(open(path)):
                if r.get("player"):
                    names.add(r["player"])
        except OSError:
            pass
    try:
        import bp_unabated as U
        d = U.load(U.DISCOVERY, {})
        for p in U._items((d.get("raw") or {}).get("people")):
            nm = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
            if nm:
                names.add(nm)
    except Exception:
        pass
    return sorted(names, key=lambda n: -len(n))


def _records(text):
    lines = [ln.rstrip() for ln in text.splitlines()]
    tabbed = [ln for ln in lines if "\t" in ln]
    if len(tabbed) >= max(1, len([ln for ln in lines if ln.strip()]) // 2):
        return [ln.replace("\t", " | ") for ln in lines if ln.strip()]
    blocks, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            blocks.append(" | ".join(cur))
            cur = []
    if cur:
        blocks.append(" | ".join(cur))
    full = [ln for ln in lines if ln.strip()]
    if len(full) >= 2 and sum(1 for ln in full if EVP.search(ln) and AMER.search(ln)) >= max(2, len(full) - 1):
        return full                                            # one play per line (each has odds and EV%)
    return blocks


def _dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


MIN_EV = 0.02        # same bar as our other +EV records
ALT_GAP = 2.0        # a screener line this far from the pull's FanDuel main line is an alternate line (dropped)
# The user's fixed screener settings: rows that break them are dropped.
FIXED = {"devig": "LW-M", "min_books": 4, "max_odds": 200.0, "book": "FanDuel", "league": "NBA"}
PASTE_LOG = os.path.join(HERE, "cno_screener_log.json")


def ev_header(text):
    """The screener's EV column header (it names the devig method, e.g. 'LW-M EV%'), or None."""
    for ln in text.splitlines():
        if "bet name" in ln.lower() and "sportsbook" in ln.lower():
            h = re.split(r"\t|\s{2,}", ln.strip())[0]
            return h if "ev" in h.lower() else None
    return None


def page_settings(text):
    """Settings shown in a full-page paste (Devig Method, Minimum Books, Maximum Odds), or {}."""
    out, lines = {}, [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        nxt = next((x for x in lines[i + 1:i + 3] if x), "")
        if ln.startswith("Devig Method") and nxt:
            out["devig"] = "LW-M" if "liquidity-weighted" in nxt.lower() and "multiplicative" in nxt.lower() else nxt
        elif ln.startswith("Minimum Books") and re.match(r"^\d+$", nxt):
            out["min_books"] = int(nxt)
        elif ln.startswith("Maximum Odds") and re.match(r"^[+-]?\d+$", nxt):
            out["max_odds"] = float(nxt)
    return out


def settings(text, plays, devig=None, min_books=None, max_odds=None):
    """This paste's screener settings (stated by flags, else the last paste's) + what the rows show; warnings when
    they differ from the last paste or the rows break them. Appends to cno_screener_log.json."""
    import json
    pg = page_settings(text)                         # a full-page paste shows the screener's own settings
    devig = devig if devig is not None else pg.get("devig")
    min_books = min_books if min_books is not None else pg.get("min_books")
    max_odds = max_odds if max_odds is not None else pg.get("max_odds")
    try:
        hist = json.load(open(PASTE_LOG))
    except (OSError, ValueError):
        hist = []
    last = hist[-1] if hist else {}
    fallback = lambda k: last.get(k) if last.get(k) is not None else FIXED[k]   # no settings shown: same as last paste
    st = {"at": L.utc_now(), "ev_header": ev_header(text),
          "devig": devig if devig is not None else fallback("devig"),
          "min_books": min_books if min_books is not None else fallback("min_books"),
          "max_odds": max_odds if max_odds is not None else fallback("max_odds"),
          "assumed": [k for k, v in (("devig", devig), ("min_books", min_books), ("max_odds", max_odds)) if v is None],
          "rows_min_books": min((p["books"] for p in plays if p.get("books") is not None), default=None),
          "rows_max_odds": max((p["odds"] for p in plays), default=None)}
    warn = []
    shown = {k for k, v in (("devig", devig), ("min_books", min_books), ("max_odds", max_odds)) if v is not None} | {"ev_header"}
    for k in ("ev_header", "devig", "min_books", "max_odds"):
        if k in shown and last and last.get(k) is not None and st.get(k) is not None and str(last.get(k)) != str(st.get(k)):
            warn.append(f"{k} changed: {last.get(k)} -> {st.get(k)}")
    if st["min_books"] is not None and st["rows_min_books"] is not None and st["rows_min_books"] < int(st["min_books"]):
        warn.append(f"a row has {st['rows_min_books']} books, under your min books {st['min_books']}")
    if st["max_odds"] is not None and st["rows_max_odds"] is not None and st["rows_max_odds"] > float(st["max_odds"]):
        warn.append(f"a row is priced {st['rows_max_odds']:+g}, over your max odds {float(st['max_odds']):+g}")
    if (st["devig"], st["min_books"], float(st["max_odds"])) != (FIXED["devig"], FIXED["min_books"], FIXED["max_odds"]):
        warn.append(f"screener page set to devig {st['devig']}, min books {st['min_books']}, max odds {float(st['max_odds']):+g}; "
                    f"your fixed settings (devig {FIXED['devig']}, min books {FIXED['min_books']}, max odds +{FIXED['max_odds']:g}) "
                    f"are applied, so rows outside them are dropped")
    if st["ev_header"] and "lw-m" not in st["ev_header"].lower():
        warn.append(f"EV column is '{st['ev_header']}', not LW-M: check the screener's devig setting")
    st["warnings"] = warn
    hist.append(st)
    json.dump(hist[-200:], open(PASTE_LOG, "w"), indent=1)
    return st


def settings_text(st):
    return (f"devig {st.get('devig') or '?'} (EV column '{st.get('ev_header') or '?'}'), min books {st.get('min_books') or '?'}, "
            f"max odds {('%+g' % float(st['max_odds'])) if st.get('max_odds') not in (None, '') else '?'}")


def _when(txt, date):
    """'Today at 8:00 PM' / 'Tomorrow at 7:30 PM' / 'Nov 23 at 7:00 PM' (ET) -> UTC ISO tip, or None."""
    import datetime as dt
    m = re.search(r"(today|tomorrow|[A-Z][a-z]{2}\s+\d{1,2})\s+at\s+(\d{1,2}):(\d{2})\s*([AP]M)", txt or "", re.I)
    if not m:
        return None
    base = dt.date.fromisoformat(date)
    day = m.group(1).lower()
    if day == "tomorrow":
        base += dt.timedelta(days=1)
    elif day != "today":
        try:
            base = dt.datetime.strptime(f"{m.group(1)} {base.year}", "%b %d %Y").date()
        except ValueError:
            return None
    h = int(m.group(2)) % 12 + (12 if m.group(4).upper() == "PM" else 0)
    t = dt.datetime.combine(base, dt.time(h, int(m.group(3))))
    try:
        from zoneinfo import ZoneInfo
        t = t.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)
    except Exception:
        t = (t + dt.timedelta(hours=5)).replace(tzinfo=dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _table(text):
    """Header-aware: the screener's own table (tab or 2+ space separated, header with 'Bet Name' and 'Sportsbook')."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    split = lambda ln: [c.strip() for c in re.split(r"\t|\s{2,}", ln.strip())]
    hi = next((i for i, ln in enumerate(lines) if "bet name" in ln.lower() and "sportsbook" in ln.lower()), None)
    if hi is None:
        return None
    head = [h.lower() for h in split(lines[hi])]
    col = lambda *names: next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    idx = {"ev": col("ev%"), "date": col("date"), "league": col("league"), "sport": col("sport"), "event": col("event"),
           "market": col("market"), "bet": col("bet name"), "odds": head.index("odds") if "odds" in head else col("odds"),
           "book": col("sportsbook"), "fair": col("fair"), "books": col("books")}
    out = []
    for ln in lines[hi + 1:]:
        cells = split(ln)
        n_missing = len(head) - len(cells)     # blank cells (Calc, Extra) collapse when split on spaces
        get = {}
        for k, i in idx.items():
            if i is None:
                continue
            j = i - (n_missing if i > col("ev%") and n_missing > 0 else 0)
            get[k] = cells[j] if 0 <= j < len(cells) else ""
        out.append(get)
    return out


def parse(text, date, min_ev=None):
    """-> (plays, dropped {reason: n}). play = {player, market, side, line, odds, ev, fair, books, tip, raw}."""
    tbl = _table(text)
    if tbl is not None:
        return _parse_table(tbl, date, MIN_EV if min_ev is None else min_ev)
    return _parse_loose(text, date)


def fd_main_lines(date):
    """{(norm player, market): FanDuel main line} from today's pull (FanDuel-priced rows only)."""
    out = {}
    try:
        for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
            if r.get("price_is_fanduel") == "True" and r.get("line"):
                out[(norm(r["player"]), r["market"])] = float(r["line"])
    except OSError:
        pass
    return out


def _parse_table(tbl, date, min_ev=MIN_EV):
    names = {norm(n): n for n in known_players(date)}
    mains = fd_main_lines(date)
    now = L.utc_now()
    plays, dropped = [], {}
    drop = lambda why: dropped.__setitem__(why, dropped.get(why, 0) + 1)
    for g in tbl:
        if (g.get("league") or g.get("sport", "")).upper().find("NBA") < 0:
            drop("not NBA")
            continue
        if "fanduel" not in (g.get("book") or "").lower():
            drop("not FanDuel")
            continue
        mk_txt = (g.get("market") or "").lower()
        if re.search(SKIP_WORDS, mk_txt) or not mk_txt.startswith("player"):
            drop("not a main player prop")
            continue
        mk = next((m for pat, m in MARKETS if re.search(pat, mk_txt)), None)
        sm = SIDE.search(g.get("bet") or "")
        if not mk or not sm:
            drop("prop not recognised")
            continue
        player_txt = (g.get("bet") or "")[:sm.start()].strip()
        player = names.get(norm(player_txt), player_txt)
        main = mains.get((norm(player), mk))
        line_note = ""
        if main is not None and abs(float(sm.group(2)) - main) >= ALT_GAP:
            drop(f"alt line (FanDuel main {main:g} at the pull)")
            continue
        if main is not None and float(sm.group(2)) != main:
            line_note = f"line differs from the pull's FanDuel main {main:g} (moved, or an alt)"
        try:
            odds = float(g.get("odds"))
            ev = float(str(g.get("ev")).rstrip("%")) / 100
        except (TypeError, ValueError):
            drop("no FanDuel odds or EV%")
            continue
        try:
            fair = 1 / _dec(float(g.get("fair")))
        except (TypeError, ValueError):
            fair = (1 + ev) / _dec(odds)
        tip = _when(g.get("date"), date)
        if tip and tip <= now:
            drop("game already started")
            continue
        if ev < min_ev or ev <= 0:
            drop(f"EV below +{100 * min_ev:g}%")
            continue
        try:
            books = int(g.get("books"))
        except (TypeError, ValueError):
            books = None
        if books is not None and books < FIXED["min_books"]:
            drop(f"under {FIXED['min_books']} books")
            continue
        if odds > FIXED["max_odds"]:
            drop(f"odds over +{FIXED['max_odds']:g}")
            continue
        plays.append({"player": player, "market": mk, "side": "over" if sm.group(1).lower().startswith("o") else "under",
                      "line": float(sm.group(2)), "odds": odds, "ev": round(ev, 4), "fair": round(fair, 4), "books": books,
                      "tip": tip, "event": g.get("event"), "line_note": line_note,
                      "raw": " | ".join(f"{k}={v}" for k, v in g.items())[:200]})
    return _with_proj(plays, date), dropped


def _with_proj(plays, date):
    import bp_projctx as P
    for p in plays:
        c = P.context(date, p["player"], p["market"], p["side"], p["line"])
        p.update(proj_note=c["text"], proj_tag=c["tag"])
    return plays


def _parse_loose(text, date):
    names = known_players(date)
    nmap = {norm(n): n for n in names}
    plays, dropped = [], {}
    drop = lambda why: dropped.__setitem__(why, dropped.get(why, 0) + 1)
    for rec in _records(text):
        low = rec.lower()
        if re.search(r"\b(nfl|ncaa[fb]?|cbb|cfb|mlb|nhl|wnba|soccer|tennis|ufc|mma|euroleague)\b", low):
            drop("not NBA")
            continue
        if "fanduel" not in low and not re.search(r"\bfd\b", low):
            drop("not FanDuel")
            continue
        if re.search(SKIP_WORDS, low):
            drop("not a main player prop")
            continue
        mk = next((m for pat, m in MARKETS if re.search(pat, low)), None)
        if not mk:
            drop("prop not recognised")
            continue
        nrec = norm(rec)
        player = next((nmap[k] for k in nmap if k and re.search(rf"\b{re.escape(k)}\b", nrec)), None)
        if not player:
            drop("player not recognised")
            continue
        sm = SIDE.search(rec)
        if not sm:
            drop("no over/under line")
            continue
        side = "over" if sm.group(1).lower().startswith("o") else "under"
        line = float(sm.group(2))
        ev = EVP.search(rec)
        odds = [float(x) for x in AMER.findall(rec)]
        if not odds or not ev:
            drop("no FanDuel odds or EV%")
            continue
        fd = odds[0]
        evv = float(ev.group(1)) / 100
        fair = None
        if len(odds) > 1:                                       # second American price = fair odds
            fair = 1 / _dec(odds[1])
        fp = re.search(r"fair[^|%]*?(\d{1,2}(?:\.\d+)?)\s*%", low)
        if fair is None and fp:
            fair = float(fp.group(1)) / 100
        if fair is None:
            fair = (1 + evv) / _dec(fd)
        bm = re.search(r"(\d+)\s*books?\b|books?\s*[:=]?\s*(\d+)", low)
        books = int(bm.group(1) or bm.group(2)) if bm else None
        plays.append({"player": player, "market": mk, "side": side, "line": line, "odds": fd, "ev": round(evv, 4),
                      "fair": round(fair, 4), "books": books, "raw": rec[:200]})
    return _with_proj(plays, date), dropped


def log(plays, date, st=None, key=KEY):
    """Log new plays as 'cno_scr' rows (¼K on fair prob, same caps). Returns rows logged."""
    import bp_value as V
    led = L._read(L.LEDGER)
    k = lambda r: (r["player"], r["market"], r["side"], str(V._f(r.get("line"))))
    have = {k(r) for r in led if r["date"] == date and r["strategy"] in (KEY, KEY_SUB)}   # one record per play per day
    board = {}
    try:
        for r in csv.DictReader(open(os.path.join(HERE, f"bp_props_{date}.csv"))):
            board.setdefault(r["player"], r)
    except OSError:
        pass
    now = L.utc_now()
    new = [p for p in sorted(plays, key=lambda p: -p["ev"]) if (p["player"], p["market"], p["side"], str(float(p["line"]))) not in have]
    rows_ctx = [dict(board.get(p["player"], {}), player=p["player"]) for p in new]
    gkey = V.game_resolver(rows_ctx + [r for r in led if r["date"] == date])
    cap = V.bet_cap()[0]
    logged = [r for r in led if r["date"] == date and r["strategy"] == key]
    cands = V.apply_exposure([{"player": p["player"], "game": gkey(c), "ev": p["ev"],
                               "stake": V.bet_kelly(p["fair"], p["odds"], cap)} for p, c in zip(new, rows_ctx)], logged, gkey)
    out = []
    for p, c, x in zip(new, rows_ctx, cands):
        if x["stake"] <= 0:
            continue
        out.append({"date": date, "pulled_at": now, "tip": p.get("tip") or c.get("tip") or "", "strategy": key, "rank": None,
                    "player": p["player"], "team": c.get("team"), "market": p["market"], "side": p["side"], "line": p["line"],
                    "odds": p["odds"], "our_prob": p["fair"], "nv_prob": p["fair"], "kelly_u": x["stake"],
                    "event_id": c.get("event_id") or "", "oa_fair": p["fair"], "oa_ev": p["ev"], "fair_src": "CNO screener",
                    "sources": p["books"], "cap_note": x["cap"], "proj_tag": p.get("proj_tag"),
                    "proj_note": p.get("proj_note"), "scr_settings": settings_text(st) if st else ""})
    if out:
        L._write(L.LEDGER, led + out, L.LEDGER_FIELDS)
    return out


def live_compare(text, date):
    """Live screener rows (games under way) vs. both projection models: BettingPros' pregame projection/lean and the
    halftime expected final (snapshot), plus the stat now (ESPN, free). Display only; nothing is logged."""
    import json
    import bp_halftime as H
    import bp_projctx as P
    tbl = _table(text) or []
    snap = {}
    try:
        for g in json.load(open(H.snapshot_path(date))).values():
            for x in g.get("rows", []):
                snap[(norm(x["player"]), x["market"])] = (x, g["game"])
    except (OSError, ValueError):
        pass
    live = {}
    try:
        for g in H.games(date):
            if g["status"] not in ("STATUS_SCHEDULED", "STATUS_FINAL"):
                live[g["name"]] = (g, H.half_box(g["id"]))
    except Exception:
        pass
    out, skipped = [], 0
    for t in tbl:
        if "NBA" not in (t.get("league") or "") or "fanduel" not in (t.get("book") or "").lower():
            skipped += 1
            continue
        mk = next((m for pat, m in MARKETS if re.search(pat, (t.get("market") or "").lower())), None)
        sm = SIDE.search(t.get("bet") or "")
        if not mk or not sm:
            skipped += 1
            continue
        pl, side, line = t["bet"][:sm.start()].strip(), "over" if sm.group(1).lower().startswith("o") else "under", float(sm.group(2))
        c = P.context(date, pl, mk, side, line)
        sx = snap.get((norm(pl), mk))
        if sx:
            x, gname = sx
            es = "over" if x["exp_final"] > line else "under" if x["exp_final"] < line else "even"
            ht = f"{x['half']} at half (pregame {x['line']:g}) -> {x['exp_final']}, {es}"
            htag = "agrees" if es == side else "disagrees"
            g, box = live.get(gname, (None, {}))
            p = box.get(norm(pl))
            now = (f"{sum(p[k] or 0 for k in H.PARTS[mk])} ({g['status'].replace('STATUS_', '').lower().replace('_', ' ')}, Q{g['period']})"
                   if p and g else "final" if gname not in live else "-")
        else:
            ht, htag, now = "no halftime snapshot", "none", "-"
        out.append({"player": pl, "market": mk, "side": side, "line": line, "odds": t.get("odds"), "ev": t.get("ev"),
                    "books": t.get("books"), "bp": c["text"], "bp_tag": c["tag"], "half": ht, "half_tag": htag, "now": now})
    return out, skipped
