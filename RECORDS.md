# Records index

Every record the system keeps: its rule, the version and start date of that rule, where its data lives, and where it
shows. **Keep this file current: when a rule changes, add a version to `RULE_VERSIONS` in `bp_learn.py`, record the
change here, and bump the version.** Older versions stay in the ledger as archives; nothing is ever deleted.

## Versions

| Version | First slate | What changed |
|---|---|---|
| v1 | (start) to 2026-11-23 | Archive. Original strategies: top-5 model lists (incl. the old "your score" formula), Unabated BETs, CrazyNinja +EV, CNO screener, halftime experiment, sims. |
| v2.1 | 2026-11-24 | **Sam score** (display and ranking only; never qualifies a -EV play or changes a stake): Tonight's bets sorted by score; under 50 = paper only (held out); the 8u nightly cap trims the lowest scores first (C before B before A) instead of scaling. v2.1 rows count in every v2 view (the dashboard's v2 toggle shows v2 and v2.1). |
| v2 | 2026-11-24 | Fresh start. "Your score" = your rule (CNO +EV + BettingPros agrees); Unabated +EV & BP agrees record; Triple green (added before the first v2 slate, so it is part of v2, not a new version); near misses to your rule; alt ladder split by DvP; R+A back on; tip-time rule; raw BettingPros projections (calibration logged, off). |

Every ledger row carries `rule_version` (stamped by `bp_learn._write`, from the slate date). The Record tab and Copy for
Claude show v2 by default; the Record tab has a v1-archive toggle and Copy for Claude ends with a v1 summary.

## v2 records

