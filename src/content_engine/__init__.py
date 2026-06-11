"""Content Engine — automated AI content pipeline.

Finds trending GitHub repositories, writes technical posts about them, reviews
the output with a second AI agent, and publishes via official platform APIs.

The pipeline is intentionally provider- and platform-agnostic:
  * AI providers are hidden behind ``agents.model_client.ModelClient``
  * trend sources are hidden behind ``sources.base.Source``
  * publishers are hidden behind ``publishers.base.Publisher``

so new models, sources, and destinations can be added without touching the
orchestration logic in ``pipeline.py``.
"""

__version__ = "0.1.0"
