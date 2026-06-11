"""Publisher registry: maps names -> classes and builds the active set."""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging_setup import get_logger
from .base import BasePublisher
from .bluesky import BlueskyPublisher
from .devto import DevToPublisher
from .dryrun import DryRunPublisher
from .ghost import GhostPublisher
from .hashnode import HashnodePublisher
from .linkedin import LinkedInPublisher
from .mastodon import MastodonPublisher
from .threads import ThreadsPublisher
from .wordpress import WordPressPublisher

log = get_logger(__name__)

AVAILABLE_PUBLISHERS: dict[str, type[BasePublisher]] = {
    "dryrun": DryRunPublisher,
    "devto": DevToPublisher,
    "ghost": GhostPublisher,
    "wordpress": WordPressPublisher,
    "hashnode": HashnodePublisher,
    "bluesky": BlueskyPublisher,
    "mastodon": MastodonPublisher,
    "linkedin": LinkedInPublisher,
    "threads": ThreadsPublisher,
}


def build_publishers(settings: Settings,
                     http_client: httpx.Client | None = None) -> list[BasePublisher]:
    """Instantiate the configured publishers.

    The dry-run publisher is always included (first) so a local artifact of the
    content is written on every run, regardless of mode — useful for auditing.
    """
    publishers: list[BasePublisher] = []
    seen: set[str] = set()
    for name in settings.enabled_publishers:
        cls = AVAILABLE_PUBLISHERS.get(name)
        if cls is None:
            log.warning("unknown publisher %r (skipping). Known: %s",
                        name, ", ".join(AVAILABLE_PUBLISHERS))
            continue
        if name in seen:
            continue
        seen.add(name)
        publishers.append(cls(settings, http_client=http_client))

    if "dryrun" not in seen:
        publishers.insert(0, DryRunPublisher(settings, http_client=http_client))

    log.info("publishers: %s", ", ".join(p.name for p in publishers))
    return publishers
