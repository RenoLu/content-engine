"""Tests for the outreach (engagement automation) subsystem.

All offline: a fake adapter and a fake model client stand in for the network and
the LLM, so we exercise the safety funnel (dry-run, caps, dedupe, approval,
quality gate) deterministically.
"""

from __future__ import annotations

import dataclasses

import pytest

from content_engine.outreach.base import BaseAdapter
from content_engine.outreach.commenter import Commenter, ReplyRejected
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.engine import OutreachEngine
from content_engine.outreach.models import Action, ActionResult, ActionType, Target
from content_engine.outreach.store import OutreachStore


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeModel:
    """Returns a canned reply; records prompts for assertion."""

    def __init__(self, reply="I like how you scoped the retry logic; did you compare it against a token-bucket approach?"):
        self.reply = reply
        self.calls = 0

    def complete(self, *, system, prompt, max_tokens=2000, temperature=0.4, json_mode=False):
        self.calls += 1
        return self.reply


class FakeAdapter(BaseAdapter):
    name = "bluesky"  # reuse a real cap profile

    def __init__(self, *args, targets=None, configured=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._targets = targets or []
        self._configured = configured
        self.executed: list[tuple[str, str]] = []  # (action, target_key)

    def is_configured(self):
        return self._configured

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
        Target(platform="bluesky", key=f"at://post/{i}", text=f"A detailed post number {i} about streaming data pipelines and backpressure handling.",
               url=f"https://bsky.app/p/{i}", author_id=f"did:{i}", author_handle=f"user{i}",
               uri=f"at://post/{i}", cid=f"cid{i}")
        for i in range(n)
    ]


def cfg(settings, **over):
    base = load_outreach_config(settings)
    # force everything on and deterministic for tests
    defaults = dict(platforms=["bluesky"], like_ratio=1.0, reply_ratio=1.0, seed=1)
    defaults.update(over)
    return dataclasses.replace(base, **defaults)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def test_store_dedupe_only_blocks_executed():
    store = OutreachStore(":memory:")
    t = make_targets(1)[0]
    # a dry-run row must not block a later real action
    store.record(ActionResult("bluesky", ActionType.LIKE, t.key, "dry_run"))
    assert store.already_done("bluesky", t.key, "like") is False
    store.record(ActionResult("bluesky", ActionType.LIKE, t.key, "executed"))
    assert store.already_done("bluesky", t.key, "like") is True


def test_store_count_today_counts_only_executed():
    store = OutreachStore(":memory:")
    store.record(ActionResult("bluesky", ActionType.LIKE, "a", "executed"))
    store.record(ActionResult("bluesky", ActionType.LIKE, "b", "dry_run"))
    assert store.count_today("bluesky", "like") == 1


# --------------------------------------------------------------------------- #
# Commenter quality gate
# --------------------------------------------------------------------------- #
def test_commenter_accepts_substantive_reply(settings):
    c = Commenter(FakeModel(), cfg(settings))
    out = c.generate(platform="bluesky", text="We rewrote our ingestion in Rust.", author="dev")
    assert "token-bucket" in out


@pytest.mark.parametrize("bad", ["Great post!", "So true 🔥", "amazing", "nice"])
def test_commenter_rejects_generic(settings, bad):
    c = Commenter(FakeModel(reply=bad), cfg(settings))
    with pytest.raises(ReplyRejected):
        c.generate(platform="bluesky", text="Some real content here.", author="dev")


def test_commenter_strips_urls_and_hashtags(settings):
    c = Commenter(FakeModel(reply="Neat approach to sharding the writes across nodes cleanly #dataeng https://spam.example"),
                  cfg(settings))
    out = c.generate(platform="bluesky", text="content", author="dev")
    assert "http" not in out and "#" not in out


def test_commenter_rejects_banned_phrase(settings):
    c = Commenter(FakeModel(reply="This is a real game changer for how teams ship data."),
                  cfg(settings))
    with pytest.raises(ReplyRejected):
        c.generate(platform="bluesky", text="content", author="dev")


# --------------------------------------------------------------------------- #
# Engine — dry-run, live, caps, dedupe, kill switch, approval
# --------------------------------------------------------------------------- #
def _engine_with(adapter, config, settings, store=None):
    """Build an engine whose registry yields our fake adapter."""
    store = store or OutreachStore(":memory:")
    engine = OutreachEngine(config, store, sleeper=lambda s: None, model_client=FakeModel())
    import content_engine.outreach.engine as eng_mod
    engine._orig_build = eng_mod.build_adapter
    eng_mod.build_adapter = lambda name, s, c, http=None: adapter
    return engine, store, eng_mod


def test_engine_dry_run_never_executes(settings):
    config = cfg(settings, mode="dry_run")
    adapter = FakeAdapter(settings, config, targets=make_targets(5))
    engine, store, eng_mod = _engine_with(adapter, config, settings)
    try:
        summary = engine.run()
    finally:
        eng_mod.build_adapter = engine._orig_build
    assert adapter.executed == []  # NOTHING hit the network
    acts = summary["platforms"]["bluesky"]["actions"]
    assert acts["like"]["dry_run"] >= 1


def test_engine_live_executes_and_dedupes(settings):
    config = cfg(settings, mode="live")
    targets = make_targets(3)
    adapter = FakeAdapter(settings, config, targets=targets)
    store = OutreachStore(":memory:")
    engine, store, eng_mod = _engine_with(adapter, config, settings, store=store)
    try:
        engine.run()
        first = list(adapter.executed)
        # a second run over the same targets must not repeat likes (dedupe)
        adapter.executed.clear()
        engine2 = OutreachEngine(config, store, sleeper=lambda s: None, model_client=FakeModel())
        engine2.run()
    finally:
        eng_mod.build_adapter = engine._orig_build
    assert any(a == "like" for a, _ in first)
    assert ("like", targets[0].key) not in adapter.executed  # deduped


def test_engine_respects_like_cap(settings):
    config = cfg(settings, mode="live", caps={**load_outreach_config(settings).caps})
    # shrink the bluesky like cap to 2
    caps = dict(config.caps)
    caps["bluesky"] = dataclasses.replace(caps["bluesky"], like=2)
    config = dataclasses.replace(config, caps=caps)
    adapter = FakeAdapter(settings, config, targets=make_targets(10))
    engine, store, eng_mod = _engine_with(adapter, config, settings)
    try:
        engine.run()
    finally:
        eng_mod.build_adapter = engine._orig_build
    likes = [a for a in adapter.executed if a[0] == "like"]
    assert len(likes) == 2


def test_engine_kill_switch(settings):
    config = cfg(settings, mode="live", enabled=False)
    adapter = FakeAdapter(settings, config, targets=make_targets(5))
    engine, store, eng_mod = _engine_with(adapter, config, settings)
    try:
        summary = engine.run()
    finally:
        eng_mod.build_adapter = engine._orig_build
    assert summary["enabled"] is False
    assert adapter.executed == []


def test_engine_approval_mode_drafts_not_executes(settings):
    config = cfg(settings, mode="live", approval=True)
    adapter = FakeAdapter(settings, config, targets=make_targets(4))
    engine, store, eng_mod = _engine_with(adapter, config, settings)
    try:
        summary = engine.run()
    finally:
        eng_mod.build_adapter = engine._orig_build
    assert adapter.executed == []  # approval mode never acts
    acts = summary["platforms"]["bluesky"]["actions"]
    assert acts["like"]["pending_approval"] >= 1
