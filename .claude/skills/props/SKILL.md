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
- If a CSV for that date already exists and is less than 30 minutes old, ask whether to reuse it instead of spending requests again (the daily limit is 100 on Free/Plus). To reuse it, run `python3 bp_props.py --from-csv --date YYYY-MM-DD`, which re-scores and rewrites the note with 0 requests.

If the script exits with an error, report the message plainly. These are the common ones:
- "Daily request allowance used up": the limit resets at midnight UTC (7 PM Eastern in winter).
- "403 Forbidden": the key in `.env` is wrong or deactivated.
- "No markets matched" or a missing-market warning: run `python3 bp_props.py --list-markets` and show the user.

## 2. Read the results

Read `bp_props_<date>.csv` with a short Python snippet; don't print the whole file. If the user named a player or team, filter to it.

Keep only rows where `price_is_fanduel` is True and `injury` is empty or "Probable", and rank them by `abs(pct_gap)` (projection vs. line).

Every row has a `score` column (0-100) made of 40% edge (`s_edge`: gap in standard deviations), 35% EV at FanDuel's price (`s_ev`, from `model_ev`) and 25% matchup (`s_dvp`), multiplied down for DTD/Questionable (x0.85) or Doubtful (x0.5).

Also read `bp_top10_<date>.md`, which the script writes. It starts with the top 10 overall by score, then the 10 biggest gaps in each of the seven categories (PTS, REB, AST, 3PM, P+A, P+R, PRA), each with its score.

## 3. Report

Give a compact markdown table of the top 10:
player | market | side | line | proj | gap% | FD odds | DvP softness (1 = softest) | score

Then show the top 5 by score (player market side line, score). Then, for each of the seven categories, list the top 3 from `bp_top10_<date>.md` on one line each (player side line, gap %, score), and tell the user the full top 10 per category is in that file. If a category is empty, say so.

After that, give 2–4 short bullets that point out:
- the highest-scoring plays and which parts drove the score (edge, EV, matchup),
- big-gap plays with low scores, and why (juiced odds, bad matchup, injury),
- plays to be careful with: a big gap but questionable injury status, very juiced odds (worse than -150), or a tough matchup,
- the request count the script printed, so the user knows how much of today's allowance is left.

Keep it short and factual. These are projections, not guarantees; add no further disclaimers beyond one line.
