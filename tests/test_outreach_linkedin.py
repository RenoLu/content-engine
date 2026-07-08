"""Tests for the LinkedIn/Kimi outreach runner (offline via a fake Kimi bridge)."""

from __future__ import annotations

import dataclasses
import json
import re

from content_engine.outreach.commenter import Commenter
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.linkedin_kimi import LinkedInKimiRunner, _clean_post_text
from content_engine.outreach.store import OutreachStore


class FakeModel:
    def complete(self, **kwargs):
        return "Interesting point about batching the writes; how does it hold up under bursty ingest?"


class FakeKimi:
    def __init__(self, posts):
        self._posts = posts
        self.actions = []

    def healthy(self):
        return True

    def navigate(self, url, new_tab=False):
        return {}

    def evaluate(self, code):
        if "JSON.stringify(posts" in code:            # the extract JS
            return json.dumps(self._posts)
        m = re.search(r'const KIND="(\w+)"', code)     # an action JS
        if m:
            kind = m.group(1)
            self.actions.append(kind)
            return "opened" if kind == "reply" else "ok"
        return "ok"

    def _cmd(self, action, args):
        self.actions.append((action, args.get("value", "")[:20]))
        return {}

    def close_session(self):
        pass


POSTS = [
    {"i": 0, "author": "Dana Dev", "text": "Feed post Dana Dev • 3rd+ Data Engineer • Follow We moved our ingestion to Bytewax and cut latency in half.", "canFollow": True},
    {"i": 1, "author": "Sam Stack", "text": "Feed post Sam Stack • 2nd • Follow Thoughts on Iceberg vs Delta for a small team?", "canFollow": True},
]


def _cfg(settings, **over):
    base = load_outreach_config(settings)
    return dataclasses.replace(base, platforms=["linkedin"], reply_ratio=1.0, like_ratio=1.0, **over)


def test_clean_post_text_strips_chrome():
    out = _clean_post_text("Feed post Dana Dev • 3rd+ Data Engineer • Follow We moved to Bytewax.")
    assert "Feed post" not in out and "We moved to Bytewax" in out


def test_linkedin_dry_run_never_acts(settings):
    cfg = _cfg(settings, mode="dry_run")
    store = OutreachStore(":memory:")
    kimi = FakeKimi(POSTS)
    runner = LinkedInKimiRunner(cfg, store, Commenter(FakeModel(), cfg), kimi=kimi, sleeper=lambda s: None)
    summary = runner.run()
    assert kimi.actions == []                          # nothing acted in dry-run
    assert "like" in summary["actions"]
    assert summary["actions"]["like"]["dry_run"] >= 1


def test_linkedin_live_acts_and_dedupes(settings):
    cfg = _cfg(settings, mode="live")
    store = OutreachStore(":memory:")
    kimi = FakeKimi(POSTS)
    runner = LinkedInKimiRunner(cfg, store, Commenter(FakeModel(), cfg), kimi=kimi, sleeper=lambda s: None)
    runner.run()
    assert "like" in kimi.actions                          # real like actions issued
    # a second run must not re-like the same posts (dedupe)
    kimi2 = FakeKimi(POSTS)
    runner2 = LinkedInKimiRunner(cfg, store, Commenter(FakeModel(), cfg), kimi=kimi2, sleeper=lambda s: None)
    runner2.run()
    assert "like" not in kimi2.actions


def test_linkedin_reports_kimi_down(settings):
    cfg = _cfg(settings, mode="live")

    class DownKimi(FakeKimi):
        def healthy(self):
            return False

    runner = LinkedInKimiRunner(cfg, OutreachStore(":memory:"), Commenter(FakeModel(), cfg),
                                kimi=DownKimi(POSTS), sleeper=lambda s: None)
    out = runner.run()
    assert "error" in out
