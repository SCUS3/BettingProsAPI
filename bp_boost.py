"""Price a FanDuel profit boost: Unabated market (one free props odds call) + CrazyNinja worst-case devig with
the boost applied, per source at FanDuel's line. Reports EV% and a ¼K stake.

    python3 bp_boost.py "Obi Toppin" 3PM over +172 --boost 25 --type profit          # plan only, no call
    python3 bp_boost.py "Obi Toppin" 3PM over +172 --boost 25 --type profit --go     # one Unabated odds call

--line pins the line (default: FanDuel's main line on Unabated's board). --board-file prices a saved board
instead of calling. No paid calls: Unabated is free (ask first, as for any Unabated odds call); CrazyNinja is open.
"""
import argparse
import json
import sys

import bp_crazyninja as C
import bp_unabated as U
import bp_value as V
from bp_rotowire import norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("player")
    ap.add_argument("market", choices=["PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA", "R+A"])
    ap.add_argument("side", choices=["over", "under", "o", "u"])
    ap.add_argument("price", type=float, help="FanDuel's price before the boost, e.g. +172")
    ap.add_argument("--boost", type=float, required=True, help="boost percent, e.g. 25")
    ap.add_argument("--type", choices=["profit", "all"], default="profit", help="boost applies to profit (default) or all")
    ap.add_argument("--line", type=float)
    ap.add_argument("--go", action="store_true", help="make the one Unabated props odds call (free)")
    ap.add_argument("--board-file")
    a = ap.parse_args()
    side = {"o": "over", "u": "under"}.get(a.side, a.side)
    btype = 0 if a.type == "profit" else 1
    if not a.go and not a.board_file:
        print(f"Plan: 1 Unabated props odds call ({a.market}, free; 1 per 5 s), then ~1 CrazyNinja call per source "
              f"(open API, 1 per second). No paid calls. Re-run with --go once approved.")
        return
    data = json.load(open(a.board_file)) if a.board_file else U.fetch_board([a.market])
    d = U.load(U.DISCOVERY, {})
    lines = [x for x in U.fd_lines(data, d) if x["market"] == a.market and norm(x["player"]) == norm(a.player)]
    if not lines and a.line is None:
        sys.exit(f"No FanDuel {a.market} line for {a.player} on Unabated's board (name or market?).")
    line = a.line if a.line is not None else lines[0]["line"]
    player = lines[0]["player"] if lines else a.player
    res = U.price_board([{"player": player, "market": a.market, "side": side, "line": line, "odds": a.price}], data, d)
    v = res.get((player, a.market, side)) or {}
    two = v.get("two_way") or []
    if not two:
        sys.exit(f"No sources pricing both sides at {line:g} ({v.get('note') or 'not on the board'}).")
    plain = C.check(side, two, a.price)
    boosted = C.check(side, two, a.price, boost=a.boost, boost_type=btype)
    print(f"{player} {a.market} {side} {line:g} at {a.price:+g}, {a.boost:g}% boost on {a.type} "
          f"({len(two)} sources at FanDuel's line: {', '.join(s for s, _, _ in two)})")
    if v.get("fair_mult") is not None:
        print(f"  Unabated median fair {v['fair_mult']:.3f}: unboosted EV {100 * v['ev_mult']:+.1f}%")
    if not boosted:
        print("  CrazyNinja unavailable: no boosted EV (no CNO check).")
        return
    one = C.devig(*((two[0][1], two[0][2]) if side == "over" else (two[0][2], two[0][1])), a.price,
                  boost=a.boost, boost_type=btype)
    cap = V.bet_cap()[0]
    k = boosted.get("kelly")
    qk = None if k is None else round(min(max(k, 0) / 4, cap), 2)
    print(f"  CrazyNinja worst-case, boosted: EV {100 * boosted['median_ev']:+.1f}% median "
          f"(lowest source {100 * boosted['min_ev']:+.1f}%), fair {boosted['fair']:.3f}"
          + (f", boosted price {one['final_odds']}" if one and one.get("final_odds") else ""))
    if plain:
        print(f"  Unboosted CrazyNinja worst-case EV {100 * plain['median_ev']:+.1f}%")
    print(f"  Stake: ¼K = {qk}u" + (f" (full Kelly {k:.2f}u; capped at {cap}u)" if k is not None and k / 4 > cap else
                                   f" (full Kelly {k:.2f}u)" if k is not None else "")
          + ("  -> +EV" if boosted["median_ev"] > 0 else "  -> not +EV"))
    print("  Exposure caps (1.5u per player, 3.5u per game) still apply alongside any BETs on this player/game.")


if __name__ == "__main__":
    main()
