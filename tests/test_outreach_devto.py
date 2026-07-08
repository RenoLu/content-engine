"""Tests for the DEV.to/Kimi engagement runner (offline via a fake Kimi bridge)."""

from __future__ import annotations

import dataclasses

from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.devto_kimi import DevtoKimiRunner
from content_engine.outreach.models import ActionType, Target
from content_engine.outreach.store import OutreachStore


class FakeModel:
    def complete(self, **kwargs):
        return "The eval-debt point is the one I keep hitting; how do you catch drift after a quant swap?"


class FakeKimi:
    def __init__(self):
        self.actions = []

    def healthy(self):
        return True

    def navigate(self, url, new_tab=False):
        self.actions.append(("nav", url))
        return {}

    def evaluate(self, code):
        if "#reaction-butt-like" in code:
            self.actions.append(("like", None)); return "ok"
        if 'Follow user:' in code:
            self.actions.append(("follow", None)); return "ok"
        if "#text-area" in code:
            self.actions.append(("comment", None)); return "sent"
        return "ok"

    def close_session(self):
        pass


def _cfg(settings, **over):
    base = load_outreach_config(settings)
    return dataclasses.replace(base, platforms=["devto"], reply_ratio=1.0, like_ratio=1.0, **over)


def _targets(n):
    return [Target(platform="devto", key=str(i), text=f"A real article about MLOps eval debt number {i}.",
                   url=f"https://dev.to/a/post-{i}", author_id=f"u{i}", author_handle=f"user{i}")
            for i in range(n)]


class _Runner(DevtoKimiRunner):
    """Override discovery so tests don't hit the network."""
    def __init__(self, *a, targets=None, **k):
        super().__init__(*a, **k)
        self._targets = targets or []
    def discover(self):
        return list(self._targets)


def _make(settings, kimi, targets, **over):
    from content_engine.outreach.commenter import Commenter
    cfg = _cfg(settings, **over)
    store = OutreachStore(":memory:")
    r = _Runner(cfg, store, Commenter(FakeModel(), cfg), kimi=kimi, sleeper=lambda s: None, targets=targets)
    return r, store


def test_devto_dry_run_never_acts(settings):
    kimi = FakeKimi()
    runner, _ = _make(settings, kimi, _targets(2), mode="dry_run")
    summary = runner.run()
    # navigation happens (to read), but no like/comment/follow are issued
    assert ("like", None) not in kimi.actions
    assert ("comment", None) not in kimi.actions
    assert summary["actions"]["like"]["dry_run"] >= 1


def test_devto_live_likes_comments_follows(settings):
    kimi = FakeKimi()
    runner, store = _make(settings, kimi, _targets(1), mode="live")
    runner.run()
    kinds = [a for a, _ in kimi.actions]
    assert "like" in kinds and "comment" in kinds and "follow" in kinds


def test_devto_dedupes_across_runs(settings):
    targets = _targets(1)
    kimi = FakeKimi()
    runner, store = _make(settings, kimi, targets, mode="live")
    runner.run()
    # second run on same store must skip the already-liked article
    kimi2 = FakeKimi()
    from content_engine.outreach.commenter import Commenter
    cfg = _cfg(settings, mode="live")
    runner2 = _Runner(cfg, store, Commenter(FakeModel(), cfg), kimi=kimi2, sleeper=lambda s: None, targets=targets)
    runner2.run()
    assert ("like", None) not in kimi2.actions


def test_devto_reports_kimi_down(settings):
    class Down(FakeKimi):
        def healthy(self): return False
    runner, _ = _make(settings, Down(), _targets(1), mode="live")
    out = runner.run()
    assert "error" in out
