"""Phase 1 of the agent-as-model outreach flow: discover reply-eligible targets
and dump them to ``_manual/outreach_targets.json`` for the agent to author
replies against. Nothing is liked/followed/replied here and the store is only
READ (for dedupe) -- all real actions happen in ``outreach_publish.py``.

Flow (see _manual/RUNBOOK.md):
  1. python _manual/outreach_discover.py         # writes outreach_targets.json
  2. agent writes _manual/outreach_replies.json  # {target_key: "reply text"}
  3. OUTREACH_MODE=live python _manual/outreach_publish.py

Only targets that (a) have post text and (b) have not already been replied to are
dumped, capped per platform at the configured reply cap so the agent never writes
more replies than can actually be posted.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from content_engine.config import load_settings
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.registry import build_adapter
from content_engine.outreach.store import OutreachStore

HERE = Path(__file__).parent


def main() -> int:
    settings = load_settings()
    config = load_outreach_config(settings)
    store = OutreachStore(settings.project_root / "data" / "outreach.sqlite3")

    dumped: list[dict] = []
    for platform in config.platforms:
        try:
            adapter = build_adapter(platform, settings, config)
        except KeyError:
            continue
        reply_cap = config.caps_for(platform).reply
        if reply_cap <= 0:
            adapter.close()
            continue
        try:
            targets = adapter.discover(config.queries, config.per_query_limit)
        except Exception as exc:  # noqa: BLE001 - one platform must not abort the rest
            print(f"discover {platform} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            adapter.close()
            continue
        finally:
            pass
        kept = 0
        for t in targets:
            if kept >= reply_cap:
                break
            if not t.text.strip():
                continue
            if store.already_done(platform, t.key, "reply"):
                continue
            dumped.append(dataclasses.asdict(t))
            kept += 1
        adapter.close()
        print(f"{platform}: dumped {kept} reply-eligible target(s)", file=sys.stderr)

    out = HERE / "outreach_targets.json"
    out.write_text(json.dumps(dumped, ensure_ascii=False, indent=2), encoding="utf-8")
    store.close()

    print(f"\nwrote {out} ({len(dumped)} targets)")
    print("\n=== POSTS TO REPLY TO (author _manual/outreach_replies.json) ===")
    for t in dumped:
        print(f"\n[{t['platform']}] key={t['key']}  author={t['author_handle']}")
        print(f"  {t['text'][:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
