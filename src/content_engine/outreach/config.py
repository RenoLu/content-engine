"""Outreach configuration.

Kept out of the core ``Settings`` dataclass on purpose: outreach is an optional
subsystem, so its knobs live in their own loader that reads env vars (secrets +
mode) plus an ``[outreach]`` section from config.toml (tunables). We still take a
``Settings`` in so we can reuse its env snapshot, model client, and quality gate.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings


def _as_bool(v: str | None, default: bool) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _as_int(v: str | None, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PlatformCaps:
    like: int
    follow: int
    reply: int

    def for_action(self, action_type: str) -> int:
        return {"like": self.like, "follow": self.follow, "reply": self.reply}.get(
            action_type, 0
        )


# Conservative defaults. LinkedIn caps are intentionally the lowest because it is
# the one platform that penalizes automation; the API-friendly platforms allow more.
_DEFAULT_CAPS: dict[str, dict[str, int]] = {
    "bluesky": {"like": 20, "follow": 10, "reply": 8},
    "mastodon": {"like": 20, "follow": 10, "reply": 8},
    "devto": {"like": 0, "follow": 0, "reply": 0},
    "linkedin": {"like": 8, "follow": 4, "reply": 3},
}

_DEFAULT_QUERIES = [
    "data engineering", "lakehouse", "streaming ML", "MLOps",
    "AI agents", "LLM applications", "quant", "iceberg",
]


@dataclass(frozen=True)
class OutreachConfig:
    enabled: bool                 # kill switch
    mode: str                     # "dry_run" | "live"
    approval: bool                # draft-only, log as pending_approval
    platforms: list[str]          # which adapters to run
    queries: list[str]            # topic queries for discovery
    per_query_limit: int          # targets fetched per query
    caps: dict[str, PlatformCaps]  # per-platform daily caps
    like_ratio: float             # fraction of eligible targets to like
    reply_ratio: float            # fraction of eligible targets to reply to
    seed: int                     # deterministic RNG seed for pacing/sampling
    settings: Settings = field(repr=False, default=None)  # for model client + env

    @property
    def is_live(self) -> bool:
        return self.mode == "live" and not self.approval

    def caps_for(self, platform: str) -> PlatformCaps:
        return self.caps.get(platform, PlatformCaps(0, 0, 0))


def _load_toml_outreach(root: Path) -> dict:
    path = root / "config" / "config.toml"
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh).get("outreach", {}) or {}


def load_outreach_config(settings: Settings) -> OutreachConfig:
    env = settings.env
    toml = _load_toml_outreach(settings.project_root)

    def envv(key: str, default: str = "") -> str:
        return str(env.get(key, default) or default)

    mode = envv("OUTREACH_MODE", "dry_run").strip().lower()
    if mode not in ("dry_run", "live"):
        mode = "dry_run"

    platforms_raw = envv("OUTREACH_PLATFORMS", ",".join(toml.get("platforms", ["bluesky", "mastodon", "devto"])))
    platforms = [p.strip().lower() for p in platforms_raw.split(",") if p.strip()]

    queries = list(toml.get("queries", _DEFAULT_QUERIES))
    if envv("OUTREACH_QUERIES"):
        queries = [q.strip() for q in envv("OUTREACH_QUERIES").split(",") if q.strip()]

    # Caps: TOML [outreach.caps.<platform>] overrides defaults; a single env
    # OUTREACH_CAP_SCALE can dial every cap up/down without editing per-platform.
    toml_caps = toml.get("caps", {}) or {}
    scale = float(envv("OUTREACH_CAP_SCALE", "1") or "1")
    caps: dict[str, PlatformCaps] = {}
    for plat, dflt in _DEFAULT_CAPS.items():
        merged = {**dflt, **(toml_caps.get(plat, {}) or {})}
        caps[plat] = PlatformCaps(
            like=max(0, int(merged["like"] * scale)),
            follow=max(0, int(merged["follow"] * scale)),
            reply=max(0, int(merged["reply"] * scale)),
        )

    return OutreachConfig(
        enabled=_as_bool(envv("OUTREACH_ENABLED", str(toml.get("enabled", True))), True),
        mode=mode,
        approval=_as_bool(envv("OUTREACH_APPROVAL", str(toml.get("approval", False))), False),
        platforms=platforms,
        queries=queries,
        per_query_limit=_as_int(envv("OUTREACH_PER_QUERY_LIMIT", str(toml.get("per_query_limit", 15))), 15),
        caps=caps,
        like_ratio=float(envv("OUTREACH_LIKE_RATIO", str(toml.get("like_ratio", 0.8))) or 0.8),
        reply_ratio=float(envv("OUTREACH_REPLY_RATIO", str(toml.get("reply_ratio", 0.35))) or 0.35),
        seed=_as_int(envv("OUTREACH_SEED", str(toml.get("seed", 1337))), 1337),
        settings=settings,
    )
