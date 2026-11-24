"""
Projected lineups and injury statuses from Rotowire's public NBA lineups page
(https://www.rotowire.com/basketball/nba-lineups.php; allowed by its robots.txt).

One request per live run. Returns, for every player on the page:
    {"name", "team", "starter": bool, "status": "Out"|"Ques"|"GTD"|"Prob"|"OFS"|"", "play_pct": 0-100,
     "lineup": "confirmed"|"expected", "time": "7:00 PM ET"}
Standard library only. Any failure returns None so the props run carries on without it.
"""

import html as _html
import re
import unicodedata
import urllib.request

URL = "https://www.rotowire.com/basketball/nba-lineups.php"
UA = "Mozilla/5.0 (compatible; bp-props/1.0; personal use, one request per run)"


def norm(name):
    """Normalize a player name for matching across sites: accents, punctuation, suffixes, case."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[.'`,-]", " ", n.lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", n)
    return " ".join(n.split())


def fetch(timeout=20):
    """Read RotoWire's public lineups page (no API request); [] if unavailable."""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def parse(page):
    """Parse the lineups page into player rows."""
    players = []
    blocks = page.split('class="lineup is-nba"')[1:]
    for blk in blocks:
        time = re.search(r'lineup__time">\s*([^<]+)<', blk)
        abbrs = re.findall(r'lineup__abbr">\s*([A-Z]{2,4})\s*<', blk)
        lists = re.findall(r'<ul class="lineup__list is-(visit|home)">(.*?)</ul>', blk, re.S)
        if len(abbrs) < 2 or not lists:
            continue
        team_of = {"visit": abbrs[0], "home": abbrs[1]}
        for side, body in lists:
            status = re.search(r'lineup__status is-(\w+)', body)
            lineup = status.group(1) if status else ""
            may_not = body.find("MAY NOT PLAY")
            for m in re.finditer(r'<li class="lineup__player[^"]*is-pct-play-(\d+)[^"]*"[^>]*>(.*?)</li>', body, re.S):
                inner = m.group(2)
                nm = re.search(r'<a title="([^"]+)"', inner)
                if not nm:
                    continue
                inj = re.search(r'lineup__inj">\s*([^<]+)<', inner)
                players.append({
                    "name": _html.unescape(nm.group(1)).strip(),
                    "team": team_of[side],
                    "starter": may_not < 0 or m.start() < may_not,
                    "status": inj.group(1).strip() if inj else "",
                    "play_pct": int(m.group(1)),
                    "lineup": lineup,
                    "time": time.group(1).strip() if time else "",
                })
    return players or None


def index(players):
    """{normalized name: player} for matching BettingPros props."""
    return {norm(p["name"]): p for p in players or []}


def is_out(p):
    """True for a status that means the player won't play."""
    return p["status"] in ("Out", "OFS") or p["play_pct"] == 0


def is_doubtful(p):
    """True for a doubtful-type status."""
    return not is_out(p) and (p["status"] in ("Ques", "GTD", "Doubt") or p["play_pct"] <= 50)
