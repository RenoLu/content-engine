"""GitHub source.

We approximate "trending" through the official GitHub Search API rather than
scraping the github.com/trending HTML page (which has no API, no stability
guarantee, and unclear ToS for automated use). See docs/API_FINDINGS.md.

Two complementary queries are merged and de-duplicated:
  * "rising"  -> repos created recently that already accumulated stars
  * "active"  -> established popular repos pushed very recently

``GitHubClient`` is a thin, injectable HTTP wrapper so the network layer can be
mocked in tests via ``httpx.MockTransport``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from ..config import Settings
from ..http_util import request_with_retry
from ..logging_setup import get_logger
from ..models import Repository
from .base import Source

log = get_logger(__name__)

_API_BASE = "https://api.github.com"
_USER_AGENT = "content-engine/0.1 (+https://github.com)"
_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")


class GitHubClient:
    """Low-level GitHub REST client (search, repo, readme)."""

    def __init__(self, token: str = "", client: httpx.Client | None = None,
                 base_url: str = _API_BASE):
        self.token = token
        self.base_url = base_url
        self._client = client
        self._owns_client = client is None

    def _headers(self, raw: bool = False) -> dict[str, str]:
        accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
        h = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def search_repositories(self, q: str, sort: str = "stars",
                            order: str = "desc", per_page: int = 50) -> list[dict]:
        resp = request_with_retry(
            self._get_client(), "GET", f"{self.base_url}/search/repositories",
            params={"q": q, "sort": sort, "order": order,
                    "per_page": max(1, min(per_page, 100))},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json().get("items", [])

    def get_repo(self, full_name: str) -> dict:
        resp = request_with_retry(
            self._get_client(), "GET", f"{self.base_url}/repos/{full_name}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_readme(self, full_name: str) -> str:
        """Return the raw README markdown, or '' if the repo has none."""
        resp = request_with_retry(
            self._get_client(), "GET", f"{self.base_url}/repos/{full_name}/readme",
            headers=self._headers(raw=True),
        )
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return resp.text


def repo_from_api(item: dict) -> Repository:
    """Map a GitHub repo JSON object to our ``Repository`` dataclass."""
    owner = (item.get("owner") or {}).get("login", "")
    lic = item.get("license") or {}
    return Repository(
        full_name=item.get("full_name", ""),
        name=item.get("name", ""),
        owner=owner,
        html_url=item.get("html_url", ""),
        description=item.get("description"),
        homepage=(item.get("homepage") or None),
        language=item.get("language"),
        stars=int(item.get("stargazers_count", 0) or 0),
        forks=int(item.get("forks_count", 0) or 0),
        watchers=int(item.get("watchers_count", 0) or 0),
        open_issues=int(item.get("open_issues_count", 0) or 0),
        topics=list(item.get("topics", []) or []),
        license=(lic.get("spdx_id") or lic.get("name")) if lic else None,
        created_at=item.get("created_at"),
        pushed_at=item.get("pushed_at"),
        updated_at=item.get("updated_at"),
        is_archived=bool(item.get("archived", False)),
        is_fork=bool(item.get("fork", False)),
        default_branch=item.get("default_branch", "main") or "main",
    )


def extract_links(markdown: str, limit: int = 15) -> list[str]:
    """Pull http(s) links out of README markdown (deduped, capped)."""
    seen: list[str] = []
    for m in _LINK_RE.finditer(markdown or ""):
        url = m.group(1).rstrip(".,)")
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def _ymd(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class GitHubTrendingSource(Source):
    """Approximates GitHub trending via the Search API."""

    name = "github"

    def __init__(self, settings: Settings, client: GitHubClient | None = None):
        self.settings = settings
        self.cfg = settings.github
        self.client = client or GitHubClient(token=settings.github_token)

    def _filters(self) -> str:
        parts: list[str] = []
        for t in self.cfg.topics:
            parts.append(f"topic:{t}")
        return (" " + " ".join(parts)) if parts else ""

    def _build_queries(self) -> list[tuple[str, str]]:
        """Return (query, sort) pairs. One per language filter (or a single
        unfiltered query when no languages are configured)."""
        created_since = _ymd(self.cfg.rising_created_within_days)
        pushed_since = _ymd(self.cfg.active_pushed_within_days)
        filters = self._filters()
        langs = self.cfg.languages or [None]
        queries: list[tuple[str, str]] = []
        for lang in langs:
            lang_q = f" language:{lang}" if lang else ""
            rising = (
                f"stars:>={self.cfg.rising_min_stars} "
                f"created:>={created_since}{lang_q}{filters}"
            )
            active = (
                f"stars:>={self.cfg.min_stars} "
                f"pushed:>={pushed_since}{lang_q}{filters}"
            )
            queries.append((rising, "stars"))
            queries.append((active, "stars"))
        return queries

    def fetch_candidates(self) -> list[Repository]:
        by_name: dict[str, Repository] = {}
        for q, sort in self._build_queries():
            try:
                items = self.client.search_repositories(
                    q, sort=sort, per_page=self.cfg.per_query_limit
                )
            except httpx.HTTPError as exc:  # one bad query shouldn't kill the run
                log.warning("GitHub search failed for query %r: %s", q, exc)
                continue
            for item in items:
                repo = repo_from_api(item)
                if repo.full_name and repo.full_name not in by_name:
                    by_name[repo.full_name] = repo
            log.info("query=%r -> %d items (total unique=%d)", q, len(items), len(by_name))
        return list(by_name.values())
