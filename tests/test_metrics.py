"""Tests for the brand-presence metrics collector (offline via httpx MockTransport)."""

from __future__ import annotations

import httpx

from content_engine.metrics import (
    MetricsStore,
    collect_bluesky,
    collect_github,
    collect_outreach,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_store_upserts_same_day():
    store = MetricsStore(":memory:")
    store.record("2026-07-08", "bluesky", {"followers": 5})
    store.record("2026-07-08", "bluesky", {"followers": 7})  # same day -> overwrite
    rows = store.latest()
    assert len(rows) == 1 and rows[0]["value"] == 7


def test_collect_bluesky_parses_followers():
    def handler(req):
        return httpx.Response(200, json={"followersCount": 42, "followsCount": 3, "postsCount": 9})
    out = collect_bluesky(_client(handler), "someone.bsky.social")
    assert out["followers"] == 42 and out["following"] == 3


def test_collect_github_sums_stars():
    def handler(req):
        if req.url.path.endswith("/users/RenoLu"):
            return httpx.Response(200, json={"followers": 10})
        return httpx.Response(200, json={"stargazers_count": 3})
    out = collect_github(_client(handler), "RenoLu", ["a", "b"])
    assert out["followers"] == 10
    assert out["stars.total"] == 6  # 3 + 3


def test_collectors_are_best_effort_on_failure():
    def handler(req):
        return httpx.Response(500)
    assert collect_bluesky(_client(handler), "x") == {}
    # github returns {} for followers but still no stars on 500
    assert "stars.total" not in collect_github(_client(handler), "RenoLu", ["a"])


def test_collect_outreach_counts_executed(tmp_path):
    import sqlite3
    db = tmp_path / "outreach.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE engagement_log (platform TEXT, target_key TEXT,
                    action_type TEXT, status TEXT)""")
    conn.executemany("INSERT INTO engagement_log VALUES (?,?,?,?)", [
        ("bluesky", "1", "like", "executed"),
        ("bluesky", "2", "like", "executed"),
        ("bluesky", "3", "like", "dry_run"),
        ("bluesky", "4", "follow", "executed"),
    ])
    conn.commit(); conn.close()
    out = collect_outreach(db)
    assert out["executed.like"] == 2 and out["executed.follow"] == 1
