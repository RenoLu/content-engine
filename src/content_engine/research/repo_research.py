"""Enriches a Repository with its README and derived signals.

Kept deliberately lightweight for the MVP: README markdown + extracted links +
length. Heavier signals (commit cadence, contributor counts, release history)
can be layered in later without changing callers.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Repository
from ..sources.github import GitHubClient, extract_links

log = get_logger(__name__)

# Cap README size we keep/feed to the model to control token usage.
README_MAX_CHARS = 12000


class RepoResearcher:
    def __init__(self, settings: Settings, client: GitHubClient | None = None):
        self.settings = settings
        self.client = client or GitHubClient(token=settings.github_token)

    def enrich(self, repo: Repository) -> Repository:
        """Fetch README (and topics if missing) and attach to the repo in place."""
        try:
            readme = self.client.get_readme(repo.full_name)
        except httpx.HTTPError as exc:
            log.warning("README fetch failed for %s: %s", repo.full_name, exc)
            readme = ""

        repo.readme_len = len(readme)
        repo.readme_markdown = readme[:README_MAX_CHARS] if readme else None
        repo.extracted_links = extract_links(readme)
        log.info("enriched %s: readme=%d chars, links=%d",
                 repo.full_name, repo.readme_len, len(repo.extracted_links))
        return repo
