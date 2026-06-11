"""Palisade campaign with Claude (me) acting as the model — zero API cost.

Weekly loop:
  1. Claude Code writes/updates ``_manual/palisade_draft.json`` with the
     adapted article for the NEXT guide in the queue:
        {"title": ..., "summary": ..., "tags": [...], "body_markdown": ...}
  2. Run:  python _manual/palisade_manual.py [--live]
     Default is dry-run. ``--live`` publishes for real (DEVTO_PUBLISHED in
     .env controls DEV.to draft-vs-public; keep it false for the first run so
     the post lands as a reviewable DEV.to draft).

The real quality gate, DEV.to publisher, and campaign store run unchanged —
only the writer model call is replayed from the JSON file.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from content_engine.agents.model_client import ModelClient
from content_engine.campaigns import PalisadeCampaign
from content_engine.config import load_settings

DRAFT_PATH = Path(__file__).parent / "palisade_draft.json"


class ClaudeReplayClient(ModelClient):
    """Returns the human/Claude-authored draft for the writer call."""

    name = "claude-replay"
    model = "claude-fable-5-manual"

    def __init__(self, draft_json: str):
        self._draft_json = draft_json

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        return self._draft_json


def main() -> int:
    live = "--live" in sys.argv
    if not DRAFT_PATH.exists():
        print(f"Missing {DRAFT_PATH} — author the adapted article first.")
        return 1
    draft_json = DRAFT_PATH.read_text(encoding="utf-8")
    json.loads(draft_json)  # fail fast on malformed JSON

    settings = load_settings()
    settings = dataclasses.replace(settings, publish_mode="live" if live else "dry_run")

    campaign = PalisadeCampaign(settings, model=ClaudeReplayClient(draft_json))
    # force=True: the dry-run preview earlier the same day is terminal; the
    # live rerun must re-pick the same guide rather than skip or advance.
    summary = campaign.run(force=True)
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    return 1 if summary.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
