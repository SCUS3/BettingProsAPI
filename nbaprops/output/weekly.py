"""Weekly review (python3 bp_props.py --weekly [--date END]): the 7 days ending END, every record, split by prop type,
line type (main / alt), side, source and entry EV bucket. Written to weekly_<END>.md and printed. No API requests."""
from __future__ import annotations

import datetime as dt

from nbaprops.output.note import md_table
from nbaprops.rules import ledger as L

GROUPS = [("Your rule", {"my_rule"}), ("Near misses (research)", {"near_miss"}), ("Unabated +EV & BP agrees", {"ua_agree"}),
          ("BETs (Unabated)", {"bet_agree", "bet_only"}), ("CrazyNinja +EV", {"cno"}), ("CNO screener", {"cno_scr", "cno_scr_sub"}),
          ("Alt ladder", {"alt_mkt", "alt_proj"}), ("All +EV (any source)", {"all_ev"}), ("HALF BETs", {"half"}),
          ("Outside picks", {"ext_pick"}), ("Projection model (research)", {"bal", "gap", "learned"})]


def _line_type(r):
    return "alt" if r["strategy"] in L.ALT_KEYS else "main"


def _source(r):
    return r.get("fair_src") or ("BettingPros model" if r["strategy"] in {"bal", "gap", "learned", "score"} else "?")


def _ev_bucket(r):
    e = L._num(r.get("entry_ev") or r.get("oa_ev"))
    if e is None or e <= 0:
        return None
    return next(name for lo, hi, name in L.EV_BUCKETS if lo <= e < hi)


SPLITS = [("Prop type", lambda r: r["market"]), ("Line type", _line_type), ("Side", lambda r: r["side"]),
          ("Source", _source), ("Entry EV", _ev_bucket)]


def _row(label, rows, clvs):
    s = L._summ(rows, clvs)
    return [label, str(len(rows)), f"{s['w']}-{s['l']}-{s['p']}", "" if s["hit"] is None else f"{s['hit']}%", f"{s['units']:+.2f}",
            f"{s['kelly_units']:+.2f}", "" if s["clv"] is None else f"{s['clv']:+}"]


COLS = [("Group", "l"), ("Plays", "r"), ("W-L-P", "r"), ("Hit", "r"), ("Flat u", "r"), ("¼K u", "r"), ("CLV", "r")]


def build(end: str) -> str:
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=6)).isoformat()
    led = L._read(L.LEDGER)
    counted, clv = L.counted_flags(led)
    graded = [(r, v) for r, ok, v in zip(led, counted, clv)
              if ok and start <= r["date"] <= end and r.get("result") in ("W", "L", "P")]
    lines = [f"# Weekly review: {start} to {end}", "",
             f"{len({r['date'] for r, _ in graded})} graded slates, {len(graded)} graded plays (pre-tip only; voids excluded). "
             "Flat u = 1u per play; ¼K u = the logged stakes (research lists have none). CLV: % EV at the closing fair for "
             "market records, line points for the model lists. Rule versions: "
             + ", ".join(sorted({L.rule_version(r) for r, _ in graded})) + ".", ""]
    body = []
    for label, keys in GROUPS:
        sub = [(r, v) for r, v in graded if r["strategy"] in keys]
        if sub:
            body.append(_row(label, [r for r, _ in sub], [v for _, v in sub if v is not None]))
    lines += ["## Every record", ""] + (md_table(COLS, body) if body else ["_Nothing graded this week._"]) + [""]
    # Entry-EV signal across all records (all graded history, not just this week); watched, not acted on
    allg = [(r, v) for r, ok, v in zip(led, counted, clv) if ok and r.get("result") in ("W", "L", "P")]
    sig = []
    for lo, hi, name in L.EV_BUCKETS:
        g = [(r, v) for r, v in allg if r["strategy"] in {s for _, ks in L.EV_RECORDS for s in ks}
             and (e := L._num(r.get("entry_ev") or r.get("oa_ev"))) is not None and lo <= e < hi]
        if g:
            s = L._summ([r for r, _ in g], [v for _, v in g])
            sig.append(f"{name}: {len(g)} plays, {s['w']}-{s['l']}, {s['units']:+.2f}u" + (f", CLV {s['clv']:+}% EV ({s['clv_n']})" if s["clv"] is not None else ""))
    if sig:
        lines += ["## Entry-EV signal (watched, not acted on)", "",
                  "All graded +EV-record plays to date, by entry EV: " + "; ".join(sig) + ". Early read: higher entry EV has "
                  "carried better CLV (4%+ about +2.0% vs. 2-4% about -0.75% at the first look), but no rule changes until every "
                  "bucket has 50+ graded plays.", ""]
    for label, keys in GROUPS:
        sub = [(r, v) for r, v in graded if r["strategy"] in keys]
        if len(sub) < 2:
            continue
        lines += [f"## {label}", ""]
        for dim, fn in SPLITS:
            groups: dict = {}
            for r, v in sub:
                k = fn(r)
                if k:
                    groups.setdefault(k, []).append((r, v))
            if len(groups) < 2 and dim != "Entry EV":
                continue
            rows = [_row(f"{dim}: {k}", [r for r, _ in g], [v for _, v in g if v is not None])
                    for k, g in sorted(groups.items(), key=lambda kv: -sum(L._num(r.get("profit_u")) or 0 for r, _ in kv[1]))]
            if rows:
                lines += md_table(COLS, rows) + [""]
    out = f"weekly_{end}.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    return out
