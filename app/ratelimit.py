"""Per-key sliding-window rate limiter. In memory, so it resets on restart and
is per process. Fine for one API process; see README limits later."""

import math
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int = 60, window: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()  # FastAPI runs sync routes in threads

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()  # forget requests outside the window
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window - now))
                return False, retry_after  # oldest hit expires then
            hits.append(now)
            return True, 0
