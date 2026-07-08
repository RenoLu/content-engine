"""Mastodon engagement adapter.

Reuses the same bearer-token auth as the Mastodon publisher.
  * discover -> GET /api/v2/search?type=statuses (plus tag timelines)
  * like     -> POST /api/v1/statuses/:id/favourite
  * follow   -> POST /api/v1/accounts/:id/follow
  * reply    -> POST /api/v1/statuses  with in_reply_to_id
"""

from __future__ import annotations

import re

from .base import BaseAdapter
from .models import ActionResult, ActionType, Target

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub(" ", html or "").replace("&amp;", "&").strip()


class MastodonAdapter(BaseAdapter):
    name = "mastodon"

    def _base(self) -> str:
        return self.settings.get_env("MASTODON_BASE_URL").rstrip("/")

    def _token(self) -> str:
        return self.settings.get_env("MASTODON_ACCESS_TOKEN")

    def is_configured(self) -> bool:
        return bool(self._base()) and bool(self._token())

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    # ---- discovery -------------------------------------------------------
    def discover(self, queries: list[str], limit: int) -> list[Target]:
        if not self.is_configured():
            return []
        out: list[Target] = []
        seen: set[str] = set()
        me = self._verify_id()
        for q in queries:
            statuses = self._search_statuses(q, limit) or self._tag_timeline(q, limit)
            for s in statuses:
                sid = str(s.get("id", ""))
                acct = s.get("account", {}) or {}
                aid = str(acct.get("id", ""))
                if not sid or sid in seen or (me and aid == me):
                    continue
                seen.add(sid)
                out.append(Target(
                    platform=self.name,
                    key=sid,
                    text=_strip_html(s.get("content", "")),
                    url=s.get("url") or s.get("uri", ""),
                    author_id=aid,
                    author_handle=acct.get("acct", ""),
                ))
        return out

    def _verify_id(self) -> str:
        try:
            resp = self.client().get(
                f"{self._base()}/api/v1/accounts/verify_credentials",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return str(resp.json().get("id", ""))
        except Exception:
            return ""

    def _search_statuses(self, q: str, limit: int) -> list[dict]:
        try:
            resp = self.client().get(
                f"{self._base()}/api/v2/search",
                headers=self._headers(),
                params={"q": q, "type": "statuses", "limit": min(limit, 20)},
            )
            resp.raise_for_status()
            return resp.json().get("statuses", []) or []
        except Exception:
            return []

    def _tag_timeline(self, q: str, limit: int) -> list[dict]:
        tag = re.sub(r"[^a-z0-9]", "", q.lower())
        if not tag:
            return []
        try:
            resp = self.client().get(
                f"{self._base()}/api/v1/timelines/tag/{tag}",
                headers=self._headers(),
                params={"limit": min(limit, 20)},
            )
            resp.raise_for_status()
            return resp.json() or []
        except Exception:
            return []

    # ---- actions ---------------------------------------------------------
    def _do_like(self, target: Target) -> ActionResult:
        resp = self.client().post(
            f"{self._base()}/api/v1/statuses/{target.key}/favourite",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return self._result(target, ActionType.LIKE, "executed", url=target.url)

    def _do_follow(self, target: Target) -> ActionResult:
        resp = self.client().post(
            f"{self._base()}/api/v1/accounts/{target.author_id}/follow",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return self._result(target, ActionType.FOLLOW, "executed",
                            url=f"{self._base()}/@{target.author_handle}")

    def _do_reply(self, target: Target, comment: str) -> ActionResult:
        resp = self.client().post(
            f"{self._base()}/api/v1/statuses",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"status": comment, "in_reply_to_id": target.key, "visibility": "public"},
        )
        resp.raise_for_status()
        data = resp.json()
        return self._result(target, ActionType.REPLY, "executed",
                            url=data.get("url") or data.get("uri"))
