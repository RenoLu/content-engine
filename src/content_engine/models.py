"""Core data model for the content pipeline.

Plain dataclasses (no pydantic) keep runtime dependencies minimal and make the
objects trivial to serialize to/from the SQLite store as JSON.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    """Today's date (UTC) as YYYY-MM-DD — the canonical per-day idempotency key."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RunStatus(str, Enum):
    """Lifecycle of a single daily pipeline run."""

    PENDING = "pending"
    NO_CANDIDATE = "no_candidate"      # nothing passed the filters
    GENERATING = "generating"
    REVIEWING = "reviewing"
    REVISING = "revising"
    REJECTED = "rejected"              # reviewer/quality gate refused the content
    DRY_RUN = "dry_run"               # completed in dry-run mode (nothing posted)
    PUBLISHED = "published"            # completed and posted live to >=1 publisher
    FAILED = "failed"
    SKIPPED = "skipped"               # an existing terminal run for this date already exists


# Statuses after which a run for a given date is considered "done".
TERMINAL_STATUSES = {
    RunStatus.NO_CANDIDATE,
    RunStatus.REJECTED,
    RunStatus.DRY_RUN,
    RunStatus.PUBLISHED,
}


@dataclass
class Repository:
    """A GitHub repository candidate, optionally enriched with README + score."""

    full_name: str                    # "owner/name" — the natural dedup key
    name: str
    owner: str
    html_url: str
    description: str | None
    homepage: str | None
    language: str | None
    stars: int
    forks: int
    watchers: int
    open_issues: int
    topics: list[str] = field(default_factory=list)
    license: str | None = None
    created_at: str | None = None
    pushed_at: str | None = None
    updated_at: str | None = None
    is_archived: bool = False
    is_fork: bool = False
    default_branch: str = "main"

    # Zero-based position on github.com/trending when sourced from the page
    # (None when the repo came from the Search-API source). Used as a scoring signal.
    trending_rank: int | None = None

    # ---- enrichment (filled by research/ranking stages) ----
    readme_markdown: str | None = None
    readme_len: int = 0
    extracted_links: list[str] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Repository":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Draft:
    """A generated article/post draft."""

    title: str
    body_markdown: str
    summary: str                      # short version for microblog platforms
    tags: list[str] = field(default_factory=list)
    angle: str = ""
    image_prompt: str = ""            # article-specific subject for the post image
    model: str = ""
    raw: str | None = None            # raw model output for debugging

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Draft":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ReviewIssue:
    type: str
    severity: str                     # low | medium | high
    text: str
    problem: str
    suggested_fix: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ReviewResult:
    """Structured output of the reviewer agent."""

    approved: bool
    overall_score: float
    severity: str                     # low | medium | high
    issues: list[ReviewIssue] = field(default_factory=list)
    recommended_action: str = "revise"  # approve | revise | reject
    notes: str = ""
    model: str = ""

    @property
    def high_severity_issues(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "high"]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReviewResult":
        issues = [ReviewIssue(**i) if isinstance(i, dict) else i for i in d.get("issues", [])]
        return cls(
            approved=bool(d.get("approved", False)),
            overall_score=float(d.get("overall_score", 0.0)),
            severity=str(d.get("severity", "medium")),
            issues=issues,
            recommended_action=str(d.get("recommended_action", "revise")),
            notes=str(d.get("notes", "")),
            model=str(d.get("model", "")),
        )


@dataclass
class EngagementReview:
    """Structured output of the engagement/voice reviewer agent.

    Scores two axes the fact-checking reviewer ignores: whether the piece
    *catches attention* (hook, distinctive angle, reader pull) and whether it
    *sounds human* (natural voice, active verbs, no AI-tells/filler). Reuses
    ``ReviewIssue`` so its findings feed the existing reviser unchanged.
    """

    approved: bool
    attention_score: float            # 0-10: hook, distinctive angle, reader pull
    voice_score: float                # 0-10: human/natural, active voice, no AI-tells
    severity: str                     # low | medium | high
    issues: list[ReviewIssue] = field(default_factory=list)
    recommended_action: str = "revise"  # approve | revise | reject
    notes: str = ""
    model: str = ""

    @property
    def overall_score(self) -> float:
        """Weakest-link: both goals must hold, so the gate uses the lower axis."""
        return min(self.attention_score, self.voice_score)

    @property
    def high_severity_issues(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "high"]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ImageAsset:
    """A generated post image. ``url`` is a stable public image (used directly as
    a DEV.to cover); ``ensure_data`` lazily downloads the bytes for platforms that
    upload the image (Bluesky blob, Mastodon media). Kept out of the hot path in
    dry-run — no network happens until a live publisher asks for the bytes."""

    url: str
    alt: str = ""
    mime: str = "image/jpeg"
    _data: bytes | None = field(default=None, repr=False, compare=False)

    def ensure_data(self, client=None, attempts: int = 3) -> bytes | None:
        if self._data is not None:
            return self._data or None
        import httpx
        own = client is None
        c = client or httpx.Client(timeout=120.0, follow_redirects=True)
        try:
            for _ in range(attempts):
                try:
                    r = c.get(self.url, follow_redirects=True)
                    if (r.status_code == 200 and r.content and len(r.content) > 2000
                            and r.headers.get("content-type", "").startswith("image")):
                        self._data = r.content
                        return self._data
                except Exception:  # noqa: BLE001 - retry, then give up gracefully
                    pass
            self._data = b""
            return None
        finally:
            if own:
                c.close()

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "alt": self.alt, "mime": self.mime}


@dataclass
class Post:
    """Platform-neutral content unit handed to publishers."""

    title: str
    body_markdown: str
    summary: str                      # short text for character-limited platforms
    tags: list[str] = field(default_factory=list)
    canonical_url: str | None = None  # where the post canonically lives, if anywhere
    repo_url: str | None = None       # the featured repo (for attribution / link)
    image: "ImageAsset | None" = None  # optional article-specific image

    @classmethod
    def from_draft(cls, draft: Draft, repo: Repository | None = None,
                   canonical_url: str | None = None) -> "Post":
        return cls(
            title=draft.title,
            body_markdown=draft.body_markdown,
            summary=draft.summary,
            tags=list(draft.tags),
            canonical_url=canonical_url,
            repo_url=repo.html_url if repo else None,
        )


@dataclass
class PublishResult:
    """Outcome of a single publisher attempt."""

    publisher: str
    status: str                       # published | dry_run | failed | skipped
    url: str | None = None
    external_id: str | None = None
    error: str | None = None
    dry_run: bool = True
    payload_preview: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("published", "dry_run")

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def to_json(obj: Any) -> str:
    """Serialize dataclasses / nested structures to a compact JSON string."""

    def default(o: Any) -> Any:
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        if isinstance(o, Enum):
            return o.value
        raise TypeError(f"Not JSON-serializable: {type(o)!r}")

    return json.dumps(obj, default=default, ensure_ascii=False)
