import pytest

from app.services import history


@pytest.fixture(autouse=True)
def isolated_history_dir(tmp_path, monkeypatch):
    """Keep comparison-history writes out of the real repo during tests."""
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
