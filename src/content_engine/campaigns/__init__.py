"""Marketing campaigns: slim orchestrators that reuse the engine's model
clients, quality gate, publishers, and store, but run off their own content
queues instead of the daily GitHub-trending source."""

from .palisade import Guide, PalisadeCampaign, load_guides

__all__ = ["Guide", "PalisadeCampaign", "load_guides"]
