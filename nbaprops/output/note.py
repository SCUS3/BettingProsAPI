"""The daily Markdown note (bp_top10_<date>.md): record, BETs, your-rule list, Unabated +EV & BP agrees, near misses,
and the per-category lists. Tables are padded and aligned by md_table."""
import json

from nbaprops.rules import ledger as bp_learn
from nbaprops.output.common import _calib, load_value, value_bets


CATEGORY_ORDER = [("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists"), ("3PM", "Threes"),
                  ("P+A", "Points + Assists"), ("P+R", "Points + Rebounds"), ("PRA", "Points + Rebounds + Assists"),
                  ("R+A", "Rebounds + Assists")]


def md_table(cols: list[tuple[str, str]], rows: list[list[str]]) -> list[str]:
    """Markdown table with padded, aligned columns. cols = [(header, "l"|"r"|"c")]; rows = lists of strings."""
    import unicodedata
    def w(s):                                       # display width (emoji dots are double width)
        return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)
    rows = [[c if c not in ("", None) else "–" for c in r] for r in rows]
    widths = [max([w(h), 3] + [w(r[i]) for r in rows]) for i, (h, _) in enumerate(cols)]
    def pad(s, i, a):
        gap = widths[i] - w(s)
        return s + " " * gap if a == "l" else " " * gap + s if a == "r" else " " * (gap // 2) + s + " " * (gap - gap // 2)
    out = ["| " + " | ".join(pad(h, i, a) for i, (h, a) in enumerate(cols)) + " |",
           "| " + " | ".join({"l": ":" + "-" * (widths[i] - 1), "r": "-" * (widths[i] - 1) + ":",
                              "c": ":" + "-" * (widths[i] - 2) + ":"}[a] for i, (_, a) in enumerate(cols)) + " |"]
    out += ["| " + " | ".join(pad(c, i, a) for i, (c, (_, a)) in enumerate(zip(r, cols))) + " |" for r in rows]
    return out


def write_top_by_market(rows: list[dict], date: str, n: int = 10, kelly_note: str = "") -> str:
    """Write the Markdown note: three overall top-10 lists, then top 10 by gap and by score in each category."""
    from nbaprops.app.props import _num, fmt, ranked, REAL_PROJ_MARKETS, DISABLED  # scoring helpers (imported here: props imports this module)
    out = f"bp_top10_{date}.md"
    def cell(v):
        return "" if v is None else fmt(v)
    from nbaprops.pricing import projctx as bp_projctx
    def bp_dot(r):                                  # BettingPros (calibrated for 3PM/P+R) vs. the listed side
        if r.get("line") is None or not r.get("rec_side"):
            return "–"
        t = bp_projctx.context(date, r["player"], r["market"], r["rec_side"], r["line"])["tag"]
        return "🟢" if t in ("agrees", "lean agrees") else "🔴" if t in ("disagrees", "lean disagrees") else "–"
    def odds_s(v):
        v = _num(v)
        return "" if v is None else f"{v:+.0f}"
    def side_s(s):
        return {"over": "Over", "under": "Under"}.get(s or "", s or "")
    def inj(r):
        return "Y" if (r.get("injury") or "").strip() else "N"
    def form_s(r):
        return "" if r.get("form_rate") is None else f"{round(r['form_rate'] * 100)}%"
    def proj_s(r):
        p = _num(r.get("bp_proj"))
        if p is None:
            return ""
        c = _calib(r.get("market"), p)
        return (f"{p:.1f}→{c:.1f}" if c is not None and c != p else f"{p:.1f}".rstrip("0").rstrip(".") if p == int(p) else f"{p:.1f}") \
            + ("*" if r.get("placeholder") else "")
    def dvp_s(r, side=None):                        # side-aware: 🟢 matchup helps the listed side, 🔴 hurts, 11-20 plain
        return bp_projctx.dvp_dot(side or r.get("rec_side"), r.get("dvp_softness"))
    fd = [r for r in rows if r["price_is_fanduel"] and r.get("score") is not None and not r.get("noproj")]
    def table(group, bold):
        ranked_col = {"gap": "Gap %", "score": "Score", "balanced": "Bal"}[bold]
        cols = [("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"), ("Proj", "r"),
                ("Gap %", "r"), ("FD", "r"), ("Mkt EV", "r"), ("DvP", "r"), ("Form", "r"), ("Score", "r"), ("Bal", "r"),
                ("Sig", "r"), ("Inj", "c")]
        cols = [(h + " ▼" if h == ranked_col else h, a) for h, a in cols]
        body = []
        for i, r in enumerate(group[:n], 1):
            mev = "" if r.get("oa_ev") in (None, "") else f"{100 * float(r['oa_ev']):+.1f}%"   # market EV; model lists carry no stake
            body.append([str(i), cell(r["player"]), cell(r["market"]), side_s(r["rec_side"]), cell(r["line"]), bp_dot(r),
                         proj_s(r), cell(r["pct_gap"]), odds_s(r["fd_odds_rec"]), mev, dvp_s(r), form_s(r),
                         cell(r["score"]), cell(r["balanced"]), cell(r.get("signals")), inj(r)])
        return md_table(cols, body) + [""]
    lean_cols = [("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"), ("BP proj", "r"),
                 ("FD", "r"), ("Mkt EV", "r"), ("DvP", "r"), ("Form", "r"), ("Mkt value", "r"), ("Bal ▼", "r"), ("Inj", "c")]
    def lean_row_cells(i, r):
        mev = "" if r.get("oa_ev") in (None, "") else f"{100 * float(r['oa_ev']):+.1f}%"
        return [str(i), cell(r["player"]), cell(r["market"]), side_s(r["rec_side"]), cell(r["line"]), bp_dot(r),
                proj_s(r) or "–", odds_s(r["fd_odds_rec"]), mev, dvp_s(r), form_s(r), cell(r.get("s_market")),
                cell(r["balanced"]), inj(r)]

    lines = [f"# NBA props: {date}", "",
             "FanDuel-priced props only. Only Tonight's bets (the first list) are bets; every other list is a paper record (no money). DvP: 1 = softest matchup. Form: recent hit rate of the "
             "recommended side vs. this line. Score: your rules (40% edge, 35% FanDuel EV, 25% matchup). "
             "Balanced: 40% Score + 25% form + 20% market value (no-vig price, line vs. consensus) + "
             "15% BettingPros bet rating. Signals: how many of six factors lean the same way. "
             "Kelly: quarter-Kelly stake, 1u = 1% of bankroll. All scores are cut for injury status.", "",
             "Columns: BP 🟢 = BettingPros' projection or lean is on the listed side (an under projected below the line is 🟢), "
             "🔴 = the other side, – = no projection. Proj = BettingPros' raw projection. DvP 🟢 = the matchup helps the listed side "
             "(soft 1-10 for an over, hard 21-30 for an under), 🔴 = it hurts, no dot = 11-20. Mkt EV = market EV at FanDuel's price. Mkt value = "
             "no-vig price and line vs. consensus (0-100). Bal = Balanced score. Sig = signals agreeing (of 6). Inj = on the "
             "injury report (Y/N). ▼ = the column the list is ranked by.", "",
             "**Rules v2 (from 2026-11-24):** your rule = CrazyNinja +EV and BettingPros agrees (section 2); BET = Unabated "
             "EV +2% and CrazyNinja worst case > 0. Tonight's bets = BETs + alt BETs + your rule, "
             "each prop once at its largest ¼K stake, capped 1.5u/player and 3.5u/game across the list; the other records' "
             "stakes are paper.", ""]
    from nbaprops.rules import tonight as _tn
    tb = _tn.payload(date)
    rec = bp_learn.record()
    _tr = next((x for x in rec["strategies"] if x.get("key") == "tonight"), None)
    lines += ["## Tonight's bets", "",
              "The only list to bet. The union of BETs (Unabated EV +2% and CrazyNinja worst case > 0), alt BETs and Your-rule "
              "props; each prop once at its largest qualifying ¼K stake (not summed), then capped at 1.5u per player and 3.5u per "
              "game across this whole list. Records = every record the prop qualifies for. Everything else in this note is a "
              "paper record.", ""]
    if _tr and _tr["w"] + _tr["l"] + _tr["p"]:
        lines += [f"Tonight's bets record (v2): {_tr['w']}-{_tr['l']}-{_tr['p']} | ¼K at the capped stakes {_tr['kelly_units']:+.2f}u"
                  + (f" ({_tr['kelly_roi']:+}% ROI)" if _tr.get("kelly_roi") is not None else "") + f" | flat 1u {_tr['units']:+.2f}u"
                  + (f" | CLV {_tr['clv']:+}% EV ({_tr['clv_n']})" if _tr.get("clv") is not None else ""), ""]
    if tb:
        def _bp(x):
            t = x.get("bp_tag") or "none"
            return "🟢" if t in ("agrees", "lean agrees") else "🔴" if t in ("disagrees", "lean disagrees") else "–"
        body = [[str(i), x["player"], x["market"], side_s(x["side"]), fmt(x["line"]), _bp(x),
                 "" if x.get("bp_proj") is None else f"{x['bp_proj']:.1f}", "" if x.get("bp_gap") is None else f"{x['bp_gap']:+.1f}",
                 odds_s(x["price"]), "" if x["ev"] is None else f"{100 * x['ev']:+.1f}%",
                 bp_projctx.dvp_dot(x["side"], x.get("dvp")), f"{x['stake']:.2f}u",
                 " + ".join(x["records"]) + (f" ({x['flags']})" if x["flags"] else ""),
                 (x["cap"] or "").replace("capped: ", "capped ") + (f" from {x['stake_raw']:.2f}u" if x["cap"] else ""), x["status"]]
                for i, x in enumerate(tb, 1)]
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"), ("BP proj", "r"),
                           ("Gap", "r"), ("FD", "r"), ("Entry EV", "r"), ("DvP", "r"), ("Stake ▼", "r"), ("Records", "l"), ("Cap", "l"), ("Game", "l")], body)
        lines += ["", f"Total {sum(x['stake'] for x in tb):.2f}u on {len(tb)} props.", ""]
    else:
        lines += ["_No bets yet tonight._", ""]
    lines += ["## Record", ""]
    played = [x for x in rec["strategies"] + [rec["combined"]] if x["w"] + x["l"] + x["p"]]
    if played:
        lines += [f"{rec['slates']} graded slates, pre-tip picks only, flat 1u.", "",
                  "| Strategy | W-L-P | Hit | Units | Kelly units | Brier ours / mkt | CLV |", "|---|---|---|---|---|---|---|"]
        for x in played:
            br = f"{x['brier']} / {x['brier_mkt']}" if x["brier"] is not None else ""
            clv = f"{x['clv']:+} pts" if x["clv"] is not None else ""
            lines.append(f"| {x['label']} | {x['w']}-{x['l']}-{x['p']} | {x['hit'] if x['hit'] is not None else ''}% | "
                         f"{x['units']:+.2f} | {x['kelly_units']:+.2f} | {br} | {clv} |")
        lines.append("")
    else:
        lines += ["No graded picks yet.", ""]
    bets = value_bets(date)
    watch = [r for r in rows if r.get("decision") == "watch"]
    if bets or watch:
        lines += ["## BET", "", "Every FanDuel line priced against the market (Unabated consensus of devigged books and exchanges; "
                  "The Odds API only for shortlist plays Unabated can't price). BET = market EV +2% or more at FanDuel's line "
                  "with 3+ sources (Unabated) or 4+ books (The Odds API). Stake = quarter-Kelly (¼K) on the fair probability. "
                  + ((load_value(date).get("_meta") or {}).get("bet_cap_note") or "") + " "
                  "Tag: '+EV + proj agrees' when BettingPros' projection is on the same side, else '+EV only'.", ""]
        lines += [f"- BET [{b['tag']}] {b['player']} {b['market']} {b['side']} {fmt(b['line'])} {fmt(b['price'])}: market EV "
                  f"{100 * b['ev']:+.1f}%, fair {b['fair']} ({b['src']}, {b['n']} sources{', exchange' if b['exchange'] else ''}); "
                  f"form {'-' if b['form'] is None else str(round(100 * b['form'])) + '%'}, DvP {'-' if b['dvp'] is None else fmt(b['dvp'])}; "
                  f"stake {fmt(b['stake'])}u ¼K" + (f"; 6:30 CLV {100 * b['close']['clv_ev']:+.1f}%, market EV now "
                  f"{100 * b['close']['mkt_ev']:+.1f}%" + (" FLAG" if b['close']['mkt_ev'] < 0 else "")
                  if b.get("close") and b["close"].get("mkt_ev") is not None else "") for b in bets] or ["- No BET plays today."]
        lines += ["", "Watch (not bets): " + "; ".join(
            f"{r['player']} {r['market']} {r['rec_side']} {fmt(r['line'])}" + (f" (mkt EV {100 * r['oa_ev']:+.1f}%)" if r.get("oa_ev") is not None else "")
            for r in watch), ""]
    lines += ["Placeholder projections (Proj*) claim no edge, use FanDuel's no-vig price for EV, and rank below every "
              "real projection unless the price alone is +EV.", ""]
    lines += ["## 1. Top 10 by projection gap", "", "Biggest |projection - line| as a % of the line, nothing else.", ""]
    lines += table(ranked(fd, lambda r: abs(r["pct_gap"] or 0)), "gap")
    from nbaprops.rules import screener as bp_cno_screener
    mine = bp_cno_screener.rule_rows(date, include_started=True)
    from nbaprops.rules import tipoff as _T
    status = lambda tip, names=None: "open" if not tip or _T.is_open(tip, names) else "started"
    _led = bp_learn._read(bp_learn.LEDGER)
    stake_of = {(x["player"], x["market"], x["side"], float(x["line"])): f"{float(x['kelly_u']):g}u" for x in _led
                if x["date"] == date and x["strategy"] == "my_rule" and x.get("kelly_u") not in (None, "")}
    _rr = [x for x in rec["strategies"] if x.get("key") == "my_rule"]
    lines += ["## 2. Top 10 by your score", "",
              "Your rule: on a CrazyNinja screener paste with any +EV (FanDuel, NBA player props, 4+ books, +200 or shorter, "
              "main line) AND BettingPros' projection or lean agrees with the side. Built from today's pastes; EV and price "
              "are from the latest paste that listed the prop. Ranked by CrazyNinja EV. ¼K = the logged stake.", ""]
    if _rr and _rr[0]["w"] + _rr[0]["l"] + _rr[0]["p"]:
        x = _rr[0]
        lines += [f"Your rule record: {x['w']}-{x['l']}-{x['p']} | flat 1u {x['units']:+.2f}u | ¼K {x['kelly_units']:+.2f}u"
                  + (f" | CLV {x['clv']:+}% EV" if x.get("clv") is not None else ""), ""]
    if mine:
        by = {(r["player"], r["market"]): r for r in rows}
        body = []
        for i, e in enumerate(mine[:n], 1):
            r = by.get((e["player"], e["market"])) or {}
            pr = _num(e.get("proj_raw"))
            gap = "" if pr is None else f"{pr - float(e['line']):+.1f}"
            seen_et = bp_projctx._et(e.get("first_seen", "")).replace(" ET", "") if e.get("first_seen") else ""
            body.append([str(i), e["player"], e["market"], side_s(e["side"]), f"{float(e['line']):g}", "🟢",
                         "" if pr is None else f"{pr:.1f}", gap, odds_s(e["odds"]), f"{100 * e['ev']:+.2f}%",
                         str(e.get("books") or ""), dvp_s(r, e["side"]) if r else "", str(e.get("seen", 1)), seen_et,
                         stake_of.get((e["player"], e["market"], e["side"], float(e["line"])), ""), status(e.get("tip"), e.get("event"))])
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"),
                           ("BP proj", "r"), ("Gap", "r"), ("FD", "r"), ("CNO EV ▼", "r"), ("Books", "r"), ("DvP", "r"),
                           ("Pastes", "r"), ("First seen", "r"), ("¼K", "r"), ("Game", "l")], body) + [""]
    else:
        lines += ["_No props yet: paste the CrazyNinja screener during the day and this list builds up._", ""]
    from nbaprops.sources.rotowire import norm as _norm
    by_pm = {(_norm(r["player"]), r["market"]): r for r in rows}
    ua3 = []
    try:
        from nbaprops.app import value as _V
        for e in json.load(open(_V.ua_store_path(date))).values():
            if (e.get("ev") or 0) <= 0 or (e.get("books") or 0) < 3:
                continue
            c = bp_projctx.context(date, e["player"], e["market"], e["side"], e["line"])
            if c["tag"] in ("agrees", "lean agrees"):
                ua3.append(dict(e, proj_raw=c.get("proj")))
    except (OSError, ValueError):
        pass
    ua3.sort(key=lambda e: -e["ev"])
    upd = max((e.get("at", "") for e in ua3), default="")
    lines += ["## 3. Unabated +EV & BettingPros agrees", "",
              "Every FanDuel line Unabated prices (median of devigged books and exchanges at FanDuel's line, 3+ sources) with market "
              "EV above 0, where BettingPros' projection or lean is on the same side. Ranked by market EV."
              + (f" Prices as of {bp_projctx._et(upd)}." if upd else ""), ""]
    if ua3:
        body = []
        for i, e in enumerate(ua3[:n], 1):
            r = by_pm.get((_norm(e["player"]), e["market"])) or {}
            pr = _num(e.get("proj_raw"))
            body.append([str(i), e["player"], e["market"], side_s(e["side"]), f"{float(e['line']):g}", "🟢",
                         "" if pr is None else f"{pr:.1f}", "" if pr is None else f"{pr - float(e['line']):+.1f}",
                         odds_s(e.get("price")), f"{100 * e['ev']:+.2f}%", str(e.get("books") or ""), dvp_s(r, e["side"]) if r else "",
                         inj(r) if r else "", status(e.get("tip"), e.get("team"))])
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"), ("BP proj", "r"),
                           ("Gap", "r"), ("FD", "r"), ("Mkt EV ▼", "r"), ("Sources", "r"), ("DvP", "r"), ("Inj", "c"), ("Game", "l")], body) + [""]
    else:
        lines += ["_None saved for this date (this record starts with the 11/24 slate; on 11/23 a scan after the last tip wiped the file, since fixed)._", ""]
    miss = bp_cno_screener.near_miss_rows(date)
    lines += ["## 4. Near misses to your rule", "",
              "From today's CrazyNinja pastes: CNO EV between -1% and 0% with BettingPros agreeing (EV just short), or CNO EV "
              "above 0 with BettingPros disagreeing or no BettingPros projection. Logged once as research (flat 1u, no stake) "
              "next to your rule, so the record shows whether skipping them was right.", ""]
    if miss:
        body = []
        for i, e in enumerate(miss[:n], 1):
            r = by_pm.get((_norm(e["player"]), e["market"])) or {}
            pr = _num(e.get("proj_raw"))
            dot = "🟢" if e.get("proj_tag") in ("agrees", "lean agrees") else "🔴" if e.get("proj_tag") in ("disagrees", "lean disagrees") else "–"
            body.append([str(i), e["player"], e["market"], side_s(e["side"]), f"{float(e['line']):g}", dot,
                         "" if pr is None else f"{pr:.1f}", odds_s(e.get("odds")), f"{100 * e['ev']:+.2f}%", e["why"],
                         str(e.get("books") or ""), dvp_s(r, e["side"]) if r else "", status(e.get("tip"), e.get("event"))])
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("BP", "c"), ("BP proj", "r"),
                           ("FD", "r"), ("CNO EV", "r"), ("Why it missed", "l"), ("Books", "r"), ("DvP", "r"), ("Game", "l")], body) + [""]
    else:
        lines += ["_None right now._", ""]
    allev = [r for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date and r["strategy"] == "all_ev"]
    allev.sort(key=lambda r: -(_num(r.get("entry_ev")) or 0))
    lines += ["## 5. All +EV tonight (any source)", "",
              "Every +EV prop from Unabated, CrazyNinja pastes and the alt ladder, once each at its first sighting (entry price and "
              "EV frozen; later sources noted). Logged as the 'All +EV' research record.", ""]
    if allev:
        body = [[str(i), r["player"], r["market"], side_s(r["side"]), f"{float(r['line']):g}", odds_s(r.get("entry_price") or r["odds"]),
                 f"{100 * (_num(r.get('entry_ev')) or 0):+.2f}%", r.get("fair_src") or "", (r.get("cap_note") or "").replace("also ", ""),
                 bp_dot({"player": r["player"], "market": r["market"], "rec_side": r["side"], "line": _num(r["line"])}),
                 status(r.get("tip"), r.get("team"))] for i, r in enumerate(allev[:25], 1)]
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("FD", "r"), ("Entry EV ▼", "r"),
                           ("First source", "l"), ("Also", "l"), ("BP", "c"), ("Game", "l")], body)
        lines += ([f"_{len(allev) - 25} more in the ledger (strategy all_ev)._"] if len(allev) > 25 else []) + [""]
    else:
        lines += ["_None logged for this date yet (the log starts with the 11/24 slate)._", ""]
    try:
        from nbaprops.pricing import alts as _alts
        cand = sorted(json.load(open(_alts.candidates_path(date))).values(), key=lambda e: -e["ev"])
    except (OSError, ValueError):
        cand = []
    lines += ["## 6. Ladder candidates", "",
              "Alt rungs that came close to the alt-ladder rules: market EV +2% or more with 3+ two-sided sources and FanDuel "
              "+300 or shorter, but under the +4% bar, or CrazyNinja's worst case at or below 0, or (3PM/P+R) BettingPros "
              "disagreeing at the rung. Display only.", ""]
    if cand:
        body = [[str(i), e["player"], e["market"], side_s(e["side"]), f"{float(e['line']):g}", "" if e.get("main") is None else f"{e['main']:g}",
                 odds_s(e.get("odds")), f"{100 * e['ev']:+.2f}%", str(e.get("books") or ""), e.get("why", ""), status(e.get("tip"), e.get("event"))]
                for i, e in enumerate(cand[:n], 1)]
        lines += md_table([("#", "r"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Rung", "r"), ("Main", "r"), ("FD", "r"),
                           ("Mkt EV ▼", "r"), ("Sources", "r"), ("Why not", "l"), ("Game", "l")], body) + [""]
    else:
        lines += ["_None saved for this date yet (saved from each board run and watch scan from 11/24)._", ""]
    from nbaprops.rules import reddit_picks as _rp
    _proven = _rp.proven_posters()
    _today = [r for r in bp_learn._read(bp_learn.LEDGER) if r["date"] == date and r["strategy"] == "reddit_pick"
              and r.get("fair_src") in _proven]
    lines += ["## 7. Proven posters today", "",
              f"Reddit (r/sportsbook) picks tonight from posters with {_rp.PROVEN_MIN}+ graded picks, positive average CLV and "
              "positive units (re-checked every run). FanDuel price and market EV at the time of the post; '+EV' when the market "
              "agrees, which also logs the staked 'Proven poster + market +EV' record.", ""]
    if _today:
        body = []
        for i, r in enumerate(sorted(_today, key=lambda r: -(_num(r.get("entry_ev")) or -9)), 1):
            rr = by_pm.get((_norm(r["player"]), r["market"])) or {}
            ev = _num(r.get("entry_ev"))
            body.append([str(i), (r.get("fair_src") or "").replace("Reddit ", ""), r["player"], r["market"], side_s(r["side"]),
                         f"{float(r['line']):g}", odds_s(r.get("entry_price")), "" if ev is None else f"{100 * ev:+.2f}%",
                         "+EV" if ev is not None and ev > 0 else "", bp_dot({"player": r["player"], "market": r["market"],
                                                                            "rec_side": r["side"], "line": _num(r["line"])}),
                         dvp_s(rr, r["side"]) if rr else "", status(r.get("tip"), r.get("team"))])
        lines += md_table([("#", "r"), ("Poster", "l"), ("Player", "l"), ("Mkt", "l"), ("Side", "l"), ("Line", "r"), ("FD", "r"),
                           ("Mkt EV", "r"), ("", "l"), ("BP", "c"), ("DvP", "r"), ("Game", "l")], body) + [""]
    else:
        lines += [f"_No proven posters yet ({len(_proven)} qualify), or none posted tonight. Picks are logged per poster with "
                  "`bp_value.py --reddit`; posters qualify after 30 graded picks._", ""]
    diffs = [r for r in fd if r.get("line_note")]
    if diffs:
        lines += ["## FanDuel line differs from BettingPros' line", "",
                  "Gap, side, EV and Kelly use FanDuel's line; BettingPros' win chance and rating are dropped for these.", ""]
        lines += [f"- {r['player']} {r['market']} {r['rec_side']}: {r['line_note']}" for r in ranked(diffs, lambda r: r["balanced"])[:15]]
        lines.append("")
    lines += ["---", "", "# Each category", "", "3PM and P+R: top 10 by projection gap. PTS, REB, AST, P+A "
              "and PRA: BettingPros publishes whole-number side leans, so those are ranked on form, matchup and market value.", ""]
    for tag, name in CATEGORY_ORDER:
        if tag in DISABLED:
            continue
        lines += [f"## {name} ({tag})", ""]
        if tag not in REAL_PROJ_MARKETS:
            lean = [r for r in rows if r["price_is_fanduel"] and r["market"] == tag and r.get("lean_only") is True
                    and r.get("balanced") is not None]
            if not lean:
                lines += ["_No FanDuel-priced props found._", ""]
                continue
            lines += [f"BettingPros publishes a whole-number side lean for {tag}, not a projection: ranked on form, matchup "
                      f"and market value. No edge, projection EV or Kelly; value here comes from the market check.", ""]
            lines += md_table(lean_cols, [lean_row_cells(i, r) for i, r in enumerate(ranked(lean, lambda r: r["balanced"])[:n], 1)])
            lines.append("")
            continue
        group = [r for r in fd if r["market"] == tag and r["pct_gap"] is not None]
        if not group:
            lines += ["_No FanDuel-priced props found._", ""]
            continue
        lines += [f"### {tag}: by projection gap", ""]
        lines += table(ranked(group, lambda r: abs(r["pct_gap"])), "gap")

    with open(out, "w") as f:
        f.write("\n".join(lines))
    return out
