"""LIVE DEV.to engagement via Kimi, agent-as-model comments.

Mirrors _manual/outreach_publish.py's wiring but for the browser-driven DEV.to
runner (DevtoKimiRunner). Discovery uses the free DEV.to JSON API; like/comment/
follow go through the user's logged-in browser via Kimi. Comment text is authored
by the agent (no API model) and replayed through ManualReplyClient, so the real
quality gate, caps, dedupe, pacing, and store all run unchanged.

Targets are restricted to a hand-vetted on-brand set (skips clickbait/promo);
the runner still applies every safety guard. Honors OUTREACH_MODE (set live to
actually post).
"""
from __future__ import annotations

import dataclasses
import sys

from content_engine.config import load_settings
from content_engine.outreach.commenter import Commenter
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.devto import DevtoAdapter
from content_engine.outreach.devto_kimi import DevtoKimiRunner
from content_engine.outreach.manual_model import ManualReplyClient
from content_engine.outreach.store import OutreachStore

# on-brand articles, keyed by DEV.to author handle
WANTED_HANDLES = [
    "labyrinthanalytics",
    "truenorthdata",
    "vaibhav7387",
]

# agent-authored comments keyed by the EXACT article title (== Target.text that
# discovery returns). A title with no entry gets liked/followed but not commented.
REPLIES_BY_TITLE = {
    "LangGraph vs LangChain in 2026: When Each Wins":
        "The split I keep hitting: LangChain is fine until a step needs to "
        "retry, branch, or resume after a failure, and then you are hand "
        "rolling state anyway. LangGraph makes that state explicit, which "
        "costs more upfront. If the flow is a straight line, the extra "
        "structure is dead weight.",

    "How I turned Canada's messy open government data into 3 clean data products":
        "Turning open government data into something usable is mostly a "
        "maintenance bet. The scrape is a weekend; what kills these projects "
        "is a province quietly renaming a column two months later. Curious "
        "whether you added schema checks that fail loudly before the product "
        "goes stale.",

    "Snowflake or Databricks? An Honest Comparison (From 200+ Migrations)":
        "200 migrations is the useful part here. Most comparisons benchmark on "
        "clean data nobody actually has. The pattern I see is the decision "
        "gets made on compute pricing, then the real cost lands in governance "
        "and how much existing Spark or dbt code has to be rewritten.",
}


def main() -> int:
    settings = load_settings()
    config = load_outreach_config(settings)

    if not config.enabled:
        print("outreach disabled (kill switch) -- nothing to do", file=sys.stderr)
        return 0

    # sanity: every authored comment must clear the 300-char gate.
    for title, body in REPLIES_BY_TITLE.items():
        assert 15 <= len(body) <= 300, f"comment out of bounds ({len(body)}): {title}"

    model = ManualReplyClient(REPLIES_BY_TITLE)
    commenter = Commenter(model, config)
    store = OutreachStore(settings.project_root / "data" / "outreach.sqlite3")

    # discover via the API, then restrict to the vetted on-brand set (one per
    # author, first match wins) in WANTED_HANDLES order.
    adapter = DevtoAdapter(config.settings, config)
    all_targets = adapter.discover(config.queries, config.per_query_limit)
    by_handle: dict[str, object] = {}
    for t in all_targets:
        if t.author_handle in WANTED_HANDLES and t.author_handle not in by_handle:
            by_handle[t.author_handle] = t
    targets = [by_handle[h] for h in WANTED_HANDLES if h in by_handle]

    runner = DevtoKimiRunner(config, store, commenter, adapter=adapter)
    runner.discover = lambda: targets  # engage only the vetted set

    print(f"mode={config.mode} live={config.is_live} targets={len(targets)} "
          f"authored={len(REPLIES_BY_TITLE)}", file=sys.stderr)
    for t in targets:
        matched = t.text in REPLIES_BY_TITLE
        line = f"  - [{t.author_handle}] comment={'yes' if matched else 'NO'} :: {t.text}\n"
        sys.stdout.buffer.write(line.encode("utf-8", "replace"))

    # Warm the Kimi session so a tab is bound before run() calls healthy();
    # healthy() 502s when the session has no bound tab yet (known quirk).
    if config.is_live:
        runner.kimi.navigate("https://dev.to", new_tab=False)

    summary = runner.run()
    store.close()

    import json
    out = json.dumps(summary, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(("\n" + out + "\n").encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
