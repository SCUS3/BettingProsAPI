"""Project-wide settings. ROOT is the project folder: every data file (CSVs, ledgers, history/, caches, .env) lives
there, wherever the code sits."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- rule versions (see RECORDS.md)
RULE_VERSIONS = [("v1", "0000-00-00"), ("v2", "2026-11-24"), ("v2.1", "2026-11-24")]   # v2.x rows count in the v2 views   # a rule change adds a line here and in RECORDS.md
RULE_NOTES = {                  # what each version's staked rules are (RECORDS.md has the full text)
    "v1": "Unabated BETs, CrazyNinja +EV, CNO screener, alt ladder, halftime; old 'your score' model lists",
    "v2": "Your rule (CNO +EV + BP agrees); Unabated +EV & BP agrees; Triple green (+EV + BP agrees + DvP helps, added "
          "before the first v2 slate); BETs with CNO > 0; alt ladder by DvP; Tonight's bets = BETs + alt BETs + your rule + "
          "Triple green, largest stake per prop, caps across the list",
    "v2.1": "Sam score (0-100, display and ranking only): Tonight's bets sorted by score, under 50 = paper only, the 8u "
            "nightly cap trims the lowest scores first; score frozen at logging for the record splits",
}

# ---------------------------------------------------------------- BettingPros (requests cost; ask first)
BP_DAILY_LIMIT = 100           # Free/Plus requests per UTC day (resets 7 PM ET in winter)
BP_MAX_PICK_LOOKUPS = 8        # per slate per grading run: single game+market lookups
PULL_LEAD_MIN = 90             # suggested first pull: earliest tip - 90 min
PULL_PER_GAME = 5.7            # estimate: full pull pages per game (+1 /events)
REPRICE_PER_GAME = 2.4         # estimate: re-price pages per game
SIM_PER_GAME = 1.5             # estimate: sim pull pages per game
SEASON_START = "2026-10-20"
REAL_PROJ_MARKETS = {"3PM", "P+R"}                     # BettingPros' real projections; other markets are side leans
SCORE_WEIGHTS = (0.40, 0.35, 0.25)                     # old score formula: edge, EV, matchup (v1; no longer logs picks)

# ---------------------------------------------------------------- BETs (Unabated market +EV)
BET_MIN_EV = 0.02              # market EV at FanDuel's price
UNABATED_MIN_SOURCES = 3       # sources pricing both sides at FanDuel's line
ODDSAPI_MIN_BOOKS = 4          # The Odds API fallback: other books at FanDuel's line
ODDSAPI_PLAN_MIN_BOOKS = 3
BET_KELLY_CAP = 1.25           # u, until BET_CAP_MIN_BETS graded BETs show positive average CLV
BET_KELLY_MAX = 2.5            # u, after
BET_CAP_MIN_BETS = 50
PLAYER_CAP = 1.5               # u per player per day (all BETs combined)
GAME_CAP = 3.5                 # u per game per day
NIGHT_CAP = 8.0                # u: Tonight's bets total; over it, every stake is scaled down proportionally
OUTLIER_EV = 0.10              # entry EV above this needs OUTLIER_MIN_SOURCES and a price no older than OUTLIER_MAX_AGE_MIN
OUTLIER_MIN_SOURCES = 5
OUTLIER_MAX_AGE_MIN = 10
# ---------------------------------------------------------------- Sam score (v2.1): display and ranking only
SAM_WEIGHTS = {"edge": 30, "quality": 10, "bp": 20, "dvp": 10, "move": 10, "role": 10, "form": 5, "consensus": 5,
               "threes": 0}       # points at full marks; each part is a 0-1 fraction x its weight ('threes' = 3PM market, off)
SAM_PENALTIES = {"long_odds": -10, "dtd": -10, "outlier": -20, "unverified": -15}
SAM_NEUTRAL = {"bp": 0.2, "dvp": 0.3, "form": 0.4, "role": 0.3}   # missing data = 4/20, 3/10, 2/5, 3/10
SAM_TIERS = [(80, "A"), (65, "B"), (50, "C")]                     # below 50 = paper only (never in Tonight's bets)
SAM_EDGE_FULL = 0.06           # entry EV for full edge marks
SAM_ADAPT_N0 = 500            # nightly adaptation: weight on the fit = n / (n + 500) graded player-games
SAM_FIT_MIN = 500              # graded v2 player-games before fitted weights may replace the hand-set ones
SAM_LONG_ODDS = 200
UNVERIFIED_BEFORE_TIP_MIN = 10
# ---------------------------------------------------------------- public site (GitHub Pages)
SITE_PUBLISH = True            # each run pushes site/ to the public site repo (confirmed by the owner 2026-11-26)
SITE_REPO = "https://github.com/scusebet/scusebet.github.io.git"   # public repo holding only the built site (GitHub Pages from main)
SITE_URL = "https://scusebet.github.io/"
SITE_PUBLISH_DELAY_MIN = 0     # bets appear on the public page this many minutes after they are logged
SITE_PLAYABLE_MARGIN = 0.02    # "playable to" = worst FanDuel price still +2% EV at the fair price
SITE_PUSH_EVERY_MIN = 10       # at most one public push per this many minutes  # both-sides conflict still unchecked this close to tip: keep the stronger side at half stake
CNO_MIN_EV = 0.02              # CrazyNinja +EV record: median worst-case EV
BOARD_MARKETS = ["PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA", "R+A"]
R_A_FROM = "2026-11-24"        # R+A on from this slate
WATCH_EVERY_S = 20 * 60        # watch: seconds between scans
CLOSE_LEAD_S = 5 * 60          # watch: scans 5 min before and after each tip
GRACE_MIN = 10                 # tip-time rule: bettable until scheduled start + 10 min unless ESPN shows it under way
CLOSE_AFTER_MIN = 5            # closing CLV finalized at tip + 5 min

# ---------------------------------------------------------------- model picks (research)
MODEL_KELLY_MAX = 2.5
CALIBRATION_MIN_PICKS = 50
PICKS_PER_STRATEGY = 5

# ---------------------------------------------------------------- The Odds API (credits; ask first)
ODDSAPI_MIN_REMAINING = 50     # stop before going under this many credits this month
ODDSAPI_DAILY_CREDITS = 8      # aim per day

# ---------------------------------------------------------------- Unabated / CrazyNinja
UNABATED_ODDS_INTERVAL = 5.0   # seconds between odds calls (free key)
UNABATED_ODDS_TIMEOUT = 90
CNO_MIN_INTERVAL = 1.0         # seconds between CrazyNinja calls

# ---------------------------------------------------------------- CrazyNinja screener paste / your rule
SCREENER_MIN_EV = 0.02         # screener record bar
SCREENER_FIXED = {"devig": "LW-M", "min_books": 4, "max_odds": 500.0, "book": "FanDuel", "league": "NBA"}
SCREENER_ALT_GAP = 2.0         # a line this far from the pull's FanDuel main line is an alternate (dropped)
NEAR_MISS_LOW_EV = -0.01       # "EV short" near misses: CNO EV in [-1%, 0] with BettingPros agreeing

# ---------------------------------------------------------------- alt ladder
ALT_MIN_EV = 0.04
ALT_MIN_SOURCES = 3
ALT_MAX_ODDS = 300
ALT_CAP = 0.5                  # u per rung

# ---------------------------------------------------------------- halftime live (experimental)
HALF_MIN_EV = 0.03
HALF_MIN_SOURCES = 3
HALF_BET_CAP = 0.5             # u per HALF BET
HALF_GAME_CAP = 1.5            # u per game
HALF_OVER_PACE = 0.25          # strong slow flag: first half <= 25% of the line ...
HALF_MIN_MINUTES = 12          # ... with 12+ first-half minutes
HALF_UNDER_PACE = 0.80         # strong fast flag: >= 80% of the line, still under
HALF_MIN_LINE = {"PTS": 14.5, "REB": 6.5, "AST": 4.5, "3PM": 2.5, "P+A": 20.5, "P+R": 20.5, "PRA": 20.5}
HALF_FOUL_FLAG = 3
HALF_MARGIN_FLAG = 20
HALF_SHRINK_N = 100            # expected-final share shrinks to 50% under this many rows

# ---------------------------------------------------------------- projection calibration (3PM, P+R)
CALIB_SHRINK_N = 200
USE_CALIBRATION = False        # off: display and tags use BettingPros' raw projection; calibrated value still logged

# ---------------------------------------------------------------- situation finder (candidate rules; never auto-adopted)
SITUATION_MIN_N = 100          # graded plays a split needs before it can be a candidate rule
SITUATION_P = 0.05             # and p below this that its average profit per play is really above 0
