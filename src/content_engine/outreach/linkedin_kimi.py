"""LinkedIn engagement via Kimi WebBridge (LOCAL ONLY, EXPERIMENTAL).

LinkedIn has no sanctioned engagement API and actively penalizes automation, so
this adapter drives the user's REAL logged-in browser session through the Kimi
WebBridge daemon (http://127.0.0.1:10086). It must run locally, never in CI, and
the first live run should be supervised.

It reuses the same safety funnel as the API adapters:
    dedupe (OutreachStore) -> daily cap -> reply quality gate -> dry-run -> act

LinkedIn's search DOM has no stable per-post URN in results, so a target's dedupe
key is a hash of author + post text. Actions target the Nth post via injected JS
(like button = aria-label "Reaction button state: no reaction"; Comment button =
aria-label "Comment"; Follow = aria-label "Follow <author>").

Caps for LinkedIn are the lowest of any platform on purpose (see config). This is
the platform most likely to flag automation, so start tiny and only raise if the
account stays clean.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Callable

import httpx

from ..logging_setup import get_logger
from .commenter import Commenter, ReplyRejected
from .config import OutreachConfig
from .models import ActionResult, ActionType, Target
from .store import OutreachStore

log = get_logger(__name__)

_DAEMON = "http://127.0.0.1:10086/command"
_SESSION = "li-outreach"


class KimiBridge:
    """Minimal client for the Kimi WebBridge daemon."""

    def __init__(self, base: str = _DAEMON, session: str = _SESSION,
                 client: httpx.Client | None = None):
        self.base = base
        self.session = session
        self._client = client or httpx.Client(timeout=45.0)

    def _cmd(self, action: str, args: dict) -> dict:
        resp = self._client.post(self.base, json={"action": action, "args": args,
                                                  "session": self.session})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok", False):
            raise RuntimeError(f"kimi {action} failed: {data.get('error')}")
        return data.get("data", {})

    def healthy(self) -> bool:
        try:
            # a no-op evaluate confirms daemon + extension are live
            self._cmd("evaluate", {"code": "1"})
            return True
        except Exception:
            return False

    def navigate(self, url: str, new_tab: bool = False) -> dict:
        return self._cmd("navigate", {"url": url, "newTab": new_tab,
                                      "group_title": "LinkedIn outreach"})

    def evaluate(self, code: str) -> object:
        return self._cmd("evaluate", {"code": code}).get("value")

    def key_type(self, text: str) -> dict:
        """Type via trusted CDP key events. Rich editors (DEV.to's comment box,
        LinkedIn's composer) ignore a synthetic value write because it never
        reaches the framework's state, so real keystrokes are the only way in."""
        return self._cmd("key_type", {"text": text})

    def mouse_click(self, x: float, y: float) -> dict:
        """Trusted click at viewport coords, for focusing an editor that ignores
        el.focus()."""
        return self._cmd("mouse_click", {"x": x, "y": y})

    def close_session(self) -> None:
        try:
            self._cmd("close_session", {})
        except Exception:
            pass


def _key(author: str, text: str) -> str:
    return "li:" + hashlib.sha256(f"{author}|{text[:200]}".encode()).hexdigest()[:20]


import re as _re

# Per Yan's rule, LinkedIn engagement targets strangers only: people he does NOT
# already follow or know (1st-degree connections), and nobody currently at a
# company he has worked for. Former employers come from the job-app exclude list.
_FORMER_EMPLOYERS = (
    "grayscale", "susquehanna", "sig ", "(sig)", "fis ", "fidelity national",
    "checkpoint systems", "penn medicine", "university of pennsylvania", "upenn",
)

_FIRST_DEGREE_RE = _re.compile(r"(^|[^0-9])1st([^a-z0-9]|$)", _re.IGNORECASE)


def _exclusion_reason(row: dict) -> str | None:
    """Return why a LinkedIn author is off-limits, or None if fair game.

    Skips people already followed/connected and anyone at a former employer, so
    engagement only reaches new, unknown people (per Yan's LinkedIn rule)."""
    if not row.get("canFollow"):
        return "already following or connected"
    actor = (row.get("actor") or "").lower()
    if _FIRST_DEGREE_RE.search(actor):
        return "1st-degree connection"
    for emp in _FORMER_EMPLOYERS:
        if emp in actor:
            return f"former employer ({emp.strip()})"
    return None

# LinkedIn search cards prefix the body with chrome like "Feed post <Author> •
# 3rd+ <headline> • Follow ". Strip that so the reply model sees the actual post.
_CHROME_RE = _re.compile(r"^Feed post .*?•.*?(?:•\s*Follow\s*)?", _re.IGNORECASE)


def _clean_post_text(text: str) -> str:
    t = _re.sub(r"\s+", " ", text or "").strip()
    t = _CHROME_RE.sub("", t, count=1).strip()
    # drop trailing engagement chrome
    t = _re.sub(r"(Activate to view larger image.*|\d+ (comments?|reposts?).*)$", "", t).strip()
    return t


# JS run in the page to extract the visible posts as structured data. Kept as a
# string so it can be tuned without touching Python. Returns a JSON array.
_EXTRACT_JS = r"""
(()=>{
  const posts=[];
  const likeBtns=[...document.querySelectorAll('button[aria-label="Reaction button state: no reaction"]')];
  likeBtns.forEach((btn,i)=>{
    // climb to the post container (a few levels up holds the whole card)
    let el=btn; for(let k=0;k<12&&el;k++){ if(el.querySelector&&el.querySelector('button[aria-label^="Follow "]')) break; el=el.parentElement; }
    const card=el||btn.closest('div');
    const followBtn=card?card.querySelector('button[aria-label^="Follow "]'):null;
    let author='';
    const ctrl=card?card.querySelector('button[aria-label^="Open control menu for post by "]'):null;
    if(ctrl){const m=(ctrl.getAttribute('aria-label')||'').match(/post by (.+)$/);author=m?m[1]:'';}
    else if(followBtn){author=(followBtn.getAttribute('aria-label')||'').replace(/^Follow /,'');}
    const textEl=card?card.querySelector('.update-components-text, .feed-shared-update-v2__description, span[dir="ltr"]'):null;
    const text=textEl?(textEl.innerText||'').replace(/\s+/g,' ').trim():(card?(card.innerText||'').replace(/\s+/g,' ').slice(0,400):'');
    // the actor block carries the connection degree (1st/2nd/3rd+) and headline,
    // which we use to skip connections and former-employer colleagues
    const actorEl=card?card.querySelector('.update-components-actor__meta, .update-components-actor'):null;
    const actor=actorEl?(actorEl.innerText||'').replace(/\s+/g,' ').trim():'';
    posts.push({i, author, text, canFollow: !!followBtn, actor});
  });
  return JSON.stringify(posts.slice(0,25));
})()
"""


def _action_js(ordinal: int, kind: str) -> str:
    """JS performing one action on the Nth post (indexed by its like-button).

    Returns "ok" for a completed like/follow, "opened" after opening a comment
    box (the reply text is filled in a second step), or an error token.
    """
    return r"""
(()=>{
  const KIND=%s, N=%d;
  const likeBtns=[...document.querySelectorAll('button[aria-label="Reaction button state: no reaction"]')];
  const btn=likeBtns[N];
  if(!btn) return "no_post";
  let el=btn; for(let k=0;k<12&&el;k++){ if(el.querySelector&&el.querySelector('button[aria-label="Comment"]')) break; el=el.parentElement; }
  const card=el||btn.closest('div');
  if(KIND==="like"){ btn.click(); return "ok"; }
  if(KIND==="follow"){ const f=card&&card.querySelector('button[aria-label^="Follow "]'); if(!f) return "no_follow"; f.click(); return "ok"; }
  if(KIND==="reply"){ const c=card&&card.querySelector('button[aria-label="Comment"]'); if(!c) return "no_comment_btn"; c.click(); return "opened"; }
  return "unknown";
})()
""" % (json.dumps(kind), ordinal)


class LinkedInKimiRunner:
    """Runs LinkedIn engagement through Kimi. Discovery is non-destructive; write
    actions honor dry-run/caps/dedupe and the reply quality gate."""

    name = "linkedin"

    def __init__(self, config: OutreachConfig, store: OutreachStore,
                 commenter: Commenter, kimi: KimiBridge | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.config = config
        self.store = store
        self.commenter = commenter
        self.kimi = kimi or KimiBridge()
        self._sleeper = sleeper

    def _search_url(self, query: str) -> str:
        from urllib.parse import quote
        return ("https://www.linkedin.com/search/results/content/"
                f"?keywords={quote(query)}&sortBy=%22date_posted%22")

    def discover(self, query: str) -> list[Target]:
        self.kimi.navigate(self._search_url(query), new_tab=True)
        self._sleeper(6.0)
        raw = self.kimi.evaluate(_EXTRACT_JS) or "[]"
        try:
            rows = json.loads(raw)
        except Exception:
            return []
        out = []
        for r in rows:
            text = _clean_post_text(r.get("text") or "")
            author = (r.get("author") or "").strip()
            if not text:
                continue
            reason = _exclusion_reason(r)
            if reason:
                log.info("linkedin: skip %s (%s)", author or "?", reason)
                continue
            out.append(Target(
                platform=self.name, key=_key(author, text), text=text,
                author_id=author, author_handle=author,
                extra={"ordinal": r.get("i"), "can_follow": r.get("canFollow", False)},
            ))
        return out

    def _remaining(self, at: ActionType) -> int:
        cap = self.config.caps_for(self.name).for_action(at.value)
        return cap - self.store.count_today(self.name, at.value)

    def _act(self, target: Target, at: ActionType, comment: str = "") -> ActionResult:
        if not self.config.is_live:
            status = "pending_approval" if self.config.approval else "dry_run"
            return ActionResult(self.name, at, target.key, status, detail=comment or None)
        ordinal = target.extra.get("ordinal")
        try:
            if at == ActionType.REPLY:
                res = self.kimi.evaluate(_action_js(ordinal, "reply"))
                if res != "opened":
                    return ActionResult(self.name, at, target.key, "failed", error=str(res))
                self._sleeper(1.5)
                # fill the comment editor and submit
                self.kimi._cmd("fill", {"selector": "div.ql-editor[contenteditable=true], div[role=textbox][contenteditable=true]", "value": comment})
                self._sleeper(1.0)
                self.kimi._cmd("evaluate", {"code": "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^(post|comment)$/i.test((x.innerText||'').trim())&&!x.disabled);if(b){b.click();return 'sent';}return 'no_submit';})()"})
                return ActionResult(self.name, at, target.key, "executed")
            outcome = self.kimi.evaluate(_action_js(ordinal, at.value))
            if outcome == "ok":
                return ActionResult(self.name, at, target.key, "executed")
            return ActionResult(self.name, at, target.key, "failed", error=str(outcome))
        except Exception as exc:  # noqa: BLE001 - contain everything
            return ActionResult(self.name, at, target.key, "failed", error=f"{type(exc).__name__}: {exc}")

    def run(self) -> dict:
        if not self.config.enabled:
            return {"platform": self.name, "enabled": False}
        if not self.kimi.healthy():
            return {"platform": self.name, "error": "kimi WebBridge not healthy"}

        results: list[ActionResult] = []
        seen: set[str] = set()
        for query in self.config.queries:
            if all(self._remaining(at) <= 0 for at in ActionType):
                break
            for target in self.discover(query):
                if target.key in seen or self.store.already_done(self.name, target.key, "like"):
                    continue
                seen.add(target.key)
                # LIKE
                if self._remaining(ActionType.LIKE) > 0:
                    res = self._act(target, ActionType.LIKE)
                    self.store.record(res, author=target.author_handle)
                    results.append(res)
                    if res.status == "executed":
                        self._sleeper(6.0)
                # REPLY (quality-gated)
                if self._remaining(ActionType.REPLY) > 0 and target.text:
                    try:
                        comment = self.commenter.generate(
                            platform="LinkedIn", text=target.text, author=target.author_handle)
                    except (ReplyRejected, Exception):
                        comment = ""
                    if comment:
                        res = self._act(target, ActionType.REPLY, comment=comment)
                        self.store.record(res, comment=comment, author=target.author_handle)
                        results.append(res)
                        if res.status == "executed":
                            self._sleeper(8.0)
        self.kimi.close_session()

        from collections import defaultdict
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in results:
            counts[r.action_type.value][r.status] += 1
        return {"platform": self.name, "mode": self.config.mode,
                "actions": {k: dict(v) for k, v in counts.items()},
                "sample": [{"action": r.action_type.value, "status": r.status,
                            "author": r.target_key} for r in results[:10]]}


def main(argv: list[str] | None = None) -> int:
    """Local-only entrypoint: ``python -m content_engine.outreach.linkedin_kimi``.

    Drives the user's real browser via Kimi WebBridge, so it CANNOT run in CI.
    Dry-run by default; a live run should be supervised (it acts on your real
    LinkedIn account, which penalizes automation).
    """
    import json as _json
    import sys as _sys

    from ..config import load_settings
    from ..agents.model_client import build_model_client
    from .config import load_outreach_config

    settings = load_settings()
    config = load_outreach_config(settings)
    store = OutreachStore(settings.project_root / "data" / "outreach.sqlite3")
    commenter = Commenter(build_model_client(settings), config)
    runner = LinkedInKimiRunner(config, store, commenter)
    log.info("linkedin outreach: mode=%s (Kimi/local only)", config.mode)
    summary = runner.run()
    store.close()
    print(_json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
