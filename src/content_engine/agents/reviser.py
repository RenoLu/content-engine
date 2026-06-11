"""Reviser agent: rewrites a Draft to address reviewer issues."""

from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Draft, Repository, ReviewResult
from . import prompts
from .model_client import ModelClient
from .parsing import extract_json

log = get_logger(__name__)


class ReviserAgent:
    def __init__(self, model: ModelClient, settings: Settings):
        self.model = model
        self.settings = settings

    def revise(self, repo: Repository, draft: Draft, review: ReviewResult,
               quality_issues: list[str] | None = None) -> Draft:
        system = prompts.reviser_system(self.settings)
        prompt = prompts.reviser_prompt(repo, draft, review, self.settings,
                                        quality_issues=quality_issues)
        raw = self.model.complete(
            system=system, prompt=prompt, max_tokens=2400, temperature=0.4, json_mode=True
        )
        data = extract_json(raw)
        revised = Draft(
            title=str(data.get("title", draft.title)).strip(),
            body_markdown=str(data.get("body_markdown", draft.body_markdown)).strip(),
            summary=str(data.get("summary", draft.summary)).strip(),
            tags=[str(t).strip() for t in data.get("tags", draft.tags) if str(t).strip()][
                : self.settings.content.max_tags
            ],
            angle=str(data.get("angle", draft.angle)).strip(),
            model=self.model.model,
            raw=raw,
        )
        log.info("reviser produced revised draft (%d chars body)", len(revised.body_markdown))
        return revised
