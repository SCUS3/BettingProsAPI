"""BettingPros' public Prop Bet Cheat Sheet page, pasted as text -> today's board CSV (0 API requests).

Each prop block on the page: "<Name>Logo for <TEAM>", name, position, "- AWY @ HOM", line, market, projection, side,
(win prob / rating / EV: premium, often hidden), opponent rank "23rd" (BettingPros: 1st = toughest), "O 24.5", "(-144)",
"Logo for <book>". The page's line and price come from whichever book it shows (Kalshi, Polymarket, FanDuel, ...); only
the projection and the opponent rank are used for the board, and FanDuel's prices come from Unabated as usual.

    python3 -m nbaprops.sources.bp_page PAGE.txt [--date YYYY-MM-DD]
"""
from __future__ import annotations

import csv
import os
import re
import sys

MARKETS = {frozenset({"pts"}): "PTS", frozenset({"reb"}): "REB", frozenset({"ast"}): "AST", frozenset({"3pts"}): "3PM",
           frozenset({"pts", "ast"}): "P+A", frozenset({"pts", "reb"}): "P+R", frozenset({"reb", "ast"}): "R+A",
           frozenset({"pts", "ast", "reb"}): "PRA"}
ESPN_TEAM = {"GSW": "GS", "PHO": "PHX", "NYK": "NY", "SAS": "SA", "NOP": "NO", "WAS": "WSH", "UTA": "UTAH", "BRK": "BKN"}
HEAD = re.compile(r"^(.+?)Logo for ([A-Z]{2,4})$")


def _market(txt):
    toks = frozenset(t.strip().lower() for t in txt.split("+"))
    return MARKETS.get(toks)


def parse(text: str) -> list[dict]:
    lines = [ln.strip() for ln in text.splitlines()]
    out, i = [], 0
    while i < len(lines):
        m = HEAD.match(lines[i])
        if not m or i + 7 >= len(lines) or not lines[i + 3].startswith("- ") or " @ " not in lines[i + 3]:
            i += 1
            continue
        name, team, game = lines[i + 1] or m.group(1), m.group(2), lines[i + 3][2:].strip()
        try:
            line, proj = float(lines[i + 4]), float(lines[i + 6])
        except ValueError:
            i += 1
            continue
        mk, side = _market(lines[i + 5]), lines[i + 7].lower()
        rank, pick_price, book = None, None, ""
        j = i + 8
        while j < len(lines) and not HEAD.match(lines[j]):
            if rank is None and re.fullmatch(r"\d{1,2}(st|nd|rd|th)", lines[j]):
                rank = int(re.match(r"\d+", lines[j]).group())
            pm = re.fullmatch(r"\(([+-]\d+)\)", lines[j])
            if pm:
                pick_price = float(pm.group(1))
            if lines[j].startswith("Logo for ") and pick_price is not None and not book:
                book = lines[j][9:].strip()
            j += 1
        if mk and side in ("over", "under"):
            out.append({"player": name, "team": team, "game": game, "market": mk, "line": line, "bp_proj": proj,
                        "bp_rec_side": side, "opp_rank_raw": rank, "dvp_softness": (31 - rank) if rank else None,
                        "price_book": book, "page_price": pick_price})
        i = j
    return out


RW_DTD = {"ques", "doubt", "gtd", "dtd", "questionable", "doubtful"}


def enrich(rows: list[dict], date: str) -> None:
    """Free extras for a page-built board: recent form from ESPN game logs (L5/L10/L20/season hit counts vs. the line)
    and RotoWire's lineups (starter, status, teammates out); RotoWire Questionable/Doubtful -> injury 'DTD'."""
    from nbaprops.rules import ledger as L
    from nbaprops.sources import espn as BX
    from nbaprops.sources import espn_form as EF
    from nbaprops.sources import rotowire as RW
    logs = EF.load(date, {r["player"]: r["team"] for r in rows})
    n_form = 0
    for r in rows:
        f = EF.form_fields(logs.get(BX.norm(r["player"])) or [], r["market"], r["rec_side"], float(r["line"]))
        if f:
            r.update(f)
            n_form += 1
    try:
        rw = RW.fetch()
    except Exception:  # noqa: BLE001
        rw = []
    if rw:
        L.save_rotowire(rw, date, rows[0]["pulled_at"] if rows else L.utc_now())   # same stamp as the board (re-score reloads it)
        L.add_teammate_context(rows, date, rw)
        for r in rows:
            if (r.get("rw_status") or "").strip().lower() in RW_DTD:
                r["injury"] = "DTD"
    print(f"Form (ESPN game logs) on {n_form} of {len(rows)} props; RotoWire lineups: "
          + (f"{len(rw)} players" if rw else "unavailable"))


def tips(date):
    """{frozenset(ESPN team abbrs): (tip ISO, status)} from ESPN's scoreboard (free)."""
    from nbaprops.rules import halftime as H
    out = {}
    for g in H.games(date):
        out[frozenset(g["score"].keys())] = g
    return out


def to_board(rows: list[dict], date: str) -> str:
    """Write bp_props_<date>.csv (projection rows; FanDuel prices left to Unabated). Games already started are dropped."""
    import datetime as dt

    from nbaprops.rules import ledger as L
    from nbaprops.sources import espn as BX
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    games = {}
    for e in sb.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        abbr = frozenset(c["team"]["abbreviation"] for c in comp.get("competitors", []))
        games[abbr] = (e["date"].replace("Z", ":00Z") if len(e["date"]) == 17 else e["date"], e.get("status", {}).get("type", {}).get("name"))
    now = L.utc_now()
    out = []
    for r in rows:
        a, h = [ESPN_TEAM.get(t.strip(), t.strip()) for t in r["game"].split("@")]
        tip, status = games.get(frozenset({a, h}), (None, None))
        if status and status != "STATUS_SCHEDULED":
            continue
        gap = r["bp_proj"] - r["line"]
        out.append({"player": r["player"], "team": r["team"], "injury": "", "market": r["market"], "rec_side": r["bp_rec_side"],
                    "line": r["line"], "fd_odds_rec": r["page_price"] if r["price_book"] == "FanDuel" else "",
                    "price_book": r["price_book"], "price_is_fanduel": r["price_book"] == "FanDuel", "bp_proj": r["bp_proj"],
                    "diff": round(gap, 2), "pct_gap": round(100 * gap / r["line"], 1) if r["line"] else None,
                    "bp_line": r["line"], "noproj": False, "bp_rec_side": r["bp_rec_side"], "bp_book": r["price_book"],
                    "opp_rank_raw": r["opp_rank_raw"], "dvp_softness": r["dvp_softness"], "event_id": "", "tip": tip or "",
                    "pulled_at": now, "lean_only": r["market"] not in ("3PM", "P+R"), "placeholder": False,
                    "source": "cheat sheet page"})
    enrich(out, date)
    path = os.path.join(L.HERE, f"bp_props_{date}.csv")
    fields = list(dict.fromkeys(k for r in out for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    _ = dt
    return path


if __name__ == "__main__":
    date = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else __import__("nbaprops.app.props", fromlist=["x"]).eastern_today()
    rows = parse(open(sys.argv[1]).read())
    path = to_board(rows, date)
    import collections
    print(f"{len(rows)} props on the page; board written to {path} "
          f"({sum(1 for _ in csv.DictReader(open(path)))} props for games not yet started).")
    print("markets:", dict(collections.Counter(r["market"] for r in rows)))
    print("games:", dict(collections.Counter(r["game"] for r in rows)))
