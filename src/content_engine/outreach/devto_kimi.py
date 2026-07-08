"""DEV.to engagement via Kimi WebBridge (LOCAL ONLY, EXPERIMENTAL).

DEV.to's public API can read and publish articles but exposes no endpoint to
react, comment, or follow, so the API adapter (`devto.py`) is discover-only.
The logged-in site, however, supports all three, so this runner drives the
user's real browser session through Kimi to actually engage. It must run
locally, never in CI.

Discovery still uses the free JSON API (`DevtoAdapter.discover`) because it is
structured and cheap; only the write actions go through the browser. It reuses
the same safety funnel as every other adapter: dedupe (OutreachStore) -> daily
cap -> reply quality gate -> dry-run -> act.

DOM notes (dev.to article page):
- reactions are buttons ``#reaction-butt-like`` / ``-unicorn`` / ``-fire`` etc.
- the author follow button has aria-label ``Follow user: <Display Name>``; the
  first such button on the page is the article author (it renders above the
  comments), so we target that.
- the comment box is ``#text-area`` inside ``form#new_comment``. It resists the
  daemon's native fill, so we set its value via the prototype setter + an input
  event, then click the form's Submit button.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from ..logging_setup import get_logger
from .commenter import Commenter, ReplyRejected
from .config import OutreachConfig
from .devto import DevtoAdapter
from .linkedin_kimi import KimiBridge
from .models import ActionResult, ActionType, Target
from .store import OutreachStore

log = get_logger(__name__)

_SESSION = "devto-outreach"

# Click the heart/like reaction. Returns "ok" if it is (now) pressed.
_LIKE_JS = r"""
(()=>{
  const b=document.querySelector('#reaction-butt-like');
  if(!b) return "no_like_button";
  if(b.getAttribute('aria-pressed')==='true') return "ok";  // already liked
  b.click();
  return "ok";
})()
"""

# Follow the article author (first "Follow user:" button, which sits above the
# comment thread). Returns "ok" once clicked, or "already" if already following.
_FOLLOW_JS = r"""
(()=>{
  const b=[...document.querySelectorAll('button[aria-label^="Follow user:"]')][0];
  if(!b) return "no_follow_button";
  const label=(b.innerText||'').trim().toLowerCase();
  if(label==='following') return "already";
  b.click();
  return "ok";
})()
"""


def _comment_js(text: str) -> str:
    """Set the comment textarea value (React-safe) and submit the form."""
    return r"""
(()=>{
  const t=document.querySelector('#text-area');
  if(!t) return "no_textarea";
  t.focus();
  const set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
  set.call(t, %s);
  t.dispatchEvent(new Event('input',{bubbles:true}));
  t.dispatchEvent(new Event('change',{bubbles:true}));
  const f=document.querySelector('#new_comment');
  const btn=f?[...f.querySelectorAll('button')].find(x=>/^submit$/i.test((x.innerText||'').trim())&&!x.disabled):null;
  if(!btn) return "no_submit";
  btn.click();
  return "sent";
})()
""" % json.dumps(text)


class DevtoKimiRunner:
    """Runs DEV.to engagement through Kimi. Discovery is via the API; likes,
    comments, and follows go through the logged-in browser and honor dry-run,
    caps, dedupe, and the reply quality gate."""

    name = "devto"

    def __init__(self, config: OutreachConfig, store: OutreachStore,
                 commenter: Commenter | None, adapter: DevtoAdapter | None = None,
                 kimi: KimiBridge | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.config = config
        self.store = store
        self.commenter = commenter
        self.adapter = adapter or DevtoAdapter(config.settings, config)
        self.kimi = kimi or KimiBridge(session=_SESSION)
        self._sleeper = sleeper

    def discover(self) -> list[Target]:
        return self.adapter.discover(self.config.queries, self.config.per_query_limit)

    def _remaining(self, at: ActionType) -> int:
        cap = self.config.caps_for(self.name).for_action(at.value)
        return cap - self.store.count_today(self.name, at.value)

    def _act(self, target: Target, at: ActionType, comment: str = "") -> ActionResult:
        if not self.config.is_live:
            status = "pending_approval" if self.config.approval else "dry_run"
            return ActionResult(self.name, at, target.key, status, detail=comment or None)
        try:
            if at == ActionType.LIKE:
                out = self.kimi.evaluate(_LIKE_JS)
            elif at == ActionType.FOLLOW:
                out = self.kimi.evaluate(_FOLLOW_JS)
                if out == "already":
                    return ActionResult(self.name, at, target.key, "skipped", detail="already following")
            elif at == ActionType.REPLY:
                out = self.kimi.evaluate(_comment_js(comment))
                out = "ok" if out == "sent" else out
            else:  # pragma: no cover
                out = "unsupported"
            if out == "ok":
                return ActionResult(self.name, at, target.key, "executed", url=target.url)
            return ActionResult(self.name, at, target.key, "failed", error=str(out))
        except Exception as exc:  # noqa: BLE001 - contain everything
            return ActionResult(self.name, at, target.key, "failed", error=f"{type(exc).__name__}: {exc}")

    def run(self) -> dict:
        if not self.config.enabled:
            return {"platform": self.name, "enabled": False}
        if not self.kimi.healthy():
            return {"platform": self.name, "error": "kimi WebBridge not healthy"}

        results: list[ActionResult] = []
        for target in self.discover():
            if all(self._remaining(at) <= 0 for at in ActionType):
                break
            if self.store.already_done(self.name, target.key, "like"):
                continue

            # open the article once; all actions run on this page
            self.kimi.navigate(target.url, new_tab=False)
            self._sleeper(3.0)
            did = False

            if self._remaining(ActionType.LIKE) > 0:
                res = self._act(target, ActionType.LIKE)
                self.store.record(res, author=target.author_handle)
                results.append(res)
                did = did or res.status == "executed"
                if res.status == "executed":
                    self._sleeper(2.0)

            if self._remaining(ActionType.REPLY) > 0 and self.commenter and target.text.strip():
                try:
                    comment = self.commenter.generate(
                        platform="DEV.to", text=target.text, author=target.author_handle)
                except (ReplyRejected, Exception):  # noqa: BLE001 - gate or model outage
                    comment = ""
                if comment:
                    res = self._act(target, ActionType.REPLY, comment=comment)
                    self.store.record(res, comment=comment, author=target.author_handle)
                    results.append(res)
                    did = did or res.status == "executed"
                    if res.status == "executed":
                        self._sleeper(3.0)

            if did and self._remaining(ActionType.FOLLOW) > 0:
                res = self._act(target, ActionType.FOLLOW)
                self.store.record(res, author=target.author_handle)
                results.append(res)
                self._sleeper(2.0)

        self.kimi.close_session()

        from collections import defaultdict
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in results:
            counts[r.action_type.value][r.status] += 1
        return {"platform": self.name, "mode": self.config.mode,
                "actions": {k: dict(v) for k, v in counts.items()},
                "sample": [{"action": r.action_type.value, "status": r.status,
                            "target": r.target_key, "url": r.url} for r in results[:10]]}