| Record | Rule (v2) | Ledger strategy | Data files | Where it shows |
|---|---|---|---|---|
| **Your rule: CNO +EV + BP agrees** | CrazyNinja screener paste with any EV > 0 (NBA, FanDuel, 4+ books, ≤ +200, main line, game open) AND BettingPros' projection/lean on the same side. Logged once per prop per day when it first qualifies; ¼K on CNO's fair prob, exposure caps. | `my_rule` | `bp_ledger.csv`; daily store `history/<date>/cno_rows_<date>.json`; settings `cno_screener_log.json` | Record tab (top); note §2 "Top 10 by your score"; Copy for Claude |
| **Near misses to your rule** (research) | From pastes: CNO EV in [-1%, 0] with BP agreeing ("EV short"), or CNO EV > 0 with BP disagreeing / no projection. Logged once with the reason; flat 1u, no stake. | `near_miss` | `bp_ledger.csv` (reason in `cap_note`); daily store | Record tab (under Your rule); note §5 |
| **Unabated +EV & BP agrees** | Every FanDuel line Unabated prices (median devigged books + exchanges at FanDuel's line, 3+ sources) with EV > 0, game open, BP agrees. Logged once per day; ¼K on the Unabated fair, BET caps. | `ua_agree` | `bp_ledger.csv`; latest EVs `history/<date>/unabated_ev_<date>.json` | Record tab; note §3 |
| **BETs: Unabated market +EV** | Unabated EV ≥ +2% at FanDuel's line, 3+ sources (or Odds API 4+ books), CNO worst case > 0 ("no CNO check" if CNO down). ¼K, 1.25u cap until 50 graded BETs with +CLV, 1.5u/player, 3.5u/game. Tagged "+EV + proj agrees" / "+EV only". Closing CLV = last pregame price (final at tip + 5). | `bet_agree`, `bet_only` | `bp_ledger.csv`; `value_<date>.json`; watch log `watch_<date>.log` | Record tab; BET chips on the board; note BET list |
| **CrazyNinja +EV** | CNO worst-case EV (median across sources, DevigMethod 4) ≥ +2%; ¼K on CNO's fair; same caps. | `cno` | `bp_ledger.csv`; `cno_cache.json` | Record tab |
| **CNO screener** | Screener paste rows ≥ +2% (fixed settings) → `cno_scr`; `--all` rows under +2% → `cno_scr_sub` (tracking). Split by BettingPros agree/disagree. | `cno_scr`, `cno_scr_sub` | `bp_ledger.csv`; `cno_screener_log.json` | Record tab |
| **Alt ladder** | FanDuel alt rungs (not the main line): 3+ sources pricing both sides at that rung, FD ≤ +300, market EV ≥ +4%, CNO worst case > 0, one rung per player, ¼K capped 0.5u. "Market only" (PTS/REB/AST/P+A/PRA/R+A) or "market + projection" (3PM/P+R, BP must agree). Ranked by DvP helps > neutral > hurts; record split by DvP. | `alt_mkt`, `alt_proj` (`dvp_tag`) | `bp_ledger.csv` | Record tab (Alt ladder + DvP rows) |
| **Halftime live** (experimental) | ESPN halftime snapshot of every player with a pregame line; alerts on strong pace flags (no side); expected final = 1st half + base-rate 2nd-half share × line (shrunk to 50% under n = 100). HALF BET from `--half-paste`: 3+ books, EV ≥ +3%, CNO > 0, skips; ¼K cap 0.5u/bet, 1.5u/game; split by expected-final agree. | `half` | `halftime_flags.csv`; `half_pastes.csv`; `history/<date>/halftime_<date>.json` | Record tab "Halftime live" (expected-final accuracy, HALF BETs) |
| **Triple green** | All three at the same sighting: +EV (CrazyNinja screener EV > 0, or Unabated EV > 0 with 3+ sources), BettingPros projection/lean on the side (🟢), DvP helps the side (🟢, side-aware: 1-10 overs, 21-30 unders). Missing DvP or projection = no. ¼K on the fair of the source that qualified it (higher EV if both), BET cap and caps. Logged once at its first qualifying sighting. Split by source (CNO / Unabated / both) and entry EV bucket; **Compare** rows: Triple green vs. Your rule vs. Your rule without DvP green. Part of Tonight's bets. | `triple` (source in `fair_src`) | `bp_ledger.csv` | Record tab (under Your rule); note §1 |
| **2 of 3** (research) | Any FanDuel side (Unabated EV store or CrazyNinja screener store, open games) where at least two of the Triple green checks hold: +EV (CNO > 0, or Unabated > 0 with 3+ sources), BettingPros agrees, DvP helps the side. Flat 1u, never in Tonight's bets (BP + DvP alone can be -EV). Logged once at first sighting; split by which held (all 3 / EV+BP / EV+DvP / BP+DvP). | `two_of_three` (checks in `fair_src`) | `bp_ledger.csv` | Record tab (under Triple green); note §2 |
| **All +EV (any source)** (research log) | Every +EV prop from Unabated (EV > 0, 3+ sources), CrazyNinja pastes (EV > 0) and the alt ladder, logged once per day at its first sighting (later sources noted); ¼K on the entry fair, capped 1.25u. Note §4. | `all_ev` | `bp_ledger.csv` | Record tab; note §4 |
| **Outside picks** | Reddit / X / RotoWire cards pasted with `bp_value.py --picks-paste FILE --date D --source "..."`; one record per source; flat 1u (no price on the card: units at -110); graded from ESPN box scores. | `ext_pick` (source in `fair_src`) | `bp_ledger.csv` | Record tab "Outside picks" |
| **Reddit picks** (research, per poster) | Every NBA player-prop pick in r/sportsbook's daily NBA thread (`bp_value.py --reddit-paste FILE` with the pasted thread text, or `--reddit` with a Reddit app; before the first tip), one row per poster/prop with entry time and FanDuel price + market EV at that time (Unabated, else CrazyNinja paste store); image-only comments skipped and counted; posts after tip not logged. Flat 1u. Per-poster leaderboard (picks, W-L, units, ROI, CLV, entry EV). | `reddit_pick` (poster in `fair_src`) | `bp_ledger.csv` | Record tab leaderboard; note §7 |
| **Proven poster + market +EV** | A proven poster (30+ graded picks, positive average CLV and positive units, re-checked every run) whose pick also has Unabated market EV > 0 (3+ sources) at entry. ¼K on the Unabated fair, BET caps. The only staked Reddit record. | `proven_ev` | `bp_ledger.csv` | Record tab; note §7 "+EV" |
| **BettingPros Projection Model** (research) | Top 5 by balanced, projection gap, learned; watch list; 3PM-overs split. Flat 1u, no stake. (The old "your score" formula stopped logging in v2.) | `bal`, `gap`, `learned`, `watch` | `bp_ledger.csv`; `bp_graded.csv`; `bp_model.json` | Record tab (research) |
| **Sims** (research) | Slates without a live pull, simmed from near-closing lines with that game removed from form. | (sim strategies) | `sim/sim_ledger.csv`; `sim/<date>/` | Record tab "Sims" |

## Tonight's bets (the betting list)

Not a separate record: the list you actually bet, built from the ledger (`nbaprops/rules/tonight.py`), top of the note
and of the dashboard's Tonight tab. The union of **BETs** (`bet_agree`, `bet_only`: Unabated EV ≥ +2% and CrazyNinja
worst case > 0; a BET logged while CNO was down stays in, flagged "no CNO check"), **alt BETs** (`alt_mkt`, `alt_proj`),
**Your rule** (`my_rule`) and **Triple green** (`triple`). Each prop (player, market, side, line) is listed once at its largest qualifying ¼K stake (not
summed); then 1.5u per player and 3.5u per game are applied across the whole list.
Every other record (Unabated +EV & BP agrees, CrazyNinja +EV, CNO screener, All +EV, Reddit, halftime, models) is a
paper record. Each underlying record keeps its own stakes and results.

