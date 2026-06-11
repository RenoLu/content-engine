"""Shared helpers for publishers (markdown->text, truncation, microblog text)."""

from __future__ import annotations

import re

from ..models import Post

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"(\*\*|__|\*|_)")
_MULTISPACE_RE = re.compile(r"[ \t]+")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")


def to_plain_text(markdown: str) -> str:
    """Best-effort markdown -> plain text for character-limited platforms."""
    text = markdown or ""
    text = _FENCE_RE.sub("", text)
    text = _IMG_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)        # keep link label, drop URL syntax
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADING_RE.sub("", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _MULTISPACE_RE.sub(" ", text)
    text = _MULTINEWLINE_RE.sub("\n\n", text)
    return text.strip()


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")


def _attr_esc(value: str) -> str:
    """Escape a value for safe use inside a double-quoted HTML attribute.

    ``_esc`` has already neutralized &/</> on the surrounding text; here we also
    escape the double-quote so a crafted URL can't break out of href/src="...".
    """
    return value.replace('"', "&quot;")


def _inline_md_to_html(text: str) -> str:
    """Inline-level markdown -> HTML (escapes first, then re-introduces tags)."""
    out = _esc(text)
    out = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    # Images must run before links (image syntax is a superset of link syntax).
    out = _MD_IMG_RE.sub(
        lambda m: f'<img src="{_attr_esc(m.group(2))}" alt="{_attr_esc(m.group(1))}">', out)
    out = _MD_LINK_RE.sub(
        lambda m: f'<a href="{_attr_esc(m.group(2))}">{m.group(1)}</a>', out)
    out = _MD_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _MD_ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    return out


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def markdown_to_html(markdown: str) -> str:
    """Block-level markdown -> HTML for platforms that require HTML (Ghost,
    WordPress). Handles headings, fenced code, ordered/unordered lists,
    blockquotes, horizontal rules, GitHub-flavored tables, images, links, and
    inline emphasis/code. Not a full CommonMark implementation (no nested lists
    or reference links) — see docs/API_FINDINGS.md. Markdown-native publishers
    (DEV.to, Hashnode) bypass this and receive the raw markdown."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    html: list[str] = []
    i = 0
    n = len(lines)

    def flush_para(buf: list[str]) -> None:
        if buf:
            html.append("<p>" + _inline_md_to_html(" ".join(buf).strip()) + "</p>")
            buf.clear()

    para: list[str] = []
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code block
        if stripped.startswith("```"):
            flush_para(para)
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            html.append("<pre><code>" + _esc("\n".join(code)) + "</code></pre>")
            continue

        # heading
        m = re.match(r"(#{1,6})\s+(.*)", stripped)
        if m:
            flush_para(para)
            level = len(m.group(1))
            html.append(f"<h{level}>{_inline_md_to_html(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # horizontal rule
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para(para)
            html.append("<hr>")
            i += 1
            continue

        # GitHub-flavored table: a "| ... |" row followed by a "| --- |" separator
        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_para(para)
            header = _split_table_row(stripped)
            i += 2  # consume header + separator
            body_rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body_rows.append(_split_table_row(lines[i]))
                i += 1
            thead = "<thead><tr>" + "".join(
                f"<th>{_inline_md_to_html(c)}</th>" for c in header) + "</tr></thead>"
            tbody_rows = "".join(
                "<tr>" + "".join(f"<td>{_inline_md_to_html(c)}</td>" for c in row) + "</tr>"
                for row in body_rows
            )
            html.append(f"<table>{thead}<tbody>{tbody_rows}</tbody></table>")
            continue

        # unordered list
        if re.match(r"[-*+]\s+", stripped):
            flush_para(para)
            items: list[str] = []
            while i < n and re.match(r"[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            html.append("<ul>" + "".join(f"<li>{_inline_md_to_html(it)}</li>" for it in items) + "</ul>")
            continue

        # ordered list
        if re.match(r"\d+\.\s+", stripped):
            flush_para(para)
            items = []
            while i < n and re.match(r"\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            html.append("<ol>" + "".join(f"<li>{_inline_md_to_html(it)}</li>" for it in items) + "</ol>")
            continue

        # blockquote
        if stripped.startswith(">"):
            flush_para(para)
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            html.append("<blockquote>" + _inline_md_to_html(" ".join(quote)) + "</blockquote>")
            continue

        # blank line ends a paragraph
        if not stripped:
            flush_para(para)
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para(para)
    return "\n".join(html)


def microblog_text(post: Post, limit: int, include_url: bool = True) -> str:
    """Compose a microblog-sized message from a Post, reserving room for the URL."""
    base = (post.summary or post.title or "").strip()
    url = post.repo_url or post.canonical_url or ""
    if include_url and url:
        if url in base:
            return truncate(base, limit)
        reserve = len(url) + 1
        body = truncate(base, max(0, limit - reserve))
        return f"{body}\n{url}".strip()
    return truncate(base, limit)
