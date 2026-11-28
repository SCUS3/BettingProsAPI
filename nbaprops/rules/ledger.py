"""
Pick tracking, grading and self-learning for bp_props.py.

Each live run:
  1. save_snapshot()  keeps a copy of the run's props (history/<date>/<pulled_at>.csv)
  2. log_picks()      writes the run's top 5 picks per strategy to bp_ledger.csv, stamped with pulled_at
Next run:
  3. grade_date()     pulls the finished slate and grades every prop and pick
  4. fit_model()      re-learns from every graded prop (one sample per player-game), validated on later slates

Per-game cutoff: a pick counts only if it came from the last run before that game's tip. Props
used for learning come from the same pre-tip snapshot. Files (local, git-ignored): bp_ledger.csv,
bp_graded.csv, bp_model.json, history/. Standard library only.
"""

from nbaprops import config as C  # thresholds, caps and settings
import csv
import datetime as dt
import glob
import json
import math
import os

from nbaprops.config import ROOT as HERE  # noqa: E402
from nbaprops.grading import grade as _grade  # noqa: E402 -- grading moved to nbaprops.grading.grade
from nbaprops.pricing import devig as _odds  # noqa: E402 -- shared odds math
# re-exported for existing callers (bp_learn.grade_date, ...)
events_without_finals = _grade.events_without_finals
finals_from = _grade.finals_from
finals_from_box = _grade.finals_from_box
grade_date = _grade.grade_date
grade_placed = _grade.grade_placed
known_finals = _grade.known_finals
missing_pick_markets = _grade.missing_pick_markets
pending_dates = _grade.pending_dates
LEDGER = os.path.join(HERE, "bp_ledger.csv")
GRADED = os.path.join(HERE, "bp_graded.csv")
MODEL = os.path.join(HERE, "bp_model.json")
HISTORY = os.path.join(HERE, "history")

PICKS_PER_STRATEGY = C.PICKS_PER_STRATEGY  # nbaprops/config.py
MARKETS = ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA", "R+A")
STRATEGIES = [("bal", "Top 5 balanced"), ("score", "Top 5 your score"), ("gap", "Top 5 projection gap"),
              ("learned", "Top 5 learned")]
# Value-check decisions on the 2 PM shortlist (bp_value.py): logged once, counted if logged before tip,
# never replaced by a later run. CLV = FanDuel's line in the last pull before tip vs. the line bet.
VALUE_STRATEGIES = [("bet_agree", "BET: +EV + proj agrees"), ("bet_only", "BET: +EV only"),
                    ("watch", "Watch list (shortlist, not bet)"), ("cno", "CrazyNinja +EV"), ("cno_scr", "CNO screener"), ("cno_scr_sub", "CNO screener (under +2%, tracking only)"),
                    ("my_rule", "Your rule: CNO +EV + BP agrees"), ("ua_agree", "Unabated +EV & BP agrees"), ("ext_pick", "Outside picks (cards)"), ("all_ev", "All +EV (any source, deduped)"), ("near_miss", "Near misses to your rule (not taken, flat 1u)"),
                    ("half", "HALF BET (halftime live)"), ("reddit_pick", "Reddit picks (research, per poster)"),
                    ("proven_ev", "Proven poster + market +EV"), ("alt_mkt", "Alt, market only"),
                    ("alt_proj", "Alt, market + projection"), ("triple", "Triple green (retired 11/28: now a DvP flag on Your rule)"),
                    ("two_of_three", "2 of 3: +EV plus Proj Model or DvP (research, flat 1u)"),
                    ("steam", "Steam: +EV and the fair price moving your way (research, flat 1u)")]
BET_KEYS = {"bet_agree", "bet_only"}
SCREENER_KEYS = {"cno_scr", "cno_scr_sub"}
RULE_KEYS = {"my_rule", "near_miss"}
UA_KEYS = {"ua_agree"}
EXT_KEYS = {"ext_pick"}
ALL_EV_KEYS = {"all_ev"}
TRIPLE_KEYS = {"triple"}
STEAM_KEYS = {"steam"}         # v2.2 research: +EV props whose fair price moved toward the side since first seen
TWO3_KEYS = {"two_of_three"}  # 2 of 3 (+EV, BP agrees, DvP helps): research, flat 1u      # Triple green: +EV (CNO or Unabated) + BettingPros agrees + DvP helps the side; staked
REDDIT_KEYS = {"reddit_pick", "proven_ev"}
SAM_KEYS = {"bet_agree", "bet_only", "alt_mkt", "alt_proj", "my_rule", "triple", "ua_agree", "cno", "cno_scr", "cno_scr_sub",
            "proven_ev", "half"}       # staked records: Sam score frozen at logging   # Reddit picks (research, per poster) and the staked proven + market +EV record      # every +EV prop from any source, once per day (deduped), ¼K on its entry fair (research log)
EV10_MIN = C.EV10_MIN  # "10%+ EV" record: any +EV sighting at or above this EV (rare; usually right as lines post)
EV_BUCKETS = [(0.0, 0.02, "0-2%"), (0.02, 0.04, "2-4%"), (0.04, 99, "4%+")]
EV_RECORDS = [("Your rule", {"my_rule"}), ("Unabated +EV & BP agrees", {"ua_agree"}), ("BETs (Unabated)", {"bet_agree", "bet_only"}),
              ("CrazyNinja +EV", {"cno"}), ("CNO screener", {"cno_scr", "cno_scr_sub"}), ("Alt ladder", {"alt_mkt", "alt_proj"}),
              ("All +EV (any source)", {"all_ev"}), ("Triple green", {"triple"}), ("2 of 3 (+EV pairs; research)", {"two_of_three"}),
              ("Proven poster + market +EV", {"proven_ev"})]       # outside picks (Reddit / X / RotoWire cards), per source; flat 1u, graded from box scores        # v2: Unabated +EV (any, 3+ sources) where BettingPros agrees; ¼K on the Unabated fair       # your score from 11/23: CNO screener +EV (any) and BettingPros agrees; own record
LOG_SKIP = {"score"}          # the old score formula no longer logs picks (its history stays in the record)   # CrazyNinja's own +EV screener, pasted (bp_value.py --cno-paste); own record
CNO_KEYS = {"cno"}
HALF_KEYS = {"half"}
ALT_KEYS = {"alt_mkt", "alt_proj"}  # alt-ladder rungs (bp_alts.py): own record, ¼K capped 0.5u      # halftime live experiment (bp_value.py --half-paste); own record, never mixed with pregame        # CrazyNinja +EV: median worst-case EV >= +2%, ¼K on CNO's fair probability (own record)
PLACED = os.path.join(HERE, "bp_placed.csv")      # what the user actually bet (bp_value.py --placed)
PLACED_FIELDS = ["date", "placed_at", "player", "market", "side", "line", "odds", "stake_u", "match", "rule_stake",
                 "rule_odds", "event_id", "tip", "result", "actual", "profit_u", "close_fair", "clv_ev", "raw"]
SIM_LEDGER = os.path.join(HERE, "sim", "sim_ledger.csv")
VALUE_KEYS = {k for k, _ in VALUE_STRATEGIES}
MIN_TRAIN = 60            # player-games needed before the model is fit at all
TRUST_N = 400             # model weight = n / (n + TRUST_N), then capped:
CAP_UNPROVEN = 0.5        #   until the model beats Balanced on held-out later slates
CAP_PROVEN = 0.8          #   after it does
MIN_TEST = 100            # held-out player-games needed for that comparison
L2_BASE = 0.05            # L2 penalty on standardized weights (plus 5/n on small samples)
TM_MIN_PTS_LINE = 9.5     # stand-in for "averages 20+ minutes" (no minutes data in the API)

LEDGER_FIELDS = ["date", "pulled_at", "tip", "strategy", "rank", "player", "team", "market", "side", "line",
                 "odds", "gap", "placeholder", "score", "balanced", "learned", "our_prob", "nv_prob", "kelly_u",
                 "injury", "tm_out", "event_id", "result", "actual", "profit_u", "profit_kelly", "oa_fair", "oa_ev", "fair_src",
                 "sources", "close_fair", "clv_ev", "close_mkt_ev", "close_fd_line", "close_at", "cno_ev", "cno_min_ev", "cap_note", "cno_fair", "cno_flag", "proj_tag", "proj_note", "scr_settings",
                 "bp_proj_raw", "bp_proj_cal", "dvp_tag", "dvp", "rule_version",
                 # entry snapshot: set once when the row is first written, never changed afterwards
                 "entry_at", "entry_fd_line", "entry_price", "entry_fair", "entry_ev", "entry_sources",
                 # latest pregame observation (watch scans, open games only); copied to close_* when the game is final
                 "latest_at", "latest_fd_line", "latest_price", "latest_fair", "latest_ev", "latest_mkt_ev", "score_entry", "score_tier", "score_parts", "score_flags", "score_fracs", "src_names", "exch_led"]
