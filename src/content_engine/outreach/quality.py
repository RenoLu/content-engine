"""Target quality filters for outreach discovery.

Topic searches on `latest` sort surface a lot of automation: job-board bots,
arXiv/HN/RSS reposters, and pure event/product promos. Engaging those makes the
account look like spam and reaches no real person, so we drop them before they
enter the funnel. Kept deliberately conservative -- a false positive silently
skips a real human, so the patterns only match handles/text that are clearly
automated or promotional.
"""

from __future__ import annotations

import re

# A handle whose first label carries one of these tokens is almost always a feed
# or bot account (e.g. "arxiv-daily-bot", "some-rss-feeds", "hn-frontpage-bot").
_BOT_TOKEN_RE = re.compile(
    r"(?:^|[._-])(?:bot|bots|rss|feed|feeds|digest|daily|newsbot|jobbot|hiring)(?:[._-]|$)",
    re.IGNORECASE,
)

# Substrings in a link/text that mark a job listing or pure promo blast.
_PROMO_MARKERS = (
    "/jobs/", "/job/", "educativ.net", "eventbrite", "bit.ly/",
    "register now", "register 🔗", "keynote:", "conference & expo",
)


def _first_label(handle: str) -> str:
    # "arxiv-daily-bot.bsky.social" -> "arxiv-daily-bot"; "user@host" -> "user"
    return (handle or "").lower().split("@")[0].split(".")[0]


def looks_like_bot(handle: str, text: str = "") -> bool:
    """True if the target is a bot/feed account or a pure promo blast."""
    if _BOT_TOKEN_RE.search(_first_label(handle)):
        return True
    low = (text or "").lower()
    if any(m in low for m in _PROMO_MARKERS):
        return True
    return False
