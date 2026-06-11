"""Palisade campaign: syndicate agentpalisade.com resource guides to DEV.to.

A slim sibling of the daily ``Pipeline``: same idempotency rules (one run per
date, never reuse a guide, dry-run by default, deterministic quality gate
before anything live), but the candidate set is a fixed, ordered queue of
already-published site guides (``config/palisade_guides.json``) rather than
GitHub trending. Each syndicated post sets ``canonical_url`` to the guide's
page on agentpalisade.com and ends with the campaign CTA.

The campaign keeps its own SQLite database (``data/palisade.sqlite3`` by
default) because ``runs.run_date`` is UNIQUE per database and the daily GitHub
pipeline owns the main one.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.model_client import ModelClient, build_model_client
from ..agents.parsing import extract_json
from ..config import Settings
from ..logging_setup import get_logger
from ..models import (
    Draft,
    Post,
    PublishResult,
    Repository,
    RunStatus,
    TERMINAL_STATUSES,
    today_str,
)
from ..publishers import build_publishers
from ..publishers.base import BasePublisher
from ..quality import QualityReport, run_quality_checks
from ..storage import Store
from . import palisade_prompts as prompts

log = get_logger(__name__)

DEFAULT_QUEUE_PATH = Path("config/palisade_guides.json")
DEFAULT_DB_NAME = "palisade.sqlite3"

# Appended to every syndicated post. Wording deliberately avoids the
# banned-phrases gate (e.g. the exact phrase "seamlessly integrate").
CTA_FOOTER = (
    "\n\n---\n\n"
    "*This guide originally appeared on "
    "[agentpalisade.com]({url}). "
    "Agent Palisade helps small and mid-sized businesses put AI to work inside "
    "the tools they already use — practical automation, internal assistants, "
    "and AI security reviews. "
    "[Book a free 30-minute call](https://www.agentpalisade.com/book-call).*\n"
)


@dataclass
class Guide:
    """One syndication candidate from the site's resource library."""

    slug: str
    title: str
    summary: str
    url: str
    read_time: int = 0
    key_points: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def dedup_key(self) -> str:
        """Key stored in repo_history so a guide is never syndicated twice."""
        return f"guide:{self.slug}"

    def to_pseudo_repo(self) -> Repository:
        """Adapter for code paths typed against Repository (quality gate, Post)."""
        return Repository(
            full_name=self.dedup_key,
            name=self.title,
            owner="agentpalisade",
            html_url=self.url,
            description=self.summary,
            homepage=self.url,
            language=None,
            stars=0,
            forks=0,
            watchers=0,
            open_issues=0,
        )