ENTRY_FROM = {"entry_at": "pulled_at", "entry_fd_line": "line", "entry_price": "odds", "entry_fair": "oa_fair",
              "entry_ev": "oa_ev", "entry_sources": "sources"}
OA_COMPARE_MIN = 50       # graded picks with both our probability and The Odds API fair probability
FEATURES = ["edge", "ev", "matchup", "form", "market", "implied", "under", "self_injured",
            "teammates_out", "teammates_dtd", "form_x_teammates_out", "threes", "combo", "placeholder", "starter"]
FEATURE_LABELS = {
    "edge": "Projection edge", "ev": "EV at FanDuel price", "matchup": "Matchup (DvP)",
    "form": "Recent form vs. line", "market": "Market value", "implied": "Price (implied prob.)",
    "under": "Under side", "self_injured": "Player tagged DTD", "teammates_out": "Teammates ruled out",
    "teammates_dtd": "Teammates DTD", "form_x_teammates_out": "Form when teammates are out",
    "threes": "Threes market", "combo": "Combo market (P+A, P+R, PRA, R+A)",
    "placeholder": "Placeholder projection", "starter": "Projected starter (Rotowire)",
}


# ---------------------------------------------------------------- helpers
def _num(v):
    return _odds.num(v)


def _read(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


# Rule versions by first slate date (see RECORDS.md). A rule change adds a line here and bumps the version; older
# versions stay in the ledger as archives and are never deleted.
RULE_VERSIONS = C.RULE_VERSIONS  # nbaprops/config.py
V2_START = dict(RULE_VERSIONS)["v2"]
SAM_START = dict(RULE_VERSIONS)["v2.1"]


def in_version(row_or_version, version: str) -> bool:
    """A row (or version string) belongs to `version` or one of its minor versions (v2 includes v2.1)."""
    v = row_or_version if isinstance(row_or_version, str) else rule_version(row_or_version)
    return v == version or v.startswith(version + ".")
CURRENT_VERSION = RULE_VERSIONS[-1][0]


def rule_version(row: dict) -> str:
    """Rule version of a ledger-style row: its stamped rule_version, else the latest version started by its slate date."""
    if row.get("rule_version"):
        return row["rule_version"]
    d = str(row.get("date", ""))
    return [v for v, start in RULE_VERSIONS if d >= start][-1]


def _write(path, rows, fields):
    if os.environ.get("PYTEST_CURRENT_TEST") and os.path.abspath(path).startswith(os.path.abspath(HERE) + os.sep) \
            and "/tests/" not in os.path.abspath(path):
        raise RuntimeError(f"test tried to write real data: {path}")   # fixtures never reach the real ledger
    if "rule_version" in fields:                       # every ledger/record row carries its rule version
        for r in rows:
            if r.get("date") and not r.get("rule_version"):
                r["rule_version"] = rule_version(r)
            if r.get("date") and not r.get("entry_at"):     # freeze the entry snapshot the first time a row is written
                for k, src in ENTRY_FROM.items():
                    r[k] = r.get(src) if src != "oa_fair" else (r.get("oa_fair") or r.get("our_prob"))
                if r.get("strategy") in SAM_KEYS and r["date"] >= SAM_START and r.get("score_entry") in (None, ""):
                    from nbaprops.pricing import samscore as _ss
                    _ss.freeze(r)                         # Sam score frozen at logging (score_entry + parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _truthy(v):
    return v in (True, "True", "true", 1, "1")


def implied(odds):
    """Implied probability of American odds (None-safe)."""
    o = _num(odds)
    return None if o is None else _odds.implied_prob(o)


def decimal(odds):
    """Decimal odds from American odds (None-safe)."""
    o = _num(odds)
    return None if o is None else _odds.american_to_decimal(o)


def no_vig(r):
    """FanDuel's vig-free probability for the recommended side (both sides needed)."""
    o, u = implied(r.get("over_odds")), implied(r.get("under_odds"))
    if o is None or u is None:
        return None
    return (o if (r.get("rec_side") or "").lower() == "over" else u) / (o + u)


def is_placeholder(proj, line):
    """A whole-number projection exactly 0.5 off the line is usually a rounded stand-in, not a real projection."""
    proj, line = _num(proj), _num(line)
    return proj is not None and line is not None and float(proj).is_integer() and abs(proj - line) == 0.5






def prop_key(event_id, market, player):
    """Key for one prop: event|market|player."""
    return f"{event_id}|{market}|{player}"


def utc_now():
    """Current UTC time, ISO with Z."""
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def tip_iso(scheduled):
    """BettingPros 'scheduled' ('2026-11-19 00:00:00', UTC) -> '2026-11-19T00:00:00Z'."""
    return scheduled.replace(" ", "T") + "Z" if scheduled else None


# ------------------------------------------------------------ snapshots
def save_snapshot(rows, date, fields):
    """Save a pull's rows to history/<date>/."""
    pulled = rows[0].get("pulled_at") if rows else None
    if not pulled:
        return None
    path = os.path.join(HISTORY, date, pulled.replace("-", "").replace(":", "") + ".csv")
    _write(path, rows, fields)
    return path


def snapshots(date):
    """[(pulled_at, rows)] for every saved run of `date`, oldest first."""
    out = []
    for path in sorted(glob.glob(os.path.join(HISTORY, date, "*.csv"))):
        rows = _read(path)
        if rows and rows[0].get("pulled_at"):
            out.append((rows[0]["pulled_at"], rows))
    if not out:                                   # fall back to the main CSV if it is stamped
        rows = _read(os.path.join(HERE, f"bp_props_{date}.csv"))
        if rows and rows[0].get("pulled_at"):
            out.append((rows[0]["pulled_at"], rows))
    return out


def pre_tip_rows(date):
    """For each game, the props from the last saved run before its tip."""
    snaps = snapshots(date)
    best = {}
    for pulled, rows in snaps:
        for r in rows:
            tip = r.get("tip")
            if tip and pulled < tip:
                best[r.get("event_id")] = pulled           # later runs overwrite earlier ones
    out = []
    for pulled, rows in snaps:
        out += [r for r in rows if best.get(r.get("event_id")) == pulled]
    return out


# ---------------------------------------------------------- teammates
def _regulars(date, lookback=5):
    """{team: {player}} for rotation players in recent saved slates before `date`.
    'Rotation' = averages a points line of TM_MIN_PTS_LINE+ (stand-in for 20+ minutes)."""
    paths = sorted(p for p in glob.glob(os.path.join(HERE, "bp_props_*.csv")) if os.path.basename(p)[9:-4] < date)[-lookback:]
    seen, lines = {}, {}
    for path in paths:
        for r in _read(path):
            if r.get("market") == "PTS" and r.get("team") and _num(r.get("line")) is not None:
                k = (r["team"], r["player"])
                seen.setdefault(k, set()).add(path)
                lines.setdefault(k, []).append(_num(r["line"]))
    need = 2 if len(paths) >= 2 else 1
    out = {}
    for k, slates in seen.items():
        if len(slates) >= need and sum(lines[k]) / len(lines[k]) >= TM_MIN_PTS_LINE:
            out.setdefault(k[0], set()).add(k[1])
    return out


BP_TO_RW = {"NOR": "NOP", "PHO": "PHX", "UTH": "UTA", "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP"}


def save_rotowire(players, date, pulled):
    """Save the run's RotoWire snapshot."""
    if not players or not pulled:
        return
    os.makedirs(os.path.join(HISTORY, date), exist_ok=True)
    with open(os.path.join(HISTORY, date, "rotowire_" + pulled.replace("-", "").replace(":", "") + ".json"), "w") as f:
        json.dump(players, f)


def save_pull_log(date, pulled, data, prefix="pull_"):
    """Pagination and per-game counts for one pull (audit trail for missing props)."""
    os.makedirs(os.path.join(HISTORY, date), exist_ok=True)
    with open(os.path.join(HISTORY, date, prefix + pulled.replace("-", "").replace(":", "") + ".json"), "w") as f:
        json.dump(data, f, indent=1)


def update_pull_log(date, pulled, extra):
    """Add fields to a saved pull log."""
    path = os.path.join(HISTORY, date, "pull_" + pulled.replace("-", "").replace(":", "") + ".json")
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    d.update(extra)
    with open(path, "w") as f:
        json.dump(d, f, indent=1)


def run_coverage(date):
    """{pulled_at: set of event ids the run priced} from history logs. Runs without a log covered every game."""
    out = {}
    for path in glob.glob(os.path.join(HISTORY, date, "pull_*.json")) + glob.glob(os.path.join(HISTORY, date, "reprice_*.json")):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("pulled_at") and d.get("events"):
            out[d["pulled_at"]] = {str(e.get("id")) for e in d["events"]
                                   if e.get("id") and not e.get("started") and not e.get("skipped")}
    return out


def last_pull_log(date, kind="full"):
    """The latest saved pull log of `kind` for `date` (game list and tip times), or None."""
    for path in sorted(glob.glob(os.path.join(HISTORY, date, "pull_*.json")), reverse=True):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("kind", "full") == kind and d.get("events"):
            return d
    return None


def load_rotowire(date, before=None):
    """Latest saved Rotowire snapshot for `date` (taken at or before `before`, if given)."""
    paths = sorted(glob.glob(os.path.join(HISTORY, date, "rotowire_*.json")))
    if before:
        stamp = "rotowire_" + before.replace("-", "").replace(":", "") + ".json"
        paths = [p for p in paths if os.path.basename(p) <= stamp]
    if not paths:
        return None
    with open(paths[-1]) as f:
        return json.load(f)


def add_teammate_context(rows, date, rw_players=None):
    """Per prop: the player's own Rotowire status (rw_status, rw_play_pct, starter, lineup) and teammate context
    (tm_out, tm_out_names, tm_dtd). A teammate counts as out only if he is a rotation regular (points props in
    recent slates, average line 9.5+, or props tonight with no PTS line under 9.5) and Rotowire lists him Out, even
    when his props are still posted, or he has no props tonight while Rotowire doesn't list him as playing.
    Long-term injuries drop out of the regulars on their own, so this flags new absences."""
    from nbaprops.sources import rotowire as rw
    idx = {}
    for p in rw_players or []:
        idx[(p["team"], rw.norm(p["name"]))] = p
    have_rw = bool(idx)
    teams_rw = {p["team"] for p in rw_players or []}

    for r in rows:                                      # the player's own Rotowire line
        p = idx.get((BP_TO_RW.get(r.get("team"), r.get("team")), rw.norm(r.get("player"))))
        r["rw_status"] = p["status"] if p else ""
        r["rw_play_pct"] = p["play_pct"] if p else (100 if have_rw else "")
        r["starter"] = p["starter"] if p else (False if have_rw else "")
        r["lineup"] = p["lineup"] if p else ""
        if p and rw.is_out(p):
            r["injury"] = "OUT"
        elif p and p["status"] in ("Ques", "GTD", "Doubt") and not r.get("injury"):
            r["injury"] = p["status"]

    tonight, status, pts_line = {}, {}, {}             # after Rotowire, so an Out there counts here
    for r in rows:
        if r.get("team") and r.get("player"):
            tonight.setdefault(r["team"], set()).add(r["player"])
            status[(r["team"], r["player"])] = (r.get("injury") or "").strip().lower()
            if r.get("market") == "PTS" and _num(r.get("line")) is not None:
                pts_line[(r["team"], r["player"])] = _num(r["line"])
    regs = {t: set(v) for t, v in _regulars(date).items()}
    for team, players in tonight.items():               # tonight's rotation: props posted, no PTS line under 9.5
        for name in players:
            if pts_line.get((team, name), TM_MIN_PTS_LINE) >= TM_MIN_PTS_LINE:
                regs.setdefault(team, set()).add(name)
    out_by_team, dtd_by_team = {}, {}
    for team, players in regs.items():
        if team not in tonight:
            continue
        rw_team = BP_TO_RW.get(team, team)
        for name in players:
            p = idx.get((rw_team, rw.norm(name)))
            if name in tonight[team]:                   # props posted: out only if tagged or listed Out
                if status.get((team, name)) in ("out", "o", "suspended") or (p and rw.is_out(p)):
                    out_by_team.setdefault(team, set()).add(name)
                elif p and rw.is_doubtful(p):
                    dtd_by_team.setdefault(team, set()).add(name)
                continue
            if have_rw and rw_team in teams_rw:
                if p is None or rw.is_out(p):          # not listed as playing, or listed Out
                    out_by_team.setdefault(team, set()).add(name)
                elif rw.is_doubtful(p):
                    dtd_by_team.setdefault(team, set()).add(name)
            else:                                      # no Rotowire data: missing props = out
                out_by_team.setdefault(team, set()).add(name)
    for r in rows:
        team, me = r.get("team"), r.get("player")
        names = out_by_team.get(team, set()) - {me}
        r["tm_out"] = len(names)
        r["tm_out_names"] = ", ".join(sorted(names))
        dtd = dtd_by_team.get(team, set()) - {me}
        dtd |= {p for (t, p), st in status.items() if t == team and p != me and st in ("dtd", "gtd", "questionable", "doubtful", "q", "d", "ques")}
        r["tm_dtd"] = len(dtd)
    return rows


# ------------------------------------------------------------ features
def features(r):
    """Feature vector; edge is None for placeholder projections (left out of the edge weight)."""
    s = lambda k: (_num(r.get(k)) if _num(r.get(k)) is not None else 50.0) / 100
    tm_out = min(_num(r.get("tm_out")) or 0, 2) / 2
    tm_dtd = min(_num(r.get("tm_dtd")) or 0, 3) / 3
    inj = (r.get("injury") or "").strip().lower()
    mk = r.get("market") or ""
    imp = implied(r.get("fd_odds_rec"))
    form = s("s_form")
    ph = _truthy(r.get("placeholder"))
    real = (r.get("market") or "") in ("3PM", "P+R")      # only these carry real BettingPros projections
    return [None if ph or not real else s("s_edge"), s("s_ev"), s("s_dvp"), form, s("s_market"), imp if imp is not None else 0.5,
            1.0 if (r.get("rec_side") or "").lower() == "under" else 0.0,
            1.0 if inj and inj != "probable" else 0.0,
            tm_out, tm_dtd, form * tm_out,
            1.0 if mk == "3PM" else 0.0, 1.0 if mk in ("P+A", "P+R", "PRA", "R+A") else 0.0,
            1.0 if ph else 0.0,
            None if r.get("starter") in (None, "") else (1.0 if _truthy(r.get("starter")) else 0.0)]


def _sig(z):
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def _player_game_weights(rows):
    """Weight 1/k for each of a player's k props in one game, so a player-game counts once."""
    count = {}
    for r in rows:
        k = (r.get("date"), r.get("event_id"), r.get("player"))
        count[k] = count.get(k, 0) + 1
    return [1.0 / count[(r.get("date"), r.get("event_id"), r.get("player"))] for r in rows]


def _fit(X, y, wt):
    """Weighted L2 logistic regression on standardized features. None in a column = left out (z = 0)."""
    n_eff = sum(wt)
    k = len(X[0])
    mu, sd = [], []
    for j in range(k):
        col = [(x[j], w) for x, w in zip(X, wt) if x[j] is not None]
        sw = sum(w for _, w in col) or 1.0
        m = sum(v * w for v, w in col) / sw
        mu.append(m)
        sd.append(max(math.sqrt(sum(w * (v - m) ** 2 for v, w in col) / sw), 1e-6))
    Z = [[0.0 if x[j] is None else (x[j] - mu[j]) / sd[j] for j in range(k)] for x in X]
    w, b = [0.0] * k, 0.0
    lam = L2_BASE + 5.0 / max(n_eff, 1)
    for _ in range(500):
        gw, gb = [0.0] * k, 0.0
        for z, t, wi in zip(Z, y, wt):
            e = wi * (_sig(b + sum(a * c for a, c in zip(w, z))) - t)
            gb += e
            for j in range(k):
                gw[j] += e * z[j]
        b -= 0.5 * gb / n_eff
        w = [wj - 0.5 * (g / n_eff + lam * wj) for wj, g in zip(w, gw)]
    return {"weights": w, "bias": b, "mu": mu, "sd": sd}


def _predict(m, x):
    z = [0.0 if v is None else (v - mu) / sd for v, mu, sd in zip(x, m["mu"], m["sd"])]
    return _sig(m["bias"] + sum(a * c for a, c in zip(m["weights"], z)))


def _logloss(ps, ys, ws):
    tot = sum(ws) or 1.0
    return sum(w * -(y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6))) for p, y, w in zip(ps, ys, ws)) / tot


