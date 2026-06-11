import httpx

from content_engine.sources.github import (
    GitHubClient,
    GitHubTrendingSource,
    extract_links,
    repo_from_api,
)

_SEARCH_ITEM = {
    "full_name": "acme/widget",
    "name": "widget",
    "owner": {"login": "acme"},
    "html_url": "https://github.com/acme/widget",
    "description": "A widget",
    "homepage": "https://widget.dev",
    "language": "Rust",
    "stargazers_count": 1200,
    "forks_count": 80,
    "watchers_count": 1200,
    "open_issues_count": 5,
    "topics": ["rust", "cli"],
    "license": {"spdx_id": "MIT"},
    "created_at": "2025-01-01T00:00:00Z",
    "pushed_at": "2026-05-30T00:00:00Z",
    "updated_at": "2026-05-30T00:00:00Z",
    "archived": False,
    "fork": False,
    "default_branch": "main",
}


def test_repo_from_api_maps_fields():
    repo = repo_from_api(_SEARCH_ITEM)
    assert repo.full_name == "acme/widget"
    assert repo.owner == "acme"
    assert repo.stars == 1200
    assert repo.license == "MIT"
    assert repo.topics == ["rust", "cli"]
    assert repo.is_archived is False


def test_extract_links():
    md = "See [docs](https://x.dev/docs) and [repo](https://x.dev/repo). Dup [docs](https://x.dev/docs)."
    links = extract_links(md)
    assert links == ["https://x.dev/docs", "https://x.dev/repo"]


def test_build_queries_include_filters(settings):
    src = GitHubTrendingSource(settings)
    queries = src._build_queries()
    # one (rising, active) pair per language (none configured -> single pair)
    assert len(queries) == 2
    rising_q = queries[0][0]
    active_q = queries[1][0]
    assert "created:>=" in rising_q
    assert "pushed:>=" in active_q
    assert "stars:>=" in rising_q and "stars:>=" in active_q


def test_fetch_candidates_dedupes_across_queries(settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # return the same single item for every query -> must be deduped to 1
        return httpx.Response(200, json={"items": [_SEARCH_ITEM]})

    client = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    src = GitHubTrendingSource(settings, client=client)
    repos = src.fetch_candidates()
    assert calls["n"] == 2          # two queries issued
    assert len(repos) == 1          # deduped by full_name
    assert repos[0].full_name == "acme/widget"


def test_get_readme_returns_empty_on_404(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.get_readme("acme/widget") == ""