**Safety checks on the list** (`nbaprops/rules/tonight.py`, `nbaprops/rules/conflicts.py`, thresholds in `config.py`):
- **Outlier EV:** entry EV above 10% needs 5+ sources AND a price no older than 10 min at decision time; otherwise
  "check: outlier EV" and held out. On the v1 archive the held-out outliers went 2-7 (−5.07u flat).
- **Both sides +EV** (same player/market/line): both held until re-priced from ONE Unabated snapshot (the board run's
  or a watch scan's board: same sources, same timestamp, FanDuel's current over and under). One side +EV → kept,
  "re-checked"; neither → both dropped; both still +EV or the re-price fails → both dropped, "data error". Never
  re-priced within 10 min of tip → the stronger side (higher EV, BP agrees, DvP helps) at half stake, "unverified".
  Each case and its cause (with both logged prices and the snapshot) is written to `history/<date>/conflicts_<date>.json`;
  the Record tab splits Tonight's bets into re-checked and unverified, plus held-out data errors and outliers (flat 1u,
  not bet) to show whether the checks hold up.
- **Contradictory rungs** (an over above an under for the same player/market, e.g. Champagnie REB u5.5 and o7.5):
  flagged in the Check column, not dropped.
- **Nightly cap:** after the player and game caps, the list totals at most 8u; above that every stake is scaled down
  proportionally and the header shows "scaled from".
- Cause of the 11/23 Jaylon Tyson PTS o9.5/u9.5 pair: the alt ladder logged o9.5 (FanDuel alt market, +142, 6 sources)
  at 00:35 UTC and a BET logged u9.5 (main line after it moved to 9.5, −118, 3 sources) at 00:55 UTC: two FanDuel
  markets, two scans, two source sets, and no check across records.

Each row shows the BettingPros projection (🟢/🔴 dot, number, gap) and the side-aware DvP dot and number, like the other
sections. **Tonight's bets record** (`key: tonight`, first on the Record tab and in Copy for Claude): every listed prop
once, graded with its ledger result, ¼K units at the **capped** stake, flat 1u alongside, CLV from the prop's row; only
plays logged before tip count. It is computed from the ledger each time (no extra rows).

## Sam score (v2.1)

`nbaprops/pricing/samscore.py`, weights in `config.SAM_WEIGHTS` (hand-set). 0-100 = parts (0-1 fraction x weight) +
penalties, clipped:

