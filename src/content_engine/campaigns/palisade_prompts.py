"""Prompts for the palisade syndication writer.

The job is adaptation, not invention: take an existing agentpalisade.com guide
and produce a DEV.to-native version of it — same substance, fresh phrasing,
practitioner voice. The canonical_url on the post already credits the original,
so the body should read as a standalone article.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .palisade import Guide


def writer_system(settings: Settings) -> str:
    c = settings.content
    return (
        "You write practical articles for DEV.to aimed at small-business "
        "operators and the developers who support them. Voice: direct, "
        "concrete, experienced practitioner; no hype, no marketing cliches, "
        "no em-dash-heavy AI cadence. Ground every claim in the source guide "
        "you are given; do not invent statistics, customer names, or "
        "guarantees. Output JSON only with keys: title, body_markdown, "
        f"summary, tags. Body: {c.target_words_min}-{c.target_words_max} "
        "words, markdown, at least two ## section headings, no H1 (the title "
        "field is the H1). Summary: one sentence under "
        f"{c.summary_max_chars} characters."
    )


def writer_prompt(guide: "Guide", settings: Settings) -> str:
    points = "\n".join(f"- {p}" for p in guide.key_points) or "- (use the summary)"
    tags = ", ".join(guide.tags)
    return (
        "Adapt the following published guide into a standalone DEV.to "
        "article. Rephrase and restructure rather than copying sentences; "
        "keep the substance and the practical, checklist-driven feel. End "
        "with a short takeaway section (the platform CTA footer is appended "
        "separately - do not write your own promotion).\n\n"
        f"Guide title: {guide.title}\n"
        f"Guide summary: {guide.summary}\n"
        f"Canonical page: {guide.url}\n"
        f"Main sections:\n{points}\n"
        f"Suggested tags: {tags}\n"
    )
