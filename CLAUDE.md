# BettingProsAPI

## API permission rule (permanent)

Before ANY call to api.bettingpros.com, stop and ask the user first. Say what you will call and the
estimated request count. This covers everything that touches the API: /props runs, grading,
tip-time (`/events`) checks, regrades, market or book lookups, and one-off diagnostic calls.

- No call counts as free unless the BettingPros API docs show it does not count against the daily
  limit, and you show the user where they say so.
- /props asks before it pulls, too (state the estimate first).
- Exception: the user may pre-approve a specific call in their message; that approval covers only
  that call, not later ones.
- No permission needed: reading local files (CSVs, `history/`, `bp_cache.json`, ledgers, saved raw
  responses), re-scoring with `--from-csv` (0 requests), reading the public RotoWire page, and public box
  scores from ESPN / NBA.com (`bp_boxscore.py`; `--grade DATE --max-requests 0` grades with no BettingPros calls).

The daily limit is 100 requests (Free/Plus) and resets at midnight UTC (7 PM ET in winter, 8 PM in summer).
Never print, cat or echo `.env` or the API key.

## The Odds API (value check, bp_value.py)

500 credits per month. Ask the user before every call that costs credits, and show the credit cost and the
credits remaining (`odds_credits.json`, from the x-requests-* headers). `python3 bp_value.py --picks` (or
`--top N`) with no `--go` only plans: it prints the cost and makes no call. Run with
`--go --max-credits <approved>` only after the user approves. `GET /events` is free; each event-odds call
costs (markets in the call) x 1 region (FanDuel + up to 9 books). The script stops before going under 50
credits. Key: `ODDS_API_KEY` in `.env`, never printed.

## Unabated (primary fair-price source, bp_unabated.py)

Ask the user before any Unabated odds call (`bp_value.py --go` makes one props odds call for the whole board
when Unabated is enabled). Discovery (`python3 bp_unabated.py --discover`: /league, /bettype, props sources,
period types, people) doesn't use the odds rate limit; run it when the user asks. Free API: at most one odds call
every 5 seconds per key (the script enforces it). A 401/403 disables Unabated (`unabated_state.json`) and
bp_value.py falls back to The Odds API and says so. Key: `UNABATED_API_KEY` in `.env` (X-Api-Key header),
never printed.

## Crazy Ninja Odds (second-opinion devig, bp_crazyninja.py)

Open pre-release devigger API (`api=open`, no key). bp_value.py calls it for each new BET (one call per source at
FanDuel's line, cached in `cno_cache.json`); it sends only odds numbers. The result is logged next to the BET and
never changes the BET rule. If the API is down the check is skipped.

