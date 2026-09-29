"""Unit tests for the comparison-history storage service.

tests/conftest.py's autouse fixture redirects history.HISTORY_DIR to a
per-test tmp_path, so these never touch the real repo's data/ directory.
"""

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
