"""Retry decorator with backoff for 429, 5xx, network errors; one-shot 401 refresh."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import ParamSpec, TypeVar

import httpx

log = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    jitter_pct: float = 0.20


def _retry_delay(policy: RetryPolicy, attempt: int) -> float:
    base: float = min(policy.backoff_base_seconds * (2**attempt), policy.backoff_max_seconds)
    jitter: float = base * policy.jitter_pct * (random.random() * 2 - 1)
    return max(0.0, base + jitter)


def _retry_after(response: httpx.Response, policy: RetryPolicy) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return _retry_delay(policy, 0)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _retry_delay(policy, 0)


def with_retries(
    policy: RetryPolicy = RetryPolicy(),
    *,
    on_401: Callable[[], None] | None = None,
    on_skypetoken_401: Callable[[], None] | None = None,
) -> Callable[[Callable[P, httpx.Response]], Callable[P, httpx.Response]]:
    """Decorate a function that returns an httpx.Response, adding retry behavior.

    - on_401: invoked once on the first 401, then the call is retried (with refreshed token).
              A second 401 after refresh is propagated (caller treats as auth-dead).
    - on_skypetoken_401: same idea but specifically for chatsvc calls whose Skype token
                        is invalid; the caller is responsible for invalidating + re-minting.
    """

    def decorator(func: Callable[P, httpx.Response]) -> Callable[P, httpx.Response]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> httpx.Response:
            refreshed = False
            skype_refreshed = False
            last_response: httpx.Response | None = None
            for attempt in range(policy.max_attempts):
                try:
                    resp = func(*args, **kwargs)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    log.warning(
                        "network error on attempt %d/%d: %s",
                        attempt + 1,
                        policy.max_attempts,
                        exc,
                    )
                    if attempt + 1 < policy.max_attempts:
                        time.sleep(_retry_delay(policy, attempt))
                        continue
                    raise

                last_response = resp
                if resp.status_code == 401 and on_401 and not refreshed:
                    log.info("got 401; invoking on_401 refresh callback")
                    on_401()
                    refreshed = True
                    continue
                if resp.status_code == 401 and on_skypetoken_401 and not skype_refreshed:
                    log.info("got 401; invoking on_skypetoken_401 refresh callback")
                    on_skypetoken_401()
                    skype_refreshed = True
                    continue
                if resp.status_code == 429 and attempt + 1 < policy.max_attempts:
                    delay = _retry_after(resp, policy)
                    log.info("got 429; waiting %.2fs before retry", delay)
                    time.sleep(delay)
                    continue
                if 500 <= resp.status_code < 600 and attempt + 1 < policy.max_attempts:
                    delay = _retry_delay(policy, attempt)
                    log.info("got %d; backing off %.2fs", resp.status_code, delay)
                    time.sleep(delay)
                    continue
                # success or non-retryable
                return resp
            assert last_response is not None  # for mypy
            return last_response

        return wrapper

    return decorator
