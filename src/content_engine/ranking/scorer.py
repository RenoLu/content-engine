"""Filtering and scoring of repository candidates.

Filtering happens in two phases so we only pay for README fetches on promising
repos:
  * ``hard_filter_reason`` — metadata-only checks (stars, archived, fork, …)
  * ``readme_filter_reason`` — runs after enrichment (thin-README check)

Scoring is a transparent weighted sum of log-scaled signals; every component is
stored in ``repo.score_breakdown`` for debuggability.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..config import ScoringConfig, Settings
from ..logging_setup import get_logger
from ..models import Repository

log = get_logger(__name__)


# AI-relevance lists for the optional ``require_ai_topic`` hard filter. Exact
# GitHub topic tags are the primary, reliable signal; the keyword fallback
# catches AI repos that simply don't tag themselves.
AI_TOPICS: frozenset[str] = frozenset({
    "llm", "llms", "large-language-models", "ai", "artificial-intelligence",
    "machine-learning", "ml", "deep-learning", "agents", "ai-agents", "agentic",
    "agent", "rag", "retrieval-augmented-generation", "generative-ai", "genai",
    "llmops", "mlops", "nlp", "natural-language-processing", "transformers",
    "transformer", "langchain", "llamaindex", "vector-database", "vector-search",
    "embeddings", "gpt", "chatgpt", "openai", "anthropic", "claude", "llama",
    "diffusion", "stable-diffusion", "computer-vision", "neural-network",
    "neural-networks", "pytorch", "tensorflow", "fine-tuning", "inference",
    "mcp", "model-context-protocol", "chatbot", "chatbots", "prompt-engineering",
    "ai-tools", "copilot", "gemini", "mistral",
})

# Substrings checked against a space-padded "name + description". Kept
# conservative (clear AI phrases) so non-AI repos don't slip through.
AI_KEYWORDS: tuple[str, ...] = (
    " ai ", " ai-", "-ai ", "a.i.", "artificial intelligence", "llm",
    "large language model", "machine learning", "deep learning",
    "neural network", "generative ai", "genai", "langchain", "llamaindex",
    "chatgpt", "openai", " gpt-", "gpt-4", "diffusion model", "stable diffusion",
    "computer vision", "fine-tun", "embeddings", "vector database",
    "vector search", "semantic search", "retrieval-augmented", "agentic",
    "ai agent", "ai-agent", "llm agent", "autonomous agent",
    "model context protocol", " mcp ", "prompt engineering", "chatbot",
)


def _looks_ai(repo: Repository) -> bool:
    """True if the repo is AI-related — by topic tag or an AI keyword in its
    name/description. Used only when ``require_ai_topic`` is enabled."""
    if {t.lower() for t in repo.topics} & AI_TOPICS:
        return True
    hay = f" {(repo.name or '').lower()} {(repo.description or '').lower()} "
    return any(kw in hay for kw in AI_KEYWORDS)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(s: str | None) -> float | None:
    dt = _parse_dt(s)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def hard_filter_reason(repo: Repository, settings: Settings,
                       used_names: set[str] | None = None) -> str | None:
    """Return a skip reason if the repo fails a metadata filter, else None."""
    rk = settings.ranking
    used_names = used_names or set()
    fn_lower = repo.full_name.lower()

    if any(b and b in fn_lower for b in rk.blocklist):
        return "blocklisted"
    if repo.full_name in used_names:
        return "already_featured"
    if rk.skip_archived and repo.is_archived:
        return "archived"
    if rk.skip_forks and repo.is_fork and repo.stars < rk.allow_forks_min_stars:
        return "fork"
    if rk.require_description and not (repo.description or "").strip():
        return "no_description"
    if repo.stars < rk.min_stars:
        return f"below_min_stars(<{rk.min_stars})"
    if settings.github.require_ai_topic and not _looks_ai(repo):
        return "not_ai"
    if rk.max_repo_age_days > 0:
        age = _days_since(repo.created_at)
        if age is not None and age > rk.max_repo_age_days:
            return "too_old"
    return None


def readme_filter_reason(repo: Repository, settings: Settings) -> str | None:
    """Post-enrichment check: skip repos with thin/missing READMEs."""
    if repo.readme_len < settings.ranking.min_readme_chars:
        return f"thin_readme(<{settings.ranking.min_readme_chars})"
    return None


def score_repo(repo: Repository, scoring: ScoringConfig,
               preferred_topics: list[str],
               active_window_days: int = 14,
               trending_limit: int = 25) -> tuple[float, dict[str, float]]:
    """Compute a composite score and a per-component breakdown."""
    breakdown: dict[str, float] = {}

    # Stars (log-scaled so a 50k-star repo doesn't dwarf everything linearly).
    breakdown["stars"] = scoring.weight_stars * math.log10(repo.stars + 1)

    # Recency of last push: 1.0 today -> 0.0 at the edge of the active window.
    pushed_days = _days_since(repo.pushed_at)
    if pushed_days is None:
        recency = 0.0
    else:
        recency = max(0.0, 1.0 - pushed_days / max(active_window_days, 1))
    breakdown["recent_push"] = scoring.weight_recent_push * recency

    # Rising: stars accrued per day since creation, log-scaled & capped.
    created_days = _days_since(repo.created_at)
    if created_days and created_days > 0:
        per_day = repo.stars / created_days
        rising = min(math.log10(per_day + 1) / 2.0, 1.0)
    else:
        rising = 0.0
    breakdown["rising"] = scoring.weight_rising * rising

    # Topic relevance to our audience.
    pref = {t.lower() for t in preferred_topics}
    overlap = len({t.lower() for t in repo.topics} & pref)
    breakdown["topic_match"] = scoring.weight_topic_match * min(overlap / 3.0, 1.0)

    # README substance (0 until enriched).
    if repo.readme_len:
        breakdown["readme_quality"] = scoring.weight_readme_quality * min(
            repo.readme_len / 4000.0, 1.0
        )
    else:
        breakdown["readme_quality"] = 0.0

    breakdown["has_homepage"] = scoring.weight_has_homepage * (1.0 if repo.homepage else 0.0)

    # Trending-page position (only set by the trending_html source): 1.0 at the
    # top of the page, decaying to 0.0 at the bottom. 0 when not from trending.
    if repo.trending_rank is not None and trending_limit > 0:
        rank_signal = max(0.0, 1.0 - repo.trending_rank / trending_limit)
    else:
        rank_signal = 0.0
    breakdown["trending_rank"] = scoring.weight_trending_rank * rank_signal

    total = round(sum(breakdown.values()), 4)
    breakdown = {k: round(v, 4) for k, v in breakdown.items()}
    return total, breakdown


class RepoRanker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def prefilter_and_score(self, repos: list[Repository],
                            used_names: set[str] | None = None) -> list[Repository]:
        """Apply metadata filters + scoring to all candidates.

        Mutates each repo (sets ``skip_reason``, ``score``, ``score_breakdown``)
        and returns the list sorted so that eligible repos (no skip reason)
        come first, highest score first.
        """
        for repo in repos:
            reason = hard_filter_reason(repo, self.settings, used_names)
            repo.skip_reason = reason
            if reason is None:
                repo.score, repo.score_breakdown = score_repo(
                    repo,
                    self.settings.scoring,
                    self.settings.github.preferred_topics,
                    self.settings.github.active_pushed_within_days,
                    self.settings.trending.limit,
                )
            else:
                repo.score, repo.score_breakdown = 0.0, {}

        repos.sort(key=lambda r: (r.skip_reason is None, r.score), reverse=True)
        eligible = sum(1 for r in repos if r.skip_reason is None)
        log.info("prefilter: %d candidates, %d eligible", len(repos), eligible)
        return repos

    def eligible(self, repos: list[Repository]) -> list[Repository]:
        return [r for r in repos if r.skip_reason is None]
