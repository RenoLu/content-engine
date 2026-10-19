"""The GitHub-Action publish path (_manual/publish_queue.py) must attach an
article image to each queued post, so scheduled posts carry a cover/embed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from content_engine.config import load_settings

_PQ = Path(__file__).resolve().parents[1] / "_manual" / "publish_queue.py"
_spec = importlib.util.spec_from_file_location("publish_queue", _PQ)
publish_queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_queue)


def _settings(**env):
    base = {"PUBLISH_MODE": "live", "ENABLED_PUBLISHERS": "dryrun"}
    base.update(env)
    return load_settings(env=base, load_dotenv_file=False)


def test_committed_image_url_is_preferred():
    s = _settings()
    data = {"title": "T", "summary": "s", "image_url": "https://raw.example/x.jpg",
            "image_prompt": "ignored when a committed url exists"}
    img = publish_queue._build_image(data, s)
    assert img is not None
    assert img.url == "https://raw.example/x.jpg"
    assert "T" in img.alt


def test_no_committed_cover_means_no_image():
    s = _settings()
    data = {"title": "T", "summary": "s", "image_prompt": "a robot reading a bug report"}
    assert publish_queue._build_image(data, s) is None


def test_pollinations_url_is_rejected():
    s = _settings()
    data = {"title": "T", "summary": "s",
            "image_url": "https://image.pollinations.ai/prompt/x?width=1280"}
    assert publish_queue._build_image(data, s) is None


def test_disabled_by_post_image_false():
    s = _settings(POST_IMAGE="false")
    data = {"title": "T", "summary": "s", "image_url": "https://raw.example/x.jpg"}
    assert publish_queue._build_image(data, s) is None
