"""The always-available dry-run publisher.

It writes the rendered content to OUTPUT_DIR as both markdown and JSON so you can
inspect exactly what would be posted. It NEVER makes network calls, in any mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models import Post, PublishResult
from .base import BasePublisher


class DryRunPublisher(BasePublisher):
    name = "dryrun"

    def is_configured(self) -> bool:
        return True

    def render_payload(self, post: Post) -> dict:
        return {
            "title": post.title,
            "tags": post.tags,
            "canonical_url": post.canonical_url,
            "repo_url": post.repo_url,
            "summary": post.summary,
            "body_markdown_preview": post.body_markdown[:500],
        }

    def _write_files(self, post: Post) -> str:
        out_dir = self.settings.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in post.title.lower())[:50].strip("-")
        stem = f"{ts}-{slug or 'post'}"

        md_path = out_dir / f"{stem}.md"
        md = (
            f"# {post.title}\n\n"
            f"> tags: {', '.join(post.tags)}\n"
            f"> repo: {post.repo_url or '-'}\n\n"
            f"{post.body_markdown}\n\n"
            f"---\n\n**Summary (microblog):** {post.summary}\n"
        )
        md_path.write_text(md, encoding="utf-8")

        json_path = out_dir / f"{stem}.json"
        json_path.write_text(
            json.dumps(self.render_payload(post) | {"body_markdown": post.body_markdown},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(md_path)

    # The dry-run publisher's "publish" always writes files, regardless of the
    # global dry_run flag (it is itself the simulation), and never posts.
    def publish(self, post: Post, *, dry_run: bool) -> PublishResult:
        path = self._write_files(post)
        return PublishResult(
            publisher=self.name,
            status="dry_run",
            dry_run=True,
            url=f"file:///{path}",
            payload_preview=self._preview(post),
        )

    def _publish_live(self, post: Post) -> PublishResult:  # pragma: no cover
        return self.publish(post, dry_run=True)
