"""Weekly check of CrazyNinja's API page (one free GET of http://api.crazyninjaodds.com/): reports when an
odds endpoint (or any new /api/ link) appears. State in cno_api_state.json.

    python3 bp_cno_watch.py          # checks only if 7+ days since the last check
    python3 bp_cno_watch.py --now    # check now
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.request

from nbaprops.config import ROOT as HERE  # noqa: E402
PAGE = "http://api.crazyninjaodds.com/"
STATE = os.path.join(HERE, "cno_api_state.json")
EVERY_DAYS = 7
KNOWN = {"/api/devigger/v1/sportsbook_devigger.aspx"}


def check(force=False):
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    last = st.get("checked")
    if not force and last and dt.date.today() - dt.date.fromisoformat(last) < dt.timedelta(days=EVERY_DAYS):
        return None
    try:
        req = urllib.request.Request(PAGE, headers={"User-Agent": "props-api-page-check"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception as e:
        return f"CrazyNinja API page check failed ({e}); will retry next run."
    links = sorted({re.sub(r"^https?://api\.crazyninjaodds\.com", "", h).split("?")[0]
                    for h in re.findall(r"""href=["']([^"']+)""", raw) if "/api/" in h})
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))
    new_links = [x for x in links if x not in KNOWN]
    odds_hint = re.findall(r"[^.]{0,80}\b(?:odds api|sportsbook odds|odds endpoint|odds url|/odds)\b[^.]{0,80}", text, re.I)
    digest = hashlib.sha256(text.encode()).hexdigest()
    changed = st.get("digest") not in (None, digest)
    st.update(checked=dt.date.today().isoformat(), digest=digest, links=links)
    json.dump(st, open(STATE, "w"), indent=1)
    if new_links or odds_hint:
        return ("CrazyNinja API page: NEW " + (f"endpoint(s) {', '.join(new_links)}" if new_links else "")
                + ("; odds wording: " + " | ".join(h.strip() for h in odds_hint[:3]) if odds_hint else ""))
    return "CrazyNinja API page: no odds endpoint yet (devigger only)" + ("; page text changed since last check." if changed else ".")


if __name__ == "__main__":
    msg = check("--now" in sys.argv)
    print(msg or f"CrazyNinja API page: checked within the last {EVERY_DAYS} days.")
