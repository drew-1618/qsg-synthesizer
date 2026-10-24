"""Unit tests for the comparison-history storage service.

tests/conftest.py's autouse fixture redirects history.HISTORY_DIR to a
per-test tmp_path, so these never touch the real repo's data/ directory.
"""

from pathlib import Path

from app.schemas import BattlecardResponse
from app.services import history


def _make_battlecard(name_a="Device A", name_b="Device B") -> BattlecardResponse:
    return BattlecardResponse(
        comparison_matrix={
            "model_name": {"device_a": name_a, "device_b": name_b},
            "default_ip": {"device_a": "192.168.1.1", "device_b": "192.168.1.1"},
            "is_private_ip": {"device_a": True, "device_b": True},
            "provisioning_method": {"device_a": "TR-069", "device_b": "Console"},
            "required_tools": {"device_a": [], "device_b": []},
            "ports": {"device_a": [], "device_b": []},
            "led_states": {"device_a": [], "device_b": []},
        },
        installation_friction_score_device_a=1,
        installation_friction_score_device_b=9,
        key_risks_device_a=[],
        key_risks_device_b=[],
        key_advantages_device_a=[],
        key_advantages_device_b=[],
        sales_talk_track="Talk track.",
    )


def test_save_and_get_roundtrip():
    battlecard = _make_battlecard()
    record = history.save_comparison(battlecard)

    fetched = history.get_comparison(record.id)
    assert fetched is not None
    assert fetched.battlecard == battlecard


def test_prunes_oldest_entries_beyond_max():
    total = history.MAX_HISTORY_ENTRIES + 5
    for i in range(total):
        history.save_comparison(_make_battlecard(name_a=f"Device {i}"))

    summaries = history.list_comparisons()
    assert len(summaries) == history.MAX_HISTORY_ENTRIES

    remaining_names = {s.device_a_name for s in summaries}
    for i in range(5):
        assert f"Device {i}" not in remaining_names
    for i in range(5, total):
        assert f"Device {i}" in remaining_names


class TestResolveHistoryDir:
    def test_uses_env_override_when_set(self):
        assert history._resolve_history_dir(
            Path("/repo"), "/tmp/qsg-scratch-history"
        ) == Path("/tmp/qsg-scratch-history")

    def test_falls_back_to_base_dir_when_unset(self):
        assert history._resolve_history_dir(Path("/repo"), None) == Path(
            "/repo/data/history"
        )

    def test_falls_back_to_base_dir_when_empty_string(self):
        assert history._resolve_history_dir(Path("/repo"), "") == Path(
            "/repo/data/history"
        )


def test_save_leaves_no_temp_files():
    history.save_comparison(_make_battlecard())
    assert [p.suffix for p in history.HISTORY_DIR.iterdir()] == [".json"]


def test_failed_write_keeps_existing_record_and_cleans_temp(monkeypatch):
    record = history.save_comparison(_make_battlecard())
    path = history.HISTORY_DIR / f"{record.id}.json"
    original = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(history.os, "replace", boom)
    try:
        history._write_atomic(path, "partial")
    except OSError:
        pass
    assert path.read_text(encoding="utf-8") == original
    assert not list(history.HISTORY_DIR.glob("*.tmp"))


def test_corrupt_and_incomplete_records_are_skipped_and_not_counted():
    good = history.save_comparison(_make_battlecard())
    (history.HISTORY_DIR / ("a" * 32 + ".json")).write_text('{"id": "x"')
    (history.HISTORY_DIR / ("b" * 32 + ".json")).write_text('{"id": "b"}')

    assert [s.id for s in history.list_comparisons()] == [good.id]
    assert history.count_comparisons() == 1
    assert history.get_comparison("a" * 32) is None
    assert history.get_comparison("b" * 32) is None
