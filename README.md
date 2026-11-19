# BettingPros NBA Props Puller

This repository contains a Python script that pulls the day's NBA player props from the BettingPros Consumer API, priced at FanDuel, and ranks them by the gap between the BettingPros projection and the sportsbook line.

## Description

The script finds the player prop markets for a slate, collects every prop with its BettingPros projection, recommended side, and FanDuel odds, and writes the results to a CSV ranked by percentage projection gap. It also prints the top 15 FanDuel-priced props to the terminal.

Markets pulled by default (full game only):

- Points, Rebounds, Assists, Threes
- Points + Assists, Points + Rebounds, Points + Rebounds + Assists

## Features

- **Projection Gap Ranking**: Sorts props by how far the BettingPros projection sits from the line, as a percentage of the line.
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
```

## Output

Each run writes `bp_props_<date>.csv` with one row per prop, including:

| Column | Meaning |
| --- | --- |
| `player`, `team`, `injury` | Player info |
| `market`, `rec_side`, `line` | Prop, recommended side, and line |
| `bp_proj`, `diff`, `pct_gap` | BettingPros projection and gap from the line |
| `fd_odds_rec`, `implied_prob` | FanDuel odds for the recommended side and implied probability |
| `bp_win_prob`, `bp_ev`, `bp_rating` | BettingPros probability, EV, and rating (Plus/Elite keys only) |
| `fd_fair_prob`, `fd_market_ev` | FanDuel fair probability and market EV (`--offers` only) |
| `dvp_softness` | Opponent defense vs. position, 1 = softest |

## Notes

This project is for research and entertainment. Projections are not guarantees, so bet responsibly.
