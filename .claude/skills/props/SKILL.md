---
name: props
description: Pull a day's NBA player props from BettingPros (priced at FanDuel) with bp_props.py and summarize the best plays. Use when the user asks for tonight's props, best bets, edges, or runs /props, optionally with a date, a player/team, or "offers".
argument-hint: "[YYYY-MM-DD] [reprice] [player or team filter]"
---

# NBA props run

Arguments from the user: `$ARGUMENTS`

## 1. Run the script

**Ask before pulling** (rule in CLAUDE.md): before running anything that calls api.bettingpros.com, tell the user what will be called and the estimated request count, and wait for their OK, unless they pre-approved this run in their message. Estimate from local files: about 1 request per 20 props on the slate (the last CSV's row count for a date, or a full slate is ~15-25 pages), plus 1 for `/events`, plus grading only if a pending slate's games have no public box score (grading uses ESPN / NBA.com box scores first at 0 BettingPros requests; run `python3 bp_props.py --grade DATE --max-requests 0` to grade with no BettingPros requests, no permission needed). `--from-csv` costs 0 requests and needs no permission.

From the project root, run:

```bash
python3 bp_schedule.py                                         # morning: ESPN schedule (free) -> suggested first pull time
python3 bp_props.py [--date YYYY-MM-DD] --max-requests N     # first pull (earliest tip - 90 min)
python3 bp_props.py [--date YYYY-MM-DD] --reprice [--max-requests N]   # 6:30 PM re-price
```

- Add `--date` only if the user gave a date (or "tomorrow", which you convert to a date in Eastern time).
- **Two runs a day.** The 2 PM ET full pull first gets every FanDuel main line from one `/offers` query across all games (`book_id=10`, condensed), then BettingPros projections from `/props` only for the players FanDuel prices (`event_id` + `market_id` + `player_id` colon lists), joined on game + player + market. After the first `/props` page it reports how many FanDuel lines can match a projection; under 50% it stops (only page 1 spent) and you must ask the user before re-running with `--unfiltered-props` (~5-6 `/props` pages per game). R+A is off (no FanDuel R+A lines in BettingPros); `DISABLED` in bp_props.py turns it back on. Gap, side, EV, Kelly and placeholder status are recomputed at FanDuel's own line and price; when FanDuel's line differs from BettingPros', both show (`line_note`). FanDuel lines with no projection stay on the board, ranked after every projected prop on form, DvP and market value only, with no edge, Kelly or logged pick. The 6:30 PM ET `--reprice` run re-prices from `/offers` only; its picks are the ones that count, and it reports lines that moved and lines pulled since 2 PM (late scratches).
- **Cost.** Full pull ≈ 5.7 requests per game + 1 (about 2.4 `/offers` + 3.3 `/props` pages per game; pages count across the whole slate). Re-price ≈ 2.4 per game. Grading the previous slate: 0 (box scores); BettingPros fallback ≈ 1.4 per game + up to 8 lookups only for games without a box score. `bp_requests.json` counts every request per UTC day. Each run prints its budget math (used earlier today + this run + 6:30 re-price vs. 100) before spending the bulk of its pages, and stops if it would pass `--max-requests` or what's left today.
- **Big-slate rule.** The 2 PM pull is never trimmed. Its budget math shows the full 6:30 re-price cost and, once picks are logged, the picks-only cost (only the games with logged picks). If the full re-price won't fit in what's left before the 7 PM ET reset, it chooses picks-only; if even that won't fit, it skips the re-price and the 2 PM picks count. `--reprice` follows that decision (`--force-reprice` overrides, only if the user asks). In a picks-only re-price, the other games keep their 2 PM prices and their 2 PM picks keep counting.
- **Kelly.** Stakes are halved (max 1.25u, "half-Kelly: model not yet calibrated") until the combined pick record has 50+ graded picks and its Brier beats the market's. Mention the note when reporting stakes. Before each step, show the user the math: used so far today, this step's estimate, and what's left.
- Run times: each morning run `python3 bp_schedule.py` (ESPN, free) and suggest the first pull at **earliest tip - 90 min** (not a fixed 2 PM); ask for the pull as usual, with its estimate. A pull after a game tips gets thinned lines for that game, and its picks and props don't count or train the model. The re-price runs later, before the bulk of the tips. If the script prints "Warning: N of M games had already tipped", tell the user. Each pull saves its pagination totals and per-game prop counts to `history/<date>/pull_<time>.json`. Always report the "requests used this run" number so the user can confirm two pulls plus grading fit under 100 a day.
- Run it before tip-off: each run logs its top 5 picks per strategy (balanced, your score, gap, learned). For each game, only picks from the last run before that game's tip count; anything logged after a game starts never counts.
- The script automatically grades earlier slates first (from ESPN / NBA.com box scores, 0 BettingPros requests; at most 3 slates per run) and re-learns from the results. To skip that, add `--no-grade`. To grade a single slate, use `--grade YYYY-MM-DD`.
- Each live run also reads Rotowire's public lineups page once (no API request). If it prints "Rotowire lineups: unavailable", say so; it falls back to missing-props detection.
- The API key loads by itself from `.env`. Never print, cat or echo `.env` or the key.
- If a CSV for that date already exists and is less than 30 minutes old, ask whether to reuse it instead of spending requests again (the daily limit is 100 on Free/Plus). To reuse it, run `python3 bp_props.py --from-csv --date YYYY-MM-DD`, which re-scores and rewrites the note with 0 requests.

If the script exits with an error, report the message plainly. These are the common ones:
- "Daily request allowance used up": the limit resets at midnight UTC (7 PM Eastern in winter).
- "403 Forbidden": the key in `.env` is wrong or deactivated.
- "No markets matched" or a missing-market warning: run `python3 bp_props.py --list-markets` and show the user.

**Projections:** only 3PM and P+R carry real BettingPros projections. PTS, REB, AST, P+A and PRA come as whole-number side leans (nearly always the line ±0.5): the board shows "BP lean: over/under", ranks them on form, DvP and market value, gives them no edge/EV/Kelly, and never logs them as strategy picks. Say so when reporting; value in those markets comes only from the market check (BETs).

**Bets vs. research:** only market BETs are bets (stake shown as ¼K). Strategy lists are "model picks (research, not a bet)" with no stake; always report their market EV and say when it's below 0. Report the record as two groups: BETs (market +EV) and model picks (research), including 3PM overs.

## 2. Read the results

Read `bp_top10_<date>.md`, which the script writes. It has:
1. **Top 10 by projection gap**: |projection - line| as a % of the line, nothing else.
2. **Top 10 by your score**: the user's rules, `score` = 40% edge (gap in SDs) + 35% EV at FanDuel's price + 25% matchup, cut for injury.
3. **Top 10 by balanced score**: `balanced` = 40% score + 25% recent form + 20% market value (no-vig price, FanDuel line vs. consensus) + 15% BettingPros bet rating.
4. For each of the eight categories (PTS, REB, AST, 3PM, P+A, P+R, PRA, R+A): a top 10 by projection gap and a top 10 by your score.

Every row also shows Form (recent hit rate vs. the line), Signals (how many of six factors lean the same way) and Kelly (quarter-Kelly stake, 1u = 1% of bankroll). For anything else (a player or team filter, extra columns such as `s_edge`, `s_ev`, `s_dvp`, `s_form`, `s_market`, `nv_edge`, `streak`), read `bp_props_<date>.csv` with a short Python snippet; don't print the whole file.

## 3. Update the dashboard

The script writes `bp_dashboard.html` (the NBA Prop Board). Publish it with the Artifact tool, using `file_path` = the absolute path to `bp_dashboard.html`:
- If `.dashboard_url` exists, read it and pass that URL as `url` so the same link updates. If the publish is refused because this conversation hasn't read the artifact, do `action: "read"` on that URL first, then publish the new local file.
- If `.dashboard_url` doesn't exist, publish without `url` (icon `chart`) and save the returned link into `.dashboard_url`.

Include the dashboard link in the report. Don't upload anything to Google Drive (the user has no Drive storage left).

## 4. Report

Show the three overall top-10 lists as compact tables:
- **By gap:** player | market | side | line | proj | gap% | FD | score | balanced
- **By your score:** player | market | side | line | FD | DvP | score | balanced | Kelly
- **By balanced:** player | market | side | line | FD | form | score | balanced | signals | Kelly

Then, for each of the eight categories, give the top 3 by gap and the top 3 by your score on one line each (player side line, gap %, score), and tell the user the full lists are in `bp_top10_<date>.md`. If a category is empty, say so.

If the script printed "Graded <date>", start the report with a **Results** line: the running record from the printed Record block (W-L, units, Brier vs. market, CLV) by strategy and for "All picks", and how many picks didn't count because they were logged after tip. Mention any situation in the model's insights that stands out (for example, form when a teammate is ruled out) once it has a real sample.

After that, give 2-4 short bullets that point out:
- plays near the top of all three lists, which are the strongest; say when a play's projection is a placeholder (`placeholder` column), since its gap isn't a real edge,
- props where a teammate is ruled out (`tm_out_names` in the CSV), since a bigger minutes load can beat weak recent form,
- big gaps or high scores that the balancing signals disagree with (poor form, bad market value, juiced odds, injury), and why,
- the request count the script printed, so the user knows how much of today's allowance is left.

Keep it short and factual. These are projections, not guarantees; add no further disclaimers beyond one line.

## Daily flow

0. **Morning:** `python3 bp_schedule.py` (free): earliest tip, suggested first pull time (earliest tip - 90 min), request estimate. Tell the user and ask for the pull at that time. Once a week it also checks CrazyNinja's API page (free GET); if it prints "NEW endpoint" or odds wording, tell the user an odds endpoint may have appeared.
1. **First BettingPros full pull** at earliest tip - 90 min (ask first, with the budget math). Shortlist = top 10 by Balanced, real projections only.
2. **Market check of the whole board:** `python3 bp_value.py --board` (plan, no call), then ask; after the OK, `python3 bp_value.py --board --go [--max-credits N]`. It first refreshes Unabated discovery (free; current player ids), then one Unabated props odds call (free; 1 per 5 s) prices both sides of every FanDuel main line in the 7 markets, including FanDuel lines BettingPros doesn't carry (no projection, so "+EV only"): fair = median of devigged books + exchanges at FanDuel's line (3+ sources; pick'em apps excluded). The Odds API is used only for shortlist plays Unabated can't price, and only with `--max-credits` the user approved (4+ books required there).
3. **BET** = market EV >= +2% at FanDuel's line with 3+ sources (Unabated) or 4+ books (The Odds API), **and** CrazyNinja's median worst-case EV > 0 (flag "no CNO check" if CNO is down); CrazyNinja +EV (median worst-case ≥ +2%) is logged as its own record; stake = quarter-Kelly (¼K, 1u = 1% of bankroll) on the fair probability, capped at 1.25u until 50 graded BETs show positive average CLV (then 2.5u), and by exposure caps: 1.5u per player (all props) and 3.5u per game, scaled down proportionally when they bind; report "capped: player" / "capped: game" next to the stake. Each BET is tagged "+EV + proj agrees" (BettingPros' projection is on the same side) or "+EV only" (other side or no projection), with form and DvP for the bet side. Both kinds are kept and logged as separate ledger strategies (`bet_agree`, `bet_only`); shortlist plays that aren't BETs are `watch`. Re-score with `python3 bp_props.py --from-csv` so the board and Copy for Claude show them.
3b. **Watch (ask once a day, right after the first pull):** ask once whether to start `python3 bp_value.py --watch`; if yes, run it in the background (`run_in_background`). It starts once the day's first pull exists and runs until the last tip: one free Unabated scan every 20 minutes plus one 5 minutes before each tip, keeping the Mac awake (caffeinate). Alerts (terminal, macOS notification, log) only on new BETs (logged with their tag) and logged BETs whose market EV fell below 0 or whose FanDuel line moved. **Auto-CLV:** the last scan before each game's tip logs closing fair price, CLV and market EV now for every BET in that game, and alerts "EV FELL BELOW 0" when it did. Don't ask again that day.
4. **Re-price** (optional, before the bulk of the tips): `python3 bp_props.py --reprice` (BettingPros `/offers`) refreshes model-pick lines. BET CLV comes from the watch, so `bp_value.py --close` is only needed when the watch isn't running (one free Unabated call; ask first). No Odds API call unless the user asks.

Report the BET plays first (tag, line, price, market EV, fair source and count, exchange included, form, DvP, stake; after tips, the watch's closing CLV and any flag), then the watch list, then the usual lists.

## Sims for days without a live pull (research, kept separate)

Every finished slate with no live pull gets simmed with `bp_sim.py` (model picks only, written to `sim/`, never the real ledger, model, Kelly cap or dashboard). Each sim pull uses BettingPros requests (≈1.5 per game), so ask first like any other call.

- **Only leftover requests, newest first. Never delay or trim a live pull for a sim.** `python3 bp_sim.py --backfill` (plan only, 0 requests) shows the window's used count, the live flow it reserves (every unpulled live pull and re-price whose time falls before the 7 PM ET reset, from ESPN's schedule) and the spare left; `--go` never spends more than that spare. Offer a sim only when the spare is above 0, after the day's live flow is safe.
- After the OK: `python3 bp_sim.py --backfill --go --max-requests <approved>` (newest dates first, hard stop) or `python3 bp_sim.py --date YYYY-MM-DD --max-requests N` for one date.
- `python3 bp_sim.py --record` prints the sim record (0 requests). Report it apart from the real record and say it's a sim: near-closing lines, form with that game removed, no market BETs.

## Boosts

`python3 bp_boost.py "<player>" <market> <over|under> <FanDuel price> --boost <pct> --type profit|all` plans; add `--go` after the user's OK (one free Unabated odds call). Report the CrazyNinja boosted EV, lowest source and ¼K stake.

## CrazyNinja screener paste

Your rule (your score): every paste also feeds the day's store, and any +EV prop where BettingPros agrees is logged once as "Your rule". After each paste, report the new ones and today's list (section 2 of bp_top10_<date>.md).

If the pasted games are already live, run `python3 bp_value.py --cno-paste <file> --live` instead (display only): each NBA FanDuel row vs. BettingPros' projection/lean and the halftime expected final, with the stat now.

When the user pastes CrazyNinja's +EV screener text, save it to a scratch file and run `python3 bp_value.py --cno-paste <file> --dry` first, then without `--dry` to log (no API calls). It keeps NBA player props at FanDuel with EV >= +2% whose game hasn't started (tip from the screener's "Today at 8:00 PM"), and logs them as the "CNO screener" record (¼K on fair prob, same caps). Report kept vs. dropped by reason, each play's BettingPros projection number and gap vs. the line (3PM/P+R: proj agrees/disagrees; other markets: lean, marked lean only; "–" if not in the pull), and any settings WARNING. Pass `--devig/--min-books/--max-odds` when the user states their screener settings; otherwise the last paste's are assumed. The screener bar stays +2%.

When the user sends props in chat, run `python3 bp_value.py --proj "<player> <market> <o|u><line>; ..."` (0 requests) and show the BettingPros context next to each. Display only: it never changes a BET decision.

## Halftime live (experimental, own record)

On game nights, after the watch, run `python3 bp_halftime.py --watch` in the background (free ESPN polling). It runs until the last game's halftime without restarting: at each halftime it saves the snapshot, sends the Mac notification and prints one `HALFTIME ALERT` line. Follow it with a Monitor on `tail -f halftime_<date>.log | grep --line-buffered -E "HALFTIME ALERT|failed|Traceback|Watch done"` so each alert reaches the session; report the flags (skip flags shown) and ask the user to paste live odds. When they paste, save it and run `python3 bp_value.py --half-paste <file> --dry`, then without `--dry` to log HALF BETs. Paste format: `Player PTS o14.5 FD -110 | DK -125/-105 | MGM -120/-110 | Novig -118/-108`. Never mix HALF BETs into the pregame records. The Odds API live test is one call only, asked first.

## R+A and the alt ladder (from 11/24)

R+A (market 337) is in every pull again: after the first 11/24 pull, report the actual extra request cost (R+A share of /offers and /props pages in the pull log vs. the ≈5.7/game estimate). The board and watch also run the alt ladder (bp_alts.py): report new "ALT [alt, market only]" / "ALT [alt, market + projection]" rungs with EV, sources, CNO worst case, stake and the BettingPros context.

## Records and rule versions

Report the v2 record (current rules, from 2026-11-24) by default; mention the v1 archive only when asked. When the user changes a rule, update RECORDS.md and add a version to `RULE_VERSIONS` in bp_learn.py.

