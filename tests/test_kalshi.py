from nbaprops.sources import kalshi as K


def test_date_code_and_two_sided_parse():
    assert K.date_code("2026-11-25") == "26NOV25"
    ms = [{"event_ticker": "KXNBAPTS-26NOV25MINOKC", "status": "active", "title": "Bones Hyland: 15+ points", "floor_strike": 14.5,
           "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42", "ticker": "T1"},
          {"event_ticker": "KXNBAPTS-26NOV25MINOKC", "status": "active", "title": "Rudy Gobert: 10+ points", "floor_strike": 9.5,
           "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.45"},                                   # spread 15c: dropped
          {"event_ticker": "KXNBAPTS-26NOV24DALDEN", "status": "active", "title": "X: 5+ points", "floor_strike": 4.5,
           "yes_bid_dollars": "0.5", "yes_ask_dollars": "0.51"},                                     # other date
          {"event_ticker": "KXNBAPTS-26NOV25MINOKC", "status": "active", "title": "Y: 5+ points", "floor_strike": 4.5,
           "yes_bid_dollars": "0", "yes_ask_dollars": "0.02"}]                                       # no bid: one-sided
    q = K.parse(ms, "2026-11-25", "PTS", "t")
    assert list(q) == [("bones hyland", "PTS", 14.5)]
    x = q[("bones hyland", "PTS", 14.5)]
    assert x["fair"] == 0.41 and x["over"] == K.american(0.42) and x["under"] == K.american(0.60)


def test_src_label_lists_exchanges():
    from nbaprops.app.props import src_label
    assert src_label({"fair_src": "Unabated", "sources": ["DraftKings", "Kalshi (direct)", "Novig"]}) == "Unabated · Kalshi, Novig"
    assert src_label({"fair_src": "Unabated", "sources": ["DraftKings", "Caesars"]}) == "Unabated"
