"""Publish the oldest queued article. NO model needed — run by the GitHub Action.

Content is pre-generated locally (the agent authors + gates it) and committed to
``_manual/queue/*.json`` (oldest filename first). This script only POSTs, so CI
never needs a model API or Anthropic credit. Each queue item::

    {"repo":"owner/name","generated_at":"...","title","summary","tags",
     "body_markdown","repo_url","canonical_url"}

Idempotency is the queue itself: a successful LIVE publish MOVES the item to
``_manual/published/`` (the Action commits that move), so it is never re-posted.
A dry-run never moves anything. The scheduled cron fires once/day.
"""
from __future__ import annotations

import json
from pathlib import Path

from content_engine import imagegen
from content_engine.config import load_settings
from content_engine.models import ImageAsset, Post
from content_engine.publishers import build_publishers

HERE = Path(__file__).parent
QUEUE = HERE / "queue"
PUBLISHED = HERE / "published"


def _queue_files() -> list[Path]:
    return sorted(QUEUE.glob("*.json")) if QUEUE.exists() else []


def _build_image(data: dict, s) -> "ImageAsset | None":
    """Article image for a queued item, attached at publish time so the GitHub
    Action's posts carry a cover/embed. Prefers a committed ``image_url`` (a
    pre-generated asset hosted in-repo, most reliable in CI); otherwise builds a
    Pollinations image from the item's ``image_prompt`` (or one derived from the
    title/summary). Disabled with POST_IMAGE=false. No network here — publishers
    fetch the bytes lazily only when they need them (Bluesky blob, Mastodon media)."""
    if s.get_env("POST_IMAGE", "true").lower() != "true":
        return None
    title = data.get("title", "")
    url = (data.get("image_url") or "").strip()
    if url:
        return ImageAsset(url=url, alt=f"Illustration for: {title}")
    prompt = (data.get("image_prompt") or "").strip()
    if not prompt:
        prompt = (f"An illustration for an article titled \"{title}\". "
                  f"{data.get('summary', '')}").strip()
    w = int(s.get_env("POST_IMAGE_WIDTH", "1280") or 1280)
    h = int(s.get_env("POST_IMAGE_HEIGHT", "720") or 720)
    return imagegen.generate(prompt, alt=f"Illustration for: {title}", width=w, height=h)


def main() -> int:
    s = load_settings()
    mode = s.publish_mode
    live = mode == "live"

    files = _queue_files()
    if not files:
        print(json.dumps({"status": "empty_queue", "mode": mode}))
        return 0

    item = files[0]
    data = json.loads(item.read_text(encoding="utf-8"))
    post = Post(
        title=data["title"],
        body_markdown=data["body_markdown"],
        summary=data["summary"],
        tags=list(data.get("tags", [])),
        canonical_url=data.get("canonical_url"),
        repo_url=data.get("repo_url"),
    )
    post.image = _build_image(data, s)
    repo = data.get("repo", item.stem)

    results = []
    posted = False
    for pub in build_publishers(s):
        dry = (pub.name == "dryrun") or (not live)
        res = pub.publish(post, dry_run=dry)
        if not dry and res.ok:
            posted = True
        results.append(res)

    if live and posted:
        status = "published"
        PUBLISHED.mkdir(parents=True, exist_ok=True)
        item.rename(PUBLISHED / item.name)
    elif live:
        status = "failed"   # leave queued to retry next run
    else:
        status = "dry_run"  # never consumes the queue

    print(json.dumps({
        "status": status,
        "mode": mode,
        "repo": repo,
        "queue_item": item.name,
        "remaining_in_queue": len(_queue_files()),
        "publish_results": [r.to_dict() for r in results],
    }, ensure_ascii=False, indent=2))
    return 0 if status in ("published", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
