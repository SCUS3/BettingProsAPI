---
name: props
description: Pull a day's NBA player props from BettingPros (priced at FanDuel) with bp_props.py and summarize the best plays. Use when the user asks for tonight's props, best bets, edges, or runs /props, optionally with a date, a player/team, or "offers".
argument-hint: "[YYYY-MM-DD] [offers] [player or team filter]"
---

# NBA props run

Arguments from the user: `$ARGUMENTS`

## 1. Run the script

From the project root, run:

```bash
python3 bp_props.py [--date YYYY-MM-DD] [--offers]
```

- Add `--date` only if the user gave a date (or "tomorrow", which you convert to a date in Eastern time).
- Add `--offers` only if the user said "offers", "EV" or "fair odds". It costs extra requests (one per game).
- The API key loads by itself from `.env`. Never print, cat or echo `.env` or the key.
- If a CSV for that date already exists and is less than 30 minutes old, ask whether to reuse it instead of spending requests again (the daily limit is 100 on Free/Plus).

If the script exits with an error, report the message plainly. These are the common ones:
- "Daily request allowance used up": the limit resets at midnight UTC (7 PM Eastern in winter).
- "403 Forbidden": the key in `.env` is wrong or deactivated.
- "No markets matched" or a missing-market warning: run `python3 bp_props.py --list-markets` and show the user.

## 2. Read the results

Read `bp_props_<date>.csv` with a short Python snippet; don't print the whole file. If the user named a player or team, filter to it.

Keep only rows where `price_is_fanduel` is True and `injury` is empty or "Probable". Then rank them:
1. by `abs(pct_gap)` (projection vs. line), and
2. also by `bp_ev` (or `fd_market_ev` with --offers) if those columns have values (Plus/Elite keys).

Also read `bp_top10_<date>.md`, which the script writes: the 10 biggest gaps in each of the seven categories (PTS, REB, AST, 3PM, P+A, P+R, PRA).

## 3. Report

Give a compact markdown table of the top 10:
player | market | side | line | proj | gap% | FD odds | DvP softness (1 = softest)

Then, for each of the seven categories, list the top 3 from `bp_top10_<date>.md` on one line each (player side line, gap %), and tell the user the full top 10 per category is in that file. If a category is empty, say so.

After that, give 2–4 short bullets that point out:
- plays where the projection gap, EV and a soft matchup (DvP 1–10) all agree,
- plays to be careful with: a big gap but questionable injury status, very juiced odds (worse than -150), or a tough matchup,
- the request count the script printed, so the user knows how much of today's allowance is left.

Keep it short and factual. These are projections, not guarantees; add no further disclaimers beyond one line.
