"""Outreach orchestrator.

For each enabled platform: discover targets, then for each target decide which
actions to take and run them through the safety funnel:

    dedupe (store) -> daily cap -> [reply: generate + quality gate] -> act -> log

Caps and dedupe are enforced HERE, before ``adapter.act`` is ever called, and the
adapter's base is a second safety net (dry-run + failure containment). Sampling
and pacing use a seeded RNG so runs are reproducible and tests need no real sleep.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Callable

from ..agents.model_client import build_model_client
from ..logging_setup import get_logger
from .commenter import Commenter, ReplyRejected
from .config import OutreachConfig
from .models import Action, ActionResult, ActionType, Target
from .registry import build_adapter
from .store import OutreachStore

log = get_logger(__name__)


class OutreachEngine:
    def __init__(self, config: OutreachConfig, store: OutreachStore,
                 sleeper: Callable[[float], None] = time.sleep,
                 model_client=None):
        self.config = config
        self.store = store
        self.settings = config.settings
        self._sleeper = sleeper
        self._rng = random.Random(config.seed)
        self._model = model_client or build_model_client(self.settings)
        self.commenter = Commenter(self._model, config)
        # Planned-actions-this-run counter plus a one-time baseline snapshot of
        # what was already executed today. We count planned actions on TOP of the
        # baseline so an execution during this run isn't double-counted (once here
        # and once via the store). This keeps caps correct in dry-run and live.
        self._run_counts: dict[tuple[str, str], int] = defaultdict(int)
        self._baseline: dict[tuple[str, str], int] = {}

    # ---- cap / dedupe helpers -------------------------------------------
    def _remaining(self, platform: str, at: ActionType) -> int:
        cap = self.config.caps_for(platform).for_action(at.value)
        key = (platform, at.value)
        if key not in self._baseline:
            self._baseline[key] = self.store.count_today(platform, at.value)
        return cap - self._baseline[key] - self._run_counts[key]

    def _eligible(self, platform: str, target: Target, at: ActionType) -> bool:
        if self._remaining(platform, at) <= 0:
            return False
        if self.store.already_done(platform, target.key, at.value):
            return False
        return True

    # ---- per-target decision + execution --------------------------------
    def _engage_target(self, adapter, target: Target, results: list[ActionResult]) -> None:
        platform = adapter.name
        did_something = False

        # 1) LIKE — the cheap, low-risk signal; most eligible targets get one.
        if self._eligible(platform, target, ActionType.LIKE) and \
                self._rng.random() < self.config.like_ratio:
            self._run_counts[(platform, "like")] += 1
            res = adapter.act(Action(platform, ActionType.LIKE, target))
            self.store.record(res, author=target.author_handle)
            results.append(res)
            did_something = did_something or res.acted or res.status in ("dry_run", "pending_approval")
            self._pace(res)

        # 2) REPLY — only a fraction, and only if a quality reply can be written.
        if self._eligible(platform, target, ActionType.REPLY) and \
                self._rng.random() < self.config.reply_ratio and target.text.strip():
            # Reserve the reply slot BEFORE generating so the daily cap bounds the
            # number of (paid) model calls, even when the quality gate rejects
            # drafts. Better to under-reply than to overspend on the model.
            self._run_counts[(platform, "reply")] += 1
            try:
                comment = self.commenter.generate(
                    platform=platform, text=target.text, author=target.author_handle)
            except ReplyRejected as exc:
                res = ActionResult(platform=platform, action_type=ActionType.REPLY,
                                   target_key=target.key, status="skipped",
                                   detail=f"quality gate: {exc}")
                self.store.record(res, author=target.author_handle)
                results.append(res)
                comment = ""
            if comment:
                res = adapter.act(Action(platform, ActionType.REPLY, target, comment=comment))
                self.store.record(res, comment=comment, author=target.author_handle)
                results.append(res)
                did_something = did_something or res.acted or res.status in ("dry_run", "pending_approval")
                self._pace(res)

        # 3) FOLLOW — only authors we actually engaged with, and sparingly.
        if did_something and target.author_id and \
                self._eligible(platform, target, ActionType.FOLLOW) and \
                self._rng.random() < 0.5:
            self._run_counts[(platform, "follow")] += 1
            res = adapter.act(Action(platform, ActionType.FOLLOW, target))
            self.store.record(res, author=target.author_handle)
            results.append(res)
            self._pace(res)

    def _pace(self, res: ActionResult) -> None:
        # Only pace after a real network action; dry-runs are instant.
        if res.status == "executed":
            self._sleeper(self._rng.uniform(3.0, 9.0))

    # ---- top-level run ---------------------------------------------------
    def run(self) -> dict:
        if not self.config.enabled:
            log.warning("outreach disabled (kill switch) — nothing to do")
            return {"enabled": False, "platforms": {}}

        summary: dict = {"enabled": True, "mode": self.config.mode,
                         "approval": self.config.approval, "platforms": {}}

        for platform in self.config.platforms:
            try:
                adapter = build_adapter(platform, self.settings, self.config)
            except KeyError as exc:
                log.warning("skipping unknown platform: %s", exc)
                continue

            results: list[ActionResult] = []
            targets: list[Target] = []
            try:
                targets = adapter.discover(self.config.queries, self.config.per_query_limit)
                # deterministic order so a run is reproducible
                self._rng.shuffle(targets)
                for target in targets:
                    # stop early once every cap for this platform is exhausted
                    if all(self._remaining(platform, at) <= 0 for at in ActionType):
                        break
                    self._engage_target(adapter, target, results)
            finally:
                adapter.close()

            summary["platforms"][platform] = self._platform_summary(results, len(targets))
        return summary

    @staticmethod
    def _platform_summary(results: list[ActionResult], discovered: int) -> dict:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in results:
            counts[r.action_type.value][r.status] += 1
        return {
            "discovered": discovered,
            "actions": {k: dict(v) for k, v in counts.items()},
            "sample": [
                {"action": r.action_type.value, "status": r.status,
                 "target": r.target_key, "url": r.url, "detail": r.detail, "error": r.error}
                for r in results[:12]
            ],
        }
