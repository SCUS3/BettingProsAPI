"""Grading: final stats (BettingPros scoring or public box scores), W/L/P/void per prop and ledger pick, units,
placed bets, and which slates still need grading.

Ledger state (LEDGER, GRADED, PLACED paths) is read through the ledger module at call time (``L.LEDGER``), so a sim
that points the ledger at its own files grades its own files.
"""
import glob
import os

from nbaprops.config import ROOT as HERE
from nbaprops.rules import ledger as L


def grade(side, line, actual):
    """'W', 'L' or 'P' for the recommended side, None if not gradable."""
    line, actual = L._num(line), L._num(actual)
    if line is None or actual is None or side not in ("over", "under"):
        return None
    if actual == line:
        return "P"
    return "W" if (actual > line) == (side == "over") else "L"


def profit(odds, result, stake=1.0):
    if result == "W":
        d = L.decimal(odds)
        return round(stake * (d - 1), 3) if d else None
    if result == "L":
        return -stake
    if result == "P":
        return 0.0
    return None


def pending_dates(today):
    """Slates before `today` that still have ungraded ledger rows or no graded props."""
    graded_dates = {r["date"] for r in L._read(L.GRADED)}
    open_ledger = {r["date"] for r in L._read(L.LEDGER) if not r.get("result")}
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "bp_props_*.csv"))):
        d = os.path.basename(path)[9:-4]
        if d < today and (d not in graded_dates or d in open_ledger):
            out.append(d)
    return out


def finals_from(fetched, labels):
    """{L.prop_key: final stat} from pulled props that are scored."""
    out = {}
    for p in fetched:
        sc = p.get("scoring") or {}
        if not sc.get("is_scored"):
            continue
        part = p.get("participant") or {}
        mk = labels.get(p.get("market_id"), p.get("market_id"))
        out[L.prop_key(p.get("event_id"), mk, part.get("name"))] = sc.get("actual")
    return out


def known_finals(date):
    """Final stats already saved for `date` (graded props and graded picks), so regrades skip re-pulling them."""
    out = {}
    for path in (L.GRADED, L.LEDGER):
        for r in L._read(path):
            if r["date"] == date and r.get("actual") not in (None, ""):
                out[L.prop_key(r.get("event_id"), r.get("market"), r.get("player"))] = r["actual"]
    return out


def _gradable(rows):
    return [r for r in rows if L._truthy(r.get("price_is_fanduel")) and r.get("score")]


def events_without_finals(date, finals):
    """Games on the pre-tip slate with no final stat at all yet (they need the full slate pull)."""
    have = {k.split("|", 1)[0] for k in finals}
    return sorted({r.get("event_id") for r in _gradable(L.pre_tip_rows(date))} - have)


def missing_pick_markets(date, finals):
    """(event_id, market) pairs of ungraded picks whose final stat isn't known yet. The full pull is
    filtered to FanDuel, and FanDuel often takes a line down before the game ends, so these are looked
    up one game and market at a time with no book filter."""
    out = []
    for r in L._read(L.LEDGER):
        if r["date"] == date and not r.get("result") and \
                L.prop_key(r.get("event_id"), r.get("market"), r.get("player")) not in finals:
            k = (r.get("event_id"), r.get("market"))
            if k not in out:
                out.append(k)
    return out


def finals_from_box(date, box):
    """Final stats from public box scores (bp_boxscore) for every pre-tip prop and ledger pick on `date`.
    Returns ({L.prop_key: actual}, {L.prop_key of players who did not play}, [unmatched player names])."""
    from nbaprops.sources import espn as BX
    out, dnp, unmatched = {}, set(), []
    rows = [(r.get("event_id"), r.get("market"), r.get("player")) for r in _gradable(L.pre_tip_rows(date))]
    rows += [(r.get("event_id"), r.get("market"), r.get("player")) for r in L._read(L.LEDGER) if r["date"] == date]
    for ev, mk, pl in rows:
        k = L.prop_key(ev, mk, pl)
        p = BX.lookup(box, pl)
        if p is None:
            if pl not in unmatched:
                unmatched.append(pl)
            continue
        v = BX.stat(p, mk)
        if v is not None:
            out[k] = v
        elif not p.get("played"):
            dnp.add(k)
    return out, dnp, unmatched


