"""Trend sources. MVP ships GitHub only, behind a generic ``Source`` interface
so additional sources (Hacker News, RSS, …) can be added later without touching
the pipeline."""

from __future__ import annotations

from ..config import Settings
from .base import Source
from .github import GitHubClient, GitHubTrendingSource
from .github_trending import GitHubTrendingHtmlSource

__all__ = [
    "Source",
    "GitHubClient",
    "GitHubTrendingSource",
    "GitHubTrendingHtmlSource",
    "build_source",
]


def build_source(settings: Settings, client: GitHubClient | None = None) -> Source:
    """Construct the configured candidate source.

    ``source_provider`` (config ``[source].provider`` / env ``GITHUB_SOURCE``):
      * ``"trending_html"`` (default) — parse the real github.com/trending page,
        hydrate via the official API, fall back to the Search API on failure.
      * ``"search_api"`` — approximate trending via the GitHub Search API only.
    """
    if settings.source_provider == "search_api":
        return GitHubTrendingSource(settings, client=client)
    return GitHubTrendingHtmlSource(settings, client=client)
