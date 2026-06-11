"""LinkedIn publisher (Posts API).

Posts to a member's feed via POST https://api.linkedin.com/rest/posts using an
OAuth2 access token with the ``w_member_social`` scope and the member's author
URN. NOTE: obtaining that token requires an approved LinkedIn developer app, so
in practice this publisher runs in dry-run/skipped mode until credentials exist.
See docs/API_FINDINGS.md.
"""

from __future__ import annotations

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import to_plain_text, truncate

_API = "https://api.linkedin.com/rest/posts"
_COMMENTARY_LIMIT = 2900  # API allows up to 3000


class LinkedInPublisher(BasePublisher):
    name = "linkedin"

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("LINKEDIN_ACCESS_TOKEN")) and bool(
            self.settings.get_env("LINKEDIN_AUTHOR_URN")
        )

    def _commentary(self, post: Post) -> str:
        url = post.repo_url or post.canonical_url or ""
        body = (post.summary or to_plain_text(post.body_markdown)).strip()
        text = f"{post.title}\n\n{body}"
        if url and url not in text:
            text = f"{text}\n\n{url}"
        return truncate(text, _COMMENTARY_LIMIT)

    def render_payload(self, post: Post) -> dict:
        return {
            "author": self.settings.get_env("LINKEDIN_AUTHOR_URN"),
            "commentary": self._commentary(post),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

    def _publish_live(self, post: Post) -> PublishResult:
        version = self.settings.get_env("LINKEDIN_API_VERSION", "202506")
        resp = self.client().post(
            _API,
            headers={
                "Authorization": f"Bearer {self.settings.get_env('LINKEDIN_ACCESS_TOKEN')}",
                "LinkedIn-Version": version,
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            json=self.render_payload(post),
        )
        resp.raise_for_status()
        # The created post URN is returned in a response header, not the body.
        post_urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id", "")
        url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None
        return PublishResult(
            publisher=self.name,
            status="published",
            url=url,
            external_id=post_urn,
            dry_run=False,
        )
