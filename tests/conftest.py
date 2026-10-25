import pytest

from qsg_synthesizer.main import compare_rate_limiter, export_rate_limiter
from qsg_synthesizer.services import history


@pytest.fixture(autouse=True)
def isolated_history_dir(tmp_path, monkeypatch):
    """Keep comparison-history writes out of the real repo during tests."""
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Give each test a fresh rate-limit window regardless of test order."""
    compare_rate_limiter.reset()
    export_rate_limiter.reset()
