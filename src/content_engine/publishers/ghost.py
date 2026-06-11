"""Ghost publisher (Admin API).

Auth is a short-lived JWT (HS256) signed with the Admin API key, which has the
form ``id:secret`` where ``secret`` is hex. We sign the token with stdlib
hmac/hashlib (no PyJWT dependency). Content is sent as HTML via ``?source=html``.
See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import markdown_to_html


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# Backdate `iat` slightly so a small client/server clock skew can't make Ghost
# reject the token as "not yet valid". exp stays within Ghost's 5-minute cap
# (exp - iat = 300s exactly; exp lands ~270s in the real future).
_CLOCK_SKEW_LEEWAY = 30


def make_ghost_jwt(admin_api_key: str, now: int | None = None) -> str:
    """Create a Ghost Admin API JWT from an ``id:secret`` key."""
    key_id, secret = admin_api_key.split(":", 1)
    base = int(now if now is not None else time.time())
    iat = base - _CLOCK_SKEW_LEEWAY
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    payload = {"iat": iat, "exp": iat + 300, "aud": "/admin/"}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(
        bytes.fromhex(secret), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


class GhostPublisher(BasePublisher):
    name = "ghost"

    def _admin_url(self) -> str:
        return self.settings.get_env("GHOST_ADMIN_API_URL").rstrip("/")

    def is_configured(self) -> bool:
        key = self.settings.get_env("GHOST_ADMIN_API_KEY")
        return bool(self._admin_url()) and ":" in key

    def render_payload(self, post: Post) -> dict:
        status = self.settings.get_env("GHOST_POST_STATUS", "draft").lower()
        if status not in ("draft", "published", "scheduled"):
            status = "draft"
        ghost_post: dict = {
            "title": post.title,
            "html": markdown_to_html(post.body_markdown),
            "status": status,
            "tags": [{"name": t} for t in post.tags],
            "custom_excerpt": post.summary[:300] if post.summary else None,
        }
        if post.canonical_url:
            ghost_post["canonical_url"] = post.canonical_url
        return {"posts": [ghost_post]}

    def _publish_live(self, post: Post) -> PublishResult:
        token = make_ghost_jwt(self.settings.get_env("GHOST_ADMIN_API_KEY"))
        url = f"{self._admin_url()}/ghost/api/admin/posts/?source=html"
        resp = self.client().post(
            url,
            headers={
                "Authorization": f"Ghost {token}",
                "Content-Type": "application/json",
                "Accept-Version": "v5.0",
            },
            json=self.render_payload(post),
        )
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("posts", [{}])
        first = posts[0] if posts else {}
        return PublishResult(
            publisher=self.name,
            status="published",
            url=first.get("url"),
            external_id=str(first.get("id", "")),
            dry_run=False,
        )
