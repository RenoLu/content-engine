"""Command-line interface.

    content-engine run [--date YYYY-MM-DD] [--mode dry_run|live] [--force]
    content-engine palisade [--date YYYY-MM-DD] [--mode dry_run|live] [--force]
    content-engine init-db
    content-engine list [--limit N]
    content-engine show <date>
    content-engine publishers
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .config import load_settings
from .logging_setup import get_logger, setup_logging
from .models import today_str
from .pipeline import Pipeline
from .publishers import AVAILABLE_PUBLISHERS
from .storage import Store

log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-engine", description="Automated AI content engine.")
    p.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the daily pipeline")
    run.add_argument("--date", default=None, help="run date YYYY-MM-DD (default: today UTC)")
    run.add_argument("--mode", choices=["dry_run", "live"], default=None,
                     help="override PUBLISH_MODE")
    run.add_argument("--dry-run", action="store_true",
                     help="force dry-run regardless of PUBLISH_MODE")
    run.add_argument("--force", action="store_true",
                     help="re-run even if a terminal run exists for the date")

    pal = sub.add_parser("palisade", help="syndicate the next agentpalisade.com guide to DEV.to")
    pal.add_argument("--date", default=None, help="run date YYYY-MM-DD (default: today UTC)")
    pal.add_argument("--mode", choices=["dry_run", "live"], default=None,
                     help="override PUBLISH_MODE")
    pal.add_argument("--dry-run", action="store_true",
                     help="force dry-run regardless of PUBLISH_MODE")
    pal.add_argument("--force", action="store_true",
                     help="re-run even if a terminal run exists for the date")

    sub.add_parser("init-db", help="create the SQLite schema and exit")

    ls = sub.add_parser("list", help="list recent runs")
    ls.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="show a single run's details")
    show.add_argument("date", help="run date YYYY-MM-DD")

    sub.add_parser("publishers", help="list available publishers and whether they are configured")
    return p


def _cmd_run(args) -> int:
    overrides = {}
    if args.dry_run:
        overrides["PUBLISH_MODE"] = "dry_run"
    elif args.mode:
        overrides["PUBLISH_MODE"] = args.mode
    settings = load_settings()
    if overrides:
        settings = dataclasses.replace(settings, publish_mode=overrides["PUBLISH_MODE"])

    log.info("provider=%s mode=%s publishers=%s",
             settings.ai_provider, settings.publish_mode, ",".join(settings.enabled_publishers))
    pipeline = Pipeline(settings)
    summary = pipeline.run(args.date or today_str(), force=args.force)

    print(_format_summary(summary))
    # Exit non-zero on hard failure so schedulers can alert.
    return 1 if summary.status == "failed" else 0


def _format_summary(summary) -> str:
    lines = [
        "",
        "================ RUN SUMMARY ================",
        f" date     : {summary.run_date}",
        f" status   : {summary.status}",
        f" mode     : {summary.mode}",
        f" repo     : {summary.repo or '-'}",
        f" score    : {summary.score if summary.score is not None else '-'}",
        f" review   : {summary.review_score if summary.review_score is not None else '-'}",
        f" approved : {summary.approved}",
        f" message  : {summary.message}",
    ]
    if summary.publish_results:
        lines.append(" publishers:")
        for r in summary.publish_results:
            tgt = r.url or r.error or ""
            lines.append(f"   - {r.publisher:<10} {r.status:<10} {tgt}")
    lines.append("=============================================")
    return "\n".join(lines)


def _cmd_palisade(args) -> int:
    from .campaigns import PalisadeCampaign

    overrides = {}
    if args.dry_run:
        overrides["PUBLISH_MODE"] = "dry_run"
    elif args.mode:
        overrides["PUBLISH_MODE"] = args.mode
    settings = load_settings()
    if overrides:
        settings = dataclasses.replace(settings, publish_mode=overrides["PUBLISH_MODE"])

    log.info("palisade: provider=%s mode=%s publishers=%s",
             settings.ai_provider, settings.publish_mode, ",".join(settings.enabled_publishers))
    summary = PalisadeCampaign(settings).run(args.date or today_str(), force=args.force)

    lines = [
        "",
        "============= PALISADE SUMMARY ==============",
        f" date     : {summary.run_date}",
        f" status   : {summary.status}",
        f" mode     : {summary.mode}",
        f" guide    : {summary.guide or '-'}",
        f" message  : {summary.message}",
    ]
    if summary.publish_results:
        lines.append(" publishers:")
        for r in summary.publish_results:
            tgt = r.url or r.error or ""
            lines.append(f"   - {r.publisher:<10} {r.status:<10} {tgt}")
    lines.append("=============================================")
    print("\n".join(lines))
    return 1 if summary.status == "failed" else 0


def _cmd_init_db(args) -> int:
    settings = load_settings()
    Store(settings.db_path).close()
    print(f"Initialized database at {settings.db_path}")
    return 0


def _cmd_list(args) -> int:
    settings = load_settings()
    store = Store(settings.db_path)
    runs = store.list_runs(args.limit)
    if not runs:
        print("No runs yet.")
        return 0
    print(f"{'date':<12} {'status':<12} {'mode':<8} repo")
    for r in runs:
        print(f"{r['run_date']:<12} {r['status']:<12} {r['mode']:<8} {r['repo_full_name'] or '-'}")
    store.close()
    return 0


def _cmd_show(args) -> int:
    settings = load_settings()
    store = Store(settings.db_path)
    run = store.get_run(args.date)
    if not run:
        print(f"No run for {args.date}")
        return 1
    out = dict(run)
    for col in ("repo_json", "draft_json", "review_json", "final_json"):
        if out.get(col):
            try:
                out[col] = json.loads(out[col])
            except (json.JSONDecodeError, TypeError):
                pass
    out["publish_results"] = store.get_publish_results(args.date)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    store.close()
    return 0


def _cmd_publishers(args) -> int:
    settings = load_settings()
    print(f"{'publisher':<12} {'enabled':<9} configured")
    for name, cls in AVAILABLE_PUBLISHERS.items():
        enabled = "yes" if name in settings.enabled_publishers else "no"
        try:
            configured = "yes" if cls(settings).is_configured() else "no"
        except Exception:  # noqa: BLE001
            configured = "error"
        print(f"{name:<12} {enabled:<9} {configured}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(args.log_level)
    handlers = {
        "run": _cmd_run,
        "palisade": _cmd_palisade,
        "init-db": _cmd_init_db,
        "list": _cmd_list,
        "show": _cmd_show,
        "publishers": _cmd_publishers,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
