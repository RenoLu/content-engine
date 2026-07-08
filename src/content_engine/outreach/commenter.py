"""Contextual reply generation + a quality gate.

The whole point of automated replies is that they must NOT read as bot spam. So
every reply is (1) generated from the target post's actual text by the shared
model client, and (2) run through a gate that rejects anything generic, banned,
empty, or obviously templated. A reply that fails the gate is dropped, not sent.
"""

from __future__ import annotations

import re

from ..agents.model_client import ModelClient
from .config import OutreachConfig

_SYSTEM = (
    "You are Yan Lu, a software engineer who builds production AI and data "
    "systems (streaming ML, data lakehouses, agentic pipelines). You reply to "
    "other people's technical posts the way a thoughtful peer would: specific, "
    "curious, and adding a real thought, never flattering or salesy."
)

_PROMPT = """TASK: OUTREACH REPLY

Write a short reply to this {platform} post. Requirements:
- 1 to 2 sentences, under 280 characters.
- React to something SPECIFIC in the post; show you actually read it.
- Add one genuine thought, question, or point of connection.
- Plain, human voice. No hashtags. No links. No emojis unless natural.
- Never generic praise ("Great post!", "So true!", "Love this!").
- Do not pitch anything or mention your own projects unless directly relevant.

POST AUTHOR: {author}
POST TEXT:
\"\"\"
{text}
\"\"\"

Reply with ONLY the reply text, nothing else."""

# Generic openers that signal low-effort bot engagement. A reply that is *mostly*
# one of these is rejected.
_GENERIC = [
    "great post", "great work", "so true", "love this", "well said",
    "totally agree", "couldn't agree more", "this is awesome", "nice post",
    "amazing", "thanks for sharing", "good stuff", "interesting post",
    "spot on", "100%", "facts", "this!",
]

_URL_RE = re.compile(r"https?://\S+")
_EMOJI_HASHTAG_RE = re.compile(r"#\w+")


class ReplyRejected(Exception):
    """Raised when a generated reply fails the quality gate."""


class Commenter:
    def __init__(self, model: ModelClient, config: OutreachConfig):
        self.model = model
        self.config = config
        self.banned = list(
            (config.settings.quality.banned_phrases if config.settings else [])
        )

    def generate(self, *, platform: str, text: str, author: str) -> str:
        raw = self.model.complete(
            system=_SYSTEM,
            prompt=_PROMPT.format(platform=platform, author=author or "someone", text=text[:1500]),
            max_tokens=160,
            temperature=0.7,
        )
        reply = self._clean(raw)
        self._gate(reply, source=text)
        return reply

    @staticmethod
    def _clean(raw: str) -> str:
        r = (raw or "").strip()
        # models sometimes wrap in quotes or add a "Reply:" preamble
        r = re.sub(r"^(reply|response)\s*[:\-]\s*", "", r, flags=re.IGNORECASE)
        if len(r) >= 2 and r[0] in "\"'" and r[-1] in "\"'":
            r = r[1:-1].strip()
        r = _URL_RE.sub("", r)          # never let a link slip in
        r = _EMOJI_HASHTAG_RE.sub("", r)  # strip hashtags
        return re.sub(r"\s+", " ", r).strip()

    def _gate(self, reply: str, *, source: str) -> None:
        low = reply.lower()
        if len(reply) < 15:
            raise ReplyRejected("too short")
        if len(reply) > 300:
            raise ReplyRejected("too long")
        # banned marketing phrases from the shared quality list
        for phrase in self.banned:
            if phrase and phrase in low:
                raise ReplyRejected(f"banned phrase: {phrase!r}")
        # reject if the reply is basically just a generic opener
        stripped = re.sub(r"[^a-z0-9 ]", "", low).strip()
        for g in _GENERIC:
            if stripped == g or stripped.startswith(g) and len(stripped) < len(g) + 12:
                raise ReplyRejected(f"generic reply: {g!r}")
        # must contain at least a few distinct words (not one repeated token)
        words = [w for w in re.findall(r"[a-z]{3,}", low)]
        if len(set(words)) < 4:
            raise ReplyRejected("not enough substance")
