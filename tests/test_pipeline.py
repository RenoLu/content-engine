import dataclasses

from content_engine.models import Draft, Post, PublishResult, ReviewResult
from content_engine.pipeline import Pipeline
from content_engine.publishers.base import BasePublisher
from content_engine.sources.base import Source
from content_engine.storage import Store

from .conftest import make_repo


class FakeSource(Source):
    name = "fake"

    def __init__(self, repos):
        self.repos = repos

    def fetch_candidates(self):
        return [dataclasses.replace(r) for r in self.repos]


class NoopResearcher:
    """Skips network; repos in tests already carry README content."""

    def enrich(self, repo):
        return repo


class RecordingPublisher(BasePublisher):
    name = "recording"

    def __init__(self, settings):
        super().__init__(settings)
        self.live_calls: list[Post] = []

    def is_configured(self) -> bool:
        return True

    def render_payload(self, post: Post) -> dict:
        return {"title": post.title}

    def _publish_live(self, post: Post) -> PublishResult:
        self.live_calls.append(post)
        return PublishResult(publisher=self.name, status="published",
                             url="https://example/post", dry_run=False)


def _pipeline(settings, repos=None, publishers=None):
    repos = repos or [make_repo()]
    return Pipeline(
        settings,
        source=FakeSource(repos),
        researcher=NoopResearcher(),
        publishers=publishers if publishers is not None else None,
    )


def test_dry_run_happy_path(settings):
    pipe = _pipeline(settings)
    summary = pipe.run("2026-05-31")
    assert summary.status == "dry_run"
    assert summary.repo == "acme/widget"
    assert summary.approved is True
    # a local artifact should have been written by the dryrun publisher
    assert list(settings.output_dir.glob("*.md"))


def test_idempotent_skip_on_second_run(settings):
    pipe = _pipeline(settings)
    pipe.run("2026-05-31")
    second = _pipeline(settings).run("2026-05-31")
    assert second.status == "skipped"


def test_force_reruns_same_date_and_repo(settings):
    _pipeline(settings).run("2026-05-31")
    forced = _pipeline(settings).run("2026-05-31", force=True)
    assert forced.status == "dry_run"
    assert forced.repo == "acme/widget"


def test_no_candidate_when_all_filtered(settings):
    archived = make_repo(full_name="x/archived", is_archived=True)
    summary = _pipeline(settings, repos=[archived]).run("2026-05-31")
    assert summary.status == "no_candidate"


def test_repo_not_reused_across_dates(settings):
    # day 1 features acme/widget; day 2 should pick the other repo
    r1 = make_repo(full_name="acme/widget", stars=5000)
    r2 = make_repo(full_name="acme/gadget", name="gadget", stars=4000)
    _pipeline(settings, repos=[r1, r2]).run("2026-05-31")
    day2 = _pipeline(settings, repos=[r1, r2]).run("2026-06-01")
    assert day2.repo == "acme/gadget"


def test_live_publishes_when_approved(settings):
    s = dataclasses.replace(settings, publish_mode="live")
    rec = RecordingPublisher(s)
    summary = _pipeline(s, publishers=[rec]).run("2026-05-31")
    assert summary.status == "published"
    assert len(rec.live_calls) == 1


def test_live_rejects_and_does_not_post_when_gate_fails(settings):
    # raise the review bar above what the mock reviewer returns (8.4)
    s = dataclasses.replace(
        settings,
        publish_mode="live",
        review=dataclasses.replace(settings.review, min_overall_score=9.9, max_revisions=0),
    )
    rec = RecordingPublisher(s)
    summary = _pipeline(s, publishers=[rec]).run("2026-05-31")
    assert summary.status == "rejected"
    assert summary.approved is False
    assert rec.live_calls == []  # nothing posted live


def test_no_double_post_on_forced_live_rerun(settings):
    s = dataclasses.replace(settings, publish_mode="live")
    rec = RecordingPublisher(s)
    first = _pipeline(s, publishers=[rec]).run("2026-05-31")
    second = _pipeline(s, publishers=[rec]).run("2026-05-31", force=True)
    assert len(rec.live_calls) == 1          # idempotent guard prevents a second post
    assert first.status == "published"
    # A forced re-run of a fully-published date stays PUBLISHED (not degraded to dry_run).
    assert second.status == "published"


def test_dry_run_then_live_actually_posts(settings):
    # Running a date in dry-run first must NOT block a later live run for that date.
    dry = _pipeline(settings).run("2026-05-31")
    assert dry.status == "dry_run"

    s = dataclasses.replace(settings, publish_mode="live")
    rec = RecordingPublisher(s)
    live = _pipeline(s, publishers=[rec]).run("2026-05-31", force=True)
    assert live.status == "published"
    assert len(rec.live_calls) == 1          # the prior dry-run did not block it


def test_run_persists_draft_and_review(settings):
    _pipeline(settings).run("2026-05-31")
    store = Store(settings.db_path)
    run = store.get_run("2026-05-31")
    assert run["draft_json"] and run["review_json"] and run["final_json"]


# --- revision loop is driven by BOTH reviewer and the deterministic quality gate ---

_LONG = "x " * 220


class _BannedPhraseWriter:
    """Produces a structurally-valid draft that nonetheless trips the quality gate."""

    def write(self, repo):
        return Draft(
            title="A look at widget",
            body_markdown=f"## What widget does\n\nwidget is a game changer {_LONG}\n\n"
                          f"## Takeaway\n\n{_LONG}",
            summary="A grounded take on widget.",
            tags=["t"],
        )


class _ApprovingReviewer:
    """Always approves — so only the quality gate can force a revision."""

    def review(self, repo, draft):
        return ReviewResult(approved=True, overall_score=9.0, severity="low",
                            issues=[], recommended_action="approve")

    def is_approved(self, review):
        return True


class _RecordingReviser:
    def __init__(self):
        self.calls = []

    def revise(self, repo, draft, review, quality_issues=None):
        self.calls.append(quality_issues)
        return Draft(
            title="A look at widget",
            body_markdown=f"## What widget does\n\nwidget is a solid tool {_LONG}\n\n"
                          f"## Takeaway\n\n{_LONG}",
            summary="A grounded take on widget.",
            tags=["t"],
        )


def test_quality_gate_forces_revision_even_when_reviewer_approves(settings):
    reviser = _RecordingReviser()
    pipe = Pipeline(
        settings,
        source=FakeSource([make_repo()]),
        researcher=NoopResearcher(),
        writer=_BannedPhraseWriter(),
        reviewer=_ApprovingReviewer(),
        reviser=reviser,
        publishers=None,
    )
    summary = pipe.run("2026-05-31")
    # The banned phrase failed the quality gate, so a revision was attempted...
    assert len(reviser.calls) == 1
    # ...and the reviser was told about the deterministic failure.
    assert reviser.calls[0] and any("banned_phrases" in q for q in reviser.calls[0])
    # The revised draft is clean, so the run is approved and completes dry-run.
    assert summary.approved is True
    assert summary.status == "dry_run"