# ------------------------------------------------------------- logging
def log_picks(rows, date):
    """Add this run's top picks per strategy to the ledger (a re-score of the same run replaces its rows)."""
    pulled = rows[0].get("pulled_at") if rows else ""
    ledger = _read(LEDGER)
    old = {(r["date"], r.get("pulled_at"), r["strategy"], r["player"], r["market"], r["side"]): r
           for r in ledger if r["date"] == date}
    keep = [r for r in ledger if not (r["date"] == date and r.get("pulled_at", "") == (pulled or "")
                                      and r.get("strategy") not in VALUE_KEYS)]
    pool = [r for r in rows if _truthy(r.get("price_is_fanduel")) and r.get("score") is not None
            and (r.get("injury") or "").strip().lower() not in ("out", "doubtful")]
    sorters = {
        "gap": lambda r: abs(_num(r.get("pct_gap")) or 0),
        "score": lambda r: _num(r.get("score")) or 0,
        "bal": lambda r: _num(r.get("balanced")) or 0,
        "learned": lambda r: _num(r.get("learned")) or 0,
    }
    demoted = lambda r: _truthy(r.get("ph_demoted"))
    new = []
    for strat, _ in [x for x in STRATEGIES if x[0] not in LOG_SKIP]:
        ordered = sorted(pool, key=lambda r: (demoted(r), -sorters[strat](r)))
        for i, r in enumerate(ordered[:PICKS_PER_STRATEGY], 1):
            our = _num(r.get("learned_prob")) if strat == "learned" and r.get("learned_prob") not in (None, "") else _num(r.get("model_prob"))
            nv = no_vig(r)
            row = {"date": date, "pulled_at": pulled, "tip": r.get("tip"), "strategy": strat, "rank": i,
                   "player": r.get("player"), "team": r.get("team"), "market": r.get("market"), "side": r.get("rec_side"),
                   "line": r.get("line"), "odds": r.get("fd_odds_rec"), "gap": r.get("pct_gap"),
                   "placeholder": r.get("placeholder"), "score": r.get("score"), "balanced": r.get("balanced"),
                   "learned": r.get("learned"), "our_prob": None if our is None else round(our, 4),
                   "nv_prob": None if nv is None else round(nv, 4), "kelly_u": None,   # model pick: research, no stake
                   "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id"),
                   "oa_fair": r.get("oa_fair"), "oa_ev": r.get("oa_ev"), "fair_src": r.get("fair_src")}
            prev = old.get((date, pulled, strat, row["player"], row["market"], row["side"]))
            if prev and prev.get("result"):
                row.update({k: prev[k] for k in ("result", "actual", "profit_u", "profit_kelly")})
            if prev and prev.get("oa_fair") and row.get("oa_fair") in (None, ""):
                row.update({k: prev.get(k) for k in ("oa_fair", "oa_ev", "fair_src")})
            new.append(row)
    _write(LEDGER, keep + new, LEDGER_FIELDS)
    return len(new)


