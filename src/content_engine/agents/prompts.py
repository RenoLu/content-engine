"""Prompt construction for the writer, reviewer, and reviser agents.

All prompts:
  * embed a machine-readable ``REPO:`` line (used for grounding + the mock client)
  * begin with a ``TASK:`` marker (helps steer models and lets the mock route)
  * present only source-grounded facts so the reviewer can check for hallucination
"""

from __future__ import annotations

from ..config import Settings
from ..models import Draft, Repository, ReviewResult

AUDIENCE = (
    "senior software engineers, fintech/quant developers, AI engineers, and "
    "cloud/data-platform and developer-tooling engineers"
)


def build_repo_facts(repo: Repository) -> str:
    """A compact, source-grounded fact sheet handed to every agent."""
    readme = repo.readme_markdown or "(no README available)"
    topics = ", ".join(repo.topics) if repo.topics else "(none)"
    links = "\n".join(f"  - {u}" for u in repo.extracted_links[:10]) or "  (none)"
    return (
        f"REPO: {repo.full_name}\n"
        f"REPO_DESC: {repo.description or '(none)'}\n"
        f"URL: {repo.html_url}\n"
        f"HOMEPAGE: {repo.homepage or '(none)'}\n"
        f"PRIMARY_LANGUAGE: {repo.language or '(unknown)'}\n"
        f"STARS: {repo.stars}    FORKS: {repo.forks}    "
        f"OPEN_ISSUES: {repo.open_issues}\n"
        f"TOPICS: {topics}\n"
        f"LICENSE: {repo.license or '(unknown)'}\n"
        f"CREATED_AT: {repo.created_at}    LAST_PUSH: {repo.pushed_at}\n"
        f"README_LENGTH_CHARS: {repo.readme_len}\n"
        f"LINKS_FROM_README:\n{links}\n"
        f"--- README (verbatim excerpt, your ONLY source of truth) ---\n"
        f"{readme}\n"
        f"--- end README ---"
    )


def _banned_list(settings: Settings) -> str:
    return ", ".join(f'"{p}"' for p in settings.quality.banned_phrases)


# --------------------------------------------------------------------- writer
def writer_system(settings: Settings) -> str:
    return (
        "You are a staff-level software engineer who writes sharp, practical "
        f"technical commentary for an audience of {AUDIENCE}. "
        "You write like an experienced engineer evaluating a tool for real use — "
        "specific, grounded, and skeptical where warranted. You never invent "
        "facts, benchmarks, or features that are not present in the source "
        "material. When the source is thin, you say so plainly rather than padding."
    )


def writer_prompt(repo: Repository, settings: Settings) -> str:
    c = settings.content
    return (
        "TASK: WRITER\n\n"
        "Write a technical post about the GitHub repository described below. "
        "Use ONLY the facts in the fact sheet; do not invent capabilities, "
        "benchmarks, adoption numbers, or comparisons that are not supported.\n\n"
        f"{build_repo_facts(repo)}\n\n"
        "Requirements:\n"
        f"- Audience: {AUDIENCE}.\n"
        "- Angle: practical engineering analysis. Cover: what it does, why "
        "engineers are paying attention, how it might be used in production, "
        "an architecture/implementation insight if the source supports one, "
        "limitations/risks/tradeoffs, and ONE clear opinionated takeaway.\n"
        f"- Body length: {c.target_words_min}-{c.target_words_max} words of markdown "
        "with at least 2 section headings (##).\n"
        "- Tone: measured engineering commentary, not marketing. If you cannot "
        "support a claim from the fact sheet, omit it or hedge explicitly.\n"
        f"- Do NOT use any of these banned/marketing phrases: {_banned_list(settings)}.\n"
        f"- Provide a separate one-paragraph summary <= {c.summary_max_chars} characters "
        "suitable for a microblog (Bluesky/Mastodon), including the repo URL.\n"
        f"- Provide up to {c.max_tags} lowercase, single-word-ish tags.\n\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        '{"title": str, "summary": str, "tags": [str], "angle": str, "body_markdown": str}'
    )


