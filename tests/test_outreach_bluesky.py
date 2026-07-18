

def test_discovery_skips_zero_reach_and_ranks_by_engagement(settings, monkeypatch):
    """Replies are capped, so they must land where there is an audience. Posts
    below the reach floor (nobody saw them) and above the ceiling (viral
    general-audience threads) are dropped, and the rest come back hottest-first."""
    import dataclasses
    from content_engine.outreach.bluesky import BlueskyAdapter
    from content_engine.outreach.config import load_outreach_config

    cfg = dataclasses.replace(load_outreach_config(settings), min_likes=5, max_likes=1000)

    def post(uri, likes):
        return {"uri": uri, "cid": "c", "likeCount": likes, "repostCount": 0,
                "replyCount": 0, "record": {"text": "a post about agents"},
                "author": {"did": "d" + uri, "handle": "h" + uri}}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"posts": [post("dead", 0), post("viral", 5000),
                              post("good", 40), post("better", 300)]}

    a = BlueskyAdapter(settings, cfg)
    monkeypatch.setattr(a, "_auth", lambda: ("jwt", "me"))
    monkeypatch.setattr(a, "is_configured", lambda: True)
    monkeypatch.setattr(a, "client", lambda: type("C", (), {"get": lambda *x, **k: Resp()})())

    keys = [t.key for t in a.discover(["agents"], 25)]
    assert keys == ["better", "good"]      # ranked by reach, band enforced
