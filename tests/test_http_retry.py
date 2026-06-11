import httpx

from content_engine.http_util import request_with_retry


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    slept: list[float] = []
    resp = request_with_retry(_client(handler), "GET", "https://x/y",
                              base_delay=0.01, sleep=slept.append)
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert len(slept) == 2  # two backoffs before the success


def test_gives_up_and_returns_last_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"})

    resp = request_with_retry(_client(handler), "GET", "https://x/y",
                              max_attempts=2, sleep=lambda s: None)
    assert resp.status_code == 429  # returned so the caller can raise_for_status


def test_non_retryable_status_returns_immediately():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="err")

    resp = request_with_retry(_client(handler), "GET", "https://x/y", sleep=lambda s: None)
    assert resp.status_code == 500
    assert calls["n"] == 1  # 500 is intentionally not retried


def test_retry_after_header_respected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"retry-after": "2"})

    slept: list[float] = []
    request_with_retry(_client(handler), "GET", "https://x", max_attempts=2, sleep=slept.append)
    assert slept == [2.0]


def test_github_403_rate_limit_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0",
                                                "retry-after": "0"})
        return httpx.Response(200, json={})

    resp = request_with_retry(_client(handler), "GET", "https://x", sleep=lambda s: None)
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_transport_error_is_retried_then_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    try:
        request_with_retry(_client(handler), "GET", "https://x",
                           max_attempts=2, base_delay=0.01, sleep=lambda s: None)
    except httpx.ConnectError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ConnectError to propagate after retries")
