"""Configuration loading: merges config/config.toml (tunables) with environment
variables (secrets + mode). Produces a typed, immutable-ish ``Settings`` object.

Design goals:
  * Works with zero setup: sensible defaults + AI_PROVIDER=mock + dry-run.
  * Secrets never come from the TOML file, only from the environment / .env.
  * Easy to construct in tests by passing an explicit ``env`` mapping.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:  # python-dotenv is optional at import time (tests may not need it)
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


# Single source of truth for the banned-phrase quality gate. Keep this in sync
# with config/config.toml [quality].banned_phrases; this constant is the fallback
# used when no TOML file is present (e.g. a packaged/minimal deployment).
DEFAULT_BANNED_PHRASES: list[str] = [
    "game changer", "game-changer", "revolutionary", "this changes everything",
    "unlocks the future", "ai is transforming everything", "groundbreaking",
    "next big thing", "supercharge your workflow", "10x productivity",
    "in today's fast-paced world", "in the world of", "look no further",
    "dive in", "delve into", "in conclusion", "in summary,",
    "as an ai", "i cannot", "elevate your", "seamlessly integrate",
]

# Built-in defaults mirror config/config.toml so the app runs even without it.
_DEFAULTS: dict[str, Any] = {
    "source": {
        "github": {
            "min_stars": 150,
            "rising_min_stars": 75,
            "rising_created_within_days": 30,
            "active_pushed_within_days": 14,
            "per_query_limit": 50,
            "languages": [],
            "topics": [],
            "preferred_topics": [
                "llm", "ai", "machine-learning", "developer-tools", "cli",
                "database", "kubernetes", "observability", "data-engineering",
                "rust", "golang", "python", "typescript", "fintech", "quant",
                "trading", "security", "infrastructure", "framework", "api",
                "compiler", "runtime",
            ],
        }
    },
    "ranking": {
        "min_stars": 150,
        "min_readme_chars": 800,
        "skip_archived": True,
        "skip_forks": True,
        "allow_forks_min_stars": 20000,
        "require_description": True,
        "max_repo_age_days": 0,
        "blocklist": [],
    },
    "scoring": {
        "weight_stars": 1.0,
        "weight_recent_push": 0.6,
        "weight_rising": 0.8,
        "weight_topic_match": 0.5,
        "weight_readme_quality": 0.3,
        "weight_has_homepage": 0.1,
    },
    "content": {
        "target_words_min": 450,
        "target_words_max": 900,
        "summary_max_chars": 280,
        "max_tags": 4,
    },
    "review": {
        "min_overall_score": 7.0,
        "block_on_high_severity": True,
        "max_revisions": 1,
    },
    "quality": {
        "banned_phrases": DEFAULT_BANNED_PHRASES,
        "max_banned_phrase_hits": 0,
        "require_repo_name_in_body": True,
        "min_headings": 2,
    },
}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Typed config sections
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GitHubSourceConfig:
    min_stars: int
    rising_min_stars: int
    rising_created_within_days: int
    active_pushed_within_days: int
    per_query_limit: int
    languages: list[str]
    topics: list[str]
    preferred_topics: list[str]


@dataclass(frozen=True)
class RankingConfig:
    min_stars: int
    min_readme_chars: int
    skip_archived: bool
    skip_forks: bool
    allow_forks_min_stars: int
    require_description: bool
    max_repo_age_days: int
    blocklist: list[str]


@dataclass(frozen=True)
class ScoringConfig:
    weight_stars: float
    weight_recent_push: float
    weight_rising: float
    weight_topic_match: float
    weight_readme_quality: float
    weight_has_homepage: float


@dataclass(frozen=True)
class ContentConfig:
    target_words_min: int
    target_words_max: int
    summary_max_chars: int
    max_tags: int


@dataclass(frozen=True)
class ReviewConfig:
    min_overall_score: float
    block_on_high_severity: bool
    max_revisions: int


@dataclass(frozen=True)
class QualityConfig:
    banned_phrases: list[str]
    max_banned_phrase_hits: int
    require_repo_name_in_body: bool
    min_headings: int


@dataclass(frozen=True)
class Settings:
    publish_mode: str                 # "dry_run" | "live"
    ai_provider: str                  # "mock" | "openai" | "anthropic"
    openai_api_key: str
    anthropic_api_key: str
    openai_model: str
    anthropic_model: str
    github_token: str
    db_path: Path
    output_dir: Path
    enabled_publishers: list[str]
    project_root: Path

    github: GitHubSourceConfig
    ranking: RankingConfig
    scoring: ScoringConfig
    content: ContentConfig
    review: ReviewConfig
    quality: QualityConfig

    # Raw environment snapshot for publisher-specific credentials.
    env: dict[str, str] = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.publish_mode == "live"

    def get_env(self, key: str, default: str = "") -> str:
        return self.env.get(key, default)


def _project_root() -> Path:
    # src/content_engine/config.py -> project root is two levels up from src/
    return Path(__file__).resolve().parents[2]


def load_settings(
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    load_dotenv_file: bool = True,
) -> Settings:
    """Build a Settings object.

    Args:
        env: explicit environment mapping (defaults to os.environ). Handy for tests.
        config_path: path to a TOML config (defaults to config/config.toml).
        load_dotenv_file: whether to load a local .env into os.environ first.
    """
    root = _project_root()

    if env is None:
        if load_dotenv_file and load_dotenv is not None:
            load_dotenv(root / ".env")
        env = dict(os.environ)
    else:
        env = dict(env)

    # ---- load + merge config file over defaults ----
    cfg_path = Path(config_path) if config_path else (root / "config" / "config.toml")
    file_cfg: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            file_cfg = tomllib.load(fh)
    cfg = _deep_merge(_DEFAULTS, file_cfg)

    gh = cfg["source"]["github"]
    rk = cfg["ranking"]
    sc = cfg["scoring"]
    ct = cfg["content"]
    rv = cfg["review"]
    ql = cfg["quality"]

    def _str(key: str, default: str = "") -> str:
        return str(env.get(key, default) or default)

    publish_mode = _str("PUBLISH_MODE", "dry_run").strip().lower()
    if publish_mode not in ("dry_run", "live"):
        publish_mode = "dry_run"

    ai_provider = _str("AI_PROVIDER", "mock").strip().lower()

    db_path = Path(_str("DB_PATH") or (root / "data" / "content_engine.sqlite3"))
    output_dir = Path(_str("OUTPUT_DIR") or (root / "output"))

    enabled = [p.strip() for p in _str("ENABLED_PUBLISHERS", "dryrun").split(",") if p.strip()]
    if not enabled:
        enabled = ["dryrun"]

    return Settings(
        publish_mode=publish_mode,
        ai_provider=ai_provider,
        openai_api_key=_str("OPENAI_API_KEY"),
        anthropic_api_key=_str("ANTHROPIC_API_KEY"),
        openai_model=_str("OPENAI_MODEL", "gpt-4o-mini"),
        anthropic_model=_str("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        github_token=_str("GITHUB_TOKEN"),
        db_path=db_path,
        output_dir=output_dir,
        enabled_publishers=enabled,
        project_root=root,
        github=GitHubSourceConfig(
            min_stars=int(gh["min_stars"]),
            rising_min_stars=int(gh["rising_min_stars"]),
            rising_created_within_days=int(gh["rising_created_within_days"]),
            active_pushed_within_days=int(gh["active_pushed_within_days"]),
            per_query_limit=int(gh["per_query_limit"]),
            languages=list(gh["languages"]),
            topics=list(gh["topics"]),
            preferred_topics=list(gh["preferred_topics"]),
        ),
        ranking=RankingConfig(
            min_stars=int(rk["min_stars"]),
            min_readme_chars=int(rk["min_readme_chars"]),
            skip_archived=bool(rk["skip_archived"]),
            skip_forks=bool(rk["skip_forks"]),
            allow_forks_min_stars=int(rk["allow_forks_min_stars"]),
            require_description=bool(rk["require_description"]),
            max_repo_age_days=int(rk["max_repo_age_days"]),
            blocklist=[str(b).lower() for b in rk["blocklist"]],
        ),
        scoring=ScoringConfig(
            weight_stars=float(sc["weight_stars"]),
            weight_recent_push=float(sc["weight_recent_push"]),
            weight_rising=float(sc["weight_rising"]),
            weight_topic_match=float(sc["weight_topic_match"]),
            weight_readme_quality=float(sc["weight_readme_quality"]),
            weight_has_homepage=float(sc["weight_has_homepage"]),
        ),
        content=ContentConfig(
            target_words_min=int(ct["target_words_min"]),
            target_words_max=int(ct["target_words_max"]),
            summary_max_chars=int(ct["summary_max_chars"]),
            max_tags=int(ct["max_tags"]),
        ),
        review=ReviewConfig(
            min_overall_score=float(rv["min_overall_score"]),
            block_on_high_severity=bool(rv["block_on_high_severity"]),
            max_revisions=int(rv["max_revisions"]),
        ),
        quality=QualityConfig(
            banned_phrases=[str(p).lower() for p in ql["banned_phrases"]],
            max_banned_phrase_hits=int(ql["max_banned_phrase_hits"]),
            require_repo_name_in_body=bool(ql["require_repo_name_in_body"]),
            min_headings=int(ql["min_headings"]),
        ),
        env={k: str(v) for k, v in env.items()},
    )
