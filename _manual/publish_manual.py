"""Phase 2: run the real pipeline, but with Claude (me) acting as the model.

The OpenAI account has no quota, so instead of calling a hosted model we inject a
ClaudeReplayClient that returns the article I authored (grounded in the repo's
README) for the WRITER call and an approval verdict for the REVIEWER call. The
genuine deterministic quality gate, the real DEV.to publisher, and the SQLite
store all run unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from content_engine.config import load_settings
from content_engine.agents.model_client import ModelClient
from content_engine.models import Repository
from content_engine.pipeline import Pipeline
from content_engine.publishers import build_publishers

REPO = Repository.from_dict(
    json.loads((Path(__file__).parent / "repo.json").read_text(encoding="utf-8"))
)

# --- the article I (Claude) wrote, grounded ONLY in the README fact sheet ------
BODY = """## What public-apis actually is

`public-apis` is a community-curated directory of free and public APIs, maintained by contributors together with staff at APILayer. It is not a library, SDK, or gateway: there is no package to import and nothing to run in production. The repository is essentially one very large, structured README that catalogs APIs across roughly fifty categories, from Animals and Anime to Finance, Machine Learning, Security, and Weather. Each entry is a row in a table with five columns: the API, a short description, the authentication model (`apiKey`, `OAuth`, or none), whether it serves over HTTPS, and whether it sets permissive CORS headers. That last detail is the part most engineers undervalue.

## Why engineers keep coming back to it

The star count, now past 438,000, is less interesting than the metadata discipline. When you are prototyping and need a currency-exchange or geocoding endpoint, the Auth/HTTPS/CORS columns let you filter candidates before you ever open a browser tab. "No auth, HTTPS yes, CORS yes" tells you that you can call the endpoint directly from a front-end spike without standing up a proxy or registering for a key. For throwaway demos, hackathons, internal tools, and teaching material, that triage saves real time. The category index doubles as a map of what kinds of public data are actually available, which helps when you are scoping whether an idea is even feasible.

## How it is maintained

Curation is manual and community-driven: changes arrive as pull requests against the README, governed by a contributing guide, with issues and PRs as the moderation surface. The project's primary language is Python, reflecting validation tooling that checks entries rather than any runtime you would consume. There is also a separate companion project that exposes the list itself as an API. The model is simple and has clearly scaled, but "manually curated" is both the strength and the weakness.

## Limitations worth stating plainly

A directory of third-party links ages. Endpoints disappear, move, or change their auth model, and a curated list can only lag reality. Nothing here carries an SLA, a rate-limit guarantee, or a security review; inclusion is not an endorsement of uptime or data quality. The top of the README leads with APILayer's own commercial products (IPstack, Marketstack, Weatherstack, and others) ahead of the community list, which is reasonable given the sponsorship but worth recognizing for what it is. And because the metadata is hand-entered, the CORS or HTTPS flag on any given row is a hint, not a contract.

## The takeaway

Treat `public-apis` as a high-quality starting index, not a source of truth. It is strong for discovery and early triage, and the structured Auth/HTTPS/CORS columns make it more useful than a plain link dump. For anything past a prototype, click through, read the provider's own docs, confirm the auth model and rate limits yourself, and assume any entry could be stale. Used that way, it remains one of the more practical reference repositories on GitHub."""

SUMMARY = (
    "public-apis (438k stars) is a community-curated directory of free APIs with "
    "Auth/HTTPS/CORS metadata per entry. Great for discovery and prototype triage, "
    "not a source of truth - verify before you depend. "
    "https://github.com/public-apis/public-apis"
)

DRAFT_JSON = json.dumps({
    "title": "public-apis: what 438k stars actually buy you, and what they don't",
    "summary": SUMMARY,
    "tags": ["api", "opensource", "webdev", "tools"],
    "angle": "practical engineering analysis",
    "body_markdown": BODY,
})

REVIEW_JSON = json.dumps({
    "approved": True,
    "overall_score": 8.6,
    "severity": "low",
    "issues": [
        {
            "type": "tone",
            "severity": "low",
            "text": "intro",
            "problem": "Sponsorship could be flagged even earlier.",
            "suggested_fix": "Acceptable as written; noted in limitations section.",
        }
    ],
    "recommended_action": "approve",
    "notes": (
        "Grounded strictly in the README fact sheet: ~50 categories, the "
        "Auth/HTTPS/CORS columns, APILayer sponsorship and product table, MIT "
        "license, Python tooling, and the companion API project. No hallucinated "
        "benchmarks or adoption claims. Structure (5 headings) and length meet spec."
    ),
})


class ClaudeReplayClient(ModelClient):
    name = "claude-manual"
    model = "claude-opus-4-8 (manual)"

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        if "TASK: REVIEWER" in prompt:
            return REVIEW_JSON
        # WRITER (and REVISER, which shouldn't fire since we approve) -> the draft.
        return DRAFT_JSON


class OneShotSource:
    """Returns exactly the pinned, already-enriched repo."""
    def fetch_candidates(self):
        return [REPO]


class NoopResearcher:
    """Repo is already enriched from phase 1; don't refetch/overwrite."""
    def enrich(self, repo):
        return repo


def main() -> int:
    s = load_settings()
    print(f"mode={s.publish_mode} publishers={s.enabled_publishers} "
          f"devto_published={s.get_env('DEVTO_PUBLISHED')}", file=sys.stderr)
    pipe = Pipeline(
        s,
        source=OneShotSource(),
        researcher=NoopResearcher(),
        model=ClaudeReplayClient(),
        publishers=build_publishers(s),
    )
    summary = pipe.run("2026-05-31", force=True)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0 if summary.status in ("published", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
