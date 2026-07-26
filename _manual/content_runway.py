"""Report how many days of content each distribution lane still has.

Every lane here runs on its own daily schedule and every one of them exits cleanly when
it runs out, so a dry lane looks exactly like a healthy one in the logs. The Agent
Palisade guide syndication sat exhausted for eight days before anyone noticed. Run this
to see the runway in one place.

  PYTHONPATH=src python _manual/content_runway.py

Needs DEVTO_API_KEY in the environment (or .env) for the Palisade check, which dedups
against the DEV.to account itself. Everything else is read from local state.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent
QUEUE = HERE / "queue"
PUBLISHED = HERE / "published"
LI = HERE / "linkedin_page"


def load_env() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def jload(p: pathlib.Path) -> dict | list:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def published_prefixes() -> set[str]:
    """Local files plus anything the CI publisher has committed to origin/main."""
    local = {p.name.split("-")[0] for p in PUBLISHED.glob("0*.json")}
    try:
        subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "origin"],
                       capture_output=True, timeout=90)
        out = subprocess.run(["git", "-C", str(REPO), "ls-tree", "--name-only",
                              "origin/main:_manual/published"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            local |= {n.split("-")[0] for n in out.stdout.split() if n.endswith(".json")}
    except Exception:
        pass
    return local


def palisade_exhausted() -> bool | None:
    """True when every site guide has already been syndicated."""
    if not os.environ.get("DEVTO_API_KEY"):
        return None
    try:
        r = subprocess.run([sys.executable, str(HERE / "palisade_next.py")],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "PYTHONPATH": "src"}, cwd=str(REPO))
        return '"exhausted": true' in r.stdout or '"exhausted":true' in r.stdout
    except Exception:
        return None


def main() -> None:
    load_env()
    rows: list[tuple[str, int, str]] = []

    queued = sorted(p.stem for p in QUEUE.glob("*.json"))
    rows.append(("DEV.to + Bluesky + Mastodon (trending stream, 1/day)", len(queued),
                 f"next: {queued[0] if queued else 'none'}"))

    pub = published_prefixes()
    personal_done = set(jload(LI / "posted_personal.json")["posted"])
    personal_todo = sorted(pub - personal_done)
    rows.append(("LinkedIn articles, personal profile (1/day)", len(personal_todo),
                 f"next: {personal_todo[0] if personal_todo else 'none'}"))

    company_q = {e["prefix"] for e in jload(LI / "queue.json")}
    company_done = set(jload(LI / "posted.json")["posted"])
    company_todo = sorted(company_q - company_done)
    rows.append(("LinkedIn link posts, Agent Palisade page (1/day)", len(company_todo),
                 f"next: {company_todo[0] if company_todo else 'none'}"))

    width = max(len(r[0]) for r in rows)
    print("content runway\n")
    for name, n, note in rows:
        flag = "EMPTY" if n == 0 else ("LOW" if n <= 3 else "ok")
        print(f"  {name.ljust(width)}  {n:>3} items  {flag:<5}  {note}")

    ex = palisade_exhausted()
    state = "EXHAUSTED (needs new guides on agentpalisade.com)" if ex else (
        "ok" if ex is False else "unknown (set DEVTO_API_KEY)")
    print(f"  {'Agent Palisade guide syndication (1/day)'.ljust(width)}  {'  -':>3} items  "
          f"{'':<5}  {state}")

    # what the company lane is missing, since that queue is hand-written per item
    missing = sorted(pub - company_q)
    if missing:
        print(f"\n  company-page queue is missing {len(missing)} published article(s): "
              f"{', '.join(missing)}")
        print("  add an entry per prefix to _manual/linkedin_page/queue.json "
              "(prefix, title, url from devto_urls.json, text = a 2-3 sentence blurb)")


if __name__ == "__main__":
    main()