| Part | Max | How |
|---|---:|---|
| Market edge | 30 | entry EV / 6% |
| Market quality | 10 | 8+ sources 3 · exchange in the market 2 · CrazyNinja worst case > 0 3 · Unabated and CNO both +EV 2 |
| BettingPros | 20 | 3PM/P+R: agrees, |gap| / SD (1 SD = 20; SD = 1.1 sqrt(max(proj, line))) · lean markets: agrees 8 · disagrees 0 |
| DvP (side-aware) | 10 | helping 1-5 / 26-30 = 10 · helping 6-10 / 21-25 = 6 · 11-20 = 3 · hurts = 0 |
| Line movement | 10 | since first seen (either store): toward the side 10 · none 5 · against 0 |
| Role | 10 | projected starter 5 · rotation teammate ruled out (overs) 5 |
| Form | 5 | side hit rate ≥ 60% 5 · 50-59% 3 · < 50% 0 |
| Consensus | 5 | 2+ CrazyNinja pastes 2 · proven Reddit poster on the same side 3 |
| 3PM market | 0 | weight 0 (for the fit report) |
| Penalties | | FanDuel longer than +200 −10 · DTD −10 · outlier EV −20 · unverified both-sides −15 |

Missing data is neutral: no projection 4/20, no DvP 3/10, no form 2/5, no role info 3/10; 2+ missing = "partial data".
Tiers: A 80+, B 65-79, C 50-64, under 50 paper only. Every staked record (BETs, alt BETs, Your rule, Triple green,
Unabated +EV & BP agrees, CNO +EV, screener, proven poster, HALF BETs) freezes `score_entry`, `score_tier`, `score_parts`
and `score_flags` at logging (line movement = first sighting to the logging scan); the board shows a separate latest score.
Record tab "Sam score splits": every staked record by tier and each part by its points (each prop once).

**Weights over time** (`nbaprops/pricing/samfit.py`): L2 logistic with logit(FanDuel no-vig) as offset, player-games
weighted once, same L2 schedule as the learned model, Newton solve for 95% Wald intervals, held out = latest ~30% of
slates. The weekly review runs it once there are 500+ graded v2 player-games and switches to fitted weights
(`sam_weights.json`) only when they beat both the hand-set score and FanDuel no-vig on held-out log loss.
`python3 -m nbaprops.pricing.samfit` prints the one-off report on all graded data.

## Re-evaluation

Every CrazyNinja paste, Unabated board run and watch scan re-runs every store-based record from the day's CNO screener
store and Unabated EV store (`nbaprops/rules/reeval.py`): Your rule, CNO screener, near misses, Unabated +EV & BP agrees,
All +EV and Triple green. Anything newly qualifying is logged at that sighting (its price and EV become the frozen entry);
started games are skipped. The alt ladder needs the live Unabated board, so it is re-scanned on board runs and watch scans.

## Prices: entry, latest, close

Every ledger row freezes an **entry snapshot** the first time it is written (`entry_at`, `entry_fd_line`, `entry_price`,
`entry_fair`, `entry_ev`, `entry_sources`); nothing ever changes it. Watch scans write only `latest_*`, and only for
games still open (before scheduled start + 10 min and not under way on ESPN). When a game is final (start + 5 min or
ESPN shows it), the last pregame `latest_*` is copied once into `close_*` / `clv_ev`. The watch stops right after the
last close, and the daily stores (Unabated EVs, CrazyNinja pastes, ladder candidates) merge, never overwrite. EV buckets
(0-2%, 2-4%, 4%+) use the entry EV.

## Weekly review

`python3 bp_props.py --weekly --date END` writes `weekly_<END>.md`: every record for the 7 days ending END, split by
prop type, line type (main / alt), side, source and entry EV bucket.

## Situation finder

`nbaprops/rules/situations.py` splits every record's graded plays by market, side, EV at logging, FanDuel odds, side-aware DvP,
BettingPros gap, sources and time before tip (Record tab "Situations" boards, Copy for Claude, terminal). A split is a
**candidate rule** at 100+ graded plays with positive units, CLV above 0 and p < 0.05 that it beats breakeven
(`SITUATION_MIN_N`, `SITUATION_P` in `nbaprops/config.py`). Candidates are never switched on automatically: adopting one
is a new rule version here and in `RULE_VERSIONS`.

## v1 archive

All rows dated before 2026-11-24 (227 ledger rows, 20 sim rows at the cutover), including the retired "Top 5 your
score" strategy, the 11/22 legacy halftime reversal flags, and every v1 BET. Shown under the Record tab's v1 toggle.
