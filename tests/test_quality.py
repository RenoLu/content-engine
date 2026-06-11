from content_engine.models import Draft
from content_engine.quality import run_quality_checks

from .conftest import make_repo

_GOOD_BODY = (
    "## What widget does\n\n" + ("It is a tool. " * 60) +
    "\n\n## Why it matters\n\n" + ("Engineers care. " * 60)
)


def _draft(**kw) -> Draft:
    base = dict(
        title="Widget: a practical look",
        body_markdown=_GOOD_BODY,
        summary="A grounded take on widget.",
        tags=["rust"],
        angle="practical",
    )
    base.update(kw)
    return Draft(**base)


def test_good_draft_passes(settings):
    report = run_quality_checks(_draft(), make_repo(), settings)
    assert report.passed, report.blocking


def test_banned_phrase_blocks(settings):
    body = _GOOD_BODY + "\n\nThis is a game changer for widget."
    report = run_quality_checks(_draft(body_markdown=body), make_repo(), settings)
    assert not report.passed
    assert any(b.startswith("banned_phrases") for b in report.blocking)


def test_missing_repo_name_blocks(settings):
    body = "## A\n\n" + ("generic text. " * 60) + "\n\n## B\n\n" + ("more. " * 60)
    report = run_quality_checks(_draft(body_markdown=body), make_repo(), settings)
    assert "repo_name_not_in_body" in report.blocking


def test_too_few_headings_blocks(settings):
    body = "Just one paragraph about widget. " * 80
    report = run_quality_checks(_draft(body_markdown=body), make_repo(), settings)
    assert any(b.startswith("too_few_headings") for b in report.blocking)


def test_short_body_blocks(settings):
    report = run_quality_checks(
        _draft(body_markdown="## a\n\n## b\n\nwidget short"), make_repo(), settings
    )
    assert any(b.startswith("body_too_short") for b in report.blocking)


def test_placeholder_blocks(settings):
    body = _GOOD_BODY + "\n\nTODO: finish this section about widget."
    report = run_quality_checks(_draft(body_markdown=body), make_repo(), settings)
    assert any(b.startswith("placeholder_text") for b in report.blocking)


def test_missing_title_blocks(settings):
    report = run_quality_checks(_draft(title=""), make_repo(), settings)
    assert "missing_title" in report.blocking


def test_none_summary_does_not_crash(settings):
    # A None summary must be reported as missing, never raise AttributeError.
    report = run_quality_checks(_draft(summary=None), make_repo(), settings)
    assert "missing_summary" in report.blocking
