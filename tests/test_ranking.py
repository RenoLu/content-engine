from content_engine.ranking import RepoRanker, hard_filter_reason, score_repo
from content_engine.ranking.scorer import readme_filter_reason

from .conftest import make_repo


def test_hard_filter_passes_good_repo(settings):
    assert hard_filter_reason(make_repo(), settings) is None


def test_hard_filter_archived(settings):
    assert hard_filter_reason(make_repo(is_archived=True), settings) == "archived"


def test_hard_filter_fork(settings):
    assert hard_filter_reason(make_repo(is_fork=True, stars=300), settings) == "fork"


def test_hard_filter_popular_fork_allowed(settings):
    # A fork above allow_forks_min_stars is allowed through.
    assert hard_filter_reason(make_repo(is_fork=True, stars=25000), settings) is None


def test_hard_filter_below_min_stars(settings):
    reason = hard_filter_reason(make_repo(stars=5), settings)
    assert reason is not None and reason.startswith("below_min_stars")


def test_hard_filter_no_description(settings):
    assert hard_filter_reason(make_repo(description=""), settings) == "no_description"


def test_hard_filter_already_used(settings):
    reason = hard_filter_reason(make_repo(), settings, used_names={"acme/widget"})
    assert reason == "already_featured"


def test_readme_filter(settings):
    assert readme_filter_reason(make_repo(readme_len=10000), settings) is None
    reason = readme_filter_reason(make_repo(readme_len=10), settings)
    assert reason is not None and reason.startswith("thin_readme")


def test_score_rewards_more_stars(settings):
    low = make_repo(stars=200)
    high = make_repo(stars=20000)
    s_low, _ = score_repo(low, settings.scoring, settings.github.preferred_topics)
    s_high, _ = score_repo(high, settings.scoring, settings.github.preferred_topics)
    assert s_high > s_low


def test_score_breakdown_keys(settings):
    _, breakdown = score_repo(make_repo(), settings.scoring, settings.github.preferred_topics)
    assert {"stars", "recent_push", "rising", "topic_match", "readme_quality",
            "has_homepage"} <= set(breakdown)


def test_ranker_orders_eligible_first(settings):
    repos = [
        make_repo(full_name="a/archived", is_archived=True),
        make_repo(full_name="b/good", stars=5000),
        make_repo(full_name="c/better", stars=30000),
    ]
    ranked = RepoRanker(settings).prefilter_and_score(repos)
    # eligible (no skip_reason) come first, highest score first
    assert ranked[0].full_name == "c/better"
    assert ranked[1].full_name == "b/good"
    assert ranked[-1].skip_reason == "archived"
    assert RepoRanker(settings).eligible(ranked) == ranked[:2]
