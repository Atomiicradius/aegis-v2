"""
resilience.py — survive Redis outages. Used by the worker and the API.

The rule: a Redis connection problem is never fatal. The caller waits (capped exponential
backoff with jitter: 0.2 s, 0.4 s, 0.8 s ... 5 s, each sleep randomised to 50-100 % so that
many workers do not hammer a restarting Redis in lock-step) and tries again until Redis is back.

Only *connection-level* errors are retried (RETRYABLE). A bug (KeyError, a bad Lua reply...)
still raises: we never want to hide a real error behind an endless retry loop.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError

# BusyLoadingError = Redis is up but still loading its AOF/RDB after a restart.
RETRYABLE = (ConnectionError, TimeoutError, BusyLoadingError)

T = TypeVar("T")


class Backoff:
    """Delay generator + one-line logging per outage (first failure, recovery), never one line per retry."""

    def __init__(self, label: str = "redis", start: float = 0.2, cap: float = 5.0) -> None:
        self.label, self.start, self.cap = label, start, cap
        self.attempt = 0                 # consecutive failures so far
        self.down_since: float | None = None

    def failed(self, exc: Exception) -> float:
        """Record a failure; return how long to sleep before the next try."""
        if self.down_since is None:
            self.down_since = time.monotonic()
            print(f"[{self.label}] redis unavailable ({type(exc).__name__}); retrying with backoff", flush=True)
        delay = min(self.cap, self.start * 2 ** self.attempt)
        self.attempt += 1
        return delay * random.uniform(0.5, 1.0)

    def ok(self) -> None:
        """Record a success; log the recovery once if we were down."""
        if self.down_since is not None:
            print(f"[{self.label}] redis is back after {time.monotonic() - self.down_since:.1f}s "
                  f"({self.attempt} failed attempt(s))", flush=True)
        self.attempt, self.down_since = 0, None


def retry(fn: Callable[[], T], label: str = "redis", max_wait_s: float | None = None) -> T:
    """Call fn() until it succeeds, sleeping with backoff on connection errors.

    max_wait_s=None waits forever (the worker must never drop a finished result);
    otherwise the last error is re-raised once that much time has passed.
    """
    backoff, started = Backoff(label), time.monotonic()
    while True:
        try:
            result = fn()
        except RETRYABLE as exc:
            if max_wait_s is not None and time.monotonic() - started > max_wait_s:
                raise
            time.sleep(backoff.failed(exc))
        else:
            backoff.ok()
            return result
