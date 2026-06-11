"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from content_engine.config import load_settings
from content_engine.models import Repository


@pytest.fixture
def settings(tmp_path):
    """A Settings object wired to temp paths, mock provider, dry-run mode."""
    env = {
        "PUBLISH_MODE": "dry_run",
        "AI_PROVIDER": "mock",
        "ENABLED_PUBLISHERS": "dryrun",
        "DB_PATH": str(tmp_path / "test.sqlite3"),
        "OUTPUT_DIR": str(tmp_path / "out"),
        "GITHUB_TOKEN": "",
    }
    return load_settings(env=env, load_dotenv_file=False)


def make_repo(**overrides) -> Repository:
    """Construct a believable Repository with sensible defaults for tests."""
    base = dict(
        full_name="acme/widget",
        name="widget",
        owner="acme",
        html_url="https://github.com/acme/widget",
        description="A fast widget toolkit for engineers.",
        homepage="https://widget.dev",
        language="Rust",
        stars=1200,
        forks=80,
        watchers=1200,
        open_issues=15,
        topics=["rust", "cli", "developer-tools"],
        license="MIT",
        created_at="2025-01-01T00:00:00Z",
        pushed_at="2026-05-30T00:00:00Z",
        updated_at="2026-05-30T00:00:00Z",
        is_archived=False,
        is_fork=False,
        default_branch="main",
        readme_markdown="# widget\n\n" + ("Detailed docs. " * 200),
        readme_len=3000,
    )
    base.update(overrides)
    return Repository(**base)


@pytest.fixture
def repo():
    return make_repo()
