"""The NBA Prop Board (bp_dashboard.html): fills dashboard_template.html with the day's rows, BETs and records."""
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
               "record": bp_learn.record("v2"), "record_v1": bp_learn.record("v1")}
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    with open(DASH_OUT, "w") as f:
        f.write(tpl.replace("__BP_DATA__", blob))
    return DASH_OUT
