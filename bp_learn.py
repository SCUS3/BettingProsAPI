"""
Pick tracking, grading and self-learning for bp_props.py.

Each slate:
  1. log_picks()   writes the top 5 picks from each strategy to bp_ledger.csv
  2. grade_date()  (next run) pulls the finished slate, grades every prop and every pick
  3. fit_model()   re-learns how much each factor actually predicts a win from all graded props
  4. learned_row() scores tonight's props with that model, blended with the balanced score
     until there is enough history to trust it

Files (all local, git-ignored): bp_ledger.csv, bp_graded.csv, bp_model.json.
Standard library only.
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

PICKS_PER_STRATEGY = 5
STRATEGIES = [("bal", "Top 5 balanced"), ("score", "Top 5 your score"), ("gap", "Top 5 projection gap"),
              ("learned", "Top 5 learned")]
MIN_TRAIN = 60          # graded props needed before the model is fit at all
TRUST_N = 400           # model weight = n / (n + TRUST_N), capped below
MAX_TRUST = 0.8

LEDGER_FIELDS = ["date", "strategy", "rank", "player", "team", "market", "side", "line", "odds", "gap",
                 "score", "balanced", "learned", "kelly_u", "injury", "tm_out", "event_id",
                 "result", "actual", "profit_u", "profit_kelly"]
FEATURES = ["edge", "ev", "matchup", "form", "market", "implied", "under", "self_injured",
            "teammates_out", "teammates_dtd", "form_x_teammates_out", "threes", "combo"]
FEATURE_LABELS = {
    "edge": "Projection edge", "ev": "EV at FanDuel price", "matchup": "Matchup (DvP)",
    "form": "Recent form vs. line", "market": "Market value", "implied": "Price (implied prob.)",
    "under": "Under side", "self_injured": "Player tagged DTD", "teammates_out": "Teammates ruled out",
    "teammates_dtd": "Teammates DTD", "form_x_teammates_out": "Form when teammates are out",
    "threes": "Threes market", "combo": "Combo market (P+A, P+R, PRA)",
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
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


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


# ---------------------------------------------------------- teammates
def _regulars(date, lookback=5):
    """{team: {player}} for players with a points prop in recent saved slates before `date`.
    Ruled-out players usually have no props at all, so a missing regular is the best rule-out signal available."""
    paths = sorted(p for p in glob.glob(os.path.join(HERE, "bp_props_*.csv")) if os.path.basename(p)[9:-4] < date)[-lookback:]
    seen = {}
    for path in paths:
        for r in _read(path):
            if r.get("market") == "PTS" and r.get("team"):
                seen.setdefault((r["team"], r["player"]), set()).add(path)
    need = 2 if len(paths) >= 2 else 1
    out = {}
    for (team, player), slates in seen.items():
        if len(slates) >= need:
            out.setdefault(team, set()).add(player)
    return out


def add_teammate_context(rows, date):
    """tm_out / tm_dtd per prop, plus tm_out_names. A teammate counts as out when tagged OUT,
    or when he had points props in recent slates but has none tonight while his team plays.
    A rule-out can open minutes and shots that recent form vs. the line doesn't capture."""
    tonight, status = {}, {}
    for r in rows:
        if r.get("team") and r.get("player"):
            tonight.setdefault(r["team"], set()).add(r["player"])
            status[(r["team"], r["player"])] = (r.get("injury") or "").strip().lower()
    missing = {team: regs - tonight.get(team, set()) for team, regs in _regulars(date).items() if team in tonight}
    for r in rows:
        team, me = r.get("team"), r.get("player")
        out_names = sorted(n for n in missing.get(team, set()))
        out_names += sorted(p for (t, p), st in status.items() if t == team and p != me and st in ("out", "o", "suspended"))
        r["tm_out"] = len(out_names)
        r["tm_out_names"] = ", ".join(out_names)
        r["tm_dtd"] = sum(1 for (t, p), st in status.items()
                          if t == team and p != me and st in ("dtd", "gtd", "questionable", "doubtful", "q", "d"))
    return rows


# ------------------------------------------------------------ features
def features(r):
    s = lambda k: (_num(r.get(k)) if _num(r.get(k)) is not None else 50.0) / 100
    tm_out = min(_num(r.get("tm_out")) or 0, 2) / 2
    tm_dtd = min(_num(r.get("tm_dtd")) or 0, 3) / 3
    inj = (r.get("injury") or "").strip().lower()
    mk = r.get("market") or ""
    imp = implied(r.get("fd_odds_rec"))
    form = s("s_form")
    return [s("s_edge"), s("s_ev"), s("s_dvp"), form, s("s_market"), imp if imp is not None else 0.5,
            1.0 if (r.get("rec_side") or "").lower() == "under" else 0.0,
            1.0 if inj and inj != "probable" else 0.0,
            tm_out, tm_dtd, form * tm_out,
            1.0 if mk == "3PM" else 0.0, 1.0 if mk in ("P+A", "P+R", "PRA") else 0.0]


