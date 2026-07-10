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
    "alexmercedcoder",
    "hexisteme",
    "vbc_risk_analytics",
    "wondadav",
    "george_k_09db0948571db2c",
]

# agent-authored comments keyed by the EXACT article title (== Target.text that
# discovery returns). A title with no entry gets liked/followed but not commented.
REPLIES_BY_TITLE = {
    "Apache Data Lakehouse Weekly: July 1 to July 8, 2026":
        "The table-statistics fight is the interesting one. Once two engines "
        "write stats against the same snapshot, last-writer-wins quietly wrecks "
        "join plans and compute cost. Curious whether Iceberg scopes stats per "
        "engine or just settles on a convention.",

    "\"A Fair Coin Isn't Enough: When a Perfectly Randomized Experiment Is "
    "Impossible to Analyze\"":
        "The missing join is the one that bites in practice. If you don't "
        "persist the assignment id with the unit and its outcome at flip time, "
        "you can prove the coin fair and still have nothing to analyze. The "
        "re-draw guard is the part most A/B setups quietly skip too.",

    "Designing an API-First Value-Based Care Analytics Stack for MA Payers":
        "Pinning the model version on the scoring call is the detail that saves "
        "you at audit time, since a V28 vs V24 swap silently reprices everyone. "
        "How do you handle retro dx deletes, where a dropped diagnosis should "
        "lower a RAF you already reported?",

    "How do I answer \"what did my data look like last month\" in Postgres?":
        "SCD Type 4 with a trigger-fed history table is a solid default, but the "
        "trigger becomes the single point of failure: a bulk update that "
        "bypasses it or a schema change on the main table leaves gaps you only "
        "notice at query time. Do you reconcile history against main on a cadence?",

    "You Don't Need to Scrape BORME: Spain's Company Registry Has an Open-Data API":
        "The consumed-chars over total-chars metric is a smart coverage check; "
        "most parsers never know what they silently dropped. The "
        "200-with-error-body case is the nastiest, since status-only caching "
        "poisons your store. Deterministic parse over a closed vocab is the right call.",
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

    summary = runner.run()
    store.close()

    import json
    out = json.dumps(summary, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(("\n" + out + "\n").encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
