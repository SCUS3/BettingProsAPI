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
  responses), re-scoring with `--from-csv` (0 requests), and reading the public RotoWire page.

The daily limit is 100 requests (Free/Plus) and resets at midnight UTC (7 PM ET in winter, 8 PM in summer).
Never print, cat or echo `.env` or the API key.
