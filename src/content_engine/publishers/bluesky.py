"""Bluesky publisher (AT Protocol).

Flow: create a session with handle + app password, then create an
``app.bsky.feed.post`` record. We attach a richtext facet so the repo URL is a
clickable link, and respect the 300-grapheme limit. See docs/API_FINDINGS.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import Post, PublishResult
from .base import BasePublisher
from .util import hashtags_for, microblog_text

_POST_LIMIT = 300
_HASHTAG_RE = re.compile(r"#(\w+)")


def _link_facets(text: str, url: str) -> list[dict]:
    """Build a link facet with UTF-8 byte offsets for ``url`` inside ``text``."""
    if not url or url not in text:
        return []
    btext = text.encode("utf-8")
    burl = url.encode("utf-8")
    start = btext.find(burl)
    if start < 0:
        return []
    return [
        {
            "index": {"byteStart": start, "byteEnd": start + len(burl)},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        }
    ]


def _tag_facets(text: str) -> list[dict]:
    """Build richtext tag facets so each ``#hashtag`` is clickable/searchable."""
    facets = []
    for m in re.finditer(_HASHTAG_RE, text):
        start = len(text[: m.start()].encode("utf-8"))
        end = len(text[: m.end()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}],
        })
    return facets


def _facets(text: str, url: str) -> list[dict]:
    return _link_facets(text, url) + _tag_facets(text)


class BlueskyPublisher(BasePublisher):
    name = "bluesky"

    def _pds(self) -> str:
        return self.settings.get_env("BLUESKY_PDS_URL", "https://bsky.social").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("BLUESKY_HANDLE")) and bool(
            self.settings.get_env("BLUESKY_APP_PASSWORD")
        )

    def _text(self, post: Post) -> str:
        return microblog_text(post, _POST_LIMIT, include_url=True,
                              hashtags=hashtags_for(post))

    def render_payload(self, post: Post) -> dict:
        # Mirrors the real createRecord body. `repo` (the account DID) and the
        # exact `createdAt` are filled in at publish time from the session; we
        # show a representative createdAt here so the dry-run preview matches the
        # real request shape rather than emitting opaque placeholders.
        text = self._text(post)
        url = post.repo_url or post.canonical_url or ""
        handle = self.settings.get_env("BLUESKY_HANDLE") or "<handle>"
        return {
            "repo": f"<did for {handle}; resolved from session at publish time>",
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "langs": ["en"],
                "facets": _facets(text, url),
            },
        }

    def _create_session(self) -> tuple[str, str]:
        resp = self.client().post(
            f"{self._pds()}/xrpc/com.atproto.server.createSession",
            json={
                "identifier": self.settings.get_env("BLUESKY_HANDLE"),
                "password": self.settings.get_env("BLUESKY_APP_PASSWORD"),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["accessJwt"], data["did"]

    def _upload_image(self, post: Post, access_jwt: str) -> dict | None:
        """Upload the post image as a blob and return an images embed, or None.

        Bluesky caps a blob at ~1MB; Pollinations JPEGs are well under that. Any
        failure (fetch/upload) returns None so the post still ships text-only."""
        if not post.image:
            return None
        data = post.image.ensure_data(self.client())
        if not data:
            return None
        resp = self.client().post(
            f"{self._pds()}/xrpc/com.atproto.repo.uploadBlob",
            headers={"Authorization": f"Bearer {access_jwt}",
                     "Content-Type": post.image.mime or "image/jpeg"},
            content=data,
        )
        resp.raise_for_status()
        blob = resp.json()["blob"]
        return {
            "$type": "app.bsky.embed.images",
            "images": [{"alt": post.image.alt or "", "image": blob}],
        }

    def _publish_live(self, post: Post) -> PublishResult:
        access_jwt, did = self._create_session()
        text = self._text(post)
        url = post.repo_url or post.canonical_url or ""
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "langs": ["en"],
            "facets": _link_facets(text, url),
        }
        embed = self._upload_image(post, access_jwt)
        if embed:
            record["embed"] = embed
        resp = self.client().post(
            f"{self._pds()}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {access_jwt}"},
            json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
        )
        resp.raise_for_status()
        data = resp.json()
        uri = data.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        handle = self.settings.get_env("BLUESKY_HANDLE")
        web_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None
        return PublishResult(
            publisher=self.name,
            status="published",
            url=web_url,
            external_id=uri,
            dry_run=False,
        )
