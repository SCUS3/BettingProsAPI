"""Shared output helpers: the day's market-check value file, its BET list, and the display projection."""
import json
import os

from nbaprops.config import ROOT as _ROOT


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def load_value(date):
    try:
        with open(os.path.join(_ROOT, f"value_{date}.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def value_bets(date):
    """BETs from the day's value check (bp_value.py --board), best market EV first."""
    out = []
    for k, e in load_value(date).items():
        if k.startswith("_") or e.get("decision") != "BET" or "sides" not in e:
            continue
        player, market, line = k.split("|")
        v = e["sides"].get(e["bet_side"]) or {}
        out.append({"player": player, "market": market, "side": e["bet_side"], "line": float(line), "price": e.get("bet_price"),
                    "ev": e.get("bet_ev"), "fair": v.get("fair_mult"), "src": v.get("fair_src"), "n": v.get("books"),
                    "exchange": bool(v.get("exchange")), "tag": e.get("tag"), "form": e.get("form"), "dvp": e.get("dvp"),
                    "stake": e.get("bet_kelly"), "cap": e.get("cap_note") or None, "close": e.get("close")})
    return sorted(out, key=lambda b: -(b["ev"] or 0))


def _calib(market, proj):
    from nbaprops.pricing import calib as bp_calib
    p = _num(proj)
    return (bp_calib.calibrate(market, p) if p is not None and market in bp_calib.MARKETS and bp_calib.USE_CALIBRATION
            else None)
