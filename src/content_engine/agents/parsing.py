"""Robust JSON extraction from model output.

Models sometimes wrap JSON in ```json fences or add prose around it. These
helpers recover the first valid JSON object/array from such output.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class JSONExtractionError(ValueError):
    pass


def extract_json(text: str) -> Any:
    """Return the first JSON value found in ``text``.

    Tries, in order: the whole string, fenced code blocks, then a brace/bracket
    scan for the first balanced ``{...}`` or ``[...]``.
    """
    if text is None:
        raise JSONExtractionError("no text to parse")

    candidates: list[str] = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    span = _first_balanced(text)
    if span is not None:
        candidates.append(span)

    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise JSONExtractionError("could not parse JSON from model output")


def _first_balanced(text: str) -> str | None:
    """Find the first balanced {...} or [...] region (string-aware)."""
    start = None
    open_ch = close_ch = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start, open_ch = i, ch
            close_ch = "}" if ch == "{" else "]"
            break
    if start is None:
        return None

    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None