def counted_flags(ledger):
    """counted[i] = True when ledger row i came from the last run before its game's tip.
    clv[i] = closing-line value in points for counted rows seen in an earlier pre-tip run."""
    runs, cover = {}, {}
    for r in ledger:                  # runs = real pulls; watch/board BET rows carry their own scan time
        if r.get("pulled_at") and r.get("strategy") not in VALUE_KEYS:
            runs.setdefault(r["date"], set()).add(r["pulled_at"])
    for d in runs:
        cover[d] = run_coverage(d)
    covers = lambda d, t, ev: t not in cover[d] or str(ev) in cover[d][t]   # a picks-only re-price skips some games
    from nbaprops.rules import tipoff as bp_tipoff
    cut = lambda r: bp_tipoff.cutoff(r.get("tip"), r["date"], r.get("event_id"))   # actual start, else tip + 10 min
    first_seen = {}
    for r in ledger:
        if r.get("pulled_at") and r.get("tip") and r["pulled_at"] < cut(r):
            k = (r["date"], r["strategy"], r["event_id"], r["player"], r["market"], r["side"])
            if k not in first_seen or r["pulled_at"] < first_seen[k][0]:
                first_seen[k] = (r["pulled_at"], _num(r.get("line")))
    counted, clv = [], []
    closing = {}
    for r in ledger:
        p, tip = r.get("pulled_at"), r.get("tip")
        if r.get("strategy") in HALF_KEYS | EXT_KEYS:
            pass
        elif not p or not tip or p >= cut(r):
            counted.append(False)
            clv.append(None)
            continue
        if r.get("strategy") in HALF_KEYS | EXT_KEYS:      # halftime (live by design) and outside cards: always count
            counted.append(True)
            clv.append(round(100 * _num(r["clv_ev"]), 2) if _num(r.get("clv_ev")) is not None else None)
            continue
        if r.get("strategy") in VALUE_KEYS:                # decided once at 2 PM: counts if logged before tip
            counted.append(True)
            if r["date"] not in closing:
                closing[r["date"]] = closing_lines(r["date"])
            if _num(r.get("clv_ev")) is not None:          # EV of the bet at the 6:30 Unabated fair price, in %
                clv.append(round(100 * _num(r["clv_ev"]), 2))
                continue
            cl, bl = closing[r["date"]].get((str(r.get("event_id")).split(".")[0], r["player"], r["market"])), _num(r.get("line"))
            clv.append(None if cl is None or bl is None or cl[0] <= p else
                       ((cl[1] - bl) if r.get("side") == "over" else (bl - cl[1])))
            continue
        last = max((t for t in runs[r["date"]] if t < cut(r) and covers(r["date"], t, r.get("event_id"))), default=None)
        ok = p == last
        counted.append(ok)
        v = None
        if ok:
            fp, fl = first_seen.get((r["date"], r["strategy"], r["event_id"], r["player"], r["market"], r["side"]), (p, None))
            cl = _num(r.get("line"))
            if fp < p and fl is not None and cl is not None:
                v = (cl - fl) if r.get("side") == "over" else (fl - cl)   # + = you beat the closing number
        clv.append(v)
    return counted, clv


# ------------------------------------------------------------- grading
def closing_lines(date):
    """{(event_id, player, market): (pulled_at, FanDuel line)} from the last saved pull before each game's tip."""
    out = {}
    for pulled, rows in snapshots(date):
        for r in rows:
            if r.get("tip") and pulled < r["tip"] and _truthy(r.get("price_is_fanduel")) and _num(r.get("line")) is not None \
                    and not _truthy(r.get("stale_price")):
                out[(str(r.get("event_id")).split(".")[0], r["player"], r["market"])] = (pulled, _num(r["line"]))
    return out


BET_CAP_MIN_BETS = C.BET_CAP_MIN_BETS  # nbaprops/config.py


def bet_clv_status():
    """(graded BETs, average CLV in EV terms or None, BETs with CLV). Counted, graded W/L/P BETs only."""
    ledger = _read(LEDGER)
    counted, _ = counted_flags(ledger)
    bets = [r for r, ok in zip(ledger, counted) if ok and r.get("strategy") in BET_KEYS and r.get("result") in ("W", "L", "P")]
    clv = [_num(r["clv_ev"]) for r in bets if _num(r.get("clv_ev")) is not None]
    return len(bets), (sum(clv) / len(clv) if clv else None), len(clv)


def log_value_decisions(date, rows):
    """Log bp_value.py decisions. A BET already logged for `date` is never removed or replaced (a later board
    run can only add BETs); watch rows for the same pull are refreshed."""
    pulled = {r.get("pulled_at") for r in rows}
    old = _read(LEDGER)
    cls = lambda st: "bet" if st in BET_KEYS else st
    key = lambda r: (cls(r.get("strategy")), r["player"], r["market"], r["side"], str(_num(r.get("line"))))
    keep = BET_KEYS | CNO_KEYS | SCREENER_KEYS | ALT_KEYS | RULE_KEYS | UA_KEYS | ALL_EV_KEYS | REDDIT_KEYS | TRIPLE_KEYS | TWO3_KEYS | STEAM_KEYS
    have = {key(r) for r in old if r["date"] == date and r.get("strategy") in keep}
    ledger = [r for r in old if not (r["date"] == date and r.get("strategy") == "watch" and r.get("pulled_at") in pulled)]
    new = [r for r in rows if not (r.get("strategy") in keep and key(r) in have)]
    _write(LEDGER, ledger + new, LEDGER_FIELDS)


















