"""Trend sources. MVP ships GitHub only, behind a generic ``Source`` interface
so additional sources (Hacker News, RSS, …) can be added later without touching
the pipeline."""

from .base import Source
from .github import GitHubClient, GitHubTrendingSource

__all__ = ["Source", "GitHubClient", "GitHubTrendingSource"]
