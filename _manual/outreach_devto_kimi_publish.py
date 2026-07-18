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
    "b0bai",
    "tmfrisinger",
    "aijasonz",
]

# agent-authored comments keyed by the EXACT article title (== Target.text that
# discovery returns). A title with no entry gets liked/followed but not commented.
REPLIES_BY_TITLE = {
    "Why AI Agent PRs Get Rejected And How Repo Contracts Help":
        "Repo contracts help because they move the argument left: the agent "
        "fails against an explicit rule instead of a reviewer's taste. The "
        "ones that pay off are the boring mechanical rules, layering, "
        "dependency direction, where tests live. Taste still needs a human, "
        "just not as the first line of defense.",

    "The Bar for TDD Just Moved":
        "The bar moved because writing the test stopped being the expensive "
        "part. Deciding what should be true still is, and an agent will "
        "happily generate tests that pin down the behavior you already have "
        "rather than the behavior you wanted. Red-green only means something "
        "if you chose the red.",

    "Agents Pattern-Match Your Test Smells":
        "This matches what I see. An agent reproduces whatever the existing "
        "suite implies is normal, so point one at a codebase where tests "
        "assert on mocks and you get more mock assertions. The suite is "
        "effectively the spec, and its smells now propagate faster than a "
        "person could spread them.",

    "Crabbox: Cloud Sandboxes for Parallel Coding Agents":
        "Running agents in parallel makes isolation the bottleneck rather "
        "than tokens. Once several of them touch one repo you are really "
        "running concurrent branches with no merge policy, so the sandbox "
        "ends up having to answer what happens when two agents are both "
        "right about different things.",
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