# ------------------------------------------------------------------- reviewer
def reviewer_system(settings: Settings) -> str:
    return (
        "You are a meticulous technical editor and fact-checker reviewing an "
        f"engineering post written for {AUDIENCE}. You are skeptical and "
        "evidence-driven. Your job is to catch factual errors, unsupported or "
        "exaggerated claims, weak/incorrect technical explanations, hallucinated "
        "features, generic AI-sounding filler, and anything not supported by the "
        "provided repo fact sheet. You output strict JSON only."
    )


def reviewer_prompt(repo: Repository, draft: Draft, settings: Settings) -> str:
    r = settings.review
    return (
        "TASK: REVIEWER\n\n"
        "Review the DRAFT below against the repo FACT SHEET. Check: factual "
        "accuracy vs the fact sheet/README, unsupported claims, exaggeration/"
        "overhype, technical correctness, clarity, structure, tone, whether it "
        "reads as generic AI output, and usefulness to the target audience. "
        "Flag any claim not supported by the fact sheet.\n\n"
        f"{build_repo_facts(repo)}\n\n"
        "--- DRAFT ---\n"
        f"TITLE: {draft.title}\n\n"
        f"{draft.body_markdown}\n\n"
        f"SUMMARY: {draft.summary}\n"
        "--- end DRAFT ---\n\n"
        "Scoring guidance:\n"
        f"- overall_score is 0-10. Approve only if score >= {r.min_overall_score} "
        "AND there are no high-severity issues.\n"
        "- severity for each issue is one of: low, medium, high. Use high for "
        "factual errors, hallucinated features, or unsupported strong claims.\n"
        "- recommended_action is one of: approve, revise, reject.\n\n"
        "Return ONLY a JSON object with this shape:\n"
        "{\n"
        '  "approved": bool,\n'
        '  "overall_score": number,\n'
        '  "severity": "low"|"medium"|"high",\n'
        '  "issues": [\n'
        '    {"type": str, "severity": "low"|"medium"|"high", "text": str, '
        '"problem": str, "suggested_fix": str}\n'
        "  ],\n"
        '  "recommended_action": "approve"|"revise"|"reject",\n'
        '  "notes": str\n'
        "}"
    )


# -------------------------------------------------------------------- reviser
def reviser_system(settings: Settings) -> str:
    return (
        "You are the original author revising your technical post in response to "
        "an editor's review. You fix every issue raised without introducing new "
        "unsupported claims, and you keep the grounded, practical tone."
    )


def reviser_prompt(repo: Repository, draft: Draft, review: ReviewResult,
                   settings: Settings, quality_issues: list[str] | None = None) -> str:
    issues_text = "\n".join(
        f"- [{i.severity}] {i.type}: {i.problem} (fix: {i.suggested_fix})"
        for i in review.issues
    ) or "- (no specific issues; tighten and improve grounding)"
    quality_text = ""
    if quality_issues:
        quality_text = (
            "\n--- AUTOMATED QUALITY FAILURES (these MUST be fixed) ---\n"
            + "\n".join(f"- {q}" for q in quality_issues)
            + "\n--- end QUALITY FAILURES ---\n"
        )
    return (
        "TASK: REVISER\n\n"
        "Revise the DRAFT to address ALL reviewer issues AND every automated "
        "quality failure below. Stay grounded in the fact sheet; do not add "
        "unsupported claims. Keep the structure and length guidance from the "
        "original task (>= 2 '##' headings, mention the repo by name, no "
        "placeholder text).\n\n"
        f"{build_repo_facts(repo)}\n\n"
        "--- CURRENT DRAFT ---\n"
        f"TITLE: {draft.title}\n\n{draft.body_markdown}\n\n"
        f"SUMMARY: {draft.summary}\n"
        "--- end DRAFT ---\n\n"
        "--- REVIEWER ISSUES ---\n"
        f"{issues_text}\n"
        "--- end ISSUES ---\n"
        f"{quality_text}\n"
        f"Do NOT use these banned phrases: {_banned_list(settings)}.\n\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        '{"title": str, "summary": str, "tags": [str], "angle": str, "body_markdown": str}'
    )
