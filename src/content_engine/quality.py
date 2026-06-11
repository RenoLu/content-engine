"""Deterministic final-quality gate.

Runs *after* the AI reviewer as a cheap, non-negotiable backstop for objective
problems the model might wave through: banned marketing phrases, missing repo
grounding, structural issues, leftover placeholders. These checks are rules, not
opinions, so they live in code rather than a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Settings
from .models import Draft, Repository

_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_PLACEHOLDERS = ("lorem ipsum", "[insert", "todo:", "tk tk", "xxxxx", "owner/repo")


@dataclass
class QualityReport:
    passed: bool = True
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "blocking": self.blocking,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def run_quality_checks(draft: Draft, repo: Repository, settings: Settings) -> QualityReport:
    q = settings.quality
    c = settings.content
    body = draft.body_markdown or ""
    body_lower = body.lower()
    summary_lower = (draft.summary or "").lower()

    report = QualityReport()
    report.stats = {
        "word_count": _word_count(body),
        "heading_count": len(_HEADING_RE.findall(body)),
        "summary_chars": len(draft.summary or ""),
        "tag_count": len(draft.tags),
    }

    # --- blocking checks ---
    if not draft.title.strip():
        report.blocking.append("missing_title")
    if not body.strip():
        report.blocking.append("empty_body")
    if not (draft.summary or "").strip():
        report.blocking.append("missing_summary")

    hits = [p for p in q.banned_phrases if p in body_lower or p in summary_lower]
    if len(hits) > q.max_banned_phrase_hits:
        report.blocking.append(f"banned_phrases:{','.join(sorted(set(hits)))}")
    report.stats["banned_phrase_hits"] = len(hits)

    if report.stats["heading_count"] < q.min_headings:
        report.blocking.append(
            f"too_few_headings({report.stats['heading_count']}<{q.min_headings})"
        )

    if q.require_repo_name_in_body:
        if repo.name.lower() not in body_lower and repo.full_name.lower() not in body_lower:
            report.blocking.append("repo_name_not_in_body")

    placeholders = [p for p in _PLACEHOLDERS if p in body_lower]
    if placeholders:
        report.blocking.append(f"placeholder_text:{','.join(placeholders)}")

    # A body that's drastically too short is almost certainly broken output.
    floor = max(150, int(c.target_words_min * 0.5))
    if report.stats["word_count"] < floor:
        report.blocking.append(f"body_too_short(<{floor}_words)")

    # --- non-blocking warnings ---
    if report.stats["word_count"] > c.target_words_max:
        report.warnings.append(
            f"body_long({report.stats['word_count']}>{c.target_words_max}_words)"
        )
    if report.stats["summary_chars"] > c.summary_max_chars:
        report.warnings.append(
            f"summary_long({report.stats['summary_chars']}>{c.summary_max_chars}_chars)"
        )
    if report.stats["tag_count"] == 0:
        report.warnings.append("no_tags")

    report.passed = not report.blocking
    return report
