# Records index

Every record the system keeps: its rule, the version and start date of that rule, where its data lives, and where it
shows. **Keep this file current: when a rule changes, add a version to `RULE_VERSIONS` in `bp_learn.py`, record the
change here, and bump the version.** Older versions stay in the ledger as archives; nothing is ever deleted.

## Versions

| Version | First slate | What changed |
|---|---|---|
| v1 | (start) to 2026-11-23 | Archive. Original strategies: top-5 model lists (incl. the old "your score" formula), Unabated BETs, CrazyNinja +EV, CNO screener, halftime experiment, sims. |
| v2 | 2026-11-24 | Fresh start. "Your score" = your rule (CNO +EV + BettingPros agrees); Unabated +EV & BP agrees record; near misses to your rule; alt ladder split by DvP; R+A back on; tip-time rule; raw BettingPros projections (calibration logged, off). |

Every ledger row carries `rule_version` (stamped by `bp_learn._write`, from the slate date). The Record tab and Copy for
Claude show v2 by default; the Record tab has a v1-archive toggle and Copy for Claude ends with a v1 summary.

## v2 records

| Record | Rule (v2) | Ledger strategy | Data files | Where it shows |
|---|---|---|---|---|
| **Your rule: CNO +EV + BP agrees** | CrazyNinja screener paste with any EV > 0 (NBA, FanDuel, 4+ books, ≤ +200, main line, game open) AND BettingPros' projection/lean on the same side. Logged once per prop per day when it first qualifies; ¼K on CNO's fair prob, exposure caps. | `my_rule` | `bp_ledger.csv`; daily store `history/<date>/cno_rows_<date>.json`; settings `cno_screener_log.json` | Record tab (top); note §2 "Top 10 by your score"; Copy for Claude |
| **Near misses to your rule** (research) | From pastes: CNO EV in [-1%, 0] with BP agreeing ("EV short"), or CNO EV > 0 with BP disagreeing / no projection. Logged once with the reason; flat 1u, no stake. | `near_miss` | `bp_ledger.csv` (reason in `cap_note`); daily store | Record tab (under Your rule); note §4 |
| **Unabated +EV & BP agrees** | Every FanDuel line Unabated prices (median devigged books + exchanges at FanDuel's line, 3+ sources) with EV > 0, game open, BP agrees. Logged once per day; ¼K on the Unabated fair, BET caps. | `ua_agree` | `bp_ledger.csv`; latest EVs `history/<date>/unabated_ev_<date>.json` | Record tab; note §3 |
| **BETs: Unabated market +EV** | Unabated EV ≥ +2% at FanDuel's line, 3+ sources (or Odds API 4+ books), CNO worst case > 0 ("no CNO check" if CNO down). ¼K, 1.25u cap until 50 graded BETs with +CLV, 1.5u/player, 3.5u/game. Tagged "+EV + proj agrees" / "+EV only". Closing CLV = last pregame price (final at tip + 5). | `bet_agree`, `bet_only` | `bp_ledger.csv`; `value_<date>.json`; watch log `watch_<date>.log` | Record tab; BET chips on the board; note BET list |
| **CrazyNinja +EV** | CNO worst-case EV (median across sources, DevigMethod 4) ≥ +2%; ¼K on CNO's fair; same caps. | `cno` | `bp_ledger.csv`; `cno_cache.json` | Record tab |
| **CNO screener** | Screener paste rows ≥ +2% (fixed settings) → `cno_scr`; `--all` rows under +2% → `cno_scr_sub` (tracking). Split by BettingPros agree/disagree. | `cno_scr`, `cno_scr_sub` | `bp_ledger.csv`; `cno_screener_log.json` | Record tab |
| **Alt ladder** | FanDuel alt rungs (not the main line): 3+ sources pricing both sides at that rung, FD ≤ +300, market EV ≥ +4%, CNO worst case > 0, one rung per player, ¼K capped 0.5u. "Market only" (PTS/REB/AST/P+A/PRA/R+A) or "market + projection" (3PM/P+R, BP must agree). Ranked by DvP helps > neutral > hurts; record split by DvP. | `alt_mkt`, `alt_proj` (`dvp_tag`) | `bp_ledger.csv` | Record tab (Alt ladder + DvP rows) |
| **Halftime live** (experimental) | ESPN halftime snapshot of every player with a pregame line; alerts on strong pace flags (no side); expected final = 1st half + base-rate 2nd-half share × line (shrunk to 50% under n = 100). HALF BET from `--half-paste`: 3+ books, EV ≥ +3%, CNO > 0, skips; ¼K cap 0.5u/bet, 1.5u/game; split by expected-final agree. | `half` | `halftime_flags.csv`; `half_pastes.csv`; `history/<date>/halftime_<date>.json` | Record tab "Halftime live" (expected-final accuracy, HALF BETs) |
| **All +EV (any source)** (research log) | Every +EV prop from Unabated (EV > 0, 3+ sources), CrazyNinja pastes (EV > 0) and the alt ladder, logged once per day at its first sighting (later sources noted); ¼K on the entry fair, capped 1.25u. Note §5. | `all_ev` | `bp_ledger.csv` | Record tab; note §5 |
| **Outside picks** | Reddit / X / RotoWire cards pasted with `bp_value.py --picks-paste FILE --date D --source "..."`; one record per source; flat 1u (no price on the card: units at -110); graded from ESPN box scores. | `ext_pick` (source in `fair_src`) | `bp_ledger.csv` | Record tab "Outside picks" |
| **Reddit picks** (research, per poster) | Every NBA player-prop pick in r/sportsbook's daily NBA thread (`bp_value.py --reddit-paste FILE` with the pasted thread text, or `--reddit` with a Reddit app; before the first tip), one row per poster/prop with entry time and FanDuel price + market EV at that time (Unabated, else CrazyNinja paste store); image-only comments skipped and counted; posts after tip not logged. Flat 1u. Per-poster leaderboard (picks, W-L, units, ROI, CLV, entry EV). | `reddit_pick` (poster in `fair_src`) | `bp_ledger.csv` | Record tab leaderboard; note §7 |
| **Proven poster + market +EV** | A proven poster (30+ graded picks, positive average CLV and positive units, re-checked every run) whose pick also has Unabated market EV > 0 (3+ sources) at entry. ¼K on the Unabated fair, BET caps. The only staked Reddit record. | `proven_ev` | `bp_ledger.csv` | Record tab; note §7 "+EV" |
| **BettingPros Projection Model** (research) | Top 5 by balanced, projection gap, learned; watch list; 3PM-overs split. Flat 1u, no stake. (The old "your score" formula stopped logging in v2.) | `bal`, `gap`, `learned`, `watch` | `bp_ledger.csv`; `bp_graded.csv`; `bp_model.json` | Record tab (research) |
| **Sims** (research) | Slates without a live pull, simmed from near-closing lines with that game removed from form. | (sim strategies) | `sim/sim_ledger.csv`; `sim/<date>/` | Record tab "Sims" |

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
