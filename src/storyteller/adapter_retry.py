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
    """Transport has remained unavailable for thirty minutes."""


T = TypeVar("T")


def retry_transport(
    request: Callable[[float], T], *, timeout: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry the identical request, without consuming an inference attempt."""
    deadline = clock() + 1800
    delay = 30.0
    while True:
        try:
            return request(min(timeout, max(0.001, deadline - clock())))
        except TransportError as error:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TransportExhausted(f"通信の失敗が30分続きました: {error}") from error
            sleep(min(delay, remaining))
            if clock() >= deadline:
                raise TransportExhausted(f"通信の失敗が30分続きました: {error}") from error
            delay = min(delay * 2, 300)
