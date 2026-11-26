"""ScuseBET public site (GitHub Pages): site/index.html, site/records.html, site/v1.html, site/robots.txt.

    python3 bp_site.py [--date YYYY-MM-DD] [--publish]

Public view only: tonight's bets (Sam score "51 (C)", EV, a "playable to" price, game status), yesterday's results with
running totals, every record with sample sizes, a plain glossary and "how it works". Left out on purpose: raw BettingPros
projections, Unabated / CrazyNinja odds tables and anything from Copy for Claude. No affiliate links, no tracking,
robots noindex until the owner flips it. Every build is scanned for secrets (keys, .env values, Drive IDs, tokens) and fails
if any are found. Publishing (push to the gh-pages branch) runs only when config.SITE_PUBLISH is True.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import subprocess
import sys

from nbaprops import config as C
from nbaprops.rules import ledger as L

ROOT = L.HERE
SITE = os.path.join(ROOT, "site")
e = html.escape


# ------------------------------------------------------------------ data
def _dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def american(dec):
    """Decimal odds -> American, rounded the way books quote it."""
    if dec >= 2:
        return f"+{round((dec - 1) * 100)}"
    return f"{round(-100 / (dec - 1))}"


def playable_to(fair, margin=None):
    """Worst FanDuel price that is still +margin EV at the fair probability, as (decimal, American)."""
    m = C.SITE_PLAYABLE_MARGIN if margin is None else margin
    if not fair or not 0 < fair < 1:
        return None, None
    d = (1 + m) / fair
    return d, american(d)


ESPN = {"GSW": "GS", "PHO": "PHX", "NYK": "NY", "SAS": "SA", "NOP": "NO", "WAS": "WSH", "UTA": "UTAH", "BRK": "BKN"}


def statuses(date):
    """{team abbr (ESPN): 'upcoming' | 'started' | 'final'} from ESPN's scoreboard (free)."""
    out = {}
    try:
        from nbaprops.rules import halftime as H
        for g in H.games(date):
            st = "final" if g["status"] == "STATUS_FINAL" else "upcoming" if g["status"] == "STATUS_SCHEDULED" else "started"
            for t in g["score"]:
                out[t] = st
    except Exception:  # noqa: BLE001
        pass
    return out


def game_status(x, st, now):
    team = ESPN.get(x.get("team") or "", x.get("team") or "")
    if team in st:
        return st[team]
    tip = x.get("tip")
    if tip:
        from nbaprops.rules import tipoff as T
        t = T._t(tip)
        return "upcoming" if now < t else "final" if now > t + dt.timedelta(hours=3, minutes=30) else "started"
    return "upcoming"


def tonight(date, now):
    from nbaprops.rules import tonight as TN
    st = statuses(date)
    delay = dt.timedelta(minutes=C.SITE_PUBLISH_DELAY_MIN)
    out = []
    for x in TN.payload(date):
        if x.get("stake", 0) <= 0:
            continue
        if delay and x.get("entry_at"):
            from nbaprops.rules import tipoff as T
            if T._t(x["entry_at"]) + delay > now:
                continue
        fair = x.get("fair_now") or x.get("fair_entry")
        need, pt = playable_to(fair)
        price_now = x.get("price_now") or x.get("price")
        moved = (x.get("line_now") not in (None, x.get("line"))) or (need is not None and price_now is not None and _dec(price_now) < need - 1e-9)
        gs = game_status(x, st, now)
        out.append({"player": x["player"], "market": x["market"], "side": x["side"], "line": x["line"],
                    "price": x.get("price"), "price_now": price_now, "ev": x.get("ev"), "score": x.get("score"), "tier": x.get("tier"),
                    "stake": x.get("stake") if gs == "upcoming" else None, "status": gs, "playable": pt, "moved": moved})
    return out


def yesterday(date):
    from nbaprops.rules import tonight as TN
    rr, rc = TN.record_rows()
    rows = [(r, v) for r, v in zip(rr, rc) if r["date"] == date and not r["tn_held"]]
    return [{"player": r["player"], "market": r["market"], "side": r["side"], "line": L._num(r["line"]),
             "price": L._num(r.get("entry_price") or r.get("odds")), "actual": r.get("actual"), "result": r.get("result"),
             "stake": L._num(r.get("kelly_u")), "units": L._num(r.get("profit_kelly")), "clv": v} for r, v in rows]


