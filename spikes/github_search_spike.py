"""Spike: validate the GitHub Search API trending approximation.

Runs the same two queries the source uses, prints the top repos, and reports the
search rate-limit headers. No credentials required (set GITHUB_TOKEN to raise the
limit). Usage:  PYTHONPATH=src python spikes/github_search_spike.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from content_engine.config import load_settings  # noqa: E402
from content_engine.sources.github import GitHubTrendingSource  # noqa: E402


def main() -> int:
    settings = load_settings()
    src = GitHubTrendingSource(settings)
    print(f"GITHUB_TOKEN set: {bool(settings.github_token)}")
    print("queries:")
    for q, sort in src._build_queries():
        print(f"  - sort={sort}: {q}")

    repos = src.fetch_candidates()
    repos.sort(key=lambda r: r.stars, reverse=True)
    print(f"\nfetched {len(repos)} unique candidates. top 10 by stars:")
    for r in repos[:10]:
        print(f"  {r.stars:>7}★  {r.full_name:<40} {r.language or '-':<12} "
              f"pushed={r.pushed_at}")

    # Report search rate-limit status (this endpoint does not count against quota).
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    rl = httpx.get("https://api.github.com/rate_limit", headers=headers, timeout=30).json()
    search = rl.get("resources", {}).get("search", {})
    reset = search.get("reset")
    reset_in = (datetime.fromtimestamp(reset, tz=timezone.utc) - datetime.now(timezone.utc)) \
        if reset else timedelta(0)
    print(f"\nsearch rate limit: {search.get('remaining')}/{search.get('limit')} "
          f"remaining (resets in ~{int(reset_in.total_seconds())}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
