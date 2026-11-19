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

import csv
import datetime as dt
import glob
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "bp_ledger.csv")
GRADED = os.path.join(HERE, "bp_graded.csv")
MODEL = os.path.join(HERE, "bp_model.json")
HISTORY = os.path.join(HERE, "history")

PICKS_PER_STRATEGY = 5
STRATEGIES = [("bal", "Top 5 balanced"), ("score", "Top 5 your score"), ("gap", "Top 5 projection gap"),
              ("learned", "Top 5 learned")]
MIN_TRAIN = 60            # player-games needed before the model is fit at all
TRUST_N = 400             # model weight = n / (n + TRUST_N), then capped:
CAP_UNPROVEN = 0.5        #   until the model beats Balanced on held-out later slates
CAP_PROVEN = 0.8          #   after it does
MIN_TEST = 100            # held-out player-games needed for that comparison
L2_BASE = 0.05            # L2 penalty on standardized weights (plus 5/n on small samples)
TM_MIN_PTS_LINE = 9.5     # stand-in for "averages 20+ minutes" (no minutes data in the API)

LEDGER_FIELDS = ["date", "pulled_at", "tip", "strategy", "rank", "player", "team", "market", "side", "line",
                 "odds", "gap", "placeholder", "score", "balanced", "learned", "our_prob", "nv_prob", "kelly_u",
                 "injury", "tm_out", "event_id", "result", "actual", "profit_u", "profit_kelly"]
FEATURES = ["edge", "ev", "matchup", "form", "market", "implied", "under", "self_injured",
            "teammates_out", "teammates_dtd", "form_x_teammates_out", "threes", "combo", "placeholder"]
FEATURE_LABELS = {
    "edge": "Projection edge", "ev": "EV at FanDuel price", "matchup": "Matchup (DvP)",
    "form": "Recent form vs. line", "market": "Market value", "implied": "Price (implied prob.)",
    "under": "Under side", "self_injured": "Player tagged DTD", "teammates_out": "Teammates ruled out",
    "teammates_dtd": "Teammates DTD", "form_x_teammates_out": "Form when teammates are out",
    "threes": "Threes market", "combo": "Combo market (P+A, P+R, PRA)",
    "placeholder": "Placeholder projection",
}