def totals(rows):
    w = sum(r["result"] == "W" for r in rows)
    lo = sum(r["result"] == "L" for r in rows)
    p = sum(r["result"] == "P" for r in rows)
    u = sum(r.get("units") or 0 for r in rows)
    staked = sum(r.get("stake") or 0 for r in rows)
    clv = [r["clv"] for r in rows if r.get("clv") is not None]
    return {"w": w, "l": lo, "p": p, "n": w + lo + p, "units": round(u, 2), "roi": round(100 * u / staked, 1) if staked else None,
            "clv": round(sum(clv) / len(clv), 2) if clv else None}


def records(version):
    rec = L.record(version)
    out = []
    for x in rec["strategies"]:
        n = x["w"] + x["l"] + x["p"]
        if not n:
            continue
        out.append({"label": x["label"], "group": x.get("group"), "w": x["w"], "l": x["l"], "p": x["p"], "n": n,
                    "units": x["units"], "roi": x.get("roi"), "kelly": x.get("kelly_units"), "clv": x.get("clv"),
                    "clv_unit": bool(x.get("clv_unit"))})
    return out


def daily(version):
    """Tonight's bets by night, newest first."""
    from nbaprops.rules import tonight as TN
    rr, rc = TN.record_rows(version)
    by: dict = {}
    for r, v in zip(rr, rc):
        if not r["tn_held"]:
            by.setdefault(r["date"], []).append({"result": r["result"], "units": L._num(r.get("profit_kelly")),
                                                 "stake": L._num(r.get("kelly_u")), "clv": v})
    return [(d, totals(rows)) for d, rows in sorted(by.items(), reverse=True)]


