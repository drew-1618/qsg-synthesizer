"""In-memory per-IP fixed-window rate limiting.

No Redis or other external store: this is a single-process personal tool,
so an in-memory window keyed by client IP is enough. Each RateLimiter
instance is meant to be shared (as a FastAPI dependency) across the routes
it protects.
"""

import time as _time
from collections import defaultdict
from threading import Lock
from typing import Callable, Dict, List

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = _time.monotonic,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: Dict[str, List[float]] = defaultdict(list)
        self._lock = Lock()

    def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[client_ip]
            while hits and hits[0] < cutoff:
                hits.pop(0)
            if len(hits) >= self.max_requests:
                retry_after = max(0.0, hits[0] + self.window_seconds - now)
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please slow down and try again shortly.",
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
