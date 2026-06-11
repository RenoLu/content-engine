"""WordPress (self-hosted) publisher via the REST API.

Auth: Application Passwords (HTTP Basic ``username:app_password`` over HTTPS).
Endpoint: POST {site}/wp-json/wp/v2/posts. ``content`` expects HTML, so we
convert the markdown body. Tags/categories require term IDs, which is fiddly to
automate, so we send the post as free-text (tags omitted by default) and leave
taxonomy assignment as a documented enhancement. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import base64

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import markdown_to_html


class WordPressPublisher(BasePublisher):
    name = "wordpress"

    def _base(self) -> str:
        return self.settings.get_env("WORDPRESS_BASE_URL").rstrip("/")

    def is_configured(self) -> bool:
        return all(
            self.settings.get_env(k)
            for k in ("WORDPRESS_BASE_URL", "WORDPRESS_USERNAME", "WORDPRESS_APP_PASSWORD")
        )

    def _auth_header(self) -> str:
        user = self.settings.get_env("WORDPRESS_USERNAME")
        pw = self.settings.get_env("WORDPRESS_APP_PASSWORD").replace(" ", "")
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return f"Basic {token}"

    def render_payload(self, post: Post) -> dict:
        status = self.settings.get_env("WORDPRESS_POST_STATUS", "draft").lower()
        if status not in ("draft", "publish", "pending", "private", "future"):
            status = "draft"
        return {
            "title": post.title,
            "content": markdown_to_html(post.body_markdown),
            "excerpt": post.summary,
            "status": status,
        }

    def _publish_live(self, post: Post) -> PublishResult:
        url = f"{self._base()}/wp-json/wp/v2/posts"
        resp = self.client().post(
            url,
            headers={"Authorization": self._auth_header(), "Content-Type": "application/json"},
            json=self.render_payload(post),
        )
        resp.raise_for_status()
        data = resp.json()
        return PublishResult(
            publisher=self.name,
            status="published",
            url=data.get("link"),
            external_id=str(data.get("id", "")),
            dry_run=False,
        )