# ------------------------------------------------------------------ rendering
CSS = """
:root { --bg:#F3F5F6; --surface:#FFFFFF; --surface-2:#EEF2F3; --text:#2B333B; --ink:#0B0C10; --muted:#5E6B74;
  --accent:#0B7F79; --accent-dim:#45A29E; --line:rgba(11,127,121,.18); --good:#2E8B57; --bad:#C0504D; --push:rgba(43,51,59,.6);
  --display:"Bebas Neue","Oswald","Arial Narrow",Impact,sans-serif; --body:"Inter","Segoe UI",system-ui,-apple-system,sans-serif; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0B0C10; --surface:#1F2833; --surface-2:#243140;
  --text:#C5C6C7; --ink:#ECEDEE; --muted:#8E959B; --accent:#66FCF1; --accent-dim:#45A29E; --line:rgba(69,162,158,.22);
  --good:#63C98A; --bad:#E58080; --push:rgba(197,198,199,.6); color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#0B0C10; --surface:#1F2833; --surface-2:#243140; --text:#C5C6C7; --ink:#ECEDEE; --muted:#8E959B;
  --accent:#66FCF1; --accent-dim:#45A29E; --line:rgba(69,162,158,.22); --good:#63C98A; --bad:#E58080; --push:rgba(197,198,199,.6); color-scheme:dark; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.6 var(--body);
  padding-left:max(16px, env(safe-area-inset-left,0px)); padding-right:max(16px, env(safe-area-inset-right,0px)); padding-block:0 40px; }
.wrap { max-width:760px; margin:0 auto; display:flex; flex-direction:column; gap:22px; }
header.top { position:sticky; top:0; z-index:10; background:var(--bg); display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between;
  gap:10px 12px; padding:12px 0; border-bottom:1px solid var(--line); }
.chips { flex-basis:100%; display:flex; gap:6px; overflow-x:auto; padding-bottom:2px; scrollbar-width:thin; }
.chips a { flex:none; white-space:nowrap; text-decoration:none; color:var(--text); border:1px solid var(--line); border-radius:999px;
  font:600 11px/1 var(--body); text-transform:uppercase; letter-spacing:.12em; padding:8px 11px; }
.chips a:hover { border-color:var(--accent-dim); color:var(--ink); }
section[id] { scroll-margin-top:118px; }
.totop { text-align:center; font-size:13px; }
.logo { font:400 28px/1 var(--display); letter-spacing:.04em; color:var(--ink); text-decoration:none; }
.logo span { color:var(--accent-dim); }
nav.pages { display:flex; gap:6px; }
nav.pages a { font:600 11px/1 var(--body); text-transform:uppercase; letter-spacing:.16em; color:var(--text); text-decoration:none;
  padding:8px 10px; border:1px solid transparent; border-radius:6px; }
nav.pages a[aria-current="page"], nav.pages a:hover { border-color:var(--accent-dim); color:var(--ink); }
.updated { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.14em; }
h1 { font:400 clamp(44px, 12vw, 76px)/.92 var(--display); color:var(--accent); margin:6px 0 0; text-transform:uppercase; }
h2 { font:400 28px/1 var(--display); letter-spacing:.03em; text-transform:uppercase; color:var(--ink); margin:0 0 10px; }
.lede { margin:0; max-width:60ch; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
.bets { display:flex; flex-direction:column; gap:10px; }
.bet { display:grid; grid-template-columns:1fr auto; gap:4px 12px; align-items:start; }
.bet .who { font-weight:600; color:var(--ink); font-size:16px; }
.bet .what { font-variant-numeric:tabular-nums; }
.bet .meta { grid-column:1 / -1; display:flex; flex-wrap:wrap; gap:6px 14px; font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
.bet .meta b { color:var(--text); font-weight:600; }
.score { font:400 26px/1 var(--display); color:var(--accent); text-align:right; white-space:nowrap; }
.score small { display:block; font:600 10px/1.4 var(--body); letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
.pill { display:inline-block; font:600 10px/1 var(--body); letter-spacing:.12em; text-transform:uppercase; padding:4px 7px; border-radius:999px; border:1px solid var(--line); color:var(--text); }
.pill.upcoming { border-color:var(--accent-dim); }
.pill.final { color:var(--muted); }
.skip { color:var(--bad); font-weight:600; }
.empty { text-align:center; padding:26px 16px; }
.empty b { font:400 30px/1 var(--display); color:var(--ink); display:block; margin-bottom:6px; letter-spacing:.03em; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; font-size:14px; }
th { text-align:right; font:600 10px/1.3 var(--body); text-transform:uppercase; letter-spacing:.14em; color:var(--muted); padding:8px 8px; border-bottom:1px solid var(--line); }
td { text-align:right; padding:9px 8px; border-bottom:1px solid var(--line); }
th.l, td.l { text-align:left; }
tbody tr:nth-child(even) td { background:var(--surface-2); }
.scroll { overflow-x:auto; margin:0 -16px; padding:0 16px; }
.W { color:var(--good); font-weight:700; } .L { color:var(--bad); font-weight:700; } .P { color:var(--push); font-weight:700; }
.pos { color:var(--good); } .neg { color:var(--bad); } .dim { color:var(--muted); }
.totals { display:flex; flex-wrap:wrap; gap:8px 18px; font-size:14px; margin-top:10px; }
.totals b { color:var(--ink); }
dl.gloss { display:grid; grid-template-columns:max-content 1fr; gap:10px 16px; margin:0; }
dl.gloss dt { font-weight:700; color:var(--ink); }
dl.gloss dd { margin:0; }
.learn { border-top:1px solid var(--line); padding-top:12px; margin-top:12px; }
.learn h3 { font:400 20px/1 var(--display); letter-spacing:.04em; color:var(--ink); margin:0 0 8px; }
.learn ul { margin:8px 0 0; padding-left:18px; }
footer { font-size:13px; color:var(--muted); border-top:1px solid var(--line); padding-top:14px; display:flex; flex-direction:column; gap:8px; }
footer b { color:var(--text); }
a { color:var(--accent-dim); }
.theme { background:transparent; border:1px solid var(--line); color:var(--text); border-radius:6px; padding:7px 9px; cursor:pointer; font:600 11px/1 var(--body); text-transform:uppercase; letter-spacing:.14em; }
@media (min-width:720px) { .wrap { gap:28px; } .card { padding:18px 20px; } }
"""

