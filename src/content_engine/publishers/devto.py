"""DEV.to (Forem) publisher.

Official API: POST https://dev.to/api/articles with the ``api-key`` header.
DEV.to consumes markdown natively (``body_markdown``) and supports draft vs
publish via the ``published`` boolean. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import re

from ..logging_setup import get_logger
from ..models import Post, PublishResult
from .base import BasePublisher

log = get_logger(__name__)

_API = "https://dev.to/api/articles"


def _normalize_tags(tags: list[str], limit: int = 4) -> list[str]:
    """DEV.to tags must be alphanumeric, lowercase, max 4."""
    out: list[str] = []
    for t in tags:
        clean = re.sub(r"[^a-z0-9]", "", t.lower())
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


class DevToPublisher(BasePublisher):
    name = "devto"

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("DEVTO_API_KEY"))

    def render_payload(self, post: Post) -> dict:
        published = self.settings.get_env("DEVTO_PUBLISHED", "false").lower() == "true"
        tags = _normalize_tags(post.tags)
        if len(tags) < len(post.tags):
            log.debug("devto: normalized tags %s -> %s (DEV.to allows max 4, alphanumeric)",
                      post.tags, tags)
        article: dict = {
            "title": post.title,
            "body_markdown": self._body_footer(post),
            "published": published,
            "tags": tags,
        }
        if post.canonical_url:
            article["canonical_url"] = post.canonical_url
        if post.summary:
            article["description"] = post.summary[:140]
        return {"article": article}

    @staticmethod
    def _body_with_repo_link(post: Post) -> str:
        """Ensure the featured repo's GitHub link is present in the body.

        The body is the only place a reader sees the link (DEV.to ignores our
        Post.repo_url), so append a footer when the URL isn't already inline.
        """
        body = post.body_markdown or ""
        url = post.repo_url
        if url and url not in body:
            body = body.rstrip() + f"\n\n---\n\n**GitHub:** [{url}]({url})\n"
        return body

    def _body_footer(self, post: Post) -> str:
        """Repo link + (when ``BRAND_BYLINE_URL`` is set) a soft brand byline that
        funnels readers from the article back to the site. Deduped: skipped if the
        brand URL is already in the body (e.g. a palisade post with its own CTA)."""
        body = self._body_with_repo_link(post)
        brand = self.settings.get_env("BRAND_BYLINE_URL")
        if brand and brand not in body:
            body = body.rstrip() + (
                f"\n\n---\n\n*Curated by [Agent Palisade]({brand}) — "
                "practical AI for small and mid-sized businesses.*\n"
            )
        return body

    def _publish_live(self, post: Post) -> PublishResult:
        resp = self.client().post(
            _API,
            headers={
                "api-key": self.settings.get_env("DEVTO_API_KEY"),
                "Content-Type": "application/json",
                "Accept": "application/vnd.forem.api-v1+json",
            },
            json=self.render_payload(post),
        )
        resp.raise_for_status()
        data = resp.json()
        return PublishResult(
            publisher=self.name,
            status="published",
            url=data.get("url"),
            external_id=str(data.get("id", "")),
            dry_run=False,
        )
