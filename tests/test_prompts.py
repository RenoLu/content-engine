from content_engine.agents import prompts
from content_engine.models import Draft, ReviewResult

from .conftest import make_repo


def test_repo_facts_contains_grounding_markers():
    facts = prompts.build_repo_facts(make_repo())
    assert "REPO: acme/widget" in facts
    assert "STARS: 1200" in facts
    assert "README (verbatim excerpt" in facts


def test_writer_prompt_markers(settings):
    p = prompts.writer_prompt(make_repo(), settings)
    assert p.startswith("TASK: WRITER")
    assert "REPO: acme/widget" in p
    # banned phrases must be injected so the model is told what to avoid
    assert "game changer" in p
    assert '"body_markdown"' in p


def test_reviewer_prompt_includes_draft(settings):
    draft = Draft(title="T", body_markdown="BODYTEXT", summary="S")
    p = prompts.reviewer_prompt(make_repo(), draft, settings)
    assert p.startswith("TASK: REVIEWER")
    assert "BODYTEXT" in p
    assert "approved" in p


def test_reviser_prompt_lists_issues(settings):
    draft = Draft(title="T", body_markdown="B", summary="S")
    review = ReviewResult(
        approved=False, overall_score=6.0, severity="high",
        issues=[], recommended_action="revise",
    )
    p = prompts.reviser_prompt(make_repo(), draft, review, settings)
    assert p.startswith("TASK: REVISER")
    assert "REPO: acme/widget" in p
