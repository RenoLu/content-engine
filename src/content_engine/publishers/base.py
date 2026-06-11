"""Base publisher.

The base class centralizes the two safety guarantees the whole system depends on:

  1. **Dry-run is handled here, once.** In dry-run mode ``publish()`` returns a
     ``dry_run`` result with a payload preview and NEVER touches the network —
     subclasses cannot accidentally post.
  2. **Failures are contained.** A live publish that raises is converted into a
     ``failed`` PublishResult, so one broken publisher never aborts the run.

A subclass implements three small methods: ``is_configured``, ``render_payload``
(used for the dry-run preview and usually by the live call too), and
``_publish_live``.
"""

from __future__ import annotations

import abc
import json

import httpx

from ..config import Settings
from ..logging_setup import get_logger
from ..models import Post, PublishResult

log = get_logger(__name__)

_PREVIEW_MAX = 1500


class BasePublisher(abc.ABC):
    name: str = "base"

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None):
        self.settings = settings
        self._client = http_client
        self._owns_client = http_client is None

    # ---- methods subclasses implement -------------------------------------
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True if all required credentials/config are present."""

    @abc.abstractmethod
    def render_payload(self, post: Post) -> dict:
        """Build the platform request payload (also shown in dry-run preview)."""

    @abc.abstractmethod
    def _publish_live(self, post: Post) -> PublishResult:
        """Perform the real API call. Only invoked when configured + live."""

    # ---- shared machinery -------------------------------------------------
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _preview(self, post: Post) -> str:
        try:
            payload = self.render_payload(post)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as exc:  # preview must never raise
            text = f"<could not render payload: {exc}>"
        if len(text) > _PREVIEW_MAX:
            text = text[:_PREVIEW_MAX] + "\n... (truncated)"
        return text

    def publish(self, post: Post, *, dry_run: bool) -> PublishResult:
        """Single entry point. Honors dry-run and contains all failures."""
        preview = self._preview(post)

        if dry_run:
            log.info("[%s] DRY-RUN — not posting.", self.name)
            return PublishResult(
                publisher=self.name, status="dry_run", dry_run=True,
                payload_preview=preview,
            )

        if not self.is_configured():
            log.warning("[%s] live mode but not configured — skipping.", self.name)
            return PublishResult(
                publisher=self.name, status="skipped", dry_run=False,
                error="publisher not configured (missing credentials)",
                payload_preview=preview,
            )

        try:
            result = self._publish_live(post)
            if not result.payload_preview:
                result.payload_preview = preview
            log.info("[%s] published: %s", self.name, result.url or result.status)
            return result
        except httpx.HTTPError as exc:
            log.error("[%s] HTTP error: %s", self.name, exc)
            return PublishResult(
                publisher=self.name, status="failed", dry_run=False,
                error=f"http_error: {exc}", payload_preview=preview,
            )
        except Exception as exc:  # noqa: BLE001 - contain everything
            log.exception("[%s] unexpected error", self.name)
            return PublishResult(
                publisher=self.name, status="failed", dry_run=False,
                error=f"{type(exc).__name__}: {exc}", payload_preview=preview,
            )
