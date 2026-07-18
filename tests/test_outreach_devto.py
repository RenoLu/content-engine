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
    """Stands in for the browser. Models the real comment flow: focus, trusted
    typing, submit, then the post-submit verification read."""

    def __init__(self, logged_in=True):
        self.actions = []
        self.logged_in = logged_in
        self.typed = ""

    def healthy(self):
        return True

    def navigate(self, url, new_tab=False):
        self.actions.append(("nav", url))
        return {}

    def key_type(self, text):
        self.actions.append(("type", text))
        self.typed += text
        return {}

    def evaluate(self, code):
        if "user-profile-dropdown" in code:
            return "yes" if self.logged_in else "no"
        if "#reaction-butt-like" in code:
            self.actions.append(("like", None)); return "ok"
        if 'Follow user:' in code:
            self.actions.append(("follow", None)); return "ok"
        if "scrollIntoView" in code:                      # focus comment box
            self.actions.append(("focus", None)); return '{"ok":true}'
        if "return t?(t.value||'').length:-1" in code:    # typed-length probe
            return len(self.typed)
        if "#new_comment" in code:                        # submit
            self.actions.append(("comment", self.typed)); return '{"ok":true,"len":%d}' % len(self.typed)
        if "document.body.innerText" in code:             # verify it landed
            return "present"
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


def test_devto_refuses_when_logged_out(settings):
    """Logged out, DEV.to still renders the comment box and reaction buttons and
    they silently no-op, so the lane must refuse rather than report success."""
    kimi = FakeKimi(logged_in=False)
    runner, _ = _make(settings, kimi, _targets(2), mode="live")
    summary = runner.run()
    assert summary["error"] == "not logged in to DEV.to"
    kinds = [a for a, _ in kimi.actions]
    assert "like" not in kinds and "comment" not in kinds and "follow" not in kinds


def test_devto_reply_fails_when_comment_not_on_page(settings):
    """A click on Submit is not proof. If the comment is not visible afterwards
    the action must be recorded failed, never executed."""
    kimi = FakeKimi()
    kimi.evaluate_absent = True
    real_eval = kimi.evaluate

    def evaluate(code):
        if "document.body.innerText" in code:
            return "absent"
        return real_eval(code)

    kimi.evaluate = evaluate
    runner, store = _make(settings, kimi, _targets(1), mode="live")
    summary = runner.run()
    assert summary["actions"]["reply"] == {"failed": 1}


def test_devto_reply_fails_when_keystrokes_do_not_land(settings):
    """If trusted typing did not reach the box, submitting would post nothing."""
    kimi = FakeKimi()
    kimi.key_type = lambda text: kimi.actions.append(("type", ""))  # swallow input
    runner, store = _make(settings, kimi, _targets(1), mode="live")
    summary = runner.run()
    assert summary["actions"]["reply"] == {"failed": 1}
    assert ("comment", None) not in kimi.actions
