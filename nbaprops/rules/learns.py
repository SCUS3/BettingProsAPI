"""Daily learns: what the model learned each night and what changed because of it.

Every nightly adaptation saves a snapshot of the Sam score weights (sam_weights_history.json, keyed by the last graded
slate). A day's entry lists each part whose weight moved ("DvP 7.8 -> 6.9 (-0.9)"), the sample it learned from, that
night's Tonight's bets result, and any rule notes (model_notes.json, e.g. a cap that unlocked).
"""
from __future__ import annotations

import json
import os
import re

from nbaprops import config as C
from nbaprops.rules import ledger as L

HIST = os.path.join(L.HERE, "sam_weights_history.json")
NOTES = os.path.join(L.HERE, "model_notes.json")
LABELS = {"edge": "Market edge", "quality": "Market quality", "bp": "BettingPros", "dvp": "DvP", "move": "Line movement",
          "role": "Role", "form": "Form", "consensus": "Consensus", "threes": "3PM market"}


def history():
    try:
        return json.load(open(HIST))
    except (OSError, ValueError):
        return []


def record(date, res):
    """Save tonight's adaptation as the snapshot for `date` (the last graded slate); replaces a same-date entry."""
    h = [x for x in history() if x["date"] != date]
    h.append({"date": date, "n": res.get("n"), "props": res.get("props"), "s": res.get("s"), "weights": res.get("weights"),
              "at": res.get("at")})
    h.sort(key=lambda x: x["date"])
    json.dump(h, open(HIST, "w"), indent=1)


def backfill_from_log(path):
    """Rebuild snapshots from logs/nightly.log (each run printed its weight table); the last run per date wins."""
    try:
        text = open(path).read()
    except OSError:
        return 0
    n = 0
    for block in re.split(r"=== nightly ", text)[1:]:
        date = block[:10]
        m = re.search(r"adapted from (\d+) graded props \(([\d.]+) player-games\), shrink s = ([\d.]+)", block)
        rows = re.findall(r"\| (\w+) \| ([\d.]+) \| ([\d.]+) \| [-+][\d.]+ \|", block)
        if m and rows:
            record(date, {"n": float(m.group(2)), "props": int(m.group(1)), "s": float(m.group(3)),
                          "weights": {k: float(now) for k, _, now in rows}})
            n += 1
    return n


def notes():
    try:
        return json.load(open(NOTES))
    except (OSError, ValueError):
        return {}


def add_note(date, text):
    nt = notes()
    if text not in nt.get(date, []):
        nt.setdefault(date, []).append(text)
        json.dump(nt, open(NOTES, "w"), indent=1)


def daily(min_change=0.05):
    """[{date, n, changes: [{part, label, old, new, delta}], bets: totals, notes}] newest first."""
    from nbaprops.rules import tonight as TN
    h = history()
    base = {"date": "start", "weights": C.SAM_WEIGHTS}
    try:
        rr = TN.record_rows()[0]
    except Exception:  # noqa: BLE001
        rr = []
    out, prev = [], base
    nt = notes()
    for x in h:
        ch = []
        for k, new in (x.get("weights") or {}).items():
            old = (prev.get("weights") or {}).get(k)
            if old is not None and abs(new - old) >= min_change:
                ch.append({"part": k, "label": LABELS.get(k, k), "old": old, "new": new, "delta": round(new - old, 1)})
        ch.sort(key=lambda c: -abs(c["delta"]))
        day = [r for r in rr if r["date"] == x["date"] and not r.get("tn_held")]
        w = sum(r["result"] == "W" for r in day)
        lo = sum(r["result"] == "L" for r in day)
        u = round(sum(L._num(r.get("profit_kelly")) or 0 for r in day), 2)
        out.append({"date": x["date"], "n": x.get("n"), "changes": ch, "first": prev is base,
                    "bets": {"w": w, "l": lo, "units": u, "n": len(day)}, "notes": nt.get(x["date"], [])})
        prev = x
    for d, txt in nt.items():                           # note-only days (no weight snapshot)
        if not any(o["date"] == d for o in out):
            out.append({"date": d, "n": None, "changes": [], "first": False, "bets": {"n": 0}, "notes": txt})
    return sorted(out, key=lambda o: o["date"], reverse=True)


def lines(entry) -> list[str]:
    """Plain sentences for one day."""
    out = []
    if entry["changes"]:
        out.append(("First Sam score update from results: " if entry["first"] else "Sam score weights moved: ") +
                   "; ".join(f"{c['label']} {c['old']:g} -> {c['new']:g} ({c['delta']:+.1f})" for c in entry["changes"]) + ".")
    elif entry.get("n"):
        out.append("No weight moved by 0.1 or more.")
    if entry.get("n"):
        out.append(f"Learned from {entry['n']:.0f} graded player-games.")
    b = entry.get("bets") or {}
    if b.get("n"):
        out.append(f"Tonight's bets that night: {b['w']}-{b['l']}, {b['units']:+.2f}u.")
    out += entry.get("notes") or []
    return out
