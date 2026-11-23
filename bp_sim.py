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
import json
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--max-requests", type=int, help="pull from BettingPros (hard stop); omit to reuse the saved pull")
    a = ap.parse_args()
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


def log_and_grade(rows, date):
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
    print(f"SIM {date}: logged {n} model picks, graded {n_picks}" + (f", {pend} pending" if pend else "")
          + (f"; no box-score match: {', '.join(unmatched[:6])}" if unmatched else ""))
    print("Research only: near-closing lines, form with tonight removed, no market BETs. Not in the real record.")

    def line(label, rs):
        w = sum(r["result"] == "W" for r in rs)
        l_ = sum(r["result"] == "L" for r in rs)
        u = sum(float(r.get("profit_u") or 0) for r in rs)
        print(f"  {label:34} {w}-{l_}  {u:+.2f}u")
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
