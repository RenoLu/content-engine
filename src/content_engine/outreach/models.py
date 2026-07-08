"""Data types for the outreach engine.

Deliberately small and JSON-friendly. A ``Target`` is something we might engage
(a post, or the person who wrote it). An ``Action`` is a decision to like/follow/
reply to a target. An ``ActionResult`` is the outcome, mirroring the shape of the
publishers' ``PublishResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ActionType(str, Enum):
    LIKE = "like"
    FOLLOW = "follow"
    REPLY = "reply"


@dataclass(frozen=True)
class Target:
    """A post (and its author) we might engage with.

    ``key`` is the stable dedupe identity for the *target* (usually the post URI
    or id). ``author_id`` is the platform id used to follow the author. ``uri`` /
    ``cid`` carry the AT-Protocol strong-ref needed for Bluesky like/reply.
    """

    platform: str
    key: str                      # stable id for the post/target (dedupe anchor)
    text: str = ""                # the post's text (context for the reply model)
    url: str = ""                 # human-facing URL, for logs
    author_id: str = ""           # platform id/DID used to follow the author
    author_handle: str = ""       # display handle, for logs + reply mentions
    uri: str = ""                 # AT-URI (Bluesky) of the post
    cid: str = ""                 # AT content id (Bluesky) of the post
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    platform: str
    action_type: ActionType
    target: Target
    comment: str = ""             # reply text (REPLY only)


@dataclass
class ActionResult:
    platform: str
    action_type: ActionType
    target_key: str
    status: str                   # executed | dry_run | pending_approval | skipped | failed
    url: str | None = None        # resulting URL (e.g. the reply's permalink)
    error: str | None = None
    detail: str | None = None     # short human note (e.g. why skipped)

    @property
    def acted(self) -> bool:
        return self.status == "executed"
