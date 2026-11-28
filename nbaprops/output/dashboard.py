"""The ScuseBET board (bp_dashboard.html): fills dashboard_template.html with the day's rows, BETs and records."""
import datetime as dt
import json
import os

from nbaprops.config import ROOT as _ROOT
from nbaprops.rules import ledger as bp_learn
from nbaprops.output.common import _calib, load_value, value_bets


DASH_TEMPLATE = os.path.join(_ROOT, "dashboard_template.html")


DASH_OUT = os.path.join(_ROOT, "bp_dashboard.html")


def write_dashboard(rows, date, kelly_note=""):
    """Fill dashboard_template.html with tonight's FanDuel-priced props -> bp_dashboard.html."""
    from nbaprops.app.props import _num, REAL_PROJ_MARKETS, DISABLED, WANTED  # scoring helpers (imported here: props imports this module)
    from nbaprops.rules import screener as _scr
    rule_of = {(e["player"], e["market"], e["side"], float(e["line"])): f"CNO EV {100 * e['ev']:+.2f}%" for e in _scr.rule_rows(date)}
    try:
        with open(DASH_TEMPLATE) as f:
            tpl = f.read()
    except OSError:
        return None
    def n(v):
        return _num(v)
    data = []
    for r in rows:
        if not r["price_is_fanduel"] or r.get("balanced") is None:
            continue
        data.append({
            "player": r["player"], "team": r["team"], "inj": r["injury"] or None, "market": r["market"],
            "side": r["rec_side"], "line": n(r["line"]), "proj": n(r["bp_proj"]), "gap": n(r["pct_gap"]),
            "proj_cal": n(_calib(r.get("market"), r.get("bp_proj"))),
            "odds": n(r["fd_odds_rec"]), "dvp": n(r["dvp_softness"]), "form": n(r.get("form_rate")),
            "score": r["score"], "bal": r.get("balanced"), "kelly": n(r.get("kelly_u")), "sig": r.get("signals"),
            "s_edge": r.get("s_edge"), "s_ev": r.get("s_ev"), "s_dvp": r.get("s_dvp"), "s_form": r.get("s_form"),
            "s_market": r.get("s_market"), "nv": n(r.get("nv_edge")), "cons": n(r.get("consensus_line")),
            "streak": r.get("streak") or None, "l10": r.get("form_l10") or None, "season": r.get("form_season") or None,
            "learned": r.get("learned"), "tm_out": r.get("tm_out"), "tm_dtd": r.get("tm_dtd"),
            "tm_out_names": r.get("tm_out_names") or None, "ph": bool(r.get("placeholder")), "demoted": bool(r.get("ph_demoted")),
            "starter": r.get("starter") in (True, "True"), "rw": r.get("rw_status") or None,
            "noproj": bool(r.get("noproj")), "bp_line": n(r.get("bp_line")), "note": r.get("line_note") or None,
            "lean": r.get("bp_lean") if r.get("lean_only") in (True, "True") else None,
            "pick": r.get("model_pick") or None,
            "rule": rule_of.get((r.get("player"), r.get("market"), r.get("rec_side"), _num(r.get("line")))),
            "decision": r.get("decision") or None, "bet_kelly": n(r.get("bet_kelly")), "bet_cap": r.get("bet_cap_note") or None,
            "bet_side": r.get("bet_side") or None, "bet_tag": r.get("bet_tag") or None, "bet_ev": n(r.get("bet_ev")),
            "close_mkt_ev": n(r.get("close_mkt_ev")), "close_clv": n(r.get("close_clv")),
            "fair_src": r.get("fair_src") or None,
            "ev": n(r.get("model_ev")), "oa_ev": n(r.get("oa_ev")), "oa_fair": n(r.get("oa_fair")), "oa_books": r.get("oa_books"),
        })
    d = dt.date.fromisoformat(date)
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
        updated = now.strftime("%b %-d, %-I:%M %p ET")
    except Exception:
        updated = dt.datetime.now().strftime("%b %d, %I:%M %p")
    payload = {"date": date, "date_label": d.strftime("%A, %B %-d"), "updated": updated, "rows": data,
               "disabled": sorted(DISABLED), "kelly_note": kelly_note, "bets": value_bets(date),
               "lean_markets": sorted(set(WANTED.values()) - REAL_PROJ_MARKETS - DISABLED),
               "bet_cap_note": (load_value(date).get("_meta") or {}).get("bet_cap_note"),
               "tonight_bets": _tonight(date), "today_lists": _today_lists(date), "sam_weights": _sam_weights(), "live_pastes": _live_pastes(), "learns": _learns(), "record": bp_learn.record("v2"), "record_v1": bp_learn.record("v1"),
               "situations": _situations("v2"), "situations_v1": _situations("v1")}
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    with open(DASH_OUT, "w") as f:
        f.write(tpl.replace("__BP_DATA__", blob))
    return DASH_OUT


def _learns():
    """Daily learns: per day, the Sam score weight changes, sample, that night's bets and rule notes."""
    try:
        from nbaprops.rules import learns as LR
        return [dict(d, text=LR.lines(d)) for d in LR.daily()]
    except Exception:  # noqa: BLE001
        return []


TODAY_LISTS = [("triple", "Triple green", {"triple"}), ("two3", "2 of 3", {"two_of_three"}), ("rule", "Your rule", {"my_rule"}),
               ("ua", "Unabated + Proj Model", {"ua_agree"}), ("bets", "BETs", {"bet_agree", "bet_only"}), ("scr", "CNO screener", {"cno_scr"}),
               ("allev", "All +EV", {"all_ev"}), ("near", "Near misses", {"near_miss"})]


