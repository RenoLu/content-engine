"""The daily pipeline orchestrator.

Ties every stage together and enforces the system's behavioral guarantees:
  * one run per day, idempotent for a given date (``runs.run_date`` is UNIQUE)
  * never reuse a repo that has already been featured
  * skip low-quality / archived / fork / thin-README repos (reasons stored)
  * publish only when the reviewer AND the deterministic quality gate pass
  * default to dry-run; contain per-publisher failures; prevent double-posting

Components are dependency-injected so the whole pipeline is unit-testable with
fakes (see tests/test_pipeline.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents import (
    EngagementReviewer,
    ReviewerAgent,
    ReviserAgent,
    WriterAgent,
    build_model_client,
)
from .config import Settings
from .logging_setup import get_logger
from .models import (
    Draft,
    EngagementReview,
    Post,
    PublishResult,
    Repository,
    ReviewResult,
    RunStatus,
    TERMINAL_STATUSES,
    today_str,
    to_json,
)
from .publishers import build_publishers
from .publishers.base import BasePublisher
from .quality import QualityReport, run_quality_checks
from .ranking import RepoRanker, readme_filter_reason
from .research import RepoResearcher
from .sources import build_source
from .sources.base import Source
from .storage import Store

log = get_logger(__name__)

# Cap how many top candidates we enrich (README fetch) before giving up.
MAX_SELECTION_ATTEMPTS = 12


@dataclass
class PipelineSummary:
    run_date: str
    status: str
    mode: str
    repo: str | None = None
    score: float | None = None
    review_score: float | None = None
    attention_score: float | None = None
    voice_score: float | None = None
    approved: bool = False
    skip_reason: str | None = None
    message: str = ""
    publish_results: list[PublishResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_date": self.run_date,
            "status": self.status,
            "mode": self.mode,
            "repo": self.repo,
            "score": self.score,
            "review_score": self.review_score,
            "attention_score": self.attention_score,
            "voice_score": self.voice_score,
            "approved": self.approved,
            "skip_reason": self.skip_reason,
            "message": self.message,
            "publish_results": [r.to_dict() for r in self.publish_results],
        }


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        store: Store | None = None,
        source: Source | None = None,
        researcher: RepoResearcher | None = None,
        model=None,
        writer: WriterAgent | None = None,
        reviewer: ReviewerAgent | None = None,
        engagement_reviewer: EngagementReviewer | None = None,
        reviser: ReviserAgent | None = None,
        publishers: list[BasePublisher] | None = None,
    ):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.source = source or build_source(settings)
        self.researcher = researcher or RepoResearcher(settings)
        self.model = model or build_model_client(settings)
        self.writer = writer or WriterAgent(self.model, settings)
        self.reviewer = reviewer or ReviewerAgent(self.model, settings)
        self.engagement_reviewer = engagement_reviewer or EngagementReviewer(self.model, settings)
        self.reviser = reviser or ReviserAgent(self.model, settings)
        self.publishers = publishers if publishers is not None else build_publishers(settings)

    # ------------------------------------------------------------------ run
    def run(self, run_date: str | None = None, *, force: bool = False) -> PipelineSummary:
        run_date = run_date or today_str()
        mode = self.settings.publish_mode
        log.info("=== pipeline start: date=%s mode=%s force=%s ===", run_date, mode, force)

        existing = self.store.get_run(run_date)
        if existing and not force and existing["status"] in {s.value for s in TERMINAL_STATUSES}:
            log.info("run for %s already terminal (%s) — skipping (use --force).",
                     run_date, existing["status"])
            return PipelineSummary(
                run_date=run_date, status=RunStatus.SKIPPED.value, mode=mode,
                repo=existing.get("repo_full_name"),
                message=f"existing terminal run ({existing['status']}); use --force to re-run",
            )

        self.store.create_run(run_date, mode)
        try:
            return self._run_inner(run_date, mode, force)
        except Exception as exc:  # noqa: BLE001
            log.exception("pipeline failed")
            self.store.set_status(run_date, RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            return PipelineSummary(
                run_date=run_date, status=RunStatus.FAILED.value, mode=mode,
                message=f"{type(exc).__name__}: {exc}",
            )

    # --------------------------------------------------------------- stages
    def _run_inner(self, run_date: str, mode: str, force: bool) -> PipelineSummary:
        # 1. collect
        candidates = self.source.fetch_candidates()
        log.info("collected %d candidate repos", len(candidates))
        if not candidates:
            self.store.set_status(run_date, RunStatus.NO_CANDIDATE)
            return PipelineSummary(run_date=run_date, status=RunStatus.NO_CANDIDATE.value,
                                   mode=mode, message="source returned no candidates")

        # 2. filter + score
        used = self.store.used_repo_names()
        if force:
            existing = self.store.get_run(run_date)
            if existing and existing.get("repo_full_name"):
                used.discard(existing["repo_full_name"])  # let --force re-pick same repo
        ranker = RepoRanker(self.settings)
        ranked = ranker.prefilter_and_score(candidates, used)

        # 3. select: enrich top candidates until one passes the README check
        selected = self._select(ranked, ranker)
        self.store.record_candidates(run_date, ranked,
                                     selected_full_name=selected.full_name if selected else None)
        if selected is None:
            self.store.set_status(run_date, RunStatus.NO_CANDIDATE)
            return PipelineSummary(run_date=run_date, status=RunStatus.NO_CANDIDATE.value,
                                   mode=mode, message="no candidate passed all filters")

        log.info("selected %s (score=%.3f)", selected.full_name, selected.score)
        self.store.update_run(run_date, status=RunStatus.GENERATING.value,
                              repo_full_name=selected.full_name, repo_json=selected.to_dict())

        # 4. write
        draft = self.writer.write(selected)

        # 5. review + engagement review + deterministic quality gate (+ revisions)
        self.store.set_status(run_date, RunStatus.REVIEWING)
        draft, review, eng, quality = self._review_and_revise(run_date, selected, draft)

        # 6. final gate: publish only if the fact reviewer AND the engagement
        #    reviewer approve AND the deterministic quality gate passes.
        eng_ok = self._engagement_ok(eng)
        approved = self.reviewer.is_approved(review) and eng_ok and quality.passed
        log.info("gate: reviewer_ok=%s engagement_ok=%s quality_ok=%s -> approved=%s",
                 self.reviewer.is_approved(review), eng_ok, quality.passed, approved)

        self.store.update_run(
            run_date,
            draft_json=draft.to_dict(),
            review_json=review.to_dict(),
            final_json={
                "draft": draft.to_dict(),
                "review": review.to_dict(),
                "engagement": eng.to_dict() if eng else None,
                "quality": quality.to_dict(),
                "approved": approved,
            },
        )

        # 7. publish (or simulate)
        post = Post.from_draft(draft, repo=selected)
        post.image = self._build_image(draft, selected)
        results, final_status = self._publish(run_date, post, mode, approved)

        # 8. persist terminal status + dedup history.
        # Only a genuine live publish "consumes" a repo (no-repeat guard). A
        # dry-run, rejection, or failure leaves the repo eligible for a future
        # run — mirrors the publisher guard where a dry-run must not block a
        # later live post. See store.used_repo_names / hard_filter_reason.
        self.store.set_status(run_date, final_status)
        if final_status == RunStatus.PUBLISHED:
            self.store.mark_repo_used(selected.full_name, run_date, "published")

        return PipelineSummary(
            run_date=run_date, status=final_status.value, mode=mode,
            repo=selected.full_name, score=selected.score,
            review_score=review.overall_score,
            attention_score=eng.attention_score if eng else None,
            voice_score=eng.voice_score if eng else None,
            approved=approved,
            publish_results=results,
            message=self._summary_message(final_status, quality, review, eng),
        )

    def _select(self, ranked: list[Repository], ranker: RepoRanker) -> Repository | None:
        # We keep each repo's ranking-time score untouched so the persisted
        # candidates audit table is internally consistent (every row is the
        # pre-enrichment ranking score). README quality isn't known at ranking
        # time anyway — it is fetched lazily here — so it never differentiated
        # selection; recomputing only the winner's score would make an
        # unselected row appear to outscore the selected one. See store.candidates.
        eligible = ranker.eligible(ranked)
        for repo in eligible[:MAX_SELECTION_ATTEMPTS]:
            self.researcher.enrich(repo)
            reason = readme_filter_reason(repo, self.settings)
            if reason:
                repo.skip_reason = reason
                log.info("skip %s: %s", repo.full_name, reason)
                continue
            return repo
        return None

    def _review_and_revise(
        self, run_date: str, repo: Repository, draft: Draft
    ) -> tuple[Draft, ReviewResult, EngagementReview | None, QualityReport]:
        """Run all three gates, rewriting while any of them fails.

        The loop is driven by the fact reviewer, the engagement/voice reviewer
        (when enabled), AND the deterministic quality gate, so a dull, robotic, or
        rule-tripping draft is rewritten with that feedback and re-checked — up to
        ``review.max_revisions`` rounds. A draft that never passes is left
        un-approved (the caller refuses to publish it) rather than shipped sub-par.
        A reviewer's terminal ``reject`` short-circuits the loop.
        """
        eng_enabled = self.settings.engagement.enabled
        review = self.reviewer.review(repo, draft)
        eng = self.engagement_reviewer.review(repo, draft) if eng_enabled else None
        quality = run_quality_checks(draft, repo, self.settings)
        rounds = 0
        while rounds < self.settings.review.max_revisions:
            if self.reviewer.is_approved(review) and self._engagement_ok(eng) and quality.passed:
                break
            if review.recommended_action == "reject" or (
                eng is not None and eng.recommended_action == "reject"
            ):
                break
            rounds += 1
            log.info("revision round %d (review=%.1f, attention=%s, voice=%s, quality_ok=%s)",
                     rounds, review.overall_score,
                     f"{eng.attention_score:.1f}" if eng else "n/a",
                     f"{eng.voice_score:.1f}" if eng else "n/a", quality.passed)
            self.store.set_status(run_date, RunStatus.REVISING)
            draft = self.reviser.revise(
                repo, draft, review, quality_issues=self._reviser_issues(quality, eng)
            )
            review = self.reviewer.review(repo, draft)
            eng = self.engagement_reviewer.review(repo, draft) if eng_enabled else None
            quality = run_quality_checks(draft, repo, self.settings)
        return draft, review, eng, quality

    def _engagement_ok(self, eng: EngagementReview | None) -> bool:
        """True when engagement review is disabled (no-op) or the reviewer approves."""
        if not self.settings.engagement.enabled or eng is None:
            return True
        return self.engagement_reviewer.is_approved(eng)

    def _reviser_issues(self, quality: QualityReport,
                        eng: EngagementReview | None) -> list[str]:
        """Combine deterministic quality failures with engagement feedback into the
        single must-fix list handed to the reviser."""
        issues = list(quality.blocking)
        if eng is not None and not self._engagement_ok(eng):
            e = self.settings.engagement
            if eng.attention_score < e.min_attention_score:
                issues.append(
                    f"engagement/attention: attention_score {eng.attention_score:.1f} < "
                    f"{e.min_attention_score} — open with a sharper thesis and a more "
                    "distinctive angle.")
            if eng.voice_score < e.min_voice_score:
                issues.append(
                    f"engagement/voice: voice_score {eng.voice_score:.1f} < "
                    f"{e.min_voice_score} — use active voice, vary the rhythm, and cut "
                    "filler/AI-cadence.")
            issues += [
                f"engagement/{i.type} [{i.severity}]: {i.problem}"
                + (f" (fix: {i.suggested_fix})" if i.suggested_fix else "")
                for i in eng.issues
            ]
        return issues

    def _build_image(self, draft, repo) -> "object | None":
        """Article-specific post image (Pollinations, free). Uses the authored
        image_prompt when present, else derives a prompt from the piece's own
        title/angle so the image illustrates THIS article, not a generic visual.
        Disabled with POST_IMAGE=false. No network here — bytes fetch lazily."""
        if self.settings.get_env("POST_IMAGE", "true").lower() != "true":
            return None
        from . import imagegen
        prompt = (draft.image_prompt or "").strip()
        if not prompt:
            angle = (getattr(draft, "angle", "") or "").strip()
            topics = ", ".join((repo.topics or [])[:5]) if repo else ""
            prompt = (f"An illustration for an article titled \"{draft.title}\". "
                      f"{angle + '. ' if angle else ''}{draft.summary} "
                      f"Visual concepts: {topics}." if topics else
                      f"An illustration for an article titled \"{draft.title}\". "
                      f"{angle + '. ' if angle else ''}{draft.summary}")
        w = int(self.settings.get_env("POST_IMAGE_WIDTH", "1280") or 1280)
        h = int(self.settings.get_env("POST_IMAGE_HEIGHT", "720") or 720)
        alt = f"Illustration for: {draft.title}"
        return imagegen.generate(prompt, alt=alt, width=w, height=h)

    def _publish(self, run_date: str, post: Post, mode: str,
                 approved: bool) -> tuple[list[PublishResult], RunStatus]:
        # Everything is dry-run unless we're in live mode AND the gate passed.
        effective_dry_run = (mode != "live") or (not approved)
        results: list[PublishResult] = []
        live_attempted = False     # a real post call was made this run
        live_ok = False            # >=1 real post call succeeded this run
        already_done = 0           # publishers already posted on a prior run

        for pub in self.publishers:
            if pub.name == "dryrun":
                res = pub.publish(post, dry_run=True)  # always writes a local artifact
            elif effective_dry_run:
                res = pub.publish(post, dry_run=True)
            else:
                if self.store.already_published(run_date, pub.name):
                    log.info("[%s] already published for %s — skipping (idempotent).",
                             pub.name, run_date)
                    res = PublishResult(publisher=pub.name, status="skipped",
                                        dry_run=False, error="already_published")
                    already_done += 1
                else:
                    res = pub.publish(post, dry_run=False)
                    # Only a real post attempt counts; an unconfigured publisher
                    # returns "skipped" and should not turn the run into FAILED.
                    if res.status in ("published", "failed"):
                        live_attempted = True
                    live_ok = live_ok or res.ok
            self.store.record_publish_result(run_date, res)
            results.append(res)

        if mode == "live" and not approved:
            return results, RunStatus.REJECTED
        # A publisher that was already posted on a prior run counts as success,
        # so a forced live re-run of a fully-published date stays PUBLISHED rather
        # than degrading to DRY_RUN.
        if mode == "live" and (live_attempted or already_done):
            posted = live_ok or already_done > 0
            return results, RunStatus.PUBLISHED if posted else RunStatus.FAILED
        return results, RunStatus.DRY_RUN

    def _summary_message(self, status: RunStatus, quality: QualityReport,
                         review: ReviewResult, eng: EngagementReview | None = None) -> str:
        if status == RunStatus.REJECTED:
            reasons = list(quality.blocking)
            if review.high_severity_issues:
                reasons.append(f"{len(review.high_severity_issues)} high-severity review issue(s)")
            if eng is not None and not self._engagement_ok(eng):
                if eng.high_severity_issues:
                    reasons.append(
                        f"{len(eng.high_severity_issues)} high-severity engagement issue(s)")
                reasons.append(
                    f"low engagement (attention={eng.attention_score:.1f}, "
                    f"voice={eng.voice_score:.1f})")
            return "content rejected: " + ("; ".join(reasons) or "did not meet review bar")
        if status == RunStatus.DRY_RUN:
            return "dry-run complete (nothing posted externally)"
        if status == RunStatus.PUBLISHED:
            return "published to live platform(s)"
        if status == RunStatus.FAILED:
            return "all live publishers failed"
        return status.value