# ------------------------------------------------------------- learning
def fit_model():
    """Fit on all graded props (one sample per player-game). Validate first: train on earlier slates,
    test on later ones, and compare with a Balanced-only model. The learned score stays capped at
    CAP_UNPROVEN model weight until the full model wins that comparison."""
    rows = [r for r in _read(GRADED) if r.get("result") in ("W", "L")]
    wt = _player_game_weights(rows)
    n_eff = round(sum(wt))
    model = {"n": n_eff, "n_props": len(rows), "trained": dt.datetime.now().isoformat(timespec="minutes"),
             "features": FEATURES, "weights": None, "validation": None, "proven": False,
             "insights": insights(rows, wt), "calibration": calibration(rows, wt)}
    if n_eff >= MIN_TRAIN:
        X = [features(r) for r in rows]
        y = [1.0 if r["result"] == "W" else 0.0 for r in rows]
        dates = sorted({r["date"] for r in rows})
        if len(dates) >= 2:
            cut = dates[-max(1, round(len(dates) * 0.3))]
            tr = [i for i, r in enumerate(rows) if r["date"] < cut]
            te = [i for i, r in enumerate(rows) if r["date"] >= cut]
            te_n = sum(wt[i] for i in te)
            if sum(wt[i] for i in tr) >= MIN_TRAIN and te_n > 0:
                full = _fit([X[i] for i in tr], [y[i] for i in tr], [wt[i] for i in tr])
                bal = _fit([[(_num(rows[i].get("balanced")) or 50) / 100] for i in tr], [y[i] for i in tr], [wt[i] for i in tr])
                ll_full = _logloss([_predict(full, X[i]) for i in te], [y[i] for i in te], [wt[i] for i in te])
                ll_bal = _logloss([_predict(bal, [(_num(rows[i].get("balanced")) or 50) / 100]) for i in te],
                                  [y[i] for i in te], [wt[i] for i in te])
                proven = te_n >= MIN_TEST and ll_full < ll_bal
                model["validation"] = {"test_from": cut, "test_n": round(te_n), "logloss_model": round(ll_full, 4),
                                       "logloss_balanced": round(ll_bal, 4), "beats_balanced": ll_full < ll_bal}
                model["proven"] = proven
        model.update(_fit(X, y, wt))
    with open(MODEL, "w") as f:
        json.dump(model, f, indent=1)
    return model


def load_model():
    """The fitted model (bp_model.json), {} if none."""
    try:
        with open(MODEL) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"n": 0, "weights": None, "insights": [], "proven": False}


def trust(model):
    """Model weight from its sample size and validation."""
    if not model.get("weights"):
        return 0.0
    n = model.get("n", 0)
    return min(n / (n + TRUST_N), CAP_PROVEN if model.get("proven") else CAP_UNPROVEN)


def learned_row(r, model):
    """Add learned_prob and learned (0-100): model EV blended with the balanced score by trust."""
    bal = _num(r.get("balanced"))
    if bal is None:
        r.update(learned_prob=None, learned=None)
        return r
    if not model.get("weights"):
        r.update(learned_prob=None, learned=round(bal))
        return r
    p = _predict(model, features(r))
    d = decimal(r.get("fd_odds_rec"))
    ev = p * d - 1 if d else 0.0
    s_model = min(max((ev + 0.10) / 0.30, 0.0), 1.0) * 100
    t = trust(model)
    r.update(learned_prob=round(p, 3), learned=round(t * s_model + (1 - t) * bal))
    return r


def calibration(rows, wt):
    """Brier score and log loss on graded props: our probability vs. FanDuel's no-vig probability."""
    out = {}
    trip = [(_num(r.get("model_prob")), no_vig(r), _num(r.get("learned_prob")), 1.0 if r["result"] == "W" else 0.0, w)
            for r, w in zip(rows, wt)]
    trip = [t for t in trip if t[0] is not None and t[1] is not None]
    if not trip:
        return out
    ws = [t[4] for t in trip]
    ys = [t[3] for t in trip]
    tot = sum(ws)
    brier = lambda ps: sum(w * (p - y) ** 2 for p, y, w in zip(ps, ys, ws)) / tot
    out["n"] = round(tot)
    out["model"] = {"brier": round(brier([t[0] for t in trip]), 4), "logloss": round(_logloss([t[0] for t in trip], ys, ws), 4)}
    out["market"] = {"brier": round(brier([t[1] for t in trip]), 4), "logloss": round(_logloss([t[1] for t in trip], ys, ws), 4)}
    lp = [t for t in trip if t[2] is not None]
    if lp:
        lws, lys = [t[4] for t in lp], [t[3] for t in lp]
        lt = sum(lws)
        out["learned"] = {"brier": round(sum(w * (t[2] - y) ** 2 for t, y, w in zip(lp, lys, lws)) / lt, 4),
                          "logloss": round(_logloss([t[2] for t in lp], lys, lws), 4), "n": round(lt)}
    return out


def insights(rows, wt):
    """Hit rates by situation (player-game weighted); each needs a minimum sample to be shown."""
    out = []
    data = list(zip(rows, wt))

    def line(label, sub, min_n=25):
        n = sum(w for _, w in sub)
        if n >= min_n:
            out.append({"label": label, "hit": round(100 * sum(w for r, w in sub if r["result"] == "W") / n, 1), "n": round(n)})

    for lo, hi in ((70, 101), (55, 70), (40, 55), (0, 40)):
        line(f"Balanced {lo}-{min(hi, 100)}", [d for d in data if lo <= (_num(d[0].get("balanced")) or 0) < hi])
    hi_form = [d for d in data if (_num(d[0].get("s_form")) or 0) >= 60]
    line("Strong form, no teammates out", [d for d in hi_form if not _num(d[0].get("tm_out"))])
    line("Strong form, teammate ruled out", [d for d in hi_form if _num(d[0].get("tm_out"))])
    lo_form = [d for d in data if (_num(d[0].get("s_form")) or 0) < 40]
    line("Weak form, teammate ruled out (overs)", [d for d in lo_form if _num(d[0].get("tm_out"))
                                                   and d[0].get("rec_side") == "over"])
    line("Placeholder projections", [d for d in data if _truthy(d[0].get("placeholder"))])
    line("Real projections", [d for d in data if not _truthy(d[0].get("placeholder"))])
    for mk in MARKETS:
        line(f"{mk} props", [d for d in data if d[0].get("market") == mk])
    return out


# --------------------------------------------------------------- record
def _summ(rows, clvs=None):
    w = sum(r["result"] == "W" for r in rows)
    l = sum(r["result"] == "L" for r in rows)
    p = sum(r["result"] == "P" for r in rows)
    units = sum(_num(r.get("profit_u")) or 0 for r in rows)
    kelly = sum(_num(r.get("profit_kelly")) or 0 for r in rows)
    staked = sum(_num(r.get("kelly_u")) or 0 for r in rows)
    probs = [(_num(r.get("our_prob")), _num(r.get("nv_prob")), 1.0 if r["result"] == "W" else 0.0)
             for r in rows if r["result"] in ("W", "L")]
    probs = [t for t in probs if t[0] is not None and t[1] is not None]
    out = {"w": w, "l": l, "p": p, "hit": round(100 * w / (w + l), 1) if (w + l) else None,
           "units": round(units, 2), "roi": round(100 * units / len(rows), 1) if rows else None,
           "kelly_units": round(kelly, 2), "kelly_roi": round(100 * kelly / staked, 1) if staked else None,
           "brier": None, "brier_mkt": None, "logloss": None, "logloss_mkt": None, "clv": None, "clv_n": 0}
    if probs:
        n = len(probs)
        out["brier"] = round(sum((a - y) ** 2 for a, _, y in probs) / n, 4)
        out["brier_mkt"] = round(sum((b - y) ** 2 for _, b, y in probs) / n, 4)
        out["logloss"] = round(_logloss([a for a, _, _ in probs], [y for *_, y in probs], [1.0] * n), 4)
        out["logloss_mkt"] = round(_logloss([b for _, b, _ in probs], [y for *_, y in probs], [1.0] * n), 4)
    if clvs:
        vals = [v for v in clvs if v is not None]
        if vals:
            out["clv"] = round(sum(vals) / len(vals), 2)
            out["clv_n"] = len(vals)
    return out


def _uniq(pairs):
    """Keep one ledger row per prop (date, game, player, market, side) across strategies."""
    seen, out = set(), []
    for r, v in pairs:
        k = (r["date"], r.get("event_id"), r["player"], r["market"], r["side"])
        if k not in seen:
            seen.add(k)
            out.append((r, v))
    return out


