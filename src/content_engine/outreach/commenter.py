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
- Plain, human voice. No hashtags. No links. No emojis.
- Write in plain English only. If the post is in another language, still reply in English.
- Never use em-dashes, en-dashes, or arrows (-> or =>). Use commas, periods, or parentheses. These punctuation habits read as AI-written.
- Do not open with "The " followed by a noun phrase and a dash; just say the thing plainly.
- Vary your opening. Do NOT start with "Curious" or "The"; open differently each time so replies never look templated.
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
# AI-tell punctuation: em/en dash used as a clause break, and arrows.
_DASH_RE = re.compile(r"\s*[—–]\s*")
_ARROW_RE = re.compile(r"\s*(->|=>|→)\s*")
# Anything outside common Latin text/punctuation: emoji, symbols, non-Latin scripts.
_NON_LATIN_RE = re.compile(
    r"[^\x00-\x7fÀ-ɏ‘’“”]"
)
# Common English function words used to sanity-check the SOURCE post is English;
# replying in English to a non-English post reads like an out-of-context bot.
_EN_STOPWORDS = {
    "the", "and", "is", "to", "of", "a", "in", "that", "it", "for", "you",
    "this", "with", "on", "are", "was", "but", "not", "have", "how", "what",
    "i", "we", "they", "as", "at", "be", "or", "an", "so", "if", "your",
    "about", "from", "by", "can", "will", "just", "more", "one", "all",
    "would", "there", "when", "which", "their", "them", "its", "into",
    "out", "up", "do", "does", "has", "had", "been", "who", "why", "our",
    "my", "me", "he", "she", "his", "her", "no", "yes", "get", "got", "make",
}


def _looks_english(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", (text or "").lower())
    if len(words) < 6:
        return True  # too short to judge; let the reply gate handle substance
    # clearly-foreign text has ~zero common English words; genuine English
    # (even terse) has at least one. One hit in 6+ words is enough signal.
    hits = sum(1 for w in words if w in _EN_STOPWORDS)
    return hits >= 1


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
        # normalize smart quotes to straight
        r = r.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))
        # AI-tell punctuation: a dash used as a clause break becomes a comma;
        # arrows become plain words/spaces. This is the core "remove AI traces" step.
        r = _DASH_RE.sub(", ", r)
        r = _ARROW_RE.sub(" ", r)
        r = _NON_LATIN_RE.sub("", r)      # drop emoji / stray non-Latin glyphs
        r = re.sub(r"\s+,", ",", r)        # tidy a space-before-comma from substitutions
        r = re.sub(r",\s*,", ",", r)       # collapse a doubled comma
        r = re.sub(r"\s+([.!?])", r"\1", r)  # no space before end punctuation
        return re.sub(r"\s+", " ", r).strip().strip(",").strip()

    def _gate(self, reply: str, *, source: str) -> None:
        low = reply.lower()
        if len(reply) < 15:
            raise ReplyRejected("too short")
        if len(reply) > 300:
            raise ReplyRejected("too long")
        # don't reply in English to a post written in another language
        if not _looks_english(source):
            raise ReplyRejected("non-english source")
        # safety net: no AI-tell punctuation may survive into a live reply
        if any(ch in reply for ch in ("—", "–", "->", "=>", "→")):
            raise ReplyRejected("contains dash/arrow ai-tell")
        if _NON_LATIN_RE.search(reply):
            raise ReplyRejected("contains emoji/non-latin glyph")
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
