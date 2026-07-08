"""Tests for article-image generation (imagegen) and publisher image attach."""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from content_engine import imagegen
from content_engine.models import ImageAsset, Post
from content_engine.publishers.bluesky import BlueskyPublisher
from content_engine.publishers.devto import DevToPublisher
from content_engine.publishers.mastodon import MastodonPublisher

_JPEG = b"\xff\xd8\xff" + b"0" * 4000  # looks like image bytes, > 2000


def _post():
    return Post(title="n8n's bet", body_markdown="body", summary="A take. https://github.com/n8n-io/n8n",
                repo_url="https://github.com/n8n-io/n8n", tags=["ai", "automation"])


# --------------------------------------------------------------------------- #
# imagegen
# --------------------------------------------------------------------------- #
def test_build_url_is_deterministic_and_parametrized():
    u1 = imagegen.build_image_url("a modular automation pipeline", width=1280, height=720)
    u2 = imagegen.build_image_url("a modular automation pipeline", width=1280, height=720)
    assert u1 == u2                                  # deterministic (crc32 seed)
    assert "image.pollinations.ai" in u1 and "width=1280" in u1 and "height=720" in u1
    assert "no%20text" in u1.lower() or "no+text" in u1.lower() or "no%2c%20text" in u1.lower()


def test_generate_returns_none_for_empty_prompt():
    assert imagegen.generate("   ") is None
    a = imagegen.generate("prototype hardening into production", alt="alt text")
    assert isinstance(a, ImageAsset) and a.alt == "alt text" and a.url


def test_ensure_data_fetches_and_caches():
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=_JPEG, headers={"content-type": "image/jpeg"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    a = imagegen.generate("x subject")
    assert a.ensure_data(client) == _JPEG
    assert a.ensure_data(client) == _JPEG          # cached, no second fetch
    assert calls["n"] == 1


def test_ensure_data_gives_up_gracefully():
    def handler(req):
        return httpx.Response(500, text="boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    a = imagegen.generate("y subject")
    assert a.ensure_data(client) is None


# --------------------------------------------------------------------------- #
# DEV.to — cover via main_image (URL only, no upload)
# --------------------------------------------------------------------------- #
def test_devto_payload_sets_main_image(settings):
    post = _post()
    post.image = imagegen.generate("subject")
    payload = DevToPublisher(settings).render_payload(post)
    assert payload["article"]["main_image"] == post.image.url


def test_devto_no_image_no_main_image(settings):
    payload = DevToPublisher(settings).render_payload(_post())
    assert "main_image" not in payload["article"]


# --------------------------------------------------------------------------- #
# Bluesky — uploadBlob + embed
# --------------------------------------------------------------------------- #
def test_bluesky_uploads_blob_and_embeds(settings):
    seen = {"blob": False, "embed": None}
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if req.url.host == "image.pollinations.ai":
            return httpx.Response(200, content=_JPEG, headers={"content-type": "image/jpeg"})
        if p.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc"})
        if p.endswith("uploadBlob"):
            seen["blob"] = True
            return httpx.Response(200, json={"blob": {"$type": "blob", "ref": {"$link": "b1"}}})
        if p.endswith("createRecord"):
            import json as _j
            seen["embed"] = _j.loads(req.content)["record"].get("embed")
            return httpx.Response(200, json={"uri": "at://did:plc:abc/app.bsky.feed.post/xyz"})
        return httpx.Response(404)

    env = {**settings.env, "BLUESKY_HANDLE": "h.bsky.social", "BLUESKY_APP_PASSWORD": "pw"}
    s = dataclasses.replace(settings, env=env)
    post = _post(); post.image = imagegen.generate("subject", alt="an alt")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = BlueskyPublisher(s, http_client=client).publish(post, dry_run=False)
    assert res.status == "published"
    assert seen["blob"] is True
    assert seen["embed"]["$type"] == "app.bsky.embed.images"
    assert seen["embed"]["images"][0]["alt"] == "an alt"


def test_bluesky_ships_text_only_when_image_fetch_fails(settings):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "image.pollinations.ai":
            return httpx.Response(500)                 # image unavailable
        p = req.url.path
        if p.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc"})
        if p.endswith("createRecord"):
            import json as _j
            assert "embed" not in _j.loads(req.content)["record"]   # no image -> no embed
            return httpx.Response(200, json={"uri": "at://did:plc:abc/app.bsky.feed.post/xyz"})
        return httpx.Response(404)
    env = {**settings.env, "BLUESKY_HANDLE": "h.bsky.social", "BLUESKY_APP_PASSWORD": "pw"}
    s = dataclasses.replace(settings, env=env)
    post = _post(); post.image = imagegen.generate("subject")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = BlueskyPublisher(s, http_client=client).publish(post, dry_run=False)
    assert res.status == "published"                   # still shipped


# --------------------------------------------------------------------------- #
# Mastodon — media upload + media_ids
# --------------------------------------------------------------------------- #
def test_mastodon_uploads_media_and_attaches(settings):
    seen = {"media_ids": None}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "image.pollinations.ai":
            return httpx.Response(200, content=_JPEG, headers={"content-type": "image/jpeg"})
        p = req.url.path
        if p.endswith("/api/v2/media"):
            return httpx.Response(200, json={"id": "77"})
        if p.endswith("/api/v1/statuses"):
            import json as _j
            seen["media_ids"] = _j.loads(req.content).get("media_ids")
            return httpx.Response(200, json={"id": "1", "url": "https://m.social/@x/1"})
        return httpx.Response(404)
    env = {**settings.env, "MASTODON_BASE_URL": "https://m.social", "MASTODON_ACCESS_TOKEN": "tok"}
    s = dataclasses.replace(settings, env=env)
    post = _post(); post.image = imagegen.generate("subject")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = MastodonPublisher(s, http_client=client).publish(post, dry_run=False)
    assert res.status == "published"
    assert seen["media_ids"] == ["77"]
