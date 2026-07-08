"""Mastodon publisher.

Posts a status via POST {instance}/api/v1/statuses with a bearer access token
created in the instance's Preferences > Development. Uses an Idempotency-Key so
retries don't double-post. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import hashlib

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import hashtags_for, microblog_text

_DEFAULT_LIMIT = 500


class MastodonPublisher(BasePublisher):
    name = "mastodon"

    def _base(self) -> str:
        return self.settings.get_env("MASTODON_BASE_URL").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._base()) and bool(self.settings.get_env("MASTODON_ACCESS_TOKEN"))

    def _text(self, post: Post) -> str:
        return microblog_text(post, _DEFAULT_LIMIT, include_url=True,
                              hashtags=hashtags_for(post))

    def render_payload(self, post: Post) -> dict:
        visibility = self.settings.get_env("MASTODON_VISIBILITY", "public").lower()
        if visibility not in ("public", "unlisted", "private", "direct"):
            visibility = "public"
        return {
            "status": self._text(post),
            "visibility": visibility,
            "language": "en",
        }

    def _idempotency_key(self, post: Post) -> str:
        seed = f"{post.repo_url}|{post.title}".encode()
        return hashlib.sha256(seed).hexdigest()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.get_env('MASTODON_ACCESS_TOKEN')}"}

    def _upload_media(self, post: Post) -> str | None:
        """Upload the post image and return its media id, or None. Needs the
        token's write:media scope; any failure returns None (post ships text-only)."""
        if not post.image:
            return None
        data = post.image.ensure_data(self.client())
        if not data:
            return None
        files = {"file": ("image.jpg", data, post.image.mime or "image/jpeg")}
        r = self.client().post(f"{self._base()}/api/v2/media", headers=self._auth(),
                               files=files, data={"description": post.image.alt or ""})
        r.raise_for_status()
        mid = r.json().get("id")
        # media may still be processing (202); the status post tolerates that.
        return str(mid) if mid else None

    def _publish_live(self, post: Post) -> PublishResult:
        body = self.render_payload(post)
        media_id = self._upload_media(post)
        if media_id:
            body["media_ids"] = [media_id]
        resp = self.client().post(
            f"{self._base()}/api/v1/statuses",
            headers={
                **self._auth(),
                "Idempotency-Key": self._idempotency_key(post),
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return PublishResult(
            publisher=self.name,
            status="published",
            url=data.get("url") or data.get("uri"),
            external_id=str(data.get("id", "")),
            dry_run=False,
        )
