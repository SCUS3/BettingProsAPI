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
- Run it before tip-off when possible: each run logs tonight's top 5 picks per strategy (balanced, your score, gap, learned) to `bp_ledger.csv`, and only the last run before the games counts.
- The script automatically grades earlier slates first (about 18 requests per slate, at most 3 slates per run) and re-learns from the results. To skip that, add `--no-grade`. To grade a single slate, use `--grade YYYY-MM-DD`.
- The API key loads by itself from `.env`. Never print, cat or echo `.env` or the key.
- If a CSV for that date already exists and is less than 30 minutes old, ask whether to reuse it instead of spending requests again (the daily limit is 100 on Free/Plus). To reuse it, run `python3 bp_props.py --from-csv --date YYYY-MM-DD`, which re-scores and rewrites the note with 0 requests.

If the script exits with an error, report the message plainly. These are the common ones:
- "Daily request allowance used up": the limit resets at midnight UTC (7 PM Eastern in winter).
- "403 Forbidden": the key in `.env` is wrong or deactivated.
- "No markets matched" or a missing-market warning: run `python3 bp_props.py --list-markets` and show the user.

## 2. Read the results

Read `bp_top10_<date>.md`, which the script writes. It has:
1. **Top 10 by projection gap**: |projection - line| as a % of the line, nothing else.
2. **Top 10 by your score**: the user's rules, `score` = 40% edge (gap in SDs) + 35% EV at FanDuel's price + 25% matchup, cut for injury.
3. **Top 10 by balanced score**: `balanced` = 40% score + 25% recent form + 20% market value (no-vig price, FanDuel line vs. consensus) + 15% BettingPros bet rating.
4. For each of the seven categories (PTS, REB, AST, 3PM, P+A, P+R, PRA): a top 10 by projection gap and a top 10 by your score.

Every row also shows Form (recent hit rate vs. the line), Signals (how many of six factors lean the same way) and Kelly (quarter-Kelly stake, 1u = 1% of bankroll). For anything else (a player or team filter, extra columns such as `s_edge`, `s_ev`, `s_dvp`, `s_form`, `s_market`, `nv_edge`, `streak`), read `bp_props_<date>.csv` with a short Python snippet; don't print the whole file.

## 3. Update the dashboard

The script writes `bp_dashboard.html` (the NBA Prop Board: one interactive page with every list, a category picker, Gap/Score/Balanced ranking and a "Copy for Claude" button).

Publish it with the Artifact tool, using `file_path` = the absolute path to `bp_dashboard.html`:
- If `.dashboard_url` exists, read it and pass that URL as `url` so the same link updates. If the publish is refused because this conversation hasn't read the artifact, do `action: "read"` on that URL first, then publish the new local file.
- If `.dashboard_url` doesn't exist, publish without `url` (icon `chart`) and save the returned link into `.dashboard_url`.

Include the dashboard link in the report.

## 4. Report

Show the three overall top-10 lists as compact tables:
- **By gap:** player | market | side | line | proj | gap% | FD | score | balanced
- **By your score:** player | market | side | line | FD | DvP | score | balanced | Kelly
- **By balanced:** player | market | side | line | FD | form | score | balanced | signals | Kelly

Then, for each of the seven categories, give the top 3 by gap and the top 3 by your score on one line each (player side line, gap %, score), and tell the user the full lists are in `bp_top10_<date>.md`. If a category is empty, say so.

If the script printed "Graded <date>", start the report with a **Results** line: last slate's record for each strategy (W-L, units) and the running record by strategy from the printed Record block. Mention any situation in the model's insights that stands out (for example, form when a teammate is ruled out) once it has a real sample.

After that, give 2-4 short bullets that point out:
- plays near the top of all three lists, which are the strongest,
- props where a teammate is ruled out (`tm_out_names` in the CSV), since a bigger minutes load can beat weak recent form,
- big gaps or high scores that the balancing signals disagree with (poor form, bad market value, juiced odds, injury), and why,
- the request count the script printed, so the user knows how much of today's allowance is left.

Keep it short and factual. These are projections, not guarantees; add no further disclaimers beyond one line.
