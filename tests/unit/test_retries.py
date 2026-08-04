from __future__ import annotations

from collections.abc import Iterator
from itertools import count
from typing import Any

import httpx
import respx

from teams_cli.api.retries import RetryPolicy, with_retries


def _make_response(
    status: int,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=body or {}, headers=headers or {})


@respx.mock
def test_retries_on_429_with_retry_after() -> None:
    seq: Iterator[httpx.Response] = iter(
        [
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(200, body={"ok": True}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(seq)

    route = respx.get("https://x.test/").mock(side_effect=handler)

    @with_retries(policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0))
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    resp = call()
    assert resp.status_code == 200
    assert route.call_count == 3


@respx.mock
def test_retries_on_5xx() -> None:
    seq = iter([_make_response(503), _make_response(200, body={"ok": True})])
    respx.get("https://x.test/").mock(side_effect=lambda r: next(seq))

    @with_retries(policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0))
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    assert call().status_code == 200


@respx.mock
def test_gives_up_after_max_attempts() -> None:
    respx.get("https://x.test/").mock(return_value=_make_response(503))

    @with_retries(policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0))
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    resp = call()
    assert resp.status_code == 503


@respx.mock
def test_401_triggers_refresh_callback_then_retries() -> None:
    seq = iter([_make_response(401), _make_response(200, body={"ok": True})])
    respx.get("https://x.test/").mock(side_effect=lambda r: next(seq))
    refresh_calls: list[int] = []

    @with_retries(
        policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0),
        on_401=lambda: refresh_calls.append(1),
    )
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    assert call().status_code == 200
    assert len(refresh_calls) == 1


@respx.mock
def test_two_consecutive_401s_dont_refresh_twice() -> None:
    """If retry-after-refresh still returns 401, give up — RT is dead."""
    respx.get("https://x.test/").mock(return_value=_make_response(401))
    refresh_calls: list[int] = []

    @with_retries(
        policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0),
        on_401=lambda: refresh_calls.append(1),
    )
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    resp = call()
    assert resp.status_code == 401
    assert len(refresh_calls) == 1  # one refresh attempt, then surrender


@respx.mock
def test_network_error_is_retried() -> None:
    counter = count()

    def handler(request: httpx.Request) -> httpx.Response:
        i = next(counter)
        if i == 0:
            raise httpx.ConnectError("network down", request=request)
        return _make_response(200, body={"ok": True})

    respx.get("https://x.test/").mock(side_effect=handler)

    @with_retries(policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0.0))
    def call() -> httpx.Response:
        return httpx.get("https://x.test/")

    assert call().status_code == 200
