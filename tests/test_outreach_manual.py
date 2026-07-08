"""Tests for the agent-as-model outreach path (ManualReplyClient + replay).

Offline: a fake adapter stands in for the network. The point is that hand-authored
replies flow through the REAL commenter gate, caps, and dedupe exactly as an API
model's replies would -- only the text source changes.
"""

from __future__ import annotations

import dataclasses

from content_engine.outreach.base import BaseAdapter
from content_engine.outreach.commenter import Commenter
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.engine import OutreachEngine
from content_engine.outreach.manual_model import ManualReplyClient
from content_engine.outreach.models import ActionResult, ActionType, Target
from content_engine.outreach.store import OutreachStore


class FakeAdapter(BaseAdapter):
    name = "bluesky"  # reuse a real cap profile

    def __init__(self, *args, targets=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._targets = targets or []
        self.executed: list[tuple[str, str]] = []

    def is_configured(self):
        return True

    def discover(self, queries, limit):
        return list(self._targets)

    def _do_like(self, target):
        self.executed.append(("like", target.key))
        return self._result(target, ActionType.LIKE, "executed", url=target.url)

    def _do_follow(self, target):
        self.executed.append(("follow", target.key))
        return self._result(target, ActionType.FOLLOW, "executed")

    def _do_reply(self, target, comment):
        self.executed.append(("reply", target.key))
        return self._result(target, ActionType.REPLY, "executed", url="https://x/y")


def make_targets(n):
    return [
        Target(platform="bluesky", key=f"at://post/{i}",
               text=f"A detailed post number {i} about streaming data pipelines and backpressure handling.",
               url=f"https://bsky.app/p/{i}", author_id=f"did:{i}", author_handle=f"user{i}",
               uri=f"at://post/{i}", cid=f"cid{i}")
        for i in range(n)
    ]


def _cfg(settings, **over):
    base = load_outreach_config(settings)
    d = dict(platforms=["bluesky"], like_ratio=1.0, reply_ratio=1.0, seed=1)
    d.update(over)
    return dataclasses.replace(base, **d)


def _reply_prompt(text: str) -> str:
    # mimic how Commenter embeds the post text in the model prompt
    return f"TASK: OUTREACH REPLY\nPOST TEXT:\n\"\"\"\n{text}\n\"\"\"\n"


def test_manual_client_matches_by_text_snippet():
    text = "A detailed post about streaming backpressure and windowed joins in Bytewax."
    client = ManualReplyClient({text: "Windowed joins under backpressure are the tricky bit; how do you bound state growth?"})
    out = client.complete(system="s", prompt=_reply_prompt(text))
    assert "Windowed joins" in out


def test_manual_client_returns_empty_when_unknown():
    client = ManualReplyClient({"some other post entirely": "irrelevant reply"})
    out = client.complete(system="s", prompt=_reply_prompt("a post we never authored a reply for"))
    assert out == ""


def test_manual_client_ignores_blank_reply():
    client = ManualReplyClient({"a real post here": ""})
    assert client._by_snippet == {}


def test_commenter_gate_runs_on_manual_reply(settings):
    """A weak hand-authored reply is still rejected by the real gate."""
    cfg = _cfg(settings)
    text = "A detailed post about Iceberg compaction strategies for small files."
    client = ManualReplyClient({text: "great post"})  # generic -> must be gated out
    commenter = Commenter(client, cfg)
    import pytest
    from content_engine.outreach.commenter import ReplyRejected
    with pytest.raises(ReplyRejected):
        commenter.generate(platform="Bluesky", text=text, author="someone")


def test_replay_posts_authored_replies_live(settings):
    """End to end: dumped targets + authored replies -> real like + reply executed."""
    targets = make_targets(2)
    replies_by_text = {t.text: f"Solid point on target {i}; how does it hold up when the stream stalls?"
                       for i, t in enumerate(targets)}
    client = ManualReplyClient(replies_by_text)

    cfg = _cfg(settings, mode="live")
    store = OutreachStore(":memory:")
    engine = OutreachEngine(cfg, store, sleeper=lambda s: None, model_client=client)
    adapter = FakeAdapter(cfg.settings, cfg, targets=targets)

    results: list[ActionResult] = []
    for t in targets:
        engine._engage_target(adapter, t, results)

    acted = {(a, k) for a, k in adapter.executed}
    assert any(a == "reply" for a, _ in acted)   # authored replies went out
    assert any(a == "like" for a, _ in acted)    # likes need no model

    # dedupe: a second replay must not re-like or re-reply the same targets
    adapter2 = FakeAdapter(cfg.settings, cfg, targets=targets)
    engine2 = OutreachEngine(cfg, store, sleeper=lambda s: None, model_client=client)
    results2: list[ActionResult] = []
    for t in targets:
        engine2._engage_target(adapter2, t, results2)
    assert adapter2.executed == []


def test_replay_skips_targets_without_authored_reply(settings):
    """A target with no authored reply still gets liked; its reply is skipped."""
    targets = make_targets(1)
    client = ManualReplyClient({})  # no replies authored at all
    cfg = _cfg(settings, mode="live")
    store = OutreachStore(":memory:")
    engine = OutreachEngine(cfg, store, sleeper=lambda s: None, model_client=client)
    adapter = FakeAdapter(cfg.settings, cfg, targets=targets)

    results: list[ActionResult] = []
    engine._engage_target(adapter, targets[0], results)

    kinds = [a for a, _ in adapter.executed]
    assert "like" in kinds
    assert "reply" not in kinds
    reply_res = [r for r in results if r.action_type == ActionType.REPLY]
    assert reply_res and reply_res[0].status == "skipped"


def test_looks_like_bot_filters_automation():
    from content_engine.outreach.quality import looks_like_bot
    # bots / feeds by handle
    assert looks_like_bot("arxiv-daily-bot.bsky.social", "a paper")
    assert looks_like_bot("some-rss-feeds.bsky.social", "new post")
    assert looks_like_bot("hn-frontpage-bot.bsky.social", "launched")
    # promo / job blasts by text
    assert looks_like_bot("educativ.bsky.social", "DCEO Engineer Job educativ.net/jobs/job/1")
    assert looks_like_bot("techinsightsinc.bsky.social", "Register 🔗 bit.ly/xyz")
    # genuine humans survive
    assert not looks_like_bot("themysteryinc.bsky.social",
                              "I build real time optimization and usage tracking")
    assert not looks_like_bot("jane.dev", "we moved ingestion to Bytewax and cut latency")