def half_summary(version="v2"):
    """Halftime live (experimental): flags, paste decisions and the Odds API live test. Never mixed with pregame."""
    out = {}
    try:
        from nbaprops.rules import halftime as bp_halftime
        out["flags"] = bp_halftime.flag_summary(version)
    except Exception:
        out["flags"] = {"nights": [], "by_type": [], "n": 0}
    try:
        rows = [r for r in csv.DictReader(open(os.path.join(HERE, "half_pastes.csv"))) if in_version(r, version)]
    except OSError:
        rows = []
    reasons = {}
    for r in rows:
        if r["decision"] == "pass":
            for x in (r.get("reason") or "").split("; "):
                if x:
                    reasons[x] = reasons.get(x, 0) + 1
    out["pastes"] = {"rows": len(rows), "priced": sum(r["decision"] in ("HALF BET", "pass") for r in rows),
                     "bets": sum(r["decision"] == "HALF BET" for r in rows), "why_not": reasons}
    tests = sorted(p for p in glob.glob(os.path.join(HISTORY, "*", "oddsapi_live_test.json"))
                   if in_version({"date": os.path.basename(os.path.dirname(p))}, version))
    out["oddsapi"] = json.load(open(tests[-1])) if tests else None
    return out


def ev_buckets(graded):
    """Each +EV record split by ENTRY EV (the frozen snapshot, never a later price): 0-2%, 2-4%, 4%+.
    graded = [(row, clv)] counted, graded rows. -> [{"record", "buckets": [{"bucket", n, w, l, p, units, kelly_units, clv}]}]."""
    out = []
    for label, keys in EV_RECORDS:
        sub = [(r, v) for r, v in graded if r["strategy"] in keys]
        if not sub:
            continue
        bk = []
        for lo, hi, name in EV_BUCKETS:
            g = [(r, v) for r, v in sub if (e := _num(r.get("entry_ev") or r.get("oa_ev"))) is not None and lo <= e < hi]
            if g:
                s = _summ([r for r, _ in g], [v for _, v in g])
                bk.append({"bucket": name, "n": len(g), "w": s["w"], "l": s["l"], "p": s["p"], "units": s["units"],
                           "kelly_units": s["kelly_units"], "clv": s["clv"], "clv_n": s["clv_n"]})
        out.append({"record": label, "buckets": bk})
    return out


_DVP: dict = {}


def _dvp_tag(r):
    """Side-aware DvP effect for a ledger row: its stamped dvp_tag, else from that slate's pull (cached)."""
    if r.get("dvp_tag"):
        return r["dvp_tag"]
    from nbaprops.pricing import projctx as P
    k = (r["date"], r["player"], r["market"])
    if k not in _DVP:
        try:
            _DVP[k] = P.dvp_of(r["date"], r["player"], r["market"])
        except Exception:  # noqa: BLE001
            _DVP[k] = None
    return P.dvp_effect(r.get("side"), _DVP[k])


def triple_rows(graded):
    """Triple green splits (source; entry EV bucket) and the DvP comparison: Triple green vs. Your rule vs. Your rule
    without DvP green (my_rule plays whose matchup did not help the side)."""
    out = []

    def add(key, label, sub):
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key=key, group="triple", label=label, n=len(sub))
            x["clv_unit"] = "% EV at closing fair"
            out.append(x)
    tg = [(r, v) for r, v in graded if r["strategy"] in TRIPLE_KEYS]
    for src in ("CNO", "Unabated", "both"):
        add("triple_src_" + src, f"Triple green: {src}", [(r, v) for r, v in tg if r.get("fair_src") == src])
    for lo, hi, name in EV_BUCKETS:
        add("triple_ev_" + name, f"Triple green: entry EV {name}",
            [(r, v) for r, v in tg if (e := _num(r.get("entry_ev") or r.get("oa_ev"))) is not None and lo <= e < hi])
    mr = [(r, v) for r, v in graded if r["strategy"] == "my_rule"]
    add("cmp_triple", "Compare: Triple green", tg)
    add("cmp_rule", "Compare: Your rule", mr)
    add("cmp_rule_nodvp", "Compare: Your rule without DvP green", [(r, v) for r, v in mr if _dvp_tag(r) != "helps"])
    return out


SAM_RECORDS = [("BETs", BET_KEYS), ("Alt BETs", ALT_KEYS), ("Your rule", {"my_rule"}), ("Triple green", {"triple"}),
               ("Unabated +EV & BP agrees", {"ua_agree"}), ("CrazyNinja +EV", {"cno"}), ("CNO screener", {"cno_scr", "cno_scr_sub"}),
               ("Proven poster + market +EV", {"proven_ev"}), ("HALF BETs", {"half"})]
SAM_PART_LABELS = {"edge": "Market edge", "quality": "Market quality", "bp": "Projection Model", "dvp": "DvP", "move": "Line movement",
                   "role": "Role", "form": "Form", "consensus": "Consensus", "threes": "3PM market"}


def sam_rows(graded):
    """Sam score splits from the score frozen at logging, kept short enough to read: the four tiers (each prop once),
    then each score part in three levels by its share of the part's full points (high >= 2/3, mid >= 1/3, low)."""
    import json as _json
    from nbaprops import config as _C
    out = []
    sc = _uniq([(r, v) for r, v in graded if r["strategy"] in SAM_KEYS and r.get("score_entry") not in (None, "")])

    def add(key, label, sub):
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key=key, group="sam", label=label, n=len(sub))
            x["clv_unit"] = "% EV at closing fair"
            out.append(x)
    for t, desc in (("A", "80+"), ("B", "65-79"), ("C", "50-64"), ("paper", "under 50")):
        add("sam_tier_" + t, f"Tier {t} ({desc})", [(r, v) for r, v in sc if r.get("score_tier") == t])
    from nbaprops.pricing import samscore as _ss            # the same props graded by today's learned score
    lw = _ss.active_weights()
    lsc = [(r, v, _ss.learned(r, lw)) for r, v in sc]
    for t, desc in (("A", "80+"), ("B", "65-79"), ("C", "50-64"), ("paper", "under 50")):
        add("sam_ltier_" + t, f"Learned {t} ({desc})", [(r, v) for r, v, s in lsc if s is not None and _ss.tier(s) == t])
    for part, lab in SAM_PART_LABELS.items():
        full = _C.SAM_WEIGHTS.get(part) or 0
        if full <= 0:
            continue
        lv = {"high": [], "mid": [], "low": []}
        for r, v in sc:
            try:
                pts = _json.loads(r.get("score_parts") or "{}").get(part)
            except ValueError:
                pts = None
            if pts is None:
                continue
            f = float(pts) / full
            lv["high" if f >= 2 / 3 else "mid" if f >= 1 / 3 else "low"].append((r, v))
        if sum(1 for g in lv.values() if g) < 2:
            continue
        for level in ("high", "mid", "low"):
            add(f"sam_part_{part}_{level}", f"{lab}: {level}", lv[level])
    return out

def sim_record_rows(version="v2"):
    """Sims (research, separate): bp_sim.py's own ledger, never mixed with the rows above."""
    rows = [r for r in _read(SIM_LEDGER) if r.get("result") in ("W", "L", "P") and in_version(r, version)]
    if not rows:
        return []
    out = []
    for strat, label in STRATEGIES:
        sub = [r for r in rows if r["strategy"] == strat]
        if sub:
            out.append(dict(_summ(sub), key="sim_" + strat, group="sim", label="Sim: " + label))
    once = [r for r, _ in _uniq([(r, None) for r in rows])]
    out.append(dict(_summ(once), key="sim_all", group="sim",
                    label=f"Sim: each prop once ({len({r['date'] for r in rows})} slates)"))
    return out


def placed_record_rows():
    """My bets (bp_value.py --placed): W-L, units at my stake, ROI, CLV vs. the closing fair, rule vs. my stake."""
    rows = _read(PLACED)
    if not rows:
        return []
    g = [r for r in rows if r.get("result") in ("W", "L", "P")]
    staked = sum(_num(r.get("stake_u")) or 0 for r in g)
    units = sum(_num(r.get("profit_u")) or 0 for r in g)
    clvs = [100 * _num(r["clv_ev"]) for r in rows if _num(r.get("clv_ev")) is not None]
    x = {"key": "mine", "group": "mine", "label": "My bets", "w": sum(r["result"] == "W" for r in g),
         "l": sum(r["result"] == "L" for r in g), "p": sum(r["result"] == "P" for r in g),
         "units": round(units, 2), "roi": round(100 * units / staked, 1) if staked else None,
         "kelly_units": round(units, 2), "kelly_roi": None, "brier": None, "brier_mkt": None, "logloss": None,
         "logloss_mkt": None, "clv": round(sum(clvs) / len(clvs), 2) if clvs else None, "clv_n": len(clvs),
         "clv_unit": "% EV at closing fair", "hit": None, "n_bets": len(rows), "pending": len(rows) - len(g),
         "my_stake": round(sum(_num(r.get("stake_u")) or 0 for r in rows), 2),
         "rule_stake": round(sum(_num(r.get("rule_stake")) or 0 for r in rows), 2),
         "off_board": sum(1 for r in rows if r.get("match") == "off-board")}
    if x["w"] + x["l"]:
        x["hit"] = round(100 * x["w"] / (x["w"] + x["l"]), 1)
    return [x]




