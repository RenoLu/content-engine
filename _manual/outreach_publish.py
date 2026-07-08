"""Phase 3 of the agent-as-model outreach flow: replay the targets dumped by
``outreach_discover.py`` through the REAL engine funnel, using the replies the
agent authored in ``_manual/outreach_replies.json`` instead of an API model.

The engine's dedupe, daily caps, quality gate, pacing, and live adapters all run
unchanged -- only the reply *text* comes from the agent (via ManualReplyClient).
Likes and follows need no model and happen as they normally would; a target with
no authored reply still gets liked/followed, its reply just skipped.

Honors OUTREACH_MODE (dry_run default; set live to actually post) and the kill
switch. reply_ratio is forced to 1.0 here because the agent has already decided,
per target, which posts are worth a hand-written reply.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from collections import defaultdict
from pathlib import Path

from content_engine.config import load_settings
from content_engine.outreach.config import load_outreach_config
from content_engine.outreach.engine import OutreachEngine
from content_engine.outreach.manual_model import ManualReplyClient
from content_engine.outreach.models import ActionResult, ActionType, Target
from content_engine.outreach.registry import build_adapter
from content_engine.outreach.store import OutreachStore

HERE = Path(__file__).parent
_TARGET_FIELDS = {f.name for f in dataclasses.fields(Target)}


def _load(name: str):
    path = HERE / name
    if not path.exists():
        raise SystemExit(f"missing {path} -- run outreach_discover.py and author replies first")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    settings = load_settings()
    config = load_outreach_config(settings)
    # the agent already hand-picked which posts deserve a reply, so don't let the
    # sampling ratio drop them.
    config = dataclasses.replace(config, reply_ratio=1.0)

    targets_raw = _load("outreach_targets.json")
    replies_by_key = _load("outreach_replies.json")  # {target_key: reply text}

    # map post text -> reply so ManualReplyClient can match on the commenter prompt
    replies_by_text = {
        t["text"]: replies_by_key[t["key"]]
        for t in targets_raw if t.get("key") in replies_by_key
    }
    model = ManualReplyClient(replies_by_text)

    if not config.enabled:
        print("outreach disabled (kill switch) -- nothing to do", file=sys.stderr)
        return 0

    store = OutreachStore(settings.project_root / "data" / "outreach.sqlite3")
    engine = OutreachEngine(config, store, model_client=model)

    print(f"mode={config.mode} approval={config.approval} "
          f"authored_replies={len(replies_by_text)}/{len(targets_raw)} targets", file=sys.stderr)

    by_platform: dict[str, list[dict]] = defaultdict(list)
    for t in targets_raw:
        by_platform[t["platform"]].append(t)

    summary: dict = {"mode": config.mode, "platforms": {}}
    for platform, rows in by_platform.items():
        try:
            adapter = build_adapter(platform, settings, config)
        except KeyError:
            continue
        results: list[ActionResult] = []
        try:
            for row in rows:
                if all(engine._remaining(platform, at) <= 0 for at in ActionType):
                    break
                target = Target(**{k: v for k, v in row.items() if k in _TARGET_FIELDS})
                engine._engage_target(adapter, target, results)
        finally:
            adapter.close()
        summary["platforms"][platform] = engine._platform_summary(results, len(rows))

    store.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