def grade_date(date, finals, scored_markets=(), voids=()):
    """Grade the slate for `date` against final stats ({L.prop_key: actual}).
    Learning rows come from each game's last pre-tip snapshot. A pick is void ('V', no action) when its
    game and market (in `scored_markets`) have final stats but the player has none (did not play).
    Returns (props graded, picks graded, still pending)."""
    graded = [r for r in L._read(L.GRADED) if r["date"] != date]
    n_props = 0
    for r in _gradable(L.pre_tip_rows(date)):
        actual = finals.get(L.prop_key(r.get("event_id"), r.get("market"), r.get("player")))
        res = grade(r.get("rec_side"), r.get("line"), actual)
        if res is None:
            continue
        graded.append(dict(r, date=date, actual=actual, result=res))
        n_props += 1
    if graded:
        fields = sorted({k for r in graded for k in r}, key=lambda k: (k != "date", k))
        L._write(L.GRADED, graded, fields)

    ledger = L._read(L.LEDGER)
    n_picks = pending = 0
    for r in ledger:
        if r["date"] != date or r.get("result"):
            continue
        actual = finals.get(L.prop_key(r.get("event_id"), r.get("market"), r.get("player")))
        res = grade(r.get("side"), r.get("line"), actual)
        if res is None:
            if actual in (None, "") and ((r.get("event_id"), r.get("market")) in scored_markets or
                                         L.prop_key(r.get("event_id"), r.get("market"), r.get("player")) in voids):
                r["result"], r["actual"], r["profit_u"], r["profit_kelly"] = "V", "", 0.0, 0.0
                n_picks += 1
            else:
                pending += 1
            continue
        r["result"], r["actual"] = res, actual
        r["profit_u"] = profit(r.get("odds"), res)
        k = L._num(r.get("kelly_u")) or 0
        r["profit_kelly"] = profit(r.get("odds"), res, k) if k else 0.0
        n_picks += 1
    L._write(L.LEDGER, ledger, L.LEDGER_FIELDS)
    return n_props, n_picks, pending


def grade_placed(date, box):
    """Grade my placed bets for `date` from box scores; CLV from the matched BET's closing fair price."""
    from nbaprops.sources import espn as BX
    rows = L._read(L.PLACED)
    if not rows:
        return 0
    led = {(r["date"], r["strategy"], r["player"], r["market"], r["side"], str(L._num(r.get("line")))): r for r in L._read(L.LEDGER)}
    n = 0
    for r in rows:
        if r["date"] != date:
            continue
        if r.get("match", "").startswith(("bet_", "cno")):
            b = led.get((date, r["match"], r["player"], r["market"], r["side"], str(L._num(r.get("line")))))
            if b and L._num(b.get("close_fair")) is not None:
                r["close_fair"] = b["close_fair"]
                r["clv_ev"] = round(L._num(b["close_fair"]) * L._dec(L._num(r["odds"])) - 1, 4)
        if r.get("result"):
            continue
        p = BX.lookup(box, r["player"]) if box else None
        if p is None:
            continue
        v = BX.stat(p, r["market"])
        if v is None and not p.get("played"):
            r["result"], r["actual"], r["profit_u"] = "V", "", 0.0
        elif v is not None:
            res = grade(r["side"], r["line"], v)
            r["result"], r["actual"] = res, v
            r["profit_u"] = profit(r["odds"], res, L._num(r["stake_u"]) or 0)
        n += 1
    L._write(L.PLACED, rows, L.PLACED_FIELDS)
    return n