def _dec(a):
    return _odds.american_to_decimal(a)


def record(version: str = "v2") -> dict:
    """Summary of counted (pre-tip) picks for the dashboard and terminal, for one rule version (v2 default; v1 = the
    archive before 2026-11-24). Versions follow slate dates, so per-day run detection is unaffected by the filter."""
    ledger = [r for r in _read(LEDGER) if in_version(r, version)]
    counted, clv = counted_flags(ledger)
    live = [(r, v) for r, ok, v in zip(ledger, counted, clv) if ok]
    graded = [(r, v) for r, v in live if r.get("result") in ("W", "L", "P")]   # voids ('V') don't count
    strategies = []
    for strat, label in STRATEGIES + VALUE_STRATEGIES:
        if version != "v1" and strat in LOG_SKIP:
            continue                                       # retired in v2 (the old score formula); kept in the v1 archive
        sub = [(r, v) for r, v in graded if r["strategy"] == strat]
        if strat in TWO3_KEYS:                             # v2.2: 2 of 3 counts only pairs with +EV (BP + DvP kept as a split)
            sub = [(r, v) for r, v in sub if r.get("fair_src") != "BP+DvP"]
        if strat in REDDIT_KEYS and not C.REDDIT_ACTIVE:
            continue                                       # Reddit paused (no app); its history stays in the ledger
        if strat in VALUE_KEYS and version == "v1" and not any(r["strategy"] == strat for r in ledger):
            continue                                       # no value-check decisions logged yet
        x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key=strat, label=label)
        if strat in BET_KEYS | CNO_KEYS | SCREENER_KEYS | HALF_KEYS | ALT_KEYS | RULE_KEYS | UA_KEYS | ALL_EV_KEYS | REDDIT_KEYS | TRIPLE_KEYS | TWO3_KEYS | STEAM_KEYS:
            x["clv_unit"] = "% EV at closing fair"
            x["ev_below_0"] = sum(1 for r, _ in live if r["strategy"] == strat and _num(r.get("close_mkt_ev")) is not None
                                  and _num(r["close_mkt_ev"]) < 0)
        x["group"] = ("rule" if strat in RULE_KEYS else "ua" if strat in UA_KEYS else "bet" if strat in BET_KEYS else
                      "cno" if strat in CNO_KEYS else
                      "screener" if strat in SCREENER_KEYS else "half" if strat in HALF_KEYS else
                      "alt" if strat in ALT_KEYS else "ext" if strat in EXT_KEYS else "allev" if strat in ALL_EV_KEYS else "reddit" if strat in REDDIT_KEYS else "triple" if strat in TRIPLE_KEYS else "two3" if strat in TWO3_KEYS else "steam" if strat in STEAM_KEYS else "model")
        strategies.append(x)
    # 10%+ EV: every prop seen at 10%+ EV by any source, once per day (highest-EV sighting first), flat 1u | ¼K
    hot = sorted([(r, v) for r, v in graded if r["strategy"] in VALUE_KEYS and (_num(r.get("entry_ev")) or -1) >= EV10_MIN],
                 key=lambda p: -(_num(p[0].get("entry_ev")) or 0))
    hot = _uniq(hot)
    x = dict(_summ([r for r, _ in hot], [v for _, v in hot]), key="ev10", group="ev10", label=f"{100 * EV10_MIN:.0f}%+ EV (any source, each prop once; research, never staked)")
    x["clv_unit"] = "% EV at closing fair"
    strategies.append(x)
    for tag, lab, keys in (("cno", "from CrazyNinja pastes", CNO_KEYS | SCREENER_KEYS | RULE_KEYS | TRIPLE_KEYS),
                           ("ua", "from Unabated", BET_KEYS | UA_KEYS | ALT_KEYS)):
        sub = [(r, v) for r, v in hot if r["strategy"] in keys]
        if sub:
            y = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="ev10_" + tag, group="ev10", label=f"10%+ EV {lab}")
            y["clv_unit"] = "% EV at closing fair"
            strategies.append(y)
    model_keys = {k for k, _ in STRATEGIES}
    uniq_all = _uniq(graded)
    uniq = _uniq([(r, v) for r, v in graded if r["strategy"] in model_keys])
    combined = dict(_summ([r for r, _ in uniq], [v for _, v in uniq]), key="all", group="model",
                    label="Projection model, each prop once")
    if any(r["strategy"] in BET_KEYS for r in ledger):
        bu = _uniq([(r, v) for r, v in graded if r["strategy"] in BET_KEYS])
        x = dict(_summ([r for r, _ in bu], [v for _, v in bu]), key="bets_all", group="bet",
                 label="All Unabated BETs")
        x["clv_unit"] = "% EV at closing fair"
        strategies.append(x)
    bx = [(r, v) for r, v in graded if r["strategy"] in BET_KEYS and r.get("exch_led") not in (None, "")]
    for flag, lab in (("True", "BETs: exchange-led fair (half+ of sources are exchanges)"), ("False", "BETs: book-led fair")):
        sub = [(r, v) for r, v in bx if str(r.get("exch_led")) == flag]
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="bets_exch_" + flag.lower(), group="bet", label=lab)
            x["clv_unit"] = "% EV at closing fair"
            strategies.append(x)
    if any(r["strategy"] in CNO_KEYS for r in ledger):
        mu = _uniq([(r, v) for r, v in graded if r["strategy"] in BET_KEYS | CNO_KEYS])
        x = dict(_summ([r for r, _ in mu], [v for _, v in mu]), key="market_all", group="cno",
                 label="Unabated BETs + CrazyNinja, each play once")
        x["clv_unit"] = "% EV at closing fair"
        strategies.append(x)
    o3 = [(r, v) for r, v in uniq if r["market"] == "3PM" and r.get("side") == "over"]
    strategies.append(dict(_summ([r for r, _ in o3], [v for _, v in o3]), key="model_3pm_over", group="model",
                           label="Projection model: 3PM overs"))
    scr = [(r, v) for r, v in graded if r["strategy"] == "cno_scr"]
    for tag, lab in (("agrees", "proj agrees (3PM/P+R)"), ("disagrees", "proj disagrees (3PM/P+R)"),
                     ("lean agrees", "BP lean agrees"), ("lean disagrees", "BP lean disagrees"), ("none", "no BP projection")):
        sub = [(r, v) for r, v in scr if (r.get("proj_tag") or "none") == tag]
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="scr_" + tag.replace(" ", "_"), group="screener",
                     label="CNO screener: " + lab)
            x["clv_unit"] = "% EV at closing fair"
            strategies.append(x)
    hb = [(r, v) for r, v in graded if r["strategy"] in HALF_KEYS]
    for tag, lab in (("exp agrees", "market EV + expected final agree"), ("exp disagrees", "market only (expected final disagrees)"),
                     ("exp none", "market only (no expected final)")):
        sub = [(r, v) for r, v in hb if (r.get("proj_tag") or "exp none") == tag]
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="half_" + tag.replace(" ", "_"), group="half",
                     label="HALF BET: " + lab)
            x["clv_unit"] = "% EV at closing fair"
            strategies.append(x)
    al = [(r, v) for r, v in graded if r["strategy"] in ALT_KEYS]
    for tag, lab in (("helps", "DvP helps the side"), ("neutral", "DvP neutral (11-20)"), ("hurts", "DvP hurts the side"),
                     ("unknown", "DvP unknown")):
        sub = [(r, v) for r, v in al if (r.get("dvp_tag") or "unknown") == tag]
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="alt_dvp_" + tag, group="alt", label="Alt: " + lab)
            x["clv_unit"] = "% EV at closing fair"
            strategies.append(x)
    strategies += triple_rows(graded)
    strategies += sam_rows(graded)
    t23 = [(r, v) for r, v in graded if r["strategy"] in TWO3_KEYS]
    for w in ("all 3", "EV+BP", "EV+DvP", "BP+DvP"):
        sub = [(r, v) for r, v in t23 if r.get("fair_src") == w]
        if sub:
            x = dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="two3_" + w, group="two3",
                     label=f"2 of 3: {w}" + (" (retired 11/28: no +EV)" if w == "BP+DvP" else ""), n=len(sub))
            x["clv_unit"] = "% EV at closing fair"
            strategies.append(x)
    ext = [(r, v) for r, v in graded if r["strategy"] in EXT_KEYS]
    for src in sorted({r.get("fair_src") or "?" for r, _ in ext}):
        sub = [(r, v) for r, v in ext if (r.get("fair_src") or "?") == src]
        strategies.append(dict(_summ([r for r, _ in sub], [v for _, v in sub]), key="ext_" + src, group="ext",
                               label=f"Outside: {src}"))
    strategies += sim_record_rows(version)          # placed_record_rows() kept but unused: the record is the system's BETs only
    by_cat = []
    for mk in MARKETS:
        sub = [r for r, _ in uniq_all if r["market"] == mk and r["result"] in ("W", "L", "P")]
        if sub:
            s = _summ(sub)
            by_cat.append({"market": mk, "w": s["w"], "l": s["l"], "p": s["p"], "units": s["units"]})
    both = [r for r, _ in uniq_all if r["result"] in ("W", "L") and _num(r.get("our_prob")) is not None
            and _num(r.get("oa_fair")) is not None]
    oa = {"n": len(both), "min": OA_COMPARE_MIN,
          "n_by_source": {s: sum(1 for r in both if (r.get("fair_src") or "OddsAPI") == s) for s in ("Unabated", "OddsAPI")}}
    if len(both) >= OA_COMPARE_MIN:
        ys = [1.0 if r["result"] == "W" else 0.0 for r in both]
        br = lambda ps: round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys), 4)
        oa.update(brier_ours=br([_num(r["our_prob"]) for r in both]), brier_oa=br([_num(r["oa_fair"]) for r in both]),
                  logloss_ours=round(_logloss([_num(r["our_prob"]) for r in both], ys, [1.0] * len(ys)), 4),
                  logloss_oa=round(_logloss([_num(r["oa_fair"]) for r in both], ys, [1.0] * len(ys)), 4))
        oa["by_source"] = {}
        for src in sorted({r.get("fair_src") or "OddsAPI" for r in both}):
            sub = [r for r in both if (r.get("fair_src") or "OddsAPI") == src]
            ys2 = [1.0 if r["result"] == "W" else 0.0 for r in sub]
            oa["by_source"][src] = {"n": len(sub), "brier": round(sum((_num(r["oa_fair"]) - y) ** 2 for r, y in zip(sub, ys2)) / len(sub), 4),
                                    "brier_ours": round(sum((_num(r["our_prob"]) - y) ** 2 for r, y in zip(sub, ys2)) / len(sub), 4)}
        pos = [r for r in both if (_num(r.get("oa_ev")) or 0) > 0]
        oa.update(oa_pos_n=len(pos), oa_pos_units=round(sum(_num(r.get("profit_u")) or 0 for r in pos), 2))
    graded_dates = sorted({r["date"] for r, _ in graded}, reverse=True)
    recent = [r for r, _ in live if r["date"] in graded_dates[:3]]
    pending = [r for r, _ in live if not r.get("result")]
    from nbaprops.rules import tipoff as bp_tipoff
    dropped = sum(1 for r, ok in zip(ledger, counted) if not ok and r.get("tip") and r.get("pulled_at")
                  and r["pulled_at"] >= bp_tipoff.cutoff(r["tip"], r["date"], r.get("event_id")))
    model = load_model()
    weights = []
    if model.get("weights"):
        weights = sorted(({"name": FEATURE_LABELS[f], "coef": round(c, 3)} for f, c in zip(FEATURES, model["weights"])),
                         key=lambda x: -abs(x["coef"]))
    try:
        from nbaprops.rules import tonight as _tn
        rr, rc = _tn.record_rows(version)
        tr = [(r, v) for r, v in zip(rr, rc) if not r["tn_held"]]
        if tr:
            strategies.insert(0, dict(_summ([r for r, _ in tr], [v for _, v in tr]), key="tonight", group="tonight",
                                      label="Tonight's bets (what you bet)", n=len(tr), clv_unit=True))
        for k, lab, sel in (("tonight_rechecked", "Tonight's bets: re-checked (both sides were +EV)", lambda r: r["tn_case"] == "re-checked" and not r["tn_held"]),
                            ("tonight_unverified", "Tonight's bets: unverified (half stake)", lambda r: r["tn_case"] == "unverified" and not r["tn_held"]),
                            ("held_data_error", "Held out: both sides +EV, data error (not bet, flat 1u)", lambda r: r["tn_held"] == "data error"),
                            ("held_outlier", "Held out: outlier EV (not bet, flat 1u)", lambda r: r["tn_held"] == "outlier")):
            sub = [(r, v) for r, v in zip(rr, rc) if sel(r)]
            if sub:
                strategies.append(dict(_summ([r for r, _ in sub], [v for _, v in sub]), key=k, group="tonight", label=lab,
                                       n=len(sub), clv_unit=True))
    except Exception:  # noqa: BLE001
        pass
    try:
        from nbaprops.rules import reddit_picks as _rp
        posters = _rp.poster_stats(version)
    except Exception:  # noqa: BLE001
        posters = []
    return {"version": version, "half": half_summary(version), "ev_buckets": ev_buckets(graded), "posters": posters, "strategies": strategies, "combined": combined, "by_cat": by_cat, "slates": len(graded_dates),
            "recent": recent, "pending": pending, "dropped_after_tip": dropped,
            "model_n": model.get("n", 0), "trust": round(100 * trust(model)), "proven": model.get("proven", False),
            "cap_unproven": round(100 * CAP_UNPROVEN), "validation": model.get("validation"),
            "calibration": model.get("calibration", {}), "weights": weights,
            "insights": model.get("insights", []), "min_train": MIN_TRAIN, "oa_compare": oa}


