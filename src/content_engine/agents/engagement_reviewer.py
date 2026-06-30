"""Engagement/voice reviewer: scores whether a Draft catches attention and sounds
human, and returns structured JSON. Runs as a second pass after the fact-checking
``ReviewerAgent``.

Like the fact reviewer, the model returns scores + issues and ``is_approved``
applies the config *policy* (per-axis thresholds, block-on-high-severity) on top,
so the bar lives in config (``settings.engagement``), not the model.
"""

from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Draft, EngagementReview, Repository, ReviewIssue
from . import prompts
from .model_client import ModelClient
from .parsing import extract_json

log = get_logger(__name__)


class EngagementReviewer:
    def __init__(self, model: ModelClient, settings: Settings):
        self.model = model
        self.settings = settings

    def review(self, repo: Repository, draft: Draft) -> EngagementReview:
        system = prompts.engagement_reviewer_system(self.settings)
        prompt = prompts.engagement_reviewer_prompt(repo, draft, self.settings)
        raw = self.model.complete(
            system=system, prompt=prompt, max_tokens=1400, temperature=0.2, json_mode=True
        )
        data = extract_json(raw)
        result = parse_engagement(data)
        result.model = self.model.model
        log.info("engagement: attention=%.1f voice=%.1f severity=%s action=%s issues=%d",
                 result.attention_score, result.voice_score, result.severity,
                 result.recommended_action, len(result.issues))
        return result

    def is_approved(self, review: EngagementReview) -> bool:
        """Apply config policy on top of the model's own ``approved`` flag."""
        e = self.settings.engagement
        if review.attention_score < e.min_attention_score:
            return False
        if review.voice_score < e.min_voice_score:
            return False
        if e.block_on_high_severity and review.high_severity_issues:
            return False
        if review.recommended_action == "reject":
            return False
        return bool(review.approved)


def parse_engagement(data: dict) -> EngagementReview:
    """Tolerant parser for engagement JSON (handles missing/loosely-typed fields)."""
    issues: list[ReviewIssue] = []
    for raw_issue in data.get("issues", []) or []:
        if not isinstance(raw_issue, dict):
            continue
        issues.append(
            ReviewIssue(
                type=str(raw_issue.get("type", "general")),
                severity=str(raw_issue.get("severity", "medium")).lower(),
                text=str(raw_issue.get("text", "")),
                problem=str(raw_issue.get("problem", "")),
                suggested_fix=str(raw_issue.get("suggested_fix", "")),
            )
        )
    return EngagementReview(
        approved=bool(data.get("approved", False)),
        attention_score=_clamp(data.get("attention_score", 0.0)),
        voice_score=_clamp(data.get("voice_score", 0.0)),
        severity=str(data.get("severity", "medium")).lower(),
        issues=issues,
        recommended_action=str(data.get("recommended_action", "revise")).lower(),
        notes=str(data.get("notes", "")),
    )


def _clamp(v: object) -> float:
    """Clamp a model score to the documented 0-10 scale (defends the gate)."""
    try:
        return max(0.0, min(10.0, float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