# ---------------------------------------------------------------- helpers
def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _read(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _write(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _truthy(v):
    return v in (True, "True", "true", 1, "1")


def implied(odds):
    o = _num(odds)
    if o is None:
        return None
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def decimal(odds):
    o = _num(odds)
    if o is None:
        return None
    return 1 + (o / 100 if o > 0 else 100 / -o)


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


def grade(side, line, actual):
    """'W', 'L' or 'P' for the recommended side, None if not gradable."""
    line, actual = _num(line), _num(actual)
    if line is None or actual is None or side not in ("over", "under"):
        return None
    if actual == line:
        return "P"
    return "W" if (actual > line) == (side == "over") else "L"


def profit(odds, result, stake=1.0):
    if result == "W":
        d = decimal(odds)
        return round(stake * (d - 1), 3) if d else None
    if result == "L":
        return -stake
    if result == "P":
        return 0.0
    return None


def prop_key(event_id, market, player):
    return f"{event_id}|{market}|{player}"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tip_iso(scheduled):
    """BettingPros 'scheduled' ('2026-11-19 00:00:00', UTC) -> '2026-11-19T00:00:00Z'."""
    return scheduled.replace(" ", "T") + "Z" if scheduled else None


# ------------------------------------------------------------ snapshots
def save_snapshot(rows, date, fields):
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


def add_teammate_context(rows, date):
    """tm_out / tm_out_names / tm_dtd per prop. A rotation teammate counts as out when tagged OUT, or when he
    had points props in recent slates but has none tonight while his team plays (ruled-out players lose their props)."""
    tonight, status, pts_line = {}, {}, {}
    for r in rows:
        if r.get("team") and r.get("player"):
            tonight.setdefault(r["team"], set()).add(r["player"])
            status[(r["team"], r["player"])] = (r.get("injury") or "").strip().lower()
            if r.get("market") == "PTS" and _num(r.get("line")) is not None:
                pts_line[(r["team"], r["player"])] = _num(r["line"])
    missing = {team: regs - tonight.get(team, set()) for team, regs in _regulars(date).items() if team in tonight}
    for r in rows:
        team, me = r.get("team"), r.get("player")
        names = set(missing.get(team, set()))
        names |= {p for (t, p), st in status.items() if t == team and p != me and st in ("out", "o", "suspended")
                  and pts_line.get((t, p), TM_MIN_PTS_LINE) >= TM_MIN_PTS_LINE}
        r["tm_out"] = len(names)
        r["tm_out_names"] = ", ".join(sorted(names))
        r["tm_dtd"] = sum(1 for (t, p), st in status.items()
                          if t == team and p != me and st in ("dtd", "gtd", "questionable", "doubtful", "q", "d"))
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
    return [None if ph else s("s_edge"), s("s_ev"), s("s_dvp"), form, s("s_market"), imp if imp is not None else 0.5,
            1.0 if (r.get("rec_side") or "").lower() == "under" else 0.0,
            1.0 if inj and inj != "probable" else 0.0,
            tm_out, tm_dtd, form * tm_out,
            1.0 if mk == "3PM" else 0.0, 1.0 if mk in ("P+A", "P+R", "PRA") else 0.0,
            1.0 if ph else 0.0]


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
    keep = [r for r in ledger if not (r["date"] == date and r.get("pulled_at", "") == (pulled or ""))]
    pool = [r for r in rows if _truthy(r.get("price_is_fanduel")) and r.get("score") is not None
            and (r.get("injury") or "").strip().lower() not in ("out", "doubtful")]
    sorters = {
        "gap": lambda r: abs(_num(r.get("pct_gap")) or 0),
        "score": lambda r: _num(r.get("score")) or 0,
        "bal": lambda r: _num(r.get("balanced")) or 0,
        "learned": lambda r: _num(r.get("learned")) or 0,
    }
    new = []
    for strat, _ in STRATEGIES:
        for i, r in enumerate(sorted(pool, key=sorters[strat], reverse=True)[:PICKS_PER_STRATEGY], 1):
            our = _num(r.get("learned_prob")) if strat == "learned" and r.get("learned_prob") not in (None, "") else _num(r.get("model_prob"))
            nv = no_vig(r)
            row = {"date": date, "pulled_at": pulled, "tip": r.get("tip"), "strategy": strat, "rank": i,
                   "player": r.get("player"), "team": r.get("team"), "market": r.get("market"), "side": r.get("rec_side"),
                   "line": r.get("line"), "odds": r.get("fd_odds_rec"), "gap": r.get("pct_gap"),
                   "placeholder": r.get("placeholder"), "score": r.get("score"), "balanced": r.get("balanced"),
                   "learned": r.get("learned"), "our_prob": None if our is None else round(our, 4),
                   "nv_prob": None if nv is None else round(nv, 4), "kelly_u": r.get("kelly_u"),
                   "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id")}
            prev = old.get((date, pulled, strat, row["player"], row["market"], row["side"]))
            if prev and prev.get("result"):
                row.update({k: prev[k] for k in ("result", "actual", "profit_u", "profit_kelly")})
            new.append(row)
    _write(LEDGER, keep + new, LEDGER_FIELDS)
    return len(new)


def counted_flags(ledger):
    """counted[i] = True when ledger row i came from the last run before its game's tip.
    clv[i] = closing-line value in points for counted rows seen in an earlier pre-tip run."""
    runs = {}
    for r in ledger:
        if r.get("pulled_at"):
            runs.setdefault(r["date"], set()).add(r["pulled_at"])
    first_seen = {}
    for r in ledger:
        if r.get("pulled_at") and r.get("tip") and r["pulled_at"] < r["tip"]:
            k = (r["date"], r["strategy"], r["event_id"], r["player"], r["market"], r["side"])
            if k not in first_seen or r["pulled_at"] < first_seen[k][0]:
                first_seen[k] = (r["pulled_at"], _num(r.get("line")))
    counted, clv = [], []
    for r in ledger:
        p, tip = r.get("pulled_at"), r.get("tip")
        if not p or not tip or p >= tip:
            counted.append(False)
            clv.append(None)
            continue
        last = max(t for t in runs[r["date"]] if t < tip)
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
def pending_dates(today):
    """Slates before `today` that still have ungraded ledger rows or no graded props."""
    graded_dates = {r["date"] for r in _read(GRADED)}
    open_ledger = {r["date"] for r in _read(LEDGER) if not r.get("result")}
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "bp_props_*.csv"))):
        d = os.path.basename(path)[9:-4]
        if d < today and (d not in graded_dates or d in open_ledger):
            out.append(d)
    return out


