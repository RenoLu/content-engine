"""Adapter registry: name -> adapter factory."""

from __future__ import annotations

import httpx

from ..config import Settings
from .base import BaseAdapter
from .bluesky import BlueskyAdapter
from .config import OutreachConfig
from .devto import DevtoAdapter
from .mastodon import MastodonAdapter

_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "bluesky": BlueskyAdapter,
    "mastodon": MastodonAdapter,
    "devto": DevtoAdapter,
}


def available() -> list[str]:
    return sorted(_ADAPTERS)


def build_adapter(name: str, settings: Settings, config: OutreachConfig,
                  http_client: httpx.Client | None = None) -> BaseAdapter:
    cls = _ADAPTERS.get(name)
    if cls is None:
        raise KeyError(f"unknown outreach platform: {name!r} (have {available()})")
    return cls(settings, config, http_client)
