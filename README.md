# BettingPros NBA Props

Python tools that price every FanDuel NBA player prop against BettingPros projections and the wider market, pick bets with a market edge, and grade every pick after the games so the model learns what actually wins.

## Description

Each day the tools:

1. **Pull the board** (`bp_props.py`): every FanDuel main line from BettingPros `/offers`, joined to BettingPros projections from `/props` for the same players. Each prop is re-scored at FanDuel's own line and price and gets two 0-100 ratings, a **score** (projection edge, EV at FanDuel's price, matchup) and a **balanced score** (score checked against recent form, market value and BettingPros' bet rating).
2. **Check the market** (`bp_value.py`): one free Unabated call prices both sides of every FanDuel line from a consensus of devigged sportsbooks and exchanges. A **BET** is a line with market EV of +2% or more at FanDuel's line, tagged by whether BettingPros' projection agrees. The Odds API is a fallback for shortlist plays Unabated can't price.
3. **Track and learn** (`bp_learn.py`): logs picks from several strategies, grades them after the games, reports W-L, units, ROI, Brier score and closing line value, and fits a model on graded props.

Markets (full game): Points, Rebounds, Assists, Threes, Pts + Ast, Pts + Reb, Pts + Reb + Ast. Reb + Ast is built in but off (`DISABLED` in `bp_props.py`), since BettingPros carries no FanDuel R+A lines.

## Features

- **Every FanDuel line**: `/offers` (FanDuel main lines, all games in one query) first, then `/props` projections only for the players FanDuel prices. Side, gap, EV, Kelly and placeholder status are computed at FanDuel's line; when BettingPros' line differs, both are shown.
- **Market fair price**: Unabated consensus (median of devigged DraftKings, BetMGM, Caesars, Fanatics, theScore, Novig, ProphetX, Kalshi and Polymarket at FanDuel's line, 3+ sources; pick'em apps excluded), with The Odds API as fallback.
- **BET tags**: "+EV + proj agrees" vs. "+EV only", with form and DvP for the bet side. Both are recorded separately so the record shows which does better.
- **Closing check**: a 6:30 PM re-price logs CLV (EV of the bet at the closing fair price) and flags BETs whose market EV fell below 0.
- **Scores and lists**: top 10 by projection gap, score and balanced score, overall and per category, plus lines with no projection.
- **Kelly stakes**: BETs at quarter-Kelly (¼K) on the market's fair probability, capped at 1.25u until 50 graded BETs show positive average CLV; model stakes halved until the model is calibrated.
- **Lineups and injuries**: Rotowire projected lineups and injury status, including teammates ruled out.
- **Self-learning record**: per-game pre-tip cutoff, automatic grading, voids for players who didn't play, and a model that is validated on later slates before it's trusted.
- **Request budgeting**: per-day request counter, budget math before each run, page-1 estimates, a `--max-requests` cap and a big-slate rule.
- **Interactive dashboard**: one page with every list, BET and watch tags, market EV and source, the record, and a Copy for Claude button.
- **No dependencies**: Python standard library only.

## Technologies Used

- **Python 3.9+**
- **BettingPros Consumer API (v3)**: projections, FanDuel lines, final stats
- **Unabated API**: market fair prices (primary)
- **The Odds API (v4)**: market fair prices (fallback)
- **Rotowire** public NBA lineups page

## Getting Started

### Prerequisites

| Service | Limit | `.env` key |
| --- | --- | --- |
| BettingPros | 100 requests per day on Free/Plus (1,000 on Elite), reset at midnight UTC | `BP_API_KEY` |
| Unabated | 1 odds call every 5 s per key (Free API); discovery calls don't count | `UNABATED_API_KEY` |
| The Odds API | 500 credits per month | `ODDS_API_KEY` |

Only `BP_API_KEY` is required; without the others the market check is skipped or falls back.

1. Clone the repository:
```bash
   git clone https://github.com/SCUS3/BettingProsAPI.git
   cd BettingProsAPI
```

2. Add your keys to `.env` (git-ignored, never committed):
```bash
   echo "BP_API_KEY=your-key-here" > .env
   echo "UNABATED_API_KEY=your-key-here" >> .env
   echo "ODDS_API_KEY=your-key-here" >> .env
```

3. Check Unabated once (discovery only):
```bash
   python3 bp_unabated.py --discover
```

4. Run the daily pull:
```bash
   python3 bp_props.py --max-requests 45
```

## Daily Flow

| Time (ET) | Step | Cost |
| --- | --- | --- |
| First run | Grade the previous slate | 0 BettingPros requests (ESPN / NBA.com box scores); BettingPros only for a game no box score covers |
| 2:00 PM | `python3 bp_props.py --max-requests N`: full pull | about 5.7 requests per game + 1 |
| After 2 PM | `python3 bp_value.py --board --go [--max-credits N]`: market check of every FanDuel line | 1 Unabated call; Odds API credits only for the fallback |
| Noon to last tip | `python3 bp_value.py --watch`: free Unabated scan every 20 min; alerts only on new BETs and BETs whose market EV fell below 0 | 1 Unabated call per scan |
| 6:30 PM | `python3 bp_props.py --reprice`: FanDuel lines from `/offers` only | about 2.4 requests per game |
| 6:30 PM | `python3 bp_value.py --close --go`: CLV for each BET | 1 Unabated call |

Without `--go`, `bp_value.py` only prints its plan and cost. Every step prints its budget math first.

**Big slates**: the 2 PM pull is never trimmed. If the full 6:30 re-price won't fit in what's left of the day, it re-prices only the games with logged picks; if even that won't fit, it's skipped and the 2 PM picks count (`--force-reprice` overrides).

## Usage

```bash
# BettingPros board
python3 bp_props.py --max-requests 45  # full pull; stops after the page-1 requests if the run would need more
python3 bp_props.py --reprice          # 6:30 PM re-price from /offers (picks from this run count)
python3 bp_props.py --from-csv         # re-score today's saved CSV, no requests
python3 bp_props.py --date 2026-11-19  # a specific date
python3 bp_props.py --grade 2026-11-18 # grade one finished slate and re-learn
python3 bp_props.py --no-grade         # skip automatic grading this run
python3 bp_props.py --unfiltered-props # projections for every player (if the player-id filter misses)
python3 bp_props.py --force-reprice    # with --reprice: run a full re-price despite the budget rule
python3 bp_props.py --measure          # 3 requests: slate prop counts with/without the FanDuel filter
python3 bp_props.py --list-markets     # every NBA prop market and which are selected
python3 bp_props.py --location NJ      # sportsbook location for the FanDuel book lookup (default NY)

# Market check
python3 bp_value.py --board                        # plan: cost of pricing every FanDuel line
python3 bp_value.py --board --go --max-credits 4   # run: Unabated for the board, Odds API fallback up to 4 credits
python3 bp_value.py --close --go                   # 6:30 PM: CLV for today's BETs
python3 bp_value.py --watch                        # noon to last tip: alerts on new BETs and BETs gone below 0 EV
python3 bp_value.py --board --go --board-file F    # price a saved Unabated board (no call)
python3 bp_value.py --top 10                       # plan an Odds API check of the top 10 by Balanced only
python3 bp_unabated.py --discover                  # Unabated discovery (no odds calls)
python3 bp_unabated.py --status                    # what discovery found and whether Unabated is used
```

## Market Check and BETs

**Unabated (primary).** Each `--board` run first refreshes discovery (free) so new players map, then one props odds call returns the whole NBA board. Every FanDuel line on it is priced, including lines BettingPros doesn't carry (no projection). For each FanDuel line, the fair probability is the **median of devigged (multiplicative) non-FanDuel sources pricing both sides at FanDuel's line**: DraftKings, BetMGM, Caesars, Fanatics and theScore US, plus the exchanges and prediction markets Novig, ProphetX, Kalshi and Polymarket. Pick'em apps (PrizePicks, Underdog, Pick6) and duplicate feeds are excluded, and live, alternate and partial-game lines are ignored. It needs 3+ sources. If the key ever returns the Unabated Line for props, that is used instead. A 401/403 switches to The Odds API automatically.

**The Odds API (fallback).** Only for shortlist plays (top 10 by Balanced, real projections) that Unabated can't price, within an 8-credit daily aim and the credits you approve. FanDuel plus 9 US books counts as one region; the fair probability is the median of the non-FanDuel books at FanDuel's line, and needs 4+ books. Credits are tracked from the response headers in `odds_credits.json`, and runs stop before going under 50 for the month.

**BET rule.** Market EV = fair probability × FanDuel decimal odds − 1. A BET needs EV ≥ +2% at FanDuel's line, with 3+ sources (Unabated) or 4+ books (The Odds API). The stake is quarter-Kelly (shown as "¼K"; 1u = 1% of bankroll) on the fair probability, capped at **1.25u until 50 graded BETs show positive average CLV**, then 2.5u. Each BET is tagged:

- **+EV + proj agrees**: BettingPros' projection is on the same side.
- **+EV only**: the projection is on the other side, or there is none.

Shortlist plays that aren't BETs are logged as **watch**. At 6:30 the closing check logs **CLV** (EV of the bet taken, at the 6:30 fair price) and flags any BET whose market EV at FanDuel's current price fell below 0.

## Scoring

Each prop's **score** blends three parts, each 0-100:

| Part | Weight | How it's measured |
| --- | --- | --- |
| **Edge** | 40% | Gap between projection and FanDuel's line in standard deviations (SD ≈ 1.1 × √projection). 0.75 SD or more earns full marks. |
| **EV** | 35% | Win chance for the side (BettingPros' probability when it was priced at the same line and side, else estimated from the projection) against FanDuel's odds, blended with FanDuel's market EV from `/offers`. -10% scores 0 and +20% scores 100. |
| **Matchup** | 25% | Opponent defense vs. position. A soft defense raises an over; a tough defense raises an under. |

The total is multiplied by an injury factor: ×0.85 for day-to-day or questionable, ×0.5 for doubtful, 0 for out.

The **balanced score** is 40% score, 25% form (hit rate of the side vs. the line over recent windows, shrunk toward 50%), 20% market value (model vs. FanDuel's no-vig probability, and FanDuel's line vs. the consensus line) and 15% BettingPros' bet rating. Missing data scores a neutral 50. **Signals** counts how many of the six parts score 60 or higher.

**Real projections only for 3PM and P+R.** BettingPros publishes PTS, REB, AST, P+A and PRA projections as whole numbers, nearly always the line ±0.5 (100% whole in every saved pull; P+R and 3PM carry decimals, and the pipeline doesn't round). For those five markets the projection is treated as a side lean: shown as "BP lean: over/under", ranked on form, matchup and market value, with no edge, projection EV, Kelly or logged strategy pick, and the model's projection-gap feature is left out. Value in every category comes from the market check, which doesn't use BettingPros projections.

**Placeholder projections** (3PM or P+R projections that are a whole number exactly 0.5 off the line) claim no edge, use FanDuel's no-vig price for EV, and rank below real projections unless the price alone is +EV. **Lines with no projection** are ranked on form, DvP and market value only, after every projected prop, with no edge, Kelly or logged pick.

**Model Kelly** is a quarter-Kelly stake on the model's win chance blended 50/50 with FanDuel's no-vig probability, halved (max 1.25u, "half-Kelly: model not yet calibrated") until the combined pick record has 50+ graded picks and its Brier score beats the market's.

The scores are ranking aids built from public projections, not win probabilities.

## Output

Each run writes:

- **`bp_props_<date>.csv`**: every prop, one row each (FanDuel line and price, BettingPros projection and line, gap, scores, form, DvP, Rotowire status, teammates out, Kelly, market fair price and source).
- **`bp_top10_<date>.md`**: the record, BETs and watch list, top 10 by gap, score and balanced score, lines with no projection, lines where FanDuel differs from BettingPros, and top 10 by gap and score per category.
- **`bp_dashboard.html`**: the NBA Prop Board (below).
- **`history/<date>/`**: each pull's snapshot, pagination log and Rotowire snapshot, used for the pre-tip cutoff and CLV.
- **`value_<date>.json`**: the market check per FanDuel line (both sides, source, decisions, closing check).

Key CSV columns:

| Column | Meaning |
| --- | --- |
| `market`, `rec_side`, `line`, `fd_odds_rec` | Prop, side, FanDuel's line and price |
| `bp_proj`, `bp_line`, `line_note` | BettingPros projection, its line, and a note when FanDuel's line differs |
| `pct_gap`, `score`, `balanced`, `signals`, `kelly_u` | Gap at FanDuel's line, ratings, agreement, model stake |
| `fd_fair_prob`, `fd_market_ev` | FanDuel's own fair probability and market EV (from `/offers`) |
| `oa_fair`, `oa_ev`, `fair_src` | Market fair probability, EV at FanDuel's price, and its source (Unabated / OddsAPI) |
| `noproj`, `placeholder` | No projection; placeholder projection |
| `rw_status`, `starter`, `tm_out_names`, `tm_dtd` | Rotowire status, projected starter, teammates out and day-to-day |
| `tip`, `pulled_at`, `stale_price` | Game start, pull time (UTC), and a price kept from 2 PM on a picks-only re-price |

## BETs vs. Model Picks

Only market BETs are bets: they carry a stake (quarter-Kelly on the market's fair probability). Everything the model lists produce (top 5 by balanced, score, gap and learned, and the shortlist watch list) is a **model pick: research, not a bet**, logged with no stake and tracked at a flat 1u only to measure it. Every model pick shows its market EV; picks the market prices below 0 are greyed out. The Record tab keeps the two apart: **BETs (market +EV)** and **Model picks (research)**, each with W-L, units and CLV, and 3PM overs tracked separately inside model picks.

## Record and Self-Learning

Every run logs the top 5 picks from four strategies (balanced, your score, projection gap, learned) to `bp_ledger.csv`, plus the market-check decisions (`bet_agree`, `bet_only`, `watch`). The first run of the next day grades them.

- **Per-game cutoff**: a strategy pick counts only if it came from the last run before that game's tip that priced the game. BET and watch decisions count when logged before tip and are never replaced by a later run.
- **Scoreboard**: W-L-P, hit rate, units and ROI, Kelly units, Brier score and log loss vs. FanDuel's no-vig probability, and CLV (line points for strategies; EV at the 6:30 fair price for BETs, with the count that fell below 0).
- **Market comparison**: after 50 graded picks with both, the record compares our probability with the market's, per fair-price source.
- **Grading** (`bp_boxscore.py`): final stats from public box scores, ESPN first and NBA.com as the fallback, so grading costs 0 BettingPros requests and covers lines BettingPros never carried. Checked against BettingPros on 11/20: 149 of 149 props matched. BettingPros is the fallback only for a game no box score covers (≈1.4 requests per game, plus up to 8 single game+market lookups). Box scores are saved to `history/<date>/boxscore_<date>.json`. A player the box score lists as not playing is graded **V** (void). `--max-requests` is a hard stop checked before every request.
- **Learning**: every graded prop goes into `bp_graded.csv`, with each player-game counted once. An L2-regularized logistic regression learns how much each factor predicts the side hitting (edge, EV, matchup, form, market value, price, side, injury, teammates out, market type, placeholder, projected starter). It starts at 60 player-games, is validated on later slates against Balanced, and is capped at 50% weight until it beats Balanced on 100+ held-out player-games (then 80%).
- **Teammates out**: a rotation regular (points props in recent slates averaging 9.5+, or props tonight with no PTS line under 9.5) counts as out when Rotowire lists him Out, even if his props are still posted, or when he has no props and Rotowire doesn't list him as playing.

## Dashboard

`bp_dashboard.html`, the **NBA Prop Board**, built from `dashboard_template.html`:

- a category picker with ranking by gap, score, balanced or learned
- **BET** tags (with the bet side and "+EV only" marker), **watch** tags, and an EV-below-0 flag after the closing check
- columns for our EV, market EV and fair source next to the scores and Kelly
- tap-to-expand details: score parts, form, Rotowire, teammates out, line differences, market check and closing CLV
- a **Record** tab with each strategy and BET group, results by category, recent picks, the model and the market comparison
- **Copy for Claude**: the record summary, BETs with tags, form, DvP and stakes, the watch list and every top-10 list as compact text

`/props` publishes it as a private claude.ai artifact and updates the same link each day (stored in git-ignored `.dashboard_url`).

## Using with Claude Code

The repo includes a `/props` skill for [Claude Code](https://claude.com/claude-code). Open Claude Code in the project folder and type:

```
/props                 # today's flow: grade, pull, market check, summary
/props reprice         # 6:30 PM re-price and closing check
/props 2026-11-19      # a specific date
/props Celtics         # filter to a player or team
```

`CLAUDE.md` makes Claude ask before any BettingPros request, Odds API credit or Unabated odds call, showing the estimated cost and what's left first.

## Notes

This project is for research and entertainment. Projections and market prices are not guarantees, so bet responsibly.

## Simulating a slate you didn't pull (`bp_sim.py`, research only)

`python3 bp_sim.py --date YYYY-MM-DD --max-requests N` pulls that date's FanDuel props from BettingPros (≈1.5 requests per game; ask first), scores them with the same model and grades the model picks from box scores. Re-running without `--max-requests` reuses the saved pull (0 requests). `--backfill` lists every finished regular-season date without a live pull, with request estimates (free, from ESPN's schedule); `--backfill --go --max-requests N` sims them newest first within N; `--record` prints the sim record. It writes only to `sim/` (`sim/sim_ledger.csv`): the real ledger, graded props, model, Kelly cap and dashboard are untouched.

Limits: the lines are the last ones BettingPros kept (near closing), not the 2 PM prices; BettingPros' form windows for a past date already include that game, so the sim removes it from every window and drops last-1, head-to-head and the streak; market BETs aren't simulated (no historical Unabated consensus).
