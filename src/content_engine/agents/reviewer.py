"""Reviewer agent: critiques a Draft against the repo facts, returns structured JSON.

The reviewer's verdict is combined with config thresholds in ``is_approved`` so
the *policy* (min score, block-on-high-severity) lives in config, not the model.
"""

from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Draft, Repository, ReviewIssue, ReviewResult
from . import prompts
from .model_client import ModelClient
from .parsing import extract_json

log = get_logger(__name__)


class ReviewerAgent:
    def __init__(self, model: ModelClient, settings: Settings):
        self.model = model
        self.settings = settings

    def review(self, repo: Repository, draft: Draft) -> ReviewResult:
        system = prompts.reviewer_system(self.settings)
        prompt = prompts.reviewer_prompt(repo, draft, self.settings)
        raw = self.model.complete(
            system=system, prompt=prompt, max_tokens=1600, temperature=0.1, json_mode=True
        )
        data = extract_json(raw)
        result = parse_review(data)
        result.model = self.model.model
        log.info("review: score=%.1f severity=%s action=%s issues=%d",
                 result.overall_score, result.severity,
                 result.recommended_action, len(result.issues))
        return result

    def is_approved(self, review: ReviewResult) -> bool:
        """Apply config policy on top of the model's own ``approved`` flag."""
        r = self.settings.review
        if review.overall_score < r.min_overall_score:
            return False
        if r.block_on_high_severity and review.high_severity_issues:
            return False
        if review.recommended_action == "reject":
            return False
        return bool(review.approved)


def parse_review(data: dict) -> ReviewResult:
    """Tolerant parser for reviewer JSON (handles missing/loosely-typed fields)."""
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
    # Clamp to the documented 0-10 scale so a malformed/pathological model score
    # (e.g. 15) can't sail past the min_overall_score gate.
    score = max(0.0, min(10.0, _to_float(data.get("overall_score", 0.0))))
    return ReviewResult(
        approved=bool(data.get("approved", False)),
        overall_score=score,
        severity=str(data.get("severity", "medium")).lower(),
        issues=issues,
        recommended_action=str(data.get("recommended_action", "revise")).lower(),
        notes=str(data.get("notes", "")),
    )


def _to_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
