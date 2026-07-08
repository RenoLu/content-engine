"""Article image generation via Pollinations (free, no API key, CI-safe).

Pollinations serves Flux-generated images at a deterministic URL built from the
prompt, so the URL itself is a stable public image (usable directly as a DEV.to
cover) and the bytes can be fetched once for platforms that need an upload
(Bluesky blob, Mastodon media). No key to manage, works in CI. This is the
automated default; higher-fidelity one-offs are done manually (ChatGPT via Kimi).

The prompt should describe what the SPECIFIC article is about (per Yan: the image
must illustrate the piece, not be a generic tech visual); a shared style suffix
keeps the look consistent across posts.
"""

from __future__ import annotations

import urllib.parse
import zlib

from .models import ImageAsset

_BASE = "https://image.pollinations.ai/prompt/"

# Appended to every prompt so posts share a look; the caller's prompt carries the
# article-specific subject. "no text" matters: diffusion models garble on-image text.
_STYLE = (" Editorial tech illustration, clean and modern, isometric, teal and "
          "slate-blue palette, soft light background, minimal, professional, "
          "no text, no words, no letters.")


def build_image_url(prompt: str, *, width: int = 1280, height: int = 720,
                    seed: int | None = None, model: str = "flux") -> str:
    full = (prompt.strip() + _STYLE).strip()
    if seed is None:
        # deterministic per prompt so a re-run yields the same image (crc32, since
        # the builtin hash() is salted per process)
        seed = zlib.crc32(full.encode("utf-8")) % 1_000_000
    q = urllib.parse.quote(full, safe="")
    return (f"{_BASE}{q}?width={width}&height={height}"
            f"&nologo=true&model={model}&seed={seed}")


def generate(prompt: str, *, alt: str = "", width: int = 1280, height: int = 720,
             model: str = "flux") -> ImageAsset | None:
    """Build an ImageAsset for the given article-specific prompt (no network yet;
    bytes are fetched lazily by publishers that need them)."""
    if not prompt or not prompt.strip():
        return None
    url = build_image_url(prompt, width=width, height=height, model=model)
    return ImageAsset(url=url, alt=alt.strip() or prompt.strip()[:280])
