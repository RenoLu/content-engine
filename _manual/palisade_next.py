"""Print the next unsyndicated palisade guide, deduping against DEV.to itself.

Stateless companion to palisade_manual.py for cloud/scheduled runs where the
local SQLite store doesn't persist: the source of truth for "already
syndicated" is the DEV.to account — any article (draft or published) whose
canonical_url matches a guide counts as done.

Usage:  DEVTO_API_KEY=... python _manual/palisade_next.py
Output: JSON for the next guide, or {"exhausted": true} when the queue is dry.
Exit codes: 0 = guide printed, 2 = exhausted, 1 = error.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

from content_engine.campaigns import load_guides

_API = "https://dev.to/api/articles/me/all"


def syndicated_canonicals(api_key: str) -> set[str]:
    urls: set[str] = set()
    page = 1
    while True:
        resp = httpx.get(
            _API,
            params={"per_page": 100, "page": page},
            headers={"api-key": api_key, "Accept": "application/vnd.forem.api-v1+json"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return urls
        for article in batch:
            canonical = article.get("canonical_url")
            if canonical:
                urls.add(canonical.rstrip("/"))
        page += 1


def main() -> int:
    api_key = os.environ.get("DEVTO_API_KEY")
    if not api_key:
        print(json.dumps({"error": "DEVTO_API_KEY not set"}))
        return 1
    done = syndicated_canonicals(api_key)
    for guide in load_guides():
        if guide.url.rstrip("/") not in done:
            print(json.dumps({
                "slug": guide.slug,
                "title": guide.title,
                "summary": guide.summary,
                "url": guide.url,
                "key_points": guide.key_points,
                "tags": guide.tags,
            }, ensure_ascii=False))
            return 0
    print(json.dumps({"exhausted": True}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
