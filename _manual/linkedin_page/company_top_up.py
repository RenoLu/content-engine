"""Keep the Agent Palisade page queue fed from what has already gone live on DEV.to.

The company lane needs three things per post: a prefix, the dev.to URL, and a line of
commentary to sit above it. Only the commentary is a judgement call, so it is written
ahead of time in company_blurbs.json and this script does the mechanical half: find the
articles the daily publisher has put live, look up their dev.to URLs, and append an entry
for each one the queue is missing.

That closes the hole that dried the lane out on 2026-07-21. queue.json was a hand-written
list of twelve, and when the twelfth posted nothing said so: the poster logs "queue empty"
and exits 0, which reads exactly like a healthy run.

Published articles are read from origin/main as well as this checkout, because the dev.to
publisher is a GitHub Action that commits from CI and this working copy runs behind it.

A published article with no blurb still gets queued, using its own summary as the
commentary, so the lane cannot starve while someone gets around to writing one. Those are
reported so they can be upgraded.

  DEVTO_API_KEY=... python _manual/linkedin_page/company_top_up.py [--dry]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC_DIR = HERE.parent / "published"
QUEUE = HERE / "queue.json"
POSTED = HERE / "posted.json"
BLURBS = HERE / "company_blurbs.json"
URL_MAP = HERE / "devto_urls.json"


def jload(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def load_env() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def published_articles() -> dict[str, dict]:
    """prefix -> article json, from this checkout plus origin/main."""
    out: dict[str, dict] = {}
    for f in sorted(SRC_DIR.glob("0*.json")):
        out[f.name.split("-")[0]] = jload(f)

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(REPO), *args],
                               capture_output=True, text=True, timeout=90, encoding="utf-8")
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    git("fetch", "-q", "origin")
    listing = git("ls-tree", "--name-only", "origin/main:_manual/published") or ""
    for name in listing.split():
        prefix = name.split("-")[0]
        if not name.endswith(".json") or prefix in out:
            continue
        blob = git("show", f"origin/main:_manual/published/{name}")
        if blob:
            try:
                out[prefix] = json.loads(blob)
            except json.JSONDecodeError:
                pass
    return out


def devto_urls(articles: dict[str, dict]) -> dict[str, str]:
    key = os.environ.get("DEVTO_API_KEY")
    if not key:
        print("DEVTO_API_KEY not set, falling back to the saved URL map")
        return jload(URL_MAP) if URL_MAP.exists() else {}
    req = urllib.request.Request("https://dev.to/api/articles/me/published?per_page=100",
                                 headers={"api-key": key, "User-Agent": "content-engine"})
    live = json.load(urllib.request.urlopen(req, timeout=60))
    by_title = {norm_title(a["title"]): a["url"] for a in live}
    urls = {p: by_title[norm_title(a["title"])] for p, a in articles.items()
            if norm_title(a["title"]) in by_title}
    URL_MAP.write_text(json.dumps(urls, indent=2), encoding="utf-8")
    return urls


def derived_blurb(article: dict) -> str:
    """Fallback commentary: the article's own summary, minus its trailing repo URL."""
    summary = re.sub(r"https?://\S+\s*$", "", article.get("summary", "")).strip().rstrip(":")
    return f"{summary} Our read." if summary else ""


def main() -> None:
    load_env()
    dry = "--dry" in sys.argv
    articles = published_articles()
    urls = devto_urls(articles)
    blurbs = {k: v for k, v in jload(BLURBS).items() if not k.startswith("_")}
    queue = jload(QUEUE)
    have = {e["prefix"] for e in queue}
    posted = set(jload(POSTED).get("posted", []))

    added, derived, no_url = [], [], []
    for prefix in sorted(articles):
        if prefix in have:
            continue
        url = urls.get(prefix)
        if not url:
            no_url.append(prefix)
            continue
        entry = blurbs.get(prefix)
        text = entry["text"] if entry else derived_blurb(articles[prefix])
        if not text:
            continue
        if not entry:
            derived.append(prefix)
        queue.append({
            "prefix": prefix,
            "title": (entry or {}).get("title") or articles[prefix]["title"][:40],
            "url": url,
            "text": text,
        })
        added.append(prefix)

    if added and not dry:
        QUEUE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    verb = "would add" if dry else "added"
    print(f"{verb} {len(added)}: {added or 'nothing'}")
    if derived:
        print(f"  using a derived blurb (worth upgrading in company_blurbs.json): {derived}")
    if no_url:
        print(f"  published but no dev.to URL matched: {no_url}")
    backlog = sorted({e['prefix'] for e in queue} - posted)
    print(f"queue depth after top-up: {len(backlog)} unposted ({backlog[:6]}{'...' if len(backlog) > 6 else ''})")
    ready = [p for p in sorted(blurbs) if p not in have and p not in urls]
    if ready:
        print(f"blurbs waiting on a dev.to publish: {len(ready)} ({ready[0]} next)")


if __name__ == "__main__":
    main()