PERIOD_RECORDS = [("Tonight's bets (what you bet)", None), ("Your rule", {"my_rule"}), ("CNO screener", {"cno_scr"}),
                  ("BETs (Unabated)", {"bet_agree", "bet_only"}), ("Alt ladder", {"alt_mkt", "alt_proj"}),
                  ("Unabated + Proj Model", {"ua_agree"}), ("Steam", {"steam"}), ("2 of 3", {"two_of_three"}),
                  ("All +EV", {"all_ev"}), ("10%+ EV", "ev10"), ("Live +EV", "live")]


def period_records(date: str) -> list[dict]:
    """For the slip on `date`: each record's last night (date - 1), last 7 nights and all-time (v2, from 11/24).
    Same counting as the Record tab: only plays logged before tip, each prop once per record."""
    import datetime as _dt
    d = _dt.date.fromisoformat(date)
    last, wk = (d - _dt.timedelta(days=1)).isoformat(), (d - _dt.timedelta(days=7)).isoformat()
    led = [r for r in _read(LEDGER) if in_version(r, "v2") and r["date"] < date]
    counted, clv = counted_flags(led)
    graded = [(r, v) for r, ok, v in zip(led, counted, clv) if ok and r.get("result") in ("W", "L", "P")]
    from nbaprops.rules import tonight as _TN
    from nbaprops.rules import livepastes as _LP
    tb = [(r, v) for r, v in zip(*_TN.record_rows("v2")) if not r.get("tn_held") and r["date"] < date]
    first = {}
    for r in sorted(_LP.read(), key=lambda r: r["at"]):
        if r["date"] < date and r["date"] >= V2_START and (_num(r.get("ev")) or 0) > 0:
            first.setdefault((r["date"], r["player"], r["market"], r["side"], r["line"]), r)
    live = [(r, None) for r in first.values() if r.get("result") in ("W", "L", "P")]
    out = []
    for label, keys in PERIOD_RECORDS:
        if keys is None:
            pairs = tb
        elif keys == "live":
            pairs = live
        elif keys == "ev10":
            pairs = _uniq(sorted([(r, v) for r, v in graded if r["strategy"] in VALUE_KEYS and (_num(r.get("entry_ev")) or -1) >= EV10_MIN],
                                 key=lambda p: -(_num(p[0].get("entry_ev")) or 0)))
        else:
            pairs = [(r, v) for r, v in graded if r["strategy"] in keys]
            if keys == {"two_of_three"}:
                pairs = [(r, v) for r, v in pairs if r.get("fair_src") != "BP+DvP"]
            pairs = _uniq(pairs)
        row = {"label": label}
        for name, lo in (("last", last), ("week", wk), ("all", "0000")):
            sub = [(r, v) for r, v in pairs if lo <= r["date"] and (name != "last" or r["date"] == last)]
            row[name] = dict(_summ([r for r, _ in sub], [v for _, v in sub]), n=len(sub))
        if row["all"]["n"]:
            out.append(row)
    return out
