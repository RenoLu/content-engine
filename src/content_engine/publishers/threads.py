"""Threads publisher (Meta Threads API).

Two-step publish: create a media container (POST /{user-id}/threads), then
publish it (POST /{user-id}/threads_publish). Requires a long-lived token with
the ``threads_content_publish`` scope from an approved Meta app, so this usually
runs in dry-run/skipped mode until credentials exist. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import microblog_text

_GRAPH = "https://graph.threads.net/v1.0"
_TEXT_LIMIT = 500


class ThreadsPublisher(BasePublisher):
    name = "threads"

    def _user_id(self) -> str:
        return self.settings.get_env("THREADS_USER_ID")

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("THREADS_ACCESS_TOKEN")) and bool(self._user_id())

    def _text(self, post: Post) -> str:
        return microblog_text(post, _TEXT_LIMIT, include_url=True)

    def render_payload(self, post: Post) -> dict:
        return {
            "endpoint": f"{_GRAPH}/{self._user_id() or '<user-id>'}/threads",
            "media_type": "TEXT",
            "text": self._text(post),
            "note": "access_token sent as query param at publish time (redacted)",
        }

    def _publish_live(self, post: Post) -> PublishResult:
        token = self.settings.get_env("THREADS_ACCESS_TOKEN")
        user = self._user_id()
        client = self.client()

        # 1) create container
        create = client.post(
            f"{_GRAPH}/{user}/threads",
            params={"media_type": "TEXT", "text": self._text(post), "access_token": token},
        )
        create.raise_for_status()
        creation_id = create.json().get("id")
        if not creation_id:
            raise RuntimeError("Threads: no creation id returned")

        # 2) publish container
        publish = client.post(
            f"{_GRAPH}/{user}/threads_publish",
            params={"creation_id": creation_id, "access_token": token},
        )
        publish.raise_for_status()
        media_id = publish.json().get("id", "")

        # 3) best-effort permalink lookup
        permalink = None
        try:
            meta = client.get(
                f"{_GRAPH}/{media_id}",
                params={"fields": "permalink", "access_token": token},
            )
            if meta.status_code < 400:
                permalink = meta.json().get("permalink")
        except Exception:  # noqa: BLE001
            permalink = None

        return PublishResult(
            publisher=self.name,
            status="published",
            url=permalink,
            external_id=str(media_id),
            dry_run=False,
        )
