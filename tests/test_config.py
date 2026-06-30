"""Config loading + guards against drift between code defaults and config.toml."""

import tomllib
from pathlib import Path

from content_engine.config import DEFAULT_BANNED_PHRASES, load_settings

_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_run_with_no_env_or_dotenv():
    s = load_settings(env={}, load_dotenv_file=False)
    assert s.publish_mode == "dry_run"        # safe default
    assert s.ai_provider == "mock"
    assert s.enabled_publishers == ["dryrun"]


def test_publish_mode_invalid_falls_back_to_dry_run():
    s = load_settings(env={"PUBLISH_MODE": "YOLO"}, load_dotenv_file=False)
    assert s.publish_mode == "dry_run"


def test_env_overrides_apply():
    s = load_settings(
        env={"PUBLISH_MODE": "live", "AI_PROVIDER": "openai",
             "ENABLED_PUBLISHERS": "dryrun, devto , bluesky"},
        load_dotenv_file=False,
    )
    assert s.publish_mode == "live"
    assert s.ai_provider == "openai"
    assert s.enabled_publishers == ["dryrun", "devto", "bluesky"]


def test_code_default_banned_phrases_match_config_toml():
    """The no-TOML fallback must enforce the same gate as the shipped config."""
    cfg_path = _ROOT / "config" / "config.toml"
    toml_phrases = tomllib.loads(cfg_path.read_text(encoding="utf-8"))["quality"]["banned_phrases"]
    assert sorted(p.lower() for p in DEFAULT_BANNED_PHRASES) == \
        sorted(p.lower() for p in toml_phrases)


def test_settings_loads_banned_phrases_lowercased():
    s = load_settings(env={}, load_dotenv_file=False)
    assert all(p == p.lower() for p in s.quality.banned_phrases)
    assert "game changer" in s.quality.banned_phrases


def test_engagement_defaults_load():
    s = load_settings(env={}, load_dotenv_file=False)
    assert s.engagement.enabled is True
    assert s.engagement.min_attention_score == 6.5
    assert s.engagement.min_voice_score == 6.5
    assert s.engagement.block_on_high_severity is True


def test_max_revisions_default_is_three():
    # "rewrite-until-pass" cap raised from 1 -> 3.
    s = load_settings(env={}, load_dotenv_file=False)
    assert s.review.max_revisions == 3


def test_max_repo_age_days_default_off():
    s = load_settings(env={}, load_dotenv_file=False)
    assert s.ranking.max_repo_age_days == 0


def test_max_repo_age_days_env_override():
    # Production enables the recency filter via the env var.
    s = load_settings(env={"MAX_REPO_AGE_DAYS": "60"}, load_dotenv_file=False)
    assert s.ranking.max_repo_age_days == 60
