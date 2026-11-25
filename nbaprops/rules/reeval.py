"""Re-evaluate every store-based record after each CNO paste, Unabated board run and watch scan, so anything that newly
qualifies is logged at its first sighting (that sighting's price and EV become the frozen entry):
Your rule, CNO screener (+2%), near misses, Unabated +EV & BP agrees, All +EV, Triple green and 2 of 3, from the day's CNO
screener store and Unabated EV store. Started games are skipped (nothing is written after a tip). The alt ladder needs the
live Unabated board, so it is re-scanned on every board run and watch scan (not on a CNO paste).
"""
from __future__ import annotations

import json

from nbaprops.rules import ledger as L


def _open_rows(rows):
    from nbaprops.rules import tipoff as T
    return [e for e in rows if not e.get("tip") or T.is_open(e["tip"], e.get("event") or ([e["team"]] if e.get("team") else None))]


def stores(date):
    """(open CNO store rows, open Unabated store rows)."""
    from nbaprops.app import value as V
    from nbaprops.rules import screener as S
    try:
        ua = list(json.load(open(V.ua_store_path(date))).values())
    except (OSError, ValueError):
        ua = []
    return _open_rows(list(S.store(date).values())), _open_rows(ua)


def run(date: str) -> dict:
    """-> {record: [rows newly logged]}."""
    if date < L.V2_START:
        return {}
    from nbaprops.app import value as V
    from nbaprops.rules import screener as S
    from nbaprops.rules import triple as TG
    cno, ua = stores(date)
    out = {}
    out["Your rule"] = S.log([e for e in cno if (e.get("ev") or 0) > 0 and e.get("proj_tag") in TG.AGREE], date, None, key=S.RULE_KEY)
    out["CNO screener"] = S.log([e for e in cno if (e.get("ev") or 0) >= S.MIN_EV], date, None)
    out["Near misses"] = S.log_near_misses(cno, date)
    uap = [e for e in ua if (e.get("ev") or 0) > 0 and (e.get("books") or 0) >= 3 and e.get("price") is not None]
    rows, seen = [], set()
    for e in uap:                                              # store rows -> the board shape log_ua_agree takes
        if (e["player"], e["market"]) not in seen:
            seen.add((e["player"], e["market"]))
            rows.append({"player": e["player"], "market": e["market"], "line": e["line"], "tip": e.get("tip"), "team": e.get("team"),
                         "event_id": "", "pulled_at": L.utc_now()})
    ua_map = {(e["player"], e["market"], e["side"]): {"ev_mult": e["ev"], "books": e["books"], "fair_src": "Unabated",
                                                      "fair_mult": e["fair"], "price_used": e["price"], "exchange": e.get("exchange")}
              for e in uap}
    out["Unabated +EV & BP agrees"] = V.log_ua_agree(date, rows, ua_map) if rows else []
    out["All +EV"] = V.log_all_ev(date, [dict(e, src="CNO screener") for e in cno if (e.get("ev") or 0) > 0]
                                  + [dict(e, odds=e["price"], src="Unabated") for e in uap])
    out["Triple green"] = TG.log(date, cno, ua)
    from nbaprops.rules import twothree as T23
    out["2 of 3"] = T23.log(date, cno, ua)
    return {k: v for k, v in out.items() if v}


def summary(res: dict) -> str:
    return "; ".join(f"{k} +{len(v)}" for k, v in res.items()) or "nothing new"
