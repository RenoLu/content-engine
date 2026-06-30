"""Engagement/voice reviewer: approval policy, tolerant parsing, and prompts."""

import dataclasses

from content_engine.agents import prompts
from content_engine.agents.engagement_reviewer import EngagementReviewer, parse_engagement
from content_engine.models import Draft, EngagementReview, ReviewIssue

from .conftest import make_repo


def _review(**kw) -> EngagementReview:
    base = dict(approved=True, attention_score=8.0, voice_score=8.0, severity="low",
                issues=[], recommended_action="approve")
    base.update(kw)
    return EngagementReview(**base)


def _agent(settings) -> EngagementReviewer:
    # is_approved() never touches the model, so None is fine here.
    return EngagementReviewer(model=None, settings=settings)


# --------------------------------------------------------------- is_approved
def test_is_approved_passes_when_above_bars(settings):
    assert _agent(settings).is_approved(_review()) is True


def test_is_approved_rejects_low_attention(settings):
    assert _agent(settings).is_approved(_review(attention_score=5.0)) is False


def test_is_approved_rejects_low_voice(settings):
    assert _agent(settings).is_approved(_review(voice_score=5.0)) is False


def test_is_approved_blocks_on_high_severity(settings):
    hi = ReviewIssue(type="hook", severity="high", text="lead",
                     problem="throat-clear opening", suggested_fix="lead with the thesis")
    assert _agent(settings).is_approved(_review(issues=[hi])) is False


def test_is_approved_rejects_reject_action(settings):
    assert _agent(settings).is_approved(_review(recommended_action="reject")) is False


def test_is_approved_respects_model_approved_flag(settings):
    assert _agent(settings).is_approved(_review(approved=False)) is False


def test_high_severity_block_can_be_disabled(settings):
    s = dataclasses.replace(
        settings,
        engagement=dataclasses.replace(settings.engagement, block_on_high_severity=False),
    )
    hi = ReviewIssue(type="hook", severity="high", text="lead",
                     problem="x", suggested_fix="y")
    assert _agent(s).is_approved(_review(issues=[hi])) is True


# ----------------------------------------------------------- parse_engagement
def test_parse_engagement_tolerant_and_clamped():
    data = {
        "approved": True,
        "attention_score": 15,            # out of range -> clamped to 10
        "voice_score": "7.5",             # string -> coerced
        "severity": "LOW",
        "issues": [{"type": "voice", "severity": "Medium", "text": "x",
                    "problem": "filler", "suggested_fix": "cut it"}],
        "recommended_action": "Approve",
    }
    r = parse_engagement(data)
    assert r.attention_score == 10.0
    assert r.voice_score == 7.5
    assert r.severity == "low"
    assert r.recommended_action == "approve"
    assert r.issues[0].severity == "medium"
    assert r.overall_score == 7.5         # weakest-link: min(10.0, 7.5)


def test_parse_engagement_defaults_on_missing():
    r = parse_engagement({})
    assert r.approved is False
    assert r.attention_score == 0.0 and r.voice_score == 0.0
    assert r.recommended_action == "revise"
    assert r.issues == []


# ------------------------------------------------------------------- prompts
def test_writer_prompt_includes_voice_rubric(settings):
    p = prompts.writer_prompt(make_repo(), settings)
    assert "VOICE & ENGAGEMENT RUBRIC" in p
    assert "thesis" in p.lower()


def test_engagement_prompt_has_marker_and_schema(settings):
    draft = Draft(title="t", body_markdown="## h\n\nbody", summary="s", tags=["a"])
    p = prompts.engagement_reviewer_prompt(make_repo(), draft, settings)
    assert p.startswith("TASK: ENGAGEMENT REVIEW")
    assert "attention_score" in p and "voice_score" in p
    # the configured thresholds are surfaced to the model
    assert str(settings.engagement.min_attention_score) in p
