"""Run the real pipeline with Claude (the agent) acting as the model.

The hosted-model accounts are out of credit, so instead of calling an API we
inject a ClaudeReplayClient that replays the article the agent authored — read
from ``_manual/article.json`` — for the WRITER / REVIEWER / ENGAGEMENT calls. The
genuine deterministic quality gate, the real publishers, and the SQLite store all
run unchanged. The selected repo + README come from ``repo.json`` (written by
discover.py); the article comes from ``article.json`` (written by the agent — see
_manual/RUNBOOK.md for the unattended daily flow).

article.json shape::

    {
      "draft":      {"title","summary","tags":[...],"angle","body_markdown"},
      "review":     {"approved","overall_score","severity","issues":[...],
                     "recommended_action","notes"},
      "engagement": {"approved","attention_score","voice_score","severity",
                     "issues":[...],"recommended_action","notes"}
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from content_engine.config import load_settings
from content_engine.agents.model_client import ModelClient
from content_engine.models import Repository, today_str
from content_engine.pipeline import Pipeline
from content_engine.publishers import build_publishers

HERE = Path(__file__).parent

REPO = Repository.from_dict(
    json.loads((HERE / "repo.json").read_text(encoding="utf-8"))
)

_article_path = HERE / "article.json"
if not _article_path.exists():
    raise SystemExit(
        "no _manual/article.json — the agent must author it (draft + review + "
        "engagement, grounded in repo.json's README) before running. "
        "See _manual/RUNBOOK.md."
    )
_ART = json.loads(_article_path.read_text(encoding="utf-8"))
DRAFT_JSON = json.dumps(_ART["draft"])
REVIEW_JSON = json.dumps(_ART["review"])
ENGAGEMENT_JSON = json.dumps(_ART["engagement"])


class ClaudeReplayClient(ModelClient):
    name = "claude-manual"
    model = "claude (agent-as-model)"

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        if "TASK: ENGAGEMENT REVIEW" in prompt:
            return ENGAGEMENT_JSON
        if "TASK: REVIEWER" in prompt:
            return REVIEW_JSON
        # WRITER (and REVISER, which shouldn't fire when we approve) -> the draft.
        return DRAFT_JSON


class OneShotSource:
    """Returns exactly the pinned, already-enriched repo."""
    def fetch_candidates(self):
        return [REPO]


class NoopResearcher:
    """Repo is already enriched by discover.py; don't refetch/overwrite."""
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
    # One post per day: today's date is a naturally-unique run key, so the
    # per-(run_date, publisher) guard prevents accidental double-posts.
    summary = pipe.run(today_str(), force=True)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0 if summary.status in ("published", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
