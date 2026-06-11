"""Spike: validate a publisher's auth + payload, with an optional real post.

Usage:
    PYTHONPATH=src python spikes/publisher_smoke.py <name>          # dry preview
    PYTHONPATH=src python spikes/publisher_smoke.py <name> --live   # real post

<name> in: dryrun devto ghost wordpress hashnode bluesky mastodon linkedin threads
Credentials come from your .env (see docs/API_FINDINGS.md). The dry preview makes
NO network call; --live performs the real official-API publish.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from content_engine.config import load_settings  # noqa: E402
from content_engine.models import Post  # noqa: E402
from content_engine.publishers import AVAILABLE_PUBLISHERS  # noqa: E402

SAMPLE = Post(
    title="Spike: evaluating acme/widget for production use",
    body_markdown=(
        "## What it does\n\n"
        "`acme/widget` is a small CLI that batches filesystem events. This is a "
        "spike post generated to validate the publisher integration end to end.\n\n"
        "## Takeaway\n\n"
        "If you can read this on the platform, automated posting works."
    ),
    summary="A test post validating the publisher integration for acme/widget.",
    tags=["test", "automation", "opensource"],
    repo_url="https://github.com/acme/widget",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=sorted(AVAILABLE_PUBLISHERS))
    ap.add_argument("--live", action="store_true", help="perform a real publish")
    args = ap.parse_args()

    settings = load_settings()
    publisher = AVAILABLE_PUBLISHERS[args.name](settings)

    configured = publisher.is_configured()
    print(f"publisher : {args.name}")
    print(f"configured: {configured}")
    print("payload it would send:")
    try:
        print(publisher._preview(SAMPLE))
    except Exception as exc:  # noqa: BLE001
        print(f"  <render error: {exc}>")

    if not args.live:
        print("\n(dry preview — no network call. Re-run with --live to post.)")
        return 0

    if not configured:
        print("\nERROR: --live requested but publisher is not configured (.env).")
        return 1

    print("\nposting for real ...")
    result = publisher.publish(SAMPLE, dry_run=False)
    print(f"status     : {result.status}")
    print(f"url        : {result.url}")
    print(f"external_id: {result.external_id}")
    print(f"error      : {result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
