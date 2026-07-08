"""DEV.to engagement adapter — discover only.

DEV.to's public API exposes article *reads* but no sanctioned endpoint to
favorite an article or create a comment with an API key, so this adapter only
surfaces targets (useful for reporting / future manual engagement). The like/
follow/reply primitives fall through to the base's ``_unsupported`` (skipped).
"""

from __future__ import annotations

from .base import BaseAdapter
from .models import Target

_API = "https://dev.to/api"


class DevtoAdapter(BaseAdapter):
    name = "devto"

    def is_configured(self) -> bool:
        # Reading articles needs no key; a key would only be needed for the
        # (unsupported) write actions.
        return True

    def discover(self, queries: list[str], limit: int) -> list[Target]:
        out: list[Target] = []
        seen: set[str] = set()
        for q in queries:
            tag = "".join(ch for ch in q.lower() if ch.isalnum())
            if not tag:
                continue
            try:
                resp = self.client().get(
                    f"{_API}/articles",
                    params={"tag": tag, "per_page": min(limit, 30)},
                )
                resp.raise_for_status()
                articles = resp.json() or []
            except Exception:
                continue
            for a in articles:
                aid = str(a.get("id", ""))
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                user = a.get("user", {}) or {}
                out.append(Target(
                    platform=self.name,
                    key=aid,
                    text=a.get("title", ""),
                    url=a.get("url", ""),
                    author_id=str(user.get("user_id", "")),
                    author_handle=user.get("username", ""),
                ))
        return out
