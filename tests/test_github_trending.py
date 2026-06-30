import dataclasses

import httpx

from content_engine.sources.base import Source
from content_engine.sources.github import GitHubClient
from content_engine.sources.github_trending import (
    GitHubTrendingHtmlSource,
    parse_trending,
)

from .conftest import make_repo

# A trimmed but structurally faithful slice of github.com/trending: six repo rows
# (the heading anchor lives in <h2 class="h3 lh-condensed">), plus chrome links
# that must be ignored (sign-in, sponsor, and a /stargazers sub-path).
_ROWS = "".join(
    f'<article class="Box-row">'
    f'<h2 class="h3 lh-condensed"><a href="/{slug}">{slug}</a></h2>'
    f'<a href="/{slug}/stargazers">stars</a>'
    f"</article>"
    for slug in ["acme/widget", "foo/bar", "octo/cat", "data/lake", "ml/kit", "rs/tool"]
)
_SAMPLE_HTML = (
    '<html><body>'
    '<a href="/login?return_to=%2Ftrending">Sign in</a>'
    '<a href="/sponsors/acme">Sponsor</a>'
    f'<div data-hpc>{_ROWS}</div>'
    '</body></html>'
)

_EXPECTED = ["acme/widget", "foo/bar", "octo/cat", "data/lake", "ml/kit", "rs/tool"]


def _repo_json(slug: str) -> dict:
    owner, name = slug.split("/")
    return {
        "full_name": slug, "name": name, "owner": {"login": owner},
        "html_url": f"https://github.com/{slug}", "description": "desc",
        "homepage": "", "language": "Python", "stargazers_count": 1000,
        "forks_count": 10, "watchers_count": 1000, "open_issues_count": 1,
        "topics": ["ai"], "license": {"spdx_id": "MIT"},
        "created_at": "2025-01-01T00:00:00Z", "pushed_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z", "archived": False, "fork": False,
        "default_branch": "main",
    }


class _FakeFallback(Source):
    name = "fake_fallback"

    def __init__(self, repos):
        self.repos = repos
        self.called = False

    def fetch_candidates(self):
        self.called = True
        return list(self.repos)


def _source(settings, handler, fallback=None, min_results_fallback=1):
    """Wire a trending source whose page fetch + API hydration both hit `handler`."""
    s = dataclasses.replace(
        settings,
        trending=dataclasses.replace(settings.trending, min_results_fallback=min_results_fallback),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubTrendingHtmlSource(
        s, client=GitHubClient(client=client), fallback=fallback, http_client=client
    )


# --------------------------------------------------------------------- parsing
def test_parse_trending_extracts_ranked_slugs():
    assert parse_trending(_SAMPLE_HTML) == _EXPECTED


def test_parse_trending_ignores_chrome_and_subpaths():
    slugs = parse_trending(_SAMPLE_HTML)
    assert not any(s.startswith("login") or s.startswith("sponsors") for s in slugs)
    assert all("/stargazers" not in s for s in slugs)


def test_parse_trending_empty_on_garbage():
    assert parse_trending("<html><body>no repos here</body></html>") == []


# ----------------------------------------------------------------- hydration
def test_fetch_candidates_hydrates_in_page_order_with_rank(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, text=_SAMPLE_HTML)
        if request.url.path.startswith("/repos/"):
            return httpx.Response(200, json=_repo_json(request.url.path[len("/repos/"):]))
        return httpx.Response(404, json={})

    repos = _source(settings, handler).fetch_candidates()
    assert [r.full_name for r in repos] == _EXPECTED
    assert [r.trending_rank for r in repos] == [0, 1, 2, 3, 4, 5]


def test_fetch_candidates_skips_repo_that_fails_hydration(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, text=_SAMPLE_HTML)
        slug = request.url.path[len("/repos/"):]
        if slug == "octo/cat":            # one bad row must not kill the run
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=_repo_json(slug))

    names = [r.full_name for r in _source(settings, handler).fetch_candidates()]
    assert "octo/cat" not in names
    assert names == ["acme/widget", "foo/bar", "data/lake", "ml/kit", "rs/tool"]


# ------------------------------------------------------------------ fallback
def test_falls_back_to_search_when_parse_thin(settings):
    fallback = _FakeFallback([make_repo(full_name="fb/one")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, text="<html><body>nothing</body></html>")
        return httpx.Response(200, json=_repo_json(request.url.path[len("/repos/"):]))

    # default min_results_fallback (5) > 0 parsed slugs -> fallback fires
    src = _source(settings, handler, fallback=fallback, min_results_fallback=5)
    repos = src.fetch_candidates()
    assert fallback.called is True
    assert [r.full_name for r in repos] == ["fb/one"]
