"""Writer agent: turns an enriched repository into a Draft."""

from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Draft, Repository
from . import prompts
from .model_client import ModelClient
from .parsing import extract_json

log = get_logger(__name__)


class WriterAgent:
    def __init__(self, model: ModelClient, settings: Settings):
        self.model = model
        self.settings = settings

    def write(self, repo: Repository) -> Draft:
        system = prompts.writer_system(self.settings)
        prompt = prompts.writer_prompt(repo, self.settings)
        raw = self.model.complete(
            system=system, prompt=prompt, max_tokens=2400, temperature=0.5, json_mode=True
        )
        data = extract_json(raw)
        draft = Draft(
            title=str(data.get("title", "")).strip(),
            body_markdown=str(data.get("body_markdown", "")).strip(),
            summary=str(data.get("summary", "")).strip(),
            tags=[str(t).strip() for t in data.get("tags", []) if str(t).strip()][
                : self.settings.content.max_tags
            ],
            angle=str(data.get("angle", "")).strip(),
            model=self.model.model,
            raw=raw,
        )
        log.info("writer produced draft: %r (%d chars body)",
                 draft.title, len(draft.body_markdown))
        return draft
