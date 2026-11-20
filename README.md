# BettingPros NBA Props Puller

This repository contains a Python script that pulls the day's NBA player props from the BettingPros Consumer API, priced at FanDuel, and ranks them by the gap between the BettingPros projection and the sportsbook line.

## Description

The script finds the player prop markets for a slate, collects every prop with its BettingPros projection, recommended side, and FanDuel odds, and writes the results to a CSV ranked by percentage projection gap. Every prop gets two 0-100 ratings: a **score** (projection edge, expected value at FanDuel's price, and matchup) and a **balanced score** that checks it against recent form, market value, and BettingPros' bet rating. It also writes a Markdown note with three top-10 lists (by projection gap, by score, and by balanced score) plus, for each of the eight categories, a top 10 by projection gap and a top 10 by score, and prints the top 15 FanDuel-priced props to the terminal.

Markets pulled by default (full game only):

- Points, Rebounds, Assists, Threes
- Points + Assists, Points + Rebounds, Points + Rebounds + Assists

## Features

- **Projection Gap Ranking**: Sorts props by how far the BettingPros projection sits from the line, as a percentage of the line.
- **0-100 Prop Score**: Combines projection edge, FanDuel EV, and matchup into a single rating, reduced for injury status (see [Scoring](#scoring)).
- **Balanced Score**: Blends the score with recent hit rate vs. the line, FanDuel's no-vig price, line vs. consensus, and BettingPros' bet rating.
- **Three Top-10 Lists**: Separate rankings by raw projection gap, by score, and by balanced score.
- **Kelly Stakes**: Suggested quarter-Kelly stake per prop (1u = 1% of bankroll).
- **Top 10 by Category**: For each of the eight prop categories (PTS, REB, AST, 3PM, P+A, P+R, PRA, R+A), a top 10 by projection gap and a top 10 by score.
- **FanDuel Pricing**: Shows the FanDuel odds for the recommended side and the implied probability.
- **Matchup Context**: Includes opponent defense-vs-position rank (converted so 1 = softest matchup) and injury status.
- **Fair Odds / Market EV**: Optional `--offers` mode adds FanDuel fair probability and market expected value per prop.
- **Request Budgeting**: Caches book and market IDs in `bp_cache.json` and backs off on rate limits, so runs fit inside the daily API allowance.
- **Self-Learning Record**: Logs the top picks of each strategy every slate, grades them automatically after the games, tracks the record and units, and re-learns how much each factor (including teammates being ruled out) actually predicts a win.
- **Interactive Dashboard**: One page with every list, filters, score breakdowns, and a copy-to-Claude button.
- **No Dependencies**: Uses only the Python standard library.

## Technologies Used

- **Python 3.9+**
- **BettingPros Consumer API (v3)**

## Getting Started

### Prerequisites

You need a BettingPros API key. Free/Plus keys allow 100 requests per day and 20 props per page; Elite allows 1,000 per day and 50 per page. The allowance resets at midnight UTC.

1. Clone the repository:
```bash
   git clone https://github.com/SCUS3/BettingProsAPI.git
   cd BettingProsAPI
```

2. Add your API key to a `.env` file in the project folder (it is git-ignored and never committed):
```bash
   echo "BP_API_KEY=your-key-here" > .env
```

3. Run the script:
```bash
   python3 bp_props.py
```

## Usage

```bash
python3 bp_props.py                    # today's slate (Eastern time)
python3 bp_props.py --date 2026-11-19  # a specific date
python3 bp_props.py --offers           # add FanDuel fair odds / market EV (more requests)
python3 bp_props.py --list-markets     # show every NBA prop market and which are selected
python3 bp_props.py --markets 156:157  # pick markets by ID
python3 bp_props.py --page-limit 50    # Elite keys
python3 bp_props.py --location NJ      # sportsbook location (default NY)
python3 bp_props.py --refresh          # re-fetch cached book and market IDs
python3 bp_props.py --from-csv         # re-score today's saved CSV, no API requests
python3 bp_props.py --grade 2026-11-18 # grade a finished slate and re-learn
python3 bp_props.py --no-grade         # skip automatic grading this run
python3 bp_props.py --measure          # 3 requests: slate prop counts with/without the FanDuel filter and default ev_threshold
```

## Output

Each run writes two files.

**`bp_top10_<date>.md`**: FanDuel-priced props only.

1. Top 10 by projection gap
2. Top 10 by score
3. Top 10 by balanced score
4. For each category (Points, Rebounds, Assists, Threes, Pts + Ast, Pts + Reb, Pts + Reb + Ast): top 10 by projection gap and top 10 by score

Each row shows line, projection, gap, FanDuel odds, matchup, recent form, score, balanced score, signal agreement, Kelly stake, and injury status.

**`bp_props_<date>.csv`**: every prop, one row each, including:

| Column | Meaning |
| --- | --- |
| `player`, `team`, `injury` | Player info |
| `market`, `rec_side`, `line` | Prop, recommended side, and line |
| `bp_proj`, `diff`, `pct_gap` | BettingPros projection and gap from the line |
| `fd_odds_rec`, `implied_prob` | FanDuel odds for the recommended side and implied probability |
| `bp_win_prob`, `bp_ev`, `bp_rating` | BettingPros probability, EV, and rating (Plus/Elite keys only) |
| `fd_fair_prob`, `fd_market_ev` | FanDuel fair probability and market EV (`--offers` only) |
| `dvp_softness` | Opponent defense vs. position, 1 = softest |
| `model_prob`, `model_ev` | Estimated win chance for the recommended side and EV at FanDuel's odds |
| `s_edge`, `s_ev`, `s_dvp` | The three score parts, each 0-100 |
| `score` | Final 0-100 score |
| `over_odds`, `under_odds`, `consensus_line`, `consensus_odds` | Both FanDuel sides and the cross-book consensus |
| `form_l5` … `form_h2h`, `streak` | Recommended side's hits vs. games against this line (e.g. `7-10`) |
| `form_rate`, `nv_edge` | Shrunk recent hit rate; model probability minus FanDuel's no-vig probability |
| `s_form`, `s_market`, `s_bp` | Balancing parts, each 0-100 |
| `balanced`, `signals`, `kelly_u` | Balanced score, factors in agreement (of 6), quarter-Kelly units |
| `is_scored`, `actual` | Final stat once the game is graded, for back-testing |
| `tm_out`, `tm_out_names`, `tm_dtd` | Teammates ruled out (by name) and teammates day-to-day |
| `learned_prob`, `learned` | Model's win probability and learned score (0-100) |
| `placeholder` | Whole-number projection exactly 0.5 off the line |
| `tip`, `pulled_at` | Game start and this run's pull time (UTC) |

## Scoring

Each prop's score is a weighted blend of three parts, each scaled 0-100:

| Part | Weight | How it's measured |
| --- | --- | --- |
| **Edge** | 40% | Gap between projection and line in standard deviations (SD ≈ 1.1 × √projection). 0.75 SD or more earns full marks. Measuring in SDs keeps a 1.5-line three-pointer prop and a 25.5-line points prop on equal footing. |
| **EV** | 35% | Win chance for the recommended side (BettingPros' probability when the key provides it, otherwise estimated from the projection) against FanDuel's odds, blended with BettingPros / FanDuel market EV when available. -10% EV scores 0 and +20% scores 100, so heavily juiced prices are penalized. |
| **Matchup** | 25% | Opponent defense vs. position. A soft defense raises an over; a tough defense raises an under. |

The total is then multiplied by an injury factor: ×0.85 for day-to-day or questionable, ×0.5 for doubtful, 0 for out.

### Balanced score

The balanced score keeps your score as its largest part and checks it from independent angles:

| Part | Weight | How it's measured |
| --- | --- | --- |
| **Your score** | 40% | As above. |
| **Form** | 25% | Hit rate of the recommended side against this line over the last 5, 10, and 20 games, the season, and head-to-head, weighted toward the last 10-20 and shrunk toward 50% on small samples. 35% scores 0 and 75% scores 100. |
| **Market value** | 20% | Model win chance minus FanDuel's no-vig probability (the price with the bookmaker's margin removed), plus whether FanDuel's line is friendlier than the consensus line across books. |
| **BettingPros rating** | 15% | BettingPros' own 1-5 bet rating (Plus/Elite keys; neutral otherwise). |

Missing data scores a neutral 50. The injury factor applies here too.

**Signals** counts how many of the six parts (edge, EV, matchup, form, market, rating) score 60 or higher. **Kelly** is a quarter-Kelly stake using the model's win chance blended 50/50 with FanDuel's no-vig probability, capped at 2.5u. 0u means no edge at that price.

The scores are ranking aids built from public projections, not win probabilities.

## Using with Claude Code

The repo includes a `/props` skill for [Claude Code](https://claude.com/claude-code). Open Claude Code in the project folder and type:

```
/props                 # tonight's slate, summarized
/props offers          # include FanDuel fair odds / EV
/props 2026-11-19      # a specific date
/props Celtics         # filter to a player or team
```

Claude runs the script, reads the results, and summarizes the three overall top-10 lists and the per-category lists.

### Record and self-learning

Every live run stamps its props with the pull time and each game's tip time (from `/events`), saves a snapshot in `history/<date>/`, and logs the top 5 picks from four strategies (balanced, your score, projection gap, learned) to `bp_ledger.csv`. The next run grades finished slates against the final stats BettingPros reports.

- **Per-game cutoff**: a pick counts only if it came from the last run before *that game's* tip. Picks logged after a game started never count, and an earlier run's pick is replaced by the last pre-tip run. Learning data comes from the same pre-tip snapshot for each game.
- **Scoreboard**: for each strategy, W-L-P, hit rate, flat-stake units and ROI, Kelly-stake units, **Brier score and log loss** of our probability vs. FanDuel's no-vig probability (lower is better), and **closing line value**: points gained between the first pre-tip run that logged the pick and the last run before tip.
- **Combined record**: an "All picks" row and the by-category table count each prop once, even when it's on several lists, including in Kelly totals.
- **Learning**: every graded FanDuel prop goes into `bp_graded.csv`, weighted so **each player-game counts once** (a player's PTS, P+A, P+R and PRA in one game share a single sample). An L2-regularized logistic regression learns how much each factor predicts the recommended side hitting: edge, EV, matchup, form, market value, price, side, the player's injury tag, teammates out or day-to-day, form when teammates are out, market type, the placeholder flag, and projected-starter status. It starts after 60 player-games.
- **Validation and cap**: the model is trained on earlier slates and tested on later ones against a Balanced-only model. The learned score blends model and balanced by sample size (n / (n + 400)) and is **capped at 50% model** until the model has lower held-out log loss than Balanced on 100+ test player-games; after that the cap is 80%.
- **Placeholder projections**: a whole-number projection exactly 0.5 off the line is flagged as a placeholder, not a real projection. It claims zero edge, its EV uses FanDuel's no-vig probability (so EV reflects only the price), and it ranks below every prop with a real projection in all lists and picks unless the price alone is +EV (FanDuel market EV > 0, available with `--offers`). It is left out of the model's edge weight, and the flag is a model feature.
- **Lineups and injuries (Rotowire)**: each live run reads Rotowire's public [NBA lineups page](https://www.rotowire.com/basketball/nba-lineups.php) once (allowed by its robots.txt; no API request) for projected starters, injury status (Out, Ques, GTD) and play probability, saved in `history/` so re-scores and learning use the pre-tip view. Rotowire Out sets the player's own injury to OUT.
- **Teammates out**: a rotation regular (points props in recent slates, average line 9.5+) counts as out when Rotowire lists him Out, or he has no props and Rotowire doesn't list him as playing. A regular Rotowire shows starting is not counted. Long-term injuries drop out of the regulars, so this flags new absences. Without Rotowire data it falls back to missing props.

Grading costs about 18 requests the first time a slate is graded (at most 3 slates per run), and each live run adds 1 request for tip times. Final stats already saved are reused, so a regrade only looks up what is still missing: FanDuel often takes a line down before the game ends, which drops that prop from the FanDuel-filtered pull, so each pick still missing a final stat is looked up by game and market with no book filter (1 request each, at most 8 per slate). A pick whose game and market are final but whose player has no stat (did not play) is graded **V** (void, no action) and left out of the record. Use `--no-grade` to skip grading, or `--grade YYYY-MM-DD` to grade one slate.

### Dashboard

Each run also writes `bp_dashboard.html`, the **NBA Prop Board**: a single interactive page built from `dashboard_template.html`. It has:

- a **Record** tab with each strategy's record, results by category, recent graded picks, and what the model has learned
- a "strongest tonight" strip of props in the top 20 on gap, score, and balanced
- a category picker (All, PTS, REB, AST, 3PM, P+A, P+R, PRA, R+A) with ranking by Gap, Score, or Balanced
- tap-to-expand score breakdowns
- a **Copy for Claude** button that copies the full slate as compact text for any Claude chat, led by a record summary: each strategy's W-L-P, units and ROI, the combined each-prop-once row, the last graded slate's picks with results and final stats, the model's sample count vs. the 60 it needs, and model vs. market Brier score

`/props` publishes it as a private claude.ai artifact and updates the same link every day (stored in git-ignored `.dashboard_url`).

## Notes

This project is for research and entertainment. Projections are not guarantees, so bet responsibly.
