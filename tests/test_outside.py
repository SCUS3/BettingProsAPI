from nbaprops.rules import outside as O


def test_parse_card_lines():
    text = """Jalen Brunson Over 27.5 Points -115
Brunson PTS o27.5 (-115)
LaMelo Ball 3PM u3.5
Jazz @ Lakers Over 228.5 -110
Utah Jazz vs Lakers total o228.5"""
    picks, skipped = O.parse(text)
    assert [(p["player"], p["market"], p["side"], p["line"], p["odds"]) for p in picks] == [
        ("Jalen Brunson", "PTS", "over", 27.5, -115.0), ("Brunson", "PTS", "over", 27.5, -115.0), ("LaMelo Ball", "3PM", "under", 3.5, None)]
    assert len(skipped) == 2                       # game totals skipped
