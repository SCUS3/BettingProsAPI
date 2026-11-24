"""Sam score weights from results.

Model (log-odds): logit p = logit(FanDuel no-vig) + b + sum_j w_j * part_j, parts = the Sam score fractions (0-1), each
prop once, player-games weighted 1/k (as the learned model), L2 penalty on standardized parts (L2_BASE + 5/n, as the
learned model), solved by Newton steps so the penalized Hessian gives 95% Wald intervals. Held out: the latest ~30% of
slates. Compared on held-out log loss with FanDuel no-vig alone and with the hand-set score (logit p = logit(no-vig) + a +
c * score/100, a and c fitted on the training slates).

    python3 -m nbaprops.pricing.samfit            # one-off report on all graded data (v1 + v2); changes nothing
"""
from __future__ import annotations

import json
import math
import os

from nbaprops import config as C
from nbaprops.pricing import samscore as SS
from nbaprops.rules import ledger as L
from nbaprops.sources.rotowire import norm

SKIP = {"ext_pick", "reddit_pick", "watch"}


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sig(z):
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def _inv(M):
    n = len(M)
    A = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(M)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        piv = A[c][c] or 1e-12
        A[c] = [v / piv for v in A[c]]
        for r in range(n):
            if r != c:
                f = A[r][c]
                A[r] = [a - f * b for a, b in zip(A[r], A[c])]
    return [row[n:] for row in A]


def fit(X, y, w, off, lam=None):
    """Weighted L2 logistic with offset on standardized columns (Newton). -> {coef (per full part), se, bias, mu, sd}."""
    n_eff, k = sum(w), len(X[0])
    mu = [sum(x[j] * wi for x, wi in zip(X, w)) / n_eff for j in range(k)]
    sd = [max(math.sqrt(sum(wi * (x[j] - mu[j]) ** 2 for x, wi in zip(X, w)) / n_eff), 1e-6) for j in range(k)]
    Z = [[1.0] + [(x[j] - mu[j]) / sd[j] for j in range(k)] for x in X]
    lam = (L.L2_BASE + 5.0 / max(n_eff, 1)) if lam is None else lam
    beta = [0.0] * (k + 1)
    for _ in range(50):
        g, H = [0.0] * (k + 1), [[0.0] * (k + 1) for _ in range(k + 1)]
        for z, t, wi, o in zip(Z, y, w, off):
            p = _sig(o + sum(a * b for a, b in zip(beta, z)))
            for a in range(k + 1):
                g[a] += wi * (p - t) * z[a]
                for b in range(k + 1):
                    H[a][b] += wi * p * (1 - p) * z[a] * z[b]
        for a in range(1, k + 1):
            g[a] += lam * n_eff * beta[a]
            H[a][a] += lam * n_eff
        step = [sum(r * gg for r, gg in zip(row, g)) for row in _inv(H)]
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    cov = _inv(H)
    flat = [sd[j] <= 1e-6 for j in range(k)]                    # no variation in the data: not estimable
    coef = [None if flat[j] else beta[j + 1] / sd[j] for j in range(k)]
    se = [None if flat[j] else math.sqrt(max(cov[j + 1][j + 1], 0)) / sd[j] for j in range(k)]
    return {"beta": beta, "coef": coef, "se": se, "mu": mu, "sd": sd}


def predict(m, x, o):
    z = [1.0] + [(v - mu) / sd for v, mu, sd in zip(x, m["mu"], m["sd"])]
    return _sig(o + sum(a * b for a, b in zip(m["beta"], z)))


def _ll(ps, ys, ws):
    return sum(w * -(y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6))) for p, y, w in zip(ps, ys, ws)) / (sum(ws) or 1)


def dataset(version=None):
    """Graded props (W/L), each once, with Sam parts, the hand-set score, FanDuel no-vig at that line and a 1/k weight."""
    led = [r for r in L._read(L.LEDGER) if r.get("result") in ("W", "L") and r["strategy"] not in SKIP
           and (version is None or L.in_version(r, version))]
    seen, rows = set(), []
    for r in sorted(led, key=lambda r: (r["strategy"] not in L.SAM_KEYS, r.get("pulled_at") or "")):
        k = (r["date"], norm(r["player"]), r["market"], r["side"], str(L._num(r.get("line"))))
        if k in seen:
            continue
        seen.add(k)
        br = SS.board_row(r["date"], r["player"], r["market"])
        o, u = L.implied(br.get("over_odds")), L.implied(br.get("under_odds"))
        if not br or o is None or u is None or L._num(br.get("line")) != L._num(r.get("line")):
            continue
        nv = (o if r["side"] == "over" else u) / (o + u)
        try:
            ctx = SS.stores(r["date"])
            feat = SS.features(r, ctx=ctx)
        except Exception:  # noqa: BLE001
            continue
        rows.append({"date": r["date"], "event_id": r.get("event_id"), "player": r["player"], "y": 1.0 if r["result"] == "W" else 0.0,
                     "x": [feat["parts"][p] for p in SS.PARTS], "score": SS.total(feat, C.SAM_WEIGHTS), "nv": nv})
    wts = L._player_game_weights(rows)
    for r, w in zip(rows, wts):
        r["w"] = w
    return rows