def _sig(z):
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


# ------------------------------------------------------------- logging
def log_picks(rows, date):
    """Replace the ledger's picks for `date` with the top picks of each strategy (kept results stay)."""
    ledger = _read(LEDGER)
    old = {(r["date"], r["strategy"], r["player"], r["market"], r["side"]): r for r in ledger if r["date"] == date}
    keep = [r for r in ledger if r["date"] != date]
    pool = [r for r in rows if r.get("price_is_fanduel") in (True, "True") and r.get("score") is not None
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
            row = {"date": date, "strategy": strat, "rank": i, "player": r.get("player"), "team": r.get("team"),
                   "market": r.get("market"), "side": r.get("rec_side"), "line": r.get("line"),
                   "odds": r.get("fd_odds_rec"), "gap": r.get("pct_gap"), "score": r.get("score"),
                   "balanced": r.get("balanced"), "learned": r.get("learned"), "kelly_u": r.get("kelly_u"),
                   "injury": r.get("injury"), "tm_out": r.get("tm_out"), "event_id": r.get("event_id")}
            prev = old.get((date, strat, row["player"], row["market"], row["side"]))
            if prev and prev.get("result"):
                row.update({k: prev[k] for k in ("result", "actual", "profit_u", "profit_kelly")})
            new.append(row)
    _write(LEDGER, keep + new, LEDGER_FIELDS)
    return len(new)


# ------------------------------------------------------------- grading
def pending_dates(today):
    """Slates with a saved CSV before `today` that still have ungraded picks or no graded props."""
    graded_dates = {r["date"] for r in _read(GRADED)}
    open_ledger = {r["date"] for r in _read(LEDGER) if not r.get("result")}
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "bp_props_*.csv"))):
        d = os.path.basename(path)[9:-4]
        if d < today and (d not in graded_dates or d in open_ledger):
            out.append(d)
    return out


def grade_date(date, fetched, labels):
    """Grade the saved slate for `date` against freshly pulled props (with final stats).
    Returns (props graded, picks graded, still pending)."""
    finals = {}
    for p in fetched:
        sc = p.get("scoring") or {}
        if not sc.get("is_scored"):
            continue
        part = p.get("participant") or {}
        mk = labels.get(p.get("market_id"), p.get("market_id"))
        finals[prop_key(p.get("event_id"), mk, part.get("name"))] = sc.get("actual")

    slate = _read(os.path.join(HERE, f"bp_props_{date}.csv"))
    graded = [r for r in _read(GRADED) if r["date"] != date]
    n_props = 0
    for r in slate:
        if r.get("price_is_fanduel") != "True" or not r.get("score"):
            continue
        actual = finals.get(prop_key(r.get("event_id"), r.get("market"), r.get("player")))
        res = grade(r.get("rec_side"), r.get("line"), actual)
        if res is None:
            continue
        r = dict(r, date=date, actual=actual, result=res)
        graded.append(r)
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
    """Logistic regression (win/loss of the recommended side) on every graded FanDuel prop."""
    data = [(features(r), 1.0 if r["result"] == "W" else 0.0, r) for r in _read(GRADED) if r.get("result") in ("W", "L")]
    n = len(data)
    model = {"n": n, "trained": dt.datetime.now().isoformat(timespec="minutes"), "features": FEATURES,
             "weights": None, "insights": insights(data)}
    if n >= MIN_TRAIN:
        X = [d[0] for d in data]
        y = [d[1] for d in data]
        k = len(FEATURES)
        mu = [sum(x[j] for x in X) / n for j in range(k)]
        sd = [max(math.sqrt(sum((x[j] - mu[j]) ** 2 for x in X) / n), 1e-6) for j in range(k)]
        Z = [[(x[j] - mu[j]) / sd[j] for j in range(k)] for x in X]
        w, b = [0.0] * k, 0.0
        lam = 5.0 / n                                   # stronger shrinkage on small samples
        for _ in range(600):
            gw, gb = [0.0] * k, 0.0
            for z, t in zip(Z, y):
                e = _sig(b + sum(wj * zj for wj, zj in zip(w, z))) - t
                gb += e
                for j in range(k):
                    gw[j] += e * z[j]
            b -= 0.5 * gb / n
            w = [wj - 0.5 * (g / n + lam * wj) for wj, g in zip(w, gw)]
        model.update(weights=w, bias=b, mu=mu, sd=sd)
    with open(MODEL, "w") as f:
        json.dump(model, f, indent=1)
    return model


def load_model():
    try:
        with open(MODEL) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"n": 0, "weights": None, "insights": []}


