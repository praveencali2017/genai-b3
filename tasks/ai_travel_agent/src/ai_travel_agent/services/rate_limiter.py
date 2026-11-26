"""An in-process sliding-window rate limiter for chat messages.

Per process only: with several workers each enforces its own window, which is
acceptable for this single-process demo (a shared store such as Redis would be
needed otherwise).
"""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from ai_travel_agent.exceptions.errors import RateLimitedError


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: defaultdict[int, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: int) -> None:
        """Count one request for `key`, or raise `RateLimitedError`."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                retry_after = math.ceil(self._window - (now - hits[0]))
                raise RateLimitedError(max(1, retry_after))
            hits.append(now)
