"""One-time test: does The Odds API return LIVE player props (FanDuel + 3 or more books) with fresh timestamps?

    python3 bp_oddsapi_live.py "Indiana Pacers" "New York Knicks"          # plan only
    python3 bp_oddsapi_live.py "Indiana Pacers" "New York Knicks" --go     # 1 free /events + 1 event-odds call

One event, one market (player_points), US region: 1 credit (stops if a call costs more than 2). Credits used and
remaining are saved to odds_credits.json from the response headers. Result saved to
history/<date>/oddsapi_live_test.json and shown on the Record tab's halftime section.
"""
import datetime as dt
import json
import os
import sys

import bp_props
import bp_value as V

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET, REGION, MAX_COST = "player_points", "us", 2


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit('usage: bp_oddsapi_live.py "Away Team" "Home Team" [--go]')
    away, home = args
    if "--force-retest" not in sys.argv:
        sys.exit("The Odds API live test failed on 11/22 (FanDuel + 2 fresh books, needs 3). No more live calls; "
                 "pass --force-retest only if the user asks for a new test.")
    if "--go" not in sys.argv:
        print(f"Plan: 1 free /events call, then 1 event-odds call ({away} @ {home}, {MARKET}, region {REGION}) = 1 credit "
              f"(max {MAX_COST}). Re-run with --go once approved.")
        return
    bp_props.load_dotenv()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("No ODDS_API_KEY in .env.")
    V.guard(MAX_COST)
    evs = V.get("/events", {}, key, 0)
    ev = next((e for e in evs if e.get("home_team") == home and e.get("away_team") == away), None)
    if not ev:
        sys.exit(f"No Odds API event for {away} @ {home} (it lists {len(evs)} events).")
    now = dt.datetime.now(dt.timezone.utc)
    body = V.get(f"/events/{ev['id']}/odds", {"regions": REGION, "markets": MARKET,
                                                                     "oddsFormat": "american"}, key, MAX_COST)
    books = []
    for b in body.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") != MARKET:
                continue
            t = m.get("last_update") or b.get("last_update")
            age = (now - dt.datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() if t else None
            books.append({"book": b.get("title") or b.get("key"), "key": b.get("key"), "last_update": t,
                          "age_s": None if age is None else round(age), "outcomes": len(m.get("outcomes", []))})
    fd = next((x for x in books if x["key"] == "fanduel"), None)
    fresh = [x for x in books if x["age_s"] is not None and x["age_s"] < 60]
    c = V.load_credits()
    res = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": f"{away} @ {home}", "event_id": ev["id"],
           "commence_time": ev.get("commence_time"), "market": MARKET, "books": books,
           "fanduel": bool(fd), "fanduel_fresh": bool(fd and fd["age_s"] is not None and fd["age_s"] < 60),
           "fresh_books": len(fresh), "other_fresh_books": len([x for x in fresh if x["key"] != "fanduel"]),
           "credits_last": (c.get("calls") or [{}])[-1].get("cost"), "credits_remaining": c.get("remaining"),
           "credits_used_month": c.get("used")}
    res["pass"] = bool(res["fanduel_fresh"] and res["other_fresh_books"] >= 3)
    date = bp_props.eastern_today()
    os.makedirs(os.path.join(HERE, "history", date), exist_ok=True)
    json.dump(res, open(os.path.join(HERE, "history", date, "oddsapi_live_test.json"), "w"), indent=1)
    print(f"{res['event']} {MARKET}: {len(books)} books returned live player points")
    for x in sorted(books, key=lambda x: x["age_s"] if x["age_s"] is not None else 9e9):
        print(f"  {x['book']:22} updated {x['age_s']}s ago, {x['outcomes']} outcomes")
    print(f"FanDuel: {'yes' if fd else 'no'}{' (fresh < 60 s)' if res['fanduel_fresh'] else ''}; other books fresh < 60 s: "
          f"{res['other_fresh_books']} -> {'PASS' if res['pass'] else 'FAIL'} (needs FanDuel + 3 fresh). "
          f"Credits: this call {res['credits_last']}, remaining {res['credits_remaining']}.")


if __name__ == "__main__":
    main()
