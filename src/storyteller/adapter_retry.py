"""Failure categories and bounded transport retry for local inference."""

from __future__ import annotations

from collections.abc import Callable
import threading
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
    """Transport has kept failing until the retry budget ran out."""


class ClaimLost(AdapterError):
    """The executor lost its claim while waiting; its result must be discarded."""


T = TypeVar("T")


def retry_transport(
    request: Callable[[float | None], T], *, timeout: float | None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    budget: float = 1800.0,
    cancelled: threading.Event | None = None,
) -> T:
    """Retry the identical request, without consuming an inference attempt.

    Each send waits up to *timeout* (``None``: as long as the connection is
    open) and is never shortened: waiting behind other users of a shared
    server is not a failure (task-model §6.3).  *budget* is counted from the
    first failure of the current run of failures.  A budget of zero or less
    sends the request once.  When *cancelled* is set (the claim was lost),
    nothing is sent again and :class:`ClaimLost` is raised.
    """
    first_failure: float | None = None
    delay = 30.0
    while True:
        if cancelled is not None and cancelled.is_set():
            raise ClaimLost("claim を失ったため、送信をやめました")
        try:
            return request(timeout)
        except TransportError as error:
            if cancelled is not None and cancelled.is_set():
                raise ClaimLost("claim を失ったため、送信をやめました") from error
            now = clock()
            if first_failure is None:
                first_failure = now
            remaining = first_failure + budget - now
            if remaining <= 0:
                raise TransportExhausted(f"通信の失敗が続き、送り直しの予算を使い切りました: {error}") from error
            sleep(min(delay, remaining))
            if clock() >= first_failure + budget:
                raise TransportExhausted(f"通信の失敗が続き、送り直しの予算を使い切りました: {error}") from error
            delay = min(delay * 2, 300)
