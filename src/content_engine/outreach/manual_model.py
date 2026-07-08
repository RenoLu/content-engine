"""Agent-as-model reply source for outreach.

The hosted-model account is out of credit, so instead of calling an API for reply
text we let Claude (the agent) author the replies and replay them here. This is
the same pattern as ``_manual/publish_manual.py``'s ``ClaudeReplayClient``: only
the raw text changes provider; the real quality gate (``Commenter._gate``), the
caps, the dedupe, and the live adapters all run exactly as they do with an API.

A reply is matched to its post by a normalized snippet of the post text, because
that is what the commenter embeds in the prompt it hands the model. If no authored
reply matches, ``complete`` returns "" and the commenter's gate rejects it (too
short), so that target is skipped gracefully instead of getting a bot reply.
"""

from __future__ import annotations

import re

from ..agents.model_client import ModelClient

_SNIPPET_LEN = 60


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


class ManualReplyClient(ModelClient):
    """Replays agent-authored replies keyed by a snippet of the post text.

    ``replies_by_text`` maps a post's text (the same text the commenter puts in
    its prompt) to the reply the agent wrote for it. Only reply prompts are
    served; any other completion (there are none in the outreach path) returns "".
    """

    name = "manual"
    model = "claude (agent-as-model)"

    def __init__(self, replies_by_text: dict[str, str], snippet_len: int = _SNIPPET_LEN):
        self._snippet_len = snippet_len
        # key on a normalized leading snippet so minor prompt formatting can't
        # break the match; drop empties so a blank post never matches everything.
        self._by_snippet: dict[str, str] = {}
        for text, reply in replies_by_text.items():
            snip = _norm(text)[:snippet_len]
            if snip and reply:
                self._by_snippet[snip] = reply

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        p = _norm(prompt)
        for snip, reply in self._by_snippet.items():
            if snip in p:
                return reply
        return ""  # no authored reply -> gate rejects -> target skipped
