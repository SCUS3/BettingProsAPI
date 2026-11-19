# BettingPros NBA Props Puller

This repository contains a Python script that pulls the day's NBA player props from the BettingPros Consumer API, priced at FanDuel, and ranks them by the gap between the BettingPros projection and the sportsbook line.

## Description

The script finds the player prop markets for a slate, collects every prop with its BettingPros projection, recommended side, and FanDuel odds, and writes the results to a CSV ranked by percentage projection gap. Every prop gets a 0-100 score that combines the projection edge, expected value at FanDuel's price, and the matchup. It also writes a Markdown note with the top 10 by score and the 10 biggest discrepancies in each of the seven categories, and prints the top 15 FanDuel-priced props to the terminal.

Markets pulled by default (full game only):

- Points, Rebounds, Assists, Threes
- Points + Assists, Points + Rebounds, Points + Rebounds + Assists

## Features

- **Projection Gap Ranking**: Sorts props by how far the BettingPros projection sits from the line, as a percentage of the line.
- **0-100 Prop Score**: Combines projection edge, FanDuel EV, and matchup into a single rating, reduced for injury status (see [Scoring](#scoring)).
- **Top 10 by Category**: A separate note lists the 10 biggest projection-vs-line gaps for each of the seven prop categories.
- **FanDuel Pricing**: Shows the FanDuel odds for the recommended side and the implied probability.
- **Matchup Context**: Includes opponent defense-vs-position rank (converted so 1 = softest matchup) and injury status.
- **Fair Odds / Market EV**: Optional `--offers` mode adds FanDuel fair probability and market expected value per prop.
- **Request Budgeting**: Caches book and market IDs in `bp_cache.json` and backs off on rate limits, so runs fit inside the daily API allowance.
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
```

## Output

Each run writes two files.

**`bp_top10_<date>.md`**: the top 10 props overall by score, then the 10 biggest discrepancies in each category (Points, Rebounds, Assists, Threes, Pts + Ast, Pts + Reb, Pts + Reb + Ast). FanDuel-priced props only, with line, projection, gap, odds, matchup, injury status, and score.

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

## Scoring

Each prop's score is a weighted blend of three parts, each scaled 0-100:

| Part | Weight | How it's measured |
| --- | --- | --- |
| **Edge** | 40% | Gap between projection and line in standard deviations (SD ≈ 1.1 × √projection). 0.75 SD or more earns full marks. Measuring in SDs keeps a 1.5-line three-pointer prop and a 25.5-line points prop on equal footing. |
| **EV** | 35% | Win chance for the recommended side (BettingPros' probability when the key provides it, otherwise estimated from the projection) against FanDuel's odds, blended with BettingPros / FanDuel market EV when available. -10% EV scores 0 and +20% scores 100, so heavily juiced prices are penalized. |
| **Matchup** | 25% | Opponent defense vs. position. A soft defense raises an over; a tough defense raises an under. |

The total is then multiplied by an injury factor: ×0.85 for day-to-day or questionable, ×0.5 for doubtful, 0 for out.

The score is a ranking aid built from public projections, not a win probability.

## Using with Claude Code

The repo includes a `/props` skill for [Claude Code](https://claude.com/claude-code). Open Claude Code in the project folder and type:

```
/props                 # tonight's slate, summarized
/props offers          # include FanDuel fair odds / EV
/props 2026-11-19      # a specific date
/props Celtics         # filter to a player or team
```

Claude runs the script, reads the results, and summarizes the top plays by score and the per-category top 10. It is blocked from reading `.env`, so the API key never appears in the chat.

## Notes

This project is for research and entertainment. Projections are not guarantees, so bet responsibly.
