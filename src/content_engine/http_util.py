"""Shared HTTP retry/backoff helper.

A daily batch job shouldn't abort on a transient 429 / 5xx / network blip. This
wraps an ``httpx`` request with bounded exponential backoff that honors
``Retry-After`` and GitHub's ``x-ratelimit-reset`` headers.

It is deliberately tiny and dependency-free. ``sleep`` is injectable so tests
never actually wait.
"""

from __future__ import annotations

import random
import time
from typing import Callable

import httpx

from .logging_setup import get_logger

log = get_logger(__name__)

# Statuses worth retrying: rate limiting + transient gateway/server errors.
# (500 is intentionally excluded — it's usually a deterministic server-side
# failure we want surfaced immediately, not masked by retries.)
DEFAULT_RETRY_STATUSES: tuple[int, ...] = (429, 502, 503, 504)


def _retry_delay(resp: httpx.Response, attempt: int, base: float, cap: float) -> float:
    """Pick a backoff delay, preferring server hints over exponential backoff."""
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    # GitHub primary/secondary rate limits: wait until the reset epoch.
    if resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            try:
                delta = float(reset) - time.time()
                if delta > 0:
                    return min(delta + 1.0, cap)
            except ValueError:
                pass
    return min(base * (2 ** attempt) + random.uniform(0, base), cap)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 4,
    retry_statuses: tuple[int, ...] = DEFAULT_RETRY_STATUSES,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs,
) -> httpx.Response:
    """Issue ``client.request(method, url, **kwargs)`` with bounded retries.

    Retries on the configured statuses, on GitHub 403 rate-limit responses, and
    on transport/timeout errors. Returns the final response (the caller still
    calls ``raise_for_status()``); re-raises the last transport error if every
    attempt failed to get a response.
    """
    last_response: httpx.Response | None = None
    for attempt in range(max_attempts):
        try:
            resp = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= max_attempts - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            log.warning("%s %s failed (%s); retry %d/%d in %.1fs",
                        method, url, exc, attempt + 1, max_attempts - 1, delay)
            sleep(delay)
            continue

        last_response = resp
        rate_limited_403 = resp.status_code == 403 and \
            resp.headers.get("x-ratelimit-remaining") == "0"
        if (resp.status_code in retry_statuses or rate_limited_403) \
                and attempt < max_attempts - 1:
            delay = _retry_delay(resp, attempt, base_delay, max_delay)
            log.warning("HTTP %d for %s; retry %d/%d in %.1fs",
                        resp.status_code, url, attempt + 1, max_attempts - 1, delay)
            sleep(delay)
            continue
        return resp

    assert last_response is not None  # loop always sets it before returning
    return last_response
