"""Simulate a slate that wasn't pulled live (research only; never touches the real record).

    python3 bp_sim.py --date 2026-11-21 --max-requests 15   # pull (BettingPros, ask first), score, log, grade
    python3 bp_sim.py --date 2026-11-21                     # re-run from the saved raw pull: 0 requests

Lines and projections come from BettingPros' /props for the past date, filtered to FanDuel: these are the last
lines BettingPros kept (near closing, possibly thinned after tip), not the 2 PM prices you would have bet.
Results come from public box scores (bp_boxscore). Everything is written under sim/<date>/ and to
sim/sim_ledger.csv; bp_ledger.csv, bp_graded.csv, the model, the Kelly cap and the dashboard are untouched.
Market BETs aren't simulated (no historical Unabated consensus), so this tests the model-pick strategies only.
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import sys

import bp_props as B
import bp_learn as L
import bp_boxscore as BX

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "sim")
SIM_LEDGER = os.path.join(SIM, "sim_ledger.csv")


def raw_path(date):
    return os.path.join(SIM, date, "props_raw.json")


def pull(date, cap):
    B.load_dotenv()
    key = os.environ.get("BP_API_KEY")
    if not key:
        sys.exit("No BP_API_KEY in .env.")
    cache = B.load_cache()
    fd = B.find_fanduel(key, "NY", cache)
    markets = B.nba_markets(key, cache)
    B.save_cache(cache)
    ids = ":".join(str(m) for m, (tag, _) in sorted(markets.items()) if tag and tag not in B.DISABLED)
    B.RUN_CAP = cap
    props = B.pull_props(key, date, fd, ids, 50)
    os.makedirs(os.path.dirname(raw_path(date)), exist_ok=True)
    json.dump({"fd": fd, "labels": {str(m): t for m, (t, _) in markets.items() if t}, "props": props,
               "pages": B.PULL_LOG, "requests": B.REQUESTS_MADE}, open(raw_path(date), "w"))
    print(f"Pulled {len(props)} FanDuel props for {date} ({B.REQUESTS_MADE} BettingPros requests) -> {raw_path(date)}")


def offer_from(p, fd):
    off = {"event_id": p.get("event_id"), "market_id": p.get("market_id"),
           "player": (p.get("participant") or {}).get("name"), "player_id": str((p.get("participant") or {}).get("id") or "")}
    for side in ("over", "under"):
        s = p.get(side) or {}
        if s.get("line") is not None and s.get("odds") is not None and str(s.get("book")) == str(fd):
            off[side] = {"line": s.get("line"), "odds": s.get("odds")}
    return off


PER_GAME = 1.5          # BettingPros /props pages per game for a past date (11/21: 9 pages, 6 games)
SEASON_START = "2026-10-20"


def regular_season_games(date):
    """(number of final regular-season games on `date`, ET) from ESPN's public scoreboard. Free."""
    sb = BX._get(f"{BX.ESPN}/scoreboard?dates={date.replace('-', '')}&limit=100")
    evs = [e for e in sb.get("events", []) if (e.get("season") or {}).get("type") == 2]
    return sum(1 for e in evs if e.get("status", {}).get("type", {}).get("name") == "STATUS_FINAL"), len(evs)


def unpulled_dates(today):
    """Finished regular-season dates with no live pull (no bp_props_<date>.csv) and no saved sim yet."""
    pulled = {os.path.basename(p)[9:-4] for p in glob.glob(os.path.join(HERE, "bp_props_*.csv"))}
    out = []
    d = dt.date.fromisoformat(SEASON_START)
    end = dt.date.fromisoformat(today)
    while d < end:
        ds = d.isoformat()
        if ds not in pulled and not os.path.exists(raw_path(ds)):
            final, total = regular_season_games(ds)
            if final and final == total:
                out.append((ds, final, math.ceil(final * PER_GAME)))
        d += dt.timedelta(days=1)
    return out


