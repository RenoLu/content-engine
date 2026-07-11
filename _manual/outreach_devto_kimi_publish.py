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
    "matt_frank_usa",
    "reza_brianca",
    "roni_das_b1b76c5ee6583027",
    "datadriven",
    "ragekill3377",
]

# agent-authored comments keyed by the EXACT article title (== Target.text that
# discovery returns). A title with no entry gets liked/followed but not commented.
REPLIES_BY_TITLE = {
    "Feature Stores: Managing ML Features at Scale":
        "The part that earns a feature store is online/offline consistency. "
        "Without one transformation path you train on features you never serve, "
        "and the skew shows up as accuracy loss. Point-in-time correctness on "
        "the offline join is the piece most teams underestimate until a "
        "backfill lies to them.",

    "Feature Stores: The Secret Sauce for Real-Time ML (and Sanity) in Production":
        "The real-time angle is where cost sneaks in. Streaming features feel "
        "free until you price the always-on materialization and the state you "
        "keep warm for low-latency reads. Deciding which features truly need "
        "sub-second freshness versus a few minutes old is the call that keeps "
        "the bill sane.",

    "How Delta Lake Brings ACID to a Data Lake":
        "Worth stressing the guarantee lives in the transaction log, not the "
        "parquet. The files are just data, the ordered log is the source of "
        "truth, so a half-written file never corrupts a read. Concurrent "
        "writers still hit optimistic-concurrency conflicts though, ACID here "
        "is not serializable for free.",

    "Why Snowflake Separates Storage From Compute":
        "Separating them lets read concurrency scale without touching storage, "
        "and you stop paying for idle compute. The catch is cold local cache "
        "after a warehouse resumes, and a cost model that rewards short bursty "
        "queries over long full scans. Sizing per workload matters more than "
        "expected.",

    "3 Staff Engineers Couldn't Pass This Single DE Job Posting":
        "Usually this is a team's full skill set crammed into one req: batch, "
        "streaming, modeling, orchestration, and cloud at senior depth. Nobody "
        "does all of them daily, so even strong engineers miss on breadth, not "
        "ability. Postings that filter well test depth in one area and "
        "reasoning in the rest.",

    "Really fast columnar analytics engine":
        "The wins in columnar come from vectorized execution and late "
        "materialization, plus encodings the CPU can scan without fully "
        "decoding. Curious where this sits, a from-scratch engine or a layer "
        "over a kernel like Arrow or DuckDB. What matters is selective scans, "
        "not full aggregates.",
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