def learned_row(r, model):
    """Add learned_prob and learned (0-100): model EV blended with the balanced score by trust."""
    bal = _num(r.get("balanced"))
    if bal is None:
        r.update(learned_prob=None, learned=None)
        return r
    if not model.get("weights"):
        r.update(learned_prob=None, learned=round(bal))
        return r
    z = [(x - m) / s for x, m, s in zip(features(r), model["mu"], model["sd"])]
    p = _sig(model["bias"] + sum(w * v for w, v in zip(model["weights"], z)))
    d = decimal(r.get("fd_odds_rec"))
    ev = p * d - 1 if d else 0.0
    s_model = min(max((ev + 0.10) / 0.30, 0.0), 1.0) * 100
    trust = min(model["n"] / (model["n"] + TRUST_N), MAX_TRUST)
    r.update(learned_prob=round(p, 3), learned=round(trust * s_model + (1 - trust) * bal))
    return r


def insights(data):
    """Plain-language findings from graded props; each needs a minimum sample to be shown."""
    out = []
    def rate(rows):
        dec = [t for _, t, _ in rows]
        return (sum(dec) / len(dec), len(dec)) if dec else (None, 0)

    def line(label, rows, min_n=25):
        p, n = rate(rows)
        if n >= min_n:
            out.append({"label": label, "hit": round(p * 100, 1), "n": n})

    for lo, hi in ((70, 101), (55, 70), (40, 55), (0, 40)):
        line(f"Balanced {lo}-{min(hi, 100)}", [d for d in data if lo <= (_num(d[2].get("balanced")) or 0) < hi])
    hi_form = [d for d in data if (_num(d[2].get("s_form")) or 0) >= 60]
    line("Strong form, no teammates out", [d for d in hi_form if not _num(d[2].get("tm_out"))])
    line("Strong form, teammate ruled out", [d for d in hi_form if _num(d[2].get("tm_out"))])
    lo_form = [d for d in data if (_num(d[2].get("s_form")) or 0) < 40]
    line("Weak form, teammate ruled out (overs)", [d for d in lo_form if _num(d[2].get("tm_out"))
                                                   and d[2].get("rec_side") == "over"])
    for mk in ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"):
        line(f"{mk} props", [d for d in data if d[2].get("market") == mk])
    return out


# --------------------------------------------------------------- record
def record():
    """Summary of the ledger for the dashboard and terminal."""
    ledger = _read(LEDGER)
    strategies = []
    for strat, label in STRATEGIES:
        rows = [r for r in ledger if r["strategy"] == strat and r.get("result")]
        w = sum(r["result"] == "W" for r in rows)
        l = sum(r["result"] == "L" for r in rows)
        p = sum(r["result"] == "P" for r in rows)
        units = sum(_num(r.get("profit_u")) or 0 for r in rows)
        kelly = sum(_num(r.get("profit_kelly")) or 0 for r in rows)
        staked_k = sum(_num(r.get("kelly_u")) or 0 for r in rows)
        strategies.append({"key": strat, "label": label, "w": w, "l": l, "p": p,
                           "hit": round(100 * w / (w + l), 1) if (w + l) else None,
                           "units": round(units, 2), "roi": round(100 * units / len(rows), 1) if rows else None,
                           "kelly_units": round(kelly, 2),
                           "kelly_roi": round(100 * kelly / staked_k, 1) if staked_k else None})
    by_cat = []
    for mk in ("PTS", "REB", "AST", "3PM", "P+A", "P+R", "PRA"):
        rows = [r for r in ledger if r["market"] == mk and r.get("result") in ("W", "L")]
        if rows:
            w = sum(r["result"] == "W" for r in rows)
            by_cat.append({"market": mk, "w": w, "l": len(rows) - w,
                           "units": round(sum(_num(r.get("profit_u")) or 0 for r in rows), 2)})
    dates = sorted({r["date"] for r in ledger}, reverse=True)
    graded_dates = [d for d in dates if any(r["date"] == d and r.get("result") for r in ledger)]
    recent = [r for r in ledger if r["date"] in graded_dates[:3]]
    pending = [r for r in ledger if not r.get("result")]
    model = load_model()
    weights = []
    if model.get("weights"):
        weights = sorted(({"name": FEATURE_LABELS[f], "coef": round(c, 3)} for f, c in zip(FEATURES, model["weights"])),
                         key=lambda x: -abs(x["coef"]))
    return {"strategies": strategies, "by_cat": by_cat, "slates": len(graded_dates),
            "recent": recent, "pending": pending, "model_n": model.get("n", 0),
            "trust": round(100 * min(model.get("n", 0) / (model.get("n", 0) + TRUST_N), MAX_TRUST)) if model.get("weights") else 0,
            "weights": weights, "insights": model.get("insights", []), "min_train": MIN_TRAIN}
