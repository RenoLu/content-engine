"""Base adapter for engagement platforms.

Mirrors ``publishers/base.py``: the base owns the two safety guarantees so no
adapter can get them wrong.

  1. **Dry-run / approval handled once.** ``act()`` only calls the live
     ``_do_*`` methods when the config is genuinely live (not dry-run, not
     approval mode) and the platform is configured. Otherwise it returns a
     non-executing result and never touches the network.
  2. **Failures contained.** A live action that raises becomes a ``failed``
     result, so one broken platform or one bad target never aborts the run.

Adapters implement: ``is_configured``, ``discover``, and the three ``_do_*``
primitives (``_do_like``, ``_do_follow``, ``_do_reply``).
"""

from __future__ import annotations

import abc

import httpx

from ..config import Settings
from ..logging_setup import get_logger
from .config import OutreachConfig
from .models import Action, ActionResult, ActionType, Target

log = get_logger(__name__)


class BaseAdapter(abc.ABC):
    name: str = "base"

    def __init__(self, settings: Settings, config: OutreachConfig,
                 http_client: httpx.Client | None = None):
        self.settings = settings
        self.config = config
        self._client = http_client
        self._owns_client = http_client is None

    # ---- adapters implement ---------------------------------------------
    @abc.abstractmethod
    def is_configured(self) -> bool: ...

    @abc.abstractmethod
    def discover(self, queries: list[str], limit: int) -> list[Target]:
        """Return candidate targets to consider engaging."""

    def _do_like(self, target: Target) -> ActionResult:  # pragma: no cover - overridden
        return self._unsupported(target, ActionType.LIKE)

    def _do_follow(self, target: Target) -> ActionResult:  # pragma: no cover
        return self._unsupported(target, ActionType.FOLLOW)

    def _do_reply(self, target: Target, comment: str) -> ActionResult:  # pragma: no cover
        return self._unsupported(target, ActionType.REPLY)

    # ---- shared machinery -----------------------------------------------
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _result(self, target: Target, action_type: ActionType, status: str,
                url: str | None = None, error: str | None = None,
                detail: str | None = None) -> ActionResult:
        return ActionResult(
            platform=self.name, action_type=action_type, target_key=target.key,
            status=status, url=url, error=error, detail=detail,
        )

    def _unsupported(self, target: Target, action_type: ActionType) -> ActionResult:
        return self._result(target, action_type, "skipped",
                            detail=f"{action_type.value} not supported on {self.name}")

    def act(self, action: Action) -> ActionResult:
        """Single entry point for one action. Honors dry-run/approval and contains
        all failures. The engine still enforces caps + dedupe *before* calling this;
        this method is the last safety layer."""
        t, at = action.target, action.action_type

        if not self.config.is_live:
            status = "pending_approval" if self.config.approval else "dry_run"
            return self._result(t, at, status, detail=action.comment or None)

        if not self.is_configured():
            return self._result(t, at, "skipped", error="not configured")

        try:
            if at == ActionType.LIKE:
                res = self._do_like(t)
            elif at == ActionType.FOLLOW:
                res = self._do_follow(t)
            elif at == ActionType.REPLY:
                res = self._do_reply(t, action.comment)
            else:  # pragma: no cover
                res = self._unsupported(t, at)
            log.info("[%s] %s %s -> %s", self.name, at.value, t.key, res.status)
            return res
        except httpx.HTTPError as exc:
            log.error("[%s] %s http error: %s", self.name, at.value, exc)
            return self._result(t, at, "failed", error=f"http_error: {exc}")
        except Exception as exc:  # noqa: BLE001 - contain everything
            log.exception("[%s] %s unexpected error", self.name, at.value)
            return self._result(t, at, "failed", error=f"{type(exc).__name__}: {exc}")