def _today_lists(date):
    """Tonight's records as lists for the Tonight tab: each record's props for `date` (EV high to low), plus live +EV and
    10%+ EV. BP and DvP dots are side-aware (projctx)."""
    try:
        from nbaprops.pricing import projctx as P
        from nbaprops.rules import livepastes as LP
    except Exception:  # noqa: BLE001
        return []

    def dots(r):
        try:
            t = P.context(date, r["player"], r["market"], r["side"], float(r["line"]))
            bp = "g" if t["tag"] in ("agrees", "lean agrees") else "r" if t["tag"] in ("disagrees", "lean disagrees") else ""
            d = P.dvp_of(date, r["player"], r["market"])
            e = P.dvp_effect(r["side"], d)
            return {"bp": bp, "bp_proj": t.get("proj"), "dvp": d, "dvp_dot": "g" if e == "helps" else "r" if e == "hurts" else ""}
        except Exception:  # noqa: BLE001
            return {"bp": "", "bp_proj": None, "dvp": None, "dvp_dot": ""}
    ledger = [r for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date]
    out = []
    for key, label, strats in TODAY_LISTS:
        rows, seen = [], set()
        for r in sorted([r for r in ledger if r["strategy"] in strats], key=lambda r: -(bp_learn._num(r.get("entry_ev") or r.get("oa_ev")) or 0)):
            k = (r["player"], r["market"], r["side"], r["line"])
            if k in seen:
                continue
            seen.add(k)
            rows.append(dict(dots(r), player=r["player"], market=r["market"], side=r["side"], line=bp_learn._num(r["line"]),
                             price=bp_learn._num(r.get("entry_price") or r.get("odds")), ev=bp_learn._num(r.get("entry_ev") or r.get("oa_ev")),
                             books=r.get("books") or r.get("sources") or "", stake=bp_learn._num(r.get("kelly_u")),
                             score=bp_learn._num(r.get("score_entry")), tier=r.get("score_tier") or "", why=r.get("cap_note") or "",
                             result=r.get("result") or ""))
        out.append({"key": key, "label": label, "rows": rows})
    hot = [x for l in out if l["key"] != "near" for x in l["rows"] if (x["ev"] or 0) >= 0.10]
    live = {}
    for r in sorted(LP.read(date), key=lambda r: r["at"]):
        if (bp_learn._num(r.get("ev")) or 0) > 0:
            live[(r["player"], r["market"], r["side"], r["line"])] = r
    lrows = [dict(dots(r), player=r["player"], market=r["market"], side=r["side"], line=bp_learn._num(r["line"]), price=bp_learn._num(r.get("odds")),
                  ev=(bp_learn._num(r.get("ev")) or 0) / 100, books=r.get("books") or "", live_now=r.get("live_now"), clock=r.get("clock") or "",
                  result=r.get("result") or "") for r in live.values()]
    lrows.sort(key=lambda x: -(x["ev"] or 0))
    seen10, hot10 = set(), []
    for x in sorted(hot + [dict(x, live=True) for x in lrows if x["ev"] >= 0.10], key=lambda x: -(x["ev"] or 0)):
        k = (x["player"], x["market"], x["side"], x["line"])
        if k not in seen10:
            seen10.add(k)
            hot10.append(x)
    out.insert(0, {"key": "live", "label": "Live +EV", "rows": lrows, "live": True})
    out.append({"key": "ev10", "label": "10%+ EV", "rows": hot10})
    return out


def _live_pastes():
    """Every live screener row you pasted (graded, with CLV vs. your next paste), for the Record tab."""
    try:
        from nbaprops.rules import livepastes as LP
        from nbaprops.pricing import projctx as P
        s = LP.summary()
        for r in s.get("rows", []):                  # side-aware dots: BP projection vs. the line, DvP for the side
            try:
                t = P.context(r["date"], r["player"], r["market"], r["side"], float(r["line"]))["tag"]
                r["bp_dot"] = "🟢" if t in ("agrees", "lean agrees") else "🔴" if t in ("disagrees", "lean disagrees") else ""
                r["dvp_dot"] = P.dvp_dot(r["side"], P.dvp_of(r["date"], r["player"], r["market"]))
            except Exception:  # noqa: BLE001
                r["bp_dot"], r["dvp_dot"] = "", ""
        return s
    except Exception:  # noqa: BLE001
        return {"rows": [], "n": 0}


def _sam_weights():
    """Current Sam score weights (adapted nightly) vs. the hand-set start, for the dashboard's ⓘ explanations."""
    import json as _json
    from nbaprops import config as C
    from nbaprops.pricing import samscore as SS
    out = {"now": SS.active_weights(), "hand": C.SAM_WEIGHTS, "penalties": C.SAM_PENALTIES, "n": None, "at": None}
    try:
        j = _json.load(open(os.path.join(_ROOT, "sam_weights.json")))
        out.update(n=j.get("n"), at=(j.get("at") or "")[:10])
    except (OSError, ValueError):
        pass
    return out


def _tonight(date):
    """Tonight's bets (union, largest stake, global caps); empty on any error so the page still builds."""
    try:
        from nbaprops.rules import tonight
        return tonight.payload(date)
    except Exception:  # noqa: BLE001
        return []


def _situations(version):
    """Situation finder output for the Record tab (empty on any error, so the page still builds)."""
    try:
        from nbaprops.rules import situations
        return situations.find(version)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "records": [], "candidates": []}

