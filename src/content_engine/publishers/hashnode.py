"""Hashnode publisher via the GraphQL API.

Endpoint: https://gql.hashnode.com with a Personal Access Token in the
``Authorization`` header. We use the ``publishPost`` mutation; ``contentMarkdown``
is consumed natively. A ``publicationId`` is required. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import re

from ..models import Post, PublishResult
from .base import BasePublisher

_API = "https://gql.hashnode.com"

_MUTATION = """
mutation PublishPost($input: PublishPostInput!) {
  publishPost(input: $input) {
    post { id slug url title }
  }
}
""".strip()


def _slugify_tag(t: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")


class HashnodePublisher(BasePublisher):
    name = "hashnode"

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("HASHNODE_API_KEY")) and bool(
            self.settings.get_env("HASHNODE_PUBLICATION_ID")
        )

    def render_payload(self, post: Post) -> dict:
        tags = [
            {"slug": _slugify_tag(t), "name": t}
            for t in post.tags
            if _slugify_tag(t)
        ][:5]
        variables = {
            "input": {
                "title": post.title,
                "contentMarkdown": post.body_markdown,
                "publicationId": self.settings.get_env("HASHNODE_PUBLICATION_ID"),
                "tags": tags,
            }
        }
        if post.canonical_url:
            variables["input"]["originalArticleURL"] = post.canonical_url
        return {"query": _MUTATION, "variables": variables}

    def _publish_live(self, post: Post) -> PublishResult:
        resp = self.client().post(
            _API,
            headers={
                "Authorization": self.settings.get_env("HASHNODE_API_KEY"),
                "Content-Type": "application/json",
            },
            json=self.render_payload(post),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"Hashnode GraphQL errors: {data['errors']}")
        node = (((data.get("data") or {}).get("publishPost") or {}).get("post")) or {}
        return PublishResult(
            publisher=self.name,
            status="published",
            url=node.get("url"),
            external_id=str(node.get("id", "")),
            dry_run=False,
        )
