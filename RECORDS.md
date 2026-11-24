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
| **BettingPros Projection Model** (research) | Top 5 by balanced, projection gap, learned; watch list; 3PM-overs split. Flat 1u, no stake. (The old "your score" formula stopped logging in v2.) | `bal`, `gap`, `learned`, `watch` | `bp_ledger.csv`; `bp_graded.csv`; `bp_model.json` | Record tab (research) |
| **Sims** (research) | Slates without a live pull, simmed from near-closing lines with that game removed from form. | (sim strategies) | `sim/sim_ledger.csv`; `sim/<date>/` | Record tab "Sims" |

## v1 archive

All rows dated before 2026-11-24 (227 ledger rows, 20 sim rows at the cutover), including the retired "Top 5 your
score" strategy, the 11/22 legacy halftime reversal flags, and every v1 BET. Shown under the Record tab's v1 toggle.
