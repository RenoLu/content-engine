"""Outreach CLI: ``python -m content_engine.outreach``.

Loads settings + outreach config, runs the engine, and prints a JSON summary.
Dry-run by default. The engine itself enforces every guardrail; this is just the
entry point.
"""

from __future__ import annotations

import json
import sys

from ..config import load_settings
from ..logging_setup import get_logger
from .config import load_outreach_config
from .engine import OutreachEngine
from .store import OutreachStore

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    config = load_outreach_config(settings)

    log.info(
        "outreach: mode=%s approval=%s platforms=%s enabled=%s",
        config.mode, config.approval, ",".join(config.platforms), config.enabled,
    )

    db_path = settings.project_root / "data" / "outreach.sqlite3"
    with OutreachStore(db_path) as store:
        engine = OutreachEngine(config, store)
        summary = engine.run()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    # a live run that executed nothing is worth flagging but not an error
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