def load_guides(path: str | Path = DEFAULT_QUEUE_PATH) -> list[Guide]:
    """Load the ordered syndication queue (implementation guides lead)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    known = {f.name for f in dataclasses.fields(Guide)}
    return [Guide(**{k: v for k, v in item.items() if k in known}) for item in raw]


def palisade_db_path(settings: Settings) -> Path:
    """Campaign DB next to the main one (PALISADE_DB_PATH overrides)."""
    override = settings.get_env("PALISADE_DB_PATH")
    if override:
        return Path(override)
    return Path(settings.db_path).parent / DEFAULT_DB_NAME


@dataclass
class CampaignSummary:
    run_date: str
    status: str
    mode: str
    guide: str | None = None
    message: str = ""
    publish_results: list[PublishResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_date": self.run_date,
            "status": self.status,
            "mode": self.mode,
            "guide": self.guide,
            "message": self.message,
            "publish_results": [r.to_dict() for r in self.publish_results],
        }


class PalisadeCampaign:
    def __init__(
        self,
        settings: Settings,
        *,
        guides: list[Guide] | None = None,
        store: Store | None = None,
        model: ModelClient | None = None,
        publishers: list[BasePublisher] | None = None,
    ):
        # Syndicated guides are adaptations of human-written site pages, so the
        # repo-grounding rule doesn't apply; everything else in the gate stays.
        self.settings = dataclasses.replace(
            settings,
            quality=dataclasses.replace(settings.quality, require_repo_name_in_body=False),
        )
        self.guides = guides if guides is not None else load_guides()
        self.store = store or Store(palisade_db_path(settings))
        self.model = model or build_model_client(settings)
        self.publishers = (
            publishers if publishers is not None else build_publishers(self.settings)
        )

    # ------------------------------------------------------------------ run
    def run(self, run_date: str | None = None, *, force: bool = False) -> CampaignSummary:
        run_date = run_date or today_str()
        mode = self.settings.publish_mode
        log.info("=== palisade campaign: date=%s mode=%s ===", run_date, mode)

        existing = self.store.get_run(run_date)
        if existing and not force and existing["status"] in {s.value for s in TERMINAL_STATUSES}:
            return CampaignSummary(
                run_date=run_date, status=RunStatus.SKIPPED.value, mode=mode,
                guide=existing.get("repo_full_name"),
                message=f"existing terminal run ({existing['status']}); use --force to re-run",
            )

        self.store.create_run(run_date, mode)
        try:
            return self._run_inner(run_date, mode, force)
        except Exception as exc:  # noqa: BLE001
            log.exception("palisade campaign failed")
            self.store.set_status(run_date, RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            return CampaignSummary(
                run_date=run_date, status=RunStatus.FAILED.value, mode=mode,
                message=f"{type(exc).__name__}: {exc}",
            )

    def _run_inner(self, run_date: str, mode: str, force: bool) -> CampaignSummary:
        guide = self._next_guide(run_date, force)
        if guide is None:
            self.store.set_status(run_date, RunStatus.NO_CANDIDATE)
            return CampaignSummary(
                run_date=run_date, status=RunStatus.NO_CANDIDATE.value, mode=mode,
                message="syndication queue exhausted — every guide has been used",
            )
        log.info("selected guide %s", guide.slug)
        pseudo = guide.to_pseudo_repo()
        self.store.update_run(run_date, status=RunStatus.GENERATING.value,
                              repo_full_name=guide.dedup_key, repo_json=pseudo.to_dict())

        draft = self._write(guide)
        quality = run_quality_checks(draft, pseudo, self.settings)
        approved = quality.passed
        log.info("quality gate: passed=%s blocking=%s", quality.passed, quality.blocking)

        self.store.update_run(
            run_date,
            draft_json=draft.to_dict(),
            final_json={"draft": draft.to_dict(), "quality": quality.to_dict(), "approved": approved},
        )

        post = self._to_post(draft, guide)
        results, final_status = self._publish(run_date, post, mode, approved)

        self.store.set_status(run_date, final_status)
        history_status = {
            RunStatus.PUBLISHED: "published",
            RunStatus.DRY_RUN: "dry_run",
            RunStatus.REJECTED: "rejected",
        }.get(final_status)
        if history_status:
            self.store.mark_repo_used(guide.dedup_key, run_date, history_status)

        return CampaignSummary(
            run_date=run_date, status=final_status.value, mode=mode, guide=guide.slug,
            publish_results=results,
            message=self._summary_message(final_status, quality),
        )

    # --------------------------------------------------------------- stages
    def _next_guide(self, run_date: str, force: bool) -> Guide | None:
        used = self.store.used_repo_names()
        if force:
            # A forced re-run of a date (e.g. dry-run preview, then --force
            # --live) must re-pick the SAME guide, not advance the queue.
            existing = self.store.get_run(run_date)
            if existing and existing.get("repo_full_name"):
                used.discard(existing["repo_full_name"])
        for guide in self.guides:
            if guide.dedup_key not in used:
                return guide
        return None

    def _write(self, guide: Guide) -> Draft:
        raw = self.model.complete(
            system=prompts.writer_system(self.settings),
            prompt=prompts.writer_prompt(guide, self.settings),
            max_tokens=2400,
            temperature=0.5,
            json_mode=True,
        )
        data = extract_json(raw)
        return Draft(
            title=str(data.get("title", "")).strip() or guide.title,
            body_markdown=str(data.get("body_markdown", "")).strip(),
            summary=str(data.get("summary", "")).strip() or guide.summary,
            tags=[str(t).strip() for t in data.get("tags", []) if str(t).strip()][
                : self.settings.content.max_tags
            ] or list(guide.tags),
            angle="syndication",
            model=self.model.model,
            raw=raw,
        )

    @staticmethod
    def _to_post(draft: Draft, guide: Guide) -> Post:
        body = draft.body_markdown.rstrip() + CTA_FOOTER.format(url=guide.url)
        return Post(
            title=draft.title,
            body_markdown=body,
            summary=draft.summary,
            tags=list(draft.tags) or list(guide.tags),
            canonical_url=guide.url,
            repo_url=None,  # not a repo post; no GitHub footer
        )

    def _publish(self, run_date: str, post: Post, mode: str,
                 approved: bool) -> tuple[list[PublishResult], RunStatus]:
        effective_dry_run = (mode != "live") or (not approved)
        results: list[PublishResult] = []
        live_attempted = False
        live_ok = False
        already_done = 0

        for pub in self.publishers:
            if pub.name == "dryrun" or effective_dry_run:
                res = pub.publish(post, dry_run=True)
            elif self.store.already_published(run_date, pub.name):
                res = PublishResult(publisher=pub.name, status="skipped",
                                    dry_run=False, error="already_published")
                already_done += 1
            else:
                res = pub.publish(post, dry_run=False)
                if res.status in ("published", "failed"):
                    live_attempted = True
                live_ok = live_ok or res.ok
            self.store.record_publish_result(run_date, res)
            results.append(res)

        if mode == "live" and not approved:
            return results, RunStatus.REJECTED
        if mode == "live" and (live_attempted or already_done):
            posted = live_ok or already_done > 0
            return results, RunStatus.PUBLISHED if posted else RunStatus.FAILED
        return results, RunStatus.DRY_RUN

    @staticmethod
    def _summary_message(status: RunStatus, quality: QualityReport) -> str:
        if status == RunStatus.REJECTED:
            return "content rejected: " + ("; ".join(quality.blocking) or "quality gate failed")
        if status == RunStatus.DRY_RUN:
            return "dry-run complete (nothing posted externally)"
        if status == RunStatus.PUBLISHED:
            return "syndicated to live platform(s)"
        return status.value