def live_reserve():
    """BettingPros requests the live flow still needs inside the current UTC day (the 100-request window).
    For each ET date whose first pull (earliest tip - 90 min) or games fall before the window resets: the full
    pull if it hasn't run, plus the re-price. Sims only get what's left, so they never delay or trim a live pull."""
    import bp_schedule as S
    now = dt.datetime.now(dt.timezone.utc)
    window_end = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
    reserve, notes = 0, []
    et_today = dt.date.fromisoformat(B.eastern_today())
    for d in (et_today, et_today + dt.timedelta(days=1)):
        p = S.plan(d.isoformat())
        if not p["games"] or p["last_tip"] <= now or p["pull_at"] >= window_end:
            continue
        pulled = os.path.exists(os.path.join(HERE, f"bp_props_{d.isoformat()}.csv"))
        left = sum(1 for tip, _, _ in p["list"] if tip > now)                 # games still to tip
        need = (0 if pulled else math.ceil(S.PULL_PER_GAME * left + 1)) + math.ceil(S.REPRICE_PER_GAME * left)
        reserve += need
        notes.append(f"{d.isoformat()} live flow ≈{need} ({'re-price' if pulled else 'pull + re-price'})")
    return reserve, notes


def backfill(today, cap, go):
    used = B.used_today()
    reserve, notes = live_reserve()
    spare = max(0, B.DAILY_LIMIT - used - reserve)
    print(f"BettingPros window: {used} used, {reserve} reserved for the live flow"
          + (f" ({'; '.join(notes)})" if notes else "") + f" -> {spare} spare for sims.")
    cap = min(cap, spare)
    todo = unpulled_dates(today)
    est = sum(c for _, _, c in todo)
    print(f"Unpulled finished dates since {SEASON_START}: {len(todo)} ({sum(g for _, g, _ in todo)} games), "
          f"≈{est} BettingPros requests in all (≈{PER_GAME} per game).")
    for ds, g, c in todo:
        print(f"  {ds}: {g} games, ≈{c} requests")
    if not go:
        print("Plan only: no BettingPros requests made.")
        return
    if cap <= 0:
        print("No spare requests after the live flow: no sims this window.")
        return
    spent, done = 0, []
    for ds, g, c in reversed(todo):                   # newest first
        if spent + c > cap:
            continue
        B.REQUESTS_MADE = 0
        try:
            pull(ds, min(cap - spent, c + 3))
        except B.RunCapReached as e:
            print(f"  {ds}: stopped ({e}); not saved.")
            spent += B.REQUESTS_MADE
            continue
        spent += B.REQUESTS_MADE
        raw = json.load(open(raw_path(ds)))
        if not raw["props"]:
            print(f"  {ds}: BettingPros returned no props for this date; stopping the backfill (older dates likely empty too).")
            break
        rows = score(raw["props"], raw["fd"], {int(k): v for k, v in raw["labels"].items()}, ds)
        log_and_grade(rows, ds, quiet=True)
        done.append(ds)
    print(f"Backfill: simmed {len(done)} dates with {spent} BettingPros requests.")
    sim_record()


def sim_record():
    led = L._read(SIM_LEDGER)
    if not led:
        return
    dates = sorted({r["date"] for r in led})
    print(f"SIM record ({len(dates)} slates, {dates[0]} to {dates[-1]}; research only, not in the real record):")
    names = dict(L.STRATEGIES)
    for strat, _ in L.STRATEGIES:
        _line(names[strat], [r for r in led if r["strategy"] == strat])
    once = list({(r["date"], r["player"], r["market"], r["side"]): r for r in led}.values())
    _line("Each prop once", once)
    for mk in ("3PM", "P+R"):
        for sd in ("over", "under"):
            _line(f"  {mk} {sd}s (each once)", [r for r in once if r["market"] == mk and r["side"] == sd])


def _line(label, rs):
    w = sum(r.get("result") == "W" for r in rs)
    l_ = sum(r.get("result") == "L" for r in rs)
    u = sum(float(r.get("profit_u") or 0) for r in rs)
    n = w + l_
    print(f"  {label:34} {w}-{l_}" + (f"  hit {100 * w / n:4.1f}%" if n else "") + f"  {u:+.2f}u")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--max-requests", type=int, help="pull from BettingPros (hard stop); omit to reuse the saved pull")
    ap.add_argument("--backfill", action="store_true", help="every finished, unpulled date this season; plan only without --go")
    ap.add_argument("--go", action="store_true", help="--backfill: make the BettingPros calls (needs --max-requests)")
    ap.add_argument("--record", action="store_true", help="sim record across all simmed slates (0 requests)")
    a = ap.parse_args()
    if a.record:
        return sim_record()
    if a.backfill:
        if a.go and a.max_requests is None:
            sys.exit("--backfill --go needs --max-requests N (approved BettingPros requests).")
        return backfill(B.eastern_today(), a.max_requests or 0, a.go)
    if not a.date:
        sys.exit("Give --date, --backfill or --record.")
    if a.max_requests is not None and not os.path.exists(raw_path(a.date)):
        pull(a.date, a.max_requests)
    if not os.path.exists(raw_path(a.date)):
        sys.exit(f"No saved pull for {a.date}; run with --max-requests N (BettingPros requests, ask first).")
    raw = json.load(open(raw_path(a.date)))
    fd, labels = raw["fd"], {int(k): v for k, v in raw["labels"].items()}
    print(f"{a.date}: {len(raw['props'])} props in the saved pull")
    rows = score(raw["props"], fd, labels, a.date)
    log_and_grade(rows, a.date)


