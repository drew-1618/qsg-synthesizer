"""Unit tests for the in-memory per-IP rate limiter.

A fake clock is injected so window-expiry behavior is tested deterministically,
without real sleeps.
"""

import pytest
from fastapi import HTTPException

from app.services.rate_limiter import RateLimiter


class FakeRequest:
    def __init__(self, ip: str):
        self.client = type("Client", (), {"host": ip})()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_requests_under_the_limit():
    clock = FakeClock()
    limiter = RateLimiter(max_requests=3, window_seconds=60, clock=clock)
    for _ in range(3):
        limiter(FakeRequest("1.2.3.4"))  # should not raise


def test_blocks_requests_over_the_limit():
    clock = FakeClock()
    limiter = RateLimiter(max_requests=2, window_seconds=60, clock=clock)
    limiter(FakeRequest("1.2.3.4"))
    limiter(FakeRequest("1.2.3.4"))

    with pytest.raises(HTTPException) as exc_info:
        limiter(FakeRequest("1.2.3.4"))
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_limits_are_tracked_per_ip():
    clock = FakeClock()
    limiter = RateLimiter(max_requests=1, window_seconds=60, clock=clock)
    limiter(FakeRequest("1.1.1.1"))
    limiter(FakeRequest("2.2.2.2"))  # different IP, own quota


def test_window_expiry_allows_requests_again():
    clock = FakeClock()
    limiter = RateLimiter(max_requests=1, window_seconds=60, clock=clock)
    limiter(FakeRequest("1.2.3.4"))
    with pytest.raises(HTTPException):
        limiter(FakeRequest("1.2.3.4"))

    clock.advance(61)
    limiter(FakeRequest("1.2.3.4"))  # window has rolled over, should not raise


def test_reset_clears_all_tracked_hits():
    clock = FakeClock()
    limiter = RateLimiter(max_requests=1, window_seconds=60, clock=clock)
    limiter(FakeRequest("1.2.3.4"))
    limiter.reset()
    limiter(FakeRequest("1.2.3.4"))  # should not raise after reset
