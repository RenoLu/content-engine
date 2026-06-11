"""Tests for the palisade syndication campaign."""

from __future__ import annotations

import json

import pytest

from content_engine.agents.model_client import ModelClient
from content_engine.campaigns import Guide, PalisadeCampaign, load_guides
from content_engine.campaigns.palisade import CTA_FOOTER, palisade_db_path
from content_engine.config import load_settings
from content_engine.models import RunStatus
from content_engine.storage import Store

GOOD_BODY = (
    "## Why audit your workflows first\n\n" + ("Practical detail. " * 120)
    + "\n\n## How to run the audit\n\n" + ("More practical detail. " * 120)
    + "\n\n## Takeaway\n\nStart with one workflow."
)


class FakeWriterModel(ModelClient):
    name = "fake"
    model = "fake-1"

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {
            "title": "Run an AI Workflow Audit Before You Buy Tools",
            "summary": "Map workflows, score opportunities, and build a roadmap first.",
            "tags": ["ai", "productivity"],
            "body_markdown": GOOD_BODY,
        }
        self.calls = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        self.calls += 1
        return json.dumps(self.payload)


def make_guide(slug="ai-workflow-audit-checklist", **overrides) -> Guide:
    base = dict(
        slug=slug,
        title="AI Workflow Audit Checklist",
        summary="A step-by-step checklist for SMBs.",
        url=f"https://www.agentpalisade.com/resources/{slug}",
        read_time=6,
        key_points=["Map the current state", "Build the roadmap"],
        tags=["ai", "productivity", "automation", "business"],
    )
    base.update(overrides)
    return Guide(**base)


@pytest.fixture
def campaign_settings(tmp_path):
    env = {
        "PUBLISH_MODE": "dry_run",
        "AI_PROVIDER": "mock",
        "ENABLED_PUBLISHERS": "dryrun,devto",
        "DB_PATH": str(tmp_path / "main.sqlite3"),
        "PALISADE_DB_PATH": str(tmp_path / "palisade.sqlite3"),
        "OUTPUT_DIR": str(tmp_path / "out"),
        "GITHUB_TOKEN": "",
    }
    return load_settings(env=env, load_dotenv_file=False)


def make_campaign(settings, guides=None, model=None):
    return PalisadeCampaign(
        settings,
        guides=guides if guides is not None else [make_guide(), make_guide("smb-ai-readiness-checklist")],
        model=model or FakeWriterModel(),
    )


def test_load_guides_preserves_order_and_fields():
    guides = load_guides()  # the checked-in production queue
    assert len(guides) == 11
    # Implementation guides lead the queue; security checklists follow.
    assert guides[0].slug == "ai-workflow-audit-checklist"
    assert all(g.url.startswith("https://www.agentpalisade.com/resources/") for g in guides)
    security = [g for g in guides if "security" in g.tags]
    assert len(security) == 6
    assert guides.index(security[0]) >= 5  # all 5 implementation guides come first


def test_dry_run_payload_has_canonical_and_cta(campaign_settings):
    campaign = make_campaign(campaign_settings)
    summary = campaign.run("2026-06-11")

    assert summary.status == RunStatus.DRY_RUN.value
    assert summary.guide == "ai-workflow-audit-checklist"
    devto = next(r for r in summary.publish_results if r.publisher == "devto")
    assert devto.status == "dry_run" and devto.payload_preview
    # The display preview is truncated, so assert the real contract on the
    # full rendered payload instead.
    pub = next(p for p in campaign.publishers if p.name == "devto")
    article = pub.render_payload(campaign._to_post(
        campaign._write(make_guide()), make_guide()))["article"]
    assert article["canonical_url"] == "https://www.agentpalisade.com/resources/ai-workflow-audit-checklist"
    assert "book-call" in article["body_markdown"]
    assert article["published"] is False
    assert article["tags"]


def test_guides_are_never_reused(campaign_settings):
    campaign = make_campaign(campaign_settings)
    first = campaign.run("2026-06-11")
    second = campaign.run("2026-06-18")
    assert first.guide == "ai-workflow-audit-checklist"
    assert second.guide == "smb-ai-readiness-checklist"

    third = campaign.run("2026-06-25")
    assert third.status == RunStatus.NO_CANDIDATE.value


def test_same_date_is_idempotent(campaign_settings):
    campaign = make_campaign(campaign_settings)
    campaign.run("2026-06-11")
    again = campaign.run("2026-06-11")
    assert again.status == RunStatus.SKIPPED.value


def test_force_rerun_repicks_same_guide(campaign_settings):
    """Dry-run preview followed by --force must not advance the queue."""
    campaign = make_campaign(campaign_settings)
    first = campaign.run("2026-06-11")
    forced = campaign.run("2026-06-11", force=True)
    assert first.guide == forced.guide == "ai-workflow-audit-checklist"


def test_quality_gate_blocks_bad_draft_in_live_mode(campaign_settings, tmp_path):
    import dataclasses

    live = dataclasses.replace(campaign_settings, publish_mode="live")
    bad_model = FakeWriterModel({
        "title": "x",
        "summary": "s",
        "tags": [],
        "body_markdown": "too short",  # fails min headings + word floor
    })
    campaign = make_campaign(live, model=bad_model)
    summary = campaign.run("2026-06-11")
    assert summary.status == RunStatus.REJECTED.value
    # A rejected guide is marked used with status=rejected (no retry loop forever);
    # the queue moves on next run.
    assert "guide:ai-workflow-audit-checklist" in campaign.store.used_repo_names()


def test_cta_footer_avoids_banned_phrases(campaign_settings):
    banned = campaign_settings.quality.banned_phrases
    footer = CTA_FOOTER.lower()
    assert not [p for p in banned if p in footer]


def test_palisade_db_is_separate_from_main(campaign_settings):
    assert palisade_db_path(campaign_settings) != campaign_settings.db_path
    campaign = make_campaign(campaign_settings)
    campaign.run("2026-06-11")
    # main pipeline DB untouched
    main_store = Store(campaign_settings.db_path)
    assert main_store.get_run("2026-06-11") is None
    main_store.close()
