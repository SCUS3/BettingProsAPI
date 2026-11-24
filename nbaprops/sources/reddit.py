"""Reddit's official API (OAuth, app-only "client credentials"). Free; this client stays far inside the limits
(at most one call per 1.1 s, and it honours X-Ratelimit-Remaining / Reset). Keys in .env, never printed:
REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and optionally REDDIT_USERNAME (for the User-Agent Reddit asks for).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

API = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
MIN_INTERVAL = 1.1
_state = {"token": None, "exp": 0.0, "last": 0.0}


class NotConfigured(RuntimeError):
    pass


def _agent() -> str:
    user = os.environ.get("REDDIT_USERNAME")
    return f"python:nbaprops:2.0 (by /u/{user})" if user else "python:nbaprops:2.0"


def _token() -> str:
    from nbaprops.sources import bettingpros as BP
    BP.load_dotenv()
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        raise NotConfigured("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not in .env")
    if _state["token"] and time.time() < _state["exp"] - 60:
        return _state["token"]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
                                 headers={"Authorization": f"Basic {auth}", "User-Agent": _agent()})
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.load(r)
    _state.update(token=j["access_token"], exp=time.time() + float(j.get("expires_in", 3600)))
    return _state["token"]


def get(path: str, params: dict | None = None) -> dict | list:
    """GET an OAuth endpoint, throttled and rate-limit aware."""
    wait = MIN_INTERVAL - (time.time() - _state["last"])
    if wait > 0:
        time.sleep(wait)
    url = f"{API}{path}?{urllib.parse.urlencode(dict(params or {}, raw_json=1))}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}", "User-Agent": _agent()})
    with urllib.request.urlopen(req, timeout=30) as r:
        _state["last"] = time.time()
        rem, reset = r.headers.get("X-Ratelimit-Remaining"), r.headers.get("X-Ratelimit-Reset")
        if rem is not None and float(rem) < 5 and reset:
            time.sleep(float(reset) + 1)
        return json.load(r)


def find_thread(date_label: str, subreddit: str = "sportsbook") -> dict | None:
    """Today's NBA picks thread on the subreddit (title mentions NBA and picks/betting, posted in the last day)."""
    j = get(f"/r/{subreddit}/search", {"q": "NBA", "restrict_sr": 1, "sort": "new", "t": "day", "limit": 50})
    posts = [c["data"] for c in j.get("data", {}).get("children", [])]
    want = [p for p in posts if re.search(r"\bNBA\b", p.get("title", ""), re.I) and re.search(r"pick|bet|prop", p.get("title", ""), re.I)]
    want.sort(key=lambda p: (date_label.lower() not in p["title"].lower(), -p.get("num_comments", 0)))
    return want[0] if want else None


def comments(post_id: str) -> list[dict]:
    """Every comment in a thread (expanding 'more' stubs): [{id, author, body, created_utc}]."""
    j = get(f"/comments/{post_id}", {"limit": 500, "depth": 10, "sort": "new"})
    out, more = [], []

    def walk(listing):
        for c in (listing or {}).get("data", {}).get("children", []):
            if c.get("kind") == "more":
                more.extend(c["data"].get("children", []))
            elif c.get("kind") == "t1":
                d = c["data"]
                out.append({"id": d["id"], "author": d.get("author") or "[deleted]", "body": d.get("body") or "",
                            "created_utc": d.get("created_utc")})
                walk(d.get("replies") or None)
    walk(j[1] if isinstance(j, list) and len(j) > 1 else None)
    for i in range(0, len(more), 100):
        r = get("/api/morechildren", {"link_id": f"t3_{post_id}", "children": ",".join(more[i:i + 100]), "api_type": "json"})
        for t in r.get("json", {}).get("data", {}).get("things", []):
            if t.get("kind") == "t1":
                d = t["data"]
                out.append({"id": d["id"], "author": d.get("author") or "[deleted]", "body": d.get("body") or "",
                            "created_utc": d.get("created_utc")})
    return out
