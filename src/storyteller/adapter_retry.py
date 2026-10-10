"""Failure categories and bounded transport retry for local inference."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TypeVar


class AdapterError(ValueError):
    """Invalid adapter configuration or protocol response."""


class AdapterRequestError(AdapterError):
    """Base class for failed HTTP requests."""


class TransportError(AdapterRequestError):
    """Connection, timeout, HTTP 5xx or rate limiting failure."""


class RequestRejected(AdapterRequestError):
    """A non-retryable HTTP 4xx response."""


class TransportExhausted(TransportError):
    """Transport has remained unavailable until the lease-bound retry budget ran out."""


T = TypeVar("T")


def retry_transport(
    request: Callable[[float], T], *, timeout: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    budget: float = 1800.0,
) -> T:
    """Retry the identical request, without consuming an inference attempt.

    *budget* is the total retry time in seconds (the claim's remaining lease
    minus five minutes).  A budget of zero or less sends the request once.
    """
    deadline = clock() + budget
    delay = 30.0
    while True:
        try:
            return request(timeout if budget <= 0 else min(timeout, max(0.001, deadline - clock())))
        except TransportError as error:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TransportExhausted(f"通信の失敗が続き、lease の残り時間内の送り直しを使い切りました: {error}") from error
            sleep(min(delay, remaining))
            if clock() >= deadline:
                raise TransportExhausted(f"通信の失敗が続き、lease の残り時間内の送り直しを使い切りました: {error}") from error
            delay = min(delay * 2, 300)