THEME_JS = """<script>
(function(){var b=document.getElementById('theme');if(!b)return;function cur(){return document.documentElement.getAttribute('data-theme');}
try{var s=localStorage.getItem('sb-theme');if(s)document.documentElement.setAttribute('data-theme',s);}catch(e){}
b.addEventListener('click',function(){var dark=cur()==='dark'||(!cur()&&matchMedia('(prefers-color-scheme: dark)').matches);
var n=dark?'light':'dark';document.documentElement.setAttribute('data-theme',n);try{localStorage.setItem('sb-theme',n);}catch(e){}});})();
</script>"""

FOOTER = """<footer>
<div><b>21+ only. For entertainment only. Not betting advice.</b> Projections, not guarantees; past results don't predict future ones.
Bet only what you can afford to lose and follow the laws where you live. If gambling stops being fun, call or text <b>1-800-GAMBLER</b>.</div>
<div>Data: player projections from BettingPros; market prices from Unabated, CrazyNinja Odds and Kalshi; box scores from ESPN; lineups
from RotoWire. ScuseBET is independent and not affiliated with or endorsed by any of them, FanDuel or the NBA. No affiliate links.</div>
</footer>"""


def page(title, active, body, updated, chips=()):
    cur = ' aria-current="page"'
    nav = "".join(f'<a href="{href}"{cur if active == key else ""}>{label}</a>'
                  for key, href, label in (("index", "index.html", "Tonight"), ("records", "records.html", "Records")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow"><title>{e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;600;700&display=swap">
<style>{CSS}</style></head><body id="top"><div class="wrap">
<header class="top"><a class="logo" href="index.html">SCUSE<span>BET</span></a><nav class="pages">{nav}</nav>
<button class="theme" id="theme" type="button" aria-label="Switch light or dark">Theme</button>
{('<nav class="chips" aria-label="Sections">' + "".join(f'<a href="#{a}">{e(t)}</a>' for a, t in chips) + "</nav>") if chips else ""}</header>
<div class="updated">Last updated {e(updated)}</div>
{body}
<p class="totop"><a href="#top">Back to top</a></p>
{FOOTER}
</div>{THEME_JS}</body></html>
"""


def _odds(v):
    if v is None:
        return "–"
    v = float(v)
    return f"+{v:g}" if v > 0 else f"{v:g}"


def _u(v):
    if v is None:
        return '<span class="dim">–</span>'
    c = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{c}">{v:+.2f}u</span>'


def _pct(v):
    if v is None:
        return '<span class="dim">–</span>'
    c = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{c}">{v:+.1f}%</span>'


def render_index(date, now, bets, yrows, ytot, run, updated):
    side = lambda s: "Over" if s == "over" else "Under"
    if bets:
        cards = []
        for b in bets:
            meta = [f'<span class="pill {b["status"]}">{b["status"]}</span>',
                    f'EV <b>{100 * (b["ev"] or 0):+.1f}%</b>' if b.get("ev") is not None else "",
                    f'FanDuel <b>{_odds(b["price_now"])}</b>',
                    ('<span class="skip">Line moved, skip</span>' if b["moved"] else f'Playable to <b>{b["playable"]}</b>') if b.get("playable") else "",
                    f'Stake <b>{b["stake"]:.2f}u</b>' if b.get("stake") is not None else ""]
            cards.append(f'''<div class="card bet"><div><div class="who">{e(b["player"])}</div>
<div class="what">{e(b["market"])} {side(b["side"])} {b["line"]:g}</div></div>
<div class="score">{b["score"]:g} ({e(b["tier"] or "")})<small>Sam score</small></div>
<div class="meta">{"".join(f"<span>{m}</span>" for m in meta if m)}</div></div>''')
        bets_html = '<div class="bets">' + "".join(cards) + "</div>"
    else:
        bets_html = ('<div class="card empty"><b>No bets tonight</b>Nothing cleared every ScuseBET check. '
                     "Passing is part of the plan; check back after the next update.</div>")
    if yrows:
        trs = "".join(f'<tr><td class="l">{e(r["player"])}</td><td class="l">{e(r["market"])} {"o" if r["side"] == "over" else "u"}{r["line"]:g}</td>'
                      f'<td>{_odds(r["price"])}</td><td>{e(str(r["actual"] if r["actual"] not in (None, "") else "–"))}</td>'
                      f'<td class="{e(r["result"] or "")}">{e(r["result"] or "–")}</td><td>{_u(r["units"])}</td><td>{_pct(r["clv"])}</td></tr>' for r in yrows)
        ytab = f'''<div class="scroll"><table><thead><tr><th class="l">Player</th><th class="l">Bet</th><th>Price</th><th>Final</th><th>Result</th>
<th>Units</th><th>CLV</th></tr></thead><tbody>{trs}</tbody></table></div>
<div class="totals"><span>Yesterday <b>{ytot["w"]}-{ytot["l"]}-{ytot["p"]}</b> {_u(ytot["units"])}</span></div>'''
    else:
        ytab = '<p class="dim">No bets yesterday.</p>'
    ytab += (f'<div class="totals"><span>All bets so far <b>{run["w"]}-{run["l"]}-{run["p"]}</b> ({run["n"]} bets)</span>'
             f'<span>{_u(run["units"])}</span><span>ROI {_pct(run["roi"])}</span><span>Avg CLV {_pct(run["clv"])}</span></div>')
    body = f'''<section id="tonight"><div class="updated">{e(dt.date.fromisoformat(date).strftime("%A, %B %-d"))}</div><h1>Tonight's bets</h1>
<p class="lede">NBA player props priced against FanDuel and scored by the ScuseBET models. These are the only plays we put money on.</p></section>
<section>{bets_html}</section>
<section class="card" id="yesterday"><h2>Yesterday's results</h2>{ytab}</section>
<section class="card" id="how"><h2>How it works</h2><p class="lede">Every night we compare FanDuel's player-prop prices with the rest of the market
(sportsbooks and exchanges) to find prices that are better than fair. A bet makes this list only when the price is good enough
<i>and</i> the ScuseBET Sam score (0-100, built from the market edge, matchup, role, recent form and more) is 50 or higher.
Stakes are a quarter of the Kelly amount with caps per player, game and night. Everything is graded from the box score the next
morning, and every record stays public, losses included.</p></section>
<section class="card" id="glossary"><h2>Glossary</h2><dl class="gloss">
<dt>EV</dt><dd>Expected value: how much a bet should return on average at this price compared with its fair odds. +4% means about 4 cents per dollar over many bets.</dd>
<dt>CLV</dt><dd>Closing line value: how much better our price was than the final market price before tip-off. Beating the close is the clearest sign of real edge, more reliable than a short winning streak.</dd>
<dt>Units</dt><dd>1u = 1% of your bankroll. If you follow casually, bet a flat 1u on each play instead of the listed stakes.</dd>
<dt>Sam score</dt><dd>ScuseBET's 0-100 rating. A = 80+, B = 65-79, C = 50-64. Below 50 is tracked but never bet.</dd>
<dt>Playable to</dt><dd>The worst FanDuel price where the bet is still about 2% better than fair. If FanDuel's price is worse than that, we show "line moved, skip".</dd>
</dl></section>'''
    return page("ScuseBET", "index", body, updated, chips=(("tonight", "Tonight's bets"), ("yesterday", "Yesterday"),
                                                             ("how", "How it works"), ("glossary", "Glossary")))


def _clv(r):
    if r["clv"] is None:
        return '<span class="dim">–</span>'
    return _pct(r["clv"]) if r["clv_unit"] else f'{r["clv"]:+.2f} pts'


def _rec_table(rows):
    trs = "".join(f'<tr><td class="l">{e(r["label"])}</td><td>{r["n"]}</td><td>{r["w"]}-{r["l"]}-{r["p"]}</td><td>{_u(r["units"])}</td>'
                  f'<td>{_pct(r["roi"])}</td><td>{_clv(r)}</td></tr>'
                  for r in rows)
    return (f'<div class="scroll"><table><thead><tr><th class="l">Record</th><th>Bets</th><th>W-L-P</th><th>Units (1u)</th><th>ROI</th><th>CLV</th></tr></thead>'
            f"<tbody>{trs}</tbody></table></div>")


def render_learns():
    """Daily learns section: per day, the Sam score weight changes, sample and notes (no third-party data)."""
    try:
        from nbaprops.rules import learns as LR
        days = LR.daily()
    except Exception:  # noqa: BLE001
        days = []
    if not days:
        return ""
    parts = []
    for d in days:
        rows = "".join(f'<tr><td class="l">{e(c["label"])}</td><td>{c["old"]:g}</td><td><b>{c["new"]:g}</b></td>'
                       f'<td class="{"pos" if c["delta"] > 0 else "neg"}">{c["delta"]:+.1f}</td></tr>' for c in d["changes"])
        other = [t for t in LR.lines(d) if not t.startswith(("Sam score weights moved", "First Sam score update"))]
        parts.append(f'<div class="learn"><h3>{e(d["date"])}{" · first update" if d["first"] else ""}</h3>'
                     + (f'<div class="scroll"><table><thead><tr><th class="l">Part</th><th>Was</th><th>Now</th><th>Change</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else "")
                     + ("<ul>" + "".join(f"<li>{e(t)}</li>" for t in other) + "</ul>" if other else "") + "</div>")
    return ('<section class="card" id="learns"><h2>Daily learns</h2><p class="lede">What the model learned each night and what it changed. '
            "The Sam score's part weights (out of 100) move a little every night toward what has actually been winning.</p>"
            + "".join(parts) + "</section>")


def render_records(v2, days, v1_link, updated, title="Records", note="", learns_html=""):
    dtab = "".join(f'<tr><td class="l">{e(d)}</td><td>{t["n"]}</td><td>{t["w"]}-{t["l"]}-{t["p"]}</td><td>{_u(t["units"])}</td><td>{_pct(t["clv"])}</td></tr>'
                   for d, t in days) or '<tr><td class="l dim" colspan="5">No graded nights yet.</td></tr>'
    body = f'''<section><h1>{e(title)}</h1><p class="lede">{note or "Every ScuseBET record with its full sample, losses included. Small samples swing a lot: read the number of bets before the win rate."}</p></section>
<section class="card" id="nights"><h2>Tonight's bets, night by night</h2><div class="scroll"><table><thead><tr><th class="l">Night</th><th>Bets</th><th>W-L-P</th>
<th>Units</th><th>CLV</th></tr></thead><tbody>{dtab}</tbody></table></div></section>
<section class="card" id="records"><h2>Every record</h2>{_rec_table(v2)}</section>
{learns_html}
{v1_link}'''
    chips = (("nights", "Night by night"), ("records", "Every record")) + ((("learns", "Daily learns"),) if learns_html else ()) + \
        ((("archive", "v1 archive"),) if v1_link else ())
    return page(f"ScuseBET {title}", "records", body, updated, chips=chips)


def build(date=None) -> list[str]:
    from nbaprops.app import props as P
    date = date or P.eastern_today()
    now = dt.datetime.now(dt.UTC)
    try:
        from zoneinfo import ZoneInfo
        updated = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%-I:%M %p ET, %b %-d")
    except Exception:  # noqa: BLE001
        updated = now.strftime("%H:%M UTC")
    ydate = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    bets = tonight(date, now)
    yrows = yesterday(ydate)
    rr, rc = __import__("nbaprops.rules.tonight", fromlist=["x"]).record_rows("v2")
    run = totals([{"result": r["result"], "units": L._num(r.get("profit_kelly")), "stake": L._num(r.get("kelly_u")), "clv": v}
                  for r, v in zip(rr, rc) if not r["tn_held"]])
    os.makedirs(SITE, exist_ok=True)
    files = {
        "index.html": render_index(date, now, bets, yrows, totals(yrows), run, updated),
        "records.html": render_records(records("v2"), daily("v2"),
                                       '<section class="card" id="archive"><h2>Archive</h2><p>Before the current rules (to Nov 23): '
                                       '<a href="v1.html">v1 archive</a>.</p></section>', updated, learns_html=render_learns()),
        "v1.html": render_records(records("v1"), daily("v1"), "", updated, title="v1 archive",
                                  note="Results under the first rule set, before November 24. Kept public for the full history."),
        "robots.txt": "User-agent: *\nDisallow: /\n",
    }
    paths = []
    for name, text in files.items():
        p = os.path.join(SITE, name)
        with open(p, "w") as f:
            f.write(text)
        paths.append(p)
    return paths


# ------------------------------------------------------------------ secret check
TOKEN_PATTERNS = {
    "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}",
    "OpenAI/Anthropic-style key": r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "Slack token": r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
    "Google Drive / Docs link": r"(?:drive|docs)\.google\.com/[^\s\"'<>]+",
    "assigned secret": r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
}


def _known_secrets():
    """{name: value} of local secrets to look for: every .env value (8+ chars) and the Drive folder id. Never printed."""
    out = {}
    try:
        for ln in open(os.path.join(ROOT, ".env")):
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if len(v) >= 8:
                    out[f".env {k.strip()}"] = v
    except OSError:
        pass
    try:
        v = open(os.path.join(ROOT, ".drive_folder_id")).read().strip()
        if len(v) >= 8:
            out["Drive folder id"] = v
    except OSError:
        pass
    return out


def scan(paths) -> list[tuple[str, str]]:
    """[(file, kind)] for every secret found. Values are never returned or printed."""
    known = _known_secrets()
    hits = []
    for p in paths:
        try:
            text = open(p, errors="ignore").read()
        except (OSError, IsADirectoryError):
            continue
        for kind, val in known.items():
            if val in text:
                hits.append((os.path.relpath(p, ROOT), kind))
        for kind, pat in TOKEN_PATTERNS.items():
            if re.search(pat, text):
                hits.append((os.path.relpath(p, ROOT), kind))
    return hits


def repo_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    return [os.path.join(ROOT, f) for f in out if not f.startswith("tests/")]


# ------------------------------------------------------------------ publish (gh-pages)
def publish(force=False) -> str:
    """Push site/ to the gh-pages branch (only when config.SITE_PUBLISH is True). -> status text."""
    if not C.SITE_PUBLISH and not force:
        return "not published (config.SITE_PUBLISH is False)"
    stamp = os.path.join(ROOT, ".site_last_push")
    try:
        if not force and (dt.datetime.now().timestamp() - os.path.getmtime(stamp)) < 60 * C.SITE_PUSH_EVERY_MIN:
            return "not published (pushed less than SITE_PUSH_EVERY_MIN ago)"
    except OSError:
        pass
    wt = os.path.join(ROOT, ".site-worktree")
    run = lambda *a, **k: subprocess.run(list(a), cwd=k.get("cwd", ROOT), capture_output=True, text=True)
    if not os.path.isdir(wt):
        if run("git", "rev-parse", "--verify", "gh-pages").returncode != 0 and \
                run("git", "ls-remote", "--exit-code", "--heads", "origin", "gh-pages").returncode != 0:
            run("git", "worktree", "add", "--detach", wt)
            run("git", "checkout", "--orphan", "gh-pages", cwd=wt)
            run("git", "rm", "-rf", "-q", ".", cwd=wt)
        else:
            run("git", "fetch", "-q", "origin", "gh-pages")
            run("git", "worktree", "add", wt, "gh-pages")
    for name in os.listdir(SITE):
        with open(os.path.join(SITE, name), "rb") as src, open(os.path.join(wt, name), "wb") as dst:
            dst.write(src.read())
    open(os.path.join(wt, ".nojekyll"), "w").close()
    run("git", "add", "-A", cwd=wt)
    if run("git", "diff", "--cached", "--quiet", cwd=wt).returncode == 0:
        return "no changes to publish"
    run("git", "commit", "-q", "-m", "ScuseBET site update", cwd=wt)
    r = run("git", "push", "-q", "origin", "gh-pages", cwd=wt)
    if r.returncode != 0:
        return "push failed: " + r.stderr.strip()[:200]
    open(stamp, "w").close()
    return "published to gh-pages"


def main():
    date = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else None
    paths = build(date)
    hits = scan(paths + repo_files())
    if hits:
        for f, kind in sorted(set(hits)):
            print(f"SECRET CHECK FAILED: {kind} in {f}")
        sys.exit(1)
    print(f"Built {len(paths)} files in site/; secret check passed ({len(paths)} site files + repo).")
    if "--publish" in sys.argv:
        print(publish())


if __name__ == "__main__":
    main()