def compare(rows, test_frac=0.3):
    """Fit on early slates, score the latest ~30% of slates. -> dict with the full-data fit and held-out log losses."""
    dates = sorted({r["date"] for r in rows})
    cut = dates[max(1, int(len(dates) * (1 - test_frac)))] if len(dates) > 1 else None
    tr = [r for r in rows if cut is None or r["date"] < cut]
    te = [r for r in rows if cut is not None and r["date"] >= cut]
    full = fit([r["x"] for r in rows], [r["y"] for r in rows], [r["w"] for r in rows], [_logit(r["nv"]) for r in rows])
    out = {"n": len(rows), "n_eff": round(sum(r["w"] for r in rows), 1), "slates": len(dates), "full": full,
           "test_slates": len([d for d in dates if cut and d >= cut]), "n_test": len(te)}
    if tr and te:
        m = fit([r["x"] for r in tr], [r["y"] for r in tr], [r["w"] for r in tr], [_logit(r["nv"]) for r in tr])
        h = fit([[r["score"] / 100] for r in tr], [r["y"] for r in tr], [r["w"] for r in tr], [_logit(r["nv"]) for r in tr])
        ys, ws = [r["y"] for r in te], [r["w"] for r in te]
        out.update(ll_nv=_ll([r["nv"] for r in te], ys, ws), ll_hand=_ll([predict(h, [r["score"] / 100], _logit(r["nv"])) for r in te], ys, ws),
                   ll_fit=_ll([predict(m, r["x"], _logit(r["nv"])) for r in te], ys, ws), train_fit=m)
    return out


def table(res) -> list[str]:
    f = res["full"]
    lines = [f"Sam score weight fit: {res['n']} graded props ({res['n_eff']} player-games), {res['slates']} slates; offset = "
             "logit(FanDuel no-vig); coefficients in log-odds per full part (0 -> 1).", "",
             "| Part | Hand-set pts | Fitted log-odds | 95% CI | Excludes 0 |", "|---|---:|---:|---|:-:|"]
    for p, c, se in zip(SS.PARTS, f["coef"], f["se"]):
        if c is None:
            lines.append(f"| {p} | {C.SAM_WEIGHTS[p]} | no variation | – | – |")
            continue
        lo, hi = c - 1.96 * se, c + 1.96 * se
        lines.append(f"| {p} | {C.SAM_WEIGHTS[p]} | {c:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {'yes' if lo > 0 or hi < 0 else 'no'} |")
    if "ll_fit" in res:
        lines += ["", f"Held-out log loss ({res['test_slates']} latest slates, {res['n_test']} props; lower is better): "
                  f"FanDuel no-vig {res['ll_nv']:.4f} | hand-set score {res['ll_hand']:.4f} | fitted parts {res['ll_fit']:.4f}"]
    return lines


def weekly_check() -> list[str]:
    """Weekly review: v2 fit vs. hand-set vs. no-vig. Switch to fitted weights (sam_weights.json) only with 500+ graded v2
    player-games AND a held-out win over both; otherwise keep the hand-set weights."""
    rows = dataset("v2")
    n_eff = sum(r["w"] for r in rows)
    lines = ["## Sam score weights", ""]
    if n_eff < C.SAM_FIT_MIN:
        return lines + [f"{n_eff:.0f} graded v2 player-games with a FanDuel no-vig price; fitting starts at {C.SAM_FIT_MIN}. "
                        "Hand-set weights in use.", ""]
    res = compare(rows)
    lines += table(res) + [""]
    if "ll_fit" in res and res["ll_fit"] < res["ll_hand"] and res["ll_fit"] < res["ll_nv"]:
        cf = [c or 0.0 for c in res["full"]["coef"]]
        scale = sum(C.SAM_WEIGHTS.values()) / (sum(abs(c) for c in cf) or 1)
        w = {p: round(c * scale, 1) for p, c in zip(SS.PARTS, cf)}
        json.dump({"active": True, "weights": w, "n": n_eff, "ll": {k: res[k] for k in ("ll_nv", "ll_hand", "ll_fit")}},
                  open(os.path.join(L.HERE, "sam_weights.json"), "w"), indent=1)
        lines.append("Fitted weights beat both on held-out log loss: switched on (sam_weights.json): " + json.dumps(w))
    else:
        lines.append("Fitted weights did not beat both the hand-set score and FanDuel no-vig on held-out log loss: hand-set kept.")
    return lines + [""]


if __name__ == "__main__":
    print("\n".join(table(compare(dataset()))))
