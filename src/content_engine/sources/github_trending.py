"""GitHub trending (real page) source.

Unlike :class:`GitHubTrendingSource` (which *approximates* trending via the
Search API), this source reads the actual ``github.com/trending`` page to get the
canonical, ranked list of repositories, then **hydrates each repo's metadata
through the official GitHub REST API** (``GET /repos/{owner}/{repo}``). READMEs
stay lazy — they're fetched later in the pipeline's selection step.

``github.com/trending`` has no official API, so we parse the public,
unauthenticated HTML. This is a deliberate, narrow exception to the project's
"official APIs only" rule (documented in docs/API_FINDINGS.md): the page needs no
login, and if GitHub changes the markup (or the fetch fails) we fall back to —
and backfill from — the Search-API source so a run never dies on a layout change.

The HTML is parsed with the stdlib ``html.parser`` (no new dependency): we capture
the repo anchor inside each ``<h2 class="... lh-condensed">`` heading, which is the
stable structural signal for a trending row.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import quote, urlencode

import httpx

from ..config import Settings
from ..http_util import request_with_retry
from ..logging_setup import get_logger
from ..models import Repository
from .base import Source
from .github import GitHubClient, GitHubTrendingSource, repo_from_api

log = get_logger(__name__)

_TRENDING_BASE = "https://github.com/trending"
# A normal browser User-Agent — github.com/trending serves HTML to browsers.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 content-engine/0.1"
)

# First path segments that are GitHub chrome, never a repo owner.
_CHROME_OWNERS = {
    "login", "logout", "join", "sponsors", "topics", "collections", "trending",
    "marketplace", "about", "features", "pricing", "new", "settings",
    "notifications", "explore", "search", "orgs", "apps", "contact", "site",
    "security", "readme", "customer-stories", "team", "enterprise", "github",
}


def _slug_from_href(href: str | None) -> str | None:
    """Return ``owner/name`` if ``href`` is a plain repo link, else ``None``.

    Trending heading links are exactly ``/owner/name`` (no query/hash, no extra
    path segments). Anything else (avatars ``/user``, ``/owner/repo/stargazers``,
    chrome links) is rejected.
    """
    if not href:
        return None
    href = href.strip()
    if not href.startswith("/") or "?" in href or "#" in href:
        return None
    parts = href.strip("/").split("/")
    if len(parts) != 2:
        return None
    owner, name = parts
    if not owner or not name or owner.lower() in _CHROME_OWNERS:
        return None
    return f"{owner}/{name}"


class _TrendingParser(HTMLParser):
    """Collect ranked repo slugs from trending HTML, in page order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_depth = 0          # >0 while inside a trending <h2> heading
        self._captured_in_heading = False
        self.slugs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: (v or "") for k, v in attrs}
        if tag == "h2" and "lh-condensed" in d.get("class", ""):
            self._heading_depth = 1
            self._captured_in_heading = False
        elif self._heading_depth:
            if tag == "h2":
                self._heading_depth += 1   # tolerate nested <h2> (shouldn't happen)
            if tag == "a" and not self._captured_in_heading:
                slug = _slug_from_href(d.get("href"))
                if slug and slug not in self.slugs:
                    self.slugs.append(slug)
                    self._captured_in_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._heading_depth:
            self._heading_depth -= 1


def parse_trending(html: str) -> list[str]:
    """Parse trending HTML into a ranked, de-duplicated list of ``owner/name``."""
    parser = _TrendingParser()
    parser.feed(html)
    return parser.slugs


class GitHubTrendingHtmlSource(Source):
    """Reads the real github.com/trending page; hydrates via the official API."""

    name = "github_trending"

    def __init__(self, settings: Settings, client: GitHubClient | None = None,
                 fallback: Source | None = None,
                 http_client: httpx.Client | None = None):
        self.settings = settings
        self.cfg = settings.trending
        self.client = client or GitHubClient(token=settings.github_token)
        self.fallback = fallback or GitHubTrendingSource(settings)
        self._http_client = http_client
        self._owns_http_client = http_client is None

    def _get_http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=30.0, follow_redirects=True)
        return self._http_client

    def _trending_url(self) -> str:
        url = _TRENDING_BASE
        if self.cfg.language:
            url = f"{url}/{quote(self.cfg.language, safe='')}"
        params: list[tuple[str, str]] = []
        if self.cfg.since:
            params.append(("since", self.cfg.since))
        if self.cfg.spoken_language_code:
            params.append(("spoken_language_code", self.cfg.spoken_language_code))
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _fetch_trending_html(self) -> str:
        resp = request_with_retry(
            self._get_http_client(), "GET", self._trending_url(),
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"},
        )
        resp.raise_for_status()
        return resp.text

    def fetch_candidates(self) -> list[Repository]:
        limit = self.cfg.limit
        min_fallback = self.cfg.min_results_fallback

        try:
            slugs = parse_trending(self._fetch_trending_html())[:limit]
        except (httpx.HTTPError, ValueError) as exc:  # network or parse failure
            log.warning("trending page fetch/parse failed (%s); using search API", exc)
            return self.fallback.fetch_candidates()

        if len(slugs) < min_fallback:
            log.warning("trending page yielded %d repos (<%d); using search API",
                        len(slugs), min_fallback)
            return self.fallback.fetch_candidates()

        repos: list[Repository] = []
        for rank, slug in enumerate(slugs):
            try:
                repo = repo_from_api(self.client.get_repo(slug))
            except httpx.HTTPError as exc:  # one bad slug shouldn't kill the run
                log.warning("skip trending repo %s: hydration failed: %s", slug, exc)
                continue
            if not repo.full_name:
                continue
            repo.trending_rank = rank
            repos.append(repo)
        log.info("trending_html: %d slugs -> %d hydrated repos", len(slugs), len(repos))

        # If hydration thinned the list below the floor, backfill (don't replace)
        # from the search source so we still have a healthy candidate pool.
        if len(repos) < min_fallback:
            log.warning("only %d trending repos hydrated (<%d); backfilling from search API",
                        len(repos), min_fallback)
            have = {r.full_name for r in repos}
            for r in self.fallback.fetch_candidates():
                if r.full_name and r.full_name not in have:
                    repos.append(r)
                    have.add(r.full_name)
        return repos
