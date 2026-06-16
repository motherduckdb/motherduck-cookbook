"""Adaptive rate limiter for API request dispatch.

Mirrors `scripts/ingest/api/rate-limiter.ts` from the legacy pipeline.
Adaptive behavior (constants, probe cadence, slowdown thresholds) is
preserved verbatim — the only change is sync Python instead of an async
serializing promise chain.

The legacy version used a module singleton with an async dispatch chain.
This is a class because module-level mutable state is harder to reset
between tests. A `RateLimiter()` is otherwise behaviorally equivalent.
"""

from __future__ import annotations

import threading
import time
from collections import deque


_WINDOW_SIZE = 15
_PROBE_INTERVAL = 8
_ERROR_RATE_HIGH = 0.10
_AGGRESSIVE_SPEEDUP = 0.85
_GENTLE_SPEEDUP = 0.95
_MODERATE_SLOWDOWN = 1.5
_AGGRESSIVE_SLOWDOWN = 2.0


class RateLimiter:
    def __init__(self, base_delay_ms: int = 500, min_delay_ms: int = 200, max_delay_ms: int = 10_000) -> None:
        self._delay_ms = base_delay_ms
        self._min_delay_ms = min_delay_ms
        self._max_delay_ms = max_delay_ms
        self._lock = threading.Lock()
        self._last_dispatch_s = 0.0
        self._outcomes: deque[bool] = deque(maxlen=_WINDOW_SIZE)
        self._successes_since_probe = 0

    @property
    def current_delay_ms(self) -> int:
        return self._delay_ms

    def acquire(self) -> None:
        """Block until it's safe to dispatch the next request.

        Serialized via a single lock so concurrent callers each wait the
        correct interval after the previous dispatch.
        """
        with self._lock:
            now = time.monotonic()
            elapsed_ms = (now - self._last_dispatch_s) * 1000.0
            wait_ms = self._delay_ms - elapsed_ms
            if wait_ms > 0:
                time.sleep(wait_ms / 1000.0)
            self._last_dispatch_s = time.monotonic()

    def _error_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        errors = sum(1 for ok in self._outcomes if not ok)
        return errors / len(self._outcomes)

    def record_success(self) -> None:
        with self._lock:
            self._outcomes.append(True)
            self._successes_since_probe += 1
            if self._successes_since_probe < _PROBE_INTERVAL:
                return

            self._successes_since_probe = 0
            error_rate = self._error_rate()

            if error_rate == 0 and len(self._outcomes) >= _PROBE_INTERVAL:
                self._delay_ms = max(self._min_delay_ms, round(self._delay_ms * _AGGRESSIVE_SPEEDUP))
            elif error_rate < _ERROR_RATE_HIGH:
                self._delay_ms = max(self._min_delay_ms, round(self._delay_ms * _GENTLE_SPEEDUP))
            # else: high error rate — hold; record_throttle handles slowdown

    def record_throttle(self) -> None:
        with self._lock:
            self._outcomes.append(False)
            # Don't reset _successes_since_probe — probe fires soon so we
            # recover quickly once errors pass
            error_rate = self._error_rate()
            multiplier = _AGGRESSIVE_SLOWDOWN if error_rate > _ERROR_RATE_HIGH else _MODERATE_SLOWDOWN
            self._delay_ms = min(self._max_delay_ms, round(self._delay_ms * multiplier))
