from content_engine.models import PublishResult, RunStatus
from content_engine.storage import Store


def _store(settings):
    return Store(settings.db_path)


def test_create_run_is_idempotent(settings):
    store = _store(settings)
    r1 = store.create_run("2026-05-31", "dry_run")
    r2 = store.create_run("2026-05-31", "dry_run")  # second call must not duplicate
    assert r1["id"] == r2["id"]
    assert len(store.list_runs()) == 1


def test_repo_history_dedup(settings):
    store = _store(settings)
    assert store.has_used_repo("acme/widget") is False
    store.mark_repo_used("acme/widget", "2026-05-31", "published")
    assert store.has_used_repo("acme/widget") is True
    assert "acme/widget" in store.used_repo_names()


def test_already_published_guard(settings):
    store = _store(settings)
    assert store.already_published("2026-05-31", "devto") is False
    store.record_publish_result(
        "2026-05-31", PublishResult(publisher="devto", status="published",
                                    url="https://x", dry_run=False)
    )
    assert store.already_published("2026-05-31", "devto") is True


def test_prior_dry_run_does_not_block_live(settings):
    # A dry-run row must NOT count as "already published", or dry-running a date
    # would permanently block a later live post for that publisher.
    store = _store(settings)
    store.record_publish_result(
        "2026-05-31", PublishResult(publisher="devto", status="dry_run", dry_run=True)
    )
    assert store.already_published("2026-05-31", "devto") is False


def test_publish_result_upsert(settings):
    store = _store(settings)
    store.record_publish_result(
        "2026-05-31", PublishResult(publisher="devto", status="failed", error="x", dry_run=False)
    )
    store.record_publish_result(
        "2026-05-31", PublishResult(publisher="devto", status="published",
                                    url="https://x", dry_run=False)
    )
    results = store.get_publish_results("2026-05-31")
    assert len(results) == 1  # unique(run_date, publisher) -> upsert, not duplicate
    assert results[0]["status"] == "published"


def test_set_status_and_get(settings):
    store = _store(settings)
    store.create_run("2026-05-31", "dry_run")
    store.set_status("2026-05-31", RunStatus.DRY_RUN)
    assert store.get_run("2026-05-31")["status"] == "dry_run"