def strip_tonight(p):
    """BettingPros' form windows for a past date already include that game (174/175 last_1 matched the result
    on 11/21). Remove tonight's outcome from every window; drop last_1, h2h and the streak."""
    perf = json.loads(json.dumps(p.get("performance") or {}))
    act = (p.get("scoring") or {}).get("actual")
    line = (p.get("over") or p.get("under") or {}).get("line")
    if act is not None and line is not None:
        res = "over" if act > line else "under" if act < line else "push"
        for w in ("last_5", "last_10", "last_15", "last_20", "season"):
            if isinstance(perf.get(w), dict) and (perf[w].get(res) or 0) > 0:
                perf[w][res] -= 1
    for w in ("last_1", "h2h"):
        perf.pop(w, None)
    perf["streak"] = perf["streak_type"] = None
    return dict(p, performance=perf, scoring={})        # the result stays out of scoring, too


def score(props, fd, labels, date):
    pulled, tip = f"{date}T17:00:00Z", f"{date}T23:59:00Z"   # sim: every prop treated as pre-tip
    rows = []
    for p in props:
        q = strip_tonight(p)
        base = B.base_from_prop(q, labels)
        rows.append(B.price_row(base, offer_from(q, fd), tip, pulled, fd))
    L.add_teammate_context(rows, date, None)
    model = L.load_model()
    for r in rows:
        if r["noproj"]:
            B.noproj_row(r)
        elif r.get("market") not in B.REAL_PROJ_MARKETS and B._num(r.get("bp_proj")) is not None:
            B.lean_row(r)
        else:
            r["lean_only"] = False
            B.score_row(r)
            B.balance_row(r)
            L.learned_row(r, model)
    os.makedirs(os.path.join(SIM, date), exist_ok=True)
    import csv
    with open(os.path.join(SIM, date, f"sim_props_{date}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def log_and_grade(rows, date, quiet=False):
    real = (L.LEDGER, L.GRADED)
    L.LEDGER, L.GRADED = SIM_LEDGER, os.path.join(SIM, "sim_graded.csv")    # never the real files
    try:
        n = L.log_picks(rows, date)
        box = BX.finals(date)
        got, dnp, unmatched = L.finals_from_box(date, box)
        _, n_picks, pend = L.grade_date(date, got, (), dnp)
        led = [r for r in L._read(SIM_LEDGER) if r["date"] == date]
    finally:
        L.LEDGER, L.GRADED = real
    if quiet:
        w = sum(r.get("result") == "W" for r in led)
        print(f"  {date}: {n} picks logged, {n_picks} graded ({w}-{n_picks - w - sum(r.get('result') == 'V' for r in led)})")
        return
    print(f"SIM {date}: logged {n} model picks, graded {n_picks}" + (f", {pend} pending" if pend else "")
          + (f"; no box-score match: {', '.join(unmatched[:6])}" if unmatched else ""))
    print("Research only: near-closing lines, form with tonight removed, no market BETs. Not in the real record.")

    line = _line
    names = dict(L.STRATEGIES)
    for strat, _ in L.STRATEGIES:
        line(names[strat], [r for r in led if r["strategy"] == strat])
    once = {(r["player"], r["market"], r["side"]): r for r in led}.values()
    line("Each prop once", list(once))
    line("3PM overs (each once)", [r for r in once if r["market"] == "3PM" and r["side"] == "over"])
    print("Picks:")
    for r in sorted(once, key=lambda r: (r["market"], r["player"])):
        print(f"  {r['player'][:22]:22} {r['market']:4} {r['side']:5} {float(r['line']):>5g} {float(r['odds']):>+5g}"
              f"  actual {r.get('actual') or '-':>3}  {r.get('result') or '-'}")


if __name__ == "__main__":
    main()