def grade_date(date, fetched, labels):
    """Grade the slate for `date` against freshly pulled props with final stats.
    Learning rows come from each game's last pre-tip snapshot. Returns (props graded, picks graded, still pending)."""
    finals = {}
    for p in fetched:
        sc = p.get("scoring") or {}
        if not sc.get("is_scored"):
            continue
        part = p.get("participant") or {}
        mk = labels.get(p.get("market_id"), p.get("market_id"))
        finals[prop_key(p.get("event_id"), mk, part.get("name"))] = sc.get("actual")

    graded = [r for r in _read(GRADED) if r["date"] != date]
    n_props = 0
    for r in pre_tip_rows(date):
        if not _truthy(r.get("price_is_fanduel")) or not r.get("score"):
            continue
        actual = finals.get(prop_key(r.get("event_id"), r.get("market"), r.get("player")))
        res = grade(r.get("rec_side"), r.get("line"), actual)
        if res is None:
            continue
        graded.append(dict(r, date=date, actual=actual, result=res))
        n_props += 1
    if graded:
        fields = sorted({k for r in graded for k in r}, key=lambda k: (k != "date", k))
        _write(GRADED, graded, fields)

    ledger = _read(LEDGER)
    n_picks = pending = 0
    for r in ledger:
        if r["date"] != date or r.get("result"):
            continue
        actual = finals.get(prop_key(r.get("event_id"), r.get("market"), r.get("player")))
        res = grade(r.get("side"), r.get("line"), actual)
        if res is None:
            pending += 1
            continue
        r["result"], r["actual"] = res, actual
        r["profit_u"] = profit(r.get("odds"), res)
        k = _num(r.get("kelly_u")) or 0
        r["profit_kelly"] = profit(r.get("odds"), res, k) if k else 0.0
        n_picks += 1
    _write(LEDGER, ledger, LEDGER_FIELDS)
    return n_props, n_picks, pending


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
    try:
        with open(MODEL) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"n": 0, "weights": None, "insights": [], "proven": False}


def trust(model):
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
    for mk in ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"):
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


def record():
    """Summary of counted (pre-tip) picks for the dashboard and terminal."""
    ledger = _read(LEDGER)
    counted, clv = counted_flags(ledger)
    live = [(r, v) for r, ok, v in zip(ledger, counted, clv) if ok]
    graded = [(r, v) for r, v in live if r.get("result")]
    strategies = []
    for strat, label in STRATEGIES:
        sub = [(r, v) for r, v in graded if r["strategy"] == strat]
        strategies.append(dict(_summ([r for r, _ in sub], [v for _, v in sub]), key=strat, label=label))
    uniq = _uniq(graded)
    combined = dict(_summ([r for r, _ in uniq], [v for _, v in uniq]), key="all", label="All picks (each prop once)")
    by_cat = []
    for mk in ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"):
        sub = [r for r, _ in uniq if r["market"] == mk and r["result"] in ("W", "L", "P")]
        if sub:
            s = _summ(sub)
            by_cat.append({"market": mk, "w": s["w"], "l": s["l"], "p": s["p"], "units": s["units"]})
    graded_dates = sorted({r["date"] for r, _ in graded}, reverse=True)
    recent = [r for r, _ in live if r["date"] in graded_dates[:3]]
    pending = [r for r, _ in live if not r.get("result")]
    dropped = sum(1 for r, ok in zip(ledger, counted) if not ok and r.get("tip") and r.get("pulled_at")
                  and r["pulled_at"] >= r["tip"])
    model = load_model()
    weights = []
    if model.get("weights"):
        weights = sorted(({"name": FEATURE_LABELS[f], "coef": round(c, 3)} for f, c in zip(FEATURES, model["weights"])),
                         key=lambda x: -abs(x["coef"]))
    return {"strategies": strategies, "combined": combined, "by_cat": by_cat, "slates": len(graded_dates),
            "recent": recent, "pending": pending, "dropped_after_tip": dropped,
            "model_n": model.get("n", 0), "trust": round(100 * trust(model)), "proven": model.get("proven", False),
            "cap_unproven": round(100 * CAP_UNPROVEN), "validation": model.get("validation"),
            "calibration": model.get("calibration", {}), "weights": weights,
            "insights": model.get("insights", []), "min_train": MIN_TRAIN}
